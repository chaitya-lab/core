"""Browser2 adapter — daemon-based browser automation via Playwright.

Architecture (PRD §8):
  - This is the CLI adapter (ephemeral). It emits request events to the
    SQLite event bus and waits for responses from the daemon.
  - The daemon runs persistently in a tmux session, holds Playwright state,
    and responds via the event bus.
  - Both CLI and daemon share the same SQLite event bus.

Usage:
  chaitya browser2 launch
  chaitya browser2 navigate --url https://example.com
  chaitya browser2 screenshot --path /tmp/screen.png
  chaitya browser2 title

Commands:
  launch       — Start the browser daemon in a tmux session
  navigate     — Open a URL
  screenshot   — Take a screenshot
  click        — Click an element
  fill         — Fill an input field
  press        — Press a key
  evaluate     — Execute JavaScript
  inner-text   — Get element text
  title        — Get page title
  url          — Get page URL
  close        — Close the browser and daemon
"""

from __future__ import annotations

import asyncio
import base64
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
from chaitya_sdk.session import SessionRunner
from chaitya_sdk.types import Event

_COMPLETION_PATTERNS = [
    re.compile(r"browser2 daemon ready"),
    re.compile(r"muku@"),
    re.compile(r"\[exit:"),
    re.compile(r"\$ "),
]

_DAEMON_SESSION = "browser2-daemon"

_ACTION_TIMEOUT = 30.0

_browser_contract = {
    "name": "browser2",
    "description": (
        "Browser automation via Playwright using a persistent daemon. "
        "The daemon runs in a tmux session and communicates via event bus."
    ),
    "commands": [
        {
            "name": "launch",
            "description": "Start the browser daemon in a tmux session.",
            "params": [
                {
                    "name": "headless",
                    "required": False,
                    "description": "Run headless (default: false)",
                },
                {
                    "name": "viewport_width",
                    "required": False,
                    "description": "Viewport width (default: 1280)",
                },
                {
                    "name": "viewport_height",
                    "required": False,
                    "description": "Viewport height (default: 800)",
                },
            ],
            "examples": ["chaitya browser2 launch", "chaitya browser2 launch --headless"],
        },
        {
            "name": "navigate",
            "description": "Open a URL in the browser.",
            "params": [
                {"name": "url", "required": True, "description": "URL to open"},
                {
                    "name": "wait_until",
                    "required": False,
                    "description": "Wait until: load, domcontentloaded, networkidle (default: load)",
                },
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Timeout in ms (default: 30000)",
                },
            ],
            "examples": ["chaitya browser2 navigate --url https://example.com"],
        },
        {
            "name": "screenshot",
            "description": "Take a screenshot. Returns base64 or saves to file.",
            "params": [
                {"name": "path", "required": False, "description": "File path to save (PNG)"},
                {
                    "name": "full_page",
                    "required": False,
                    "description": "Capture full scrollable page (default: false)",
                },
            ],
            "examples": [
                "chaitya browser2 screenshot --path /tmp/screen.png",
                "chaitya browser2 screenshot",
            ],
        },
        {
            "name": "click",
            "description": "Click an element by selector.",
            "params": [
                {
                    "name": "selector",
                    "required": True,
                    "description": "CSS selector or text selector",
                },
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Timeout in ms (default: 5000)",
                },
            ],
            "examples": ['chaitya browser2 click --selector "#submit"'],
        },
        {
            "name": "fill",
            "description": "Fill an input field.",
            "params": [
                {"name": "selector", "required": False, "description": "CSS selector"},
                {
                    "name": "label",
                    "required": False,
                    "description": "Label text to locate the input",
                },
                {"name": "name", "required": False, "description": "Input name attribute"},
                {"name": "value", "required": True, "description": "Text value to fill"},
            ],
            "examples": ['chaitya browser2 fill --selector "#q" --value "hello"'],
        },
        {
            "name": "press",
            "description": "Press a key or key combination.",
            "params": [
                {
                    "name": "selector",
                    "required": False,
                    "description": "CSS selector to focus first",
                },
                {"name": "key", "required": True, "description": "Key name (Enter, Escape, etc.)"},
            ],
            "examples": ["chaitya browser2 press --key Enter"],
        },
        {
            "name": "evaluate",
            "description": "Execute JavaScript in the page.",
            "params": [
                {"name": "script", "required": True, "description": "JavaScript code"},
            ],
            "examples": ['chaitya browser2 evaluate --script "document.title"'],
        },
        {
            "name": "inner-text",
            "description": "Get inner text of an element.",
            "params": [
                {"name": "selector", "required": True, "description": "CSS selector"},
            ],
            "examples": ['chaitya browser2 inner-text --selector "h1"'],
        },
        {
            "name": "title",
            "description": "Get the current page title.",
            "params": [],
            "examples": ["chaitya browser2 title"],
        },
        {
            "name": "url",
            "description": "Get the current page URL.",
            "params": [],
            "examples": ["chaitya browser2 url"],
        },
        {
            "name": "close",
            "description": "Close the browser and stop the daemon.",
            "params": [],
            "examples": ["chaitya browser2 close"],
        },
        {
            "name": "console",
            "description": "Get captured console logs from the current page.",
            "params": [
                {
                    "name": "clear",
                    "required": False,
                    "description": "Clear console logs after reading. Default: false",
                },
            ],
            "examples": [
                "chaitya browser2 console",
                "chaitya browser2 console --clear",
            ],
        },
        {
            "name": "wait",
            "description": "Wait for a specified time (useful for testing).",
            "params": [
                {
                    "name": "seconds",
                    "required": False,
                    "description": "Number of seconds to wait. Default: 1",
                },
            ],
            "examples": [
                "chaitya browser2 wait --seconds 2",
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


async def _request_daemon(event_type: str, payload: dict, response_type: str) -> tuple[bytes, int]:
    request_id = str(uuid.uuid4())
    await event_bus.emit(
        Event(
            type=event_type,
            source_adapter="browser2",
            request_id=request_id,
            payload=payload,
        )
    )
    try:
        response = await event_bus.wait_for_response(
            request_id,
            event_types=[response_type],
            timeout=_ACTION_TIMEOUT,
        )
        result = response.payload or {}

        if result.get("error"):
            return f"[error] {result['error']}\n".encode(), 1

        if event_type == "browser2.screenshot_requested" and result.get("base64"):
            b64 = result["base64"]
            if payload.get("path"):
                return f"Screenshot saved to: {payload.get('path')}\n".encode(), 0
            return f"data:image/png;base64,{b64}\n".encode(), 0

        if event_type == "browser2.navigate_requested":
            return (
                f"Navigated to {result.get('url', '')} (status: {result.get('status', 0)})\n".encode(),
                0,
            )

        if event_type == "browser2.title_requested":
            return f"{result.get('title', '')}\n".encode(), 0

        if event_type == "browser2.url_requested":
            return f"{result.get('url', '')}\n".encode(), 0

        if event_type == "browser2.inner_text_requested":
            return f"{result.get('text', '')}\n".encode(), 0

        if event_type == "browser2.evaluate_requested":
            res = result.get("result", "")
            prefix = "> " if res else ""
            return f"{prefix}{res}\n".encode(), 0

        if event_type == "browser2.launch_requested":
            mode = result.get("mode", "unknown")
            vp = result.get("viewport", (0, 0))
            return f"Browser launched in {mode} mode ({vp[0]}x{vp[1]})\n".encode(), 0

        if event_type == "browser2.close_requested":
            return f"Browser closed.\n".encode(), 0

        if event_type == "browser2.console_requested":
            logs = result.get("logs", [])
            if not logs:
                return "No console logs captured.\n".encode(), 0
            return "\n".join(logs).encode() + b"\n", 0

        if event_type == "browser2.wait_requested":
            waited = result.get("waited", 0)
            return f"Waited {waited} second(s).\n".encode(), 0

        return json.dumps(result, default=str).encode() + b"\n", 0

    except asyncio.TimeoutError:
        return (
            f"[error] Timeout waiting for {event_type} response after {_ACTION_TIMEOUT}s\n".encode(),
            1,
        )
    except Exception as exc:
        return f"[error] {exc}\n".encode(), 1


import json


@adapter(**_browser_contract)  # type: ignore[arg-type]
async def browser2_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if sub == "launch":
        runner = SessionRunner("browser2", _DAEMON_SESSION, timeout=10.0)
        try:
            output, _ = await runner.run(
                "python -m chaitya_adapter_browser2.daemon",
                completion_patterns=_COMPLETION_PATTERNS,
            )
        except Exception as exc:
            return f"[error] Failed to start daemon: {exc}\n".encode(), 1
        return f"Daemon started in tmux session '{_DAEMON_SESSION}'\n".encode(), 0

    if sub == "navigate":
        return await _request_daemon(
            "browser2.navigate_requested",
            {
                "url": ctx.args.get("url", ""),
                "wait_until": ctx.args.get("wait_until", "load"),
                "timeout": float(ctx.args.get("timeout") or 30000),
            },
            "browser2.navigate_response",
        )

    if sub == "screenshot":
        return await _request_daemon(
            "browser2.screenshot_requested",
            {
                "path": ctx.args.get("path", ""),
                "full_page": str(ctx.args.get("full_page", "false")).lower()
                in ("true", "1", "yes"),
            },
            "browser2.screenshot_response",
        )

    if sub == "click":
        return await _request_daemon(
            "browser2.click_requested",
            {
                "selector": ctx.args.get("selector", ""),
                "timeout": float(ctx.args.get("timeout") or 5000),
            },
            "browser2.click_response",
        )

    if sub == "fill":
        return await _request_daemon(
            "browser2.fill_requested",
            {
                "selector": ctx.args.get("selector", ""),
                "label": ctx.args.get("label", ""),
                "name": ctx.args.get("name", ""),
                "value": ctx.args.get("value", ""),
            },
            "browser2.fill_response",
        )

    if sub == "press":
        return await _request_daemon(
            "browser2.press_requested",
            {
                "selector": ctx.args.get("selector", ""),
                "key": ctx.args.get("key", ""),
            },
            "browser2.press_response",
        )

    if sub == "evaluate":
        return await _request_daemon(
            "browser2.evaluate_requested",
            {
                "script": ctx.args.get("script", ""),
            },
            "browser2.evaluate_response",
        )

    if sub == "inner-text":
        return await _request_daemon(
            "browser2.inner_text_requested",
            {
                "selector": ctx.args.get("selector", ""),
            },
            "browser2.inner_text_response",
        )

    if sub == "title":
        return await _request_daemon("browser2.title_requested", {}, "browser2.title_response")

    if sub == "url":
        return await _request_daemon("browser2.url_requested", {}, "browser2.url_response")

    if sub == "close":
        return await _request_daemon("browser2.close_requested", {}, "browser2.close_response")

    if sub == "console":
        return await _request_daemon(
            "browser2.console_requested",
            {
                "clear": str(ctx.args.get("clear", "false")).lower() in ("true", "1", "yes"),
            },
            "browser2.console_response",
        )

    if sub == "wait":
        return await _request_daemon(
            "browser2.wait_requested",
            {
                "seconds": float(ctx.args.get("seconds") or 1),
            },
            "browser2.wait_response",
        )

    return f"Unknown browser2 command: {sub}\n".encode(), 1


__chaitya_handler__ = browser2_handler
__adapter_contract__ = asdict(browser2_handler.__chaitya_contract__)
