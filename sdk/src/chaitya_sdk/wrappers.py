"""CLIWrapper — declarative subprocess wrapper for the Chaitya SDK.

Usage::

    from chaitya_sdk.wrappers import CLIWrapper, run_cli

    @CLIWrapper(
        name="mytool",
        description="A thin wrapper around my CLI tool",
        commands=[
            {
                "name": "run",
                "description": "Run the tool",
                "params": [{"name": "query", "required": True}],
            },
        ],
    )
    class MyToolWrapper:
        def get_command(self, ctx, subcommand, **params) -> list[str]:
            return ["mytool", subcommand, params["query"]]

        def parse_output(self, ctx, stdout: bytes, stderr: bytes) -> tuple[bytes, int]:
            return stdout, 0

The adapter handler can then use ``run_cli(self, ctx, subcommand)`` to execute
the command, which:
1. Calls ``get_command()`` to build the argv
2. Runs the subprocess asynchronously
3. Collects stdout/stderr
4. Calls ``parse_output()`` to format the result
5. Returns (bytes, exit_code) for the adapter

The subprocess execution (spawn, streaming, timeout, PTY) is handled by the
SDK, not the adapter.  Adapters only define WHAT to run and HOW to interpret
the output.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from chaitya_sdk.session import SessionRunner
    from chaitya_sdk.types import SessionContext


@dataclass
class CommandResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    duration_s: float


class CLIWrapper:
    """Base class for declarative CLI wrappers.

    Subclass this and implement ``get_command`` and ``parse_output``.
    Then use ``run_cli()`` in your adapter handler::

        @adapter(...)
        async def my_handler(stream, ctx):
            result = await run_cli(MyToolWrapper(), ctx, "run", prompt="hello")
            return result.stdout, result.exit_code
    """

    name: str = ""
    description: str = ""
    commands: list[dict[str, Any]] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)

    def get_command(
        self,
        ctx: SessionContext,
        subcommand: str,
        **params: Any,
    ) -> list[str]:
        """Build the argv for a command.

        Override this to build the CLI invocation from the session context
        and parameters.  The default implementation assembles::

            [self.name, subcommand, --key value, positional...]

        Args:
            ctx: The session context (args, env, etc.)
            subcommand: The subcommand name parsed from the dispatch
            **params: Keyword arguments from the dispatch args

        Returns:
            List of strings: the argv to pass to subprocess
        """
        argv = [self.name, subcommand]
        positional: list[str] = []
        for key, value in params.items():
            if isinstance(value, bool) and value:
                argv.append(f"--{key}")
            elif value is not None:
                argv.append(f"--{key}")
                argv.append(str(value))
        argv.extend(positional)
        return argv

    def detect_activity(self, output: str) -> str | None:
        """Parse terminal output to detect activity state.

        Override this to detect waiting/idle/active states from the
        output stream.  Return None for default behavior.

        Common patterns:
            - ``^[>$#]\\s*$`` → idle (shell prompt)
            - ``approval required`` → waiting_input
            - ``Do you want to proceed`` → waiting_input

        Args:
            output: The terminal output string

        Returns:
            One of: "idle", "active", "waiting_input", or None for default
        """
        return None

    def parse_output(
        self,
        ctx: SessionContext,
        result: CommandResult,
    ) -> tuple[bytes, int]:
        """Format subprocess output for the adapter return value.

        Override this to transform stdout/stderr into the adapter's
        return format (bytes, exit_code).

        Args:
            ctx: The session context
            result: Named tuple with stdout, stderr, exit_code, duration_s

        Returns:
            (formatted_output_bytes, exit_code)
        """
        return result.stdout, result.exit_code


async def run_cli(
    wrapper: CLIWrapper,
    ctx: SessionContext,
    subcommand: str,
    *,
    timeout: float = 300.0,
    check: bool = True,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    capture_output: bool = True,
    **params: Any,
) -> CommandResult:
    """Run a CLI command via a CLIWrapper and return the result.

    This is the standard way to execute a subprocess from inside an adapter.
    It handles asyncio subprocess management, timeout, and error propagation.

    Args:
        wrapper: The CLIWrapper instance defining the command
        ctx: Session context (used to build the command)
        subcommand: The subcommand name
        timeout: Seconds before the process is killed (default 300)
        check: Raise CalledProcessError on non-zero exit (default True)
        env: Additional env vars for the subprocess
        cwd: Working directory for the subprocess
        capture_output: Capture stdout/stderr (default True)
        **params: Forwarded to wrapper.get_command()

    Returns:
        CommandResult with stdout, stderr, exit_code, duration_s

    Raises:
        asyncio.TimeoutError: If timeout is exceeded
        subprocess.CalledProcessError: If check=True and exit_code != 0
    """
    import subprocess
    import time

    argv = wrapper.get_command(ctx, subcommand, **params)
    start = time.monotonic()

    process_env = dict(env) if env else None

    proc = await asyncio.create_subprocess_exec(
        argv[0],
        *argv[1:],
        stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        env=process_env,
        cwd=cwd,
    )

    stdout_bytes: bytes
    stderr_bytes: bytes
    try:
        if capture_output:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        else:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            stdout_bytes = b""
            stderr_bytes = b""
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise asyncio.TimeoutError(f"Command {' '.join(argv)} timed out after {timeout}s") from None

    duration = time.monotonic() - start

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, argv, stdout_bytes, stderr_bytes)

    return CommandResult(
        stdout=stdout_bytes,
        stderr=stderr_bytes,
        exit_code=proc.returncode or 0,
        duration_s=round(duration, 3),
    )


async def stream_cli(
    wrapper: CLIWrapper,
    ctx: SessionContext,
    subcommand: str,
    *,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    on_stdout: Callable[[bytes], None] | None = None,
    on_stderr: Callable[[bytes], None] | None = None,
    **params: Any,
) -> tuple[bytes, int]:
    """Run a CLI command with streaming output.

    Unlike ``run_cli()``, this yields stdout/stderr chunks in real time
    via callbacks.  Use this for long-running commands where you want
    incremental output (e.g., build progress).

    Args:
        wrapper: The CLIWrapper instance
        ctx: Session context
        subcommand: Subcommand name
        timeout: Seconds before the process is killed
        env: Additional env vars for the subprocess
        cwd: Working directory
        on_stdout: Called with each stdout chunk as it arrives
        on_stderr: Called with each stderr chunk as it arrives
        **params: Forwarded to wrapper.get_command()

    Yields:
        (stdout_chunks_joined, exit_code) when the process completes

    Raises:
        asyncio.TimeoutError: If timeout is exceeded
    """
    import time

    argv = wrapper.get_command(ctx, subcommand, **params)
    start = time.monotonic()

    process_env = dict(env) if env else None

    proc = await asyncio.create_subprocess_exec(
        argv[0],
        *argv[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=process_env,
        cwd=cwd,
    )

    accumulated_stdout = bytearray()

    async def read_stream(
        reader: asyncio.StreamReader,
        callback: Callable[[bytes], None] | None,
    ) -> bytes:
        chunks = []
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            chunks.append(chunk)
            if callback:
                callback(chunk)
        return b"".join(chunks)

    stdout_task = asyncio.create_task(read_stream(proc.stdout, on_stdout))
    stderr_task = asyncio.create_task(read_stream(proc.stderr, on_stderr))

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise asyncio.TimeoutError(f"Command {' '.join(argv)} timed out after {timeout}s") from None

    stdout_bytes = await stdout_task
    accumulated_stdout.extend(stdout_bytes)
    stderr_bytes = await stderr_task

    duration = time.monotonic() - start
    exit_code = proc.returncode or 0

    parsed, _ = wrapper.parse_output(
        ctx,
        CommandResult(
            stdout=bytes(accumulated_stdout),
            stderr=stderr_bytes,
            exit_code=exit_code,
            duration_s=round(duration, 3),
        ),
    )

    return parsed, exit_code


@dataclass
class ActivityState:
    """Represents a detected activity state from terminal output."""

    state: str
    timestamp: float | None = None
    detail: str | None = None


def detect_activity_from_output(output: str) -> str | None:
    """Default activity detection from terminal output.

    Detects:
    - Shell prompts (muku@, $, >) → idle
    - Permission/confirmation prompts → waiting_input
    - Everything else → active

    Override ``CLIWrapper.detect_activity()`` for custom behavior.
    """
    import re

    if not output.strip():
        return "idle"

    lines = output.strip().split("\n")
    last_line = lines[-1].strip() if lines else ""

    if re.match(r"^[>$#]\s*$", last_line):
        return "idle"
    if re.search(r"\(Y\)es.*\(N\)o", output, re.IGNORECASE):
        return "waiting_input"
    if re.search(r"approval required", output, re.IGNORECASE):
        return "waiting_input"
    if re.search(r"Do you want to proceed\?", output, re.IGNORECASE):
        return "waiting_input"
    if re.search(r"Allow .+\?", output):
        return "waiting_input"
    return "active"


def shell_escape(s: str) -> str:
    """Escape a string for safe use in a shell command."""
    return shlex.quote(s)


def format_exit_code_note(command: str, exit_code: int) -> str | None:
    """Return a human-readable note for non-error exit codes.

    Many Unix commands use non-zero exits for informational purposes
    (grep exit 1 = no matches, diff exit 1 = files differ).
    This function returns a note when the exit code is not an error.

    Override in ``CLIWrapper.parse_output()`` to add command-specific notes.
    """
    import re

    if exit_code == 0:
        return None

    segments = re.split(r"\s*(?:\|\||&&|[|;])\s*", command)
    last_segment = (segments[-1] if segments else command).strip()
    words = last_segment.split()
    base_cmd = ""
    for w in words:
        if "=" in w and not w.startswith("-"):
            continue
        base_cmd = w.split("/")[-1]
        break

    if not base_cmd:
        return None

    semantics = {
        "grep": {1: "No matches found (not an error)"},
        "rg": {1: "No matches found (not an error)"},
        "diff": {1: "Files differ (expected, not an error)"},
        "find": {1: "Some directories inaccessible (partial results may be valid)"},
        "test": {1: "Condition evaluated to false (expected, not an error)"},
        "curl": {
            6: "Could not resolve host",
            7: "Failed to connect to host",
            22: "HTTP error response (e.g. 404, 500)",
            28: "Request timed out",
        },
    }

    cmd_semantics = semantics.get(base_cmd)
    if cmd_semantics and exit_code in cmd_semantics:
        return cmd_semantics[exit_code]

    return None


# ---------------------------------------------------------------------------
# PassthroughCLI — wildcard command wrapper for generic CLI tools
# ---------------------------------------------------------------------------


@dataclass
class PassthroughCLI:
    """Thin wrapper for CLI tools that just pass through arguments.

    Use this for wrapping large CLIs (git, kubectl, gimp) where you don't want
    to define every subcommand. The adapter declares ``"*"`` as its only command,
    and this class handles the passthrough logic.

    Usage::

        from chaitya_sdk.wrappers import PassthroughCLI
        from chaitya_sdk import adapter, ChaityaStream, SessionContext

        git = PassthroughCLI(
            name="git",
            blocked=["push", "reset --hard", "clean -fd"],
            overrides={
                "clone": _handle_clone,   # custom handler for specific subcommand
            },
        )

        @adapter(**git.build_contract())
        async def git_handler(stream: ChaityaStream, ctx: SessionContext):
            return await git.passthrough(ctx)

    The ``blocked`` list blocks specific subcommands (exact match or prefix).
    The ``overrides`` dict lets you add custom logic for specific subcommands
    before falling through to passthrough.

    Architecture (PRD §3.3):
        - The kernel handles tmux via SessionRunner
        - This class only builds the command string and calls SessionRunner
        - No tmux calls in the adapter
    """

    name: str = ""
    description: str = ""
    blocked: list[str] = field(default_factory=list)
    overrides: dict[str, Callable[..., Awaitable[tuple[bytes, int]]]] = field(default_factory=dict)
    pass_env: list[str] = field(default_factory=list)
    default_session: str = "default"
    completion_patterns: list[Any] = field(default_factory=list)
    timeout: float = 300.0

    def is_blocked(self, subcommand: str, raw_args: list[str]) -> bool:
        """Return True if the subcommand/args combination is blocked."""
        full = f"{subcommand} {' '.join(raw_args)}"
        for blocked in self.blocked:
            if blocked == subcommand or full.startswith(blocked):
                return True
        return False

    def build_contract(self) -> dict[str, Any]:
        """Return the adapter contract dict with ``"*"`` as the wildcard command."""
        override_commands = [
            {
                "name": sub,
                "description": f"Override handler for {self.name} {sub}",
                "examples": [f"chaitya {self.name} {sub} --help"],
            }
            for sub in sorted(self.overrides.keys())
        ]
        wildcard = [
            {
                "name": "*",
                "description": "Wildcard: passes all other subcommands directly to the CLI.",
                "examples": [
                    f"chaitya {self.name} status",
                    f"chaitya {self.name} log --oneline -5",
                ],
            }
        ]
        return {
            "name": self.name,
            "description": self.description or f"Wrapper for the {self.name} CLI tool.",
            "commands": override_commands + wildcard,
            "permissions": {
                "fs_read": ["."],
                "fs_write": ["."],
                "network": True,
                "can_emit_events": True,
            },
        }

    async def passthrough(
        self,
        ctx: SessionContext,
        *,
        extra_args: list[str] | None = None,
    ) -> tuple[bytes, int]:
        """Execute the CLI with raw args passed through.

        1. Checks blocklist
        2. Runs override handler if exists
        3. Otherwise: builds command string from raw args and runs via SessionRunner
        4. Returns (output_bytes, exit_code)
        """
        sub = str(ctx.args.get("subcommand", "*"))
        raw_args: list[str] = list(ctx.args.get("__raw_args__", []))

        if extra_args:
            raw_args = list(extra_args) + raw_args

        if self.is_blocked(sub, raw_args):
            return (
                f"[blocked] '{self.name} {sub}' is disabled for safety.\n".encode(),
                1,
            )

        if sub in self.overrides:
            return await self.overrides[sub](ctx)

        return await self._run_passthrough(ctx, raw_args)

    async def _run_passthrough(
        self,
        ctx: SessionContext,
        raw_args: list[str],
    ) -> tuple[bytes, int]:
        """Build and run the passthrough command via SessionRunner."""
        import re

        from chaitya_sdk.session import SessionRunner

        session_name = str(ctx.args.get("session") or self.default_session)
        cmd_str = " ".join([self.name, *raw_args]) if raw_args else self.name
        patterns = self.completion_patterns or [
            re.compile(r"muku@"),
            re.compile(r"\[exit:"),
            re.compile(r"\$ "),
            re.compile(r">\s*$"),
        ]

        runner = SessionRunner(self.name, session_name, timeout=self.timeout)
        try:
            output, _elapsed = await runner.run(cmd_str, completion_patterns=patterns)
            return output.encode("utf-8"), 0
        except Exception as exc:
            return f"[error] {self.name}: {exc}\n".encode(), 1
