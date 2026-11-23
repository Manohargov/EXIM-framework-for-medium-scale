"""Finance and referral read models.

There is no ledger database in this project, so every figure is either derived
from recorded events (reservations, refunds, markdowns) or, for the referral
programme, a clearly-labelled deterministic demo ledger. The same helpers feed
the Finance tab, the API (``GET /finance``) and the assistant, so all three say the
same thing.

Access control lives in ``auth``: this module computes data, it does not decide
who may see it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .inventory import Inventory

# --------------------------------------------------------------------------- #
# Referral programme (deterministic demo ledger)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReferralPartner:
    name: str
    channel: str
    commission_pct: float


REFERRAL_PARTNERS = (
    ReferralPartner("Google Shopping", "paid search", 8.0),
    ReferralPartner("Instagram", "social", 6.5),
    ReferralPartner("Costco Connect", "marketplace", 5.0),
    ReferralPartner("Email newsletter", "owned", 0.0),
    ReferralPartner("Affiliate network", "affiliate", 12.0),
)


@dataclass(frozen=True)
class ReferralRow:
    partner: str
    channel: str
    clicks: int
    orders: int
    revenue: float
    commission: float
    status: str

    @property
    def conversion_pct(self) -> float:
        return round(100.0 * self.orders / self.clicks, 2) if self.clicks else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "partner": self.partner,
            "channel": self.channel,
            "clicks": self.clicks,
            "orders": self.orders,
            "conversion_pct": self.conversion_pct,
            "revenue": round(self.revenue, 2),
            "commission": round(self.commission, 2),
            "status": self.status,
        }


def _seed_int(key: str, *, low: int, high: int) -> int:
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=8).digest()
    span = max(1, high - low)
    return low + int.from_bytes(digest[:4], "big") % span


def referral_rows(seed: str = "msm-2026") -> list[ReferralRow]:
    """Stable, plausible referral performance for the demo (no network)."""
    rows: list[ReferralRow] = []
    for partner in REFERRAL_PARTNERS:
        clicks = _seed_int(f"{seed}:{partner.name}:clicks", low=400, high=9000)
        orders = max(3, int(clicks * _seed_int(f"{seed}:{partner.name}:cvr", low=15, high=52) / 1000))
        average_order = _seed_int(f"{seed}:{partner.name}:aov", low=35, high=120)
        revenue = orders * average_order
        commission = revenue * partner.commission_pct / 100.0
        status = "paid" if _seed_int(f"{seed}:{partner.name}:status", low=0, high=3) else "pending"
        rows.append(
            ReferralRow(
                partner=partner.name,
                channel=partner.channel,
                clicks=clicks,
                orders=orders,
                revenue=float(revenue),
                commission=round(commission, 2),
                status=status,
            )
        )
    rows.sort(key=lambda row: row.revenue, reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Event-derived finance snapshot
# --------------------------------------------------------------------------- #


def finance_snapshot(system) -> dict[str, Any]:
    """Revenue, refunds, markdowns and the referral ledger for one system."""
    bus = system.bus
    inventory: Inventory = system.inventory

    reservations = bus.of_type("StockReserved")
    gross = 0.0
    for reservation in reservations:
        product = inventory.products.get(reservation.sku)
        if product is not None:
            gross += reservation.quantity * product.unit_price

    refunds = bus.of_type("RefundIssued")
    refund_total = sum(event.amount for event in refunds)

    markdown_total = 0.0
    for discount in bus.of_type("DiscountApplied"):
        product = inventory.products.get(discount.sku)
        stock = product.stock if product is not None else 0
        markdown_total += max(0.0, discount.previous_price - discount.new_price) * stock

    inventory_value = sum(product.inventory_value for product in inventory.products.values())
    referrals = referral_rows()
    referral_revenue = sum(row.revenue for row in referrals)
    commission_due = sum(row.commission for row in referrals if row.status == "pending")
    commission_paid = sum(row.commission for row in referrals if row.status == "paid")

    return {
        "orders": len(reservations),
        "gross_revenue": round(gross, 2),
        "refunds": len(refunds),
        "refund_total": round(refund_total, 2),
        "net_revenue": round(gross - refund_total, 2),
        "discounts": len(bus.of_type("DiscountApplied")),
        "markdown_value": round(markdown_total, 2),
        "inventory_value": round(inventory_value, 2),
        "replacements": len(bus.of_type("ReplacementAuthorised")),
        "referrals": [row.as_dict() for row in referrals],
        "referral_revenue": round(referral_revenue, 2),
        "referral_orders": sum(row.orders for row in referrals),
        "commission_due": round(commission_due, 2),
        "commission_paid": round(commission_paid, 2),
        "pending_approvals": len(bus.pending_approvals),
    }


def finance_summary_line(snapshot: dict[str, Any]) -> str:
    """One-line digest used by the assistant and the compact UI."""
    return (
        f"gross ${snapshot['gross_revenue']:,.2f} · "
        f"refunds ${snapshot['refund_total']:,.2f} · "
        f"net ${snapshot['net_revenue']:,.2f} · "
        f"referral revenue ${snapshot['referral_revenue']:,.2f} "
        f"({snapshot['referral_orders']} orders)"
    )
