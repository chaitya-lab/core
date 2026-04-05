"""Route adapter — Conditional pipeline routing for Chaitya Core.

Provides L1 conditional filtering: inspects input stream and
decides whether to pass it through, modify it, or drop it.

Design (PRD §3.5):
  - Conditional routing is NOT in the pipeline orchestrator.
  - A route adapter handles conditions. The orchestrator only connects pipes.

Usage:
  chaitya route --if-exit 0              # pass only if exit code == 0
  chaitya route --if-exit-nonzero        # pass only if exit code != 0
  chaitya route --if-pattern <regex>     # pass only if content matches
  chaitya route --if-type <mime>         # pass only if declared type matches
  chaitya route --if-blank               # pass only if content is empty
  chaitya route --unless-pattern <regex> # pass only if content does NOT match

Pipeline examples:
  chaitya file read log.txt | chaitya route --if-pattern "ERROR" | chaitya shell run --command "cat"
  chaitya shell run --command "make" | chaitya route --if-exit 0 | chaitya shell run --command "notify-send success"
"""

from __future__ import annotations

import re
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter


def _match_pattern(content: bytes, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, content.decode("utf-8", errors="replace")))
    except re.error:
        return False


@adapter(
    name="route",
    description="Conditional pipeline routing: filter content based on exit code, pattern, or type.",
    commands=[
        {
            "name": "check",
            "description": "Evaluate conditions on input stream, pass content if conditions match.",
            "params": [
                {
                    "name": "if-exit",
                    "required": False,
                    "description": "Pass only if exit code equals this value (0 = success)",
                },
                {
                    "name": "if-exit-nonzero",
                    "required": False,
                    "description": "Pass only if exit code is non-zero",
                },
                {
                    "name": "if-pattern",
                    "required": False,
                    "description": "Pass only if content matches this regex",
                },
                {
                    "name": "unless-pattern",
                    "required": False,
                    "description": "Pass only if content does NOT match this regex",
                },
                {
                    "name": "if-type",
                    "required": False,
                    "description": "Pass only if declared MIME type matches (e.g. text/plain)",
                },
                {
                    "name": "if-blank",
                    "required": False,
                    "description": "Pass only if content is empty",
                },
                {
                    "name": "inverse",
                    "required": False,
                    "description": "Invert the condition: pass content that would be dropped",
                },
            ],
            "examples": [
                "chaitya route --if-exit 0",
                "chaitya route --if-pattern 'ERROR|WARN'",
                "chaitya route --unless-pattern 'debug'",
                "chaitya route --if-type text/plain",
                "chaitya route --inverse --if-exit 0",
            ],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": [], "network": False},
)
async def route_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    sub = str(args.get("subcommand", ""))

    if sub and sub != "check":
        return (
            f"[error] route: unknown subcommand: {sub}\n"
            f"Available: check\n"
            f"Try: chaitya route --help\n".encode(),
            127,
        )

    content = stream.content or b""

    if_exit_raw = args.get("if-exit")
    if_exit_nonzero = args.get("if-exit-nonzero")
    if_pattern = args.get("if-pattern")
    unless_pattern = args.get("unless-pattern")
    if_type = args.get("if-type")
    if_blank = args.get("if-blank")
    inverse = args.get("inverse")

    passed = True

    if if_exit_raw is not None:
        try:
            expected = int(if_exit_raw)
            last_code = int(str(ctx.args.get("exit_code", "0")))
            passed = passed and (last_code == expected)
        except (ValueError, TypeError):
            return f"[error] route: --if-exit requires an integer, got: {if_exit_raw}\n".encode(), 1

    if if_exit_nonzero:
        last_code = int(str(ctx.args.get("exit_code", "0")))
        passed = passed and (last_code != 0)

    if if_pattern:
        passed = passed and _match_pattern(content, str(if_pattern))

    if unless_pattern:
        passed = passed and not _match_pattern(content, str(unless_pattern))

    if if_type:
        declared = stream.declared_type or ""
        passed = passed and (declared == str(if_type))

    if if_blank:
        passed = passed and (len(content) == 0)

    if inverse:
        passed = not passed

    if passed:
        return content, 0

    return (
        f"[route] dropped (condition not met)\n".encode(),
        0,
    )


__chaitya_handler__ = route_handler
__adapter_contract__ = asdict(route_handler.__chaitya_contract__)
