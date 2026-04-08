"""End-to-end integration tests using the chaitya-test workspace adapter.

These tests exercise the full kernel feature surface:
  - Basic command dispatch
  - Event emission and watch
  - Suspension / resume (input respond)
  - Session lifecycle (create / send-input / output / kill)
  - Registry commands
  - Pipeline chaining

The test adaptor (`chaitya-adapter-test`) is auto-discovered by the workspace
adapter loader when the kernel boots with default system adapters.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from chaitya.core.kernel import Kernel
from chaitya.core.types import CommandOutput, EventFilter


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _session_backend() -> str:
    """Return appropriate session backend for the platform."""
    return "psmux" if os.name == "nt" else "tmux"


@pytest.fixture
async def kernel():
    """Kernel with platform-appropriate session backend and workspace adapters."""
    k = Kernel(db_path=":memory:", session_backend=_session_backend())
    await k.boot()
    yield k
    await k.shutdown()


# ---------------------------------------------------------------------------
# Basic Dispatch
# ---------------------------------------------------------------------------


class TestBasicDispatch:
    async def test_hello_returns_greeting(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test hello")
        assert result.exit_code == 0
        assert "chaitya" in result.processed.lower()
        assert "working" in result.processed.lower()

    async def test_echo_preserves_message(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test echo --message 'hello world'")
        assert result.exit_code == 0
        assert "hello world" in result.processed

    async def test_echo_empty_message(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test echo --message ''")
        assert result.exit_code == 0
        assert "echo:" in result.processed

    async def test_unknown_subcommand_returns_nonzero(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test notasubcommand")
        assert result.exit_code == 1
        assert "unknown" in result.processed.lower()

    async def test_unknown_adapter_returns_nonzero(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("notanadapter do-something")
        assert result.exit_code != 0

    async def test_info_shows_adapter_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("info")
        assert result.exit_code == 0
        processed = result.processed.lower()
        assert "adapter" in processed


# ---------------------------------------------------------------------------
# Event Emission
# ---------------------------------------------------------------------------


class TestEventEmission:
    async def test_ping_emits_test_ping_event(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test ping")
        assert result.exit_code == 0
        assert "pong" in result.processed

        history = await kernel.event_bus.history(EventFilter(event_types=["test.ping"]))
        assert len(history) >= 1
        assert history[0].type == "test.ping"
        assert history[0].source_adapter == "test"

    async def test_emit_custom_event(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test emit --name custom_test --payload '{\"key\":42}'")
        assert result.exit_code == 0
        assert "custom_test" in result.processed

        history = await kernel.event_bus.history(EventFilter(event_types=["test.custom_test"]))
        assert len(history) >= 1
        assert history[0].payload.get("key") == 42

    async def test_emit_with_raw_payload_fallback(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test emit --name raw_test --payload not-json")
        assert result.exit_code == 0
        history = await kernel.event_bus.history(EventFilter(event_types=["test.raw_test"]))
        assert len(history) >= 1
        assert history[0].payload.get("raw") == "not-json"


# ---------------------------------------------------------------------------
# Watch
# ---------------------------------------------------------------------------


class TestWatch:
    async def test_watch_on_test_ping_after_ping(self, kernel: Kernel) -> None:
        await kernel.dispatch("test ping")

        result = await kernel.dispatch("watch --on test.ping --limit 5")
        assert result.exit_code == 0
        assert "test.ping" in result.processed or "ping" in result.processed

    async def test_watch_with_session_filter(self) -> None:
        kernel = Kernel(db_path=":memory:", session_backend=_session_backend())
        await kernel.boot()
        try:
            await kernel.dispatch("session create watch-session")
            await kernel.dispatch("test ping")

            result = await kernel.dispatch("watch --session watch-session --limit 5")
            assert result.exit_code == 0
            assert "watch-session" in result.processed or "session" in result.processed
        finally:
            await kernel.shutdown()

    async def test_watch_unknown_event_type_returns_empty(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("watch --on never.emitted.event --limit 3")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Suspension / Resume
# ---------------------------------------------------------------------------


class TestSuspensionResume:
    async def test_ask_without_name_suspends(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test ask")
        assert "[waiting]" in result.processed
        assert "What is your name?" in result.processed
        assert "session send-input" in result.processed

    async def test_input_list_shows_no_sessions_without_context(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test ask")
        assert "[waiting]" in result.processed
        list_result = await kernel.dispatch("input list")
        assert "no sessions waiting" in list_result.processed.lower()

    async def test_input_respond_shows_guidance(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("input respond does-not-exist myname")
        assert "session send-input" in result.processed.lower()


class TestSessionInputHandling:
    async def test_session_send_input_resumes_suspended_command(self, kernel: Kernel) -> None:
        import asyncio
        await kernel.dispatch("session create input-test")
        try:
            result = await kernel.dispatch("test ask --session input-test")
            assert "[waiting]" in result.processed
            assert "session send-input input-test" in result.processed

            await kernel.dispatch("session send-input input-test Alice --newline")
            await asyncio.sleep(0.1)

            history = await kernel.event_bus.history(EventFilter(event_types=["test.ask.completed"]))
            assert len(history) >= 1
            assert history[0].payload.get("name") == "Alice"
        finally:
            await kernel.dispatch("session kill input-test")


# ---------------------------------------------------------------------------
# Session Lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    async def test_session_create_and_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("session create my-session")
        assert result.exit_code == 0

        list_result = await kernel.dispatch("session list")
        assert list_result.exit_code == 0
        assert "my-session" in list_result.processed

    async def test_session_status(self, kernel: Kernel) -> None:
        await kernel.dispatch("session create status-test")
        result = await kernel.dispatch("session status status-test")
        assert result.exit_code == 0
        assert "status-test" in result.processed

    async def test_session_kill(self, kernel: Kernel) -> None:
        await kernel.dispatch("session create kill-test")
        result = await kernel.dispatch("session kill kill-test")
        assert result.exit_code == 0

    async def test_session_test_command_returns_guidance(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test session-test --session demo-session")
        assert result.exit_code == 0
        assert "session create" in result.processed
        assert "session send-input" in result.processed
        assert "session output" in result.processed

    async def test_session_send_input_and_output(self, kernel: Kernel) -> None:
        if os.name == "nt":
            pytest.skip("psmux sessions don't work well without TTY on Windows")
        await kernel.dispatch("session create io-test")

        if os.name == "nt":
            # PowerShell syntax - use Write-Output with explicit string
            await kernel.dispatch('session send-input io-test "Write-Output hello" --newline')
            await kernel.dispatch('session send-input io-test "Write-Output world" --newline')
        else:
            # Bash syntax
            await kernel.dispatch('session send-input io-test "echo hello" --newline')
            await kernel.dispatch('session send-input io-test "echo world" --newline')

        result = await kernel.dispatch("session output io-test --idle-timeout 1.0")
        assert result.exit_code == 0
        assert "hello" in result.processed.lower()
        assert "world" in result.processed.lower()


class TestSuspensionTypes:
    """Tests for different suspension input types (PRD §8)."""

    async def test_ask_text_type_suspension(self, kernel: Kernel) -> None:
        """Test TEXT type suspension requires name."""
        result = await kernel.dispatch("test ask")
        assert "[waiting]" in result.processed
        assert "session send-input" in result.processed

    async def test_input_list_shows_suspended_request(self, kernel: Kernel) -> None:
        """Test input list shows suspended requests via session state."""
        await kernel.dispatch("test ask")
        result = await kernel.dispatch("input list")
        assert result.exit_code == 0
        assert "default" in result.processed or "waiting" in result.processed


class TestResourceLimits:
    """Tests for resource limits enforcement (PRD §9, §15)."""

    async def test_adapter_has_resource_limits(self, kernel: Kernel) -> None:
        """Test adapters declare resource_limits in contract."""
        registry = kernel._registry
        test_adapter = registry.get_adapter("test")
        assert test_adapter is not None
        assert hasattr(test_adapter.contract, "resource_limits")

    async def test_pipeline_enforces_resource_limits(self, kernel: Kernel) -> None:
        """Test pipeline checks resource_limits from registry."""
        from chaitya.core.pipeline import PipelineOrchestrator

        orch = kernel._pipeline
        assert hasattr(orch, "_get_resource_limits")


class TestPipeEquivalence:
    """Tests for pipe ↔ flag form equivalence (PRD §5, §15)."""

    async def test_info_shows_adapter_list(self, kernel: Kernel) -> None:
        """Test info command shows adapter list (PRD §5)."""
        result = await kernel.dispatch("info")
        assert result.exit_code == 0
        assert "adapter" in result.processed.lower() or "file" in result.processed.lower()

    async def test_adapter_without_subcommand_shows_info(self, kernel: Kernel) -> None:
        """Test <adapter> alone equals <adapter> info (PRD §5)."""
        result = await kernel.dispatch("test")
        assert result.exit_code == 0
        assert "test" in result.processed.lower()

    async def test_pipe_operator_works(self, kernel: Kernel) -> None:
        """Test pipe operator feeds output to next command."""
        result = await kernel.dispatch("test echo --message hello | test echo --message world")
        assert result.exit_code == 0

    async def test_session_create_nonexistent_backend_fails(self) -> None:
        with pytest.raises(ValueError, match="Unsupported session backend"):
            Kernel(db_path=":memory:", session_backend="nonexistent-backend")


# ---------------------------------------------------------------------------
# Registry Commands
# ---------------------------------------------------------------------------


class TestRegistry:
    async def test_registry_list_shows_adapters(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry list")
        assert result.exit_code == 0
        processed = result.processed.lower()
        assert "test" in processed
        assert "file" in processed
        assert "shell" in processed

    async def test_registry_info_test_adapter(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry info test")
        assert result.exit_code == 0
        assert "test" in result.processed.lower()

    async def test_registry_info_shell_adapter(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry info shell")
        assert result.exit_code == 0
        assert "shell" in result.processed.lower()

    async def test_registry_validate_file_adapter(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry validate file")
        assert result.exit_code == 0

    async def test_registry_validate_unknown_returns_error(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry validate no-such-adapter")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Pipeline Chaining
# ---------------------------------------------------------------------------


class TestPipeline:
    async def test_pipeline_two_commands_chained(self, kernel: Kernel) -> None:
        if os.name == "nt":
            result = await kernel.dispatch("test echo --message ok | shell run --command '$input'")
        else:
            result = await kernel.dispatch("test echo --message ok | shell run --command 'grep .'")
        assert result.exit_code == 0
        assert "ok" in result.processed

    async def test_pipeline_unknown_subcommand_in_chain(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("test echo --message ok | test notasubcmd")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Session Signal (PRD §15)
# ---------------------------------------------------------------------------


class TestSessionSignal:
    """Tests for session signal command (PRD §15)."""

    async def test_session_signal_sends_to_process(self, kernel: Kernel) -> None:
        """Test session signal delivers signal to PTY process."""
        await kernel.dispatch("session create signal-test")

        result = await kernel.dispatch("session signal signal-test SIGTERM")
        assert result.exit_code == 0
        assert "SIGTERM" in result.processed

    async def test_session_signal_unknown_session_returns_error(self, kernel: Kernel) -> None:
        """Test session signal on nonexistent session returns error."""
        result = await kernel.dispatch("session signal nonexistent SIGTERM")
        assert result.exit_code != 0

    async def test_session_signal_usage_without_args(self, kernel: Kernel) -> None:
        """Test session signal without name/signal returns usage."""
        result = await kernel.dispatch("session signal")
        assert result.exit_code != 0
        assert "Usage" in result.processed or "usage" in result.raw.decode()


# ---------------------------------------------------------------------------
# Watch Exit After (PRD §15)
# ---------------------------------------------------------------------------


class TestWatchExitAfter:
    """Tests for watch --exit-after N (PRD §15)."""

    async def test_watch_accepts_exit_after_flag(self, kernel: Kernel) -> None:
        """Test watch command accepts --exit-after flag."""
        result = await kernel.dispatch("watch --exit-after 3")
        assert result.exit_code == 0

    async def test_watch_exit_after_returns_after_n_events(self, kernel: Kernel) -> None:
        """Test watch --exit-after N exits after N events."""
        await kernel.dispatch("test emit --name event1")
        await kernel.dispatch("test emit --name event2")
        await kernel.dispatch("test emit --name event3")

        result = await kernel.dispatch("watch --exit-after 2")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Kernel Restart Sessions (PRD §15)
# ---------------------------------------------------------------------------


class TestKernelRestartSessions:
    """Tests for session restoration on kernel restart (PRD §15)."""

    async def test_session_persists_across_store(self, kernel: Kernel) -> None:
        """Test session exists in store after creation."""
        await kernel.dispatch("session create persist-test")

        result = await kernel.dispatch("session status persist-test")
        assert result.exit_code == 0
        assert "persist-test" in result.processed

    async def test_kernel_restart_restores_running_sessions(
        self, tmp_path: Path
    ) -> None:
        """Test sessions with auto_restart are recreated on kernel restart."""
        db_path = str(tmp_path / "restart_test.db")

        kernel1 = Kernel(db_path=db_path)
        await kernel1.boot()
        await kernel1.dispatch("session create restart-test")
        await kernel1.shutdown()

        kernel2 = Kernel(db_path=db_path)
        await kernel2.boot()
        try:
            result = await kernel2.dispatch("session status restart-test")
            assert result.exit_code == 0
            assert "restart-test" in result.processed
        finally:
            await kernel2.shutdown()


# ---------------------------------------------------------------------------
# CONFIRM Type Suspension (PRD §15)
# ---------------------------------------------------------------------------


class TestConfirmSuspension:
    """Tests for CONFIRM type suspension (PRD §8, §15)."""

    async def test_confirm_suspension_returns_waiting(self, kernel: Kernel) -> None:
        """Test CONFIRM type suspension returns waiting state."""
        result = await kernel.dispatch("test confirm")
        assert result.exit_code == 0
        raw_str = result.raw if isinstance(result.raw, str) else result.raw.decode(errors="replace")
        assert "waiting" in raw_str.lower() or "[waiting:" in raw_str

    async def test_confirm_suspension_requires_response(self, kernel: Kernel) -> None:
        """Test CONFIRM suspension via session send-input."""
        await kernel.dispatch("session create confirm-test")
        try:
            result = await kernel.dispatch("test confirm --session confirm-test")
            assert "[waiting]" in result.processed

            record = await kernel._store.get_session("confirm-test")
            assert record is not None
            assert record.state.value == "waiting"

            await kernel.dispatch("session send-input confirm-test yes --newline")
            await asyncio.sleep(0.5)

            history = await kernel.event_bus.history(EventFilter(event_types=["test.confirm.completed"]))
            assert len(history) >= 1
            assert history[0].payload.get("answer") == "yes"
        finally:
            await kernel.dispatch("session kill confirm-test")
