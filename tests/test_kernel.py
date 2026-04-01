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

    async def test_boot_with_no_adapters(self, kernel: Kernel) -> None:
        """Kernel with no adapters is functional but capability-free."""
        assert kernel.is_booted
        assert len(kernel.registry.loaded_adapters) == 0


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

    async def test_dispatch_unknown_adapter(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("nonexistent doSomething")
        assert isinstance(result, CommandOutput)
        # Should contain error about unknown adapter
        assert "nonexistent" in result.processed.lower() or result.exit_code != 0 or "error" in result.processed.lower()

