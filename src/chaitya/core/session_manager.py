"""Session Manager — orchestrates session lifecycle, state, and events.

The SessionManager wraps a SessionBackend implementation, coordinates with
the Store for persistence, and emits session lifecycle events on the EventBus.

It is the kernel's single point of contact for all session operations.
Adapters interact with sessions only through kernel commands; the kernel
routes those commands through this manager.

Reference: PRD §3.3, §5, §11
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from chaitya.core.protocols import EventBusProtocol, SessionBackend, StoreProtocol
from chaitya.core.types import (
    Event,
    EventFilter,
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    SessionState,
    SESSION_CREATED,
    SESSION_KILLED,
    SESSION_STATE_CHANGED,
    SESSION_STUCK,
    SESSION_DEAD,
)

logger = logging.getLogger("chaitya.core.session_manager")


class SessionManager:
    """Manages named running environments.

    Coordinates between the SessionBackend (tmux, local-process, etc.),
    the persistent Store (session records), and the EventBus (lifecycle events).

    Attributes:
        stuck_threshold_seconds: Seconds of silence before emitting stuck event.
    """

    def __init__(
        self,
        backend: SessionBackend,
        store: StoreProtocol,
        event_bus: EventBusProtocol,
        *,
        stuck_threshold_seconds: int = 60,
    ) -> None:
        self._backend = backend
        self._store = store
        self._bus = event_bus
        self._stuck_threshold = stuck_threshold_seconds
        # Track last activity per session for stuck detection
        self._last_activity: dict[str, datetime] = {}
        self._stuck_monitor_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the session manager and restore sessions from store."""
        records = await self._store.list_sessions()
        for record in records:
            # Check if backend still has the session
            if await self._backend.exists(record.name):
                self._last_activity[record.name] = datetime.now(timezone.utc)
                logger.info("Restored session %s (state=%s)", record.name, record.state.value)
            else:
                # Session pane is gone — mark dead
                if record.state != SessionState.DEAD:
                    record.state = SessionState.DEAD
                    await self._store.save_session(record)
                    await self._emit_state_change(record.name, record.state, SessionState.DEAD)
                logger.info("Session %s marked dead (pane gone)", record.name)

        # Start stuck-detection monitor
        self._stuck_monitor_task = asyncio.create_task(
            self._stuck_monitor_loop(), name="session-stuck-monitor"
        )
        logger.info("SessionManager started (%d sessions restored)", len(records))

    async def stop(self) -> None:
        """Stop the session manager."""
        if self._stuck_monitor_task and not self._stuck_monitor_task.done():
            self._stuck_monitor_task.cancel()
            try:
                await self._stuck_monitor_task
            except asyncio.CancelledError:
                pass
        self._stuck_monitor_task = None
        logger.info("SessionManager stopped")

    # ------------------------------------------------------------------
    # Session Operations
    # ------------------------------------------------------------------

    async def create(
        self,
        name: str,
        *,
        identity: SessionIdentity | None = None,
        template: str | None = None,
    ) -> SessionHandle:
        """Create a new named session."""
        if identity is None:
            identity = SessionIdentity()

        handle = await self._backend.create(name, identity)

        record = SessionRecord(
            name=name,
            state=SessionState.IDLE,
            template=template,
            identity=identity,
        )
        await self._store.save_session(record)
        self._last_activity[name] = datetime.now(timezone.utc)

        await self._bus.emit(Event(
            type=SESSION_CREATED,
            source_adapter="kernel",
            session_id=name,
            payload={"template": template, "backend_id": handle.backend_id},
        ))
        logger.info("Session %s created", name)
        return handle

    async def kill(self, name: str) -> None:
        """Terminate a session and its processes."""
        await self._backend.kill(name)
        record = await self._store.get_session(name)
        old_state = record.state if record else SessionState.IDLE
        if record:
            record.state = SessionState.DEAD
            await self._store.save_session(record)
        self._last_activity.pop(name, None)

        await self._bus.emit(Event(
            type=SESSION_KILLED,
            source_adapter="kernel",
            session_id=name,
        ))
        logger.info("Session %s killed", name)

    async def list(self) -> list[SessionRecord]:
        """List all session records."""
        return await self._store.list_sessions()

    async def status(self, name: str) -> SessionRecord | None:
        """Get the current status of a session."""
        return await self._store.get_session(name)

    async def attach(self, name: str) -> None:
        """Attach to an existing session (interactive)."""
        await self._backend.attach(name)

    async def detach(self, name: str) -> None:
        """Detach from a session without killing it."""
        await self._backend.detach(name)

    async def signal(self, name: str, sig: str) -> None:
        """Send a signal to the session's process."""
        await self._backend.signal(name, sig)

    async def send_input(self, name: str, data: bytes) -> None:
        """Send input to a session's stdin.

        Checks session state: if busy, behaviour depends on adapter's
        on_busy_input policy (handled at dispatch level, not here).
        """
        await self._backend.send_input(name, data)
        self._touch(name)

    async def set_env(self, name: str, key: str, value: str) -> None:
        """Set an environment variable in a session."""
        await self._backend.set_env(name, key, value)
        # Update stored identity
        record = await self._store.get_session(name)
        if record:
            record.identity.env_vars[key] = value
            await self._store.save_session(record)

    async def unset_env(self, name: str, key: str) -> None:
        """Remove an environment variable from a session."""
        await self._backend.unset_env(name, key)
        record = await self._store.get_session(name)
        if record:
            record.identity.env_vars.pop(key, None)
            await self._store.save_session(record)

    async def stream_output(self, name: str) -> AsyncIterator[bytes]:
        """Stream output from a session."""
        async for chunk in await self._backend.stream_output(name):
            self._touch(name)
            yield chunk

    async def update_state(self, name: str, new_state: SessionState) -> None:
        """Update a session's state (used by pipeline orchestrator)."""
        record = await self._store.get_session(name)
        if record is None:
            raise ValueError(f"Session {name!r} not found")
        old_state = record.state
        if old_state == new_state:
            return
        record.state = new_state
        record.last_activity = datetime.now(timezone.utc).isoformat()
        await self._store.save_session(record)
        await self._emit_state_change(name, old_state, new_state)
        self._touch(name)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _touch(self, name: str) -> None:
        """Record activity timestamp for stuck detection."""
        self._last_activity[name] = datetime.now(timezone.utc)

    async def _emit_state_change(
        self, name: str, old: SessionState, new: SessionState
    ) -> None:
        """Emit a session_state_changed event."""
        await self._bus.emit(Event(
            type=SESSION_STATE_CHANGED,
            source_adapter="kernel",
            session_id=name,
            payload={"from": old.value, "to": new.value},
        ))

    async def _stuck_monitor_loop(self) -> None:
        """Background loop that detects stuck sessions."""
        while True:
            await asyncio.sleep(10)  # check every 10 seconds
            now = datetime.now(timezone.utc)
            for name, last in list(self._last_activity.items()):
                elapsed = (now - last).total_seconds()
                if elapsed >= self._stuck_threshold:
                    record = await self._store.get_session(name)
                    if record and record.state == SessionState.BUSY:
                        record.state = SessionState.STUCK
                        await self._store.save_session(record)
                        await self._bus.emit(Event(
                            type=SESSION_STUCK,
                            source_adapter="kernel",
                            session_id=name,
                            payload={"idle_seconds": int(elapsed)},
                        ))
                        logger.warning(
                            "Session %s stuck (%ds idle)", name, int(elapsed)
                        )

