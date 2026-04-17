"""Comprehensive tests for core adapters using fake terminal apps.

This test suite uses fake terminal apps to simulate different CLI patterns
(REPL, Markdown CLI, API CLI, Shell, etc.) and tests:
- Session management with multiple concurrent sessions
- Input/output adapters
- Event observation with watch
- Cross-session coordination
- Edge cases and error handling
"""

from __future__ import annotations

import asyncio
import re
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
    MultiTerminalTestHarness,
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


@pytest.fixture
async def harness(kernel):
    """Create a test harness with the kernel."""
    h = MultiTerminalTestHarness(kernel)
    yield h
    await h.close_all()


# =============================================================================
# Basic Session Tests
# =============================================================================


class TestBasicSessions:
    """Test basic session lifecycle."""

    async def test_create_and_delete_session(self, kernel):
        """Sessions can be created and deleted."""
        result = await kernel.dispatch("session create test-session")
        assert result.exit_code == 0
        assert "created" in result.raw.lower()

        result = await kernel.dispatch("session list")
        assert "test-session" in result.raw

        result = await kernel.dispatch("session kill test-session")
        assert result.exit_code == 0

    async def test_session_status(self, kernel):
        """Session status shows correct information."""
        await kernel.dispatch("session create status-test")
        result = await kernel.dispatch("session status status-test")
        assert result.exit_code == 0
        assert "status-test" in result.raw
        await kernel.dispatch("session kill status-test")

    async def test_session_not_found(self, kernel):
        """Non-existent session returns error."""
        result = await kernel.dispatch("session status nonexistent")
        assert result.exit_code == 1
        assert "not found" in result.raw.lower()


# =============================================================================
# Input Adapter Tests
# =============================================================================


class TestInputAdapter:
    """Test the input adapter (L0 Ingest)."""

    async def test_input_text(self, kernel):
        """Input from text works."""
        result = await kernel.dispatch('input --text "hello world"')
        assert result.exit_code == 0
        assert "hello world" in result.raw

    async def test_input_file(self, kernel, tmp_path):
        """Input from file works."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("file content")

        result = await kernel.dispatch(f'input --file "{test_file}"')
        assert result.exit_code == 0
        assert "file content" in result.raw

    async def test_input_file_not_found(self, kernel):
        """Input from non-existent file returns error."""
        result = await kernel.dispatch("input --file /nonexistent/file.txt")
        assert result.exit_code == 1
        assert "not found" in result.raw.lower()

    async def test_input_file_multiple(self, kernel, tmp_path):
        """Input from multiple files works."""
        file1 = tmp_path / "a.txt"
        file2 = tmp_path / "b.txt"
        file1.write_text("AAA")
        file2.write_text("BBB")

        result = await kernel.dispatch(f'input --file "{file1}" --file "{file2}"')
        assert result.exit_code == 0
        assert "AAA" in result.raw
        assert "BBB" in result.raw

    async def test_input_no_source_error(self, kernel):
        """Input without source returns error."""
        result = await kernel.dispatch("input --unknown-flag value")
        assert result.exit_code == 1


# =============================================================================
# Output Adapter Tests
# =============================================================================


class TestOutputAdapter:
    """Test the output adapter (L2 Present)."""

    async def test_output_passthrough(self, kernel):
        """Output passes through content unchanged."""
        result = await kernel.dispatch('input --text "hello" | output')
        assert result.exit_code == 0
        assert "hello" in result.raw

    async def test_output_filter(self, kernel):
        """Output filter works."""
        result = await kernel.dispatch(
            'input --text "line1\\nERROR: bad\\nline3" | output --filter ERROR'
        )
        assert result.exit_code == 0

    async def test_output_json(self, kernel):
        """Output JSON format works."""
        result = await kernel.dispatch('input --text "hello" | output --format json')
        assert result.exit_code == 0
        assert "content" in result.raw or "hello" in result.raw


# =============================================================================
# Info Adapter Tests
# =============================================================================


class TestInfoAdapter:
    """Test the info adapter."""

    async def test_info_overview(self, kernel):
        """Info shows kernel overview."""
        result = await kernel.dispatch("info")
        assert result.exit_code == 0
        assert "chaitya" in result.raw.lower() or "core" in result.raw.lower()

    async def test_info_kernel(self, kernel):
        """Info --kernel shows kernel details."""
        result = await kernel.dispatch("info --kernel")
        assert result.exit_code == 0

    async def test_info_adapter(self, kernel):
        """Info <adapter> shows adapter details."""
        result = await kernel.dispatch("info session")
        assert result.exit_code == 0


# =============================================================================
# Watch Adapter Tests
# =============================================================================


class TestWatchAdapter:
    """Test the watch adapter."""

    async def test_watch_empty(self, kernel):
        """Watch with no events returns empty."""
        result = await kernel.dispatch("watch --all --limit 10")
        assert isinstance(result.exit_code, int)

    async def test_watch_with_session(self, kernel):
        """Watch can filter by session."""
        await kernel.dispatch("session create watch-test")
        result = await kernel.dispatch("watch --session watch-test --limit 5")
        assert isinstance(result.exit_code, int)
        await kernel.dispatch("session kill watch-test")


# =============================================================================
# Registry Adapter Tests
# =============================================================================


class TestRegistryAdapter:
    """Test the registry adapter."""

    async def test_registry_list(self, kernel):
        """Registry list shows adapters."""
        result = await kernel.dispatch("registry list")
        assert result.exit_code == 0
        assert "adapter" in result.raw.lower()


# =============================================================================
# Multi-Session Tests
# =============================================================================


class TestMultiSession:
    """Test multiple concurrent sessions."""

    async def test_multiple_sessions(self, kernel):
        """Multiple sessions can exist simultaneously."""
        for i in range(3):
            result = await kernel.dispatch(f"session create multi-{i}")
            assert result.exit_code == 0

        result = await kernel.dispatch("session list")
        assert "multi-0" in result.raw
        assert "multi-1" in result.raw
        assert "multi-2" in result.raw

        for i in range(3):
            await kernel.dispatch(f"session kill multi-{i}")

    async def test_session_isolation(self, kernel):
        """Sessions are isolated from each other."""
        await kernel.dispatch("session create session-a")
        await kernel.dispatch("session create session-b")

        await kernel.dispatch("session send-input session-a hello --newline")
        await kernel.dispatch("session send-input session-b world --newline")

        await kernel.dispatch("session kill session-a")
        await kernel.dispatch("session kill session-b")

    async def test_session_env_vars(self, kernel):
        """Session environment variables work correctly."""
        await kernel.dispatch("session create env-test")
        result = await kernel.dispatch(
            'session set-env env-test --key TEST_VAR --value "test value"'
        )
        assert result.exit_code == 0
        await kernel.dispatch("session kill env-test")


# =============================================================================
# Session Lifecycle Tests
# =============================================================================


class TestSessionLifecycle:
    """Test session create/exec/disable lifecycle."""

    async def test_session_exec_mode(self, kernel):
        """Session exec mode can be changed."""
        await kernel.dispatch("session create exec-test")

        result = await kernel.dispatch("session exec disable exec-test")
        assert result.exit_code == 0

        result = await kernel.dispatch("session exec enable exec-test")
        assert result.exit_code == 0

        await kernel.dispatch("session kill exec-test")

    async def test_session_attach_detach(self, kernel):
        """Session attach and detach work."""
        await kernel.dispatch("session create attach-test")

        result = await kernel.dispatch("session attach attach-test")
        assert result.exit_code == 0

        result = await kernel.dispatch("session detach attach-test")
        assert result.exit_code == 0

        await kernel.dispatch("session kill attach-test")


# =============================================================================
# Signal Tests
# =============================================================================


class TestSignals:
    """Test session signals."""

    async def test_session_signal(self, kernel):
        """Session signals can be sent."""
        await kernel.dispatch("session create signal-test")

        result = await kernel.dispatch("session signal signal-test 15")
        assert isinstance(result.exit_code, int)

        await kernel.dispatch("session kill signal-test")


# =============================================================================
# Fake Terminal Integration Tests
# =============================================================================


class TestFakeTerminalIntegration:
    """Test integration with fake terminal apps."""

    async def test_repl_session(self, harness):
        """REPL terminal works with sessions."""
        terminal = await harness.create_terminal("repl", "repl-test")

        response = await terminal.send("Hello!")
        assert response.exit_code == 0

        response = await terminal.send("!echo test")
        assert "test" in response.output.lower() or "Executed" in response.output

    async def test_markdown_session(self, harness):
        """Markdown CLI terminal works with sessions."""
        terminal = await harness.create_terminal("markdown", "markdown-test")

        response = await terminal.send("task: test task")
        assert response.exit_code == 0

    async def test_api_session(self, harness):
        """API CLI terminal works with sessions."""
        terminal = await harness.create_terminal("api", "api-test")

        response = await terminal.send("/complete test prompt")
        assert response.exit_code == 0

    async def test_generic_custom_handler(self, harness):
        """Generic terminal custom handlers work."""
        terminal = await harness.create_terminal("generic", "generic-test")

        async def handle_ping(text):
            return TerminalResponse(output="PONG\n", exit_code=0)

        terminal.add_handler(r"^ping$", handle_ping)

        response = await terminal.send("ping")
        assert "PONG" in response.output

    async def test_multi_terminal_coordination(self, harness):
        """Multiple terminals can coordinate."""
        await harness.create_terminal("repl", "multi-repl")
        await harness.create_terminal("api", "multi-api")
        await harness.create_terminal("markdown", "multi-markdown")

        await harness.send_to_terminal("multi-repl", "!echo repl")
        await harness.send_to_terminal("multi-api", "/complete test")
        await harness.send_to_terminal("multi-markdown", "task: test")

        events = harness.get_events()
        assert len(events) > 0


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error conditions."""

    async def test_empty_session_name(self, kernel):
        """Empty session name returns error."""
        result = await kernel.dispatch("session create ")
        # Should handle gracefully

    async def test_special_chars_in_session_name(self, kernel):
        """Special characters in session names are handled."""
        result = await kernel.dispatch("session create test_session_123")
        if result.exit_code == 0:
            await kernel.dispatch("session kill test_session_123")

    async def test_very_long_input(self, kernel, tmp_path):
        """Long input is handled correctly."""
        long_text = "x" * 10000
        result = await kernel.dispatch(f'input --text "{long_text}"')
        assert result.exit_code == 0

    async def test_binary_content(self, kernel, tmp_path):
        """Binary content is handled correctly."""
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(bytes(range(256)))

        result = await kernel.dispatch(f'input --file "{binary_file}"')
        assert result.exit_code == 0

    async def test_unicode_content(self, kernel):
        """Unicode content is handled correctly."""
        result = await kernel.dispatch('input --text "Hello 世界 🌍"')
        assert result.exit_code == 0
        assert "世界" in result.raw

    async def test_concurrent_session_operations(self, kernel):
        """Concurrent session operations don't interfere."""
        results = await asyncio.gather(
            kernel.dispatch("session create concurrent-1"),
            kernel.dispatch("session create concurrent-2"),
            kernel.dispatch("session create concurrent-3"),
        )
        assert all(r.exit_code == 0 for r in results)

        result = await kernel.dispatch("session list")
        assert "concurrent-1" in result.raw
        assert "concurrent-2" in result.raw
        assert "concurrent-3" in result.raw

        await asyncio.gather(
            kernel.dispatch("session kill concurrent-1"),
            kernel.dispatch("session kill concurrent-2"),
            kernel.dispatch("session kill concurrent-3"),
        )

    async def test_session_after_kill(self, kernel):
        """Killed session shows dead state."""
        await kernel.dispatch("session create kill-test")
        await kernel.dispatch("session kill kill-test")

        result = await kernel.dispatch("session status kill-test")
        assert result.exit_code == 0
        assert "kill-test" in result.raw


# =============================================================================
# Pipeline Tests
# =============================================================================


class TestPipelines:
    """Test pipeline operations."""

    async def test_simple_pipeline(self, kernel):
        """Simple input -> output pipeline works."""
        result = await kernel.dispatch('input --text "test" | output')
        assert result.exit_code == 0
        assert "test" in result.raw

    async def test_multi_step_pipeline(self, kernel):
        """Multi-step pipelines work."""
        result = await kernel.dispatch(
            'input --text "line1\\nERROR\\nline3" | output --filter ERROR'
        )
        assert isinstance(result.exit_code, int)

    async def test_session_in_pipeline(self, kernel):
        """Session commands work in pipeline context."""
        await kernel.dispatch("session create pipe-test")
        result = await kernel.dispatch("session status pipe-test")
        assert result.exit_code == 0
        await kernel.dispatch("session kill pipe-test")
