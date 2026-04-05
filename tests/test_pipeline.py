"""Tests for chaitya.core.pipeline — chain parsing, framing, L2, execution."""

from __future__ import annotations

import pytest

from chaitya.core.pipeline import (
    PipelineOrchestrator,
    apply_l2,
    decode_frame,
    encode_frame,
    parse_chain,
    strip_ansi,
    L2_MAX_LINES,
    L2_MAX_BYTES,
)
from chaitya.core.protocols import PipelineOrchestratorProtocol
from chaitya.core.types import (
    ChaityaStream,
    CommandOutput,
    OutputChunk,
    PipelineContext,
    PipelineOperator,
    StreamHeader,
)


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_orchestrator_satisfies_protocol(self) -> None:
        assert isinstance(PipelineOrchestrator(), PipelineOrchestratorProtocol)


# ---------------------------------------------------------------------------
# Stream Framing
# ---------------------------------------------------------------------------


class TestStreamFraming:
    def test_encode_decode_roundtrip(self) -> None:
        header = StreamHeader(type="text/plain", encoding="utf-8", size=5)
        payload = b"hello"
        frame = encode_frame(header, payload)
        decoded_header, decoded_payload = decode_frame(frame)
        assert decoded_header.type == "text/plain"
        assert decoded_header.encoding == "utf-8"
        assert decoded_header.size == 5
        assert decoded_payload == b"hello"

    def test_encode_decode_binary(self) -> None:
        header = StreamHeader(type="application/octet-stream", stream=True)
        payload = b"\x00\x01\x02\xff"
        frame = encode_frame(header, payload)
        h, p = decode_frame(frame)
        assert h.type == "application/octet-stream"
        assert h.stream is True
        assert p == payload

    def test_encode_no_size(self) -> None:
        header = StreamHeader()
        frame = encode_frame(header, b"data")
        h, p = decode_frame(frame)
        assert h.size is None
        assert p == b"data"

    def test_decode_malformed_no_newline(self) -> None:
        with pytest.raises(ValueError, match="no header/payload separator"):
            decode_frame(b"no-newline-here")

    def test_decode_malformed_bad_json(self) -> None:
        with pytest.raises(ValueError, match="Malformed frame header"):
            decode_frame(b"not-json\npayload")


# ---------------------------------------------------------------------------
# Chain Parser
# ---------------------------------------------------------------------------


class TestChainParser:
    def test_single_command(self) -> None:
        chain = parse_chain("git status")
        assert len(chain.steps) == 1
        cmd, op = chain.steps[0]
        assert cmd.adapter == "git"
        assert cmd.subcommand == "status"
        assert op is None

    def test_pipe_operator(self) -> None:
        chain = parse_chain("a list | b filter")
        assert len(chain.steps) == 2
        assert chain.steps[0][1] == PipelineOperator.PIPE
        assert chain.steps[1][1] is None

    def test_and_operator(self) -> None:
        chain = parse_chain("a build && b deploy")
        assert chain.steps[0][1] == PipelineOperator.AND

    def test_or_operator(self) -> None:
        chain = parse_chain("a try || b fallback")
        assert chain.steps[0][1] == PipelineOperator.OR

    def test_sequence_operator(self) -> None:
        chain = parse_chain("a first ; b second")
        assert chain.steps[0][1] == PipelineOperator.SEQUENCE

    def test_mixed_operators(self) -> None:
        chain = parse_chain("a x | b y && c z || d w ; e v")
        assert len(chain.steps) == 5
        ops = [op for _, op in chain.steps]
        assert ops == [
            PipelineOperator.PIPE,
            PipelineOperator.AND,
            PipelineOperator.OR,
            PipelineOperator.SEQUENCE,
            None,
        ]

    def test_command_with_args(self) -> None:
        chain = parse_chain("git commit --message=hello --verbose")
        cmd = chain.steps[0][0]
        assert cmd.adapter == "git"
        assert cmd.subcommand == "commit"
        assert cmd.args["message"] == "hello"
        assert cmd.args["verbose"] is True

    def test_command_with_key_value_args(self) -> None:
        chain = parse_chain("fs read --path /tmp/file")
        cmd = chain.steps[0][0]
        assert cmd.args["path"] == "/tmp/file"

    def test_quoted_args(self) -> None:
        chain = parse_chain('git commit --message "hello world"')
        cmd = chain.steps[0][0]
        assert cmd.args["message"] == "hello world"

    def test_empty_expression_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty pipeline"):
            parse_chain("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty pipeline"):
            parse_chain("   ")

    def test_adapter_only(self) -> None:
        chain = parse_chain("info")
        cmd = chain.steps[0][0]
        assert cmd.adapter == "info"
        assert cmd.subcommand == ""
        assert cmd.raw_args == []


# ---------------------------------------------------------------------------
# ANSI Stripping
# ---------------------------------------------------------------------------


class TestStripAnsi:
    def test_removes_color_codes(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_removes_cursor_codes(self) -> None:
        assert strip_ansi("\x1b[2Jclear\x1b[H") == "clear"

    def test_removes_carriage_return(self) -> None:
        assert strip_ansi("line\r\n") == "line\n"

    def test_plain_text_unchanged(self) -> None:
        assert strip_ansi("hello world") == "hello world"


# ---------------------------------------------------------------------------
# L2 Presentation
# ---------------------------------------------------------------------------


class TestL2Presentation:
    def test_plain_text_passthrough(self) -> None:
        result = apply_l2(b"hello", b"", exit_code=0)
        assert result.processed == "hello\n[exit:0 | 0ms]"
        assert result.exit_code == 0

    def test_binary_guard(self) -> None:
        result = apply_l2(b"hello\x00world", b"", exit_code=0)
        assert "binary content" in result.processed
        assert "output.bin" in result.processed

    def test_ansi_stripped(self) -> None:
        result = apply_l2(b"\x1b[31mred\x1b[0m text", b"", exit_code=0)
        assert result.processed == "red text\n[exit:0 | 0ms]"

    def test_stderr_attached_on_error(self) -> None:
        result = apply_l2(b"out", b"err msg", exit_code=1)
        assert "[stderr] err msg" in result.processed
        assert "[exit:1 |" in result.processed

    def test_stderr_not_attached_on_success(self) -> None:
        result = apply_l2(b"out", b"err msg", exit_code=0)
        assert "[stderr]" not in result.processed
        assert "[exit:0 |" in result.processed

    def test_overflow_truncation(self) -> None:
        many_lines = b"\n".join(f"line {i}".encode() for i in range(300))
        result = apply_l2(many_lines, b"", exit_code=0)
        assert "[truncated" in result.processed
        assert "300 lines total" in result.processed
        assert "[exit:0 |" in result.processed

    def test_session_id_in_output(self) -> None:
        result = apply_l2(b"data", b"", exit_code=0, session_id="s1")
        assert result.session_id == "s1"

    def test_duration_recorded(self) -> None:
        result = apply_l2(b"data", b"", exit_code=0, duration_ms=42)
        assert result.duration_ms == 42
        assert "[exit:0 | 42ms]" in result.processed

    def test_empty_output(self) -> None:
        result = apply_l2(b"", b"", exit_code=0)
        assert result.processed == "\n[exit:0 | 0ms]"
        assert result.exit_code == 0

    def test_metadata_footer_format(self) -> None:
        result = apply_l2(b"data", b"", exit_code=5, duration_ms=123)
        assert result.processed.endswith("[exit:5 | 123ms]")


# ---------------------------------------------------------------------------
# Pipeline Execution
# ---------------------------------------------------------------------------


async def _echo_handler(input_stream: ChaityaStream, ctx: PipelineContext) -> tuple[bytes, int]:
    """Test handler that echoes 'echo' or the input content."""
    if input_stream.content:
        return input_stream.content, 0
    return b"echo-output", 0


async def _fail_handler(input_stream: ChaityaStream, ctx: PipelineContext) -> tuple[bytes, int]:
    """Test handler that always fails."""
    return b"fail-output", 1


async def _upper_handler(input_stream: ChaityaStream, ctx: PipelineContext) -> tuple[bytes, int]:
    """Test handler that uppercases input."""
    return input_stream.content.upper(), 0


async def _error_handler(input_stream: ChaityaStream, ctx: PipelineContext) -> tuple[bytes, int]:
    """Test handler that raises an exception."""
    raise RuntimeError("boom")


async def _collect_chunks(orch: PipelineOrchestrator, expr: str) -> list[OutputChunk]:
    """Helper to collect all output chunks from a pipeline execution."""
    chain = orch.parse_chain(expr)
    ctx = PipelineContext(session_id="test")
    chunks = []
    async for chunk in orch.execute(chain, ctx):
        chunks.append(chunk)
    return chunks


class TestPipelineExecution:
    def _make_orchestrator(self) -> PipelineOrchestrator:
        orch = PipelineOrchestrator()
        orch.register_handler("echo", _echo_handler)
        orch.register_handler("fail", _fail_handler)
        orch.register_handler("upper", _upper_handler)
        orch.register_handler("error", _error_handler)
        return orch

    async def test_single_command(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "echo run")
        assert len(chunks) == 2  # intermediate + final
        assert chunks[-1].is_final is True
        assert b"echo-output" in chunks[-1].data

    async def test_pipe_feeds_output_to_next(self) -> None:
        orch = self._make_orchestrator()
        # echo produces b"echo-output", upper uppercases it
        chunks = await _collect_chunks(orch, "echo run | upper run")
        final = chunks[-1]
        assert final.is_final
        assert b"ECHO-OUTPUT" in final.data

    async def test_and_stops_on_failure(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "fail run && echo run")
        final = chunks[-1]
        assert final.is_final
        # echo should NOT have run — only fail output
        assert b"echo-output" not in final.data

    async def test_and_continues_on_success(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "echo run && echo run")
        final = chunks[-1]
        assert final.is_final
        assert b"echo-output" in final.data

    async def test_or_skips_on_success(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "echo run || fail run")
        final = chunks[-1]
        # echo succeeded, fail should not have run
        assert b"fail-output" not in final.data

    async def test_or_continues_on_failure(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "fail run || echo run")
        final = chunks[-1]
        assert b"echo-output" in final.data

    async def test_sequence_always_runs(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "fail run ; echo run")
        final = chunks[-1]
        assert b"echo-output" in final.data

    async def test_unknown_adapter_yields_error(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "nonexistent run")
        # Should have error stderr chunk + final
        stderr_chunks = [c for c in chunks if c.is_stderr]
        assert len(stderr_chunks) >= 1
        assert b"[error] unknown adapter" in stderr_chunks[0].data

    async def test_handler_exception_caught(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "error run")
        stderr_chunks = [c for c in chunks if c.is_stderr]
        assert len(stderr_chunks) >= 1
        assert b"boom" in stderr_chunks[0].data

    async def test_l2_applied_exactly_once(self) -> None:
        orch = self._make_orchestrator()
        chunks = await _collect_chunks(orch, "echo run")
        final_chunks = [c for c in chunks if c.is_final]
        assert len(final_chunks) == 1

    async def test_register_unregister(self) -> None:
        orch = PipelineOrchestrator()
        orch.register_handler("test", _echo_handler)
        assert "test" in orch._handlers
        orch.unregister_handler("test")
        assert "test" not in orch._handlers
