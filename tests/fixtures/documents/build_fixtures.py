"""Build the document-intake fixtures.

Run from the repository root::

    python tests/fixtures/documents/build_fixtures.py

Requires ghostscript (``ps2pdf``) and poppler (``pdftoppm``) for the scan
variant. The generated files are checked in, so the test suite does not need
either tool at run time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAGE = 842


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def postscript(lines: list[tuple[float, float, str, bool]], title: str) -> str:
    """lines: (x, y, text, bold) in PDF points."""
    body = [
        "%!PS-Adobe-3.0",
        f"<< /PageSize [595 {PAGE}] >> setpagedevice",
        f"%%Title: {title}",
    ]
    for x, y, text, bold in lines:
        font = "Helvetica-Bold" if bold else "Helvetica"
        body.append(f"/{font} findfont 10 scalefont setfont")
        body.append(f"{x} {y} moveto ({_escape(text)}) show")
    body.extend(["showpage", "%%EOF", ""])
    return "\n".join(body)


def build(name: str, lines: list[tuple[float, float, str, bool]], title: str) -> Path:
    ps_path = HERE / f"{name}.ps"
    pdf_path = HERE / f"{name}.pdf"
    ps_path.write_text(postscript(lines, title), encoding="utf-8")
    subprocess.run(
        ["ps2pdf", "-dEmbedAllFonts=false", str(ps_path), str(pdf_path)],
        check=True,
        capture_output=True,
    )
    ps_path.unlink()
    return pdf_path


PURCHASE_ORDER = [
    (72, 780, "MSM PURCHASE ORDER", True),
    (72, 762, "PO No: PO-2026-0148", False),
    (72, 749, "Date: 2026-11-13", False),
    (72, 736, "Customer: Northwind Traders", False),
    (72, 710, "SKU", True),
    (200, 710, "Description", True),
    (400, 710, "Qty", True),
    (450, 710, "Unit Price", True),
    (520, 710, "Total", True),
    (72, 694, "SKU-104", False),
    (200, 694, "Cashmere Scarf", False),
    (400, 694, "12", False),
    (450, 694, "184.00", False),
    (520, 694, "2208.00", False),
    (72, 680, "SKU-221", False),
    (200, 680, "Leather Card Holder", False),
    (400, 680, "30", False),
    (450, 680, "72.50", False),
    (520, 680, "2175.00", False),
    (72, 666, "SKU-330", False),
    (200, 666, "Silk Pocket Square", False),
    (400, 666, "8", False),
    (450, 666, "96.00", False),
    (520, 666, "768.00", False),
    (72, 636, "Order Total", True),
    (520, 636, "5151.00", True),
    (72, 600, "Ship to: 44 Hanover Square, London W1S 1JD", False),
    (72, 587, "Requested delivery: 2026-11-20", False),
]

RETURN_SLIP = [
    (72, 780, "MSM RETURN / REFUND SLIP", True),
    (72, 762, "Return No: RET-2026-0031", False),
    (72, 749, "Date: 2026-11-12", False),
    (72, 736, "Customer: Northwind Traders", False),
    (72, 723, "Order No: ORD-2026-0114", False),
    (72, 710, "Shipment: SHIP-2026-0093", False),
    (72, 697, "SKU: SKU-104", False),
    (72, 684, "Qty: 1", False),
    (72, 671, "Reason: Damaged on arrival", False),
    (72, 658, "Refund Total: 184.00", True),
]

STOCK_SHEET = [
    (72, 780, "MSM STOCK / CLEARANCE SHEET", True),
    (72, 762, "Warehouse: London-01", False),
    (72, 749, "SKU: SKU-221", False),
    (72, 736, "Description: Leather Card Holder", False),
    (72, 723, "On Hand: 42", False),
    (72, 710, "Unit Cost: 72.50", False),
    (72, 697, "Markdown: 25%", False),
    (72, 684, "Location: A-14", False),
]


def build_scan(pdf: Path, name: str) -> Path:
    """Render the first page and wrap it as an image-only PDF."""
    from PIL import Image

    prefix = HERE / f"_scan_{name}"
    subprocess.run(
        ["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", "1", str(pdf), str(prefix)],
        check=True,
        capture_output=True,
    )
    png = next(HERE.glob(f"_scan_{name}-*.png"))
    target = HERE / f"{name}_scan.pdf"
    with Image.open(png) as image:
        image.convert("RGB").save(target, "PDF", resolution=150)
    png.unlink()
    return target


def main() -> int:
    po = build("purchase_order", PURCHASE_ORDER, "Purchase order PO-2026-0148")
    ret = build("return_slip", RETURN_SLIP, "Return slip RET-2026-0031")
    build("stock_sheet", STOCK_SHEET, "Stock sheet SKU-221")
    build_scan(po, "purchase_order")
    build_scan(ret, "return_slip")
    print(f"fixtures written to {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
