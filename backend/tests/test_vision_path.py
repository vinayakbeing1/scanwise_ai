"""The vision-model path end to end, with a scripted provider (no network).

Checks: schema validation + repair retry, grounding of vision values in OCR tokens,
disagreement detection, arbitration by arithmetic, and currency from the
vision transcription (which reads the ₹ glyph that OCR dropped).
"""

import pytest

from app.pipeline import orchestrator
from app.pipeline.llm.providers import VisionProvider
from app.pipeline.llm.schema import LLMExtraction, to_gemini_schema, to_json_schema
from app.pipeline.orchestrator import PageSource, Pipeline

from .conftest import FIXTURES


def F(text, tokens=(), conf="high"):
    return {"text": text, "tokens": list(tokens), "confidence": conf}


class ScriptedProvider(VisionProvider):
    name = "scripted"

    def __init__(self, responses):
        super().__init__("test-model", 5, 0, 4)
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, images):
        self.calls.append((system, messages, len(images)))
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def tokens(settings):
    """Real OCR token ids for the sample (the provider must cite them)."""
    res = Pipeline(settings, llm_mode="off").run([PageSource(1, "image", FIXTURES / "sample_invoice_inr.png")])
    by_text = {}
    for t in res.pages[0].tokens:
        by_text.setdefault(t.text, t.id)
    return by_text


def vision_answer(tok):
    return {
        "document_type": "invoice",
        "language": "en",
        "currency_code": "INR",
        "currency_evidence": "₹ symbols on every amount",
        "prices_include_tax": False,
        "header": {
            "document_number": F("INV-00001", [tok["INV-00001"]]),
            "document_date": F("24 Sep 2026", [tok["24 Sep 2026"]]),
            "vendor_name": F("Patel Electric Works", [tok["Patel Electric Works"]]),
            "customer_name": F("Shree Krupa Developers", [tok["Shree Krupa Developers"]]),
            "payment_terms": F("Net 14", []),  # no citation: must be re-anchored by search
        },
        "line_items": [
            {"page": 1, "name": F("Wiring and rewiring", [tok["Wiring and rewiring"]]), "description": F("Includes materials and travel"),
             "quantity": F("1"), "unit": F("job"), "rate": F("₹4,800.00", [], "medium"),  # deliberate misreading
             "amount": F("₹4,500.00"), "category": "Services"},
            {"page": 1, "name": F("Labour charge", [tok["Labour charge"]]), "quantity": F("2"), "unit": F("h"),
             "rate": F("₹600.00"), "amount": F("₹1,200.00"), "category": "Services"},
        ],
        "taxes": [
            {"label": F("CGST 9%"), "rate": F("9%"), "amount": F("₹405.00")},
            {"label": F("SGST 9%"), "rate": F("9%"), "amount": F("₹405.00")},
            {"label": F("IGST 18%"), "rate": F("18%"), "amount": F("₹216.00")},
        ],
        "summary": {"subtotal": F("₹5,700.00"), "grand_total": F("₹6,726.00"), "amount_paid": F("-₹6,726.00"), "balance_due": F("₹0.00")},
        "observations": ["Totals area shows both CGST/SGST and IGST."],
    }


def test_schemas_are_strict_and_convertible():
    js = to_json_schema()
    assert js["additionalProperties"] is False and set(js["required"]) == set(js["properties"])
    g = to_gemini_schema()
    assert g["type"] == "OBJECT" and "line_items" in g["properties"]
    LLMExtraction.model_validate({"document_type": "invoice"})


def test_vision_path_grounds_and_cross_checks(settings, tokens, monkeypatch):
    provider = ScriptedProvider([{"document_type": "invoice", "line_items": "not-a-list"}, vision_answer(tokens)])
    monkeypatch.setattr(orchestrator, "build_provider", lambda s: provider)
    res = Pipeline(settings, llm_mode="always").run([PageSource(1, "image", FIXTURES / "sample_invoice_inr.png")])
    ex = res.extraction

    assert len(provider.calls) == 2, "invalid JSON must trigger exactly one repair call"
    assert "vision:scripted/test-model" in ex.extractors
    vision_stage = next(s for s in res.log if s.stage == "vision extraction")
    assert "repaired" in vision_stage.detail

    num = ex.header["document_number"]
    assert num.value == "INV-00001" and "vision" in num.sources and "layout" in num.sources and num.bbox
    terms = ex.header["payment_terms"]
    assert terms.value == "Net 14" and terms.bbox is not None  # re-anchored to the OCR token

    wiring = ex.line_items[0]
    # The vision model said 4,800 but OCR and the arithmetic say 4,500: both readings are kept,
    # the reconciling one is used, and the field must be reviewed by a person.
    assert wiring.rate.value == "4500.00"
    assert wiring.rate.status == "needs_review"
    assert any((a.raw or "").startswith("₹4,800") or a.value == "4800.00" for a in wiring.rate.alternatives)

    assert ex.currency.code == "INR" and ex.currency.confidence == "high"  # ₹ read by the vision model
    assert ex.validation.status in ("pass", "warning")
    assert any("Vision model:" in n for n in ex.notes)


def test_vision_failure_falls_back_to_ocr(settings, monkeypatch):
    class Broken(ScriptedProvider):
        def complete(self, system, messages, images):
            raise RuntimeError("HTTP 503")

    monkeypatch.setattr(orchestrator, "build_provider", lambda s: Broken([]))
    res = Pipeline(settings, llm_mode="always").run([PageSource(1, "image", FIXTURES / "sample_invoice_inr.png")])
    ex = res.extraction
    assert ex.extractors == ["layout"]
    assert any("Vision model failed" in w for w in ex.warnings)  # surfaced, not silently discarded
    assert ex.summary["grand_total"].value == "6726.00"
