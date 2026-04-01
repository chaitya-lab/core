"""Local process session backend — platform-independent fallback.

Uses asyncio subprocesses instead of tmux. Suitable for testing and
platforms without tmux (e.g., Windows during development).

This backend creates one subprocess per session. It does NOT provide
PTY allocation or attach/detach — those require tmux or equivalent.

Reference: PRD §3.3, §13
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from typing import Any

from chaitya.core.types import (
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    SessionState,
)

logger = logging.getLogger("chaitya.core.backends.local")


class _LocalSession:
    """Internal representation of a local process session."""

    __slots__ = ("name", "identity", "process", "env", "output_buffer")

    def __init__(
        self, name: str, identity: SessionIdentity, process: asyncio.subprocess.Process
    ) -> None:
        self.name = name
        self.identity = identity
        self.process = process
        self.env: dict[str, str] = dict(identity.env_vars)
        self.output_buffer: bytearray = bytearray()


class LocalProcessBackend:
    """Session backend using local asyncio subprocesses.

    This is a minimal backend for testing and development on platforms
    without tmux. It satisfies the SessionBackend protocol.

    Each session spawns a shell subprocess. On Windows this is cmd.exe,
    on Unix it is /bin/sh.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _LocalSession] = {}

    async def create(self, name: str, identity: SessionIdentity) -> SessionHandle:
        """Create a new session with a shell subprocess."""
        if name in self._sessions:
            raise ValueError(f"Session {name!r} already exists")

        env = dict(os.environ)
        env.update(identity.env_vars)
        if identity.working_dir:
            cwd = os.path.expanduser(identity.working_dir)
        else:
            cwd = None

        shell = "cmd.exe" if sys.platform == "win32" else "/bin/sh"

        process = await asyncio.create_subprocess_exec(
            shell,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=cwd,
        )

        session = _LocalSession(name, identity, process)
        self._sessions[name] = session

        logger.info("Local session %s created (pid=%d)", name, process.pid)
        return SessionHandle(name=name, backend_id=str(process.pid))

    async def attach(self, name: str) -> None:
        """Attach is a no-op for local backend (no PTY)."""
        self._require(name)

    async def detach(self, name: str) -> None:
        """Detach is a no-op for local backend (no PTY)."""
        self._require(name)

    async def kill(self, name: str) -> None:
        """Terminate the session's subprocess."""
        session = self._require(name)
        try:
            session.process.terminate()
            await asyncio.wait_for(session.process.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
        del self._sessions[name]
        logger.info("Local session %s killed", name)

    async def signal(self, name: str, sig: str) -> None:
        """Send a signal to the session's process."""
        session = self._require(name)
        sig_num = getattr(signal, sig, None)
        if sig_num is None:
            # Try common mapping
            sig_map = {
                "SIGTERM": signal.SIGTERM,
                "SIGINT": signal.SIGINT,
            }
            if sys.platform != "win32":
                sig_map["SIGKILL"] = signal.SIGKILL
                sig_map["SIGHUP"] = signal.SIGHUP
            sig_num = sig_map.get(sig)
        if sig_num is not None:
            session.process.send_signal(sig_num)
        else:
            raise ValueError(f"Unknown signal: {sig}")

    async def list(self) -> list[SessionRecord]:
        """List all active sessions."""
        records = []
        for name, session in self._sessions.items():
            state = (
                SessionState.DEAD
                if session.process.returncode is not None
                else SessionState.IDLE
            )
            records.append(SessionRecord(name=name, state=state, identity=session.identity))
        return records

    async def exists(self, name: str) -> bool:
        """Check if a session exists."""
        return name in self._sessions

    async def stream_output(self, name: str) -> AsyncIterator[bytes]:
        """Stream output from the session's stdout."""
        session = self._require(name)
        assert session.process.stdout is not None
        while True:
            chunk = await session.process.stdout.read(4096)
            if not chunk:
                break
            yield chunk

    async def send_input(self, name: str, data: bytes) -> None:
        """Send input to the session's stdin."""
        session = self._require(name)
        if session.process.stdin is not None:
            session.process.stdin.write(data)
            await session.process.stdin.drain()

    async def set_env(self, name: str, key: str, value: str) -> None:
        """Set an environment variable (takes effect on next command)."""
        session = self._require(name)
        session.env[key] = value
        session.identity.env_vars[key] = value

    async def unset_env(self, name: str, key: str) -> None:
        """Remove an environment variable."""
        session = self._require(name)
        session.env.pop(key, None)
        session.identity.env_vars.pop(key, None)

    def _require(self, name: str) -> _LocalSession:
        """Get session or raise."""
        session = self._sessions.get(name)
        if session is None:
            raise ValueError(f"Session {name!r} not found")
        return session

