"""Shell adapter — Unix execution layer for Chaitya Core.

This is the primary "run anything" adapter. It executes shell commands
via subprocess, giving:
  - Real shell semantics: pipes, redirects, subshells all work
  - Consistent output format: [exit:N | Xms] metadata footer (via L2)
  - Works everywhere: no tmux dependency required

Design (Reddit cli-reddit.txt philosophy):
  - Single run() tool: everything via --command string
  - Progressive help: no args → usage, unknown → suggestions
  - stderr always visible: non-zero exit + stderr → [stderr] appended
  - Binary guard in L2: binary output → actionable error message

For persistent sessions with environment/state, use the Chaitya pipeline
with adapter-level piping:
  chaitya file read log.txt | chaitya shell run --command "grep ERROR"
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter

_SHELL_HELP = """chaitya shell — Execute shell commands.

Usage:
  chaitya shell run --command "<shell-command>"

All shell features work: pipes, redirects, subshells, env vars.

Examples:
  chaitya shell run --command "pwd"
  chaitya shell run --command "ls -la | head -20"
  chaitya shell run --command "cat log.txt | grep ERROR | wc -l"

For inter-adapter piping, use the Chaitya pipeline:
  chaitya file read log.txt | chaitya shell run --command "grep ERROR"
"""

_SHELL_RUN_HELP = """chaitya shell run — Execute a shell command.

Usage:
  chaitya shell run --command "<shell-command>"

The command runs in the platform shell with full shell semantics.
Stdin from the pipeline is passed to the command.
"""

# Common interactive programs that need --batch or --quiet mode
_INTERACTIVE_WARN = {
    "vim": "--batch --not-a-term",
    "vi": "--batch --not-a-term",
    "nano": "--quiet --batch",
    "emacs": "--batch",
    "less": "--quit-at-eof",
    "more": "--quit-at-eof",
}


def _build_shell_command(command: str) -> tuple[list[str], str]:
    """Return the platform shell argv and label used to execute a command."""
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [shell, "-NoLogo", "-NoProfile", "-Command", command], "PowerShell"
    shell = os.environ.get("SHELL", "/bin/sh")
    return [shell, "-lc", command], shell


@adapter(
    name="shell",
    description="Execute shell commands with full shell semantics.",
    commands=[
        {
            "name": "run",
            "description": "Run a shell command. Pipes, redirects, and subshells all work.",
            "params": [
                {
                    "name": "command",
                    "required": True,
                    "description": "Shell command to execute",
                },
            ],
            "examples": [
                "chaitya shell run --command 'pwd'",
                "chaitya shell run --command 'ls -la | head -20'",
                "chaitya shell run --command 'cat log.txt | grep ERROR | wc -l'",
            ],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": ["."], "network": True},
)
async def shell_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if not sub:
        return _SHELL_HELP.encode(), 0

    if sub != "run":
        return (
            f"[error] shell: unknown subcommand: {sub}\n"
            f"Available: run\n"
            f"Try: chaitya shell run --command 'help'\n".encode(),
            127,
        )

    command = ctx.args.get("command")
    if not command:
        return _SHELL_RUN_HELP.encode(), 0

    command_str = str(command)

    # Warn about interactive programs
    first_word = command_str.strip().split()[0] if command_str.strip() else ""
    if first_word in _INTERACTIVE_WARN:
        hint = _INTERACTIVE_WARN[first_word]
        return (
            f"[error] shell: '{first_word}' is interactive. "
            f"Use {hint} for non-interactive mode.\n"
            f"[exit:1 | 0ms]\n".encode(),
            1,
        )

    # Detect obvious destructive commands and warn
    dangerous = ["rm -rf", "dd if=", ":(){:|:&};:", "mkfs", "dd conv=sparse"]
    for d in dangerous:
        if command_str.strip().startswith(d):
            return (
                f"[error] shell: potentially destructive command detected.\n"
                f"If intentional, run directly: {command_str}\n"
                f"[exit:1 | 0ms]\n".encode(),
                1,
            )

    shell_argv, _shell_label = _build_shell_command(command_str)

    # Run the command via the platform shell so adapters behave consistently
    # across macOS/Linux and Windows.
    proc = await asyncio.create_subprocess_exec(
        *shell_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(ctx.env or {})},
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=300.0,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return (
            f"[error] shell: command timed out after 300s\n"
            f"Command: {command_str[:100]}\n"
            f"[exit:124 | 300000ms]\n".encode(),
            124,
        )

    exit_code = proc.returncode or 0
    combined = stdout + (b"\n[stderr] " + stderr if stderr else b"")

    return combined, exit_code


__chaitya_handler__ = shell_handler
__adapter_contract__ = asdict(shell_handler.__chaitya_contract__)
