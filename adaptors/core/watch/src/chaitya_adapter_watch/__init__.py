"""Watch adapter — observe events: query history or stream live.

This is a core adapter that provides the ``watch`` command.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import AsyncIterator

from chaitya_sdk import ChaityaStream, SessionContext, adapter, event_bus, store
from chaitya_sdk.types import EventFilter


@adapter(
    name="watch",
    description="Observe events: query history or stream live.",
    commands=[
        {
            "name": "--all",
            "description": "Watch all events (history query).",
            "params": [
                {"name": "on", "required": False, "description": "Event type filter."},
                {"name": "exit-after", "required": False, "description": "Exit after N events."},
                {"name": "limit", "required": False, "description": "Max events to return."},
            ],
            "examples": [
                "chaitya watch --all",
                "chaitya watch --all --on session_created --limit 10",
            ],
        },
        {
            "name": "--session",
            "description": "Watch events for a session.",
            "params": [
                {"name": "name", "required": False, "description": "Session name."},
                {"name": "on", "required": False, "description": "Event type filter."},
                {"name": "exit-after", "required": False, "description": "Exit after N events."},
            ],
            "examples": ["chaitya watch --session my-session"],
        },
        {
            "name": "--search",
            "description": "Search events by query string.",
            "params": [
                {"name": "search", "required": False, "description": "Search query."},
                {
                    "name": "since",
                    "required": False,
                    "description": "Since duration (e.g. 1h, 30m).",
                },
                {"name": "limit", "required": False, "description": "Max events to return."},
            ],
            "examples": ["chaitya watch --search error --limit 20"],
        },
        {
            "name": "--live",
            "description": "Stream live events as they occur.",
            "params": [
                {"name": "on", "required": False, "description": "Event type filter."},
                {"name": "session", "required": False, "description": "Session name."},
                {"name": "timeout", "required": False, "description": "Timeout in seconds."},
            ],
            "examples": ["chaitya watch --live --on session_created"],
        },
    ],
    permissions={
        "fs_read": [],
        "fs_write": [],
        "network": False,
        "can_emit_events": False,
        "can_read_all_events": True,
    },
)
async def watch_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    s = store.get_store()

    event_types = [args.get("on")] if args.get("on") else None
    session_id = args.get("session") or args.get("name")
    limit = int(args.get("limit", 100))
    exit_after = int(args.get("exit_after", args.get("exit-after", 0)))

    ef = EventFilter(event_types=event_types, session_id=session_id, limit=limit)
    try:
        events = await s.get_events(ef)
        if exit_after > 0:
            events = events[:exit_after]
        lines = []
        for ev in events:
            lines.append(json.dumps(asdict(ev)))
        return "\n".join(lines).encode("utf-8"), 0
    except (TypeError, AttributeError) as exc:
        return f"watch: event history not available ({exc})\n".encode("utf-8"), 1


__chaitya_handler__ = watch_handler
__adapter_contract__ = asdict(watch_handler.__chaitya_contract__)
