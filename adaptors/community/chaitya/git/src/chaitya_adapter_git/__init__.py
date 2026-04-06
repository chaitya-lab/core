"""Passthrough adapter for the git CLI — blocks dangerous commands.

This adapter demonstrates the PassthroughCLI pattern from the SDK:
- Declares ``"*"`` as the wildcard command
- Blocks destructive subcommands (push, reset --hard, clean -fd) unless --confirm is passed
- Passes everything else through to git via SessionRunner
- Uses override handlers for specific subcommands when needed

Usage:
    chaitya git status
    chaitya git add .
    chaitya git commit -m "fix"
    chaitya git log --oneline -5

Destructive commands (require --confirm to execute):
    chaitya git push          # requires --confirm: network action
    chaitya git reset --hard  # requires --confirm: destructive
    chaitya git clean -fd     # requires --confirm: destructive
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from chaitya_sdk import (
    ChaityaStream,
    SessionContext,
    adapter,
)
from chaitya_sdk.wrappers import PassthroughCLI


# ---------------------------------------------------------------------------
# Per-command override handlers (custom logic for specific subcommands)
# ---------------------------------------------------------------------------


async def _handle_clone(ctx: SessionContext) -> tuple[bytes, int]:
    """Override: git clone with optional --depth and directory args."""
    raw_args: list[str] = list(ctx.args.get("__raw_args__", []))
    session_name = str(ctx.args.get("session") or "git-default")

    from chaitya_sdk.session import SessionRunner

    runner = SessionRunner("git", session_name, timeout=60.0)
    cmd_str = "git clone " + " ".join(raw_args)
    output, _ = await runner.run(
        cmd_str,
        completion_patterns=[
            re.compile(r"Cloning into"),
            re.compile(r"muku@"),
            re.compile(r"\$ "),
        ],
    )
    return output.encode("utf-8"), 0


# ---------------------------------------------------------------------------
# PassthroughCLI definition
# ---------------------------------------------------------------------------

_git = PassthroughCLI(
    name="git",
    description="Wrapper for the git CLI. Passes subcommands through to git. "
    "Destructive commands require --confirm to execute.",
    blocked=[
        "push",
        "push --force",
        "force push",
        "reset --hard",
        "reset --mixed",
        "reset --soft",
        "clean -fd",
        "clean -fdx",
        "rebase --abort",
        "am --abort",
    ],
    overrides={
        "clone": _handle_clone,
    },
    default_session="git-default",
)


# ---------------------------------------------------------------------------
# Adapter handler
# ---------------------------------------------------------------------------


@adapter(**_git.build_contract())  # type: ignore[arg-type]
async def git_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    has_confirm = bool(ctx.args.get("confirm"))
    blocked = _git.is_blocked(
        str(ctx.args.get("subcommand", "*")),
        list(ctx.args.get("__raw_args__", [])),
    )
    if blocked and not has_confirm:
        sub = str(ctx.args.get("subcommand", ""))
        raw = ctx.args.get("__raw_args__", [])
        full_cmd = f"git {sub} {' '.join(str(a) for a in raw)}".strip()
        return (
            f"[confirm] This will execute: {full_cmd}\nRun with --confirm to proceed.\n".encode(
                "utf-8"
            ),
            0,
        )
    return await _git.passthrough(ctx)


# ---------------------------------------------------------------------------
# Module exports (Chaitya SDK contract)
# ---------------------------------------------------------------------------

__chaitya_handler__ = git_handler
__adapter_contract__ = asdict(git_handler.__chaitya_contract__)
