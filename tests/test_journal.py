"""Durable event journal: append, typed read-back, causal verification."""

import json

from ecomops.events import CustomerRequestReceived, event_classes
from ecomops.journal import EventJournal, journal_summary, read_journal, verify_journal
from ecomops.system import build_system


async def test_journal_records_every_event(tmp_path):
    path = tmp_path / "events.jsonl"
    system = build_system(journal_path=path)

    await system.receive_customer_request("CUST-J1", "1 desk lamp led")

    records = read_journal(path)
    assert len(records) == len(system.bus.history)
    assert [r.topic for r in records] == [r.topic for r in system.bus.history]
    assert verify_journal(records) == []
    assert journal_summary(records)["conversations"] >= 1


def test_journal_round_trips_typed_events(tmp_path):
    path = tmp_path / "typed.jsonl"
    journal = EventJournal(path)
    event = CustomerRequestReceived(customer_id="CUST-J2", request_text="hello")
    journal.write(event)

    records = read_journal(path)
    assert len(records) == 1
    assert isinstance(records[0].event, CustomerRequestReceived)
    assert records[0].event.customer_id == "CUST-J2"


def test_verify_detects_broken_causation(tmp_path):
    path = tmp_path / "broken.jsonl"
    journal = EventJournal(path)
    event = CustomerRequestReceived(
        customer_id="CUST-J3",
        request_text="hello",
        correlation_id="corr",
        causation_id="missing-parent",
    )
    journal.write(event)

    problems = verify_journal(read_journal(path))
    assert any("missing-parent" in p for p in problems)


def test_event_registry_covers_new_topics():
    classes = event_classes()
    assert {
        "CustomerRequestReceived",
        "ShipmentRouted",
        "RefundIssued",
        "ReplacementAuthorised",
        "ReturnRejected",
        "EscalationRaised",
    } <= set(classes)


def test_journal_line_is_valid_json(tmp_path):
    path = tmp_path / "line.jsonl"
    journal = EventJournal(path)
    journal.write(CustomerRequestReceived(customer_id="C", request_text="hi"))
    line = path.read_text(encoding="utf-8").strip()
    body = json.loads(line)
    assert body["topic"] == "CustomerRequestReceived"
    assert body["event"]["customer_id"] == "C"
