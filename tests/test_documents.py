"""Document intake: extraction, classification, resolution, fill and audit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ecomops.auth import Role, demo_user
from ecomops.documents import (
    DocType,
    DocumentFieldsResolved,
    DocumentFilled,
    DocumentTooLarge,
    DocumentUploaded,
    TooManyPages,
    UnsupportedDocument,
    answer,
    apply_answers,
    classify,
    discard,
    extract,
    extract_fields,
    finish,
    message,
    ocr_available,
    open_document,
    parse_answers,
    pipeline,
    rows,
    validate,
)
from ecomops.documents.extract import locate_box
from ecomops.documents.schema import FieldStatus
from ecomops.events import RefundIssued, RiskLevel, RoutePlan, ShipmentRouted
from ecomops.system import build_system

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
PO = FIXTURES / "purchase_order.pdf"
RETURN = FIXTURES / "return_slip.pdf"
STOCK = FIXTURES / "stock_sheet.pdf"


@pytest.fixture()
def system():
    return build_system()


@pytest.fixture()
def user():
    return demo_user(Role.MD)


def _pdf_pages(path: Path) -> int:
    result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        if line.lower().startswith("pages:"):
            return int(line.split(":", 1)[1].strip())
    raise AssertionError(f"pdfinfo did not report a page count for {path}")


def _render_page(path: Path, tmp_path: Path, page: int = 1):
    from PIL import Image

    base = tmp_path / f"page-{page}"
    subprocess.run(
        ["pdftoppm", "-png", "-f", str(page), "-l", str(page), "-r", "60", str(path), str(base)],
        check=True,
        capture_output=True,
    )
    produced = sorted(tmp_path.glob(f"page-{page}-*.png"))
    assert produced, f"pdftoppm produced no image for {path}"
    return Image.open(produced[0]).convert("RGB")


def _has_ink(image) -> bool:
    from PIL import ImageOps

    return ImageOps.grayscale(image).getextrema()[0] < 140


def _ship(system, *, order_id="ORD-2026-0114", shipment_id="SHIP-2026-0093", sku="SKU-1001"):
    system.bus.publish(
        ShipmentRouted(
            shipment_id=shipment_id,
            order_id=order_id,
            sku=sku,
            quantity=1,
            destination="44 Hanover Square, London W1S 1JD",
            customer_id="CUST-001",
            order_value=184.0,
            plan=RoutePlan(
                carrier="DHL Express",
                service_level="next-day",
                eta_days=2,
                cost=12.0,
                risk=RiskLevel.LOW,
                rationale="cheapest within SLA",
            ),
        )
    )


# --------------------------------------------------------------------------- #
# Extraction & classification
# --------------------------------------------------------------------------- #

def test_extract_keeps_word_level_boxes():
    doc = extract(PO)
    assert doc.page_count == 1
    assert doc.kind.value == "text_pdf"
    assert doc.text.startswith("MSM PURCHASE ORDER")
    assert doc.sha256

    box = locate_box(doc.pages, "SKU-104")
    assert box is not None
    # A word box, not the whole "SKU-104 Cashmere Scarf 12 …" line.
    assert box.w < 60


def test_locator_ignores_substrings_inside_longer_ids():
    doc = extract(RETURN)
    quantity = next(f for f in extract_fields(doc.text, DocType.RETURN_SLIP) if f.key == "quantity")
    from ecomops.documents.classify import attach_boxes

    attach_boxes([quantity], doc.pages)
    assert quantity.value == "1"
    assert quantity.box is not None
    assert quantity.box.w < 12  # the "1" in "Qty: 1", not inside RET-2026-0031


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (PO, DocType.PURCHASE_ORDER),
        (RETURN, DocType.RETURN_SLIP),
        (STOCK, DocType.STOCK_SHEET),
    ],
)
def test_classification_is_deterministic(path, expected):
    doc = extract(path)
    result = classify(doc.text)
    assert result.doc_type is expected
    assert result.confidence >= 0.8
    assert result.evidence


def test_field_extraction_reads_labelled_values():
    doc = extract(PO)
    fields = {f.key: f for f in extract_fields(doc.text, DocType.PURCHASE_ORDER)}
    assert fields["po_number"].value == "PO-2026-0148"
    assert fields["customer"].value == "Northwind Traders"
    assert fields["order_total"].value == "5151.00"
    assert fields["requested_delivery"].value == "2026-11-20"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def test_resolution_asks_only_for_what_is_unknown(system, user):
    session = open_document(system, user, RETURN)
    try:
        assert session.job.doc_type is DocType.RETURN_SLIP
        blocking = {f.key for f in session.missing}
        assert blocking == {"sku"}
        # Shipment is optional: it is flagged, but it does not block the fill.
        assert {f.key for f in session.job.missing} == {"sku", "shipment_id"}
        assert "SKU-104 is not in the catalogue" in message(session)
    finally:
        discard(session)


def test_answers_re_resolve_and_enrich_from_the_catalogue(system, user):
    session = open_document(system, user, STOCK)
    try:
        assert [f.key for f in session.missing] == ["sku"]

        updated = answer(system, user, session, "sku=SKU-1001")
        assert updated == ["sku"]
        assert session.ready

        fields = {f.key: f for f in session.job.fields}
        assert fields["sku"].resolved == "SKU-1001"
        assert fields["sku"].source == "operator:md@msm.local"
        # Catalogue wins over the printed sheet for stock and description.
        assert fields["on_hand"].answer == "240"
        assert fields["description"].answer == "wireless bluetooth earbuds"
        assert fields["on_hand"].source == "catalogue:SKU-1001"
    finally:
        discard(session)


def test_events_supply_shipment_and_refund_facts(system, user):
    _ship(system)
    system.bus.publish(
        RefundIssued(
            customer_id="CUST-001",
            order_id="ORD-2026-0114",
            sku="SKU-1001",
            quantity=1,
            amount=184.0,
            restocked=True,
            decided_by="accountant@msm.local",
        )
    )
    session = open_document(system, user, RETURN)
    try:
        fields = {f.key: f for f in session.job.fields}
        assert fields["shipment_id"].answer == "SHIP-2026-0093"
        assert fields["shipment_id"].source.startswith("ShipmentRouted:")
        assert fields["refund_total"].answer == "184.00"
        assert fields["refund_total"].source.startswith("RefundIssued:")
        # The document's SKU-104 is still unknown, so that remains the only gap.
        assert [f.key for f in session.missing] == ["sku"]
    finally:
        discard(session)


def test_blank_answer_marks_a_gap_deliberately_left_empty(system, user):
    session = open_document(system, user, RETURN)
    try:
        answer(system, user, session, "sku=")
        field = next(f for f in session.job.fields if f.key == "sku")
        assert field.status is FieldStatus.KNOWN
        assert field.resolved == ""
        assert field.source.startswith("operator:")
        assert session.ready
    finally:
        discard(session)


def test_parse_answers_accepts_labels_and_bare_values(system, user):
    session = open_document(system, user, RETURN)
    try:
        assert parse_answers("sku=SKU-1001", session.job) == {"sku": "SKU-1001"}
        assert parse_answers("SKU: SKU-104", session.job) == {"sku": "SKU-104"}
    finally:
        discard(session)

    stock = open_document(system, user, STOCK)
    try:
        assert parse_answers("SKU-1001", stock.job) == {"sku": "SKU-1001"}
    finally:
        discard(stock)


def test_apply_answers_ignores_unknown_keys(system, user):
    session = open_document(system, user, PO)
    try:
        assert apply_answers(user, session.job, {"not_a_field": "x"}) == []
    finally:
        discard(session)


# --------------------------------------------------------------------------- #
# Fill
# --------------------------------------------------------------------------- #

def test_overlay_fill_writes_a_pdf_with_an_annex(system, user, tmp_path):
    session = open_document(system, user, RETURN)
    try:
        answer(system, user, session, "sku=SKU-1001\nshipment_id=")
        output = session.workdir / "filled.pdf"
        finish(system, user, session, output=output)
        assert output.read_bytes().startswith(b"%PDF")
        assert output.stat().st_size > 40_000
        # Filled pages are raster, so the annex is checked as pixels, not text.
        assert _pdf_pages(output) == 2  # original page + answers & sources
        assert _has_ink(_render_page(output, tmp_path, page=2))
        assert session.job.status == "filled"
    finally:
        discard(session)


def test_overlay_writes_the_answer_inside_the_word_box():
    from PIL import Image, ImageOps

    from ecomops.documents.fill import _overlay_page
    from ecomops.documents.schema import Box, DocField, FieldStatus, Page, Word

    box = Box(x=100, y=100, w=40, h=10)
    page = Page(number=1, width=595, height=842, words=[Word(text="SKU-104", box=box)])
    field = DocField(
        key="sku",
        label="SKU",
        value="SKU-104",
        resolved="SKU-1001",
        source="operator:md@msm.local",
        status=FieldStatus.KNOWN,
        box=box,
    )
    canvas = Image.new("RGB", (1190, 1684), (255, 255, 255))

    filled = _overlay_page(canvas, page, [field])
    grey = ImageOps.grayscale(filled)

    assert filled is canvas
    inside = [grey.getpixel((x, y)) for x in range(195, 285) for y in range(195, 225)]
    assert min(inside) < 140  # the answer was drawn over the old value
    assert grey.getpixel((900, 900)) == 255  # the rest of the page is untouched


def test_reflow_fill_is_a_clean_regenerated_pdf(system, user, tmp_path):
    session = open_document(system, user, PO)
    try:
        output = session.workdir / "reflow.pdf"
        finish(system, user, session, output=output, mode="reflow")
        assert output.read_bytes().startswith(b"%PDF")
        assert _pdf_pages(output) >= 1
        assert _has_ink(_render_page(output, tmp_path, page=1))
        assert session.job.output_path == output
    finally:
        discard(session)


def test_finish_emits_one_audit_trail(system, user):
    session = open_document(system, user, PO)
    try:
        finish(system, user, session, output=session.workdir / "audit.pdf")
        topics = [event.topic for event in system.bus.history]
        assert topics == ["DocumentUploaded", "DocumentFieldsResolved", "DocumentFilled"]
        assert isinstance(system.bus.history[0].event, DocumentUploaded)
        assert isinstance(system.bus.history[1].event, DocumentFieldsResolved)
        filled = system.bus.history[2].event
        assert isinstance(filled, DocumentFilled)
        assert filled.filled_by == "md@msm.local"
        assert filled.doc_type == "purchase_order"
        # The document bytes never travel on the bus.
        assert not hasattr(filled, "bytes")
    finally:
        discard(session)


def test_system_entry_point_and_rows(system, user):
    session = system.open_document(PO, user)
    try:
        table = rows(session.job)
        assert {row["key"] for row in table} == {
            "po_number",
            "order_date",
            "customer",
            "ship_to",
            "requested_delivery",
            "order_total",
        }
        assert all(row["status"] == "known" for row in table)
    finally:
        discard(session)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #

def test_unsupported_type_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(UnsupportedDocument):
        validate(path)


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "big.png"
    path.write_bytes(b"x" * 64)
    monkeypatch.setattr(pipeline, "MAX_BYTES", 32)
    with pytest.raises(DocumentTooLarge):
        validate(path)


def test_page_limit_is_enforced(tmp_path, monkeypatch):
    path = tmp_path / "many.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(pipeline, "pdf_page_count", lambda _path: 25)
    with pytest.raises(TooManyPages):
        validate(path)


def test_scanned_pdf_needs_ocr_when_engine_is_missing():
    scan = FIXTURES / "return_slip_scan.pdf"
    if ocr_available():
        pytest.skip("tesseract is installed; the OCR path is exercised instead")
    from ecomops.documents import OcrUnavailable

    with pytest.raises(OcrUnavailable):
        extract(scan)


def test_empty_file_is_refused(tmp_path):
    path = tmp_path / "empty.pdf"
    path.touch()
    with pytest.raises(pipeline.DocumentError):
        validate(path)
