"""Test adaptor for exercising all Chaitya Core kernel features.

This adaptor is NOT for external use — it exists to validate that the
kernel's interactive features work correctly.

Commands:
    hello     — Simple greeting, no args
    ping      — Emits a test event and returns pong
    echo      — Echoes back args verbatim
    ask       — Suspends requesting a name, resumes with greeting
    confirm   — Suspends with CONFIRM type, resumes with yes/no response
    emit      — Emits a named event with payload
    session   — Tests session create/send-input/output lifecycle

All commands return 0 on success, 1 on error.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict

from chaitya_sdk import (
    ChaityaStream,
    SessionContext,
    Suspension,
    InputSpec,
    InputType,
    adapter,
    event_bus,
)
from chaitya_sdk.types import Event


# ---------------------------------------------------------------------------
# hello — simplest possible command
# ---------------------------------------------------------------------------


@adapter(
    name="test",
    description="Test adaptor exercising all Chaitya Core kernel features.",
    commands=[
        {
            "name": "hello",
            "description": "Returns a greeting. No arguments.",
            "params": [],
            "examples": ["chaitya test hello"],
        },
        {
            "name": "ping",
            "description": "Emits a ping event, returns pong.",
            "params": [],
            "examples": ["chaitya test ping"],
        },
        {
            "name": "echo",
            "description": "Echoes back the message argument.",
            "params": [{"name": "message", "required": True, "description": "Text to echo back"}],
            "examples": ["chaitya test echo --message hello"],
        },
        {
            "name": "ask",
            "description": "Suspends asking for a name, resumes with a greeting.",
            "params": [
                {
                    "name": "name",
                    "required": True,
                    "description": "Your name",
                    "on_missing": "suspend",
                }
            ],
            "examples": ["chaitya test ask --name Alice"],
        },
        {
            "name": "confirm",
            "description": "Suspends with CONFIRM type, resumes with yes/no response.",
            "params": [
                {
                    "name": "answer",
                    "required": True,
                    "description": "Yes or no",
                    "on_missing": "suspend",
                }
            ],
            "examples": ["chaitya test confirm --answer yes"],
        },
        {
            "name": "emit",
            "description": "Emit a named event with JSON payload.",
            "params": [
                {"name": "name", "required": True, "description": "Event type name"},
                {"name": "payload", "required": False, "description": "JSON payload"},
            ],
            "examples": ['chaitya test emit --name my_event --payload \'{"key":"value"}\''],
        },
        {
            "name": "session-test",
            "description": "Tests session lifecycle: create, send-input, output.",
            "params": [
                {"name": "session", "required": True, "description": "Session name"},
            ],
            "examples": ["chaitya test session-test --session test-session"],
        },
    ],
    permissions={"fs_read": [], "fs_write": [], "network": False, "can_emit_events": True},
)
async def test_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    subcommand = str(ctx.args.get("subcommand", ""))

    # ---- hello ----
    if subcommand == "hello":
        return b"Hello from the test adaptor! chaitya-core is working.\n", 0

    # ---- ping ----
    if subcommand == "ping":
        await event_bus.emit(
            Event(
                type="test.ping",
                source_adapter="test",
                session_id=ctx.session_id,
                payload={"ping": True},
            )
        )
        return b"pong\n", 0

    # ---- echo ----
    if subcommand == "echo":
        message = str(ctx.args.get("message", ""))
        return f"echo: {message}\n".encode("utf-8"), 0

    # ---- ask (suspension) ----
    if subcommand == "ask":
        if "name" not in ctx.args or not ctx.args["name"]:
            raise Suspension(
                InputSpec(
                    name="name",
                    prompt="What is your name?",
                    input_type=InputType.TEXT,
                )
            )
        name = str(ctx.args["name"])
        greeting = f"Hello, {name}! Welcome to chaitya-core.\n"
        await event_bus.emit(
            Event(
                type="test.ask.completed",
                source_adapter="test",
                session_id=ctx.session_id,
                payload={"name": name},
            )
        )
        return greeting.encode("utf-8"), 0

    # ---- confirm (CONFIRM type suspension) ----
    if subcommand == "confirm":
        if "answer" not in ctx.args or not ctx.args["answer"]:
            raise Suspension(
                InputSpec(
                    name="answer",
                    prompt="Proceed with action?",
                    input_type=InputType.CONFIRM,
                )
            )
        answer = str(ctx.args["answer"]).lower()
        if answer in ("yes", "y", "true", "1"):
            result = "Confirmed: proceeding with action.\n"
        else:
            result = "Denied: action cancelled.\n"
        await event_bus.emit(
            Event(
                type="test.confirm.completed",
                source_adapter="test",
                session_id=ctx.session_id,
                payload={"answer": answer},
            )
        )
        return result.encode("utf-8"), 0

    # ---- emit ----
    if subcommand == "emit":
        name = str(ctx.args.get("name", "test.custom"))
        payload_str = str(ctx.args.get("payload", "{}"))
        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": payload_str}
        await event_bus.emit(
            Event(
                type=f"test.{name}",
                source_adapter="test",
                session_id=ctx.session_id,
                payload=payload,
            )
        )
        return f"Emitted event: test.{name}\n".encode("utf-8"), 0

    # ---- session-test ----
    # Session operations require using the kernel's session commands directly.
    # This subcommand documents the expected flow rather than accessing sessions directly.
    if subcommand == "session-test":
        session = str(ctx.args.get("session", ""))
        if not session:
            return b"Usage: test session-test --session <name>\n", 1
        return (
            f"Session test: use 'session create {session}' then "
            f"'session send-input {session} <cmd>' and 'session output {session}'\n".encode(
                "utf-8"
            ),
            0,
        )

    return f"Unknown test subcommand: {subcommand}\n".encode("utf-8"), 1


__chaitya_handler__ = test_handler
__adapter_contract__ = asdict(test_handler.__chaitya_contract__)
