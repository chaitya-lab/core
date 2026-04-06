"""Process adapter — System process management for Chaitya Core.

Provides system-level process introspection and control via subprocess calls
to the native `ps` command (available on all Unix-like systems and macOS).

Design philosophy (Linux kernel / Unix tradition):
  - Simple, composable tools that do one thing well
  - `ps` provides process info; signal delivery via os.kill
  - No psutil dependency — uses only stdlib + ps subprocess

Usage:
  chaitya process list                         List all processes
  chaitya process info --pid <n>               Show process details
  chaitya process signal --pid <n> --sig TERM  Send signal to process
  chaitya process kill --pid <n>              Send SIGKILL to process
  chaitya process children --pid <n>          Show child processes
  chaitya process tree                        Show full process tree
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter


def _available_signal_map() -> dict[str, int]:
    """Return the portable subset of supported signals for this platform."""
    sig_map: dict[str, int] = {
        "TERM": signal.SIGTERM,
        "SIGTERM": signal.SIGTERM,
        "INT": signal.SIGINT,
        "SIGINT": signal.SIGINT,
    }
    optional_names = [
        "SIGHUP",
        "SIGKILL",
        "SIGUSR1",
        "SIGUSR2",
        "SIGSTOP",
        "SIGCONT",
    ]
    aliases = {
        "SIGHUP": "HUP",
        "SIGKILL": "KILL",
        "SIGUSR1": "USR1",
        "SIGUSR2": "USR2",
        "SIGSTOP": "STOP",
        "SIGCONT": "CONT",
    }
    for name in optional_names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        sig_map[name] = sig
        sig_map[aliases[name]] = sig
    return sig_map


def _ps_list() -> bytes:
    if sys.platform == "win32":
        proc = asyncio.run(
            asyncio.create_subprocess_shell(
                "tasklist",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        stdout, _ = asyncio.run(proc.communicate())
        return stdout

    proc = asyncio.run(
        asyncio.create_subprocess_shell(
            "ps auxww",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    stdout, stderr = asyncio.run(proc.communicate())
    if proc.returncode != 0:
        return f"[error] ps: {stderr.decode(errors='replace')}".encode()
    return stdout


async def _ps_list_async() -> tuple[bytes, int]:
    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_shell(
            "tasklist",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout, proc.returncode

    proc = await asyncio.create_subprocess_shell(
        "ps auxww",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return stderr, proc.returncode
    return stdout, proc.returncode


async def _ps_tree_async() -> tuple[bytes, int]:
    proc = await asyncio.create_subprocess_shell(
        "ps axweo pid,ppid,stat,time,comm --forest",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return stderr, proc.returncode
    return stdout, proc.returncode


async def _ps_children_async(pid: int) -> tuple[bytes, int]:
    proc = await asyncio.create_subprocess_shell(
        f"ps --ppid {pid} -o pid,ppid,stat,time,comm",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return stderr, proc.returncode
    return stdout, proc.returncode


async def _ps_info_async(pid: int) -> tuple[bytes, int]:
    proc = await asyncio.create_subprocess_shell(
        f"ps -p {pid} -o pid,ppid,stat,time,user,comm,args",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not stdout.strip():
        return f"[error] process: no such process: {pid}\n".encode(), 1
    return stdout, 0


_PROCESS_HELP = """chaitya process — System process management.

Usage:
  chaitya process list                          List all processes
  chaitya process tree                          Show process tree
  chaitya process info --pid <n>                Show process details
  chaitya process children --pid <n>            Show child processes
  chaitya process signal --pid <n> --sig TERM  Send signal
  chaitya process kill --pid <n>               Send SIGKILL (force kill)

Note: On Unix, signal delivery requires permission (same UID as target).
On macOS/Linux, root/owner can signal any process.
"""


@adapter(
    name="process",
    description="System process management: list, inspect, signal, and kill processes.",
    commands=[
        {
            "name": "list",
            "description": "List all running processes (ps auxww).",
            "params": [],
            "examples": ["chaitya process list"],
        },
        {
            "name": "tree",
            "description": "Show process tree with parent-child relationships.",
            "params": [],
            "examples": ["chaitya process tree"],
        },
        {
            "name": "info",
            "description": "Show detailed info for a specific process.",
            "params": [
                {"name": "pid", "required": True, "description": "Process ID"},
            ],
            "examples": ["chaitya process info --pid 1234"],
        },
        {
            "name": "children",
            "description": "Show child processes of a given PID.",
            "params": [
                {"name": "pid", "required": True, "description": "Parent process ID"},
            ],
            "examples": ["chaitya process children --pid 1"],
        },
        {
            "name": "signal",
            "description": "Send a signal to a process.",
            "params": [
                {"name": "pid", "required": True, "description": "Process ID"},
                {"name": "sig", "required": False, "description": "Signal name (default: TERM)"},
            ],
            "examples": [
                "chaitya process signal --pid 1234 --sig TERM",
                "chaitya process signal --pid 1234 --sig KILL",
            ],
        },
        {
            "name": "kill",
            "description": "Force kill a process (SIGKILL).",
            "params": [
                {"name": "pid", "required": True, "description": "Process ID"},
            ],
            "examples": ["chaitya process kill --pid 1234"],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": [], "network": False},
)
async def process_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if not sub:
        return _PROCESS_HELP.encode(), 0

    if sub == "list":
        return await _ps_list_async()

    if sub == "tree":
        return await _ps_tree_async()

    if sub == "info":
        pid_str = ctx.args.get("pid")
        if not pid_str:
            return b"[error] process info: --pid is required\n", 1
        try:
            pid = int(pid_str)
        except ValueError:
            return f"[error] process info: --pid must be an integer, got: {pid_str}\n".encode(), 1
        return await _ps_info_async(pid)

    if sub == "children":
        pid_str = ctx.args.get("pid")
        if not pid_str:
            return b"[error] process children: --pid is required\n", 1
        try:
            pid = int(pid_str)
        except ValueError:
            return (
                f"[error] process children: --pid must be an integer, got: {pid_str}\n".encode(),
                1,
            )
        return await _ps_children_async(pid)

    if sub == "signal":
        pid_str = ctx.args.get("pid")
        sig_str = str(ctx.args.get("sig", "TERM")).upper()
        if not pid_str:
            return b"[error] process signal: --pid is required\n", 1
        try:
            pid = int(pid_str)
        except ValueError:
            return f"[error] process signal: --pid must be an integer, got: {pid_str}\n".encode(), 1

        sig_map = _available_signal_map()
        sig = sig_map.get(sig_str)
        if sig is None:
            return f"[error] process signal: unknown signal: {sig_str}\n".encode(), 1
        try:
            os.kill(pid, sig)
            return f"[ok] signal {sig_str} sent to process {pid}\n".encode(), 0
        except PermissionError:
            return f"[error] process: permission denied: {pid}\n".encode(), 1
        except ProcessLookupError:
            return f"[error] process: no such process: {pid}\n".encode(), 1
        except OSError as exc:
            return f"[error] process signal: {exc}\n".encode(), 1

    if sub == "kill":
        pid_str = ctx.args.get("pid")
        if not pid_str:
            return b"[error] process kill: --pid is required\n", 1
        try:
            pid = int(pid_str)
        except ValueError:
            return f"[error] process kill: --pid must be an integer, got: {pid_str}\n".encode(), 1
        try:
            kill_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            kill_name = "SIGKILL" if hasattr(signal, "SIGKILL") else "SIGTERM"
            os.kill(pid, kill_sig)
            return f"[ok] process {pid} killed ({kill_name})\n".encode(), 0
        except PermissionError:
            return f"[error] process: permission denied: {pid}\n".encode(), 1
        except ProcessLookupError:
            return f"[error] process: no such process: {pid}\n".encode(), 1
        except OSError as exc:
            return f"[error] process kill: {exc}\n".encode(), 1

    return (
        f"[error] process: unknown subcommand: {sub}\n"
        f"Available: list, tree, info, children, signal, kill\n"
        f"Try: chaitya process --help\n".encode(),
        127,
    )


__chaitya_handler__ = process_handler
__adapter_contract__ = asdict(process_handler.__chaitya_contract__)
