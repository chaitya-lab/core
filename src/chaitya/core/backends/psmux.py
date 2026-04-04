"""psmux-backed session backend for Windows.

psmux (https://github.com/psmux/psmux) is a native Windows terminal
multiplexer written in Rust. It speaks the same command language as tmux,
meaning our TmuxSessionBackend logic is directly reusable.

The PsmuxBackend subclasses TmuxSessionBackend and overrides only what
differs on Windows: signal delivery (no POSIX signals, no os.killpg),
environment management (PowerShell syntax vs. bash), and the binary name.

Install psmux:
    winget install psmux
    # or: cargo install psmux
    # or: scoop install psmux

Reference: PRD §3.3 — "psmux (Windows, v2)"
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import sys

from chaitya.core.types import SessionHandle, SessionIdentity

# Re-use TmuxSessionBackend for all shared tmux-protocol logic
from chaitya.core.backends.tmux import TmuxSessionBackend

logger = logging.getLogger("chaitya.core.backends.psmux")


class PsmuxBackend(TmuxSessionBackend):
    """Session backend using psmux on Windows.

    psmux is a native Rust tmux clone for Windows that uses ConPTY and
    speaks the tmux command protocol. It supports the same command set:
    new-session, kill-session, has-session, send-keys, capture-pane, etc.

    This backend subclasses TmuxSessionBackend and overrides Windows-
    specific behaviour (signal delivery, env-var syntax, binary detection).

    Attributes:
        BACKEND_NAME: Canonical name used in core.yaml session.backend.
    """

    BACKEND_NAME = "psmux"

    def __init__(self, *, psmux_bin: str = "psmux", poll_interval: float = 0.1) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "PsmuxBackend is only supported on Windows. "
                "Use TmuxSessionBackend on macOS/Linux."
            )
        resolved = shutil.which(psmux_bin)
        if resolved is None:
            raise RuntimeError(
                f"psmux binary '{psmux_bin}' not found on PATH.\n"
                "Install psmux with one of:\n"
                "  winget install psmux\n"
                "  cargo install psmux\n"
                "  scoop install psmux\n"
                "Then restart your terminal so PATH is updated."
            )
        # Initialise the parent using psmux as the tmux binary
        # sys.platform check in TmuxSessionBackend is bypassed by going
        # straight to the grandparent __init__ equivalent — we replicate
        # its setup here using the resolved psmux path.
        self._tmux_bin = resolved
        self._poll_interval = poll_interval
        self._capture_offsets: dict[str, bytes] = {}
        self._env_vars: dict[str, dict[str, str]] = {}
        logger.info("PsmuxBackend initialised with binary: %s", resolved)

    # ------------------------------------------------------------------
    # Windows-specific overrides
    # ------------------------------------------------------------------

    async def create(self, name: str, identity: SessionIdentity) -> SessionHandle:
        """Create a new named psmux session.

        Uses PowerShell as the default shell on Windows instead of $SHELL.
        """
        if await self.exists(name):
            raise ValueError(f"Session {name!r} already exists")

        # Prefer PowerShell 7 (pwsh) → Windows PowerShell → cmd.exe
        shell = (
            shutil.which("pwsh")
            or shutil.which("powershell")
            or os.environ.get("COMSPEC", "cmd.exe")
        )
        cwd = (
            os.path.expanduser(identity.working_dir)
            if identity.working_dir
            else os.getcwd()
        )

        args = ["new-session", "-d", "-s", name, "-c", cwd]
        for key, value in identity.env_vars.items():
            args.extend(["-e", f"{key}={value}"])
        args.append(shell)
        await self._run_tmux(*args)

        # Small stabilization delay for psmux server/session initialization on Windows
        await asyncio.sleep(0.5)

        self._env_vars[name] = dict(identity.env_vars)
        self._capture_offsets[name] = b""
        return SessionHandle(name=name, backend_id=await self._pane_id(name))

    async def signal(self, name: str, sig: str) -> None:
        """Send a signal to the session's process.

        Windows does not have POSIX signals. Maps common signals to the
        closest Windows equivalent via psmux send-keys.
        """
        await self._ensure_exists(name)

        if sig in ("SIGKILL", "SIGTERM"):
            # Kill the session entirely — closest to SIGKILL/SIGTERM on Windows
            await self._run_tmux("kill-session", "-t", name)
            self._capture_offsets.pop(name, None)
            self._env_vars.pop(name, None)
            logger.info("Session %s terminated via %s (psmux kill-session)", name, sig)
        elif sig == "SIGINT":
            # Send Ctrl-C to the running process
            await self._run_tmux("send-keys", "-t", name, "C-c", "")
            logger.info("Session %s received SIGINT (Ctrl-C)", name)
        elif sig == "SIGHUP":
            # Send Ctrl-Break — closest HUP equivalent on Windows
            await self._run_tmux("send-keys", "-t", name, "C-break", "")
            logger.info("Session %s received SIGHUP (Ctrl-Break)", name)
        else:
            raise ValueError(
                f"Signal {sig!r} is not supported on Windows. "
                "Supported: SIGTERM, SIGKILL, SIGINT, SIGHUP."
            )

    async def set_env(self, name: str, key: str, value: str) -> None:
        """Set an environment variable in the session.

        Uses PowerShell $env: syntax instead of bash export.
        """
        await self._ensure_exists(name)
        # Set at the psmux level (persists for new windows/panes)
        await self._run_tmux("set-environment", "-t", name, key, value)
        # Inject into the running shell — try PowerShell syntax first
        ps_cmd = f'$env:{key}="{value}"'
        # send-keys with Enter
        await self._run_tmux("send-keys", "-t", name, ps_cmd, "Enter")
        self._env_vars.setdefault(name, {})[key] = value

    async def unset_env(self, name: str, key: str) -> None:
        """Remove an environment variable from the session."""
        await self._ensure_exists(name)
        await self._run_tmux("set-environment", "-u", "-t", name, key)
        ps_cmd = f"Remove-Item Env:\\{key} -ErrorAction SilentlyContinue"
        await self._run_tmux("send-keys", "-t", name, ps_cmd, "Enter")
        self._env_vars.setdefault(name, {}).pop(key, None)

    async def send_input(self, name: str, data: bytes) -> None:
        """Send input to a psmux session.

        Handles Windows line endings (\r\n) and CRLF input properly.
        """
        await self._ensure_exists(name)
        # Normalise to LF for splitting, then use Enter as the line terminator
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if part:
                await self._run_tmux("send-keys", "-t", name, "-l", part)
            if index < len(parts) - 1:
                await self._run_tmux("send-keys", "-t", name, "Enter")

    # ------------------------------------------------------------------
    # Resilience Overrides (Windows can be slower/flaky with ConPTY)
    # ------------------------------------------------------------------

    async def _run_tmux(self, *args: str) -> None:
        """Run psmux command with retries for 'no server' errors."""
        max_retries = 10
        delay = 0.2
        for i in range(max_retries):
            proc = await asyncio.create_subprocess_exec(
                self._tmux_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return

            error = stderr.decode("utf-8", errors="replace").strip() or stdout.decode(
                "utf-8", errors="replace"
            ).strip()

            if "no server running" in error.lower() and i < max_retries - 1:
                await asyncio.sleep(delay)
                continue

            raise ValueError(error or f"psmux command failed: {' '.join(args)}")

    async def _run_tmux_capture(self, *args: str) -> str:
        """Run psmux capture command with retries."""
        max_retries = 10
        delay = 0.2
        for i in range(max_retries):
            proc = await asyncio.create_subprocess_exec(
                self._tmux_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode("utf-8", errors="replace").rstrip("\n")

            error = stderr.decode("utf-8", errors="replace").strip()
            if "no server running" in error.lower() and i < max_retries - 1:
                await asyncio.sleep(delay)
                continue

            raise ValueError(error or f"psmux command failed: {' '.join(args)}")


def is_psmux_available(bin_name: str = "psmux") -> bool:
    """Return True if psmux binary is available on PATH."""
    return shutil.which(bin_name) is not None
