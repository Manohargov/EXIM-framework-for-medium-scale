"""Document intake: read a PDF or image, fill it from the event store, ask for gaps.

The reading path is poppler-first (``pdftotext`` / ``pdftohtml`` / ``pdftoppm``)
so the core works without a Python PDF library. OCR is optional and only needed
for scans and photos.
"""

from __future__ import annotations

from .classify import Classification, attach_boxes, classify, extract_fields
from .events import DocumentFieldsResolved, DocumentFilled, DocumentUploaded
from .extract import (
    ExtractionError,
    OcrUnavailable,
    available_languages,
    configured_languages,
    detect_kind,
    extract,
    has_text_layer,
    ocr_available,
)
from .fill import fill, fill_overlay, fill_reflow, preview_bytes
from .pipeline import (
    ALLOWED_SUFFIXES,
    MAX_BYTES,
    MAX_PAGES,
    DocumentError,
    DocumentSession,
    DocumentTooLarge,
    TooManyPages,
    UnsupportedDocument,
    answer,
    discard,
    finish,
    make_workdir,
    message,
    open_document,
    questions,
    refresh,
    rows,
    validate,
)
from .resolve import Resolution, apply_answers, parse_answers, resolve
from .schema import (
    SCHEMAS,
    Box,
    DocField,
    DocJob,
    DocKind,
    DocType,
    ExtractedDoc,
    FieldStatus,
    Page,
    Word,
)

__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_BYTES",
    "MAX_PAGES",
    "SCHEMAS",
    "Box",
    "Classification",
    "DocField",
    "DocJob",
    "DocKind",
    "DocType",
    "DocumentError",
    "DocumentFieldsResolved",
    "DocumentFilled",
    "DocumentSession",
    "DocumentTooLarge",
    "DocumentUploaded",
    "ExtractedDoc",
    "ExtractionError",
    "FieldStatus",
    "OcrUnavailable",
    "Page",
    "Resolution",
    "TooManyPages",
    "UnsupportedDocument",
    "Word",
    "answer",
    "apply_answers",
    "attach_boxes",
    "available_languages",
    "classify",
    "configured_languages",
    "detect_kind",
    "discard",
    "extract",
    "extract_fields",
    "fill",
    "fill_overlay",
    "fill_reflow",
    "finish",
    "has_text_layer",
    "make_workdir",
    "message",
    "ocr_available",
    "open_document",
    "parse_answers",
    "preview_bytes",
    "questions",
    "refresh",
    "resolve",
    "rows",
    "validate",
]
