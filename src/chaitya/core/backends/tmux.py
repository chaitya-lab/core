"""tmux-backed session backend for macOS and Linux."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import signal
import sys
from collections.abc import AsyncIterator

from chaitya.core.types import SessionHandle, SessionIdentity, SessionRecord, SessionState


class TmuxSessionBackend:
    """Session backend using tmux detached sessions as the PTY substrate."""

    def __init__(self, *, tmux_bin: str = "tmux", poll_interval: float = 0.1) -> None:
        self._tmux_bin = tmux_bin
        self._poll_interval = poll_interval
        self._capture_offsets: dict[str, bytes] = {}
        self._env_vars: dict[str, dict[str, str]] = {}

        if sys.platform == "win32":
            raise RuntimeError("tmux backend is only supported on macOS/Linux")
        if shutil.which(tmux_bin) is None:
            raise RuntimeError("tmux is not installed or not available on PATH")

    async def create(self, name: str, identity: SessionIdentity) -> SessionHandle:
        if await self.exists(name):
            raise ValueError(f"Session {name!r} already exists")

        shell = os.environ.get("SHELL", "/bin/sh")
        cwd = os.path.expanduser(identity.working_dir) if identity.working_dir else os.getcwd()
        args = ["new-session", "-d", "-s", name, "-c", cwd]
        for key, value in identity.env_vars.items():
            args.extend(["-e", f"{key}={value}"])
        args.append(f"exec {shlex.quote(shell)} -i")
        await self._run_tmux(*args)

        self._env_vars[name] = dict(identity.env_vars)
        self._capture_offsets[name] = b""
        return SessionHandle(name=name, backend_id=await self._pane_id(name))

    async def attach(self, name: str) -> None:
        await self._ensure_exists(name)
        proc = await asyncio.create_subprocess_exec(self._tmux_bin, "attach-session", "-t", name)
        await proc.wait()

    async def detach(self, name: str) -> None:
        await self._ensure_exists(name)
        proc = await asyncio.create_subprocess_exec(
            self._tmux_bin,
            "detach-client",
            "-s",
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def kill(self, name: str) -> None:
        await self._ensure_exists(name)
        await self._run_tmux("kill-session", "-t", name)
        self._capture_offsets.pop(name, None)
        self._env_vars.pop(name, None)

    async def signal(self, name: str, sig: str) -> None:
        await self._ensure_exists(name)
        pid = int(await self._run_tmux_capture("display-message", "-p", "-t", name, "#{pane_pid}"))
        try:
            sig_num = getattr(signal, sig)
        except AttributeError as exc:
            raise ValueError(f"Unknown signal: {sig}") from exc
        try:
            os.killpg(os.getpgid(pid), sig_num)
        except Exception:
            os.kill(pid, sig_num)

    async def list(self) -> list[SessionRecord]:
        proc = await asyncio.create_subprocess_exec(
            self._tmux_bin,
            "list-sessions",
            "-F",
            "#{session_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            text = stderr.decode("utf-8", errors="replace").strip().lower()
            if "no server running" in text:
                return []
            raise ValueError(text or "failed to list tmux sessions")

        records: list[SessionRecord] = []
        for raw_name in stdout.decode("utf-8", errors="replace").splitlines():
            name = raw_name.strip()
            if not name:
                continue
            records.append(
                SessionRecord(
                    name=name,
                    state=SessionState.IDLE,
                    identity=SessionIdentity(env_vars=dict(self._env_vars.get(name, {}))),
                )
            )
        return records

    async def exists(self, name: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            self._tmux_bin,
            "has-session",
            "-t",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait() == 0

    async def stream_output(self, name: str) -> AsyncIterator[bytes]:
        await self._ensure_exists(name)
        while await self.exists(name):
            snapshot = await self._capture_pane(name)
            previous = self._capture_offsets.get(name, b"")
            delta = snapshot[len(previous):] if snapshot.startswith(previous) else snapshot
            self._capture_offsets[name] = snapshot
            if delta:
                yield delta
            await asyncio.sleep(self._poll_interval)

    async def send_input(self, name: str, data: bytes) -> None:
        await self._ensure_exists(name)
        text = data.decode("utf-8", errors="replace")
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if part:
                await self._run_tmux("send-keys", "-t", name, "-l", part)
            if index < len(parts) - 1:
                await self._run_tmux("send-keys", "-t", name, "Enter")

    async def set_env(self, name: str, key: str, value: str) -> None:
        await self._ensure_exists(name)
        await self._run_tmux("set-environment", "-t", name, key, value)
        await self.send_input(name, f"export {key}={shlex.quote(value)}\n".encode("utf-8"))
        self._env_vars.setdefault(name, {})[key] = value

    async def unset_env(self, name: str, key: str) -> None:
        await self._ensure_exists(name)
        await self._run_tmux("set-environment", "-u", "-t", name, key)
        await self.send_input(name, f"unset {key}\n".encode("utf-8"))
        self._env_vars.setdefault(name, {}).pop(key, None)

    async def _ensure_exists(self, name: str) -> None:
        if not await self.exists(name):
            raise ValueError(f"Session {name!r} not found")

    async def _pane_id(self, name: str) -> str:
        return await self._run_tmux_capture("display-message", "-p", "-t", name, "#{session_name}:#{window_index}.#{pane_index}")

    async def _capture_pane(self, name: str) -> bytes:
        return (
            await self._run_tmux_capture("capture-pane", "-p", "-t", name, "-S", "-")
        ).encode("utf-8")

    async def _run_tmux(self, *args: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self._tmux_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip() or stdout.decode(
                "utf-8", errors="replace"
            ).strip()
            raise ValueError(error or f"tmux command failed: {' '.join(args)}")

    async def _run_tmux_capture(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            self._tmux_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(error or f"tmux command failed: {' '.join(args)}")
        return stdout.decode("utf-8", errors="replace").rstrip("\n")
