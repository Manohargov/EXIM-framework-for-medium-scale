"""Pushing a finished poster to customers: audience, event and report."""

from __future__ import annotations

import pytest

from ecomops.advert import Offer
from ecomops.bus import EventBus
from ecomops.events import CustomerRequestReceived, PromotionPublished, event_for
from ecomops.promotion import CHANNELS, DEMO_AUDIENCE, audience, publish_promotion


def offer(**overrides) -> Offer:
    base = {
        "company": "Maison Nord",
        "product_name": "Cold-Pressed Olive Oil 750ml",
        "old_price": 24.0,
        "new_price": 16.8,
        "sku": "GRO-0552",
    }
    base.update(overrides)
    return Offer(**base)


def _request(bus: EventBus, customer_id: str) -> None:
    bus.publish(CustomerRequestReceived(customer_id=customer_id, request_text="where is my order?"))


def test_audience_falls_back_to_the_demo_list():
    assert audience(EventBus()) == DEMO_AUDIENCE


def test_audience_is_every_customer_on_file_in_a_stable_order():
    bus = EventBus()
    _request(bus, "CUST-009")
    _request(bus, "CUST-002")
    _request(bus, "CUST-009")          # duplicates collapse
    assert audience(bus) == ("CUST-002", "CUST-009")


def test_publish_records_a_promotion_event():
    bus = EventBus()
    _request(bus, "CUST-004")

    report = publish_promotion(
        bus, offer(), caption="Was $24.00, now $16.80", channel="sms",
        published_by="md@msm.local", image_bytes=4096,
    )

    assert report.count == 1
    assert report.recipients == ("CUST-004",)
    assert report.channel == "sms"
    assert "1 customer via sms" in report.summary()

    events = bus.of_type("PromotionPublished")
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, PromotionPublished)
    assert event.event_id == report.event_id
    assert event.company == "Maison Nord"
    assert event.items == ["Cold-Pressed Olive Oil 750ml"]
    assert event.old_price == pytest.approx(24.0)
    assert event.new_price == pytest.approx(16.8)
    assert event.recipients == ["CUST-004"]
    assert event.published_by == "md@msm.local"
    assert event.source == "md@msm.local"


def test_promotion_event_survives_a_json_round_trip():
    bus = EventBus()
    report = publish_promotion(
        bus, offer(photos=()), caption="caption", published_by="system",
    )
    recorded = bus.history[-1].as_dict()
    rebuilt = event_for(recorded["topic"], recorded["event"])
    assert rebuilt == bus.of_type("PromotionPublished")[0]
    assert rebuilt.event_id == report.event_id


def test_explicit_recipients_override_the_audience():
    bus = EventBus()
    report = publish_promotion(
        bus, offer(), caption="c", recipients=["CUST-777", "CUST-888"], channel="whatsapp",
    )
    assert report.recipients == ("CUST-777", "CUST-888")
    assert bus.last("PromotionPublished").recipients == ["CUST-777", "CUST-888"]


def test_unknown_channel_is_rejected_before_anything_is_published():
    bus = EventBus()
    with pytest.raises(ValueError, match="unsupported channel"):
        publish_promotion(bus, offer(), caption="c", channel="carrier-pigeon")
    assert bus.of_type("PromotionPublished") == []
    assert CHANNELS == ("email", "sms", "whatsapp")


def test_caption_and_multi_item_offers_are_carried_through():
    from ecomops.advert import Photo

    bus = EventBus()
    item = offer(photos=tuple(Photo(name) for name in ("Olive Oil", "Mug", "Stand")))
    report = publish_promotion(bus, item, caption="three items, one price")
    event = bus.last("PromotionPublished")
    assert event.items == ["Olive Oil", "Mug", "Stand"]
    assert event.caption == "three items, one price"
    assert report.product_name == "Cold-Pressed Olive Oil 750ml"
