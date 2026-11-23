"""In-process event bus.

Design decisions (deliberate, documented in the README):

* **Deterministic FIFO dispatch.** ``publish`` appends to a global pending queue;
  ``drain`` processes it in order, awaiting each handler to completion. The same
  inputs always produce the same event sequence — which is what makes the test
  suite and the demo reproducible without a broker.
* **Correlation and causation are resolved by the bus**, not by agents. An event
  published while handling another inherits that event's correlation id and
  records its own causation id, so an entire business conversation can be
  replayed from the history.
* **The interface is swappable.** ``subscribe/publish/drain/history`` is the whole
  contract; a Redis Streams or NATS implementation can replace this without
  touching agent code.

There is no real concurrency here: agents are isolated by handler boundaries,
not by threads. That is a scaling trade-off, not an accident — see README.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .events import Event

log = logging.getLogger("ecomops.bus")

Handler = Callable[[Event], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    subscriber: str
    handler: Handler
    topics: frozenset[str]           # exact event names, or "*"

    def matches(self, topic: str) -> bool:
        return "*" in self.topics or topic in self.topics


@dataclass(frozen=True)
class Recorded:
    """One event as it sits in the bus history."""

    seq: int
    topic: str
    event: Event

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "topic": self.topic, "event": self.event.model_dump(mode="json")}


@dataclass
class EventBus:
    _subs: list[Subscription] = field(default_factory=list)
    _pending: deque[tuple[Subscription, Event]] = field(default_factory=deque)
    _history: list[Recorded] = field(default_factory=list)
    _current: Event | None = None

    # ------------------------------------------------------------------ setup

    def subscribe(self, subscriber: str, handler: Handler, *topics: str) -> None:
        """Register ``handler`` for the named event topics (or "*" for all)."""
        if not topics:
            raise ValueError("subscribe() requires at least one topic (or '*')")
        self._subs.append(Subscription(subscriber, handler, frozenset(topics)))

    # ---------------------------------------------------------------- publish

    def publish(self, event: Event) -> Event:
        """Enqueue ``event`` for every matching subscriber; returns the enriched event."""
        # Correlation / causation inheritance from the event being handled.
        if self._current is not None:
            if event.correlation_id is None:
                event.correlation_id = self._current.correlation_id or self._current.event_id
            event.causation_id = self._current.event_id
        elif event.correlation_id is None:
            event.correlation_id = event.event_id

        self._history.append(Recorded(len(self._history) + 1, event.topic, event))

        delivered = 0
        for sub in list(self._subs):
            if sub.matches(event.topic):
                self._pending.append((sub, event))
                delivered += 1
        log.debug("publish %s#%s -> %d subscriber(s)", event.topic, event.event_id, delivered)
        return event

    def publish_all(self, events: Iterable[Event]) -> None:
        for e in events:
            self.publish(e)

    # ------------------------------------------------------------------ drain

    async def drain(self, *, max_events: int = 10_000) -> int:
        """Process every pending event, FIFO, until the system is quiescent.

        Returns the number of handler invocations. Raises ``RuntimeError`` if the
        system does not settle within ``max_events`` — a runaway loop guard.
        """
        processed = 0
        while self._pending:
            if processed >= max_events:
                raise RuntimeError(f"bus did not settle within {max_events} events (loop?)")
            sub, event = self._pending.popleft()
            processed += 1
            self._current = event
            try:
                await sub.handler(event)
            except Exception:
                log.exception("handler %s failed for %s", sub.subscriber, event.topic)
            finally:
                self._current = None
        return processed

    # ------------------------------------------------------------------ introspection

    @property
    def history(self) -> list[Recorded]:
        return list(self._history)

    def of_type(self, *topics: str) -> list[Event]:
        wanted = set(topics)
        return [r.event for r in self._history if r.topic in wanted]

    def last(self, topic: str) -> Event | None:
        for r in reversed(self._history):
            if r.topic == topic:
                return r.event
        return None

    def conversation(self, correlation_id: str) -> list[Recorded]:
        """Every event belonging to one business conversation, in order."""
        return [r for r in self._history if (r.event.correlation_id or "") == correlation_id]

    @property
    def pending_approvals(self) -> list[Event]:
        """ApprovalRequested events with no ApprovalDecision yet."""
        decided = {e.proposal_id for e in self.of_type("ApprovalDecision")}  # type: ignore[attr-defined]
        return [e for e in self.of_type("ApprovalRequested") if e.proposal_id not in decided]  # type: ignore[attr-defined]

    def trace(self) -> list[tuple[int, str, str, str]]:
        """(seq, topic, source, one-line summary) for every event — used by the CLI."""
        out: list[tuple[int, str, str, str]] = []
        for r in self._history:
            body = r.event.model_dump(mode="json", exclude={"event_id", "correlation_id", "causation_id", "source", "ts"})
            out.append((r.seq, r.topic, r.event.source, _summarise(body)))
        return out


def _summarise(body: dict[str, Any]) -> str:
    parts = []
    for k, v in body.items():
        if isinstance(v, dict):
            v = "{...}"
        elif isinstance(v, list):
            v = f"[{len(v)} items]" if v else "[]"
        parts.append(f"{k}={v}")
    return ", ".join(parts)
