"""Document classification and field extraction.

Classification is deterministic: weighted keyword signals plus a light table
shape check. It never guesses wildly — anything below the threshold becomes
``generic``, which still extracts labelled values and asks for the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .extract import locate_box
from .schema import (
    GENERIC_FIELDS,
    SCHEMAS,
    Box,
    DocField,
    DocType,
    FieldSpec,
    FieldStatus,
    Page,
)


class Classification(BaseModel):
    doc_type: DocType
    confidence: float
    scores: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


_SIGNALS: dict[DocType, list[tuple[str, float]]] = {
    DocType.PURCHASE_ORDER: [
        (r"purchase order", 5),
        (r"\bp\.?o\.?\s*(?:no|number|#)", 4),
        (r"\border total\b", 3),
        (r"\bship to\b", 3),
        (r"\bunit price\b", 2),
        (r"\brequested delivery\b", 3),
        (r"\bbill to\b", 2),
        (r"\bpo-?\d{3,}", 3),
    ],
    DocType.RETURN_SLIP: [
        (r"\breturn\b", 4),
        (r"\brma\b", 5),
        (r"\brefund\b", 4),
        (r"\breturn reason\b", 5),
        (r"\breason for return\b", 5),
        (r"\bcredit note\b", 3),
        (r"\bdamaged\b", 1),
        (r"\bwrong (?:size|item|colour|color)\b", 2),
    ],
    DocType.STOCK_SHEET: [
        (r"stock sheet", 5),
        (r"\bon hand\b", 4),
        (r"\bmarkdown\b", 4),
        (r"\bclearance\b", 4),
        (r"\bunit cost\b", 3),
        (r"\bwarehouse\b", 2),
        (r"\binventory\b", 3),
        (r"\breorder level\b", 3),
    ],
}

_SKU = re.compile(r"\b[A-Z]{2,5}-\d{2,6}\b")
_MONEY = re.compile(r"[£$€]\s?\d|\d+\.\d{2}\b")


@dataclass(slots=True)
class _Signals:
    scores: dict[DocType, float]
    evidence: list[str]


def _score(text: str) -> _Signals:
    lowered = text.lower()
    scores = {doc_type: 0.0 for doc_type in _SIGNALS}
    evidence: list[str] = []
    for doc_type, signals in _SIGNALS.items():
        for pattern, weight in signals:
            if re.search(pattern, lowered):
                scores[doc_type] += weight
                evidence.append(f"{doc_type.value}: {pattern}")

    # Table shape: an SKU next to money reads like a purchase order; the same
    # shape with markdown language reads like a stock sheet.
    for line in lowered.splitlines():
        if _SKU.search(line) and _MONEY.search(line):
            scores[DocType.PURCHASE_ORDER] += 2
            if "markdown" in lowered or "clearance" in lowered:
                scores[DocType.STOCK_SHEET] += 2
            break

    return _Signals(scores=scores, evidence=evidence)


def classify(text: str) -> Classification:
    signals = _score(text)
    ordered = sorted(signals.scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0

    if best_score < 3:
        return Classification(
            doc_type=DocType.GENERIC,
            confidence=0.2,
            scores={key.value: value for key, value in signals.scores.items()},
            evidence=signals.evidence,
        )

    # Confidence: how much the winner beats the runner-up, damped by absolute
    # signal strength. Two weak keywords should not read as 100% certain.
    margin = (best_score - runner_up) / max(best_score, 1)
    confidence = round(min(0.99, 0.45 + 0.35 * margin + 0.02 * best_score), 2)
    return Classification(
        doc_type=best_type,
        confidence=confidence,
        scores={key.value: value for key, value in signals.scores.items()},
        evidence=signals.evidence,
    )


# --------------------------------------------------------------------------- #
# Field extraction
# --------------------------------------------------------------------------- #

_TRAILING = " \t.,;:"


def _clean(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(_TRAILING)
    return value[:180]


def _slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "field"


def _generic_specs(text: str) -> list[FieldSpec]:
    """Pick up ``Label: value`` lines from a document we do not recognise."""
    specs: dict[str, FieldSpec] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Za-z /#.()-]{1,40})\s*:\s*(\S.*)$", line.strip())
        if not match:
            continue
        label, value = _clean(match.group(1)), _clean(match.group(2))
        if not value or len(value) > 120:
            continue
        key = _slug(label)
        if key in specs:
            continue
        specs[key] = FieldSpec(key=key, label=label, required=False, hint=None)
    return list(specs.values())[:10]


def _field_from_spec(spec: FieldSpec, text: str) -> DocField:
    if spec.pattern is None:
        return DocField(
            key=spec.key,
            label=spec.label,
            status=FieldStatus.MISSING,
            hint=spec.hint,
            required=spec.required,
        )

    values: list[str] = []
    for match in re.finditer(spec.pattern, text, re.IGNORECASE | re.MULTILINE):
        value = _clean(match.group(1))
        if value and value.lower() not in {seen.lower() for seen in values}:
            values.append(value)

    if not values:
        return DocField(
            key=spec.key,
            label=spec.label,
            status=FieldStatus.MISSING,
            hint=spec.hint,
            required=spec.required,
        )

    if len(values) > 1:
        return DocField(
            key=spec.key,
            label=spec.label,
            value=values[0],
            status=FieldStatus.AMBIGUOUS,
            confidence=0.5,
            hint=f"Found several values: {' | '.join(values[:4])}",
            required=spec.required,
        )

    return DocField(
        key=spec.key,
        label=spec.label,
        value=values[0],
        status=FieldStatus.EXTRACTED,
        confidence=0.9,
        hint=spec.hint,
        required=spec.required,
    )


def _generic_fields(text: str) -> list[DocField]:
    fields: list[DocField] = []
    for spec in _generic_specs(text):
        match = re.search(
            rf"^{re.escape(spec.label)}\s*:\s*(\S.*)$", text, re.IGNORECASE | re.MULTILINE
        )
        if not match:
            continue
        fields.append(
            DocField(
                key=spec.key,
                label=spec.label,
                value=_clean(match.group(1)),
                status=FieldStatus.EXTRACTED,
                confidence=0.6,
                required=False,
            )
        )
    return fields


def extract_fields(text: str, doc_type: DocType) -> list[DocField]:
    if doc_type is DocType.GENERIC or not SCHEMAS.get(doc_type):
        return _generic_fields(text)
    specs = SCHEMAS.get(doc_type, GENERIC_FIELDS)
    return [_field_from_spec(spec, text) for spec in specs]


def attach_boxes(fields: list[DocField], pages: list[Page]) -> None:
    """Attach the document box of each extracted value, in place."""
    for field in fields:
        if not field.value:
            continue
        box: Box | None = locate_box(pages, field.value)
        if box is not None:
            field.box = box


def signature(text: str, limit: int = 240) -> str:
    """A compact, human-readable summary for the review card."""
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] + ("…" if len(compact) > limit else "")
