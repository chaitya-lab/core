"""Output adapter — L2 Present: format and filter command output.

This is a core adapter that provides the ``output`` command.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter


@adapter(
    name="output",
    description="L2 Present: format and filter command output.",
    commands=[
        {
            "name": "--format",
            "description": "Set output format.",
            "params": [{"name": "format", "required": False, "description": "Format: text, json."}],
            "examples": ["chaitya output --format json"],
        },
        {
            "name": "--filter",
            "description": "Filter output lines by pattern.",
            "params": [{"name": "filter", "required": False, "description": "Filter pattern."}],
            "examples": ["chaitya output --filter ERROR"],
        },
    ],
    permissions={"fs_read": [], "fs_write": [], "network": False, "can_emit_events": False},
)
async def output_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    output_format = str(args.get("format", "text")).lower()
    filter_pattern = args.get("filter")

    if not output_format and not filter_pattern:
        return (
            b"output configuration:\n"
            b"  --format json|text    Set output format\n"
            b"  --filter <pattern>    Filter output lines\n"
            b"Current format: text (JSON-lines also supported)\n"
            b"Use as pipeline modifier: pipe to filter output",
            0,
        )

    content = stream.content or b""

    if filter_pattern:
        text_content = content.decode("utf-8", errors="replace")
        lines = text_content.splitlines()
        try:
            pattern = re.compile(str(filter_pattern))
            filtered = [l for l in lines if pattern.search(l)]
            result = "\n".join(filtered)
        except re.error:
            result = f"Invalid regex pattern: {filter_pattern}"
        return result.encode("utf-8"), 0

    if output_format == "json":
        return json.dumps(
            {
                "content": content.decode("utf-8", errors="replace"),
                "type": stream.declared_type,
                "size_bytes": stream.size_bytes,
            },
            separators=(",", ":"),
        ).encode("utf-8"), 0

    return content, 0


__chaitya_handler__ = output_handler
__adapter_contract__ = asdict(output_handler.__chaitya_contract__)
