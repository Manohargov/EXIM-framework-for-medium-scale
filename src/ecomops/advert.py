"""Advertisement poster generation for clearance offers.

Turns one clearance decision into a share-ready social poster: company name,
product image, old price, new price and a discount badge, composed on the same
obsidian/champagne palette as the console.

Two halves, both deterministic so the demo, the CLI and the tests need no model
and no network:

* :func:`build_copy` — headline / tagline / badge / caption text per tone;
* :func:`render_poster` — a 1080x1350 (4:5 feed) or 1080x1080 (square) PNG.

The optional LLM path just replaces the copy: the layout is untouched, so a
model headline can never break the poster. Copy is validated against
:class:`AdvertCopy` before it reaches the renderer.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .rules import write_advert_copy, write_transit_copy

# Palette — kept in step with the console theme in app.py.
INK = (11, 11, 13)
INK_WARM = (26, 23, 18)
SURFACE = (20, 20, 23)
IVORY = (242, 239, 232)
MUTED = (154, 150, 140)
GOLD = (200, 169, 106)
GOLD_DIM = (139, 116, 70)

PORTRAIT = (1080, 1350)   # 4:5 — Instagram / Facebook feed
SQUARE = (1080, 1080)     # 1:1 — grid-safe

TONES = ("bold", "minimal", "playful", "premium")

MARGIN_RATIO = 72 / 1080


class AdvertError(ValueError):
    """The offer cannot be turned into a poster (bad image, empty name…)."""


# --------------------------------------------------------------------------- #
# Offer + copy
# --------------------------------------------------------------------------- #

MAX_PHOTOS = 5


@dataclass(frozen=True)
class Photo:
    """One item on the poster: its name, its picture (if any) and where it came from."""

    name: str
    image: bytes | None = None
    source: str = ""          # "upload" | "web:wikimedia" | "" (monogram)

    @property
    def has_image(self) -> bool:
        return bool(self.image)


@dataclass(frozen=True)
class Offer:
    """The items on offer, priced for the poster.

    ``photos`` carries up to :data:`MAX_PHOTOS` items for the collage; when it is
    empty the poster falls back to the single ``product_name``/``image`` pair, so
    older callers keep working unchanged.
    """

    company: str
    product_name: str
    old_price: float
    new_price: float
    sku: str = ""
    currency: str = "$"
    category: str = ""
    image: bytes | None = None
    tone: str = "bold"
    photos: tuple[Photo, ...] = ()

    def __post_init__(self) -> None:
        if not self.company.strip():
            raise AdvertError("company name is required")
        if not self.product_name.strip():
            raise AdvertError("product name is required")
        if self.old_price <= 0 or self.new_price <= 0:
            raise AdvertError("prices must be greater than zero")
        if self.new_price > self.old_price:
            raise AdvertError("the offer price cannot exceed the old price")

    @property
    def discount_pct(self) -> float:
        if self.old_price <= 0:
            return 0.0
        return round((1 - self.new_price / self.old_price) * 100, 1)

    @property
    def saving(self) -> float:
        return round(self.old_price - self.new_price, 2)

    @property
    def has_discount(self) -> bool:
        return self.saving > 0.005

    def price(self, value: float) -> str:
        return f"{self.currency}{value:,.2f}"

    @property
    def gallery(self) -> tuple[Photo, ...]:
        """The photos to draw: the explicit gallery, else the single image slot."""
        if self.photos:
            return self.photos[:MAX_PHOTOS]
        return (Photo(self.product_name, self.image, "upload" if self.image else ""),)

    @property
    def item_names(self) -> tuple[str, ...]:
        return tuple(photo.name for photo in self.gallery)


@dataclass(frozen=True)
class Notice:
    """A customer shipping notice: what is moving, where to, and when it lands.

    The mirror image of :class:`Offer` — same palette and geometry, but the big
    gold number is an ETA rather than a price, and the recipient is one buyer
    instead of a broadcast audience.
    """

    company: str
    shipment_id: str
    order_id: str
    customer_id: str
    destination: str
    carrier: str
    service_level: str = ""
    eta_days: float = 0.0
    items: tuple[Photo, ...] = ()
    quantity: int = 1
    tone: str = "bold"

    def __post_init__(self) -> None:
        for field_name in ("company", "shipment_id", "customer_id", "destination", "carrier"):
            if not str(getattr(self, field_name)).strip():
                raise AdvertError(f"{field_name.replace('_', ' ')} is required")
        if self.eta_days < 0:
            raise AdvertError("eta cannot be negative")
        if self.quantity < 1:
            raise AdvertError("quantity must be at least one")

    @property
    def eta_label(self) -> str:
        """Big-number label for the facts row."""
        if self.eta_days <= 0:
            return "TODAY"
        days = round(self.eta_days)
        return "IN 1 DAY" if days <= 1 else f"IN {days} DAYS"

    @property
    def eta_sentence(self) -> str:
        if self.eta_days <= 0:
            return "today"
        days = round(self.eta_days)
        return "tomorrow" if days <= 1 else f"in {days} days"

    @property
    def gallery(self) -> tuple[Photo, ...]:
        return self.items[:MAX_PHOTOS]

    @property
    def item_names(self) -> tuple[str, ...]:
        return tuple(photo.name for photo in self.gallery)

    def item_line(self) -> str:
        names = self.item_names
        if not names:
            return "your order"
        joined = " · ".join(names)
        return f"{self.quantity} x {joined}"

    def caption(self) -> str:
        """Message to send the customer alongside the image."""
        service = f" {self.service_level}" if self.service_level else ""
        return "\n".join([
            f"Hi {self.customer_id} — order {self.order_id} is on its way.",
            self.item_line(),
            f"{self.carrier}{service} · arriving {self.eta_sentence} to {self.destination}.",
            f"Track it with {self.shipment_id}.",
        ])


@dataclass(frozen=True)
class AdvertCopy:
    """All text the poster draws; produced by rules or by the model."""

    badge: str
    headline: str
    subheadline: str
    tagline: str
    cta: str
    hashtags: tuple[str, ...] = field(default_factory=tuple)

    def caption(self, offer: Offer) -> str:
        """Social caption to paste next to the image."""
        names = offer.item_names
        title = offer.product_name if len(names) <= 1 else f"{len(names)} clearance items"
        lines = [f"{offer.company} — {title}"]
        if len(names) > 1:
            lines.append(" · ".join(names))
        if offer.has_discount:
            lines.append(
                f"Was {offer.price(offer.old_price)}, now {offer.price(offer.new_price)} "
                f"({offer.discount_pct:.0f}% off, save {offer.price(offer.saving)})."
            )
        else:
            lines.append(f"Now {offer.price(offer.new_price)}.")
        if self.tagline:
            lines.append(self.tagline)
        if self.hashtags:
            lines.append(" ".join(f"#{tag}" for tag in self.hashtags))
        return "\n".join(lines)

    @classmethod
    def from_payload(cls, data: dict[str, Any], *, offer: Offer) -> AdvertCopy:
        """Coerce model output into a copy object, falling back on blanks."""
        base = build_copy(offer)
        tags = data.get("hashtags") or base.hashtags
        return cls(
            badge=str(data.get("badge") or base.badge)[:12].upper(),
            headline=str(data.get("headline") or base.headline)[:64],
            subheadline=str(data.get("subheadline") or base.subheadline)[:64],
            tagline=str(data.get("tagline") or base.tagline)[:96],
            cta=str(data.get("cta") or base.cta)[:24].upper(),
            hashtags=tuple(str(tag).lstrip("#") for tag in tags)[:5],
        )


def build_copy(offer: Offer, *, tone: str | None = None) -> AdvertCopy:
    """Deterministic copy for the four tones (the ``write_advert_copy`` rule)."""
    payload = {
        "company": offer.company,
        "product_name": offer.product_name,
        "old_price": offer.old_price,
        "new_price": offer.new_price,
        "currency": offer.currency,
        "category": offer.category,
        "tone": (tone or offer.tone or "bold").lower(),
    }
    data = write_advert_copy(payload)
    names = offer.item_names
    subheadline = str(data["subheadline"])[:64]
    if len(names) > 1:
        shown = " · ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names) - 3} more"
        subheadline = shown[:96]
    return AdvertCopy(
        badge=str(data["badge"]).upper()[:12],
        headline=str(data["headline"])[:64],
        subheadline=subheadline,
        tagline=str(data["tagline"])[:96],
        cta=str(data["cta"]).upper()[:24],
        hashtags=tuple(str(tag).lstrip("#") for tag in data["hashtags"])[:5],
    )


def build_notice_copy(notice: Notice, *, tone: str | None = None) -> AdvertCopy:
    """Deterministic copy for a shipping notice (the ``write_transit_copy`` rule)."""
    payload = {
        "company": notice.company,
        "customer_id": notice.customer_id,
        "shipment_id": notice.shipment_id,
        "order_id": notice.order_id,
        "items": list(notice.item_names),
        "quantity": notice.quantity,
        "destination": notice.destination,
        "carrier": notice.carrier,
        "service_level": notice.service_level,
        "eta_days": notice.eta_days,
        "tone": (tone or notice.tone or "bold").lower(),
    }
    data = write_transit_copy(payload)
    return AdvertCopy(
        badge=str(data["badge"]).upper()[:16],
        headline=str(data["headline"])[:64],
        subheadline=str(data["subheadline"])[:96],
        tagline=str(data["tagline"])[:96],
        cta=str(data["cta"]).upper()[:24],
    )


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

_FONT_CANDIDATES = {
    "serif_bold": ("DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf", "NotoSerif-Bold.ttf",
                   "Times New Roman Bold.ttf", "georgia bold.ttf"),
    "serif": ("DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "NotoSerif-Regular.ttf",
              "Times New Roman.ttf", "georgia.ttf"),
    "sans_bold": ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf",
                  "arialbd.ttf"),
    "sans": ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf",
             "arial.ttf"),
}

_FONT_DIRS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local" / "share" / "fonts",
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path("C:/Windows/Fonts"),
)


@lru_cache(maxsize=32)
def _font_path(role: str) -> str | None:
    for filename in _FONT_CANDIDATES.get(role, ()):
        for root in _FONT_DIRS:
            if not root.is_dir():
                continue
            hit = next(root.rglob(filename), None)
            if hit is not None:
                return str(hit)
    return None


@lru_cache(maxsize=128)
def _font(role: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path(role)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:  # pragma: no cover - unreadable font file
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

def _width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float) -> float:
    """Draw letter-spaced text; returns the advance width."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += _width(draw, char, font) + tracking
    return x - xy[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: float,
          max_lines: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _width(draw, candidate, font) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines:
        lines.append(current)
    # Ellipsise the final line when the text did not fit.
    if lines and _width(draw, lines[-1], font) > max_width:
        line = lines[-1]
        while line and _width(draw, line + "…", font) > max_width:
            line = line[:-1]
        lines[-1] = line.rstrip() + "…"
    return lines[:max_lines]


def _elide(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if _width(draw, text, font) <= max_width:
        return text
    trimmed = text
    while trimmed and _width(draw, trimmed + "…", font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed.rstrip() + "…"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _background(size: tuple[int, int]) -> Image.Image:
    """Warm vertical gradient plus a soft champagne glow in the top corner."""
    w, h = size
    column = Image.new("RGB", (1, h))
    pixels = column.load()
    for y in range(h):
        blend = y / max(h - 1, 1)
        pixels[0, y] = tuple(
            round(start + (end - start) * blend)
            for start, end in zip(INK_WARM, INK, strict=True)
        )
    canvas = column.resize(size, Image.Resampling.BILINEAR)

    # Cheap radial glow: build small, blur, scale up.
    small = (w // 6, h // 6)
    glow = Image.new("L", small, 0)
    ImageDraw.Draw(glow).ellipse(
        (-small[0] // 4, -small[1] // 2, int(small[0] * 0.95), int(small[1] * 0.7)),
        fill=52,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(12))
    glow = glow.resize(size, Image.Resampling.BILINEAR)
    canvas.paste(Image.new("RGB", size, GOLD), (0, 0), glow)
    return canvas


def _frame(draw: ImageDraw.ImageDraw, size: tuple[int, int], margin: int) -> None:
    inset = max(margin // 3, 12)
    draw.rectangle(
        (inset, inset, size[0] - inset - 1, size[1] - inset - 1),
        outline=(*GOLD_DIM, ),
        width=1,
    )


def _placeholder_card(size: tuple[int, int], photo: Photo) -> Image.Image:
    """Stand-in artwork when no product photo was supplied."""
    card = Image.new("RGB", size, SURFACE)
    draw = ImageDraw.Draw(card)
    # Faint diagonal pinstripes give the empty card some texture.
    step = 26
    for offset in range(-size[1], size[0], step):
        draw.line([(offset, 0), (offset + size[1], size[1])], fill=(27, 27, 31), width=1)
    letter = (photo.name.strip()[:1] or "◈").upper()
    font = _font("serif_bold", max(18, int(min(size) * 0.40)))
    box = draw.textbbox((0, 0), letter, font=font)
    draw.text(
        ((size[0] - (box[2] - box[0])) / 2 - box[0], (size[1] - (box[3] - box[1])) / 2 - box[1]),
        letter, font=font, fill=GOLD_DIM,
    )
    return card


def _photo_card(size: tuple[int, int], photo: Photo) -> Image.Image:
    if not photo.image:
        return _placeholder_card(size, photo)
    try:
        source = Image.open(io.BytesIO(photo.image))
        source.load()
    except Exception as exc:
        raise AdvertError(f"unreadable item image: {exc}") from exc
    source = ImageOps.exif_transpose(source).convert("RGB")
    return ImageOps.fit(source, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


# Normalised tile rectangles for 1-5 items, laid out to fill the photo card.
_LAYOUTS: dict[int, tuple[tuple[float, float, float, float], ...]] = {
    1: ((0, 0, 1, 1),),
    2: ((0, 0, 0.5, 1), (0.5, 0, 1, 1)),
    3: ((0, 0, 0.5, 1), (0.5, 0, 1, 0.5), (0.5, 0.5, 1, 1)),
    4: ((0, 0, 0.5, 0.5), (0.5, 0, 1, 0.5), (0, 0.5, 0.5, 1), (0.5, 0.5, 1, 1)),
    5: (
        (0, 0, 1 / 3, 0.5), (1 / 3, 0, 2 / 3, 0.5), (2 / 3, 0, 1, 0.5),
        (0, 0.5, 0.5, 1), (0.5, 0.5, 1, 1),
    ),
}


def _collage(size: tuple[int, int], photos: tuple[Photo, ...]) -> Image.Image:
    """Arrange 1-5 item photos (or monogram cards) into the photo area."""
    items = photos[:MAX_PHOTOS]
    if not items:
        return _placeholder_card(size, Photo(""))
    if len(items) == 1:
        return _photo_card(size, items[0])

    width, height = size
    gutter = max(6, round(min(size) * 0.018))
    radius = max(6, round(width * 0.012))
    board = Image.new("RGB", size, SURFACE)

    for photo, (x0, y0, x1, y1) in zip(items, _LAYOUTS[len(items)], strict=False):
        left = round(x0 * width) + (gutter if x0 > 0 else 0)
        top = round(y0 * height) + (gutter if y0 > 0 else 0)
        right = round(x1 * width) - (gutter if x1 < 1 else 0)
        bottom = round(y1 * height) - (gutter if y1 < 1 else 0)
        tile_size = (max(1, right - left), max(1, bottom - top))
        tile = _photo_card(tile_size, photo)
        mask = Image.new("L", tile_size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, tile_size[0] - 1, tile_size[1] - 1), radius=radius, fill=255
        )
        board.paste(tile, (left, top), mask)
    return board


def _draw_header(
    draw: ImageDraw.ImageDraw,
    w: int,
    h: int,
    margin: int,
    company: str,
    chip_text: str,
) -> int:
    """Company wordmark, pill chip and hairline. Returns the rule's y."""
    header_y = round(h * 0.052)
    company_font = _font("sans_bold", round(w * 0.028))
    wordmark = _elide(draw, company.upper(), company_font, w - 2 * margin - round(w * 0.20))
    _tracked(draw, (margin, header_y), wordmark, company_font, GOLD, round(w * 0.0055))

    chip_font = _font("sans_bold", round(w * 0.0155))
    chip_w = _width(draw, chip_text, chip_font) + round(w * 0.020)
    chip_h = round(w * 0.030)
    chip_x = w - margin - chip_w
    draw.rounded_rectangle(
        (chip_x, header_y + round(w * 0.004), chip_x + chip_w, header_y + round(w * 0.004) + chip_h),
        radius=chip_h // 2, outline=GOLD_DIM, width=1,
    )
    draw.text(
        (chip_x + round(w * 0.010), header_y + round(w * 0.004) + round(chip_h * 0.26)),
        chip_text, font=chip_font, fill=MUTED,
    )

    rule_y = header_y + round(w * 0.046)
    draw.line(
        [(margin, rule_y), (margin + round(w * 0.10), rule_y)], fill=GOLD, width=max(2, round(w * 0.002))
    )
    draw.line(
        [(margin + round(w * 0.10), rule_y), (w - margin, rule_y)], fill=(44, 42, 38), width=1
    )
    return rule_y


def _draw_photo_card(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    w: int,
    margin: int,
    rule_y: int,
    gallery: tuple[Photo, ...],
    *,
    square: bool,
) -> tuple[int, int, int]:
    """Photo area below the header. Returns (top, height, width)."""
    card_top = rule_y + round(w * 0.030)
    card_h = round(w * 0.40) if square else round(w * 0.58)
    card_w = w - 2 * margin
    card_box = (margin, card_top, margin + card_w, card_top + card_h)
    draw.rounded_rectangle(
        card_box, radius=round(w * 0.006), fill=SURFACE, outline=(38, 37, 34), width=1
    )

    inset = round(w * 0.011)
    inner = (card_w - 2 * inset, card_h - 2 * inset)
    photo = _collage(inner, gallery)
    mask = Image.new("L", inner, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, inner[0] - 1, inner[1] - 1), radius=round(w * 0.004), fill=255
    )
    canvas.paste(photo, (margin + inset, card_top + inset), mask)
    return card_top, card_h, card_w


def _draw_badge(
    draw: ImageDraw.ImageDraw, w: int, margin: int, card_top: int, badge_text: str
) -> None:
    """Gold pill straddling the photo card's top-left corner."""
    badge_font = _font("sans_bold", round(w * 0.030))
    text = badge_text.upper()
    badge_w = _width(draw, text, badge_font) + round(w * 0.042)
    badge_h = round(w * 0.058)
    badge_x = margin + round(w * 0.026)
    badge_y = card_top + round(w * 0.026)
    draw.rounded_rectangle(
        (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
        radius=badge_h // 2, fill=GOLD,
    )
    badge_box = draw.textbbox((0, 0), text, font=badge_font)
    draw.text(
        (
            badge_x + (badge_w - (badge_box[2] - badge_box[0])) / 2 - badge_box[0],
            badge_y + (badge_h - (badge_box[3] - badge_box[1])) / 2 - badge_box[1],
        ),
        text, font=badge_font, fill=INK,
    )


def _draw_cta(
    draw: ImageDraw.ImageDraw, w: int, h: int, margin: int, cta: str
) -> None:
    cta_font = _font("sans_bold", round(w * 0.0175))
    cta_text = cta.upper()
    tracking = round(w * 0.006)
    text_w = _width(draw, cta_text, cta_font) + tracking * max(len(cta_text) - 1, 0)
    cta_w = text_w + round(w * 0.055)
    cta_h = round(w * 0.050)
    cta_x = w - margin - cta_w
    cta_y = h - margin - round(w * 0.045) - cta_h
    draw.rounded_rectangle(
        (cta_x, cta_y, cta_x + cta_w, cta_y + cta_h), radius=cta_h // 2, outline=GOLD, width=2
    )
    _tracked(
        draw,
        (
            cta_x + (cta_w - _width(draw, cta_text, cta_font) - round(w * 0.006) * (len(cta_text) - 1)) / 2,
            cta_y + (cta_h - cta_font.size) / 2 - round(cta_font.size * 0.10),
        ),
        cta_text, cta_font, GOLD, round(w * 0.006),
    )


def _draw_footer(
    draw: ImageDraw.ImageDraw, w: int, h: int, margin: int, left: str, right: str
) -> None:
    footer_font = _font("sans", round(w * 0.0145))
    footer_y = h - margin - round(w * 0.012)
    draw.text((margin, footer_y), _elide(draw, left.upper(), footer_font, (w - 2 * margin) * 0.5),
              font=footer_font, fill=(120, 117, 109))
    draw.text((w - margin - _width(draw, right, footer_font), footer_y),
              _elide(draw, right.upper(), footer_font, (w - 2 * margin) * 0.5),
              font=footer_font, fill=(120, 117, 109))


def render_poster(
    offer: Offer,
    copy: AdvertCopy | None = None,
    *,
    size: tuple[int, int] = PORTRAIT,
) -> Image.Image:
    """Compose the poster. ``size`` is (width, height); see PORTRAIT / SQUARE."""
    copy = copy or build_copy(offer)
    w, h = size
    margin = round(w * MARGIN_RATIO)
    square = h <= w * 1.02

    canvas = _background(size)
    draw = ImageDraw.Draw(canvas)
    _frame(draw, size, margin)

    rule_y = _draw_header(draw, w, h, margin, offer.company, "STOCK CLEARANCE")
    card_top, card_h, card_w = _draw_photo_card(
        canvas, draw, w, margin, rule_y, offer.gallery, square=square,
    )
    _draw_badge(draw, w, margin, card_top, copy.badge)

    # --- copy block ------------------------------------------------------- #
    text_top = card_top + card_h + round(w * 0.028)
    divider_y = h - round(w * 0.255)

    product_font = _font("sans_bold", round(w * 0.0225))
    tagline_font = _font("serif", round(w * 0.0185))
    product_h = round(product_font.size * 1.30)
    tagline_h = round(tagline_font.size * 1.55)

    headline_size = round(w * (0.044 if square else 0.052))
    headline_font = _font("serif_bold", headline_size)
    line_h = round(headline_size * 1.12)
    available = divider_y - text_top - round(w * 0.014)

    headline = copy.headline.upper()
    if available >= line_h * 2 + product_h + tagline_h:
        headline_lines = _wrap(draw, headline, headline_font, card_w, 2)
    else:
        # One line only: shrink the headline until it fits rather than truncating.
        while headline_size > round(w * 0.030) and _width(draw, headline, headline_font) > card_w:
            headline_size -= 2
            headline_font = _font("serif_bold", headline_size)
        line_h = round(headline_font.size * 1.12)
        headline_lines = [_elide(draw, headline, headline_font, card_w)]

    y = text_top
    # The item list wraps to two lines for multi-item posters, shrinking once
    # before it is allowed to ellipsise.
    sub_font = product_font
    sub_lines = _wrap(draw, copy.subheadline, sub_font, card_w, 2)
    if len(sub_lines) > 1:
        sub_font = _font("sans_bold", round(w * 0.019))
        sub_lines = _wrap(draw, copy.subheadline, sub_font, card_w, 2)
    sub_h = round(sub_font.size * 1.34) * len(sub_lines)
    block_h = (
        line_h * len(headline_lines) + round(w * 0.006) + sub_h
        + round(w * 0.004) + tagline_h
    )
    if available > block_h:  # centre the block in the space under the photo
        y += min(round((available - block_h) * 0.5), round(w * 0.045))
    else:  # no room for a second line: keep the poster from spilling into the price
        sub_font = product_font
        sub_lines = [_elide(draw, copy.subheadline, sub_font, card_w)]
        sub_h = round(sub_font.size * 1.34)
    for line in headline_lines:
        draw.text((margin, y), line, font=headline_font, fill=IVORY)
        y += line_h

    y += round(w * 0.006)
    for line in sub_lines:
        draw.text((margin, y), line, font=sub_font, fill=(215, 210, 198))
        y += round(sub_font.size * 1.34)

    y += round(w * 0.004)
    if y + tagline_font.size <= divider_y - round(w * 0.010):
        draw.text(
            (margin, y), _elide(draw, copy.tagline, tagline_font, card_w),
            font=tagline_font, fill=MUTED,
        )

    # --- price row -------------------------------------------------------- #
    draw.line([(margin, divider_y), (w - margin, divider_y)], fill=(44, 42, 38), width=1)

    price_font = _font("serif_bold", round(w * 0.088))
    old_font = _font("sans", round(w * 0.0225))
    save_font = _font("sans_bold", round(w * 0.0165))

    price_y = divider_y + round(w * 0.020)
    new_text = offer.price(offer.new_price)
    draw.text((margin, price_y), new_text, font=price_font, fill=GOLD)
    new_box = draw.textbbox((margin, price_y), new_text, font=price_font)

    old_text = offer.price(offer.old_price)
    old_x = new_box[2] + round(w * 0.030)
    old_y = price_y + round(w * 0.016)
    if offer.has_discount:
        draw.text((old_x, old_y), old_text, font=old_font, fill=MUTED)
        old_w = _width(draw, old_text, old_font)
        strike_y = old_y + round(old_font.size * 0.58)
        draw.line(
            [(old_x - 2, strike_y), (old_x + old_w + 2, strike_y)],
            fill=(196, 122, 106), width=max(2, round(w * 0.002)),
        )
        note, note_y = f"SAVE {offer.price(offer.saving)}", old_y + round(w * 0.042)
    else:
        note, note_y = "TODAY ONLY", price_y + round(w * 0.030)
    draw.text((old_x, note_y), note, font=save_font, fill=GOLD)

    # --- call to action --------------------------------------------------- #
    _draw_cta(draw, w, h, margin, copy.cta)

    # --- footer ----------------------------------------------------------- #
    left = offer.sku if offer.sku else offer.company
    _draw_footer(draw, w, h, margin, left, "WHILE STOCKS LAST")

    return canvas


def render_notice(
    notice: Notice,
    copy: AdvertCopy | None = None,
    *,
    size: tuple[int, int] = PORTRAIT,
) -> Image.Image:
    """Compose the shipping notice: same geometry as the poster, ETA in gold."""
    copy = copy or build_notice_copy(notice)
    w, h = size
    margin = round(w * MARGIN_RATIO)
    square = h <= w * 1.02

    canvas = _background(size)
    draw = ImageDraw.Draw(canvas)
    _frame(draw, size, margin)

    rule_y = _draw_header(draw, w, h, margin, notice.company, "SHIPPING NOTICE")
    card_top, card_h, card_w = _draw_photo_card(
        canvas, draw, w, margin, rule_y, notice.gallery, square=square,
    )
    _draw_badge(draw, w, margin, card_top, copy.badge)

    # --- copy block ------------------------------------------------------- #
    text_top = card_top + card_h + round(w * 0.028)
    divider_y = h - round(w * 0.255)

    sub_font = _font("sans_bold", round(w * 0.0225))
    tagline_font = _font("serif", round(w * 0.0185))
    headline_size = round(w * (0.044 if square else 0.052))
    headline_font = _font("serif_bold", headline_size)
    line_h = round(headline_size * 1.12)
    available = divider_y - text_top - round(w * 0.014)

    headline = copy.headline.upper()
    headline_lines = _wrap(draw, headline, headline_font, card_w, 2)
    if len(headline_lines) > 1 and available < line_h * 2 + round(sub_font.size * 2.8):
        while headline_size > round(w * 0.030) and _width(draw, headline, headline_font) > card_w:
            headline_size -= 2
            headline_font = _font("serif_bold", headline_size)
        line_h = round(headline_font.size * 1.12)
        headline_lines = [_elide(draw, headline, headline_font, card_w)]

    sub_lines = _wrap(draw, copy.subheadline, sub_font, card_w, 2)
    sub_h = round(sub_font.size * 1.34) * len(sub_lines)
    block_h = line_h * len(headline_lines) + round(w * 0.006) + sub_h + round(w * 0.004) + round(
        tagline_font.size * 1.55
    )

    y = text_top
    if available > block_h:
        y += min(round((available - block_h) * 0.5), round(w * 0.045))
    for line in headline_lines:
        draw.text((margin, y), line, font=headline_font, fill=IVORY)
        y += line_h

    y += round(w * 0.006)
    for line in sub_lines:
        draw.text((margin, y), line, font=sub_font, fill=(215, 210, 198))
        y += round(sub_font.size * 1.34)

    y += round(w * 0.004)
    draw.text(
        (margin, y), _elide(draw, copy.tagline, tagline_font, card_w),
        font=tagline_font, fill=MUTED,
    )

    # --- facts row: ETA left, tracking right ------------------------------ #
    draw.line([(margin, divider_y), (w - margin, divider_y)], fill=(44, 42, 38), width=1)

    eta_font = _font("serif_bold", round(w * 0.058))
    label_font = _font("sans_bold", round(w * 0.0145))
    eta_text = notice.eta_label
    eta_y = divider_y + round(w * 0.026)
    draw.text((margin, eta_y), eta_text, font=eta_font, fill=GOLD)
    eta_w = _width(draw, eta_text, eta_font)

    right_x = margin + max(round(w * 0.42), eta_w + round(w * 0.05))
    right_w = w - margin - right_x
    draw.text((right_x, eta_y + round(w * 0.006)), "TRACKING", font=label_font, fill=MUTED)
    track_font = _font("sans_bold", round(w * 0.024))
    draw.text(
        (right_x, eta_y + round(w * 0.030)),
        _elide(draw, notice.shipment_id.upper(), track_font, right_w),
        font=track_font, fill=IVORY,
    )

    # --- call to action --------------------------------------------------- #
    _draw_cta(draw, w, h, margin, copy.cta)

    # --- footer ----------------------------------------------------------- #
    _draw_footer(draw, w, h, margin, notice.order_id, notice.destination)

    return canvas


def notice_bytes(
    notice: Notice,
    copy: AdvertCopy | None = None,
    *,
    size: tuple[int, int] = PORTRAIT,
    fmt: str = "PNG",
) -> bytes:
    """Render the notice and return encoded bytes for ``st.download_button``."""
    image = render_notice(notice, copy, size=size)
    buffer = io.BytesIO()
    if fmt.upper() == "JPEG":
        image.save(buffer, "JPEG", quality=92, optimize=True)
    else:
        image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def poster_bytes(
    offer: Offer,
    copy: AdvertCopy | None = None,
    *,
    size: tuple[int, int] = PORTRAIT,
    fmt: str = "PNG",
) -> bytes:
    """Render the poster and return encoded bytes ready for ``st.download_button``."""
    image = render_poster(offer, copy, size=size)
    buffer = io.BytesIO()
    if fmt.upper() == "JPEG":
        image.save(buffer, "JPEG", quality=92, optimize=True)
    else:
        image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def save_poster(
    offer: Offer,
    copy: AdvertCopy | None,
    path: str | Path,
    *,
    size: tuple[int, int] = PORTRAIT,
) -> Path:
    """Write the poster to ``path`` (``.jpg``/``.jpeg`` → JPEG, else PNG)."""
    target = Path(path)
    fmt = "JPEG" if target.suffix.lower() in {".jpg", ".jpeg"} else "PNG"
    target.write_bytes(poster_bytes(offer, copy, size=size, fmt=fmt))
    return target


__all__ = [
    "MAX_PHOTOS",
    "PORTRAIT",
    "SQUARE",
    "TONES",
    "AdvertCopy",
    "AdvertError",
    "Notice",
    "Offer",
    "Photo",
    "build_copy",
    "build_notice_copy",
    "notice_bytes",
    "poster_bytes",
    "render_notice",
    "render_poster",
    "save_poster",
]
