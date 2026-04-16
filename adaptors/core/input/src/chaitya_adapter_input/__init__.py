"""Input adapter — L0 Ingest: provide input data to the pipeline.

This is a core adapter that provides the ``input`` command.
"""

from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import asdict
from pathlib import Path

from chaitya_sdk import ChaityaStream, SessionContext, adapter, store


def _strip_quotes(value: str) -> str:
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value[1:-1]
    return value


@adapter(
    name="input",
    description="L0 Ingest: provide input data to the pipeline.",
    commands=[
        {
            "name": "--text",
            "description": "Create stream from text.",
            "params": [{"name": "text", "required": False, "description": "Text content."}],
            "examples": ["chaitya input --text 'hello world'"],
        },
        {
            "name": "--file",
            "description": "Create stream from file.",
            "params": [
                {"name": "file", "required": False, "description": "File path (may be repeated)."},
                {"name": "type", "required": False, "description": "MIME type override."},
                {
                    "name": "merge",
                    "required": False,
                    "description": "Merge mode: concat, lines, json.",
                },
            ],
            "examples": [
                "chaitya input --file myfile.txt",
                "chaitya input --file a.txt --file b.txt --merge concat",
            ],
        },
        {
            "name": "--clipboard",
            "description": "Create stream from clipboard.",
            "params": [],
            "examples": ["chaitya input --clipboard"],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": [], "network": False, "can_emit_events": False},
)
async def input_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    sub = args.get("subcommand", "")

    if sub == "list":
        s = store.get_store()
        sessions = await s.list_sessions()
        waiting = [s for s in sessions if s.state.value == "waiting"]
        if not waiting:
            return b"No sessions waiting for input. Use 'session status' to check all sessions.", 0
        lines = [f"{'SESSION':30s} {'STATE':10s} LAST ACTIVITY"]
        for s in waiting:
            lines.append(f"{s.name:30s} {s.state.value:10s} {s.last_activity}")
        return "\n".join(lines).encode("utf-8"), 0

    if sub in ("respond",):
        return (
            b"Use 'session send-input <name> <value>' instead.\n"
            b"Input responses are handled directly by sessions.",
            0,
        )

    text = _strip_quotes(str(args.get("text", "")))
    clipboard = args.get("clipboard")
    declared_type = _strip_quotes(str(args.get("type", "")))
    merge = _strip_quotes(str(args.get("merge", "concat")))

    raw_args = args.get("__raw_args__", [])
    file_paths: list[str] = []
    i = 0
    while i < len(raw_args):
        arg = raw_args[i]
        if arg == "--file":
            if i + 1 < len(raw_args):
                next_val = raw_args[i + 1]
                if not str(next_val).startswith("--"):
                    file_paths.append(_strip_quotes(str(next_val)))
                    i += 2
                    continue
        elif str(arg).startswith("--file="):
            file_paths.append(_strip_quotes(str(arg)[7:]))
        i += 1

    content = b""
    mime_type = declared_type or "text/plain"
    sources: list[str] = []

    if text:
        content = text.encode("utf-8")
        mime_type = "text/plain"
        sources.append("<text>")

    if clipboard:
        return b"[error] clipboard not available in adapter context", 1

    if file_paths:
        merged = b""
        for file_path in file_paths:
            try:
                path = Path(file_path)
                if not path.exists():
                    return f"[error] file not found: {file_path}\n".encode(), 1
                file_content = path.read_bytes()
                merged += file_content
                sources.append(str(path))
            except PermissionError:
                return f"[error] permission denied: {file_path}\n".encode(), 1
            except Exception as exc:
                return f"[error] cannot read {file_path}: {exc}\n".encode(), 1

        if merge == "concat":
            content = merged
        elif merge == "lines":
            content = b"\n".join(merged.splitlines(keepends=True))
        elif merge == "json":
            import json

            content = json.dumps(
                {"parts": [str(len(file_paths)), merged.decode("utf-8", errors="replace")]}
            ).encode()
        else:
            content = merged

        if not declared_type:
            mime, _ = mimetypes.guess_type(file_paths[0])
            mime_type = mime or "application/octet-stream"

    if not content:
        return b"[error] no input source specified (--text, --file, or --clipboard)\n", 1

    return content, 0


__chaitya_handler__ = input_handler
__adapter_contract__ = asdict(input_handler.__chaitya_contract__)
