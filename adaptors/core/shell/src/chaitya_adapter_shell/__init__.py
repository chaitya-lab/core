from __future__ import annotations

import asyncio
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter


@adapter(
    name="shell",
    description="Execute shell commands in a local process.",
    commands=[
        {
            "name": "run",
            "description": "Run a shell command and return stdout/stderr.",
            "params": [{"name": "command", "required": True, "description": "Shell command"}],
            "examples": ["chaitya shell run --command 'pwd'"],
        }
    ],
    permissions={"fs_read": ["."], "fs_write": ["."], "network": True},
)
async def shell_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    subcommand = str(ctx.args.get("subcommand", ""))
    if subcommand != "run":
        return f"Unknown shell subcommand: {subcommand}".encode("utf-8"), 1

    command = ctx.args.get("command")
    if not command:
        return b"Missing required argument: --command", 1

    proc = await asyncio.create_subprocess_shell(
        str(command),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ctx.env or None,
    )
    stdout, stderr = await proc.communicate()
    return stdout + stderr, int(proc.returncode or 0)


__chaitya_handler__ = shell_handler
__adapter_contract__ = asdict(shell_handler.__chaitya_contract__)
