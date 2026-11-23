"""Pushing a finished advertisement to customers.

The poster itself is a file; "send it" is a business event. This module builds
the audience from the bus (every customer who has ever contacted the shop, plus
a deterministic demo fallback), publishes one :class:`PromotionPublished` event
so the send is auditable in the Event trace and metrics, and returns a report the
UI can show.

No SMTP or SMS gateway is wired in: the channel is recorded, not dialled. That
keeps the demo honest — the event says what would have been sent, to whom, and
by which operator — without pretending a third-party delivery succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .advert import Notice, Offer
from .bus import EventBus
from .events import PromotionPublished, TransitNoticeSent

DEMO_AUDIENCE = ("CUST-001", "CUST-002", "CUST-003")
CHANNELS = ("email", "sms", "whatsapp")


@dataclass(frozen=True)
class DispatchReport:
    """What was sent, to whom, and the event that records it."""

    event_id: str
    recipients: tuple[str, ...]
    channel: str
    sent_at: datetime
    company: str
    product_name: str

    @property
    def count(self) -> int:
        return len(self.recipients)

    def summary(self) -> str:
        who = f"{self.count} customer" + ("" if self.count == 1 else "s")
        return f"Poster pushed to {who} via {self.channel}."


@dataclass(frozen=True)
class NoticeReport:
    """What was sent to one customer about their shipment."""

    event_id: str
    shipment_id: str
    customer_id: str
    channel: str
    sent_at: datetime

    def summary(self) -> str:
        return f"Notice for {self.shipment_id} sent to {self.customer_id} via {self.channel}."


def audience(bus: EventBus) -> tuple[str, ...]:
    """Customers known to the system, in a stable order.

    Anyone who has raised a request counts; with an empty history the demo
    audience is used so the button still does something visible.
    """
    seen = {
        event.customer_id
        for event in bus.of_type("CustomerRequestReceived")
        if getattr(event, "customer_id", "")
    }
    return tuple(sorted(seen)) or DEMO_AUDIENCE


def publish_promotion(
    bus: EventBus,
    offer: Offer,
    *,
    caption: str,
    recipients: tuple[str, ...] | list[str] | None = None,
    channel: str = "email",
    published_by: str = "",
    image_bytes: int = 0,
) -> DispatchReport:
    """Publish the promotion and return the delivery report.

    ``image_bytes`` is only recorded for the operator's benefit; the event keeps
    a size rather than the blob so the bus history stays small and JSON-safe.
    """
    targets = tuple(recipients) if recipients else audience(bus)
    if not targets:
        raise ValueError("no customers to send the promotion to")
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel {channel!r}; use one of {', '.join(CHANNELS)}")

    event = PromotionPublished(
        source=published_by or "advert-generator",
        company=offer.company,
        product_name=offer.product_name,
        sku=offer.sku,
        old_price=offer.old_price,
        new_price=offer.new_price,
        currency=offer.currency,
        items=list(offer.item_names),
        caption=caption,
        channel=channel,
        recipients=list(targets),
        published_by=published_by,
    )
    recorded = bus.publish(event)
    del image_bytes  # kept out of the event on purpose
    return DispatchReport(
        event_id=recorded.event_id,
        recipients=targets,
        channel=channel,
        sent_at=datetime.now(UTC),
        company=offer.company,
        product_name=offer.product_name,
    )


def publish_transit_notice(
    bus: EventBus,
    notice: Notice,
    *,
    caption: str,
    channel: str = "email",
    sent_by: str = "",
) -> NoticeReport:
    """Tell one buyer their parcel is moving; the event is the audit trail.

    Unlike :func:`publish_promotion` the audience is a single customer, taken
    from the shipment itself — a tracking note must never become a broadcast.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel {channel!r}; use one of {', '.join(CHANNELS)}")
    if not notice.customer_id.strip():
        raise ValueError("the shipment has no customer to notify")

    event = TransitNoticeSent(
        source=sent_by or "transit-notice",
        shipment_id=notice.shipment_id,
        order_id=notice.order_id,
        customer_id=notice.customer_id,
        destination=notice.destination,
        carrier=notice.carrier,
        service_level=notice.service_level,
        eta_days=notice.eta_days,
        items=list(notice.item_names) or ["your order"],
        caption=caption,
        channel=channel,
        sent_by=sent_by,
    )
    recorded = bus.publish(event)
    return NoticeReport(
        event_id=recorded.event_id,
        shipment_id=notice.shipment_id,
        customer_id=notice.customer_id,
        channel=channel,
        sent_at=datetime.now(UTC),
    )


__all__ = [
    "CHANNELS",
    "DEMO_AUDIENCE",
    "DispatchReport",
    "NoticeReport",
    "audience",
    "publish_promotion",
    "publish_transit_notice",
]
