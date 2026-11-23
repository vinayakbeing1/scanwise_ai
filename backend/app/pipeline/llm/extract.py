"""Run the vision model over a document (page batches, schema validation, repair retry)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import numpy as np
from pydantic import ValidationError

from app.config import Settings

from ..imaging import encode_jpeg, resize_max
from ..ocr.base import Token
from .prompt import SYSTEM_PROMPT, repair_prompt, user_prompt
from .providers import VisionProvider
from .schema import LLMExtraction

log = logging.getLogger(__name__)


@dataclass
class VisionPage:
    page: int
    image: np.ndarray
    tokens: list[Token]
    width: float
    height: float


@dataclass
class VisionRun:
    result: LLMExtraction
    calls: int
    repaired: bool
    pages_sent: int
    pages_skipped: int


def _call(provider: VisionProvider, batch: list[VisionPage], total_pages: int, max_side: int) -> tuple[LLMExtraction, int, bool]:
    images = [encode_jpeg(resize_max(p.image, max_side), 88) for p in batch]
    text = user_prompt([(p.page, p.tokens, p.width, p.height) for p in batch], total_pages)
    messages: list[tuple[str, str]] = [("user", text)]
    raw = provider.complete(SYSTEM_PROMPT, messages, images)
    try:
        return LLMExtraction.model_validate(raw), 1, False
    except ValidationError as e:
        log.warning("vision output failed validation, retrying with repair prompt")
        messages += [("assistant", json.dumps(raw)[:30000]), ("user", repair_prompt(str(e)))]
        raw2 = provider.complete(SYSTEM_PROMPT, messages, images)
        return LLMExtraction.model_validate(raw2), 2, True


def _merge(parts: list[LLMExtraction]) -> LLMExtraction:
    if len(parts) == 1:
        return parts[0]
    out = LLMExtraction(document_type=parts[0].document_type)
    for p in parts:
        out.language = out.language or p.language
        out.currency_code = out.currency_code or p.currency_code
        out.currency_evidence = out.currency_evidence or p.currency_evidence
        if out.prices_include_tax is None:
            out.prices_include_tax = p.prices_include_tax
        for k, v in p.header.items():
            if v is not None and v.text and not (out.header.get(k) and out.header[k].text):
                out.header[k] = v
        out.line_items.extend(p.line_items)
        out.taxes.extend(p.taxes)
        for k, v in p.summary.items():  # totals usually sit on the last page: later pages win
            if v is not None and v.text:
                out.summary[k] = v
        out.observations.extend(p.observations)
    return out


def run_vision(provider: VisionProvider, pages: list[VisionPage], settings: Settings) -> VisionRun:
    use = pages[: settings.llm_max_pages]
    per = max(1, provider.pages_per_call)
    parts: list[LLMExtraction] = []
    calls = 0
    repaired = False
    for i in range(0, len(use), per):
        batch = use[i : i + per]
        res, n, rep = _call(provider, batch, len(pages), settings.llm_image_max_side)
        for li in res.line_items:
            if not li.page or li.page < batch[0].page or li.page > batch[-1].page:
                li.page = batch[0].page
        parts.append(res)
        calls += n
        repaired = repaired or rep
    return VisionRun(_merge(parts), calls, repaired, len(use), max(0, len(pages) - len(use)))
