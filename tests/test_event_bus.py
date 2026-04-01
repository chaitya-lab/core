"""Tests for chaitya.core.event_bus — SQLite-backed pub/sub EventBus."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.protocols import EventBusProtocol
from chaitya.core.types import Event, EventFilter, Subscription


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def bus() -> SqliteEventBus:
    """Create an in-memory event bus, open it, yield, then close."""
    b = SqliteEventBus(db_path=":memory:")
    await b.open()
    yield b  # type: ignore[misc]
    await b.close()


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_event_bus_protocol(self) -> None:
        bus = SqliteEventBus()
        assert isinstance(bus, EventBusProtocol)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_open_close(self) -> None:
        bus = SqliteEventBus(db_path=":memory:")
        await bus.open()
        assert bus._db is not None
        await bus.close()
        assert bus._db is None

    async def test_emit_before_open_raises(self) -> None:
        bus = SqliteEventBus(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            await bus.emit(Event(type="test"))

    async def test_history_before_open_raises(self) -> None:
        bus = SqliteEventBus(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            await bus.history(EventFilter())


# ---------------------------------------------------------------------------
# Emit & Persist
# ---------------------------------------------------------------------------


class TestEmit:
    async def test_emit_persists_event(self, bus: SqliteEventBus) -> None:
        event = Event(type="kernel_started", source_adapter="kernel")
        await bus.emit(event)

        count = await bus.event_count()
        assert count == 1

    async def test_emit_multiple_events(self, bus: SqliteEventBus) -> None:
        for i in range(5):
            await bus.emit(Event(type=f"event_{i}"))
        assert await bus.event_count() == 5

    async def test_emit_preserves_payload(self, bus: SqliteEventBus) -> None:
        event = Event(
            type="test",
            payload={"key": "value", "number": 42},
        )
        await bus.emit(event)
        events = await bus.history(EventFilter())
        assert events[0].payload == {"key": "value", "number": 42}

    async def test_emit_preserves_all_fields(self, bus: SqliteEventBus) -> None:
        event = Event(
            type="process_exit",
            source_adapter="shell",
            session_id="s1",
            exit_code=0,
            duration_ms=150,
            payload={"cmd": "ls"},
            request_id="req-1",
        )
        await bus.emit(event)
        result = (await bus.history(EventFilter()))[0]
        assert result.event_id == event.event_id
        assert result.type == "process_exit"
        assert result.source_adapter == "shell"
        assert result.session_id == "s1"
        assert result.exit_code == 0
        assert result.duration_ms == 150
        assert result.request_id == "req-1"


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


class TestSubscriptions:
    async def test_subscribe_receives_matching_events(
        self, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        ef = EventFilter(event_types=["test_event"])
        await bus.subscribe(ef, received.append)

        await bus.emit(Event(type="test_event"))
        await bus.emit(Event(type="other_event"))

        assert len(received) == 1
        assert received[0].type == "test_event"

    async def test_subscribe_all_events(self, bus: SqliteEventBus) -> None:
        received: list[Event] = []
        await bus.subscribe(EventFilter(), received.append)

        await bus.emit(Event(type="a"))
        await bus.emit(Event(type="b"))

        assert len(received) == 2

    async def test_unsubscribe_stops_delivery(
        self, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        sub = await bus.subscribe(EventFilter(), received.append)

        await bus.emit(Event(type="before"))
        await bus.unsubscribe(sub)
        await bus.emit(Event(type="after"))

    async def test_filter_by_session_id(self, bus: SqliteEventBus) -> None:
        received: list[Event] = []
        ef = EventFilter(session_id="s1")
        await bus.subscribe(ef, received.append)

        await bus.emit(Event(type="a", session_id="s1"))
        await bus.emit(Event(type="b", session_id="s2"))

        assert len(received) == 1
        assert received[0].session_id == "s1"

    async def test_filter_by_request_id(self, bus: SqliteEventBus) -> None:
        received: list[Event] = []
        ef = EventFilter(request_id="req-42")
        await bus.subscribe(ef, received.append)

        await bus.emit(Event(type="a", request_id="req-42"))
        await bus.emit(Event(type="b", request_id="req-99"))

        assert len(received) == 1

    async def test_async_handler(self, bus: SqliteEventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(EventFilter(), handler)
        await bus.emit(Event(type="async_test"))
        assert len(received) == 1

    async def test_handler_error_does_not_break_bus(
        self, bus: SqliteEventBus
    ) -> None:
        good_received: list[Event] = []

        def bad_handler(event: Event) -> None:
            raise ValueError("boom")

        await bus.subscribe(EventFilter(), bad_handler)
        await bus.subscribe(EventFilter(), good_received.append)

        await bus.emit(Event(type="test"))
        # Bad handler crashed but good one still received
        assert len(good_received) == 1


# ---------------------------------------------------------------------------
# History Queries
# ---------------------------------------------------------------------------


class TestHistory:
    async def test_history_returns_all(self, bus: SqliteEventBus) -> None:
        for i in range(3):
            await bus.emit(Event(type=f"ev_{i}"))
        events = await bus.history(EventFilter())
        assert len(events) == 3

    async def test_history_filter_by_type(self, bus: SqliteEventBus) -> None:
        await bus.emit(Event(type="alpha"))
        await bus.emit(Event(type="beta"))
        await bus.emit(Event(type="alpha"))

        events = await bus.history(EventFilter(event_types=["alpha"]))
        assert len(events) == 2

    async def test_history_filter_by_session(
        self, bus: SqliteEventBus
    ) -> None:
        await bus.emit(Event(type="a", session_id="s1"))
        await bus.emit(Event(type="b", session_id="s2"))

        events = await bus.history(EventFilter(session_id="s1"))
        assert len(events) == 1

    async def test_history_with_limit(self, bus: SqliteEventBus) -> None:
        for i in range(10):
            await bus.emit(Event(type="ev"))
        events = await bus.history(EventFilter(limit=3))
        assert len(events) == 3

    async def test_history_filter_by_request_id(
        self, bus: SqliteEventBus
    ) -> None:
        await bus.emit(Event(type="req", request_id="r1"))
        await bus.emit(Event(type="req", request_id="r2"))

        events = await bus.history(EventFilter(request_id="r1"))
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    async def test_rate_limit_drops_excess(self) -> None:
        bus = SqliteEventBus(db_path=":memory:", max_events_per_second=5)
        await bus.open()
        try:
            for i in range(10):
                await bus.emit(Event(type=f"ev_{i}"))
            count = await bus.event_count()
            # Should have dropped some events
            assert count <= 5
        finally:
            await bus.close()
