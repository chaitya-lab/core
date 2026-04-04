"""OpenCode agent adapter for Chaitya Core.

Uses Chaitya's existing session management (tmux) as the execution substrate.
OpenCode runs as an interactive shell inside a named tmux session.
The adapter wraps session commands to send prompts and read responses.

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
import uuid
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


def _poll_until_done(
    session_name: str,
    last_len: int,
    timeout: float,
) -> tuple[str, float]:
    import time

    start = time.monotonic()
    from chaitya.core.kernel import Kernel

    kernel = Kernel._instance  # type: ignore[attr-defined]
    if kernel is None:
        return "", 0.0

    while time.monotonic() - start < timeout:
        import asyncio

        asyncio.get_event_loop().run_until_complete(asyncio.sleep(_POLL_INTERVAL))
        output = asyncio.get_event_loop().run_until_complete(
            kernel.dispatch(f"session output {session_name}")
        )
        if len(output.processed) > last_len:
            new_text = output.processed[last_len:]
            if _looks_done(new_text):
                return output.processed, time.monotonic() - start

    return "", timeout


def _looks_done(text: str) -> bool:
    patterns = [
        r"muku@",  # shell prompt
        r"\[exit:",  # L2 footer
        r"\$ ",  # bash prompt
    ]
    return any(re.search(p, text) for p in patterns)


# ---------------------------------------------------------------------------
# Chaitya adapter contract
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

    from chaitya.core.kernel import Kernel

    kernel = _get_kernel()
    if kernel is None:
        return b"Kernel not accessible from adapter\n", 1

    raw_args = ctx.env.get("__args__", [])
    positional_prompt = raw_args[0] if raw_args else ""
    if positional_prompt and positional_prompt not in ("list", "create", "kill", "status"):
        prompt = positional_prompt

    try:
        status = await kernel.dispatch(f"session status {session_name}")
        if status.exit_code != 0:
            create = await kernel.dispatch(f"session create {session_name}")
            if create.exit_code != 0:
                return f"Failed to create session {session_name}: {create.processed}\n".encode(), 1
    except Exception:
        create = await kernel.dispatch(f"session create {session_name}")
        if create.exit_code != 0:
            return f"Failed to create session {session_name}: {create.processed}\n".encode(), 1

    output_before = await kernel.dispatch(f"session output {session_name}")
    last_len = len(output_before.processed)

    sent = await kernel.dispatch(
        f'session send-input {session_name} "opencode run {prompt}" --newline'
    )
    if sent.exit_code != 0:
        return f"Failed to send prompt: {sent.processed}\n".encode(), 1

    await event_bus.emit(
        Event(
            type="opencode.prompt_sent",
            source_adapter="opencode",
            session_id=session_name,
            payload={"prompt": prompt, "session": session_name},
        )
    )

    result, elapsed = await _poll_output(kernel, session_name, last_len, timeout)

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

    return result.encode("utf-8"), 0


async def _poll_output(
    kernel: Any,
    session_name: str,
    last_len: int,
    timeout: float,
) -> tuple[str, float]:
    import time

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        await asyncio.sleep(_POLL_INTERVAL)
        output = await kernel.dispatch(f"session output {session_name}")
        if len(output.processed) > last_len:
            new_text = output.processed[last_len:]
            if _looks_done(new_text):
                return output.processed, time.monotonic() - start
    return "", timeout


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


async def _handle_session(ctx: SessionContext) -> tuple[bytes, int]:
    raw_args = ctx.args.get("__raw_args__", [])
    positional = raw_args[0] if raw_args else "list"
    action = str(ctx.args.get("action") or positional)
    session_name = str(ctx.args.get("session") or "")

    kernel = _get_kernel()
    if kernel is None:
        return b"Kernel not accessible from adapter\n", 1

    if action == "list":
        result = await kernel.dispatch("session list")
        return result.processed.encode(), result.exit_code

    if action == "create":
        if not session_name:
            session_name = _DEFAULT_SESSION
        result = await kernel.dispatch(f"session create {session_name}")
        return result.processed.encode(), result.exit_code

    if action == "kill":
        if not session_name:
            return b"Usage: opencode session kill --session <name>\n", 1
        result = await kernel.dispatch(f"session kill {session_name}")
        return result.processed.encode(), result.exit_code

    if action == "status":
        if not session_name:
            return b"Usage: opencode session status --session <name>\n", 1
        result = await kernel.dispatch(f"session status {session_name}")
        return result.processed.encode(), result.exit_code

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
        if on_filter and not any(on_filter in t for t in [event.type]):
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
# Kernel reference — adapters use module-level _instance set during boot
# ---------------------------------------------------------------------------


def _get_kernel() -> Any:
    from chaitya.core import kernel as kmod

    return getattr(kmod, "_instance", None)


__chaitya_handler__ = opencode_handler
__adapter_contract__ = asdict(opencode_handler.__chaitya_contract__)
