"""Advertisement posters: copy rules, rendering and the stock-clearance wiring.

The layout is deterministic, so the tests assert on pixels only where it matters
(size, format, valid decode). Copy is asserted exactly — that is the part a
merchant reads.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from ecomops.advert import (
    MAX_PHOTOS,
    PORTRAIT,
    SQUARE,
    AdvertCopy,
    AdvertError,
    Offer,
    Photo,
    build_copy,
    poster_bytes,
    render_poster,
    save_poster,
)
from ecomops.rules import build_rule_llm, write_advert_copy


def offer(**overrides) -> Offer:
    base = {
        "company": "Maison Nord",
        "product_name": "Cold-Pressed Olive Oil 750ml",
        "old_price": 24.0,
        "new_price": 16.8,
        "sku": "GRO-0552",
        "category": "Pantry",
        "currency": "$",
    }
    base.update(overrides)
    return Offer(**base)


# --------------------------------------------------------------------------- copy

def test_offer_derives_discount_and_saving():
    item = offer()
    assert item.discount_pct == pytest.approx(30.0)
    assert item.saving == pytest.approx(7.2)
    assert item.has_discount
    assert item.price(24.0) == "$24.00"


def test_copy_carries_price_and_discount_into_the_offer_lines():
    copy = build_copy(offer(), tone="bold")
    assert copy.badge == "-30%"
    assert copy.headline == "PRICE DROP"
    assert copy.subheadline == "Cold-Pressed Olive Oil 750ml"
    assert "$7.20" in copy.tagline
    assert "pantry" in copy.hashtags and "30off" in copy.hashtags


@pytest.mark.parametrize(
    ("tone", "headline"),
    [("minimal", "30% off"), ("playful", "SNAP IT UP"), ("premium", "FINAL EDITION")],
)
def test_tones_change_the_headline(tone, headline):
    assert build_copy(offer(), tone=tone).headline == headline


def test_no_discount_falls_back_to_an_offer_message():
    copy = build_copy(offer(new_price=24.0), tone="bold")
    assert copy.badge == "OFFER"
    assert copy.headline == "NOW IN STOCK"
    assert "30off" not in copy.hashtags


def test_caption_is_ready_to_paste():
    item = offer()
    caption = build_copy(item).caption(item)
    assert "Maison Nord — Cold-Pressed Olive Oil 750ml" in caption
    assert "Was $24.00, now $16.80 (30% off, save $7.20)" in caption
    assert "#pantry" in caption


def test_rule_registered_and_schema_shaped():
    rule = write_advert_copy({
        "product_name": "Olive Oil", "old_price": 24, "new_price": 16.8,
        "tone": "premium", "category": "Pantry", "currency": "$",
    })
    assert set(rule) == {"badge", "headline", "subheadline", "tagline", "cta", "hashtags"}
    assert rule["headline"] == "FINAL EDITION"
    assert "write_advert_copy" in build_rule_llm().rules


def test_model_copy_is_coerced_and_blanks_fall_back():
    item = offer()
    copy = AdvertCopy.from_payload(
        {"headline": "", "badge": "30 %", "hashtags": ["#Sale", "clearance"]}, offer=item
    )
    assert copy.headline == "PRICE DROP"      # blank → rule copy
    assert copy.badge == "30 %"
    assert copy.hashtags == ("Sale", "clearance")


@pytest.mark.parametrize(
    "overrides",
    [
        {"company": "  "},
        {"product_name": ""},
        {"old_price": 0},
        {"new_price": 0},
        {"new_price": 30.0},          # above the old price
    ],
)
def test_offer_validation(overrides):
    with pytest.raises(AdvertError):
        offer(**overrides)


# --------------------------------------------------------------------------- render

PIL = pytest.importorskip("PIL")


def _sample_photo() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (900, 1100), (232, 224, 208))
    ImageDraw.Draw(image).rounded_rectangle((300, 200, 600, 900), radius=60, fill=(74, 96, 74))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_poster_is_a_png_at_the_requested_size():
    data = poster_bytes(offer(image=_sample_photo()))
    assert data.startswith(b"\x89PNG")
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        assert image.size == PORTRAIT

    square = poster_bytes(offer(image=_sample_photo()), size=SQUARE)
    with Image.open(io.BytesIO(square)) as image:
        assert image.size == SQUARE


def test_poster_renders_without_a_photo_and_without_a_discount():
    image = render_poster(offer(image=None, new_price=24.0))
    assert image.size == PORTRAIT
    assert image.mode == "RGB"


def test_save_poster_picks_format_from_the_suffix(tmp_path: Path):
    png = save_poster(offer(), None, tmp_path / "poster.png")
    jpg = save_poster(offer(), None, tmp_path / "poster.jpg")
    assert png.read_bytes().startswith(b"\x89PNG")
    assert jpg.read_bytes().startswith(b"\xff\xd8")


def test_unreadable_image_is_a_domain_error():
    with pytest.raises(AdvertError):
        poster_bytes(offer(image=b"not an image"))


# --------------------------------------------------------------------------- app

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "ecomops" / "app.py"


def _button(at: AppTest, label: str):
    return next(button for button in at.button if button.label == label)


def _nav(at: AppTest, label: str) -> None:
    keys = {"Stock clearance": "icon_stock"}
    next(button for button in at.button if button.key == keys[label]).click().run()


def test_stock_tab_generates_a_downloadable_poster(monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")
    monkeypatch.setenv("ECOMOPS_WEB_IMAGES", "off")   # tests never touch the network
    os.environ["ECOMOPS_AUTH_AUTOLOGIN"] = "md"

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    _nav(at, "Stock clearance")
    assert not at.exception, at.exception

    # The generator sits next to the warehouse analysis, as a second action.
    labels = [button.label for button in at.button]
    assert "Analyse warehouse stock" in labels and "Adv generator" in labels

    _button(at, "Adv generator").click()
    at.run()
    assert not at.exception, at.exception
    assert any(box.label == "Item for offer" for box in at.selectbox)
    assert any(field.label == "Company name" for field in at.text_input)

    at.text_input(key="advert_company").set_value("Maison Nord")
    _button(at, "Generate poster").click()
    at.run()
    assert not at.exception, at.exception

    downloads = at.get("download_button")
    assert [button.label for button in downloads] == ["Download poster"]
    assert at.session_state["advert_file"].endswith(".png")
    assert "Maison Nord" in at.session_state["advert_caption"]


# ------------------------------------------------------------------------- collage

def test_gallery_falls_back_to_the_single_image_slot():
    item = offer(image=_sample_photo())
    gallery = item.gallery
    assert [photo.name for photo in gallery] == [item.product_name]
    assert gallery[0].source == "upload" and gallery[0].has_image
    assert offer().gallery[0].source == ""


def test_gallery_is_capped_at_five_photos():
    photos = tuple(Photo(f"Item {index}") for index in range(8))
    assert len(offer(photos=photos).gallery) == MAX_PHOTOS
    assert offer(photos=photos).item_names[:2] == ("Item 0", "Item 1")


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_every_gallery_size_renders(count):
    from PIL import Image

    photos = tuple(Photo(f"Item {index}", _sample_photo()) for index in range(count))
    data = poster_bytes(offer(photos=photos))
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == PORTRAIT


def test_placeholder_tiles_carry_the_item_monogram():

    photos = (Photo("Olive Oil"), Photo("Mug"), Photo("Stand"))
    image = render_poster(offer(photos=photos))
    assert image.size == PORTRAIT
    assert image.getbbox() is not None


def test_multi_item_caption_and_subheadline_list_the_items():
    photos = tuple(Photo(name) for name in ("Olive Oil", "Mug", "Stand", "Lamp", "Bottle"))
    item = offer(photos=photos)
    copy = build_copy(item)
    assert copy.subheadline.startswith("Olive Oil · Mug · Stand")
    assert copy.subheadline.endswith("+2 more")
    caption = copy.caption(item)
    assert "5 clearance items" in caption
    assert "Olive Oil · Mug · Stand · Lamp · Bottle" in caption


def test_generator_pushes_the_poster_to_customers(monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    monkeypatch.setenv("ECOMOPS_GROCERY", "none")
    monkeypatch.setenv("ECOMOPS_WEB_IMAGES", "off")
    os.environ["ECOMOPS_AUTH_AUTOLOGIN"] = "md"

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    _nav(at, "Stock clearance")
    assert not at.exception, at.exception

    _button(at, "Adv generator").click()
    at.run()
    _button(at, "Generate poster").click()
    at.run()
    assert not at.exception, at.exception

    _button(at, "Push poster").click()
    at.run()
    assert not at.exception, at.exception

    report = at.session_state["advert_report"]
    assert report.count >= 1
    assert report.channel == "email"
    assert any("pushed" in str(message.value).lower() for message in at.success)

    events = [event for event in at.session_state["system"].bus.of_type("PromotionPublished")]
    assert len(events) == 1
    assert events[0].recipients == list(report.recipients)
    assert events[0].published_by
