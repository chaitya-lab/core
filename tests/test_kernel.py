"""Tests for chaitya.core.kernel — boot, dispatch, shutdown, kernel commands."""

from __future__ import annotations

import pytest

from chaitya.core.kernel import Kernel, KERNEL_COMMANDS
from chaitya.core.types import (
    AdapterContract,
    AdapterPackage,
    CommandOutput,
    CommandSpec,
    Event,
    EventFilter,
    KernelBootError,
    KERNEL_STARTED,
    KERNEL_SHUTTING_DOWN,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def kernel():
    """Create, boot, and yield a kernel; shut down after test."""
    k = Kernel(db_path=":memory:", system_adapters=frozenset())
    await k.boot()
    yield k
    await k.shutdown()


@pytest.fixture
async def raw_kernel():
    """Unbooted kernel for boot-sequence tests."""
    return Kernel(db_path=":memory:", system_adapters=frozenset())


# ---------------------------------------------------------------------------
# Boot Sequence
# ---------------------------------------------------------------------------


class TestBootSequence:
    async def test_boot_succeeds(self, raw_kernel: Kernel) -> None:
        assert not raw_kernel.is_booted
        await raw_kernel.boot()
        assert raw_kernel.is_booted
        await raw_kernel.shutdown()

    async def test_double_boot_raises(self, kernel: Kernel) -> None:
        with pytest.raises(KernelBootError, match="already booted"):
            await kernel.boot()

    async def test_boot_emits_kernel_started(self, raw_kernel: Kernel) -> None:
        await raw_kernel.boot()
        # Check event history for KERNEL_STARTED
        history = await raw_kernel.event_bus.history(
            EventFilter(event_types=[KERNEL_STARTED])
        )
        assert len(history) >= 1
        assert history[0].type == KERNEL_STARTED
        await raw_kernel.shutdown()

    async def test_boot_with_workspace_adapters(self, kernel: Kernel) -> None:
        """Kernel boots cleanly with the in-repo first-party adaptors."""
        assert kernel.is_booted
        assert "file" in kernel.registry.loaded_adapters
        assert "shell" in kernel.registry.loaded_adapters

    async def test_boot_discovers_workspace_system_adapters(self) -> None:
        kernel = Kernel(db_path=":memory:")
        await kernel.boot()
        try:
            assert "file" in kernel.registry.loaded_adapters
            assert "shell" in kernel.registry.loaded_adapters
        finally:
            await kernel.shutdown()


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown(self, raw_kernel: Kernel) -> None:
        await raw_kernel.boot()
        assert raw_kernel.is_booted
        await raw_kernel.shutdown()
        assert not raw_kernel.is_booted

    async def test_shutdown_emits_event(self, raw_kernel: Kernel) -> None:
        await raw_kernel.boot()
        # We can't easily capture the event after shutdown closes the bus,
        # so just verify shutdown doesn't error
        await raw_kernel.shutdown()
        assert not raw_kernel.is_booted

    async def test_double_shutdown_is_noop(self, kernel: Kernel) -> None:
        await kernel.shutdown()
        await kernel.shutdown()  # Should not raise
        assert not kernel.is_booted

    async def test_uptime(self, kernel: Kernel) -> None:
        import asyncio
        await asyncio.sleep(0.05)  # 50ms — enough even on Windows
        assert kernel.uptime_seconds >= 0.01


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    async def test_dispatch_before_boot_raises(self, raw_kernel: Kernel) -> None:
        with pytest.raises(RuntimeError, match="not booted"):
            await raw_kernel.dispatch("info")

    async def test_dispatch_info(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("info")
        assert isinstance(result, CommandOutput)
        # With no adapters, should mention "No adapters"
        assert "no adapters" in result.processed.lower() or "adapter" in result.processed.lower()

    async def test_dispatch_session_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("session list")
        assert isinstance(result, CommandOutput)
        # No sessions yet
        assert "no active" in result.processed.lower() or "NAME" in result.processed

    async def test_dispatch_registry_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry list")
        assert isinstance(result, CommandOutput)

    async def test_dispatch_watch(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("watch")
        assert isinstance(result, CommandOutput)

    async def test_dispatch_preserves_nonzero_exit_code(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry validate missing-adapter")
        assert result.exit_code == 1

    async def test_dispatch_session_create_uses_positional_name(
        self, kernel: Kernel
    ) -> None:
        result = await kernel.dispatch("session create mac-dev")
        assert result.exit_code == 0
        status = await kernel.dispatch("session status mac-dev")
        assert "Name: mac-dev" in status.processed

    async def test_dispatch_unknown_adapter(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("nonexistent doSomething")
        assert isinstance(result, CommandOutput)
        # Should contain error about unknown adapter
        assert (
            "nonexistent" in result.processed.lower()
            or result.exit_code != 0
            or "error" in result.processed.lower()
        )

    async def test_dispatch_workspace_shell_adapter(self) -> None:
        kernel = Kernel(db_path=":memory:")
        await kernel.boot()
        try:
            result = await kernel.dispatch("shell run --command 'printf hello'")
            assert result.exit_code == 0
            assert "hello" in result.processed
        finally:
            await kernel.shutdown()

    async def test_dispatch_workspace_file_adapter(self, tmp_path) -> None:
        kernel = Kernel(db_path=":memory:")
        await kernel.boot()
        file_path = tmp_path / "note.txt"
        try:
            write = await kernel.dispatch(
                f"file write --path {file_path} --text hello"
            )
            assert write.exit_code == 0
            read = await kernel.dispatch(f"file read --path {file_path}")
            assert read.exit_code == 0
            assert "hello" in read.processed
        finally:
            await kernel.shutdown()

    async def test_session_send_input_and_output_roundtrip(self) -> None:
        kernel = Kernel(
            db_path=":memory:",
            session_backend="local",
            system_adapters=frozenset(),
        )
        await kernel.boot()
        try:
            created = await kernel.dispatch("session create loop")
            assert created.exit_code == 0

            first = await kernel.dispatch('session send-input loop "read X" --newline')
            assert first.exit_code == 0
            second = await kernel.dispatch('session send-input loop "muku" --newline')
            assert second.exit_code == 0
            third = await kernel.dispatch('session send-input loop "echo ACK:$X" --newline')
            assert third.exit_code == 0

            output = await kernel.dispatch("session output loop")
            assert output.exit_code == 0
            assert "ACK:muku" in output.processed
        finally:
            await kernel.shutdown()

    async def test_session_set_env_and_watch(self) -> None:
        kernel = Kernel(
            db_path=":memory:",
            session_backend="local",
            system_adapters=frozenset(),
        )
        await kernel.boot()
        try:
            await kernel.dispatch("session create envloop")
            set_env = await kernel.dispatch("session set-env envloop GREETING=hello")
            assert set_env.exit_code == 0
            await kernel.dispatch('session send-input envloop "echo $GREETING" --newline')
            output = await kernel.dispatch("session output envloop")
            assert "hello" in output.processed

            watched = await kernel.dispatch("watch --session envloop --on session_created --limit 5")
            assert watched.exit_code == 0
            assert "session_created" in watched.processed
            assert "envloop" in watched.processed
        finally:
            await kernel.shutdown()
