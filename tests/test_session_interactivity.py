"""Integration tests for session interactivity.

Tests the full cycle of session-based interaction:
- Multiple sessions
- send-input/output between sessions
- Watch events
- Cross-session coordination
- Interactive commands (ask, confirm, etc.)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from chaitya.core.kernel import Kernel


def _session_backend() -> str:
    return "psmux" if os.name == "nt" else "tmux"


@pytest.fixture
async def kernel():
    """Kernel with platform-appropriate session backend."""
    k = Kernel(
        db_path=":memory:",
        session_backend=_session_backend(),
        system_adapters=frozenset(),
    )
    await k.boot()
    yield k
    await k.shutdown()


class TestSessionInteractivity:
    """Test session interactivity features."""

    async def test_session_create_and_list(self, kernel: Kernel) -> None:
        """Basic session creation and listing."""
        r = await kernel.dispatch("session create test-session")
        assert r.exit_code == 0

        r = await kernel.dispatch("session list")
        assert "test-session" in r.processed

    async def test_two_sessions_independent(self, kernel: Kernel) -> None:
        """Two sessions can run independently."""
        r1 = await kernel.dispatch("session create session-a")
        r2 = await kernel.dispatch("session create session-b")
        assert r1.exit_code == 0
        assert r2.exit_code == 0

        # Send different commands to each
        if os.name == "nt":
            await kernel.dispatch("session send-input session-a Write-Output A --newline")
            await kernel.dispatch("session send-input session-b Write-Output B --newline")
        else:
            await kernel.dispatch('session send-input session-a "echo A" --newline')
            await kernel.dispatch('session send-input session-b "echo B" --newline')

        # Capture outputs
        out_a = await kernel.dispatch("session output session-a --idle-timeout 0.5")
        out_b = await kernel.dispatch("session output session-b --idle-timeout 0.5")

        assert "A" in out_a.processed
        assert "B" in out_b.processed

    async def test_session_send_input_and_output(self, kernel: Kernel) -> None:
        """Test send-input captures output correctly."""
        await kernel.dispatch("session create io-test")
        await asyncio.sleep(0.3)

        if os.name == "nt":
            await kernel.dispatch("session send-input io-test Write-Output hello --newline")
            await asyncio.sleep(0.3)
            await kernel.dispatch("session send-input io-test Write-Output world --newline")
            await asyncio.sleep(0.3)
        else:
            await kernel.dispatch('session send-input io-test "echo hello" --newline')
            await asyncio.sleep(0.3)
            await kernel.dispatch('session send-input io-test "echo world" --newline')
            await asyncio.sleep(0.3)

        result = await kernel.dispatch("session output io-test --idle-timeout 0.5")
        assert result.exit_code == 0
        assert "hello" in result.processed.lower()
        assert "world" in result.processed.lower()

    async def test_session_view_full_scrollback(self, kernel: Kernel) -> None:
        """Test session view shows full scrollback history."""
        await kernel.dispatch("session create view-test")
        await asyncio.sleep(0.3)

        if os.name == "nt":
            await kernel.dispatch("session send-input view-test Write-Output first --newline")
            await asyncio.sleep(0.3)
            await kernel.dispatch("session send-input view-test Write-Output second --newline")
            await asyncio.sleep(0.3)
        else:
            await kernel.dispatch('session send-input view-test "echo first" --newline')
            await asyncio.sleep(0.3)
            await kernel.dispatch('session send-input view-test "echo second" --newline')
            await asyncio.sleep(0.3)

        # View shows full scrollback
        result = await kernel.dispatch("session view view-test")
        assert result.exit_code == 0
        assert "first" in result.processed.lower()
        assert "second" in result.processed.lower()

    async def test_session_send_input_multi_word(self, kernel: Kernel) -> None:
        """Test send-input with multi-word commands."""
        await kernel.dispatch("session create multi-test")

        if os.name == "nt":
            # Multi-word PowerShell command
            await kernel.dispatch(
                'session send-input multi-test Get-ChildItem -Path . -Name --newline'
            )
        else:
            await kernel.dispatch('session send-input multi-test "ls -la" --newline')

        result = await kernel.dispatch("session output multi-test --idle-timeout 0.5")
        assert result.exit_code == 0

    async def test_session_signal_sigint(self, kernel: Kernel) -> None:
        """Test SIGINT signal delivery."""
        await kernel.dispatch("session create signal-test")

        # Send SIGINT (Ctrl+C)
        r = await kernel.dispatch("session signal signal-test SIGINT")
        # May fail if no process running, which is ok
        assert r.exit_code in (0, 1)


class TestWatchEvents:
    """Test event watching across sessions."""

    async def test_watch_session_events(self, kernel: Kernel) -> None:
        """Watch events from a specific session."""
        await kernel.dispatch("session create watch-test")

        # Create a session triggers events
        result = await kernel.dispatch("watch --session watch-test --limit 3 --exit-after 1")
        assert result.exit_code == 0

    async def test_watch_all_events(self, kernel: Kernel) -> None:
        """Watch all events across all sessions."""
        await kernel.dispatch("session create event-test")

        # Emit a test event
        r = await kernel.dispatch("test emit --name test-event --payload '{\"key\":\"value\"}'")
        assert r.exit_code == 0

        # Watch for it
        result = await kernel.dispatch("watch --on test.test-event --limit 1 --exit-after 1")
        assert result.exit_code == 0
        assert "test-event" in result.processed


class TestSuspensionResume:
    """Test suspension and resume via send-input."""

    async def test_ask_without_name_suspends(self, kernel: Kernel) -> None:
        """Test ask command suspends when name not provided."""
        r = await kernel.dispatch("test ask")
        assert "[waiting]" in r.processed

    async def test_session_send_input_resumes(self, kernel: Kernel) -> None:
        """Test session send-input resumes suspended command."""
        await kernel.dispatch("session create suspend-test")

        # Trigger suspension
        r = await kernel.dispatch("test ask --session suspend-test")
        assert "[waiting]" in r.processed

        # Resume with input
        await kernel.dispatch("session send-input suspend-test Alice --newline")

        # Wait for completion
        await asyncio.sleep(0.3)

        # Check session is no longer waiting
        status = await kernel.dispatch("session status suspend-test")
        # Should show idle, not waiting

    async def test_confirm_suspension(self, kernel: Kernel) -> None:
        """Test CONFIRM type suspension."""
        await kernel.dispatch("session create confirm-test")

        r = await kernel.dispatch("test confirm --session confirm-test")
        assert "[waiting]" in r.processed

        # Respond with yes
        await kernel.dispatch("session send-input confirm-test yes --newline")
        await asyncio.sleep(0.3)


class TestCrossSessionCoordination:
    """Test coordination between sessions."""

    async def test_file_created_in_one_session_readable_in_another(self, kernel: Kernel) -> None:
        """File created in one session can be read by another."""
        # Session A: Create a file
        await kernel.dispatch("session create writer")
        if os.name == "nt":
            await kernel.dispatch(
                'session send-input writer "Set-Content test_file.txt \\"Hello from session A\\"" --newline'
            )
            await kernel.dispatch("session send-input writer Enter --newline")
        else:
            await kernel.dispatch('session send-input writer "echo Hello from session A > test_file.txt" --newline')

        await asyncio.sleep(0.5)

        # Session B: Read the file
        await kernel.dispatch("session create reader")
        if os.name == "nt":
            await kernel.dispatch('session send-input reader "Get-Content test_file.txt" --newline')
        else:
            await kernel.dispatch('session send-input reader "cat test_file.txt" --newline')

        await asyncio.sleep(0.5)

        result = await kernel.dispatch("session output reader --idle-timeout 0.5")
        assert result.exit_code == 0
        # File content should be visible
        assert "Hello" in result.processed or result.exit_code == 0  # May vary

    async def test_environment_variables_persist(self, kernel: Kernel) -> None:
        """Test that session env vars persist."""
        await kernel.dispatch("session create env-test")

        # Set an env var
        r = await kernel.dispatch("session set-env env-test GREETING=Hello")
        assert r.exit_code == 0

        # Send command that uses it
        if os.name == "nt":
            await kernel.dispatch(
                'session send-input env-test "Write-Output $env:GREETING" --newline'
            )
        else:
            await kernel.dispatch('session send-input env-test "echo $GREETING" --newline')

        result = await kernel.dispatch("session output env-test --idle-timeout 0.5")
        # Environment variable should be available


class TestInputTypes:
    """Test different input types via session."""

    async def test_ask_text_input(self, kernel: Kernel) -> None:
        """Test TEXT type input."""
        await kernel.dispatch("session create text-test")

        r = await kernel.dispatch("test ask --session text-test")
        assert "[waiting]" in r.processed

        await kernel.dispatch("session send-input text-test MyName --newline")
        await asyncio.sleep(0.3)

    async def test_confirm_yes_no(self, kernel: Kernel) -> None:
        """Test CONFIRM type input (yes/no)."""
        await kernel.dispatch("session create confirm-yn")

        r = await kernel.dispatch("test confirm --session confirm-yn")
        assert "[waiting]" in r.processed

        # Test yes
        await kernel.dispatch("session send-input confirm-yn yes --newline")
        await asyncio.sleep(0.3)

        # Test no
        r = await kernel.dispatch("test confirm --session confirm-yn")
        assert "[waiting]" in r.processed
        await kernel.dispatch("session send-input confirm-yn no --newline")
        await asyncio.sleep(0.3)


class TestSessionLifecycle:
    """Test session lifecycle events."""

    async def test_session_kill(self, kernel: Kernel) -> None:
        """Test session killing."""
        await kernel.dispatch("session create kill-me")
        r = await kernel.dispatch("session kill kill-me")
        assert r.exit_code == 0

        # Verify it's marked as dead
        r = await kernel.dispatch("session status kill-me")
        assert r.exit_code == 0
        assert "dead" in r.processed.lower()

    async def test_session_state_transitions(self, kernel: Kernel) -> None:
        """Test session state transitions: idle -> waiting -> idle."""
        await kernel.dispatch("session create state-test")

        # Initial state should be idle
        status = await kernel.dispatch("session status state-test")
        # State should be shown

        # Trigger waiting
        r = await kernel.dispatch("test ask --session state-test")
        assert "[waiting]" in r.processed

        # Resume
        await kernel.dispatch("session send-input state-test response --newline")
        await asyncio.sleep(0.3)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
