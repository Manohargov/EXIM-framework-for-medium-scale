"""Shipping notices: the copy rule, the poster, the event and the transit panel.

The notice is the transit counterpart of the clearance poster: same palette and
geometry, but it addresses one buyer with an ETA instead of a price.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from ecomops.advert import (
    PORTRAIT,
    SQUARE,
    AdvertError,
    Notice,
    Photo,
    build_notice_copy,
    notice_bytes,
    render_notice,
)
from ecomops.bus import EventBus
from ecomops.events import TransitNoticeSent, event_for
from ecomops.promotion import NoticeReport, publish_transit_notice
from ecomops.rules import build_rule_llm, write_transit_copy


def notice(**overrides) -> Notice:
    base = {
        "company": "Maison Nord",
        "shipment_id": "SHP-0007",
        "order_id": "ORD-0007",
        "customer_id": "CUST-001",
        "destination": "Lisbon",
        "carrier": "SwiftExpress",
        "service_level": "next-day",
        "eta_days": 1.0,
        "items": (Photo("Wireless earbuds"),),
        "quantity": 2,
    }
    base.update(overrides)
    return Notice(**base)


# ---------------------------------------------------------------------- model

@pytest.mark.parametrize(
    "overrides",
    [
        {"company": " "},
        {"shipment_id": ""},
        {"customer_id": ""},
        {"destination": ""},
        {"carrier": ""},
        {"eta_days": -1},
        {"quantity": 0},
    ],
)
def test_notice_validation(overrides):
    with pytest.raises(AdvertError):
        notice(**overrides)


@pytest.mark.parametrize(
    ("eta", "label", "sentence"),
    [(0, "TODAY", "today"), (1, "IN 1 DAY", "tomorrow"), (1.4, "IN 1 DAY", "tomorrow"),
     (3, "IN 3 DAYS", "in 3 days")],
)
def test_eta_reads_naturally(eta, label, sentence):
    item = notice(eta_days=eta)
    assert item.eta_label == label
    assert item.eta_sentence == sentence


def test_caption_lists_the_parcel_and_the_tracking_id():
    caption = notice().caption()
    assert caption.splitlines()[0] == "Hi CUST-001 — order ORD-0007 is on its way."
    assert "2 x Wireless earbuds" in caption
    assert "SwiftExpress next-day · arriving tomorrow to Lisbon." in caption
    assert "Track it with SHP-0007." in caption


# ----------------------------------------------------------------------- copy

def test_rule_registered_and_schema_shaped():
    data = write_transit_copy({
        "carrier": "SwiftExpress", "service_level": "next-day", "destination": "Lisbon",
        "shipment_id": "SHP-0007", "eta_days": 1, "items": ["Wireless earbuds"],
        "quantity": 2, "tone": "bold",
    })
    assert set(data) >= {"badge", "headline", "subheadline", "tagline", "cta"}
    assert data["badge"] == "IN TRANSIT"
    assert data["headline"] == "ON ITS WAY"
    assert data["subheadline"] == "2 x Wireless earbuds"
    assert "tomorrow" in data["tagline"] and "Lisbon" in data["tagline"]
    assert "write_transit_copy" in build_rule_llm().rules


@pytest.mark.parametrize(
    ("tone", "headline", "cta"),
    [
        ("minimal", "Arriving tomorrow", "TRACK PARCEL"),
        ("playful", "ALMOST THERE!", "FOLLOW IT"),
        ("premium", "EN ROUTE", "TRACK DELIVERY"),
    ],
)
def test_tones_change_the_headline(tone, headline, cta):
    copy = build_notice_copy(notice(), tone=tone)
    assert copy.headline == headline
    assert copy.cta == cta


def test_copy_handles_a_missing_item_list_and_multi_day_eta():
    copy = build_notice_copy(notice(items=(), eta_days=4))
    assert copy.subheadline == "2 x your order"
    assert "in 4 days" in copy.tagline


# --------------------------------------------------------------------- render

PIL = pytest.importorskip("PIL")


def _photo() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 900), (212, 198, 172)).save(buffer, "PNG")
    return buffer.getvalue()


def test_notice_is_a_png_at_the_requested_size():
    from PIL import Image

    data = notice_bytes(notice(items=(Photo("Wireless earbuds", _photo()),)))
    assert data.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == PORTRAIT

    square = notice_bytes(notice(items=(Photo("Wireless earbuds", _photo()),)), size=SQUARE)
    with Image.open(io.BytesIO(square)) as image:
        assert image.size == SQUARE


def test_notice_renders_without_a_photo():
    image = render_notice(notice(items=()))
    assert image.size == PORTRAIT and image.mode == "RGB"


def test_same_day_notice_renders():
    image = render_notice(notice(eta_days=0))
    assert image.mode == "RGB"


# ------------------------------------------------------------------ dispatch

def test_transit_notice_is_recorded_for_one_customer():
    bus = EventBus()
    item = notice()
    report = publish_transit_notice(
        bus, item, caption=item.caption(), channel="sms", sent_by="md@msm.local",
    )

    assert isinstance(report, NoticeReport)
    assert report.customer_id == "CUST-001"
    assert "SHP-0007 sent to CUST-001 via sms" in report.summary()

    event = bus.last("TransitNoticeSent")
    assert isinstance(event, TransitNoticeSent)
    assert event.event_id == report.event_id
    assert event.shipment_id == "SHP-0007"
    assert event.order_id == "ORD-0007"
    assert event.customer_id == "CUST-001"
    assert event.carrier == "SwiftExpress"
    assert event.eta_days == pytest.approx(1.0)
    assert event.items == ["Wireless earbuds"]
    assert event.channel == "sms"
    assert event.sent_by == "md@msm.local"


def test_transit_notice_falls_back_to_a_generic_item_line():
    bus = EventBus()
    publish_transit_notice(bus, notice(items=()), caption="c", sent_by="system")
    assert bus.last("TransitNoticeSent").items == ["your order"]


def test_transit_notice_survives_a_json_round_trip():
    bus = EventBus()
    publish_transit_notice(bus, notice(), caption="c", sent_by="system")
    recorded = bus.history[-1].as_dict()
    assert event_for(recorded["topic"], recorded["event"]) == bus.last("TransitNoticeSent")


@pytest.mark.parametrize("channel", ["pigeon", ""])
def test_unknown_channel_is_rejected(channel):
    bus = EventBus()
    with pytest.raises(ValueError, match="unsupported channel"):
        publish_transit_notice(bus, notice(), caption="c", channel=channel)
    assert bus.of_type("TransitNoticeSent") == []


# ----------------------------------------------------------------------- app

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "ecomops" / "app.py"


def _button(at: AppTest, label: str):
    return next(button for button in at.button if button.label == label)


def test_transit_tab_generates_and_sends_a_notice(monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")
    monkeypatch.setenv("ECOMOPS_WEB_IMAGES", "off")
    os.environ["ECOMOPS_AUTH_AUTOLOGIN"] = "md"

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    next(b for b in at.button if b.key == "icon_transit").click().run()
    assert not at.exception, at.exception

    system = at.session_state["system"]
    sku = next(iter(system.inventory.products))
    system.transit.shipments["SHP-0001"] = {
        "order_id": "ORD-0001", "sku": sku, "quantity": 2,
        "customer_id": "CUST-042", "destination": "Lisbon",
        "plan": {"carrier": "SwiftExpress", "service_level": "next-day",
                 "eta_days": 1.0, "cost": 18.5, "risk": "low", "rationale": "urgent"},
    }
    at.run()

    _button(at, "Customer notice").click()
    at.run()
    assert not at.exception, at.exception
    assert any(box.label == "Shipment for the notice" for box in at.selectbox)

    at.text_input(key="transit_company").set_value("Maison Nord")
    _button(at, "Generate notice").click()
    at.run()
    assert not at.exception, at.exception
    assert [button.label for button in at.get("download_button")] == ["Download notice"]
    assert at.session_state["transit_file"].endswith(".png")
    assert "ORD-0001" in at.session_state["transit_caption"]
    assert "CUST-042" in at.session_state["transit_caption"]

    _button(at, "Send to customer").click()
    at.run()
    assert not at.exception, at.exception

    report = at.session_state["transit_report"]
    assert report.shipment_id == "SHP-0001" and report.customer_id == "CUST-042"
    events = system.bus.of_type("TransitNoticeSent")
    assert len(events) == 1
    assert events[0].customer_id == "CUST-042"
    assert events[0].sent_by
    assert any("CUST-042" in str(message.value) for message in at.success)
