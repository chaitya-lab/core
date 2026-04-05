"""Pipeline Orchestrator — chain parsing, L0→L1→L2 execution, stream framing.

Default implementation of the PipelineOrchestratorProtocol. Ships with the
kernel and handles all standard pipeline cases.

Reference: PRD §3.5, §4, §13
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from chaitya.core.types import (
    ChaityaStream,
    CommandChain,
    CommandOutput,
    OutputChunk,
    PipelineCommand,
    PipelineContext,
    PipelineOperator,
    StreamHeader,
)

logger = logging.getLogger("chaitya.core.pipeline")

# ---------------------------------------------------------------------------
# Stream Framing (PRD §3.5)
# ---------------------------------------------------------------------------


def encode_frame(header: StreamHeader, payload: bytes) -> bytes:
    """Encode a stream frame: JSON header line + payload.

    Format:
        {"type":"text/plain","encoding":"utf-8","stream":false}\n
        [payload bytes]
    """
    header_dict: dict[str, Any] = {
        "type": header.type,
        "encoding": header.encoding,
        "stream": header.stream,
    }
    if header.size is not None:
        header_dict["size"] = header.size
    header_line = json.dumps(header_dict, separators=(",", ":")).encode("utf-8")
    return header_line + b"\n" + payload


def decode_frame(data: bytes) -> tuple[StreamHeader, bytes]:
    """Decode a stream frame into (header, payload).

    Raises ValueError if the frame is malformed.
    """
    newline_pos = data.find(b"\n")
    if newline_pos == -1:
        raise ValueError("Malformed frame: no header/payload separator")
    header_raw = data[:newline_pos]
    payload = data[newline_pos + 1 :]
    try:
        header_dict = json.loads(header_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed frame header: {exc}") from exc
    return StreamHeader(
        type=header_dict.get("type", "text/plain"),
        encoding=header_dict.get("encoding", "utf-8"),
        size=header_dict.get("size"),
        stream=header_dict.get("stream", False),
    ), payload


# ---------------------------------------------------------------------------
# Chain Parser (PRD §3.5)
# ---------------------------------------------------------------------------

# Operator tokens in descending length order (longest match first)
_OPERATOR_MAP: dict[str, PipelineOperator] = {
    "&&": PipelineOperator.AND,
    "||": PipelineOperator.OR,
    "|": PipelineOperator.PIPE,
    ";": PipelineOperator.SEQUENCE,
}

# Regex to split on operators while preserving them
# Matches: ||, &&, |, ; — with optional surrounding whitespace
_SPLIT_RE = re.compile(r"\s*(&&|\|\||\||\;)\s*")


def _parse_command_token(token: str) -> PipelineCommand:
    """Parse a single command token into a PipelineCommand.

    Expected format: <adapter> <subcommand> [args...]
    Args are split by shell-like rules (simplified: whitespace splitting
    with basic quoted-string support).
    """
    token = token.strip()
    if not token:
        raise ValueError("Empty command in pipeline expression")

    parts = _shell_split(token)
    if not parts:
        raise ValueError("Empty command in pipeline expression")

    adapter = parts[0]
    # Subcommand: first non-flag token that follows a non-flag token
    # (skip --flags and their values; positional args that follow flags are args)
    subcommand = ""
    raw_args: list[str] = []
    skip_next = False
    for i, part in enumerate(parts[1:]):
        if skip_next:
            skip_next = False
            continue
        if part.startswith("--"):
            raw_args.append(part)
            # Check if next token is a flag value (doesn't start with --)
            if i + 2 < len(parts):
                next_tok = parts[i + 2]
                if not next_tok.startswith("--"):
                    # Quoted string consumed as flag value — don't add to raw_args,
                    # handle in extraction loop directly
                    if next_tok.startswith('"') or next_tok.startswith("'"):
                        skip_next = True
                    else:
                        raw_args.append(next_tok)
                        skip_next = True
            continue
        # Non-flag token
        if not subcommand:
            subcommand = part
        else:
            raw_args.append(part)

    # Parse --key=value and --key value pairs into dict
    args: dict[str, Any] = {}
    i = 0
    while i < len(raw_args):
        arg = raw_args[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                args[k] = v
            elif i + 1 < len(raw_args):
                next_arg = raw_args[i + 1]
                if next_arg.startswith('"') or next_arg.startswith("'"):
                    args[key] = next_arg
                    i += 1
                elif not next_arg.startswith("--"):
                    args[key] = next_arg
                    i += 1
                else:
                    args[key] = True
            else:
                args[key] = True
        i += 1

    return PipelineCommand(
        adapter=adapter,
        subcommand=subcommand,
        args=args,
        raw_args=raw_args,
    )


def _shell_split(s: str) -> list[str]:
    """Simple shell-like tokenizer.

    Handles double-quoted strings and single-quoted strings.
    Does NOT handle escapes within quotes (kept simple).
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for ch in s:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            else:
                current.append(ch)
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == " " or ch == "\t":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)

    if current:
        tokens.append("".join(current))
    return tokens


def parse_chain(expression: str) -> CommandChain:
    """Parse a pipeline expression into a CommandChain.

    Examples:
        "adapter sub --flag"
            → single command, no operator
        "a sub1 | b sub2"
            → two commands joined by PIPE
        "a sub1 && b sub2 || c sub3 ; d sub4"
            → four commands with AND, OR, SEQUENCE operators
    """
    expression = expression.strip()
    if not expression:
        raise ValueError("Empty pipeline expression")

    # Split on operators, keeping the operator tokens
    parts = _SPLIT_RE.split(expression)

    # parts alternates: [cmd, op, cmd, op, cmd, ...]
    steps: list[tuple[PipelineCommand, PipelineOperator | None]] = []
    i = 0
    while i < len(parts):
        cmd_str = parts[i].strip()
        if not cmd_str:
            i += 1
            continue

        cmd = _parse_command_token(cmd_str)

        # Check for operator after this command
        if i + 1 < len(parts):
            op_str = parts[i + 1].strip()
            op = _OPERATOR_MAP.get(op_str)
            if op is not None:
                steps.append((cmd, op))
                i += 2
                continue

        # Last command — no operator
        steps.append((cmd, None))
        i += 1

    if not steps:
        raise ValueError("No commands found in pipeline expression")

    return CommandChain(steps=steps)


# ---------------------------------------------------------------------------
# Adapter Command Handler Type
# ---------------------------------------------------------------------------

# Type for adapter command execution functions.
# Adapters register these with the kernel. The pipeline calls them.
AdapterHandler = Callable[
    [ChaityaStream, "PipelineContext"],
    Awaitable[tuple[bytes, int]],
]
"""Signature: async (input_stream, ctx) -> (output_bytes, exit_code)"""


# ---------------------------------------------------------------------------
# L2 Presentation (PRD §4, L2)
# ---------------------------------------------------------------------------

# ANSI escape code pattern
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\r")

# Binary content detection: presence of null bytes
_NULL_BYTE = b"\x00"

# L2 limits
L2_MAX_LINES = 200
L2_MAX_BYTES = 50 * 1024  # 50 KB


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and carriage returns."""
    return _ANSI_RE.sub("", text)


def apply_l2(
    raw_output: bytes,
    stderr_output: bytes,
    exit_code: int,
    *,
    output_type: str = "text/plain",
    session_id: str | None = None,
    duration_ms: int = 0,
    overflow_dir: str | None = None,
) -> CommandOutput:
    """Apply L2 presentation rules to raw command output.

    Rules (PRD §4, L2):
    1. Binary guard: binary content → error with redirect suggestion
    2. ANSI strip: remove all escape codes
    3. Overflow: >200 lines or >50KB → truncate, write full to file
    4. Stderr attachment: non-zero exit + stderr → append [stderr]
    5. Metadata footer: [exit:{code} | {duration}ms] as last line (PRD §4)
    """
    # Binary guard — use output_type hint first, null byte detection as fallback
    is_binary_type = (
        output_type.startswith("image/")
        or output_type.startswith("audio/")
        or output_type.startswith("video/")
        or output_type
        in (
            "application/octet-stream",
            "application/pdf",
            "application/zip",
            "application/gzip",
        )
    )
    if is_binary_type or _NULL_BYTE in raw_output:
        hint = f"[{output_type}] " if is_binary_type else ""
        return CommandOutput(
            raw="[binary content]",
            processed=(
                f"[error] {hint}binary data ({len(raw_output):,} bytes). "
                f"LLMs cannot read binary. Use: chaitya file read --path <file>\n"
                f"[exit:{exit_code} | {duration_ms}ms]"
            ),
            exit_code=exit_code,
            duration_ms=duration_ms,
            type=output_type,
            session_id=session_id,
        )

    # Decode and strip ANSI
    try:
        text = raw_output.decode("utf-8", errors="replace")
    except Exception:
        text = raw_output.decode("latin-1")

    text = strip_ansi(text)

    # Overflow check
    overflow_path: str | None = None
    lines = text.splitlines(keepends=True)
    is_overflow = len(lines) > L2_MAX_LINES or len(raw_output) > L2_MAX_BYTES

    if is_overflow and overflow_dir:
        import os

        os.makedirs(overflow_dir, exist_ok=True)
        ts = int(time.time() * 1000)
        overflow_path = os.path.join(
            overflow_dir,
            f"output-{session_id or 'none'}-{ts}.txt",
        )
        with open(overflow_path, "w", encoding="utf-8") as f:
            f.write(text)

    if is_overflow:
        truncated = "".join(lines[:L2_MAX_LINES])
        suffix = f"\n[truncated — {len(lines)} lines total]"
        if overflow_path:
            suffix += f"\n[full output: {overflow_path}]"
        text = truncated + suffix

    processed = text

    # Stderr attachment
    if exit_code != 0 and stderr_output:
        stderr_text = strip_ansi(stderr_output.decode("utf-8", errors="replace"))
        processed += f"\n[stderr] {stderr_text}"

    # Metadata footer (PRD §4: always last line)
    processed += f"\n[exit:{exit_code} | {duration_ms}ms]"

    return CommandOutput(
        raw=raw_output.decode("utf-8", errors="replace"),
        processed=processed,
        exit_code=exit_code,
        duration_ms=duration_ms,
        type=output_type,
        stderr=stderr_output.decode("utf-8", errors="replace") if stderr_output else "",
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Pipeline Orchestrator (PRD §3.5, §13)
# ---------------------------------------------------------------------------


class PipelineOrchestrator:
    """Default pipeline orchestrator implementation.

    Manages L0 → L1 → L2 data flow. Parses and executes command chains.
    Dispatches to registered adapter handlers. Applies L2 exactly once
    after the full chain completes.

    The orchestrator does NOT interpret commands. It connects pipes.
    Conditional routing is handled by a `route` adapter, not here.
    """

    def __init__(
        self,
        *,
        overflow_dir: str | None = None,
        registry: Any = None,
    ) -> None:
        self._handlers: dict[str, AdapterHandler] = {}
        self._overflow_dir = overflow_dir
        self._registry = registry

    def register_handler(self, adapter: str, handler: AdapterHandler) -> None:
        """Register an adapter command handler for pipeline dispatch."""
        self._handlers[adapter] = handler
        logger.debug("Registered pipeline handler for adapter %r", adapter)

    def _get_output_type(self, chain: CommandChain) -> str:
        """Look up the output type from the first command's contract.

        Checks per-command output_type first, falls back to adapter
        default_output_type, then 'text/plain'.
        """
        if not chain.steps:
            return "text/plain"
        cmd, _ = chain.steps[0]
        if self._registry is None:
            return "text/plain"
        pkg = self._registry.get_adapter(cmd.adapter)
        if pkg is None or pkg.contract is None:
            return "text/plain"
        contract = pkg.contract
        subcommand = cmd.subcommand
        if subcommand:
            for cmd_spec in getattr(contract, "commands", []):
                if cmd_spec.name == subcommand:
                    ot = getattr(cmd_spec, "output_type", None)
                    if ot:
                        return ot
        return getattr(contract, "default_output_type", "text/plain")

    def unregister_handler(self, adapter: str) -> None:
        """Remove an adapter handler."""
        self._handlers.pop(adapter, None)

    def parse_chain(self, expression: str) -> CommandChain:
        """Parse a command expression into a structured chain."""
        return parse_chain(expression)

    async def run(self, chain: CommandChain, ctx: PipelineContext) -> CommandOutput:
        """Execute a command chain and return the final L2 output."""
        if not chain.steps:
            return CommandOutput()

        # Build initial input stream from context
        current_input = ctx.input_stream or ChaityaStream()
        accumulated_stdout = bytearray()
        accumulated_stderr = bytearray()
        last_exit_code = 0
        start_time = time.monotonic()
        skip_next = False

        for cmd, operator in chain.steps:
            # If a previous OR short-circuited, skip this command
            if skip_next:
                skip_next = False
                continue

            handler = self._handlers.get(cmd.adapter)
            if handler is None:
                available = ", ".join(sorted(self._handlers)) if self._handlers else "(none)"
                err_msg = (
                    f"[error] unknown adapter: {cmd.adapter}\n"
                    f"Available: {available}\n"
                    f"Try: chaitya info\n"
                ).encode()
                accumulated_stderr.extend(err_msg)
                last_exit_code = 127
                if operator in (PipelineOperator.AND, PipelineOperator.PIPE):
                    break
                continue

            step_ctx = PipelineContext(
                session_id=ctx.session_id,
                input_stream=current_input,
                env=dict(ctx.env),
                dry_run=ctx.dry_run,
            )
            step_ctx.env["__adapter__"] = cmd.adapter
            step_ctx.env["__subcommand__"] = cmd.subcommand
            step_ctx.env["__args__"] = list(cmd.raw_args)
            step_ctx.env["exit_code"] = str(last_exit_code)
            step_ctx.env.update(cmd.args)

            try:
                output_bytes, exit_code = await handler(current_input, step_ctx)
            except Exception as exc:
                err_msg = f"Pipeline error in {cmd.adapter}.{cmd.subcommand}: {exc}"
                logger.error(err_msg)
                err_bytes = err_msg.encode("utf-8")
                accumulated_stderr.extend(err_bytes)
                last_exit_code = 1
                if operator in (PipelineOperator.AND, PipelineOperator.PIPE):
                    break
                continue

            last_exit_code = exit_code
            accumulated_stdout.extend(output_bytes)

            if operator == PipelineOperator.AND and exit_code != 0:
                break
            if operator == PipelineOperator.OR and exit_code == 0:
                skip_next = True
                continue
            if operator == PipelineOperator.PIPE:
                current_input = ChaityaStream(
                    content=output_bytes,
                    declared_type="application/octet-stream",
                    size_bytes=len(output_bytes),
                    exit_code=last_exit_code,
                )
            elif operator in (PipelineOperator.SEQUENCE, PipelineOperator.OR):
                current_input = ctx.input_stream or ChaityaStream()

        duration_ms = int((time.monotonic() - start_time) * 1000)
        return apply_l2(
            raw_output=bytes(accumulated_stdout),
            stderr_output=bytes(accumulated_stderr),
            exit_code=last_exit_code,
            session_id=ctx.session_id,
            duration_ms=duration_ms,
            overflow_dir=self._overflow_dir,
            output_type=self._get_output_type(chain),
        )

    async def execute(
        self, chain: CommandChain, ctx: PipelineContext
    ) -> AsyncIterator[OutputChunk]:
        """Execute a command chain through L0 → L1 → L2.

        Operator semantics:
        - PIPE (|): stdout of left → stdin of right
        - AND (&&): run right only if left exits 0
        - OR (||): run right only if left exits non-zero
        - SEQUENCE (;): always run right regardless of left's exit code

        L2 is applied once after the full chain completes.
        """
        if not chain.steps:
            return

        current_input = ctx.input_stream or ChaityaStream()
        accumulated_stdout = bytearray()
        accumulated_stderr = bytearray()
        last_exit_code = 0
        start_time = time.monotonic()
        skip_next = False

        for cmd, operator in chain.steps:
            if skip_next:
                skip_next = False
                continue

            handler = self._handlers.get(cmd.adapter)
            if handler is None:
                available = ", ".join(sorted(self._handlers)) if self._handlers else "(none)"
                err_msg = (
                    f"[error] unknown adapter: {cmd.adapter}\n"
                    f"Available: {available}\n"
                    f"Try: chaitya info\n"
                ).encode()
                accumulated_stderr.extend(err_msg)
                last_exit_code = 127
                yield OutputChunk(data=err_msg, is_stderr=True)
                if operator in (PipelineOperator.AND, PipelineOperator.PIPE):
                    break
                continue

            step_ctx = PipelineContext(
                session_id=ctx.session_id,
                input_stream=current_input,
                env=dict(ctx.env),
                dry_run=ctx.dry_run,
            )
            step_ctx.env["__adapter__"] = cmd.adapter
            step_ctx.env["__subcommand__"] = cmd.subcommand
            step_ctx.env["__args__"] = list(cmd.raw_args)
            step_ctx.env["exit_code"] = str(last_exit_code)
            step_ctx.env.update(cmd.args)

            try:
                output_bytes, exit_code = await handler(current_input, step_ctx)
            except Exception as exc:
                err_msg = f"Pipeline error in {cmd.adapter}.{cmd.subcommand}: {exc}"
                logger.error(err_msg)
                err_bytes = err_msg.encode("utf-8")
                accumulated_stderr.extend(err_bytes)
                last_exit_code = 1
                yield OutputChunk(data=err_bytes, is_stderr=True)
                if operator in (PipelineOperator.AND, PipelineOperator.PIPE):
                    break
                continue

            last_exit_code = exit_code
            accumulated_stdout.extend(output_bytes)
            yield OutputChunk(data=output_bytes, is_stderr=False, is_final=False)

            if operator == PipelineOperator.AND and exit_code != 0:
                break
            if operator == PipelineOperator.OR and exit_code == 0:
                skip_next = True
                continue
            if operator == PipelineOperator.PIPE:
                current_input = ChaityaStream(
                    content=output_bytes,
                    declared_type="application/octet-stream",
                    size_bytes=len(output_bytes),
                    exit_code=last_exit_code,
                )
            elif operator in (PipelineOperator.SEQUENCE, PipelineOperator.OR):
                current_input = ctx.input_stream or ChaityaStream()

        duration_ms = int((time.monotonic() - start_time) * 1000)
        cmd_output = apply_l2(
            raw_output=bytes(accumulated_stdout),
            stderr_output=bytes(accumulated_stderr),
            exit_code=last_exit_code,
            session_id=ctx.session_id,
            duration_ms=duration_ms,
            overflow_dir=self._overflow_dir,
            output_type=self._get_output_type(chain),
        )

        yield OutputChunk(
            data=cmd_output.processed.encode("utf-8"),
            is_stderr=False,
            is_final=True,
        )
