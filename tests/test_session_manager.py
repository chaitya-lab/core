"""Tests for chaitya.core.session_manager using the appropriate session backend.

Reference: PRD §3.3
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from chaitya.core.backends.psmux import PsmuxBackend
from chaitya.core.backends.tmux import TmuxSessionBackend
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


def _backend_available() -> bool:
    if os.name == "nt":
        return shutil.which("psmux") is not None
    return shutil.which("tmux") is not None


def _make_backend() -> SessionBackend:
    if os.name == "nt":
        return PsmuxBackend()
    return TmuxSessionBackend()


# Skip all tests if the appropriate backend is not available
pytestmark = pytest.mark.skipif(
    not _backend_available(),
    reason="session backend not available",
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
def backend() -> SessionBackend:
    return _make_backend()


@pytest.fixture
async def bus() -> SqliteEventBus:
    b = SqliteEventBus(db_path=":memory:")
    await b.open()
    yield b  # type: ignore[misc]
    await b.close()


@pytest.fixture
async def manager(
    backend: TmuxSessionBackend, store: SqliteStore, bus: SqliteEventBus
) -> SessionManager:
    mgr = SessionManager(backend, store, bus, stuck_threshold_seconds=60)
    await mgr.start()
    yield mgr  # type: ignore[misc]
    try:
        for record in await mgr.list():
            try:
                await backend.kill(record.name)
            except Exception:
                pass
        await mgr.stop()
    except Exception:
        pass


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

    async def test_create_emits_event(self, manager: SessionManager, bus: SqliteEventBus) -> None:
        received: list[Event] = []
        await bus.subscribe(EventFilter(event_types=[SESSION_CREATED]), received.append)
        await manager.create("ev-test")
        assert len(received) == 1
        assert received[0].session_id == "ev-test"

    async def test_kill_session(self, manager: SessionManager) -> None:
        await manager.create("to-kill")
        await manager.kill("to-kill")
        record = await manager.status("to-kill")
        assert record is not None
        assert record.state == SessionState.DEAD

    async def test_kill_emits_event(self, manager: SessionManager, bus: SqliteEventBus) -> None:
        received: list[Event] = []
        await bus.subscribe(EventFilter(event_types=[SESSION_KILLED]), received.append)
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
        await bus.subscribe(EventFilter(event_types=[SESSION_STATE_CHANGED]), received.append)
        await manager.create("sc-test")
        await manager.update_state("sc-test", SessionState.BUSY)
        assert len(received) == 1
        assert received[0].payload["from"] == "idle"
        assert received[0].payload["to"] == "busy"

    async def test_update_state_same_is_noop(
        self, manager: SessionManager, bus: SqliteEventBus
    ) -> None:
        received: list[Event] = []
        await bus.subscribe(EventFilter(event_types=[SESSION_STATE_CHANGED]), received.append)
        await manager.create("noop-test")
        await manager.update_state("noop-test", SessionState.IDLE)  # same as current
        assert len(received) == 0

    async def test_update_state_nonexistent_raises(self, manager: SessionManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            await manager.update_state("nonexistent", SessionState.BUSY)

    async def test_set_env_persisted(self, manager: SessionManager, store: SqliteStore) -> None:
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
        # Pre-populate store with a session record (no actual tmux pane)
        await store.save_session(SessionRecord(name="ghost", state=SessionState.IDLE))
        # Fresh backend (no sessions) + fresh manager
        backend = _make_backend()
        mgr = SessionManager(backend, store, bus)
        await mgr.start()
        record = await store.get_session("ghost")
        assert record is not None
        assert record.state == SessionState.DEAD
        await mgr.stop()


class TestTemplateFields:
    """Tests for template fields: git_worktree, health_check_interval."""

    async def test_parse_duration_seconds(self, manager: SessionManager) -> None:
        """_parse_duration handles seconds."""
        assert manager._parse_duration("60") == 60
        assert manager._parse_duration("120") == 120

    async def test_parse_duration_with_suffix(self, manager: SessionManager) -> None:
        """_parse_duration handles s/m/h suffixes."""
        assert manager._parse_duration("60s") == 60
        assert manager._parse_duration("2m") == 120
        assert manager._parse_duration("1h") == 3600

    async def test_parse_duration_int(self, manager: SessionManager) -> None:
        """_parse_duration handles int values."""
        assert manager._parse_duration(45) == 45

    async def test_identity_from_template_git_worktree(
        self, store: SqliteStore, bus: SqliteEventBus, tmp_path: Path
    ) -> None:
        """Template git_worktree field is extracted into SessionIdentity."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "git-test.yaml"
        template_file.write_text(
            "identity:\n  working_dir: /tmp\n\ngit_worktree: true\n"
        )

        mgr = SessionManager(_make_backend(), store, bus, templates_dir=str(templates_dir))
        await mgr.start()
        try:
            await mgr.create("git-worktree-test", template="git-test")
            record = await mgr.status("git-worktree-test")
            assert record is not None
            assert record.identity.git_worktree is True
        finally:
            await mgr.stop()

    async def test_health_check_interval_loaded(
        self, store: SqliteStore, bus: SqliteEventBus, tmp_path: Path
    ) -> None:
        """Template health_check_interval is stored in session manager."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        template_file = templates_dir / "health.yaml"
        template_file.write_text(
            "identity:\n  working_dir: /tmp\n\ngit_worktree: false\nhealth_check_interval: 30s\n"
        )

        mgr = SessionManager(_make_backend(), store, bus, templates_dir=str(templates_dir))
        await mgr.start()
        try:
            await mgr.create("health-test", template="health")
            assert mgr._template_health_check.get("health-test") == 30
        finally:
            await mgr.stop()
