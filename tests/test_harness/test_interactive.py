"""Interactive session tests with different terminal types.

This module tests interactive workflows with various terminal app types
(REPL, Markdown CLI, API CLI, Shell) to ensure the core handles
different TUI patterns correctly.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "src"))

from src.chaitya.core.kernel import Kernel
from tests.test_harness.fake_terminals import (
    FakeAPICLI,
    FakeGenericTerminal,
    FakeMarkdownCLI,
    FakeREPL,
    TerminalResponse,
)


@pytest.fixture
async def kernel():
    """Create and boot a fresh kernel for each test."""
    k = Kernel(
        db_path=":memory:",
        system_adapters=frozenset(),
    )
    await k.boot()
    yield k
    await k.shutdown()


# =============================================================================
# REPL Terminal Tests
# =============================================================================


class TestREPLTerminal:
    """Test REPL-style terminal patterns."""

    async def test_repl_thinking_blocks(self, kernel):
        """Thinking blocks work in REPL."""
        terminal = FakeREPL("repl-thinking")
        await terminal.start()

        response = await terminal.send("think: Let me analyze this problem")
        assert response.exit_code == 0
        assert "<thinking>" in response.output
        assert "analyze" in response.output.lower()

        assert any(e.type == "thinking_completed" for e in terminal.events)
        await terminal.stop()

    async def test_repl_command_execution(self, kernel):
        """Command execution with ! prefix works."""
        terminal = FakeREPL("repl-exec")
        await terminal.start()

        response = await terminal.send("!ls -la")
        assert response.exit_code == 0
        assert "Executed" in response.output or "ls" in response.output

        assert any(e.type == "command_executed" for e in terminal.events)
        await terminal.stop()

    async def test_repl_file_write(self, kernel):
        """File write operations work."""
        terminal = FakeREPL("repl-write")
        await terminal.start()

        response = await terminal.send("Write to test.txt")
        assert response.exit_code == 0
        assert "Wrote" in response.output or "✓" in response.output

        assert any(e.type == "file_written" for e in terminal.events)
        await terminal.stop()

    async def test_repl_error_handling(self, kernel):
        """Error handling works."""
        terminal = FakeREPL("repl-error")
        await terminal.start()

        response = await terminal.send("This contains an error")
        assert response.exit_code == 0

        assert any(e.type == "error" for e in terminal.events)
        await terminal.stop()

    async def test_repl_ansi_output(self, kernel):
        """ANSI escape sequences are in output."""
        terminal = FakeREPL("repl-ansi")
        await terminal.start()

        assert len(terminal._buffer) > 0

        await terminal.stop()

    async def test_repl_multi_turn(self, kernel):
        """Multi-turn conversations work."""
        terminal = FakeREPL("repl-multi")
        await terminal.start()

        r1 = await terminal.send("Hello!")
        assert r1.exit_code == 0

        r2 = await terminal.send("think: processing")
        assert "<thinking>" in r2.output

        assert len(terminal.events) >= 2
        await terminal.stop()


# =============================================================================
# API CLI Tests
# =============================================================================


class TestAPICLI:
    """Test API-style CLI patterns."""

    async def test_api_help(self, kernel):
        """Help command works."""
        terminal = FakeAPICLI("api-help")
        await terminal.start()

        response = await terminal.send("help")
        assert response.exit_code == 0
        assert "Commands" in response.output or "/complete" in response.output

        await terminal.stop()

    async def test_api_complete(self, kernel):
        """/complete API works."""
        terminal = FakeAPICLI("api-complete")
        await terminal.start()

        response = await terminal.send("/complete Write a function")
        assert response.exit_code == 0
        assert "API" in response.output or "completion" in response.output.lower()

        assert len(terminal._api_calls) == 1
        assert terminal._api_calls[0]["type"] == "complete"
        await terminal.stop()

    async def test_api_edit(self, kernel):
        """/edit command works."""
        terminal = FakeAPICLI("api-edit")
        await terminal.start()

        response = await terminal.send("/edit main.py")
        assert response.exit_code == 0
        assert "edit" in response.output.lower()

        await terminal.stop()

    async def test_api_call_tracking(self, kernel):
        """API calls are tracked."""
        terminal = FakeAPICLI("api-tracks")
        await terminal.start()

        await terminal.send("/complete call1")
        await terminal.send("/edit file.py")

        assert len(terminal._api_calls) >= 1
        await terminal.stop()


# =============================================================================
# Markdown CLI Tests
# =============================================================================


class TestMarkdownCLI:
    """Test markdown-based CLI patterns."""

    async def test_markdown_markdown(self, kernel):
        """Markdown formatting works."""
        terminal = FakeMarkdownCLI("md-format")
        await terminal.start()

        response = await terminal.send("What is this?")
        assert response.exit_code == 0
        assert "#" in response.output or "**" in response.output

        await terminal.stop()

    async def test_markdown_code_blocks(self, kernel):
        """Code blocks work."""
        terminal = FakeMarkdownCLI("md-code")
        await terminal.start()

        response = await terminal.send("```\ncode here\n```")
        assert response.exit_code == 0
        assert "```" in response.output

        assert any(e.type == "code_block" for e in terminal.events)
        await terminal.stop()

    async def test_markdown_task_execution(self, kernel):
        """Task execution with spinner works."""
        terminal = FakeMarkdownCLI("md-task")
        await terminal.start()

        response = await terminal.send("task: test task")
        assert response.exit_code == 0
        assert "Task" in response.output or "Done" in response.output

        assert any(e.type == "task_completed" for e in terminal.events)
        await terminal.stop()

    async def test_markdown_status_table(self, kernel):
        """Status table works."""
        terminal = FakeMarkdownCLI("md-status")
        await terminal.start()

        response = await terminal.send("status")
        assert response.exit_code == 0
        assert "|" in response.output  # Markdown table

        await terminal.stop()

    async def test_markdown_spinner_animation(self, kernel):
        """Spinner animation works."""
        terminal = FakeMarkdownCLI("md-spinner")
        await terminal.start()

        frames_seen = set()
        for _ in range(15):
            response = await terminal.send("task: long task")
            for spinner in terminal.SPINNERS:
                if spinner in response.output:
                    frames_seen.add(spinner)

        assert len(frames_seen) >= 2
        await terminal.stop()

    async def test_markdown_status_badges(self, kernel):
        """Status badges work."""
        terminal = FakeMarkdownCLI("md-badges")
        await terminal.start()

        response = await terminal.send("task: status check")
        assert response.exit_code == 0

        assert terminal.STATUS_BADGES["success"] in response.output
        await terminal.stop()


# =============================================================================
# Shell Terminal Tests
# =============================================================================


class TestShellTerminal:
    """Test simple shell patterns."""

    async def test_shell_basic(self, kernel):
        """Basic shell operations work."""
        terminal = FakeAPICLI("shell-test")
        await terminal.start()

        response = await terminal.send("test command")
        assert response.exit_code == 0

        await terminal.stop()


# =============================================================================
# Generic Terminal Tests
# =============================================================================


class TestGenericTerminal:
    """Test generic terminal with custom handlers."""

    async def test_generic_basic(self, kernel):
        """Generic terminal basic operations work."""
        terminal = FakeGenericTerminal("generic-basic")
        await terminal.start()

        response = await terminal.send("Hello world")
        assert response.exit_code == 0
        assert "Hello" in response.output or "world" in response.output

        await terminal.stop()

    async def test_generic_custom_handler(self, kernel):
        """Generic terminal custom handlers work."""
        terminal = FakeGenericTerminal("generic-custom")
        await terminal.start()

        async def handle_echo(text):
            return TerminalResponse(output=f"ECHO: {text}\n", exit_code=0)

        async def handle_reverse(text):
            return TerminalResponse(output=f"REVERSE: {text[::-1]}\n", exit_code=0)

        terminal.add_handler(r"^echo\s+(.*)$", handle_echo)
        terminal.add_handler(r"^reverse\s+(.*)$", handle_reverse)

        r1 = await terminal.send("echo Hello")
        assert "ECHO" in r1.output

        r2 = await terminal.send("reverse Hello")
        assert "REVERSE" in r2.output
        assert "olleH" in r2.output

        await terminal.stop()

    async def test_generic_fallback(self, kernel):
        """Generic terminal falls back for unknown patterns."""
        terminal = FakeGenericTerminal("generic-fallback")
        await terminal.start()

        response = await terminal.send("unknown command xyz")
        assert response.exit_code == 0
        assert "xyz" in response.output

        await terminal.stop()


# =============================================================================
# Cross-Terminal Tests
# =============================================================================


class TestCrossTerminalInteraction:
    """Test interactions between different terminal types."""

    async def test_different_terminal_types(self, kernel):
        """Different terminal types can be routed to sessions."""
        sessions = ["repl-session", "api-session", "markdown-session"]
        terminals = [
            FakeREPL(sessions[0]),
            FakeAPICLI(sessions[1]),
            FakeMarkdownCLI(sessions[2]),
        ]

        for terminal in terminals:
            await terminal.start()

        for terminal in terminals:
            await terminal.send("test command")

        assert all(len(t.events) > 0 for t in terminals)

        for terminal in terminals:
            await terminal.stop()

    async def test_terminal_event_isolation(self, kernel):
        """Events are isolated between terminals."""
        repl = FakeREPL("event-iso-repl")
        api = FakeAPICLI("event-iso-api")

        await repl.start()
        await api.start()

        await repl.send("!command1")
        await api.send("/complete prompt1")

        assert any(e.type == "command_executed" for e in repl.events)
        assert any(e.type == "api_call" for e in api.events)

        await repl.stop()
        await api.stop()

    async def test_terminal_output_format_differences(self, kernel):
        """Different terminals produce different output formats."""
        repl = FakeREPL("format-repl")
        api = FakeAPICLI("format-api")
        md = FakeMarkdownCLI("format-md")

        await repl.start()
        await api.start()
        await md.start()

        await repl.send("test")
        await api.send("test")
        await md.send("test")

        assert len(repl._buffer) > 0
        assert len(api._buffer) > 0
        assert len(md._buffer) > 0

        await repl.stop()
        await api.stop()
        await md.stop()


# =============================================================================
# Stress Tests
# =============================================================================


class TestTerminalStressTests:
    """Stress tests for terminal handling."""

    async def test_rapid_commands(self, kernel):
        """Rapid commands are handled."""
        terminal = FakeREPL("rapid")
        await terminal.start()

        for i in range(5):
            response = await terminal.send(f"!echo command {i}")
            assert response.exit_code == 0

        assert len(terminal.events) >= 5
        await terminal.stop()

    async def test_concurrent_terminals(self, kernel):
        """Concurrent terminals don't interfere."""
        terminals = [FakeREPL(f"concurrent-{i}") for i in range(5)]

        await asyncio.gather(*[t.start() for t in terminals])

        tasks = [t.send(f"command-{i}") for i, t in enumerate(terminals)]
        results = await asyncio.gather(*tasks)

        assert all(r.exit_code == 0 for r in results)

        for t in terminals:
            assert len(t.events) == 1

        await asyncio.gather(*[t.stop() for t in terminals])

    async def test_empty_input(self, kernel):
        """Empty input is handled."""
        terminal = FakeREPL("empty")
        await terminal.start()

        response = await terminal.send("")
        assert response.exit_code == 0

        await terminal.stop()

    async def test_very_long_input(self, kernel):
        """Very long input is handled."""
        terminal = FakeREPL("long")
        await terminal.start()

        long_text = "x" * 1000
        response = await terminal.send(long_text)
        assert response.exit_code == 0

        await terminal.stop()

    async def test_special_characters(self, kernel):
        """Special characters in input are handled."""
        terminal = FakeREPL("special")
        await terminal.start()

        special_inputs = [
            "test with 'single' quotes",
            'test with "double" quotes',
            "test with $variables",
            "test with | pipes",
            "test with newlines\n",
            "test with tabs\t",
            "test with emoji 🚀",
            "test with unicode: 世界",
        ]

        for inp in special_inputs:
            response = await terminal.send(inp)
            assert response.exit_code == 0

        await terminal.stop()


# =============================================================================
# Session Integration Tests
# =============================================================================


class TestSessionIntegration:
    """Test terminal integration with kernel sessions."""

    async def test_session_with_repl(self, kernel):
        """REPL terminal integrates with kernel session."""
        result = await kernel.dispatch("session create repl-kernel")
        assert result.exit_code == 0

        terminal = FakeREPL("repl-kernel")
        await terminal.start()

        await terminal.send("!echo hello")
        await terminal.send("think: analyzing")

        assert len(terminal.events) >= 2

        await terminal.stop()
        await kernel.dispatch("session kill repl-kernel")

    async def test_session_with_api(self, kernel):
        """API CLI terminal integrates with kernel session."""
        result = await kernel.dispatch("session create api-kernel")
        assert result.exit_code == 0

        terminal = FakeAPICLI("api-kernel")
        await terminal.start()

        await terminal.send("/complete test")
        assert any(e.type == "api_call" for e in terminal.events)

        await terminal.stop()
        await kernel.dispatch("session kill api-kernel")

    async def test_session_with_markdown(self, kernel):
        """Markdown CLI terminal integrates with kernel session."""
        result = await kernel.dispatch("session create md-kernel")
        assert result.exit_code == 0

        terminal = FakeMarkdownCLI("md-kernel")
        await terminal.start()

        await terminal.send("task: test")
        assert any(e.type == "task_completed" for e in terminal.events)

        await terminal.stop()
        await kernel.dispatch("session kill md-kernel")

    async def test_multiple_terminals_per_session(self, kernel):
        """Multiple terminals can share context."""
        result = await kernel.dispatch("session create shared-session")
        assert result.exit_code == 0

        repl = FakeREPL("shared-session")
        api = FakeAPICLI("shared-session")
        md = FakeMarkdownCLI("shared-session")

        await repl.start()
        await api.start()
        await md.start()

        await repl.send("!claude cmd")
        await api.send("/complete codex cmd")
        await md.send("task: opencode cmd")

        assert len(repl.events) >= 1
        assert len(api.events) >= 1
        assert len(md.events) >= 1

        await repl.stop()
        await api.stop()
        await md.stop()
        await kernel.dispatch("session kill shared-session")
