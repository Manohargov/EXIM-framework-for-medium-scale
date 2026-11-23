"""Resolve extracted document fields against the running system.

The resolver is deterministic and conservative: a value is only promoted to
``known`` when it has a source — the document itself, a catalogue row, or a
domain event. Anything else stays ``not_found`` / ``missing`` and becomes a
question for the operator.

Resolution is idempotent. The pipeline calls :func:`resolve` after extraction,
again after the operator answers, and once more before filling, so a corrected
SKU can enrich the description, price and stock in the same pass.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field as dc_field
from datetime import timedelta
from difflib import SequenceMatcher
from typing import Any

from ..auth import User
from ..events import (
    CustomerRequestReceived,
    DiscountApplied,
    RefundIssued,
    ReturnAssessed,
    ReturnRequested,
    ShipmentRouted,
)
from ..inventory import Product
from .schema import DocField, DocJob, FieldStatus

# A source is always something a person can go and look at.
DOCUMENT_SOURCE = "document"
OPERATOR_PREFIX = "operator"

MIN_CONFIDENCE = 0.8
FUZZY_THRESHOLD = 0.78


@dataclass(slots=True)
class Resolution:
    """What one field resolved to."""

    value: str | None = None
    source: str | None = None
    status: FieldStatus = FieldStatus.MISSING
    confidence: float = 0.0
    hint: str | None = None


@dataclass
class ResolverContext:
    """Indexes over the system, built once per resolution pass."""

    system: Any
    user: User
    products: dict[str, Product] = dc_field(default_factory=dict)
    shipments: list[ShipmentRouted] = dc_field(default_factory=list)
    returns: list[ReturnRequested] = dc_field(default_factory=list)
    assessments: list[ReturnAssessed] = dc_field(default_factory=list)
    refunds: list[RefundIssued] = dc_field(default_factory=list)
    discounts: list[DiscountApplied] = dc_field(default_factory=list)
    requests: list[CustomerRequestReceived] = dc_field(default_factory=list)

    @classmethod
    def build(cls, system: Any, user: User) -> ResolverContext:
        bus = system.bus
        return cls(
            system=system,
            user=user,
            products=dict(system.inventory.products),
            shipments=list(bus.of_type("ShipmentRouted")),
            returns=list(bus.of_type("ReturnRequested")),
            assessments=list(bus.of_type("ReturnAssessed")),
            refunds=list(bus.of_type("RefundIssued")),
            discounts=list(bus.of_type("DiscountApplied")),
            requests=list(bus.of_type("CustomerRequestReceived")),
        )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _product(ctx: ResolverContext, raw: str | None) -> Product | None:
    if not raw:
        return None
    key = raw.strip().upper()
    if key in ctx.products:
        return ctx.products[key]
    return ctx.products.get(key.replace(" ", ""))


def _fuzzy_product(ctx: ResolverContext, text: str) -> tuple[Product | None, float]:
    target = _norm(text)
    if not target:
        return None, 0.0
    best: Product | None = None
    score = 0.0
    for product in ctx.products.values():
        name = _norm(product.name)
        ratio = SequenceMatcher(None, target, name).ratio()
        if target in name or name in target:
            ratio = max(ratio, 0.9)
        if ratio > score:
            best, score = product, ratio
    return (best if score >= FUZZY_THRESHOLD else None), round(score, 2)


def _shipment(
    ctx: ResolverContext,
    *,
    shipment_id: str | None = None,
    order_id: str | None = None,
    sku: str | None = None,
) -> ShipmentRouted | None:
    if shipment_id:
        for event in reversed(ctx.shipments):
            if _norm(event.shipment_id) == _norm(shipment_id):
                return event
    if order_id:
        for event in reversed(ctx.shipments):
            if _norm(event.order_id) == _norm(order_id):
                return event
    if sku:
        for event in reversed(ctx.shipments):
            if _norm(event.sku) == _norm(sku):
                return event
    return None


def _linked_shipment(ctx: ResolverContext, job: DocJob) -> ShipmentRouted | None:
    values = {f.key: f for f in job.fields}

    def value(key: str) -> str | None:
        found = values.get(key)
        return found.answer if found else None

    return _shipment(
        ctx,
        shipment_id=value("shipment_id"),
        order_id=value("order_id"),
        sku=_sku_of(value("sku")),
    )


def _sku_of(raw: str | None) -> str | None:
    """``SKU-104 — Cashmere Scarf`` -> ``SKU-104``."""
    if not raw:
        return None
    match = re.match(r"\s*([A-Za-z]{2,6}-\d{2,8})", raw)
    return match.group(1) if match else raw.strip()


def _refund(ctx: ResolverContext, *, order_id: str | None, sku: str | None) -> RefundIssued | None:
    """Best match first, then order-level, then SKU-level.

    A return slip usually names the order and one line; the refund event may name
    either. Matching on the order is the safer fallback, and the resolved source
    still names the exact event so the operator can verify it.
    """
    same_order = [e for e in ctx.refunds if order_id and _norm(e.order_id) == _norm(order_id)]
    exact = [e for e in same_order if sku and _norm(e.sku) == _norm(sku)]
    if exact:
        return exact[-1]
    if same_order:
        return same_order[-1]
    if sku:
        same_sku = [e for e in ctx.refunds if _norm(e.sku) == _norm(sku)]
        if same_sku:
            return same_sku[-1]
    return None


def _assessment(ctx: ResolverContext, *, order_id: str | None, sku: str | None) -> ReturnAssessed | None:
    same_order = [e for e in ctx.assessments if order_id and _norm(e.order_id) == _norm(order_id)]
    exact = [e for e in same_order if sku and _norm(e.sku) == _norm(sku)]
    if exact:
        return exact[-1]
    if same_order:
        return same_order[-1]
    if sku:
        same_sku = [e for e in ctx.assessments if _norm(e.sku) == _norm(sku)]
        if same_sku:
            return same_sku[-1]
    return None


def _customer(ctx: ResolverContext, raw: str | None) -> CustomerRequestReceived | None:
    target = _norm(raw)
    if not target:
        return None
    for event in reversed(ctx.requests):
        if target == _norm(event.customer_id) or target in _norm(event.customer_id):
            return event
    return None


# --------------------------------------------------------------------------- #
# Derivation: fill gaps from ids already present on the document
# --------------------------------------------------------------------------- #

def _derive_missing(ctx: ResolverContext, job: DocJob) -> None:
    """Answer blank fields from records the document already points at."""
    by_key = {f.key: f for f in job.fields}
    ship = _linked_shipment(ctx, job)
    sku = _sku_of(
        by_key["sku"].answer if "sku" in by_key else None
    ) or (ship.sku if ship else None)
    product = _product(ctx, sku)
    order_id = (by_key.get("order_id").answer if by_key.get("order_id") else None) or (
        ship.order_id if ship else None
    )

    def fill(key: str, value: str | None, source: str, confidence: float = 0.9) -> None:
        target = by_key.get(key)
        if target is None or target.answer:
            return
        if value in (None, ""):
            return
        target.resolved = str(value)
        target.source = source
        target.status = FieldStatus.KNOWN
        target.confidence = confidence

    if product is not None:
        fill("description", product.name, f"catalogue:{product.sku}", 0.95)
        fill("on_hand", product.stock, f"catalogue:{product.sku}", 0.95)
        fill("unit_cost", _money(product.unit_price), f"catalogue:{product.sku}", 0.8)
        fill("unit_price", _money(product.unit_price), f"catalogue:{product.sku}", 0.95)

    if ship is not None:
        fill("shipment_id", ship.shipment_id, f"ShipmentRouted:{ship.shipment_id}", 0.95)
        fill("order_id", ship.order_id, f"ShipmentRouted:{ship.shipment_id}", 0.95)
        fill("ship_to", ship.destination, f"ShipmentRouted:{ship.shipment_id}", 0.9)
        fill("customer", ship.customer_id, f"ShipmentRouted:{ship.shipment_id}", 0.9)
        fill("quantity", ship.quantity, f"ShipmentRouted:{ship.shipment_id}", 0.85)
        eta = (ship.ts + timedelta(days=float(ship.plan.eta_days or 0))).date().isoformat()
        fill("requested_delivery", eta, f"ShipmentRouted:{ship.shipment_id} · {ship.plan.carrier}", 0.9)

    refund = _refund(ctx, order_id=order_id, sku=sku)
    if refund is not None:
        fill("refund_total", _money(refund.amount), f"RefundIssued:{refund.order_id or refund.sku}", 0.97)
        fill("refund_amount", _money(refund.amount), f"RefundIssued:{refund.order_id or refund.sku}", 0.97)
    else:
        assessed = _assessment(ctx, order_id=order_id, sku=sku)
        if assessed is not None and assessed.assessment.refund_amount:
            fill(
                "refund_total",
                _money(assessed.assessment.refund_amount),
                f"ReturnAssessed:{assessed.order_id or assessed.sku}",
                0.9,
            )

    discount = next(
        (d for d in reversed(ctx.discounts) if sku and _norm(d.sku) == _norm(sku)), None
    )
    if discount is not None:
        fill("markdown_pct", f"{discount.discount_pct:g}%", f"DiscountApplied:{discount.sku}", 0.9)


# --------------------------------------------------------------------------- #
# Field resolvers
# --------------------------------------------------------------------------- #

def _document_backed(field: DocField, confidence: float = 0.85) -> Resolution:
    if field.value:
        return Resolution(field.value, DOCUMENT_SOURCE, FieldStatus.KNOWN, confidence)
    if field.resolved:
        return Resolution(field.resolved, field.source or DOCUMENT_SOURCE, FieldStatus.KNOWN, confidence)
    return Resolution(status=FieldStatus.MISSING, hint=field.hint)


def _resolve_document(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    return _document_backed(field)


def _resolve_sku(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    raw = field.value or field.resolved
    if not raw:
        return Resolution(status=FieldStatus.MISSING, hint="No SKU on the document or in the system.")
    product = _product(ctx, _sku_of(raw))
    if product is not None:
        return Resolution(
            f"{product.sku} — {product.name}",
            f"catalogue:{product.sku}",
            FieldStatus.KNOWN,
            0.98,
        )
    candidate, score = _fuzzy_product(ctx, raw)
    if candidate is not None:
        return Resolution(
            None,
            None,
            FieldStatus.AMBIGUOUS,
            score,
            hint=f"Did you mean {candidate.sku} — {candidate.name}?",
        )
    return Resolution(
        None,
        None,
        FieldStatus.NOT_FOUND,
        0.0,
        hint=f"{raw} is not in the catalogue. e.g. SKU-1001",
    )


def _resolve_description(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    sku = _sku_of(_value(job, "sku"))
    product = _product(ctx, sku)
    if product is not None:
        return Resolution(product.name, f"catalogue:{product.sku}", FieldStatus.KNOWN, 0.97)
    if field.value:
        return _document_backed(field, 0.8)
    return Resolution(status=FieldStatus.MISSING, hint=field.hint or "product description")


def _resolve_on_hand(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    product = _product(ctx, _sku_of(_value(job, "sku")))
    if product is not None:
        return Resolution(str(product.stock), f"catalogue:{product.sku}", FieldStatus.KNOWN, 0.97)
    if field.value:
        return _document_backed(field, 0.7)
    return Resolution(status=FieldStatus.MISSING, hint="units on hand")


def _resolve_price(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    product = _product(ctx, _sku_of(_value(job, "sku")))
    if product is not None:
        return Resolution(_money(product.unit_price), f"catalogue:{product.sku}", FieldStatus.KNOWN, 0.95)
    if field.value:
        return _document_backed(field, 0.8)
    return Resolution(status=FieldStatus.MISSING, hint=field.hint or "unit price")


def _resolve_money(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    if field.value:
        return _document_backed(field, 0.85)
    shipment = _linked_shipment(ctx, job)
    if shipment is not None and shipment.order_value:
        return Resolution(
            _money(shipment.order_value),
            f"ShipmentRouted:{shipment.shipment_id}",
            FieldStatus.KNOWN,
            0.9,
        )
    return Resolution(status=FieldStatus.MISSING, hint=field.hint or "amount")


def _resolve_customer(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    if field.value:
        event = _customer(ctx, field.value)
        if event is not None:
            return Resolution(
                event.customer_id,
                f"CustomerRequestReceived:{event.customer_id}",
                FieldStatus.KNOWN,
                0.95,
            )
        return _document_backed(field, 0.85)
    shipment = _linked_shipment(ctx, job)
    if shipment is not None and shipment.customer_id:
        return Resolution(
            shipment.customer_id,
            f"ShipmentRouted:{shipment.shipment_id}",
            FieldStatus.KNOWN,
            0.9,
        )
    return Resolution(status=FieldStatus.MISSING, hint=field.hint or "customer name or id")


def _resolve_shipment(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    event = _shipment(ctx, shipment_id=field.value)
    if event is not None:
        return Resolution(
            event.shipment_id,
            f"ShipmentRouted:{event.shipment_id}",
            FieldStatus.KNOWN,
            0.97,
        )
    if field.value:
        return Resolution(
            None,
            None,
            FieldStatus.NOT_FOUND,
            0.0,
            hint=f"No shipment {field.value} in the event store.",
        )
    return Resolution(status=FieldStatus.MISSING, hint="shipment or tracking id")


def _resolve_order(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    event = _shipment(ctx, order_id=field.value)
    if event is not None:
        return Resolution(
            event.order_id,
            f"ShipmentRouted:{event.shipment_id}",
            FieldStatus.KNOWN,
            0.97,
        )
    if field.value:
        return _document_backed(field, 0.8)
    return Resolution(status=FieldStatus.MISSING, hint="order id")


def _resolve_return(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    if field.value:
        return _document_backed(field, 0.85)
    return Resolution(status=FieldStatus.MISSING, hint="return reference")


def _resolve_refund(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    order_id = _value(job, "order_id")
    sku = _sku_of(_value(job, "sku"))
    refund = _refund(ctx, order_id=order_id, sku=sku)
    if refund is not None:
        return Resolution(
            _money(refund.amount),
            f"RefundIssued:{refund.order_id or refund.sku}",
            FieldStatus.KNOWN,
            0.97,
        )
    assessed = _assessment(ctx, order_id=order_id, sku=sku)
    if assessed is not None and assessed.assessment.refund_amount:
        return Resolution(
            _money(assessed.assessment.refund_amount),
            f"ReturnAssessed:{assessed.order_id or assessed.sku}",
            FieldStatus.KNOWN,
            0.9,
        )
    if field.value:
        return _document_backed(field, 0.85)
    shipment = _linked_shipment(ctx, job)
    if shipment is not None and shipment.order_value:
        quantity = shipment.quantity or 1
        return Resolution(
            _money(shipment.order_value * quantity),
            f"ShipmentRouted:{shipment.shipment_id}",
            FieldStatus.KNOWN,
            0.85,
        )
    return Resolution(status=FieldStatus.MISSING, hint="refund amount")


def _resolve_markdown(ctx: ResolverContext, job: DocJob, field: DocField) -> Resolution:
    sku = _sku_of(_value(job, "sku"))
    discount = next((d for d in reversed(ctx.discounts) if sku and _norm(d.sku) == _norm(sku)), None)
    if discount is not None:
        return Resolution(f"{discount.discount_pct:g}%", f"DiscountApplied:{discount.sku}", FieldStatus.KNOWN, 0.9)
    if field.value:
        return _document_backed(field, 0.8)
    return Resolution(status=FieldStatus.MISSING, hint="markdown %")


def _value(job: DocJob, key: str) -> str | None:
    field = next((f for f in job.fields if f.key == key), None)
    return field.answer if field else None


_HANDLERS: dict[str, Callable[[ResolverContext, DocJob, DocField], Resolution]] = {
    "po_number": _resolve_document,
    "order_date": _resolve_document,
    "return_id": _resolve_return,
    "reason": _resolve_document,
    "location": _resolve_document,
    "ship_to": _resolve_document,
    "requested_delivery": _resolve_document,
    "sku": _resolve_sku,
    "description": _resolve_description,
    "on_hand": _resolve_on_hand,
    "unit_cost": _resolve_price,
    "unit_price": _resolve_price,
    "order_total": _resolve_money,
    "refund_total": _resolve_refund,
    "refund_amount": _resolve_refund,
    "customer": _resolve_customer,
    "shipment_id": _resolve_shipment,
    "order_id": _resolve_order,
    "markdown_pct": _resolve_markdown,
}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _apply(field: DocField, resolution: Resolution) -> None:
    if field.source and field.source.startswith(OPERATOR_PREFIX):
        return  # the operator's correction wins over every automatic pass
    field.resolved = resolution.value
    field.source = resolution.source
    field.status = resolution.status
    field.confidence = resolution.confidence
    if resolution.hint:
        field.hint = resolution.hint


def resolve(system: Any, user: User, job: DocJob) -> dict[str, Any]:
    """Resolve every field in ``job`` in place; return a compact summary."""
    ctx = ResolverContext.build(system, user)
    _derive_missing(ctx, job)
    for field in job.fields:
        _apply(field, _HANDLERS.get(field.key, _resolve_document)(ctx, job, field))

    asked = [f for f in job.fields if f.needs_input and f.required]
    optional = [f for f in job.fields if f.needs_input and not f.required]
    job.status = "awaiting_input" if asked else "review"
    return {
        "known": sum(1 for f in job.fields if not f.needs_input),
        "total": len(job.fields),
        "asked": asked,
        "optional": optional,
        "sources": {f.key: f.source or "" for f in job.fields if f.source},
    }


def apply_answers(user: User, job: DocJob, answers: dict[str, str]) -> list[str]:
    """Store operator answers; return the keys that were updated."""
    updated: list[str] = []
    for key, raw in answers.items():
        field = next((f for f in job.fields if f.key == key), None)
        if field is None:
            continue
        value = (raw or "").strip()
        field.resolved = value  # "" means the operator chose to leave it blank
        field.source = f"{OPERATOR_PREFIX}:{user.email}"
        field.status = FieldStatus.KNOWN
        field.confidence = 1.0
        field.hint = None
        updated.append(key)
    return updated


def parse_answers(text: str, job: DocJob) -> dict[str, str]:
    """Parse ``key=value`` / ``Label: value`` lines, or a bare value for one gap."""
    answers: dict[str, str] = {}
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    missing = [f for f in job.fields if f.needs_input]
    by_key = {f.key.lower(): f for f in job.fields}
    by_label = {_norm(f.label): f for f in job.fields}

    for line in lines:
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _/-]{1,40})\s*[=:]\s*(.*)$", line)
        if match:
            name, value = match.group(1), match.group(2).strip()
            field = by_key.get(_norm(name).replace(" ", "_")) or by_label.get(_norm(name))
            if field is not None:
                answers[field.key] = value
                continue
        if len(missing) == 1:
            answers[missing[0].key] = line
    return answers
