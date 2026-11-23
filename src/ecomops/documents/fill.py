"""Fill a document while keeping its format.

Two renderers share one interface:

``fill_overlay``  draw answers on the rendered page, at the coordinates the
                  extractor found (default; pixel-true layout).
``fill_reflow``   rebuild a clean, clearly-labelled document from the fields
                  when the original geometry is unreliable.

Both return a PDF written with Pillow, so no PDF-writing dependency is needed.
An "Answers & sources" annex page is always appended, which makes the output
auditable: every drawn value can be traced to a catalogue row or an event.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .extract import PREVIEW_DPI, render_pages
from .schema import Box, DocField, DocJob, ExtractedDoc, Page

INK = (17, 17, 19)
MUTED = (108, 108, 112)
ACCENT = (150, 118, 62)
PAPER = (255, 255, 255)
RULE = (214, 210, 202)

A4 = (1240, 1754)  # 150 dpi
MARGIN = 96

_FONT_CANDIDATES = {
    "sans": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ),
    "sans-bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ),
    "serif-bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ),
}


@lru_cache(maxsize=64)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES.get(kind, _FONT_CANDIDATES["sans"]):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:  # pragma: no cover - broken font file
                continue
    return ImageFont.load_default(size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
    return right - left


def _fit(draw: ImageDraw.ImageDraw, text: str, font_kind: str, size: int, max_width: float):
    size = max(6, int(size))
    font = _font(font_kind, size)
    while size > 6 and _text_width(draw, text, font) > max_width:
        size -= 1
        font = _font(font_kind, size)
    return font


# --------------------------------------------------------------------------- #
# Overlay renderer
# --------------------------------------------------------------------------- #

def _answer(field: DocField) -> str | None:
    answer = field.answer
    return answer.strip() if isinstance(answer, str) and answer.strip() else None


def _page_images(doc: ExtractedDoc, workdir: Path | None) -> list[Image.Image]:
    images: list[Image.Image] = []
    rendered: list[Path] = []
    needs_render = any(page.image is None for page in doc.pages)
    if needs_render and doc.path.suffix.lower() == ".pdf":
        target = (workdir or doc.path.parent) / "_render"
        rendered = render_pages(doc.path, target, dpi=PREVIEW_DPI)
    for index, page in enumerate(doc.pages):
        source = page.image
        if source is None:
            source = rendered[index] if index < len(rendered) else None
        if source is None:
            # No raster available: synthesise a blank page at the page geometry.
            size = (
                max(1, int(page.width * PREVIEW_DPI / 72)),
                max(1, int(page.height * PREVIEW_DPI / 72)),
            )
            images.append(Image.new("RGB", size, PAPER))
            continue
        with Image.open(source) as picture:
            images.append(picture.convert("RGB"))
    return images


def _box_on_page(box: Box, page: Page, image: Image.Image) -> tuple[float, float, float, float]:
    scale_x = image.width / page.width if page.width else 1.0
    scale_y = image.height / page.height if page.height else 1.0
    return box.x * scale_x, box.y * scale_y, box.w * scale_x, box.h * scale_y


def _overlay_page(image: Image.Image, page: Page, fields: list[DocField]) -> Image.Image:
    draw = ImageDraw.Draw(image)
    for field in fields:
        if field.box is None:
            continue
        answer = _answer(field)
        if answer is None:
            continue
        if field.value and answer == field.value.strip():
            continue  # already printed on the page; leave the original pixels alone
        x, y, w, h = _box_on_page(field.box, page, image)
        if w <= 0 or h <= 0:
            continue
        draw.rectangle([x - 1, y - 1, x + w + 1, y + h + 1], fill=PAPER)
        size = int(max(7, h * 1.05))
        font = _fit(draw, answer, "sans", size, image.width - x - 8)
        draw.text((x, y - h * 0.06), answer, font=font, fill=INK)
        underline = y + h + 1
        draw.line([x, underline, min(x + _text_width(draw, answer, font), image.width - 8), underline], fill=ACCENT, width=1)
    return image


# --------------------------------------------------------------------------- #
# Annex
# --------------------------------------------------------------------------- #

def _annex_page(job: DocJob) -> Image.Image:
    page = Image.new("RGB", A4, PAPER)
    draw = ImageDraw.Draw(page)
    y = MARGIN

    draw.text((MARGIN, y), "Answers & sources", font=_font("serif-bold", 34), fill=INK)
    y += 50
    draw.text(
        (MARGIN, y),
        f"{job.doc_type.label} · {job.filename} · {job.id}",
        font=_font("sans", 17),
        fill=MUTED,
    )
    y += 40
    draw.line([MARGIN, y, A4[0] - MARGIN, y], fill=RULE, width=1)
    y += 26

    for field in job.fields:
        answer = _answer(field) or "— not provided"
        label = field.label
        font = _font("sans-bold", 18)
        draw.text((MARGIN, y), label, font=font, fill=INK)
        source = field.source or "—"
        draw.text((MARGIN + 380, y), answer[:90], font=_font("sans", 18), fill=INK)
        y += 26
        draw.text(
            (MARGIN + 380, y),
            f"source: {source}",
            font=_font("sans", 13),
            fill=MUTED,
        )
        y += 32
        if y > A4[1] - MARGIN - 60:
            break

    draw.line([MARGIN, A4[1] - MARGIN - 34, A4[0] - MARGIN, A4[1] - MARGIN - 34], fill=RULE, width=1)
    draw.text(
        (MARGIN, A4[1] - MARGIN - 24),
        "Prepared by MSM · deterministic rules · every value traceable to a source",
        font=_font("sans", 13),
        fill=MUTED,
    )
    return page


# --------------------------------------------------------------------------- #
# Re-flow renderer
# --------------------------------------------------------------------------- #

def _wrapped(text: str, font, draw: ImageDraw.ImageDraw, width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(draw, candidate, font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _reflow_pages(doc: ExtractedDoc, job: DocJob) -> list[Image.Image]:
    pages: list[Image.Image] = []
    page = Image.new("RGB", A4, PAPER)
    draw = ImageDraw.Draw(page)
    y = MARGIN

    def new_page() -> None:
        nonlocal page, draw, y
        pages.append(page)
        page = Image.new("RGB", A4, PAPER)
        draw = ImageDraw.Draw(page)
        y = MARGIN

    def ensure(space: int) -> None:
        if y + space > A4[1] - MARGIN:
            new_page()

    draw.text((MARGIN, y), "MSM", font=_font("serif-bold", 30), fill=ACCENT)
    y += 44
    draw.text((MARGIN, y), job.doc_type.label, font=_font("serif-bold", 40), fill=INK)
    y += 56
    draw.text(
        (MARGIN, y),
        f"{job.filename} · regenerated {job.created_at:%Y-%m-%d %H:%M} UTC",
        font=_font("sans", 16),
        fill=MUTED,
    )
    y += 36
    draw.line([MARGIN, y, A4[0] - MARGIN, y], fill=RULE, width=1)
    y += 30

    for field in job.fields:
        answer = _answer(field)
        if answer is None:
            continue
        ensure(64)
        draw.text((MARGIN, y), field.label.upper(), font=_font("sans-bold", 13), fill=MUTED)
        y += 20
        font = _font("sans", 20)
        for line in _wrapped(answer, font, draw, A4[0] - 2 * MARGIN):
            ensure(30)
            draw.text((MARGIN, y), line, font=font, fill=INK)
            y += 28
        if field.source:
            ensure(22)
            draw.text((MARGIN, y), f"source: {field.source}", font=_font("sans", 12), fill=MUTED)
            y += 22
        y += 14

    y += 10
    ensure(40)
    draw.line([MARGIN, y, A4[0] - MARGIN, y], fill=RULE, width=1)
    y += 25
    draw.text((MARGIN, y), "ORIGINAL TEXT", font=_font("sans-bold", 13), fill=MUTED)
    y += 26
    body = _font("serif", 15)
    for raw_line in doc.text.splitlines():
        if not raw_line.strip():
            continue
        for line in _wrapped(raw_line.strip(), body, draw, A4[0] - 2 * MARGIN):
            ensure(24)
            draw.text((MARGIN, y), line, font=body, fill=INK)
            y += 22
    pages.append(page)
    return pages


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #

def _save(pages: list[Image.Image], output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    first, rest = pages[0], pages[1:]
    first.save(output, "PDF", save_all=True, append_images=rest, resolution=float(PREVIEW_DPI))
    return output


def fill_overlay(doc: ExtractedDoc, job: DocJob, output: Path, workdir: Path | None = None) -> Path:
    images = _page_images(doc, workdir)
    for index, image in enumerate(images):
        page = doc.pages[index] if index < len(doc.pages) else None
        fields = [f for f in job.fields if page is not None and f.box is not None]
        images[index] = _overlay_page(image, page, fields) if page is not None else image
    images.append(_annex_page(job))
    return _save(images, output)


def fill_reflow(doc: ExtractedDoc, job: DocJob, output: Path) -> Path:
    return _save(_reflow_pages(doc, job), output)


def fill(
    doc: ExtractedDoc,
    job: DocJob,
    output: Path,
    *,
    mode: str = "original",
    workdir: Path | None = None,
) -> Path:
    """Render ``job`` into ``output``; ``mode`` is ``original`` or ``reflow``."""
    if mode not in {"original", "reflow"}:
        raise ValueError(f"unknown fill mode: {mode!r}")
    if mode == "reflow":
        return fill_reflow(doc, job, output)
    return fill_overlay(doc, job, output, workdir=workdir)


def preview_bytes(path: Path, *, page: int = 1, width: int = 900) -> bytes:
    """PNG bytes of one page of the filled PDF, for the chat preview."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ecomops-preview-") as tmp:
        base = Path(tmp) / "preview"
        subprocess.run(
            ["pdftoppm", "-png", "-f", str(page), "-l", str(page), "-scale-to", str(width), str(path), str(base)],
            check=True,
            capture_output=True,
        )
        produced = sorted(Path(tmp).glob("preview-*.png"))
        if not produced:
            raise FileNotFoundError(f"no preview rendered for {path}")
        return produced[0].read_bytes()
