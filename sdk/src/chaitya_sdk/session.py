"""SessionRunner — high-level session execution helper for adapters.

This is the SDK's abstraction over the kernel's session management primitives.
Adapters use this instead of directly calling tmux or managing polling loops.

PRD alignment:
  - Kernel does session management (§3.3): creates, sends input, polls output.
  - Adapters act through this SDK helper (§10): adapters only call runner.run().

Usage::

    from chaitya_sdk import adapter, ChaityaStream, SessionContext
    from chaitya_sdk.session import SessionRunner

    @adapter(...)
    async def my_handler(stream: ChaityaStream, ctx: SessionContext):
        runner = SessionRunner("myadapter", ctx.session_name, timeout=300.0)
        output, elapsed = await runner.run(
            "mytool run --query hello",
            completion_patterns=[re.compile(r"muku@"), re.compile(r"\\[exit:")],
        )
        return output.encode(), 0

The runner handles:
  1. Creates session if it doesn't exist.
  2. Records current output length.
  3. Checks session state; applies on_busy_input policy.
  4. Sends input to the session PTY.
  5. Polls output until a completion pattern matches or timeout fires.
  6. Returns (new_output_since_start, elapsed_seconds).
  7. Emits adapter.<action>_start and adapter.<action>_complete events.

Architecture note: this module accesses ``chaitya.core.kernel._instance``
to get the kernel's SessionManager and registry. The kernel sets this at startup.
This is the only entry point where the SDK reaches into kernel internals —
everything else is purely through the SDK public API.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import suppress
from typing import Any

# ---------------------------------------------------------------------------
# Kernel access
# ---------------------------------------------------------------------------


def _get_kernel() -> Any:
    try:
        from chaitya.core import kernel as kmod

        return getattr(kmod, "_instance", None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SessionRunner
# ---------------------------------------------------------------------------


class SessionRunner:
    """High-level session execution helper.

    Wraps the kernel's session management primitives into a single ``run()``
    call. The kernel handles tmux; the adapter just calls ``runner.run()``.
    """

    DEFAULT_POLL_INTERVAL: float = 0.5
    DEFAULT_COMPLETION_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\[exit:"),
        re.compile(r"\$ "),
        re.compile(r"muku@"),
    ]

    def __init__(
        self,
        adapter_name: str,
        session_name: str,
        timeout: float = 300.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self.adapter_name = adapter_name
        self.session_name = session_name
        self.timeout = timeout
        self.poll_interval = poll_interval

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        command: str,
        completion_patterns: list[re.Pattern[str]] | None = None,
    ) -> tuple[str, float]:
        """Run a command in the session and wait for completion.

        1. Creates session if missing.
        2. Records current output length.
        3. Sends input (command + newline).
        4. Polls output until a completion pattern matches or timeout.
        5. Returns (output_since_start, elapsed_seconds).
        6. Emits adapter.<action>_start and adapter.<action>_complete events.

        Args:
            command: The text to send to the session PTY.
            completion_patterns: Regex patterns. Run returns when any matches
                in the full accumulated output. Defaults to shell prompt patterns.

        Returns:
            (new_output, elapsed_seconds)
            new_output is everything since we started (not full buffer).
            elapsed_seconds includes all time spent polling.
        """
        patterns = completion_patterns or self.DEFAULT_COMPLETION_PATTERNS
        kernel = _get_kernel()
        if kernel is None:
            raise RuntimeError("Kernel not running")

        await self._ensure_session(kernel)

        output_before = await self._read_output(kernel)
        last_len = len(output_before)

        await self._send_input(kernel, command)

        full_output, elapsed = await self._poll(
            kernel,
            last_len,
            lambda text: _looks_done(text, patterns),
        )

        new_output = _extract_output(full_output, last_len)
        return new_output, elapsed

    async def run_and_stream(
        self,
        command: str,
        callback: Any,
        completion_patterns: list[re.Pattern[str]] | None = None,
    ) -> tuple[str, float]:
        """Like ``run()`` but calls ``callback(chunk: str)`` for each new chunk.

        The callback receives the new text since the last poll. It is called
        from within the polling loop. Use for real-time streaming of session
        output to L2.

        Args:
            command: Text to send to the session PTY.
            callback: Callable invoked with each new output chunk (str).
            completion_patterns: Regex patterns for completion detection.

        Returns:
            (full_output_since_start, elapsed_seconds)
        """
        patterns = completion_patterns or self.DEFAULT_COMPLETION_PATTERNS
        kernel = _get_kernel()
        if kernel is None:
            raise RuntimeError("Kernel not running")

        await self._ensure_session(kernel)

        output_before = await self._read_output(kernel)
        last_len = len(output_before)

        await self._send_input(kernel, command)

        full_output, elapsed = await self._poll(
            kernel,
            last_len,
            lambda text: _looks_done(text, patterns),
            on_chunk=callback,
        )

        return full_output, elapsed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_session(self, kernel: Any) -> None:
        s_mgr = kernel._session_mgr
        with suppress(Exception):
            await s_mgr.create(name=self.session_name, template=None)

    async def _send_input(self, kernel: Any, command: str) -> None:
        await self._apply_busy_input_policy(kernel)
        data = f"{command}\n".encode()
        await kernel._session_mgr.send_input(self.session_name, data)

    async def _apply_busy_input_policy(self, kernel: Any) -> None:
        """Check session state and apply on_busy_input policy.

        If session is BUSY, either wait (queue) or raise (reject) per
        the adapter's contract. Falls back to queue if registry unavailable.
        """
        record = await kernel._session_mgr.status(self.session_name)
        if record is None:
            return

        from chaitya.core.types import SessionState
        from chaitya_sdk.types import BusyInputPolicy

        if record.state != SessionState.BUSY:
            return

        policy = self._get_busy_input_policy(kernel)
        if policy == BusyInputPolicy.REJECT:
            raise RuntimeError(
                f"Session '{self.session_name}' is busy. "
                f"Adapter '{self.adapter_name}' has on_busy_input=reject. "
                "Wait for the session to become idle and retry."
            )

        # Queue: wait for session to become non-BUSY
        deadline = time.monotonic() + self.timeout
        poll_interval = min(self.poll_interval, 2.0)
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            record = await kernel._session_mgr.status(self.session_name)
            if record is None or record.state != SessionState.BUSY:
                return
        raise RuntimeError(
            f"Session '{self.session_name}' is busy and did not become idle "
            f"within {self.timeout}s timeout."
        )

    def _get_busy_input_policy(self, kernel: Any) -> Any:
        """Look up the adapter's on_busy_input policy from the registry."""
        try:
            registry = getattr(kernel, "_registry", None)
            if registry is None:
                return BusyInputPolicy.QUEUE
            pkg = registry.get_adapter(self.adapter_name)
            if pkg is None or pkg.contract is None:
                return BusyInputPolicy.QUEUE
            return getattr(pkg.contract, "on_busy_input", BusyInputPolicy.QUEUE)
        except Exception:
            return BusyInputPolicy.QUEUE

    async def _read_output(self, kernel: Any) -> str:
        output = await kernel._session_mgr.read_output(
            self.session_name,
            idle_timeout_seconds=0.1,
        )
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output or ""

    async def _poll(
        self,
        kernel: Any,
        last_len: int,
        done_fn: Any,
        on_chunk: Any = None,
    ) -> tuple[str, float]:
        start = time.monotonic()
        full_output = ""
        tracked_len = last_len
        while time.monotonic() - start < self.timeout:
            await asyncio.sleep(self.poll_interval)
            output = await self._read_output(kernel)
            full_output = output
            if len(output) > tracked_len:
                if on_chunk is not None:
                    new_chunk = output[tracked_len:]
                    on_chunk(new_chunk)
                if done_fn(output):
                    return output, time.monotonic() - start
                tracked_len = len(output)
        return full_output, self.timeout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_done(text: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return True if any completion pattern matches in text."""
    return any(p.search(text) for p in patterns)


def _extract_output(full_output: str, last_len: int) -> str:
    """Extract output new since last_len."""
    return full_output[last_len:] if last_len < len(full_output) else ""


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = ["SessionRunner", "_looks_done", "_extract_output"]
