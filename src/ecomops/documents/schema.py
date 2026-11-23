"""Schemas for document intake.

A document is extracted into :class:`ExtractedDoc` (pages of positioned words),
classified into a :class:`DocType`, and described by a list of :class:`DocField`
values. Field specifications live next to the schemas so extraction and
resolution share one definition of "what a purchase order contains".
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DocType(StrEnum):
    PURCHASE_ORDER = "purchase_order"
    RETURN_SLIP = "return_slip"
    STOCK_SHEET = "stock_sheet"
    GENERIC = "generic"

    @property
    def label(self) -> str:
        return {
            DocType.PURCHASE_ORDER: "Purchase order",
            DocType.RETURN_SLIP: "Return / refund slip",
            DocType.STOCK_SHEET: "Stock / clearance sheet",
            DocType.GENERIC: "Generic document",
        }[self]


class DocKind(StrEnum):
    TEXT_PDF = "text_pdf"
    SCAN = "scan"
    IMAGE = "image"


class FieldStatus(StrEnum):
    EXTRACTED = "extracted"  # read from the document, not resolved yet
    KNOWN = "known"  # answered from the system
    AMBIGUOUS = "ambiguous"  # several candidates, operator must choose
    MISSING = "missing"  # not in the document, not in the system
    NOT_FOUND = "not_found"  # in the document, nothing in the system matches


class Box(BaseModel):
    """A rectangle in PDF points, origin top-left."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def union(self, other: Box) -> Box:
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return Box(
            x=x,
            y=y,
            w=max(self.right, other.right) - x,
            h=max(self.bottom, other.bottom) - y,
        )


class Word(BaseModel):
    text: str
    box: Box
    size: float = 10.0
    bold: bool = False


class Page(BaseModel):
    number: int
    width: float
    height: float
    words: list[Word] = Field(default_factory=list)
    image: Path | None = None

    def lines(self, tolerance: float = 3.0) -> list[list[Word]]:
        """Group words into visual lines, ordered top-to-bottom, left-to-right."""
        rows: list[list[Word]] = []
        for word in sorted(self.words, key=lambda w: (round(w.box.y, 1), w.box.x)):
            for row in rows:
                if abs(row[0].box.y - word.box.y) <= tolerance:
                    row.append(word)
                    break
            else:
                rows.append([word])
        return [sorted(row, key=lambda w: w.box.x) for row in rows]

    @property
    def text(self) -> str:
        return "\n".join(" ".join(word.text for word in line) for line in self.lines())


class ExtractedDoc(BaseModel):
    path: Path
    kind: DocKind
    pages: list[Page] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    sha256: str = ""

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


class DocField(BaseModel):
    key: str
    label: str
    value: str | None = None  # as printed in the document
    resolved: str | None = None  # as answered by the system
    source: str | None = None  # catalogue:SKU-104, ShipmentRouted:SHIP-9 ...
    confidence: float = 0.0
    status: FieldStatus = FieldStatus.MISSING
    box: Box | None = None
    hint: str | None = None
    required: bool = True

    @property
    def answer(self) -> str | None:
        return self.resolved if self.resolved is not None else self.value

    @property
    def needs_input(self) -> bool:
        return self.answer is None or self.status in {
            FieldStatus.AMBIGUOUS,
            FieldStatus.MISSING,
            FieldStatus.NOT_FOUND,
        }


class FieldSpec(BaseModel):
    """How to recognise one field in extracted text."""

    key: str
    label: str
    pattern: str | None = None
    kind: str = "text"  # text | money | date | number | percent
    required: bool = True
    hint: str | None = None


class DocJob(BaseModel):
    id: str
    filename: str
    sha256: str
    kind: DocKind
    doc_type: DocType = DocType.GENERIC
    pages: int = 0
    fields: list[DocField] = Field(default_factory=list)
    status: str = "extracted"  # extracted | review | awaiting_input | filled
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    output_path: Path | None = None

    @property
    def missing(self) -> list[DocField]:
        return [f for f in self.fields if f.needs_input]

    @property
    def blocking(self) -> list[DocField]:
        """Gaps that stop the job; optional fields never block a fill."""
        return [f for f in self.fields if f.needs_input and f.required]

    @property
    def answered(self) -> int:
        return sum(1 for f in self.fields if not f.needs_input)


# --------------------------------------------------------------------------- #
# Field specifications
# --------------------------------------------------------------------------- #

_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|[A-Z][a-z]{2,8} \d{1,2},? \d{4})"
_MONEY = r"([£$€]?\s?-?[\d,]+(?:\.\d{2})?)"
_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"


PURCHASE_ORDER_FIELDS = [
    FieldSpec(
        key="po_number",
        label="PO number",
        pattern=r"(?:P\.?O\.?|Purchase Order)\s*(?:No\.?|Number|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
        hint="e.g. PO-2026-0148",
    ),
    FieldSpec(
        key="order_date",
        label="Order date",
        pattern=rf"(?:Order Date|Date|Issued)\s*[:\-]?\s*{_DATE}",
        kind="date",
        hint="YYYY-MM-DD",
    ),
    FieldSpec(
        key="customer",
        label="Customer",
        pattern=r"(?:Customer|Buyer|Bill To|Sold To|Client)\s*[:\-]\s*(.+)",
        hint="customer name or id",
    ),
    FieldSpec(
        key="ship_to",
        label="Ship to",
        pattern=r"(?:Ship To|Ship to|Deliver To|Delivery Address|Consignee)\s*[:\-]\s*(.+)",
        hint="delivery address",
    ),
    FieldSpec(
        key="requested_delivery",
        label="Requested delivery",
        pattern=rf"(?:Requested Delivery|Delivery Date|Due Date|Required By)\s*[:\-]?\s*{_DATE}",
        kind="date",
        required=False,
        hint="YYYY-MM-DD",
    ),
    FieldSpec(
        key="order_total",
        label="Order total",
        pattern=rf"(?:Order Total|Grand Total|Total Amount|Total)\s*[:\-]?\s*{_MONEY}",
        kind="money",
        hint="e.g. 5151.00",
    ),
]

RETURN_SLIP_FIELDS = [
    FieldSpec(
        key="return_id",
        label="Return reference",
        pattern=r"(?:Return|RMA|Reference)\s*(?:No\.?|Number|#|ID|Ref)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
        hint="e.g. RET-2026-0031",
    ),
    FieldSpec(
        key="order_id",
        label="Order",
        pattern=r"(?:Order)\s*(?:No\.?|Number|ID|#)?\s*[:\-]?\s*([A-Z]{2,}[A-Z0-9\-/]{2,})",
        required=False,
        hint="e.g. ORD-2026-0114",
    ),
    FieldSpec(
        key="shipment_id",
        label="Shipment",
        pattern=r"(?:Shipment|Tracking)\s*(?:No\.?|Number|ID|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
        required=False,
        hint="e.g. SHIP-2026-0093",
    ),
    FieldSpec(
        key="sku",
        label="SKU",
        pattern=r"(?:SKU|Item|Product Code|Article)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{2,})",
        hint="e.g. SKU-104",
    ),
    FieldSpec(
        key="quantity",
        label="Quantity",
        pattern=rf"(?:Qty|Quantity|Units)\s*[:\-]?\s*{_NUMBER}",
        kind="number",
        hint="e.g. 1",
    ),
    FieldSpec(
        key="reason",
        label="Return reason",
        pattern=r"(?:Reason|Cause|Why)\s*[:\-]\s*(.+)",
        hint="e.g. damaged, wrong size",
    ),
    FieldSpec(
        key="refund_total",
        label="Refund total",
        pattern=rf"(?:Refund|Refund Total|Amount|Credit)\s*[:\-]?\s*{_MONEY}",
        kind="money",
        required=False,
        hint="e.g. 184.00",
    ),
    FieldSpec(
        key="customer",
        label="Customer",
        pattern=r"(?:Customer|Client|Buyer)\s*[:\-]\s*(.+)",
        required=False,
        hint="customer name or id",
    ),
]

STOCK_SHEET_FIELDS = [
    FieldSpec(
        key="sku",
        label="SKU",
        pattern=r"(?:SKU|Item|Product Code|Article)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{2,})",
        hint="e.g. SKU-221",
    ),
    FieldSpec(
        key="description",
        label="Description",
        pattern=r"(?:Description|Product|Item Name|Name)\s*[:\-]\s*(.+)",
        hint="product description",
    ),
    FieldSpec(
        key="on_hand",
        label="On hand",
        pattern=rf"(?:On Hand|In Stock|Stock|Available)\s*[:\-]?\s*{_NUMBER}",
        kind="number",
        hint="e.g. 42",
    ),
    FieldSpec(
        key="unit_cost",
        label="Unit cost",
        pattern=rf"(?:Unit Cost|Cost|Unit Price|Price)\s*[:\-]?\s*{_MONEY}",
        kind="money",
        hint="e.g. 72.50",
    ),
    FieldSpec(
        key="markdown_pct",
        label="Markdown",
        pattern=r"(?:Markdown|Clearance|Discount)\s*[:\-]?\s*(\d+(?:\.\d+)?\s?%)",
        kind="percent",
        required=False,
        hint="e.g. 25%",
    ),
    FieldSpec(
        key="location",
        label="Location",
        pattern=r"(?:Location|Warehouse|Bin)\s*[:\-]\s*(.+)",
        required=False,
        hint="warehouse or bin",
    ),
]

GENERIC_FIELDS: list[FieldSpec] = []

SCHEMAS: dict[DocType, list[FieldSpec]] = {
    DocType.PURCHASE_ORDER: PURCHASE_ORDER_FIELDS,
    DocType.RETURN_SLIP: RETURN_SLIP_FIELDS,
    DocType.STOCK_SHEET: STOCK_SHEET_FIELDS,
    DocType.GENERIC: GENERIC_FIELDS,
}


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()
