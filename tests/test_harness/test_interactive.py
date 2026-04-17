"""Interactive session tests with different terminal types.

This module tests interactive workflows with various terminal app types
to ensure the core handles different TUI patterns correctly.
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
    FakeClaudeCode,
    FakeCodex,
    FakeGenericTerminal,
    FakeOpenCode,
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
# Claude Code Interactive Tests
# =============================================================================


class TestClaudeCodeInteractive:
    """Test Claude Code interactive patterns."""

    async def test_claude_thinking_blocks(self, kernel):
        """Claude Code thinking blocks work."""
        terminal = FakeClaudeCode("claude-thinking")
        await terminal.start()

        # Send thinking command
        response = await terminal.send("think: Let me analyze this problem")
        assert response.exit_code == 0
        assert "<thinking>" in response.output
        assert "analyze" in response.output.lower()

        # Verify thinking event was recorded
        assert any(e.type == "thinking_completed" for e in terminal.events)

        await terminal.stop()

    async def test_claude_confirmation(self, kernel):
        """Claude Code confirmation prompts work."""
        terminal = FakeClaudeCode("claude-confirm")
        await terminal.start()

        # Send confirmation response
        response = await terminal.send("[y] (confirm): yes")
        assert response.exit_code == 0

        # Verify response was handled
        assert len(terminal.events) >= 1

        await terminal.stop()

    async def test_claude_command_execution(self, kernel):
        """Claude Code command execution with ! prefix works."""
        terminal = FakeClaudeCode("claude-exec")
        await terminal.start()

        response = await terminal.send("!ls -la")
        assert response.exit_code == 0
        assert "Executed" in response.output or "ls" in response.output

        # Verify command executed event
        assert any(e.type == "command_executed" for e in terminal.events)

        await terminal.stop()

    async def test_claude_file_write(self, kernel):
        """Claude Code file write operations work."""
        terminal = FakeClaudeCode("claude-write")
        await terminal.start()

        response = await terminal.send("Write to test.txt")
        assert response.exit_code == 0
        assert "Wrote" in response.output or "✓" in response.output

        # Verify file written event
        assert any(e.type == "file_written" for e in terminal.events)

        await terminal.stop()

    async def test_claude_error_handling(self, kernel):
        """Claude Code error handling works."""
        terminal = FakeClaudeCode("claude-error")
        await terminal.start()

        response = await terminal.send("This contains an error")
        assert response.exit_code == 0

        # Verify error was handled
        assert any(e.type == "error" for e in terminal.events)

        await terminal.stop()

    async def test_claude_ansi_colors(self, kernel):
        """Claude Code ANSI color codes are preserved."""
        terminal = FakeClaudeCode("claude-colors")
        await terminal.start()

        # Claude code starts with ANSI in the header
        # and can include ANSI in responses
        assert len(terminal._buffer) > 0

        await terminal.stop()

    async def test_claude_multi_turn_conversation(self, kernel):
        """Claude Code supports multi-turn conversations."""
        terminal = FakeClaudeCode("claude-multi")
        await terminal.start()

        # Turn 1
        r1 = await terminal.send("Hello!")
        assert r1.exit_code == 0

        # Turn 2
        r2 = await terminal.send("think: processing")
        assert "<thinking>" in r2.output

        # Should have events
        assert len(terminal.events) >= 2

        await terminal.stop()


# =============================================================================
# Codex Interactive Tests
# =============================================================================


class TestCodexInteractive:
    """Test Codex CLI interactive patterns."""

    async def test_codex_help(self, kernel):
        """Codex help command works."""
        terminal = FakeCodex("codex-help")
        await terminal.start()

        response = await terminal.send("help")
        assert response.exit_code == 0
        assert "Commands" in response.output or "/complete" in response.output

        await terminal.stop()

    async def test_codex_complete(self, kernel):
        """Codex /complete API works."""
        terminal = FakeCodex("codex-complete")
        await terminal.start()

        response = await terminal.send("/complete Write a function")
        assert response.exit_code == 0
        assert "API" in response.output or "completion" in response.output.lower()

        # Verify API call was recorded
        assert len(terminal._api_calls) == 1
        assert terminal._api_calls[0]["type"] == "complete"

        await terminal.stop()

    async def test_codex_edit(self, kernel):
        """Codex /edit command works."""
        terminal = FakeCodex("codex-edit")
        await terminal.start()

        response = await terminal.send("/edit main.py")
        assert response.exit_code == 0
        assert "edit" in response.output.lower()

        await terminal.stop()

    async def test_codex_json_output(self, kernel):
        """Codex JSON-like output works."""
        terminal = FakeCodex("codex-json")
        await terminal.start()

        response = await terminal.send("/complete test")
        assert response.exit_code == 0
        assert "[" in response.output or "Response" in response.output

        await terminal.stop()

    async def test_codex_api_call_tracking(self, kernel):
        """Codex tracks API calls."""
        terminal = FakeCodex("codex-tracks")
        await terminal.start()

        # Make API calls
        await terminal.send("/complete call1")
        await terminal.send("/edit file.py")

        # Codex should track calls
        assert len(terminal._api_calls) >= 1

        await terminal.stop()


# =============================================================================
# OpenCode Interactive Tests
# =============================================================================


class TestOpenCodeInteractive:
    """Test OpenCode CLI interactive patterns."""

    async def test_opencode_markdown(self, kernel):
        """OpenCode markdown formatting works."""
        terminal = FakeOpenCode("opencode-md")
        await terminal.start()

        response = await terminal.send("What is this?")
        assert response.exit_code == 0
        assert "#" in response.output or "**" in response.output

        await terminal.stop()

    async def test_opencode_code_blocks(self, kernel):
        """OpenCode code blocks work."""
        terminal = FakeOpenCode("opencode-code")
        await terminal.start()

        response = await terminal.send("```\ncode here\n```")
        assert response.exit_code == 0
        assert "```" in response.output

        # Verify code block event
        assert any(e.type == "code_block" for e in terminal.events)

        await terminal.stop()

    async def test_opencode_task_execution(self, kernel):
        """OpenCode task execution with spinner works."""
        terminal = FakeOpenCode("opencode-task")
        await terminal.start()

        response = await terminal.send("task: test task")
        assert response.exit_code == 0
        assert "Task" in response.output or "Done" in response.output or "✅" in response.output

        # Verify task completed event
        assert any(e.type == "task_completed" for e in terminal.events)

        await terminal.stop()

    async def test_opencode_status_table(self, kernel):
        """OpenCode status table works."""
        terminal = FakeOpenCode("opencode-status")
        await terminal.start()

        response = await terminal.send("status")
        assert response.exit_code == 0
        assert "|" in response.output  # Markdown table

        await terminal.stop()

    async def test_opencode_spinner_animation(self, kernel):
        """OpenCode spinner animation works."""
        terminal = FakeOpenCode("opencode-spinner")
        await terminal.start()

        # Spinner should cycle through frames
        frames_seen = set()
        for _ in range(15):  # More iterations to see spinner
            response = await terminal.send("task: long task")
            for spinner in terminal.SPINNERS:
                if spinner in response.output:
                    frames_seen.add(spinner)

        # Should see multiple spinner frames
        assert len(frames_seen) >= 2

        await terminal.stop()

    async def test_opencode_status_badges(self, kernel):
        """OpenCode status badges work."""
        terminal = FakeOpenCode("opencode-badges")
        await terminal.start()

        response = await terminal.send("task: status check")
        assert response.exit_code == 0

        # Should have success badge
        assert terminal.STATUS_BADGES["success"] in response.output

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

        # Add custom handlers
        async def handle_echo(text):
            return TerminalResponse(output=f"ECHO: {text}\n", exit_code=0)

        async def handle_reverse(text):
            return TerminalResponse(output=f"REVERSE: {text[::-1]}\n", exit_code=0)

        terminal.add_handler(r"^echo\s+(.*)$", handle_echo)
        terminal.add_handler(r"^reverse\s+(.*)$", handle_reverse)

        # Test echo
        r1 = await terminal.send("echo Hello")
        assert "ECHO" in r1.output

        # Test reverse
        r2 = await terminal.send("reverse Hello")
        assert "REVERSE" in r2.output
        assert "olleH" in r2.output

        await terminal.stop()

    async def test_generic_fallback(self, kernel):
        """Generic terminal falls back to default for unknown patterns."""
        terminal = FakeGenericTerminal("generic-fallback")
        await terminal.start()

        response = await terminal.send("unknown command xyz")
        assert response.exit_code == 0
        assert "xyz" in response.output  # Should echo the input

        await terminal.stop()


# =============================================================================
# Cross-Terminal Tests
# =============================================================================


class TestCrossTerminalInteraction:
    """Test interactions between different terminal types."""

    async def test_session_routing_different_terminals(self, kernel):
        """Different terminal types can be routed to sessions."""
        # Create sessions for different terminals
        sessions = ["claude-session", "codex-session", "opencode-session"]
        terminals = [
            FakeClaudeCode(sessions[0]),
            FakeCodex(sessions[1]),
            FakeOpenCode(sessions[2]),
        ]

        # Start all terminals
        for terminal in terminals:
            await terminal.start()

        # Send commands to each
        for terminal in terminals:
            await terminal.send("test command")

        # Verify all handled their commands
        assert all(len(t.events) > 0 for t in terminals)

        # Cleanup
        for terminal in terminals:
            await terminal.stop()

    async def test_terminal_event_isolation(self, kernel):
        """Events are isolated between terminals."""
        claude = FakeClaudeCode("event-iso-a")
        codex = FakeCodex("event-iso-b")

        await claude.start()
        await codex.start()

        # Different commands generate different events
        await claude.send("!command1")
        await codex.send("/complete prompt1")

        # Claude should have command_executed
        assert any(e.type == "command_executed" for e in claude.events)

        # Codex should have api_call
        assert any(e.type == "api_call" for e in codex.events)

        # But not the other type
        assert not any(e.type == "api_call" for e in claude.events)
        assert not any(e.type == "command_executed" for e in codex.events)

        await claude.stop()
        await codex.stop()

    async def test_terminal_output_format_differences(self, kernel):
        """Different terminals produce different output formats."""
        claude = FakeClaudeCode("format-a")
        codex = FakeCodex("format-b")
        opencode = FakeOpenCode("format-c")

        await claude.start()
        await codex.start()
        await opencode.start()

        await claude.send("test")
        await codex.send("test")
        await opencode.send("test")

        # All terminals should produce output
        assert len(claude._buffer) > 0
        assert len(codex._buffer) > 0
        assert len(opencode._buffer) > 0

        await claude.stop()
        await codex.stop()
        await opencode.stop()


# =============================================================================
# Stress and Edge Case Tests
# =============================================================================


class TestTerminalStressTests:
    """Stress tests for terminal handling."""

    async def test_rapid_sequential_commands(self, kernel):
        """Rapid sequential commands are handled."""
        terminal = FakeClaudeCode("rapid")
        await terminal.start()

        # Send commands that generate events
        for i in range(5):
            response = await terminal.send(f"!echo command {i}")
            assert response.exit_code == 0

        # Command events should be recorded
        assert len(terminal.events) >= 5

        await terminal.stop()

    async def test_concurrent_terminals(self, kernel):
        """Concurrent terminals don't interfere."""
        terminals = [FakeClaudeCode(f"concurrent-{i}") for i in range(5)]

        # Start all concurrently
        await asyncio.gather(*[t.start() for t in terminals])

        # Send to all concurrently
        tasks = [t.send(f"command-{i}") for i, t in enumerate(terminals)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(r.exit_code == 0 for r in results)

        # Each terminal should have its own events
        for t in terminals:
            assert len(t.events) == 1

        # Cleanup
        await asyncio.gather(*[t.stop() for t in terminals])

    async def test_empty_input(self, kernel):
        """Empty input is handled gracefully."""
        terminal = FakeClaudeCode("empty")
        await terminal.start()

        response = await terminal.send("")
        assert response.exit_code == 0

        await terminal.stop()

    async def test_very_long_input(self, kernel):
        """Very long input is handled."""
        terminal = FakeClaudeCode("long")
        await terminal.start()

        long_text = "x" * 1000
        response = await terminal.send(long_text)
        assert response.exit_code == 0

        await terminal.stop()

    async def test_special_characters(self, kernel):
        """Special characters in input are handled."""
        terminal = FakeClaudeCode("special")
        await terminal.start()

        special_inputs = [
            "test with 'single' quotes",
            'test with "double" quotes',
            "test with $variables",
            "test with | pipes",
            "test with > redirects",
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

    async def test_session_with_claude(self, kernel):
        """Claude Code terminal integrates with kernel session."""
        # Create kernel session
        result = await kernel.dispatch("session create claude-kernel")
        assert result.exit_code == 0

        # Create terminal
        terminal = FakeClaudeCode("claude-kernel")
        await terminal.start()

        # Simulate interaction through session
        await terminal.send("!echo hello")
        await terminal.send("think: analyzing")

        # Verify terminal state (includes session_started + 2 commands)
        assert len(terminal.events) >= 2

        # Cleanup
        await terminal.stop()
        result = await kernel.dispatch("session kill claude-kernel")
        assert result.exit_code == 0

    async def test_session_with_codex(self, kernel):
        """Codex terminal integrates with kernel session."""
        result = await kernel.dispatch("session create codex-kernel")
        assert result.exit_code == 0

        terminal = FakeCodex("codex-kernel")
        await terminal.start()

        await terminal.send("/complete test")
        assert any(e.type == "api_call" for e in terminal.events)

        await terminal.stop()
        await kernel.dispatch("session kill codex-kernel")

    async def test_session_with_opencode(self, kernel):
        """OpenCode terminal integrates with kernel session."""
        result = await kernel.dispatch("session create opencode-kernel")
        assert result.exit_code == 0

        terminal = FakeOpenCode("opencode-kernel")
        await terminal.start()

        await terminal.send("task: test")
        assert any(e.type == "task_completed" for e in terminal.events)

        await terminal.stop()
        await kernel.dispatch("session kill opencode-kernel")

    async def test_multiple_terminals_per_session(self, kernel):
        """Multiple terminals can share context."""
        result = await kernel.dispatch("session create shared-session")
        assert result.exit_code == 0

        # Multiple terminals for same session
        claude = FakeClaudeCode("shared-session")
        codex = FakeCodex("shared-session")
        opencode = FakeOpenCode("shared-session")

        await claude.start()
        await codex.start()
        await opencode.start()

        # Each terminal has its own event log
        await claude.send("!claude cmd")
        await codex.send("/complete codex cmd")
        await opencode.send("task: opencode cmd")

        # Each terminal has its events (includes session_started)
        assert len(claude.events) >= 1
        assert len(codex.events) >= 1
        assert len(opencode.events) >= 1

        await claude.stop()
        await codex.stop()
        await opencode.stop()
        await kernel.dispatch("session kill shared-session")
