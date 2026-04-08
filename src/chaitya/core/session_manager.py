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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chaitya.core.protocols import EventBusProtocol, SessionBackend, StoreProtocol
from chaitya.core.types import (
    SESSION_CREATED,
    SESSION_INPUT_RECEIVED,
    SESSION_KILLED,
    SESSION_STATE_CHANGED,
    SESSION_STUCK,
    Event,
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    SessionState,
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
        templates_dir: str | None = None,
    ) -> None:
        self._backend = backend
        self._store = store
        self._bus = event_bus
        self._stuck_threshold = stuck_threshold_seconds
        self._templates_dir = templates_dir
        self._last_activity: dict[str, datetime] = {}
        self._stuck_monitor_task: asyncio.Task[None] | None = None
        self._template_auto_restart: dict[str, bool] = {}
        self._template_startup_cmd: dict[str, str | None] = {}
        self._template_git_worktree: dict[str, bool] = {}
        self._template_health_check: dict[str, int] = {}
        self._suspended_commands: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the session manager and restore sessions from store.

        PRD §3.3: sessions with ``auto_restart_on_kernel_start: true``
        in their template are recreated on startup.
        """
        records = await self._store.list_sessions()
        restored = 0
        for record in records:
            # Check if backend still has the session
            if await self._backend.exists(record.name):
                self._last_activity[record.name] = datetime.now(UTC)
                restored += 1
                logger.info("Restored session %s (state=%s)", record.name, record.state.value)
            else:
                template_data: dict[str, Any] = {}
                auto_restart = False
                if record.template:
                    try:
                        template_data = await self._load_template(record.template)
                        auto_restart = bool(
                            template_data.get("auto_restart_on_kernel_start", False)
                        )
                        self._template_auto_restart[record.name] = auto_restart
                        self._template_startup_cmd[record.name] = template_data.get(
                            "startup_command"
                        )
                        self._template_git_worktree[record.name] = template_data.get("git_worktree", False)
                        health_check = template_data.get("health_check_interval")
                        if health_check:
                            self._template_health_check[record.name] = self._parse_duration(health_check)
                    except Exception as exc:
                        logger.warning(
                            "Failed to load template %r during restore: %s",
                            record.template,
                            exc,
                        )
                if auto_restart and record.template:
                    try:
                        identity = self._identity_from_template(template_data)
                        await self._backend.create(record.name, identity)
                        self._last_activity[record.name] = datetime.now(UTC)
                        record.state = SessionState.IDLE
                        await self._store.save_session(record)
                        logger.info(
                            "Auto-restarted session %s from template %s",
                            record.name,
                            record.template,
                        )
                    except Exception as exc:
                        logger.warning("Failed to auto-restart session %s: %s", record.name, exc)
                        await self._mark_dead(record.name)
                else:
                    if record.state != SessionState.DEAD:
                        await self._mark_dead(record.name)
                    logger.info("Session %s marked dead (pane gone)", record.name)

        # Start stuck-detection monitor
        self._stuck_monitor_task = asyncio.create_task(
            self._stuck_monitor_loop(), name="session-stuck-monitor"
        )
        logger.info("SessionManager started (%d/%d sessions alive)", restored, len(records))

    async def stop(self) -> None:
        """Stop the session manager and clean up all sessions."""
        if self._stuck_monitor_task and not self._stuck_monitor_task.done():
            self._stuck_monitor_task.cancel()
            try:
                await self._stuck_monitor_task
            except asyncio.CancelledError:
                pass
        self._stuck_monitor_task = None
        for record in await self.list():
            try:
                await self._backend.kill(record.name)
            except Exception:
                pass
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
        """Create a new named session.

        If ``template`` is provided, loads the template YAML and applies
        its identity settings (env vars, working_dir, browser_profile).
        """
        if identity is None:
            if template:
                try:
                    template_data = await self._load_template(template)
                    identity = self._identity_from_template(template_data)
                    self._template_auto_restart[name] = template_data.get(
                        "auto_restart_on_kernel_start", False
                    )
                    self._template_startup_cmd[name] = template_data.get("startup_command")
                    self._template_git_worktree[name] = template_data.get("git_worktree", False)
                    health_check = template_data.get("health_check_interval")
                    if health_check:
                        self._template_health_check[name] = self._parse_duration(health_check)
                except Exception as exc:
                    logger.warning("Failed to load template %r: %s — using defaults", template, exc)
                    identity = SessionIdentity()
            else:
                identity = SessionIdentity()

        handle = await self._backend.create(name, identity)

        record = SessionRecord(
            name=name,
            state=SessionState.IDLE,
            template=template,
            identity=identity,
            exec_mode="enabled",
        )
        await self._store.save_session(record)
        self._last_activity[name] = datetime.now(UTC)

        await self._bus.emit(
            Event(
                type=SESSION_CREATED,
                source_adapter="kernel",
                session_id=name,
                payload={"template": template, "backend_id": handle.backend_id},
            )
        )
        logger.info("Session %s created", name)

        startup_cmd = self._template_startup_cmd.get(name)
        if startup_cmd:
            try:
                logger.info("Running startup command for session %s: %s", name, startup_cmd)
                data = f"{startup_cmd}\n".encode()
                await self._backend.send_input(name, data)
            except Exception as exc:
                logger.warning(
                    "Session %s startup command failed: %s",
                    name,
                    exc,
                )

        return handle

    async def get_exec_mode(self, name: str) -> str:
        """Return the exec gate mode for a session.

        Returns 'enabled' | 'disabled' | 'readonly'.
        Defaults to 'enabled' if session does not exist (allows session management).
        Defaults to 'enabled' if session record has no exec_mode set.
        """
        record = await self._store.get_session(name)
        if record is None:
            return "enabled"
        return record.exec_mode or "enabled"

    async def set_exec_mode(self, name: str, mode: str) -> bool:
        """Set the exec gate mode for a session.

        Args:
            mode: 'enabled' | 'disabled' | 'readonly'

        Returns True on success, False if session not found.
        """
        if mode not in ("enabled", "disabled", "readonly"):
            raise ValueError(
                f"Invalid exec_mode: {mode!r}. Must be 'enabled', 'disabled', or 'readonly'."
            )
        record = await self._store.get_session(name)
        if record is None:
            return False
        record.exec_mode = mode
        await self._store.save_session(record)
        logger.info("Session %s exec_mode set to %s", name, mode)
        return True

    async def kill(self, name: str) -> None:
        """Terminate a session and its processes."""
        await self._backend.kill(name)
        record = await self._store.get_session(name)
        old_state = record.state if record else SessionState.IDLE
        if record:
            record.state = SessionState.DEAD
            await self._store.save_session(record)
        self._last_activity.pop(name, None)

        await self._bus.emit(
            Event(
                type=SESSION_KILLED,
                source_adapter="kernel",
                session_id=name,
            )
        )
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

        Also handles resuming suspended commands if session has one.
        """
        await self._backend.send_input(name, data)
        self._touch(name)
        if self.has_suspended_command(name):
            text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\n", "")
            await self._bus.emit(
                Event(
                    type=SESSION_INPUT_RECEIVED,
                    source_adapter="kernel",
                    session_id=name,
                    payload={"input": text, "raw": data.decode("utf-8", errors="replace")},
                )
            )

    def store_suspended_command(
        self,
        name: str,
        context: dict[str, Any],
    ) -> None:
        """Store suspended command context for a session."""
        self._suspended_commands[name] = context

    def get_suspended_command(self, name: str) -> dict[str, Any] | None:
        """Get and clear suspended command context for a session."""
        return self._suspended_commands.pop(name, None)

    def has_suspended_command(self, name: str) -> bool:
        """Check if session has a suspended command."""
        return name in self._suspended_commands

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
        async for chunk in self._backend.stream_output(name):
            self._touch(name)
            yield chunk

    async def read_output(
        self,
        name: str,
        *,
        idle_timeout_seconds: float = 0.2,
        max_chunks: int = 128,
        max_bytes: int = 5_242_880,
    ) -> bytes:
        """Collect a bounded snapshot of session output.

        Reads from the backend stream until it goes idle for ``idle_timeout_seconds``
        or one of the safety limits is reached.
        """
        iterator = self.stream_output(name).__aiter__()
        chunks: list[bytes] = []
        total_bytes = 0

        while len(chunks) < max_chunks and total_bytes < max_bytes:
            try:
                chunk = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=idle_timeout_seconds,
                )
            except TimeoutError:
                break
            except StopAsyncIteration:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
            if total_bytes >= max_bytes:
                break

        return b"".join(chunks)

    async def update_state(self, name: str, new_state: SessionState) -> None:
        """Update a session's state (used by pipeline orchestrator)."""
        record = await self._store.get_session(name)
        if record is None:
            raise ValueError(f"Session {name!r} not found")
        old_state = record.state
        if old_state == new_state:
            return
        record.state = new_state
        record.last_activity = datetime.now(UTC).isoformat()
        await self._store.save_session(record)
        await self._emit_state_change(name, old_state, new_state)
        self._touch(name)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _touch(self, name: str) -> None:
        """Record activity timestamp for stuck detection."""
        self._last_activity[name] = datetime.now(UTC)

    async def _load_template(self, name: str) -> dict[str, Any]:
        """Load a template YAML file (PRD §11)."""
        if not self._templates_dir:
            return {}
        template_path = Path(self._templates_dir).expanduser() / f"{name}.yaml"
        if not template_path.is_file():
            raise FileNotFoundError(f"Template not found: {template_path}")
        import yaml

        with open(template_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _identity_from_template(self, data: dict[str, Any]) -> SessionIdentity:
        """Build a SessionIdentity from parsed template data."""
        identity_data = data.get("identity", {})
        env_vars = dict(identity_data.get("env_vars", {}))
        for key, val in env_vars.items():
            if isinstance(val, str) and val.startswith("${vault:"):
                pass
        return SessionIdentity(
            env_vars=env_vars,
            working_dir=identity_data.get("working_dir"),
            browser_profile=identity_data.get("browser_profile"),
            git_worktree=data.get("git_worktree", False),
        )

    def _parse_duration(self, value: str | int) -> int:
        """Parse duration string like '60s' to seconds (int)."""
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            value = value.strip().lower()
            if value.endswith("s"):
                return int(value[:-1])
            elif value.endswith("m"):
                return int(value[:-1]) * 60
            elif value.endswith("h"):
                return int(value[:-1]) * 3600
            else:
                try:
                    return int(value)
                except ValueError:
                    return 60
        return 60

    async def _mark_dead(self, name: str) -> None:
        """Mark a session as dead in store."""
        from chaitya.core.types import SESSION_DEAD

        record = await self._store.get_session(name)
        if record:
            old_state = record.state
            record.state = SessionState.DEAD
            await self._store.save_session(record)
            await self._emit_state_change(name, old_state, SessionState.DEAD)
            await self._bus.emit(
                Event(
                    type=SESSION_DEAD,
                    source_adapter="kernel",
                    session_id=name,
                )
            )

    async def _emit_state_change(self, name: str, old: SessionState, new: SessionState) -> None:
        """Emit a session_state_changed event."""
        await self._bus.emit(
            Event(
                type=SESSION_STATE_CHANGED,
                source_adapter="kernel",
                session_id=name,
                payload={"from": old.value, "to": new.value},
            )
        )

    async def _stuck_monitor_loop(self) -> None:
        """Background loop that detects stuck sessions and performs health checks.

        Verifies the session still exists in the backend before marking it stuck.
        If the backend pane is gone (e.g. tmux killed externally), marks DEAD instead.

        For sessions with health_check_interval, also performs periodic health checks.
        """
        HEALTH_CHECK_COUNTER: dict[str, int] = {}
        while True:
            await asyncio.sleep(10)  # check every 10 seconds
            now = datetime.now(UTC)
            for name, last in list(self._last_activity.items()):
                elapsed = (now - last).total_seconds()

                health_interval = self._template_health_check.get(name)
                if health_interval:
                    HEALTH_CHECK_COUNTER.setdefault(name, 0)
                    HEALTH_CHECK_COUNTER[name] += 10
                    if HEALTH_CHECK_COUNTER[name] >= health_interval:
                        HEALTH_CHECK_COUNTER[name] = 0
                        await self._perform_health_check(name)

                if elapsed >= self._stuck_threshold:
                    record = await self._store.get_session(name)
                    if record and record.state == SessionState.BUSY:
                        try:
                            exists = await self._backend.exists(name)
                        except Exception:
                            exists = False
                        if not exists:
                            await self._mark_dead(name)
                            logger.warning(
                                "Session %s marked dead (backend pane gone after %ds idle)",
                                name,
                                int(elapsed),
                            )
                        else:
                            record.state = SessionState.STUCK
                            await self._store.save_session(record)
                            await self._bus.emit(
                                Event(
                                    type=SESSION_STUCK,
                                    source_adapter="kernel",
                                    session_id=name,
                                    payload={"idle_seconds": int(elapsed)},
                                )
                            )
                            logger.warning("Session %s stuck (%ds idle)", name, int(elapsed))

    async def _perform_health_check(self, name: str) -> None:
        """Perform a health check on a session.

        Emits session_health_check event. Template can define on_stuck behavior.
        """
        record = await self._store.get_session(name)
        if not record or record.state == SessionState.DEAD:
            return

        health_ok = True
        try:
            exists = await self._backend.exists(name)
            if not exists:
                health_ok = False
        except Exception:
            health_ok = False

        await self._bus.emit(
            Event(
                type="session_health_check",
                source_adapter="kernel",
                session_id=name,
                payload={
                    "healthy": health_ok,
                    "state": record.state.value,
                    "template": record.template,
                },
            )
        )
