"""Watchdog adapter — Event-driven automation for Chaitya Core.

Daemon-pattern adapter. Each watchdog runs in its own tmux session,
subscribes to the event bus, and dispatches adapter actions when
matching events arrive.

Usage:
  chaitya watchdog start --for <event-type> [--if-pattern <regex>] [--do <adapter> <subcommand> [--arg val...]]
  chaitya watchdog list
  chaitya watchdog stop <watchdog-id>

The daemon is started via SessionRunner. It writes a small Python bootstrap
to /tmp/watchdog-{id}.py and runs it. The bootstrap imports the daemon
module from the SDK path and runs the main loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from chaitya_sdk import ChaityaStream, SessionContext, adapter, event_bus
from chaitya_sdk.session import SessionRunner
from chaitya_sdk.types import Event


_WATCHDOG_SESSION_PREFIX = "watchdog-"
_BOOTSTRAP_TEMPLATE = r"""
import asyncio
import json
import re
import sys

sys.path.insert(0, "{sdk_path}")

from chaitya_sdk import event_bus
from chaitya_sdk.types import Event

PATTERN = {pattern_repr}
ADAPTER = "{adapter}"
EVENT_TYPE = "{event_type}"
WATCHDOG_ID = "{watchdog_id}"

async def main():
    triggered_count = 0
    running = True

    async def on_event(ev: Event) -> None:
        global triggered_count, running
        if not running:
            return
        if ev.type != EVENT_TYPE:
            return

        payload_str = json.dumps(ev.payload or {})
        if PATTERN and not PATTERN.search(payload_str):
            return

        triggered_count += 1
        print(f"[watchdog {WATCHDOG_ID}] matched: {{ev.type}} (trigger #{triggered_count})", file=sys.stderr)
        sys.stderr.flush()

        await event_bus.emit(Event(
            type="watchdog.triggered",
            source_adapter="watchdog-daemon",
            payload={{
                "watchdog_id": WATCHDOG_ID,
                "matched_event_type": ev.type,
                "matched_event_id": ev.event_id,
                "adapter": ADAPTER,
                "trigger_count": triggered_count,
            }},
        ))

    await event_bus.subscribe(on_event, event_types=[EVENT_TYPE])
    print("watchdog ready", flush=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
"""

_COMPLETION_PATTERNS = [
    re.compile(r"watchdog ready"),
    re.compile(r"muku@"),
    re.compile(r"\[exit:"),
    re.compile(r"\$ "),
]


_WATCHDOG_CONTRACT = {
    "name": "watchdog",
    "description": (
        "Event-driven automation: watch the event bus and dispatch "
        "adapter actions when conditions match. Runs as persistent "
        "daemon(s) in tmux sessions."
    ),
    "commands": [
        {
            "name": "start",
            "description": "Start a watchdog daemon. Waits for matching events on the event bus.",
            "params": [
                {
                    "name": "for",
                    "required": True,
                    "description": "Event type to watch for (e.g. browser.error, session.created)",
                },
                {
                    "name": "if-pattern",
                    "required": False,
                    "description": "Regex pattern matched against event payload JSON text",
                },
                {
                    "name": "do",
                    "required": False,
                    "description": "Adapter name to dispatch when condition matches (future)",
                },
                {
                    "name": "label",
                    "required": False,
                    "description": "Human-readable label for this watchdog",
                },
            ],
            "examples": [
                "chaitya watchdog start --for browser.error --do shell run --command 'echo error!'",
                "chaitya watchdog start --for session.created --if-pattern 'test'",
            ],
        },
        {
            "name": "list",
            "description": "List running watchdog daemons (tmux sessions).",
            "params": [],
            "examples": ["chaitya watchdog list"],
        },
        {
            "name": "stop",
            "description": "Stop a watchdog daemon by its tmux session name.",
            "params": [
                {
                    "name": "id",
                    "required": True,
                    "description": "Watchdog session name (e.g. watchdog-abc12345)",
                },
            ],
            "examples": ["chaitya watchdog stop watchdog-abc12345"],
        },
    ],
    "permissions": {
        "fs_read": ["."],
        "fs_write": ["/tmp"],
        "network": False,
        "can_emit_events": True,
    },
}


def _write_daemon_script(
    watchdog_id: str,
    event_type: str,
    pattern: str,
    adapter: str,
) -> str:
    sdk_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pattern_repr = repr(re.compile(pattern)) if pattern else "None"

    content = _BOOTSTRAP_TEMPLATE.format(
        sdk_path=sdk_path,
        watchdog_id=watchdog_id,
        event_type=event_type,
        pattern_repr=pattern_repr,
        adapter=adapter,
    )
    script_path = f"/tmp/watchdog-{watchdog_id}.py"
    Path(script_path).write_text(content)
    return script_path


_WATCHDOG_HELP = """chaitya watchdog — Event-driven automation daemon.

Usage:
  chaitya watchdog start --for <event-type> [--if-pattern <regex>]
  chaitya watchdog list
  chaitya watchdog stop <session-name>

The daemon subscribes to the event bus. When a matching event arrives,
it emits a watchdog.triggered event with details.

Pipeline example:
  # Watch for browser errors and trigger a screenshot
  chaitya watchdog start --for browser.error
  # Then in another terminal:
  chaitya watch --all --on watchdog.triggered
"""


@adapter(**_WATCHDOG_CONTRACT)  # type: ignore[arg-type]
async def watchdog_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if not sub:
        return _WATCHDOG_HELP.encode(), 0

    if sub == "start":
        event_type = str(ctx.args.get("for", ""))
        pattern = str(ctx.args.get("if-pattern", ""))
        adapter = str(ctx.args.get("do", ""))
        label = str(ctx.args.get("label", ""))

        if not event_type:
            return b"[error] watchdog start: --for <event-type> is required\n", 1

        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                return f"[error] watchdog start: invalid regex: {exc}\n".encode(), 1

        watchdog_id = str(uuid.uuid4())[:8]
        session_name = f"{_WATCHDOG_SESSION_PREFIX}{watchdog_id}"

        script_path = _write_daemon_script(watchdog_id, event_type, pattern, adapter)

        runner = SessionRunner("watchdog", session_name, timeout=10.0)
        try:
            await runner.run(
                f"{sys.executable} {script_path}",
                completion_patterns=_COMPLETION_PATTERNS,
            )
        except Exception as exc:
            return f"[error] watchdog: failed to start daemon: {exc}\n".encode(), 1

        return (
            f"[ok] watchdog '{watchdog_id}' started in tmux session '{session_name}'\n"
            f"  watching event type: {event_type}\n"
            f"  pattern: {pattern or '(any)'}\n"
            f"  action adapter: {adapter or '(emit watchdog.triggered event)'}\n"
            f"  label: {label or '-'}\n"
            f"  monitor with: chaitya watch --session {session_name}\n".encode(),
            0,
        )

    if sub == "list":
        runner = SessionRunner("watchdog", "watchdog-list", timeout=5.0)
        try:
            output, _ = await runner.run(
                "tmux list-sessions 2>/dev/null | grep watchdog- || true",
            )
        except Exception:
            return b"[error] watchdog list: failed to query tmux\n", 1

        sessions = output.strip()
        if not sessions:
            return (
                b"[ok] no watchdog daemons running\n"
                b"  start one with: chaitya watchdog start --for <event-type>\n"
            ), 0

        lines = ["Running watchdog daemons:", sessions]
        return "\n".join(lines).encode(), 0

    if sub == "stop":
        session_name = str(ctx.args.get("id", ""))
        if not session_name:
            return b"[error] watchdog stop: --id <session-name> is required\n", 1

        if not session_name.startswith(_WATCHDOG_SESSION_PREFIX):
            session_name = _WATCHDOG_SESSION_PREFIX + session_name

        runner = SessionRunner("watchdog", session_name, timeout=5.0)
        try:
            await runner.run(
                f"tmux kill-session -t '{session_name}' 2>/dev/null || true",
            )
        except Exception as exc:
            return f"[error] watchdog stop: {exc}\n".encode(), 1

        return f"[ok] watchdog session '{session_name}' stopped\n".encode(), 0

    return (
        f"[error] watchdog: unknown subcommand: {sub}\n"
        f"Available: start, list, stop\n"
        f"Try: chaitya watchdog --help\n".encode(),
        127,
    )


__chaitya_handler__ = watchdog_handler
__adapter_contract__ = asdict(watchdog_handler.__chaitya_contract__)
