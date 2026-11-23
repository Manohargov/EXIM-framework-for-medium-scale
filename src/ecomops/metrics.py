"""Operational metrics derived from the event history.

There is no separate metrics store: the bus history is already the source of
truth, so every counter is computed from recorded events. That keeps the demo
and a real deployment consistent — if the event happened, it is counted.

``snapshot`` returns a JSON-friendly dict for the HTTP API; ``prometheus``
renders the same data as Prometheus text format for a scrape endpoint.
"""

from __future__ import annotations

from typing import Any

from .bus import EventBus
from .inventory import Inventory


def snapshot(bus: EventBus, inventory: Inventory) -> dict[str, Any]:
    """Counters and gauges for the whole system."""
    history = bus.history
    topics: dict[str, int] = {}
    conversations: set[str] = set()
    for record in history:
        topics[record.topic] = topics.get(record.topic, 0) + 1
        if record.event.correlation_id:
            conversations.add(record.event.correlation_id)

    approved = bus.of_type("ActionApproved")
    rejected = bus.of_type("ActionRejected")
    refunds = bus.of_type("RefundIssued")
    products = list(inventory.products.values())

    return {
        "events": len(history),
        "topics": topics,
        "conversations": len(conversations),
        "pending_approvals": len(bus.pending_approvals),
        "approval_requests": len(bus.of_type("ApprovalRequested")),
        "actions_approved": len(approved),
        "actions_rejected": len(rejected),
        "autonomous_approvals": sum(1 for e in approved if e.mode == "policy"),
        "human_approvals": sum(1 for e in approved if e.mode == "human"),
        "reservations": len(bus.of_type("StockReserved")),
        "reservation_failures": len(bus.of_type("StockReservationFailed")),
        "shipments_routed": len(bus.of_type("ShipmentRouted")),
        "discounts_applied": len(bus.of_type("DiscountApplied")),
        "exceptions_resolved": len(bus.of_type("DeliveryExceptionResolved")),
        "returns_assessed": len(bus.of_type("ReturnAssessed")),
        "returns_rejected": len(bus.of_type("ReturnRejected")),
        "refunds_issued": len(refunds),
        "refund_amount_total": round(sum(e.amount for e in refunds), 2),
        "replacements_authorised": len(bus.of_type("ReplacementAuthorised")),
        "escalations": len(bus.of_type("EscalationRaised")),
        "products": len(products),
        "inventory_units": sum(p.stock for p in products),
        "inventory_value": round(sum(p.inventory_value for p in products), 2),
    }


def prometheus(snap: dict[str, Any]) -> str:
    """Render a snapshot in Prometheus text exposition format."""
    lines = [
        "# HELP ecomops_events_total Total events recorded on the bus.",
        "# TYPE ecomops_events_total counter",
        f"ecomops_events_total {snap['events']}",
        "# HELP ecomops_events_by_topic_total Events by topic.",
        "# TYPE ecomops_events_by_topic_total counter",
    ]
    for topic, count in sorted(snap["topics"].items()):
        lines.append(f'ecomops_events_by_topic_total{{topic="{_escape(topic)}"}} {count}')

    gauges = [
        ("ecomops_conversations", "Distinct correlation ids.", snap["conversations"]),
        ("ecomops_pending_approvals", "Approvals waiting for a human.", snap["pending_approvals"]),
        ("ecomops_actions_approved_total", "Actions approved (all modes).", snap["actions_approved"]),
        ("ecomops_actions_rejected_total", "Actions rejected.", snap["actions_rejected"]),
        ("ecomops_autonomous_approvals_total", "Actions approved by policy.", snap["autonomous_approvals"]),
        ("ecomops_human_approvals_total", "Actions approved by a human.", snap["human_approvals"]),
        ("ecomops_reservations_total", "Stock reservations created.", snap["reservations"]),
        ("ecomops_reservation_failures_total", "Reservations that could not be met.", snap["reservation_failures"]),
        ("ecomops_shipments_routed_total", "Shipments routed.", snap["shipments_routed"]),
        ("ecomops_discounts_applied_total", "Markdowns applied.", snap["discounts_applied"]),
        ("ecomops_exceptions_resolved_total", "Delivery exceptions resolved.", snap["exceptions_resolved"]),
        ("ecomops_returns_assessed_total", "Return requests assessed.", snap["returns_assessed"]),
        ("ecomops_returns_rejected_total", "Returns rejected by policy or a human.", snap["returns_rejected"]),
        ("ecomops_refunds_issued_total", "Refunds issued.", snap["refunds_issued"]),
        ("ecomops_refund_amount_total", "Value refunded.", snap["refund_amount_total"]),
        ("ecomops_replacements_authorised_total", "Replacements authorised.", snap["replacements_authorised"]),
        ("ecomops_escalations_total", "Escalations raised.", snap["escalations"]),
        ("ecomops_inventory_units", "Units on hand.", snap["inventory_units"]),
        ("ecomops_inventory_value", "Inventory value at current prices.", snap["inventory_value"]),
    ]
    for name, help_text, value in gauges:
        kind = "counter" if name.endswith("_total") else "gauge"
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
