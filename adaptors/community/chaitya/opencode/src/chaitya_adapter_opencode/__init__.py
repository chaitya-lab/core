"""OpenCode agent adapter for Chaitya Core.

Uses the kernel's session management (tmux) via SessionRunner.
No HTTP servers, no ACP subprocesses. The kernel handles tmux;
this adapter just calls runner.run().

Commands:
  run     — Send a prompt to an opencode session (creates session if missing)
  session — Thin wrapper around kernel session commands (list/create/kill)
  watch   — Stream Chaitya events to stdout (delegates to kernel watch)
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from typing import Any

from chaitya_sdk import (
    ChaityaStream,
    SessionContext,
    adapter,
    event_bus,
)
from chaitya_sdk.session import SessionRunner
from chaitya_sdk.types import Event

_DEFAULT_SESSION = "opencode-default"
_DEFAULT_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Completion detection patterns
# ---------------------------------------------------------------------------

_COMPLETION_PATTERNS = [
    re.compile(r"muku@"),
    re.compile(r"\[exit:"),
    re.compile(r"\$ "),
    re.compile(r">\s*$"),
]


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

    return f"Unknown opencode subcommand: {sub}\n".encode(), 1


# ---------------------------------------------------------------------------
# run — uses SessionRunner
# ---------------------------------------------------------------------------


async def _handle_run(ctx: SessionContext) -> tuple[bytes, int]:
    prompt = str(ctx.args.get("prompt") or "")
    session_name = str(ctx.args.get("session") or _DEFAULT_SESSION)
    timeout = float(ctx.args.get("timeout") or _DEFAULT_TIMEOUT)

    if not prompt:
        return b"opencode run: --prompt is required\n", 1

    await event_bus.emit(
        Event(
            type="opencode.prompt_sent",
            source_adapter="opencode",
            session_id=session_name,
            payload={"prompt": prompt, "session": session_name},
        )
    )

    runner = SessionRunner("opencode", session_name, timeout=timeout)
    output, elapsed = await runner.run(
        f"opencode run '{prompt}'",
        completion_patterns=_COMPLETION_PATTERNS,
    )

    await event_bus.emit(
        Event(
            type="opencode.response_received",
            source_adapter="opencode",
            session_id=session_name,
            payload={
                "prompt": prompt,
                "session": session_name,
                "duration_s": round(elapsed, 2),
                "output_length": len(output),
            },
        )
    )

    return output.encode("utf-8"), 0


# ---------------------------------------------------------------------------
# session — delegates to kernel dispatch
# ---------------------------------------------------------------------------


async def _dispatch(expr: str) -> Any:
    try:
        from chaitya.core import kernel as kmod

        k = getattr(kmod, "_instance", None)
        if k is None:
            return None
        return await k.dispatch(expr)
    except Exception:
        return None


async def _handle_session(ctx: SessionContext) -> tuple[bytes, int]:
    raw_args = ctx.args.get("__raw_args__", [])
    positional = raw_args[0] if raw_args else "list"
    action = str(ctx.args.get("action") or positional)
    session_name = str(ctx.args.get("session") or "")

    if action == "list":
        r = await _dispatch("session list")
        if not r or r.exit_code != 0:
            return b"(no sessions)\n", 0
        return r.processed.encode("utf-8"), 0

    if action == "create":
        name = session_name or _DEFAULT_SESSION
        r = await _dispatch(f"session create {name}")
        ok = r is not None and r.exit_code == 0
        return (f"Session '{name}' created\n" if ok else f"Failed to create '{name}'\n").encode(
            "utf-8"
        ), 0 if ok else 1

    if action == "kill":
        if not session_name:
            return b"Usage: opencode session kill --session <name>\n", 1
        r = await _dispatch(f"session kill {session_name}")
        ok = r is not None and r.exit_code == 0
        return (
            f"Session '{session_name}' killed\n" if ok else f"Failed to kill '{session_name}'\n"
        ).encode("utf-8"), 0 if ok else 1

    if action == "status":
        if not session_name:
            return b"Usage: opencode session status --session <name>\n", 1
        r = await _dispatch(f"session status {session_name}")
        return (r.processed if r else "").encode("utf-8"), 0 if (r and r.exit_code == 0) else 1

    return f"Unknown session action: {action}\n".encode(), 1


# ---------------------------------------------------------------------------
# watch — event stream
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
