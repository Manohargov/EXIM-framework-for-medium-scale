"""Text and geometry extraction for PDFs and images.

Reading is poppler-first so no Python PDF library is required:

* digital PDF  -> ``pdftohtml -xml`` gives every text node with a bounding box
* scanned PDF  -> ``pdftoppm`` renders pages, ``tesseract`` returns word boxes
* image        -> ``tesseract`` directly on the picture

All coordinates in :class:`~ecomops.documents.schema.Box` are PDF points with a
top-left origin. ``pdftohtml`` reports at 1.5 px/pt (108 dpi); the conversion
lives in one constant so the filler and the extractor cannot disagree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .schema import Box, DocKind, ExtractedDoc, Page, Word, file_sha256

# pdftohtml works at a fixed 1.5 px per point (108 dpi).
XML_SCALE = 1.5

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_SUFFIXES = {".pdf"}

OCR_DPI = 300
PREVIEW_DPI = 150
DEFAULT_LANGS = "eng"


class ExtractionError(RuntimeError):
    """The document could not be read."""


class OcrUnavailable(ExtractionError):
    """A scan or photo was supplied but no OCR engine is installed."""


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #

def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the host
        raise ExtractionError(f"{args[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"{args[0]} timed out after {timeout}s") from exc


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise ExtractionError(
            f"{binary} is not installed. On Ubuntu: sudo apt-get install -y poppler-utils"
        )
    return path


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def available_languages() -> list[str]:
    if not ocr_available():
        return []
    result = _run(["tesseract", "--list-langs"], timeout=20)
    lines = [line.strip() for line in result.stdout.splitlines()]
    return [line for line in lines[1:] if line and " " not in line]


def configured_languages() -> str:
    """Language spec for tesseract, e.g. ``eng`` or ``eng+deu``."""
    raw = os.environ.get("ECOMOPS_OCR_LANGS", DEFAULT_LANGS).strip() or DEFAULT_LANGS
    return "+".join(part.strip() for part in raw.split("+") if part.strip())


def _check_languages(spec: str) -> None:
    installed = set(available_languages())
    if not installed:
        return
    wanted = [part for part in spec.split("+") if part]
    missing = [part for part in wanted if part not in installed]
    if missing:
        raise OcrUnavailable(
            "Tesseract language pack(s) missing: "
            + ", ".join(missing)
            + ". Install them with: sudo apt-get install -y "
            + " ".join(f"tesseract-ocr-{part}" for part in missing)
        )


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def pdf_page_count(path: Path) -> int:
    _require("pdfinfo")
    result = _run(["pdfinfo", str(path)], timeout=60)
    if result.returncode != 0:
        raise ExtractionError(f"pdfinfo failed: {result.stderr.strip()[:300]}")
    match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else 0


def has_text_layer(path: Path) -> bool:
    """A PDF has a usable text layer when pdftotext returns real content."""
    _require("pdftotext")
    result = _run(["pdftotext", "-q", "-l", "3", str(path), "-"], timeout=60)
    if result.returncode != 0:
        raise ExtractionError(f"pdftotext failed: {result.stderr.strip()[:300]}")
    return len(result.stdout.strip()) >= 20


def detect_kind(path: Path) -> DocKind:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return DocKind.IMAGE
    if suffix in PDF_SUFFIXES:
        return DocKind.TEXT_PDF if has_text_layer(path) else DocKind.SCAN
    raise ExtractionError(f"Unsupported file type: {suffix or path.name}")


# --------------------------------------------------------------------------- #
# Digital PDFs
# --------------------------------------------------------------------------- #

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_bbox(xml_text: str) -> list[Page]:
    """Parse ``pdftotext -bbox`` XHTML into word-positioned pages.

    Word-level boxes matter for filling: the overlay must be able to replace
    ``SKU-104`` without painting over the ``SKU:`` label beside it.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ExtractionError(f"could not parse pdftotext output: {exc}") from exc

    pages: list[Page] = []
    for node in root.iter():
        if _local(node.tag) != "page":
            continue
        width = float(node.get("width", "0") or 0)
        height = float(node.get("height", "0") or 0)
        words: list[Word] = []
        for element in node.iter():
            if _local(element.tag) != "word":
                continue
            content = (element.text or "").strip()
            if not content:
                continue
            try:
                x1 = float(element.get("xMin", "0") or 0)
                y1 = float(element.get("yMin", "0") or 0)
                x2 = float(element.get("xMax", "0") or 0)
                y2 = float(element.get("yMax", "0") or 0)
            except ValueError:
                continue
            box = Box(x=x1, y=y1, w=max(0.1, x2 - x1), h=max(0.1, y2 - y1))
            words.append(Word(text=content, box=box, size=max(6.0, box.h * 1.05)))
        pages.append(Page(number=len(pages) + 1, width=width, height=height, words=words))
    return pages


def _parse_xml(xml_path: Path) -> list[Page]:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ExtractionError(f"could not parse pdftohtml output: {exc}") from exc

    fonts: dict[str, tuple[float, str]] = {}
    for spec in root.findall("fontspec"):
        fonts[spec.get("id", "")] = (
            float(spec.get("size", "10") or 10),
            spec.get("family", "") or "",
        )

    pages: list[Page] = []
    for index, node in enumerate(root.findall("page"), start=1):
        width = float(node.get("width", "0") or 0) / XML_SCALE
        height = float(node.get("height", "0") or 0) / XML_SCALE
        words: list[Word] = []
        for text in node.findall("text"):
            content = "".join(text.itertext()).strip()
            if not content:
                continue
            font_size, _family = fonts.get(text.get("font", ""), (10.0, ""))
            words.append(
                Word(
                    text=content,
                    box=Box(
                        x=float(text.get("left", "0") or 0) / XML_SCALE,
                        y=float(text.get("top", "0") or 0) / XML_SCALE,
                        w=float(text.get("width", "0") or 0) / XML_SCALE,
                        h=float(text.get("height", "0") or 0) / XML_SCALE,
                    ),
                    size=font_size / XML_SCALE,
                    bold=text.find("b") is not None,
                )
            )
        pages.append(Page(number=index, width=width, height=height, words=words))
    return pages


def extract_text_pdf(path: Path, workdir: Path | None = None) -> list[Page]:
    """Word-level boxes from ``pdftotext -bbox``; line-level XML as a fallback."""
    _require("pdftotext")
    result = _run(["pdftotext", "-bbox", "-q", str(path), "-"], timeout=180)
    if result.returncode == 0 and "<word" in result.stdout:
        return _parse_bbox(result.stdout)
    return _extract_text_pdf_html(path, workdir)


def _extract_text_pdf_html(path: Path, workdir: Path | None = None) -> list[Page]:
    _require("pdftohtml")
    temp = Path(tempfile.mkdtemp(prefix="ecomops-xml-", dir=workdir))
    base = temp / "doc"
    result = _run(["pdftohtml", "-xml", "-hidden", "-i", "-q", str(path), str(base)], timeout=180)
    xml_path = base.with_suffix(".xml")
    if result.returncode != 0 or not xml_path.exists():
        detail = (result.stderr or result.stdout).strip()[:300]
        raise ExtractionError(f"pdftohtml failed: {detail or 'no XML produced'}")
    return _parse_xml(xml_path)


# --------------------------------------------------------------------------- #
# Scans and photos
# --------------------------------------------------------------------------- #

def render_pages(path: Path, outdir: Path, dpi: int = PREVIEW_DPI) -> list[Path]:
    _require("pdftoppm")
    outdir.mkdir(parents=True, exist_ok=True)
    base = outdir / "page"
    result = _run(
        ["pdftoppm", "-r", str(dpi), "-png", str(path), str(base)],
        timeout=300,
    )
    if result.returncode != 0:
        raise ExtractionError(f"pdftoppm failed: {result.stderr.strip()[:300]}")
    return sorted(outdir.glob("page-*.png"), key=_page_number)


def _page_number(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    return int(match.group(1)) if match else 0


def _ocr_words(image: Path, langs: str, scale: float) -> tuple[float, float, list[Word]]:
    _require("tesseract")
    _check_languages(langs)
    result = _run(
        ["tesseract", str(image), "stdout", "-l", langs, "--psm", "3", "tsv"],
        timeout=300,
    )
    if result.returncode != 0:
        raise ExtractionError(f"tesseract failed: {result.stderr.strip()[:300]}")

    from PIL import Image  # local import: Pillow is optional outside document work

    with Image.open(image) as picture:
        width_px, height_px = picture.size

    words: list[Word] = []
    lines = result.stdout.splitlines()
    if not lines:
        return width_px * scale, height_px * scale, words
    header = lines[0].split("\t")
    index = {name: position for position, name in enumerate(header)}
    for raw in lines[1:]:
        cells = raw.split("\t")
        if len(cells) < len(header):
            continue
        try:
            level = int(cells[index["level"]])
        except (KeyError, ValueError):
            continue
        if level != 5:  # 5 = word
            continue
        content = cells[index["text"]].strip()
        if not content:
            continue
        try:
            left = float(cells[index["left"]])
            top = float(cells[index["top"]])
            w = float(cells[index["width"]])
            h = float(cells[index["height"]])
            confidence = float(cells[index["conf"]])
        except (KeyError, ValueError):
            continue
        if confidence < 30:
            continue
        words.append(
            Word(
                text=content,
                box=Box(x=left * scale, y=top * scale, w=w * scale, h=h * scale),
                size=max(6.0, h * scale * 0.72),
            )
        )
    return width_px * scale, height_px * scale, words


def extract_scan(path: Path, workdir: Path | None = None, langs: str | None = None) -> list[Page]:
    if not ocr_available():
        raise OcrUnavailable(
            "This looks like a scan or a photo and no OCR engine is installed. "
            "Install it with: sudo apt-get install -y tesseract-ocr tesseract-ocr-eng"
        )
    spec = langs or configured_languages()
    temp = Path(tempfile.mkdtemp(prefix="ecomops-ocr-", dir=workdir))
    pages: list[Page] = []
    for index, image in enumerate(render_pages(path, temp, dpi=OCR_DPI), start=1):
        width, height, words = _ocr_words(image, spec, 72 / OCR_DPI)
        pages.append(
            Page(number=index, width=width, height=height, words=words, image=image)
        )
    return pages


def extract_image(path: Path, langs: str | None = None) -> list[Page]:
    if not ocr_available():
        raise OcrUnavailable(
            "Reading images needs an OCR engine. "
            "Install it with: sudo apt-get install -y tesseract-ocr tesseract-ocr-eng"
        )
    spec = langs or configured_languages()
    width, height, words = _ocr_words(path, spec, 72 / 96)  # assume 96 dpi for photos
    return [Page(number=1, width=width, height=height, words=words, image=path)]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def extract(
    path: Path,
    workdir: Path | None = None,
    langs: str | None = None,
) -> ExtractedDoc:
    """Read ``path`` into positioned text, choosing the right strategy."""
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"No such file: {path}")
    if path.stat().st_size == 0:
        raise ExtractionError("The file is empty.")

    kind = detect_kind(path)
    if kind is DocKind.TEXT_PDF:
        pages = extract_text_pdf(path, workdir=workdir)
    elif kind is DocKind.SCAN:
        pages = extract_scan(path, workdir=workdir, langs=langs)
    else:
        pages = extract_image(path, langs=langs)

    spec = langs or configured_languages()
    return ExtractedDoc(
        path=path,
        kind=kind,
        pages=pages,
        languages=[part for part in spec.split("+") if part],
        sha256=file_sha256(path),
    )


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def locate_box(pages: list[Page], needle: str) -> Box | None:
    """Find the box of the first line containing ``needle``.

    Returns the union of the words covering the needle, so a filler can draw a
    value without touching the label to its left. The needle must sit on word
    boundaries, otherwise ``"1"`` would match inside ``RET-2026-0031``.
    """
    target = _normalise(needle)
    if not target:
        return None
    boundary = re.compile(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])")
    for page in pages:
        for line in page.lines():
            text = " ".join(word.text for word in line)
            haystack = _normalise(text)
            match = boundary.search(haystack)
            if not match:
                continue
            start = match.start()
            # Walk words until the character offset of the match is reached.
            cursor = 0
            box: Box | None = None
            for word in line:
                begin = haystack.find(_normalise(word.text), cursor)
                if begin < 0:
                    begin = cursor
                if begin + len(_normalise(word.text)) > start and box is None:
                    box = word.box
                if box is not None:
                    box = box.union(word.box)
                cursor = begin + len(_normalise(word.text))
                if cursor >= start + len(target):
                    break
            return box
    return None
