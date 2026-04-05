"""OpenCode agent adapter for Chaitya Core.

Uses Chaitya's existing session management (tmux) as the execution substrate.
OpenCode runs as an interactive shell inside a named tmux session.
The adapter sends prompts and reads responses via kernel session commands.

No HTTP servers, no ACP subprocesses. The kernel's session infrastructure
(tmux) handles PTY allocation, I/O streaming, and session state.

Commands:
  run     — Send a prompt to an opencode session (creates session if missing)
  session — Thin wrapper around kernel session commands (list/create/kill)
  watch   — Stream Chaitya events to stdout (delegates to kernel watch)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict
from typing import Any

from chaitya_sdk import (
    ChaityaStream,
    SessionContext,
    adapter,
    event_bus,
)
from chaitya_sdk.types import Event


_DEFAULT_SESSION = "opencode-default"
_POLL_INTERVAL = 0.5
_DEFAULT_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Session command helper
# ---------------------------------------------------------------------------


class _SessionHelper:
    """Thin wrapper around kernel session commands for the opencode adapter.

    Adapters must use kernel session commands (not direct subprocess calls)
    to interact with tmux sessions. This helper provides a clean interface.
    """

    def __init__(self) -> None:
        self._kernel: Any = None

    def _get_kernel(self) -> Any:
        if self._kernel is None:
            from chaitya.core import kernel as kmod

            self._kernel = getattr(kmod, "_instance", None)
        return self._kernel

    async def _dispatch(self, expr: str) -> Any:
        k = self._get_kernel()
        if k is None:
            return None
        return await k.dispatch(expr)

    async def status(self, name: str) -> tuple[bool, str]:
        r = await self._dispatch(f"session status {name}")
        return r is not None and r.exit_code == 0, r.processed if r else ""

    async def create(self, name: str) -> bool:
        r = await self._dispatch(f"session create {name}")
        return r is not None and r.exit_code == 0

    async def output(self, name: str) -> str:
        r = await self._dispatch(f"session output {name}")
        return r.processed if r else ""

    async def send_input(self, name: str, text: str, newline: bool = True) -> bool:
        text_with_nl = text + ("\n" if newline else "")
        try:
            kernel = self._get_kernel()
            if kernel is None:
                return False
            await kernel._session_mgr.send_input(name, text_with_nl.encode("utf-8"))
            return True
        except Exception:
            return False

    async def list_sessions(self) -> list[dict[str, str]]:
        r = await self._dispatch("session list")
        if not r or r.exit_code != 0:
            return []
        sessions = []
        for line in r.processed.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                sessions.append({"name": parts[0], "state": parts[1] if len(parts) > 1 else ""})
        return sessions

    async def kill(self, name: str) -> bool:
        r = await self._dispatch(f"session kill {name}")
        return r is not None and r.exit_code == 0


_session = _SessionHelper()


# ---------------------------------------------------------------------------
# Output detection patterns
# ---------------------------------------------------------------------------

_SHELL_PROMPT_PATTERNS = [
    re.compile(r"muku@"),
    re.compile(r"\[exit:"),
    re.compile(r"\$ "),
    re.compile(r">\s*$"),
]


def _looks_done(text: str) -> bool:
    """Return True if text contains a shell prompt or L2 footer."""
    return any(p.search(text) for p in _SHELL_PROMPT_PATTERNS)


def _extract_output(full_output: str, last_len: int) -> str:
    """Extract the new output since last_len."""
    return full_output[last_len:] if last_len < len(full_output) else ""


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

_opencode_contract = {
    "name": "opencode",
    "description": "Interactive AI coding agent powered by OpenCode CLI, via tmux sessions.",
    "commands": [
        {
            "name": "run",
            "description": "Send a prompt to an opencode session. Creates the session if missing.",
            "params": [
                {"name": "prompt", "required": True, "description": "Prompt to send to OpenCode"},
                {
                    "name": "session",
                    "required": False,
                    "description": "Session name (default: opencode-default)",
                },
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Timeout in seconds (default 300)",
                },
            ],
            "examples": [
                'chaitya opencode run --prompt "Explain async/await in Python"',
                'chaitya opencode run --session dev --prompt "Fix the bug"',
            ],
        },
        {
            "name": "session",
            "description": "Manage opencode sessions (delegate to kernel session commands).",
            "params": [
                {
                    "name": "action",
                    "required": True,
                    "description": "Action: list, create, kill, status",
                },
                {"name": "session", "required": False, "description": "Session name"},
            ],
            "examples": [
                "chaitya opencode session list",
                "chaitya opencode session create --session dev",
                "chaitya opencode session kill --session dev",
            ],
        },
        {
            "name": "watch",
            "description": "Stream Chaitya events to stdout (delegates to kernel watch).",
            "params": [
                {"name": "session", "required": False, "description": "Filter by session name"},
                {
                    "name": "limit",
                    "required": False,
                    "description": "Max events to return (default 50)",
                },
                {"name": "on", "required": False, "description": "Event type to filter"},
            ],
            "examples": [
                "chaitya opencode watch",
                "chaitya opencode watch --session dev --limit 20",
            ],
        },
    ],
    "permissions": {
        "fs_read": ["."],
        "fs_write": ["."],
        "network": True,
        "can_emit_events": True,
    },
}


# ---------------------------------------------------------------------------
# Adapter handler
# ---------------------------------------------------------------------------


@adapter(**_opencode_contract)  # type: ignore[arg-type]
async def opencode_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if sub == "run":
        return await _handle_run(ctx)
    if sub == "session":
        return await _handle_session(ctx)
    if sub == "watch":
        return await _handle_watch(ctx)

    return f"Unknown opencode subcommand: {sub}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


async def _handle_run(ctx: SessionContext) -> tuple[bytes, int]:
    prompt = str(ctx.args.get("prompt") or "")
    session_name = str(ctx.args.get("session") or _DEFAULT_SESSION)
    timeout = float(ctx.args.get("timeout") or _DEFAULT_TIMEOUT)

    if not prompt:
        return b"opencode run: --prompt is required\n", 1

    exists, _ = await _session.status(session_name)
    if not exists:
        created = await _session.create(session_name)
        if not created:
            return f"Failed to create session {session_name}\n".encode("utf-8"), 1

    output_before = await _session.output(session_name)
    last_len = len(output_before)

    sent = await _session.send_input(session_name, f"opencode run '{prompt}'")
    if not sent:
        return f"Failed to send prompt to session {session_name}\n".encode("utf-8"), 1

    await event_bus.emit(
        Event(
            type="opencode.prompt_sent",
            source_adapter="opencode",
            session_id=session_name,
            payload={"prompt": prompt, "session": session_name},
        )
    )

    result, elapsed = await _poll_output(session_name, last_len, timeout)

    await event_bus.emit(
        Event(
            type="opencode.response_received",
            source_adapter="opencode",
            session_id=session_name,
            payload={
                "prompt": prompt,
                "session": session_name,
                "duration_s": round(elapsed, 2),
                "output_length": len(result),
            },
        )
    )

    new_output = _extract_output(result, last_len)
    return new_output.encode("utf-8"), 0


async def _poll_output(
    session_name: str,
    last_len: int,
    timeout: float,
) -> tuple[str, float]:
    start = time.monotonic()
    full_output = ""
    while time.monotonic() - start < timeout:
        await asyncio.sleep(_POLL_INTERVAL)
        output = await _session.output(session_name)
        full_output = output
        if len(output) > last_len:
            if _looks_done(output):
                return output, time.monotonic() - start
            last_len = len(output)
    return full_output, timeout


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


async def _handle_session(ctx: SessionContext) -> tuple[bytes, int]:
    raw_args = ctx.args.get("__raw_args__", [])
    positional = raw_args[0] if raw_args else "list"
    action = str(ctx.args.get("action") or positional)
    session_name = str(ctx.args.get("session") or "")

    if action == "list":
        r = await _session.list_sessions()
        lines = [f"{s['name']}  {s['state']}" for s in r]
        output = "\n".join(lines) + "\n" if lines else "(no sessions)\n"
        return output.encode("utf-8"), 0

    if action == "create":
        name = session_name or _DEFAULT_SESSION
        ok = await _session.create(name)
        return (f"Session '{name}' created\n" if ok else f"Failed to create '{name}'\n").encode(
            "utf-8"
        ), 0 if ok else 1

    if action == "kill":
        if not session_name:
            return b"Usage: opencode session kill --session <name>\n", 1
        ok = await _session.kill(session_name)
        return (
            f"Session '{session_name}' killed\n" if ok else f"Failed to kill '{session_name}'\n"
        ).encode("utf-8"), 0 if ok else 1

    if action == "status":
        if not session_name:
            return b"Usage: opencode session status --session <name>\n", 1
        ok, output = await _session.status(session_name)
        return output.encode("utf-8"), 0 if ok else 1

    return f"Unknown session action: {action}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


async def _handle_watch(ctx: SessionContext) -> tuple[bytes, int]:
    session_filter = str(ctx.args.get("session") or "")
    limit = int(ctx.args.get("limit") or 50)
    on_filter = str(ctx.args.get("on") or "")

    collected: list[str] = []
    count = 0
    lock = asyncio.Lock()

    async def collector(event: Event) -> None:
        nonlocal count
        if count >= limit:
            return
        if session_filter and event.session_id != session_filter:
            return
        if on_filter and on_filter not in event.type:
            return
        line = json.dumps({"type": event.type, "payload": event.payload}, default=str)
        async with lock:
            collected.append(line)
            count += 1

    sub_id = await event_bus.subscribe(
        collector,
        event_types=[],
    )
    try:
        await asyncio.sleep(10)
    finally:
        await event_bus.unsubscribe(sub_id)

    output = "\n".join(collected) + "\n" if collected else ""
    return output.encode("utf-8"), 0


# ---------------------------------------------------------------------------
# Module exports (Chaitya SDK contract)
# ---------------------------------------------------------------------------

__chaitya_handler__ = opencode_handler
__adapter_contract__ = asdict(opencode_handler.__chaitya_contract__)
