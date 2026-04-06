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
        assert "[waiting:" in result.processed
        assert len(kernel._pending_inputs) == 1

        pending = next(iter(kernel._pending_inputs.values()))
        assert pending.spec.name == "name"
        assert "name" in pending.spec.prompt.lower()

    async def test_ask_resumed_with_name_prints_greeting(self, kernel: Kernel) -> None:
        first = await kernel.dispatch("test ask")
        assert "[waiting:" in first.processed

        request_id = next(iter(kernel._pending_inputs.keys()))
        result = await kernel.dispatch(f"input respond {request_id} Alice")
        assert result.exit_code == 0

        assert len(kernel._pending_inputs) == 0

        history = await kernel.event_bus.history(EventFilter(event_types=["test.ask.completed"]))
        assert len(history) >= 1
        assert history[0].payload.get("name") == "Alice"

    async def test_input_list_shows_pending(self, kernel: Kernel) -> None:
        await kernel.dispatch("test ask")
        result = await kernel.dispatch("input list")
        assert result.exit_code == 0
        assert "name" in result.processed.lower() or "adapter" in result.processed.lower()

    async def test_input_respond_unknown_id_returns_error(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("input respond does-not-exist myname")
        assert result.exit_code != 0

    async def test_multiple_ask_sessions_suspended(self, kernel: Kernel) -> None:
        await kernel.dispatch("test ask")
        await kernel.dispatch("test ask")

        assert len(kernel._pending_inputs) == 2

        ids = list(kernel._pending_inputs.keys())
        r1 = await kernel.dispatch(f"input respond {ids[0]} Alice")
        assert r1.exit_code == 0
        assert len(kernel._pending_inputs) == 1

        r2 = await kernel.dispatch(f"input respond {ids[1]} Bob")
        assert r2.exit_code == 0
        assert len(kernel._pending_inputs) == 0


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
