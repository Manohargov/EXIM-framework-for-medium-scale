"""Audit events for document intake.

Every job leaves a trail on the bus, so the assistant, the audit tab and the
JSONL journal can explain what MSM did with a document without keeping the
document itself:

    DocumentUploaded -> DocumentFieldsResolved -> DocumentFilled

The events carry the file hash and field metadata, never the bytes.
"""

from __future__ import annotations

from ..events import Event


class DocumentUploaded(Event):
    topic = "DocumentUploaded"

    job_id: str
    filename: str
    sha256: str
    kind: str
    pages: int
    doc_type: str
    doc_confidence: float
    uploaded_by: str


class DocumentFieldsResolved(Event):
    topic = "DocumentFieldsResolved"

    job_id: str
    filename: str
    doc_type: str
    known: int
    total: int
    asked: list[str]
    sources: dict[str, str]


class DocumentFilled(Event):
    topic = "DocumentFilled"

    job_id: str
    filename: str
    doc_type: str
    mode: str
    output: str
    fields_filled: int
    fields_unknown: list[str]
    filled_by: str
