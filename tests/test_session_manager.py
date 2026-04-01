"""Tests for chaitya.core.session_manager and backends.local."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chaitya.core.backends.local import LocalProcessBackend
from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.protocols import SessionBackend
from chaitya.core.session_manager import SessionManager
from chaitya.core.store import SqliteStore
from chaitya.core.types import (
    Event,
    EventFilter,
    SessionIdentity,
    SessionRecord,
    SessionState,
    SESSION_CREATED,
    SESSION_KILLED,
    SESSION_STATE_CHANGED,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def store() -> SqliteStore:
    s = SqliteStore(db_path=":memory:")
    await s.open()
    yield s  # type: ignore[misc]
    await s.close()


@pytest.fixture
def backend() -> LocalProcessBackend:
    return LocalProcessBackend()


@pytest.fixture
async def bus() -> SqliteEventBus:
    b = SqliteEventBus(db_path=":memory:")
    await b.open()
    yield b  # type: ignore[misc]
    await b.close()


@pytest.fixture
async def manager(
    backend: LocalProcessBackend, store: SqliteStore, bus: SqliteEventBus
) -> SessionManager:
    mgr = SessionManager(backend, store, bus, stuck_threshold_seconds=60)
    await mgr.start()
    yield mgr  # type: ignore[misc]
    # Kill any remaining sessions
    for name in list(backend._sessions):
        try:
            await backend.kill(name)
        except Exception:
            pass
    await mgr.stop()


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_local_backend_is_session_backend(self) -> None:
        assert isinstance(LocalProcessBackend(), SessionBackend)


# ---------------------------------------------------------------------------
# LocalProcessBackend
# ---------------------------------------------------------------------------


class TestLocalProcessBackend:
    async def test_create_and_exists(self, backend: LocalProcessBackend) -> None:
        handle = await backend.create("test-1", SessionIdentity())
        assert handle.name == "test-1"
        assert await backend.exists("test-1")
        assert not await backend.exists("nonexistent")
        # Cleanup
        await backend.kill("test-1")

    async def test_create_duplicate_raises(self, backend: LocalProcessBackend) -> None:
        await backend.create("dup", SessionIdentity())
        with pytest.raises(ValueError, match="already exists"):
            await backend.create("dup", SessionIdentity())
        await backend.kill("dup")

    async def test_kill_removes_session(self, backend: LocalProcessBackend) -> None:
        await backend.create("killme", SessionIdentity())
        await backend.kill("killme")
        assert not await backend.exists("killme")

    async def test_list_sessions(self, backend: LocalProcessBackend) -> None:
        await backend.create("a", SessionIdentity())
        await backend.create("b", SessionIdentity())
        records = await backend.list()
        names = [r.name for r in records]
        assert "a" in names and "b" in names
        await backend.kill("a")
        await backend.kill("b")

    async def test_send_input(self, backend: LocalProcessBackend) -> None:
        await backend.create("input-test", SessionIdentity())
        # Should not raise
        await backend.send_input("input-test", b"echo hello\n")
        await backend.kill("input-test")

    async def test_set_unset_env(self, backend: LocalProcessBackend) -> None:
        await backend.create("env-test", SessionIdentity())
        await backend.set_env("env-test", "FOO", "bar")
        session = backend._sessions["env-test"]
        assert session.env["FOO"] == "bar"
        await backend.unset_env("env-test", "FOO")
        assert "FOO" not in session.env
        await backend.kill("env-test")

    async def test_require_nonexistent_raises(
        self, backend: LocalProcessBackend
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            backend._require("nope")


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    async def test_create_session(self, manager: SessionManager) -> None:
        handle = await manager.create("work")
        assert handle.name == "work"
        record = await manager.status("work")
        assert record is not None
        assert record.state == SessionState.IDLE

    async def test_create_emits_event(
        self, manager: SessionManager, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        await bus.subscribe(
            EventFilter(event_types=[SESSION_CREATED]), received.append
        )
        await manager.create("ev-test")
        assert len(received) == 1
        assert received[0].session_id == "ev-test"

    async def test_kill_session(self, manager: SessionManager) -> None:
        await manager.create("to-kill")
        await manager.kill("to-kill")
        record = await manager.status("to-kill")
        assert record is not None
        assert record.state == SessionState.DEAD

    async def test_kill_emits_event(
        self, manager: SessionManager, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        await bus.subscribe(
            EventFilter(event_types=[SESSION_KILLED]), received.append
        )
        await manager.create("kill-ev")
        await manager.kill("kill-ev")
        assert len(received) == 1
        assert received[0].session_id == "kill-ev"

    async def test_list_sessions(self, manager: SessionManager) -> None:
        await manager.create("s1")
        await manager.create("s2")
        sessions = await manager.list()
        names = [s.name for s in sessions]
        assert "s1" in names and "s2" in names

    async def test_update_state(self, manager: SessionManager) -> None:
        await manager.create("state-test")
        await manager.update_state("state-test", SessionState.BUSY)
        record = await manager.status("state-test")
        assert record is not None
        assert record.state == SessionState.BUSY

    async def test_update_state_emits_event(
        self, manager: SessionManager, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        await bus.subscribe(
            EventFilter(event_types=[SESSION_STATE_CHANGED]), received.append
        )
        await manager.create("sc-test")
        await manager.update_state("sc-test", SessionState.BUSY)
        assert len(received) == 1
        assert received[0].payload["from"] == "idle"
        assert received[0].payload["to"] == "busy"

    async def test_update_state_same_is_noop(
        self, manager: SessionManager, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        await bus.subscribe(
            EventFilter(event_types=[SESSION_STATE_CHANGED]), received.append
        )
        await manager.create("noop-test")
        await manager.update_state("noop-test", SessionState.IDLE)  # same as current
        assert len(received) == 0

    async def test_update_state_nonexistent_raises(
        self, manager: SessionManager
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            await manager.update_state("nonexistent", SessionState.BUSY)

    async def test_set_env_persisted(
        self, manager: SessionManager, store: SqliteStore
    ) -> None:
        await manager.create("env-persist")
        await manager.set_env("env-persist", "MY_KEY", "my_value")
        record = await store.get_session("env-persist")
        assert record is not None
        assert record.identity.env_vars["MY_KEY"] == "my_value"

    async def test_create_with_template(self, manager: SessionManager) -> None:
        await manager.create("tmpl-test", template="python-dev")
        record = await manager.status("tmpl-test")
        assert record is not None
        assert record.template == "python-dev"

    async def test_start_restores_dead_sessions(
        self, store: SqliteStore, bus: SqliteEventBus
    ) -> None:
        """Sessions whose backend pane is gone are marked dead on start."""
        # Pre-populate store with a session record
        await store.save_session(
            SessionRecord(name="ghost", state=SessionState.IDLE)
        )
        # Fresh backend (no sessions) + fresh manager
        backend = LocalProcessBackend()
        mgr = SessionManager(backend, store, bus)
        await mgr.start()
        record = await store.get_session("ghost")
        assert record is not None
        assert record.state == SessionState.DEAD
        await mgr.stop()

