# MSM — Document Intake & Auto-Fill in the Assistant

**Status:** implemented (v1, 2026-11-14) — see §16 for what shipped and the known limits.
**Goal:** an operator attaches a PDF or image in the sidebar Assistant; MSM extracts
the text, keeps the original layout, fills every field it can answer from the
event store, and asks the operator only for what it cannot resolve.

---

## 1. Scope

### In scope (v1)

- Upload a PDF or image from the chat panel.
- Extract text **and coordinates** (so the layout is preserved).
- Classify the document and map it to a canonical field set.
- Resolve each field against the running system (catalogue, inventory, finance,
  shipments, returns, customer requests).
- Show a review table: field · extracted value · resolved value · source · confidence.
- Ask the operator for missing/ambiguous fields, one batched round at a time.
- Produce a filled output that keeps the original layout, plus a preview.
- Audit the whole job as events.

### Out of scope (v1)

- Handwriting recognition.
- Multi-language OCR beyond English.
- E-signature / legal validity.
- Writing back into the source ERP (there is none by design).

### Non-goals

- No cloud OCR, no third-party upload. Files never leave the machine.
- The grocery CSV stays interface test data; it is **not** training data.

---

## 2. Document types

Start with the three that appear in this domain, then extend:

| Type | Key fields |
|---|---|
| Purchase order / requisition | PO no., customer, SKU lines, qty, destination, date |
| Return / refund slip | order or shipment id, SKU, reason, refund amount |
| Stock / clearance sheet | SKU, description, on hand, unit cost, markdown % |

Classification is rule-based (keyword + table-shape scoring) with an optional
local-LLM tiebreak. Unknown types still work in "generic" mode: extract fields,
fill what matches, ask the rest.

---

## 3. End-to-end flow

```
attach (pdf/png/jpg)
      │
      ▼
[1] Detect       text-layer PDF? scanned PDF? photo?
      │
      ▼
[2] Extract      text + words + bounding boxes (+ tables)
      │
      ▼
[3] Classify     PO / return / stock / generic  → canonical schema
      │
      ▼
[4] Resolve      each field → value + source + confidence
      │              ├─ known    → answer
      │              └─ unknown  → question
      ▼
[5] Review       table in chat + "3 fields need you"
      │
      ▼
[6] Fill         overlay answers on the original page geometry
      │
      ▼
[7] Deliver      preview image + filled PDF download + event audit
```

---

## 4. Architecture

New package `src/ecomops/documents/`:

| Module | Responsibility |
|---|---|
| `schema.py` | Pydantic: `DocType`, `DocField`, `ExtractedDoc`, `DocJob`, `FieldStatus` |
| `extract.py` | Adapters: poppler text/XML, image OCR, page rendering |
| `classify.py` | Rule-based type detection + schema selection |
| `resolve.py` | Field resolvers against the system; confidence scoring |
| `fill.py` | Overlay renderer + clean re-flow (Pillow), PDF assembly, preview |
| `pipeline.py` | State machine: extract → resolve → ask → fill |
| `events.py` | `DocumentUploaded`, `DocumentFieldsResolved`, `DocumentFilled` |

Integration points:

- `chat.py` — `OpsChat` gains a document branch; keeps the deterministic rules
  for text questions unchanged.
- `app.py` — `st.chat_input(accept_file=True, file_type=[...])`; renders the job
  card, the review table, question chips and the download button.
- `cli.py` — `ecomops doc extract <file>` / `ecomops doc fill <file>` for testing
  without the UI.
- `system.py` — `EcomopsSystem.open_document(path, user)` entry point so the CLI,
  API and UI share one path.

---

## 5. Extraction layer

Environment facts (verified):

- `pdftotext`, `pdftoppm`, `pdfimages`, `pdftohtml` are installed (poppler).
- `tesseract` is **not** installed; no `pytesseract`, `PyMuPDF`, `pdfplumber`.
- `Pillow`, `jinja2`, `numpy` are installed. Python is 3.14.8.

Strategy:

1. **Text-layer PDF** — `pdftotext -bbox` gives **word-level** boxes
   (`xMin/yMin/xMax/yMax`). Word boxes matter for filling: the overlay must be
   able to replace `SKU-104` without painting over the `SKU:` label beside it.
   `pdftohtml -xml` (line-level boxes) remains as a fallback when `-bbox` yields
   no words; `pdftotext -layout` is the human-readable fallback.
2. **Scanned PDF / photo** — `pdftoppm -r 150 -png` to page images, then OCR:
   - preferred: `tesseract` TSV (word boxes) via `pytesseract`;
   - pip-only alternative: `rapidocr-onnxruntime` (needs an ONNX wheel for 3.14);
   - if neither is available: extract nothing, tell the operator which package
     to install, and offer the manual-question path.
3. **Tables** — derive from XML word coordinates by row/column clustering.
   Avoids `camelot` (ghostscript dependency).
4. **Page geometry** — keep page width/height and the rendered PNG so the filler
   can work in image space.

No PDF Python library is required for reading. That removes the PyMuPDF /
pdfplumber 3.14-wheel risk.

---

## 6. Canonical schema & resolution

```python
class DocField(BaseModel):
    key: str                 # "sku", "quantity", "refund_total" ...
    label: str
    value: str | None        # extracted from the document
    resolved: str | None     # answered from the system
    source: str | None       # "catalogue:SKU-104", "ShipmentRouted:SHIP-9"
    confidence: float        # 0..1
    status: Literal["known", "ambiguous", "missing", "not_found"]
    box: tuple[float, float, float, float] | None   # x, y, w, h
```

Resolvers (each returns value + source + confidence):

| Field | Source |
|---|---|
| SKU / description / unit price / stock | `system.inventory.products`, catalogue |
| Shipment, carrier, ETA, tracking | `ShipmentRouted`, `DeliveryException*` |
| Return status, refund total | `ReturnRequested`, `ReturnAssessed`, `RefundIssued` |
| Revenue, commission, referrals | `finance_snapshot` (role-gated) |
| Customer identity / history | `CustomerRequestReceived`, `CustomerIntentCaptured` |

Rules:

- Exact match first (SKU, ids), then fuzzy match (`difflib`; `rapidfuzz` optional).
- Never invent: no value without a source event or catalogue row.
- Confidence < 0.8 ⇒ `ambiguous` ⇒ ask, don't guess.
- Finance fields stay hidden for roles without `view_finance`.

---

## 7. Asking the operator

- Batch questions: one message listing up to 5 unknowns, each with an example.
- Quick replies where the answer space is closed ("Which SKU: A / B / neither?").
- Free text accepted; the answer is re-resolved, then re-asked only if still unclear.
- "Leave blank" is always allowed and is recorded as `unknown (reason)`.
- The operator can correct a resolved value before the fill step.

State lives in `st.session_state["doc_session"]`; it survives reruns, is cleared on
sign-out, on a new upload, and when the system is reset.

---

## 8. Filling while keeping the format

Two renderers, chosen by input:

1. **Overlay (default)** — draw the resolved value at the original word box on the
   rendered page image (Pillow), matching font size to the box height. Pages are
   re-assembled into a PDF with `PIL.Image.save(..., save_all=True)`.
   Pixel-true layout, zero PDF-writing dependencies.
2. **Clean re-flow (fallback)** — a labelled, regenerated A4 document rendered
   with Pillow: fields with values and sources, followed by the original text.
   Used when boxes are unreliable (skewed scans, merged cells). Clearly marked
   as a re-generated document.

Safety: values are drawn in the champagne accent or plain black on white, never
over unreadable content; an "Answers" annex page lists every field with its
source so the filled document is auditable.

---

## 9. UI in the Assistant

- `st.chat_input("Ask MSM or attach a document…", accept_file=True, file_type=["pdf","png","jpg","jpeg"])`.
- Job card shows: file name, pages, detected type, progress.
- Review table: field · in document · from MSM · source · confidence.
- Unknowns render as question chips + a text box.
- Result: inline preview + **Download filled PDF**.
- Errors (encrypted PDF, unsupported format, OCR missing) are actionable messages.

---

## 10. Security & limits

- Accept only `pdf/png/jpg/jpeg`, max 10 MB, max 10 pages (configurable).
- Write uploads to a temp dir; delete on job end and on session end.
- Reject encrypted PDFs with a clear message (`pdftotext` fails safely).
- No shell interpolation: file paths passed as list args, filenames sanitized.
- RBAC reuses `Permission`; the fill step cannot expose finance to operations roles.
- Audit events include user, file hash (sha256), page count and fields filled —
  never the raw document bytes.

---

## 11. Dependencies

Required (already present): Streamlit 1.67, Pillow, Jinja2, poppler CLI.
Optional extras, added as `[docs]`:

- `pytesseract` + system `tesseract-ocr` (best OCR quality), **or**
- `rapidocr-onnxruntime` (pip-only, if a cp314 wheel exists).
- `reportlab` only if the clean re-flow renderer needs a real PDF writer.

Risk: Python 3.14 wheels. Mitigation: the reading path is poppler-only, and the
default filler is Pillow, so core features do not depend on new wheels.

---

## 12. Tests

- Fixtures: a text-layer PO PDF, a scanned return slip PNG, a shipping label PNG,
  an encrypted PDF, a 20-page PDF (limit test).
- Unit: extraction boxes, classification, resolver confidence, overlay placement.
- Golden: filled PDF contains the expected strings at expected coordinates.
- `AppTest`: upload → review → answer → download (no real OCR: stub the adapter).
- Negative: oversized file, wrong mime, missing OCR engine, unknown type.
- Event audit: one `DocumentFilled` per completed job.

---

## 13. Phases

| Phase | Deliverable | Est. |
|---|---|---|
| 0 | Spike: poppler XML on a sample, box accuracy, OCR decision | 0.5 d |
| 1 | `extract` + `classify`; CLI prints fields and boxes | 1.5 d |
| 2 | `resolve` + review UI + ask loop | 1.5 d |
| 3 | Overlay filler + download + events | 1.5 d |
| 4 | Hardening: limits, RBAC, tests, docs | 1 d |

Total ≈ 6 dev-days, plus OCR installation if scans must work.

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| No OCR engine installed | Scans/photos unusable | Offer install path; manual fallback |
| Python 3.14 wheels unavailable | OCR/PDF libs fail to install | poppler + Pillow default path |
| Skewed scans / merged cells | Bad box placement | Clean re-flow fallback + operator correction |
| Ambiguous SKUs in free text | Wrong product | Fuzzy + confidence gate + ask |
| Large PDFs | Slow UI | Page/size caps, background job later |

---

## 15. Decisions (agreed 2026-11-13)

| # | Decision | Chosen | Consequence |
|---|---|---|---|
| 1 | Document types in v1 | **All three** — PO, return/refund slip, stock/clearance sheet | Three field schemas; resolvers are shared, so marginal cost is mostly schema + fixtures |
| 2 | Fidelity | **Both, operator picks** | Overlay is the default; a "re-flow" toggle re-renders via Jinja2 HTML when boxes look unreliable |
| 3 | Output | **Filled PDF only** | One download button; no DOCX dependency, no HTML deliverable |
| 4 | OCR | **Install `tesseract-ocr` via sudo** | Unlocks scans and phone photos; requires a one-time manual install (sudo is not available to the agent) |
| 5 | Languages | **Multi-language** | Language list is configurable (`ECOMOPS_OCR_LANGS`); ships with English + orientation detection, more packs installed on demand |

### Install command (run once, in a terminal)

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd
# optional, for non-English documents:
sudo apt-get install -y tesseract-ocr-all      # every language pack (~hundreds of MB)
```

`tesseract --list-langs` should then list `eng` and `osd`.

### Defaults derived from the decisions

- `ECOMOPS_OCR_LANGS=eng` — comma-separated, e.g. `eng+deu+fra`; the extractor
  passes it to `tesseract -l`.
- Both renderers live behind one interface: `fill_overlay(doc)` and
  `fill_reflow(doc)`; the review step exposes a **Format: original / re-flowed**
  choice, defaulting to `original`.
- Re-flowed output is still delivered as PDF (HTML is an intermediate), so the
  download experience does not change.

---

## 16. Implementation status (2026-11-14)

### What shipped

| Piece | Where |
|---|---|
| Schemas, field specs, `needs_input` / `blocking` gates | `documents/schema.py` |
| Word-box extraction + OCR adapters + `locate_box` | `documents/extract.py` |
| Deterministic classifier + labelled-value extraction | `documents/classify.py` |
| Resolvers over catalogue and event store | `documents/resolve.py` |
| Overlay + re-flow renderers, annex, page preview | `documents/fill.py` |
| Session state machine, limits, audit events | `documents/pipeline.py` |
| `DocumentUploaded`, `DocumentFieldsResolved`, `DocumentFilled` | `documents/events.py` |
| `EcomopsSystem.open_document(path, user)` entry point | `system.py` |
| Assistant upload + document panel | `app.py` |
| `ecomops doc extract` / `ecomops doc fill` | `cli.py` |
| `POST /documents` → answers → fill → delete | `api.py` |
| 22 tests (extraction → fill → audit → guards) | `tests/test_documents.py` |
| 3 AppTest flows (panel answer → generate → discard; chat answers; question safety) | `tests/test_app_document.py` |
| Document-aware chat intents + snapshot | `chat.py`, `tests/test_chat.py` |

### Operator behaviour

- Attach a PDF/PNG/JPEG with the composer's attachment control; MSM appends a
  review message and opens the **Document** panel under the chat.
- The panel lists each field as *field · resolved value · source*, flags gaps,
  and offers one `key=value` box for answers (`sku=SKU-1001`; `sku=` leaves it
  deliberately blank).
- **Required** gaps block the fill and are asked; **optional** gaps are reported
  but never block (e.g. a return slip's shipment id).
- Choose **Original layout** or **Re-flowed**, then **Generate document**:
  inline preview plus a download button. **Discard** deletes the temp folder.
- The document session is cleared on sign-out, on a new upload, and on reset.
- The chat itself is document-aware: with a job open, "what is missing?",
  "can you generate the PDF?" and "how do I download it?" answer from the job
  (and the LLM snapshot includes the job), while ordinary operations questions
  still route to their own intents.

### CLI

```bash
.venv/bin/ecomops doc extract tests/fixtures/documents/return_slip.pdf
.venv/bin/ecomops doc fill tests/fixtures/documents/return_slip.pdf \
    -a sku=SKU-1001 -a shipment_id= --mode original
```

`--mode reflow` produces the labelled rebuild; `--out` selects the target path.

### Known limits

- **Filled PDFs are raster** (page images + annex), so they have no text layer.
  Tests validate them with `pdfinfo` page counts and rendered pixels, not
  `pdftotext`.
- Scans/photos still need `tesseract-ocr` installed (§15); text-layer PDFs do
  not. Until then the UI reports `OcrUnavailable` with the install command.
- Finance figures are not written into documents for roles without
  `view_finance`; those fields fall through to the ask loop.
- Re-flow content comes from the field table plus the extracted text; the
  original page geometry is deliberately not reused.

### Deliberately not done (v1)

- No handwriting, no e-signature, no ERP write-back, no cloud OCR.
