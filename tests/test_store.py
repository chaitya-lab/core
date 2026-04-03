"""Tests for chaitya.core.store — SQLite persistent store."""

from __future__ import annotations

import pytest

from chaitya.core.protocols import StoreProtocol
from chaitya.core.store import SqliteStore
from chaitya.core.types import (
    Event,
    EventFilter,
    SessionIdentity,
    SessionRecord,
    SessionState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> SqliteStore:
    """Create an in-memory store, open it, yield, then close."""
    s = SqliteStore(db_path=":memory:")
    await s.open()
    yield s  # type: ignore[misc]
    await s.close()


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_store_protocol(self) -> None:
        s = SqliteStore()
        assert isinstance(s, StoreProtocol)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_open_close(self) -> None:
        s = SqliteStore(db_path=":memory:")
        await s.open()
        assert s._db is not None
        await s.close()
        assert s._db is None

    async def test_save_before_open_raises(self) -> None:
        s = SqliteStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not open"):
            await s.save_session(SessionRecord(name="test"))


# ---------------------------------------------------------------------------
# Session Records
# ---------------------------------------------------------------------------


class TestSessionRecords:
    async def test_save_and_get(self, store: SqliteStore) -> None:
        record = SessionRecord(name="work", state=SessionState.IDLE)
        await store.save_session(record)
        result = await store.get_session("work")
        assert result is not None
        assert result.name == "work"
        assert result.state == SessionState.IDLE

    async def test_get_nonexistent_returns_none(self, store: SqliteStore) -> None:
        result = await store.get_session("nope")
        assert result is None

    async def test_save_updates_existing(self, store: SqliteStore) -> None:
        await store.save_session(SessionRecord(name="s1", state=SessionState.IDLE))
        await store.save_session(SessionRecord(name="s1", state=SessionState.BUSY))
        result = await store.get_session("s1")
        assert result is not None
        assert result.state == SessionState.BUSY

    async def test_list_sessions(self, store: SqliteStore) -> None:
        await store.save_session(SessionRecord(name="a"))
        await store.save_session(SessionRecord(name="b"))
        await store.save_session(SessionRecord(name="c"))
        sessions = await store.list_sessions()
        assert len(sessions) == 3
        names = [s.name for s in sessions]
        assert "a" in names and "b" in names and "c" in names

    async def test_delete_session(self, store: SqliteStore) -> None:
        await store.save_session(SessionRecord(name="to_delete"))
        await store.delete_session("to_delete")
        result = await store.get_session("to_delete")
        assert result is None

    async def test_delete_nonexistent_no_error(self, store: SqliteStore) -> None:
        await store.delete_session("nonexistent")  # should not raise

    async def test_session_identity_roundtrip(self, store: SqliteStore) -> None:
        identity = SessionIdentity(
            env_vars={"API_KEY": "secret", "HOME": "/home/user"},
            working_dir="~/projects",
            browser_profile="default",
        )
        record = SessionRecord(name="identity_test", identity=identity)
        await store.save_session(record)

        result = await store.get_session("identity_test")
        assert result is not None
        assert result.identity.env_vars["API_KEY"] == "secret"
        assert result.identity.working_dir == "~/projects"
        assert result.identity.browser_profile == "default"

    async def test_session_metadata_roundtrip(self, store: SqliteStore) -> None:
        record = SessionRecord(
            name="meta_test",
            metadata={"count": 42, "tags": ["dev", "test"]},
        )
        await store.save_session(record)
        result = await store.get_session("meta_test")
        assert result is not None
        assert result.metadata["count"] == 42
        assert result.metadata["tags"] == ["dev", "test"]

    async def test_session_template_roundtrip(self, store: SqliteStore) -> None:
        record = SessionRecord(name="tmpl_test", template="python-dev")
        await store.save_session(record)
        result = await store.get_session("tmpl_test")
        assert result is not None
        assert result.template == "python-dev"


# ---------------------------------------------------------------------------
# Event Log
# ---------------------------------------------------------------------------


class TestEventLog:
    async def test_append_and_get(self, store: SqliteStore) -> None:
        event = Event(type="test_event", payload={"msg": "hello"})
        await store.append_event(event)
        events = await store.get_events(EventFilter())
        assert len(events) == 1
        assert events[0].type == "test_event"

    async def test_search_events(self, store: SqliteStore) -> None:
        await store.append_event(Event(type="a", payload={"text": "find me"}))
        await store.append_event(Event(type="b", payload={"text": "ignore"}))
        results = await store.search_events("find me")
        assert len(results) == 1
        assert results[0].type == "a"

    async def test_event_bus_accessible(self, store: SqliteStore) -> None:
        """Store exposes the event bus for direct subscription."""
        received: list[Event] = []
        await store.event_bus.subscribe(EventFilter(), received.append)
        await store.append_event(Event(type="bus_test"))
        assert len(received) == 1


class TestPendingInputs:
    async def test_save_list_delete_pending_input(self, store: SqliteStore) -> None:
        await store.save_pending_input(
            request_id="req-1",
            adapter_name="asker",
            session_id="s1",
            spec={"name": "body", "prompt": "Body", "input_type": "text"},
            args={"subcommand": "send"},
            ctx_env={"foo": "bar"},
            input_stream={
                "content": "",
                "declared_type": "text/plain",
                "detected_type": None,
                "source": "",
                "size_bytes": 0,
                "encoding": "utf-8",
            },
            created_at="2026-04-03T00:00:00+00:00",
        )
        pending = await store.list_pending_inputs()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "req-1"
        assert pending[0]["adapter_name"] == "asker"

        await store.delete_pending_input("req-1")
        assert await store.list_pending_inputs() == []
