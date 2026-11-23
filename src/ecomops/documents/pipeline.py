"""Document intake pipeline: extract -> classify -> resolve -> ask -> fill.

The pipeline is the only place that mutates a job. The UI and the CLI call the
same functions, so the assistant and ``ecomops doc`` cannot disagree about what
a document says or which fields still need a person.

A session owns a temporary directory; call :func:`discard` when the operator is
done (or a new upload replaces it) so uploads never outlive the job.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from ..auth import User
from .classify import attach_boxes, classify, extract_fields
from .events import DocumentFieldsResolved, DocumentFilled, DocumentUploaded
from .extract import ExtractionError, extract, pdf_page_count
from .fill import fill
from .resolve import apply_answers, parse_answers, resolve
from .schema import DocField, DocJob, ExtractedDoc

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_QUESTIONS = 5


class DocumentError(RuntimeError):
    """A document cannot be accepted or processed."""


class UnsupportedDocument(DocumentError):
    pass


class DocumentTooLarge(DocumentError):
    pass


class TooManyPages(DocumentError):
    pass


@dataclass
class DocumentSession:
    job: DocJob
    doc: ExtractedDoc
    workdir: Path
    summary: dict[str, Any] = dc_field(default_factory=dict)

    @property
    def missing(self) -> list[DocField]:
        return self.job.blocking

    @property
    def ready(self) -> bool:
        return not self.job.blocking

    @property
    def output(self) -> Path | None:
        return self.job.output_path


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "document"
    return stem[:80]


def validate(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UnsupportedDocument(
            f"{suffix or path.name} is not supported. Attach a PDF, PNG or JPEG."
        )
    if not path.exists():
        raise DocumentError(f"No such file: {path}")
    size = path.stat().st_size
    if size == 0:
        raise DocumentError("The file is empty.")
    if size > MAX_BYTES:
        raise DocumentTooLarge(
            f"{size / 1_048_576:.1f} MB is over the {MAX_BYTES // 1_048_576} MB limit."
        )
    if suffix == ".pdf":
        pages = pdf_page_count(path)
        if pages > MAX_PAGES:
            raise TooManyPages(
                f"{pages} pages is over the {MAX_PAGES}-page limit for an interactive job."
            )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

def _publish(system: Any, event: Any) -> None:
    bus = getattr(system, "bus", None)
    if bus is not None:
        bus.publish(event)


def _emit_uploaded(system: Any, session: DocumentSession, user: User) -> None:
    _publish(
        system,
        DocumentUploaded(
            job_id=session.job.id,
            filename=session.job.filename,
            sha256=session.job.sha256,
            kind=session.doc.kind.value,
            pages=session.doc.page_count,
            doc_type=session.job.doc_type.value,
            doc_confidence=round(session.summary.get("classification", 0.0), 2),
            uploaded_by=user.email,
            source="document-intake",
        ),
    )


def _emit_resolved(system: Any, session: DocumentSession) -> None:
    _publish(
        system,
        DocumentFieldsResolved(
            job_id=session.job.id,
            filename=session.job.filename,
            doc_type=session.job.doc_type.value,
            known=session.summary.get("known", 0),
            total=session.summary.get("total", len(session.job.fields)),
            asked=[f.key for f in session.missing],
            sources=session.summary.get("sources", {}),
            source="document-intake",
        ),
    )


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

def make_workdir(prefix: str = "ecomops-doc-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def open_document(
    system: Any,
    user: User,
    path: str | Path,
    *,
    workdir: Path | None = None,
    filename: str | None = None,
) -> DocumentSession:
    """Read, classify and resolve one document into a session."""
    path = Path(path)
    validate(path)
    folder = Path(workdir) if workdir is not None else make_workdir()

    doc = extract(path, workdir=folder)
    if doc.page_count > MAX_PAGES:
        raise TooManyPages(
            f"{doc.page_count} pages is over the {MAX_PAGES}-page limit for an interactive job."
        )

    classification = classify(doc.text)
    fields = extract_fields(doc.text, classification.doc_type)
    attach_boxes(fields, doc.pages)

    job = DocJob(
        id=f"doc-{doc.sha256[:10]}",
        filename=_safe_name(filename or path.name),
        sha256=doc.sha256,
        kind=doc.kind,
        doc_type=classification.doc_type,
        pages=doc.page_count,
        fields=fields,
    )
    session = DocumentSession(job=job, doc=doc, workdir=folder)
    session.summary = resolve(system, user, job)
    session.summary["classification"] = classification.confidence
    session.summary["evidence"] = classification.evidence[:5]

    _emit_uploaded(system, session, user)
    _emit_resolved(system, session)
    return session


def refresh(system: Any, user: User, session: DocumentSession) -> dict[str, Any]:
    """Re-resolve after the catalogue or the event store changed."""
    session.summary = resolve(system, user, session.job)
    return session.summary


def answer(system: Any, user: User, session: DocumentSession, text: str) -> list[str]:
    """Apply operator answers, re-resolve, and return the keys that changed."""
    answers = parse_answers(text, session.job)
    updated = apply_answers(user, session.job, answers)
    if updated:
        session.summary = resolve(system, user, session.job)
        _emit_resolved(system, session)
    return updated


def finish(
    system: Any,
    user: User,
    session: DocumentSession,
    *,
    output: str | Path | None = None,
    mode: str = "original",
) -> Path:
    """Fill the document and audit it."""
    mode = "reflow" if mode in {"reflow", "re-flowed"} else "original"
    target = Path(output) if output is not None else session.workdir / f"{session.job.id}-filled.pdf"
    filled = fill(session.doc, session.job, target, mode=mode, workdir=session.workdir)

    session.job.status = "filled"
    session.job.output_path = filled
    _publish(
        system,
        DocumentFilled(
            job_id=session.job.id,
            filename=session.job.filename,
            doc_type=session.job.doc_type.value,
            mode=mode,
            output=str(filled),
            fields_filled=sum(1 for f in session.job.fields if (f.answer or "").strip()),
            fields_unknown=[f.key for f in session.job.fields if not (f.answer or "").strip()],
            filled_by=user.email,
            source="document-intake",
        ),
    )
    return filled


def discard(session: DocumentSession | None) -> None:
    if session is None:
        return
    shutil.rmtree(session.workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Presentation helpers (shared by the assistant, the UI and the CLI)
# --------------------------------------------------------------------------- #

def questions(job: DocJob, limit: int = MAX_QUESTIONS) -> list[str]:
    lines: list[str] = []
    for field in job.blocking[:limit]:
        reason = field.hint or "no value on the document and nothing in MSM matches"
        lines.append(f"{field.label} — {reason}")
    return lines


def message(session: DocumentSession) -> str:
    """The assistant's review message for one job."""
    job = session.job
    pages = "page" if job.pages == 1 else "pages"
    head = f"**{job.filename}** · {job.doc_type.label} · {job.pages} {pages}"

    if not job.fields:
        return (
            f"{head}\n\nNo labelled fields were recognised. The text was read "
            f"({len(session.doc.text)} characters) — attach a clearer copy or ask me about it."
        )

    known = sum(1 for f in job.fields if not f.needs_input)
    lines = [head, "", f"{known} of {len(job.fields)} fields answered from MSM."]

    gaps = questions(job)
    if gaps:
        lines.append("")
        lines.append("Still needed:")
        for index, gap in enumerate(gaps, start=1):
            lines.append(f"{index}. {gap}")
        if len(job.blocking) > len(gaps):
            lines.append(f"…and {len(job.blocking) - len(gaps)} more.")
        optional = [f for f in job.missing if not f.required]
        if optional:
            lines.append(
                "Optional: " + ", ".join(f.label for f in optional) + " (can stay blank)."
            )
        lines.append("")
        lines.append("Reply one per line, e.g. `sku=SKU-1001` — or `sku=` to leave it blank.")
    else:
        lines.append("")
        optional = [f for f in job.missing if not f.required]
        if optional:
            lines.append("Optional: " + ", ".join(f.label for f in optional) + " (can stay blank).")
            lines.append("")
        lines.append(
            "Nothing blocking is missing — choose a format and generate the document."
        )
    return "\n".join(lines)


def rows(job: DocJob) -> list[dict[str, str]]:
    """One flat row per field, for tables and JSON."""
    table: list[dict[str, str]] = []
    for field in job.fields:
        table.append(
            {
                "field": field.label,
                "key": field.key,
                "document": field.value or "",
                "msm": field.resolved or "",
                "source": field.source or "",
                "status": field.status.value.replace("_", " "),
            }
        )
    return table


def fillable(job: DocJob) -> list[DocField]:
    return [f for f in job.fields if (f.answer or "").strip()]


__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_BYTES",
    "MAX_PAGES",
    "DocumentError",
    "DocumentSession",
    "DocumentTooLarge",
    "ExtractionError",
    "TooManyPages",
    "UnsupportedDocument",
    "answer",
    "discard",
    "fillable",
    "finish",
    "make_workdir",
    "message",
    "open_document",
    "questions",
    "refresh",
    "rows",
    "validate",
]
