# ScanWise AI — refined build specification

A tightened version of the original master prompt. It keeps everything that improves
**extraction accuracy, traceability and review speed**, adds what the original was
missing for live scanning, and drops infrastructure that added moving parts without
improving accuracy (see *Deliberately out of scope*).

---

## 1. Goal

Turn invoices, bills, receipts, quotations, purchase orders and price lists — captured
live with a camera, uploaded as images or PDFs — into structured, validated financial
data with **minimal manual correction** and **no silent errors**.

**Primary metric:** *silent error rate* — values that are wrong but were **not** flagged
for review. A wrong value the user is warned about costs seconds; a wrong value that
looks confident costs money. Optimise for this first, then for review rate.

## 2. Non-negotiable principles

1. **Transcribe, then compute.** Models (OCR or LLM) only read text. All arithmetic is
   deterministic Python `Decimal`. No model ever fills in a missing number.
2. **Evidence for every value.** Each value keeps: printed text, normalised value, page,
   bounding box, OCR tokens, OCR confidence, and which methods produced/agreed with it.
3. **Separate signals, never one blended score.** OCR confidence, extractor confidence and
   arithmetic validation are shown separately.
4. **No silent corrections.** Disagreements keep both readings; failed arithmetic produces
   *suggestions* (e.g. "leading 7 may be a misread ₹ symbol") that only a person applies.
5. **Unknown is a valid answer.** Missing → empty; unreadable → flagged; ambiguous
   (`$`, `03/04/2026`, `1.500`) → flagged with the assumption stated.
6. **Never convert currencies.** Keep the document's currency (ISO 4217).

## 3. Capture (accuracy starts here)

- **Live camera:** real-time document edge detection (paper-likeness segmentation +
  edge-enclosed regions, scored by boundary contrast), stable outline, auto-capture only
  when the page is **steady, sharp and large enough to read**; manual shutter; torch;
  zoom; front/back camera; multi-page sessions with reorder/retake/delete.
- **Quality gates at capture time:** "move closer — text too small" when the page would be
  under ~1000 px tall; blur and darkness hints. Prefer full-resolution stills
  (`ImageCapture.takePhoto`) over video frames; re-detect corners on the still.
- **Corner editor:** draggable corners/edges, magnifier loupe, auto-detect, full page,
  rotate, live perspective-corrected preview.
- **Uploads:** JPG/PNG/WEBP/HEIC/TIFF/PDF, drag & drop, paste, batch or "one multi-page
  document"; magic-byte validation, size/page limits.
- **PDFs:** use the text layer when reliable (exact, confidence 1.0); OCR scanned pages;
  merge both on mixed pages. Never rasterise a good text layer.

## 4. Reading pipeline (conditional, audited)

1. Geometry: user corners → perspective warp, otherwise conservative auto-detection
   (never crop screenshots/scans); upsample small captures during the warp; orientation
   search; deskew from text-line angles.
2. OCR (PP-OCRv6 via ONNX Runtime by default; PaddleOCR/Tesseract pluggable) with word
   boxes; a second **consensus pass** on an enhanced/upscaled image when confidence is
   low — agreeing readings reinforce, disagreeing readings are kept for review.
3. Layout extraction (always): key/value headers, party blocks, table columns by
   alignment, multi-line descriptions, section headers, price lists (`$40.00 / visit`),
   totals and tax lines. Fuzzy label matching survives OCR noise ("Descrigtion").
4. Vision model (configurable: always / only when the layout result is weak / off) —
   returns values **as printed** plus the OCR token ids that contain them. Invalid JSON →
   one repair retry → otherwise fall back to OCR with a visible warning.
5. Cross-validation: every vision value is checked against its cited tokens (or
   re-anchored by search); vision and layout rows are aligned; disagreements are kept.
6. Decimal validation: qty × rate (− discount), line tax, Σ lines = subtotal, each tax
   line (including *tax applied to a subset of items* and *tax-inclusive* prices),
   grand total, total − paid = balance, rounding; consistency checks (duplicate rows,
   due date before issue date, CGST≠SGST, CGST/SGST + IGST together).
7. Arbitration: for failing checks, try competing machine readings; adopt one only if it
   reconciles, and keep the field flagged.
8. Status per field: verified · high confidence · needs review · unreadable · mismatch.

## 5. Data model

Document details, parties (name, address, phone, email, tax id, contact), dates, terms,
PO/reference, currency (code, confidence, evidence, ambiguity), line items (name,
description, SKU, qty, unit + original unit text, rate, discount, tax rate/amount,
amount, line total, category, page, row box, original text, status), tax lines (type,
rate, base, amount, applies-to items, inclusive), summary (subtotal, discount, taxable,
tax total, shipping, other charges, rounding, grand total, paid, balance), validation
report (extracted vs recalculated vs difference, checks, suggestions, reasons),
immutable machine reading + user corrections + audit log.

## 6. Review experience

Two panels: document viewer (zoom, pan, rotate, pages, field boxes coloured by status,
OCR-text layer, original/processed toggle) and editable data. Clicking a value zooms to its
source; clicking a box selects the value; **pick-from-document** fills a field from any
OCR word. "Next issue" walks through flagged values. Inspector shows printed text,
machine reading, sources, confidence, issues and alternative readings with one-click
"Use". Approve (blocked until issues are acknowledged), re-process, export.

## 7. Price book

Search, All/Services/Materials (+custom categories), "Scan bill or price list", add, edit
(item, rate + currency, unit chips, category, tax: default/presets/not taxable/custom),
delete, multi-select bulk actions, CSV/XLSX import, export. Adding from documents or
imports goes through a review step with duplicate detection (name/SKU/unit/fuzzy) showing
old → new rate; the user chooses add / update / skip. Rate changes keep price history.

## 8. Outputs

Excel (summary, items, taxes, validation, field review), CSV, JSON (with evidence,
original reading and audit trail), PDF report; per document, all line items, or the price
book. Documented REST API (OpenAPI at `/docs`).

## 9. Stack

Next.js + TypeScript + Tailwind (light/dark, responsive, accessible) · FastAPI + Pydantic ·
OpenCV · RapidOCR/PP-OCRv6 · PyMuPDF · provider-agnostic vision client (OpenAI-compatible
incl. NVIDIA/OpenRouter/Ollama, Gemini, Anthropic) · SQLAlchemy + Alembic (SQLite by
default, PostgreSQL in Docker) · in-process job runner with crash recovery · pytest +
Node tests · synthetic benchmark.

## 10. Quality bar

- Unit tests for parsing (formats, signs, Indian grouping, ambiguity), currency, units, tax,
  dates, categories and the Decimal engine; integration tests on real samples; API tests;
  vision path tested with a scripted provider (repair retry, grounding, arbitration).
- Benchmark on a generated dataset with exact ground truth across currencies, number
  formats, tax regimes and three capture conditions (digital PDF, flat scan, phone photo):
  CER/WER, header/item/rate/qty/unit/amount/tax/total/currency accuracy, row association,
  false-positive rows, reconciliation, **review rate and silent error rate**.
- Never report accuracy without a measured dataset.

## Deliberately out of scope (v1) — and why

| Original ask | Decision |
|---|---|
| Celery + Redis | In-process worker pool with DB-backed job state and restart recovery. Same behaviour for a single node, two fewer services. Swap in Celery/RQ for multi-node. |
| LangGraph | The workflow is a fixed, conditional pipeline; a plain orchestrator with a stage log is clearer and dependency-free. |
| Multi-user auth / RBAC | Single-user local app. Bind to localhost or put it behind your SSO/reverse proxy; add auth before multi-tenant use. |
| S3 object storage | Local storage behind a small interface; add an S3 implementation when deploying to the cloud. |
| Benchmark dashboard UI | Benchmark produces Markdown/JSON reports; a UI adds little. |
| 21 named workflow nodes | Collapsed into ~9 audited stages with the same checks. |
