"""Bus behaviour: dispatch, correlation/causation, drain, loop guard."""

import pytest

from ecomops.bus import EventBus
from ecomops.events import Event


class Ping(Event):
    topic = "Ping"


class Pong(Event):
    topic = "Pong"


async def test_publish_delivers_to_matching_subscriber_only():
    bus = EventBus()
    seen: list[Event] = []

    async def on_ping(e: Event) -> None:
        seen.append(e)

    bus.subscribe("a", on_ping, "Ping")
    bus.publish(Ping())
    await bus.drain()

    assert [e.topic for e in seen] == ["Ping"]


async def test_wildcard_subscription():
    bus = EventBus()
    seen: list[str] = []

    async def on_any(e: Event) -> None:
        seen.append(e.topic)

    bus.subscribe("watcher", on_any, "*")
    bus.publish(Ping())
    bus.publish(Pong())
    await bus.drain()

    assert seen == ["Ping", "Pong"]


async def test_correlation_and_causation_chain():
    bus = EventBus()
    triggers: list[Event] = []

    async def on_ping(e: Event) -> None:
        triggers.append(e)
        bus.publish(Pong())          # published while handling Ping

    bus.subscribe("a", on_ping, "Ping")
    root = bus.publish(Ping())
    await bus.drain()

    pongs = bus.of_type("Pong")
    assert len(pongs) == 1
    assert pongs[0].correlation_id == root.correlation_id   # inherited
    assert pongs[0].causation_id == root.event_id           # parent recorded


async def test_drain_terminates_and_counts():
    bus = EventBus()

    async def noop(e: Event) -> None:
        return None

    bus.subscribe("a", noop, "*")
    bus.publish(Ping())
    bus.publish(Pong())
    processed = await bus.drain()

    assert processed == 2


async def test_runaway_loop_guard():
    bus = EventBus()

    async def echo(e: Event) -> None:
        bus.publish(Pong() if e.topic == "Ping" else Ping())

    bus.subscribe("a", echo, "*")
    bus.publish(Ping())

    with pytest.raises(RuntimeError, match="did not settle"):
        await bus.drain(max_events=50)


async def test_handler_exception_does_not_kill_bus():
    bus = EventBus()
    ok: list[Event] = []

    async def bad(e: Event) -> None:
        raise RuntimeError("boom")

    async def good(e: Event) -> None:
        ok.append(e)

    bus.subscribe("bad", bad, "Ping")
    bus.subscribe("good", good, "Ping")
    bus.publish(Ping())
    processed = await bus.drain()

    assert processed == 2
    assert len(ok) == 1


async def test_of_type_and_last_and_conversation():
    bus = EventBus()

    async def noop(e: Event) -> None:
        return None

    bus.subscribe("a", noop, "*")
    a = bus.publish(Ping())
    b = bus.publish(Pong())
    b.correlation_id = a.correlation_id

    assert len(bus.of_type("Ping", "Pong")) == 2
    assert bus.last("Ping") is a
    assert [r.topic for r in bus.conversation(a.correlation_id or "")] == ["Ping", "Pong"]
