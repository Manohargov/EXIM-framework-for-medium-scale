"""Append-only JSONL event journal.

The in-process bus gives deterministic behaviour, but its history dies with the
process. The journal subscribes to ``*`` and writes every event to disk as one
JSON object per line, so the audit trail can be inspected, diffed, shipped to a
log pipeline, or replayed after a restart.

The file is deliberately boring: no database, no schema migrations, no locking
beyond append mode. Each line is::

    {"seq": 1, "topic": "CustomerRequestReceived", "event": {...}}

``read_journal`` rebuilds typed events through the topic registry in
``events.py``; ``verify_journal`` checks the correlation/causation chain rather
than trusting the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bus import EventBus
from .events import Event, event_for


@dataclass(frozen=True)
class JournalRecord:
    seq: int
    topic: str
    event: Event

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "topic": self.topic,
            "event": self.event.model_dump(mode="json"),
        }


class EventJournal:
    """Subscribes to every event and appends it to a JSONL file."""

    def __init__(self, path: str | Path, *, truncate: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if truncate:
            self.path.write_text("", encoding="utf-8")
        existing = read_journal(self.path) if self.path.exists() else []
        self._seq = max((record.seq for record in existing), default=0)

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("event-journal", self.on_event, "*")

    async def on_event(self, event: Event) -> None:
        self.write(event)

    def write(self, event: Event) -> JournalRecord:
        self._seq += 1
        record = JournalRecord(seq=self._seq, topic=event.topic, event=event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
        return record


def read_journal(path: str | Path) -> list[JournalRecord]:
    """Read a journal back into typed events, skipping blank lines."""
    p = Path(path)
    if not p.exists():
        return []
    records: list[JournalRecord] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            body = json.loads(line)
            topic = body["topic"]
            records.append(
                JournalRecord(seq=int(body["seq"]), topic=topic, event=event_for(topic, body["event"]))
            )
    return records


def verify_journal(records: list[JournalRecord]) -> list[str]:
    """Return human-readable problems with the recorded causal chain.

    An empty list means every causation id points at an earlier event and every
    event has a correlation id — i.e. the conversation can be replayed.
    """
    problems: list[str] = []
    seen: dict[str, int] = {}
    previous_seq = 0
    for record in records:
        if record.seq <= previous_seq:
            problems.append(f"seq {record.seq} is not increasing (previous {previous_seq})")
        previous_seq = record.seq
        if not record.event.correlation_id:
            problems.append(f"seq {record.seq} {record.topic}: missing correlation_id")
        causation = record.event.causation_id
        if causation and causation not in seen:
            problems.append(
                f"seq {record.seq} {record.topic}: causation {causation} not recorded earlier"
            )
        seen[record.event.event_id] = record.seq
    return problems


def journal_summary(records: list[JournalRecord]) -> dict[str, Any]:
    """Counts used by the ``replay`` CLI and the HTTP ``/metrics`` view."""
    topics: dict[str, int] = {}
    conversations: set[str] = set()
    for record in records:
        topics[record.topic] = topics.get(record.topic, 0) + 1
        if record.event.correlation_id:
            conversations.add(record.event.correlation_id)
    return {
        "events": len(records),
        "topics": topics,
        "conversations": len(conversations),
        "first": records[0].event.ts.isoformat() if records else None,
        "last": records[-1].event.ts.isoformat() if records else None,
    }
