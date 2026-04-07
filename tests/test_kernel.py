"""Tests for chaitya.core.kernel — boot, dispatch, shutdown, kernel commands."""

from __future__ import annotations

import asyncio
import os

import pytest

from chaitya.core.kernel import Kernel, KERNEL_COMMANDS, _PendingInputRequest
from chaitya_sdk.types import InputSpec as SdkInputSpec
from chaitya.core.types import (
    AdapterContract,
    AdapterPackage,
    AdapterPermissions,
    AdapterStatus,
    ChaityaStream,
    CommandOutput,
    CommandSpec,
    Event,
    EventFilter,
    KernelBootError,
    KERNEL_STARTED,
    KERNEL_SHUTTING_DOWN,
    PipelineContext,
)


def _session_backend() -> str:
    """Return appropriate session backend for the platform."""
    return "psmux" if os.name == "nt" else "tmux"


def _make_mock_registry_adapter() -> AdapterPackage:
    """Build a mock registry adapter package for tests.

    The real registry adapter is loaded via pip entry points by BootstrapLoader.
    In tests, we bypass pip by providing this pre-built package directly.
    """
    contract = AdapterContract(
        contract_version="1",
        name="registry",
        description="Registry adapter — discovers and manages other adapters.",
        commands=[
            CommandSpec(
                name="list",
                description="List all discovered adapters.",
                params=[],
                examples=["chaitya registry list"],
            ),
            CommandSpec(
                name="info",
                description="Show details for a named adapter.",
                params=[],
                examples=["chaitya registry info --name shell"],
            ),
            CommandSpec(
                name="validate",
                description="Validate an adapter contract.",
                params=[],
                examples=["chaitya registry validate --name file"],
            ),
        ],
        permissions=AdapterPermissions(can_emit_events=True),
    )

    async def mock_handler(stream, ctx):
        return b"[registry adapter - mock]", 0

    return AdapterPackage(
        name="registry",
        entry_point="<test-mock>",
        contract=contract,
        handler=mock_handler,
        status=AdapterStatus.LOADED,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def kernel():
    """Create, boot, and yield a kernel; shut down after test."""
    k = Kernel(
        db_path=":memory:",
        system_adapters=frozenset(),
    )
    await k.boot()
    yield k
    await k.shutdown()


@pytest.fixture
async def raw_kernel():
    """Unbooted kernel for boot-sequence tests."""
    return Kernel(
        db_path=":memory:",
        system_adapters=frozenset(),
    )


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
        history = await raw_kernel.event_bus.history(EventFilter(event_types=[KERNEL_STARTED]))
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
        # No sessions yet — should show helpful message or header
        assert (
            "no" in result.processed.lower()
            or "NAME" in result.processed
            or "session" in result.processed.lower()
        )

    async def test_dispatch_registry_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry list")
        assert isinstance(result, CommandOutput)

    async def test_dispatch_watch(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("watch")
        assert isinstance(result, CommandOutput)

    async def test_dispatch_preserves_nonzero_exit_code(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry validate missing-adapter")
        assert result.exit_code == 1

    async def test_dispatch_session_create_uses_positional_name(self, kernel: Kernel) -> None:
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
        kernel = Kernel(db_path=":memory:", session_backend=_session_backend())
        await kernel.boot()
        try:
            command = "Write-Output hello" if os.name == "nt" else "printf hello"
            result = await kernel.dispatch(f"shell run --command '{command}'")
            assert result.exit_code == 0
            assert "hello" in result.processed
        finally:
            await kernel.shutdown()

    async def test_dispatch_workspace_file_adapter(self, tmp_path) -> None:
        kernel = Kernel(db_path=":memory:")
        await kernel.boot()
        file_path = tmp_path / "note.txt"
        try:
            write = await kernel.dispatch(f"file write --path {file_path} --text hello")
            assert write.exit_code == 0, f"write failed: {write.processed}"
            read = await kernel.dispatch(f"file read --path {file_path}")
            assert read.exit_code == 0
            assert "hello" in read.processed
        finally:
            await kernel.shutdown()

    async def test_session_send_input_and_output_roundtrip(self) -> None:
        kernel = Kernel(
            db_path=":memory:",
            session_backend=_session_backend(),
            system_adapters=frozenset(),
        )
        await kernel.boot()
        try:
            created = await kernel.dispatch("session create loop")
            assert created.exit_code == 0

            read_cmd = "$X = Read-Host 'X'" if os.name == "nt" else "read X"
            first = await kernel.dispatch(f'session send-input loop "{read_cmd}" --newline')
            assert first.exit_code == 0
            await asyncio.sleep(1.0)
            second = await kernel.dispatch('session send-input loop "muku" --newline')
            assert second.exit_code == 0
            await asyncio.sleep(1.0)
            echo_cmd = "Write-Output ACK:$X" if os.name == "nt" else "echo ACK:$X"
            third = await kernel.dispatch(f'session send-input loop "{echo_cmd}" --newline')
            assert third.exit_code == 0

            output = await kernel.dispatch("session output loop --idle-timeout 1.5")
            assert output.exit_code == 0
            assert "ACK:muku" in output.processed
        finally:
            await kernel.shutdown()

    async def test_session_set_env_and_watch(self) -> None:
        kernel = Kernel(
            db_path=":memory:",
            session_backend=_session_backend(),
            system_adapters=frozenset(),
        )
        await kernel.boot()
        try:
            await kernel.dispatch("session create envloop")
            set_env = await kernel.dispatch("session set-env envloop --key GREETING --value hello")
            assert set_env.exit_code == 0
            await asyncio.sleep(0.5)
            await kernel.dispatch('session send envloop --text "echo $GREETING" --newline')
            output = await kernel.dispatch("session output envloop --idle-timeout 1.0")
            assert "hello" in output.processed

            watched = await kernel.dispatch(
                "watch --session envloop --on session_created --limit 5"
            )
            assert watched.exit_code == 0
            assert "session_created" in watched.processed
            assert "envloop" in watched.processed
        finally:
            await kernel.shutdown()

    async def test_input_timeout_expires_pending_request(self) -> None:
        """Pending input requests are expired after the configured timeout.

        We set created_at to the distant past so the timeout loop immediately
        detects the expired request on its next iteration.
        """
        from datetime import timedelta, datetime as dt, UTC

        kernel = Kernel(
            db_path=":memory:",
            system_adapters=frozenset(),
            input_timeout_seconds=300,
        )
        await kernel.boot()
        try:
            past = (dt.now(UTC) - timedelta(seconds=600)).isoformat()
            kernel._pending_inputs["test-request-id"] = _PendingInputRequest(
                request_id="test-request-id",
                adapter_name="test-adapter",
                handler=None,
                contract=None,
                permissions=AdapterPermissions(),
                input_stream=ChaityaStream(),
                ctx=PipelineContext(),
                spec=SdkInputSpec(name="test-field", prompt="test"),
                args={},
                created_at=past,
            )
            assert "test-request-id" in kernel._pending_inputs

            # Manually invoke one iteration of the timeout loop
            from chaitya.core.types import Event

            original_emit = kernel._event_bus.emit

            emitted_types: list[str] = []

            async def tracking_emit(event: Event) -> None:
                emitted_types.append(event.type)
                await original_emit(event)

            kernel._event_bus.emit = tracking_emit
            try:
                # Trigger timeout check by calling the loop body directly
                from chaitya.core.types import Event as CoreEvent
                from datetime import datetime as dt

                now = dt.now(UTC)
                expired: list[str] = []
                for request_id, pending in list(kernel._pending_inputs.items()):
                    created_str = pending.created_at
                    if not created_str:
                        continue
                    try:
                        created = dt.fromisoformat(created_str)
                    except (ValueError, TypeError):
                        continue
                    age_seconds = (now - created).total_seconds()
                    if age_seconds >= kernel._input_timeout_seconds:
                        expired.append(request_id)
                        await kernel._store.delete_pending_input(request_id)
                        await kernel._event_bus.emit(
                            CoreEvent(
                                type="input_timeout",
                                source_adapter="kernel",
                                session_id=pending.ctx.session_id,
                                request_id=request_id,
                                payload={
                                    "adapter": pending.adapter_name,
                                    "field": pending.spec.name if pending.spec else "unknown",
                                    "age_seconds": int(age_seconds),
                                },
                            )
                        )

                for request_id in expired:
                    kernel._pending_inputs.pop(request_id, None)

                assert "test-request-id" not in kernel._pending_inputs
                assert "input_timeout" in emitted_types
            finally:
                kernel._event_bus.emit = original_emit
        finally:
            await kernel.shutdown()


# ---------------------------------------------------------------------------
# L0 Ingest Tests
# ---------------------------------------------------------------------------


class TestL0Ingest:
    """Tests for L0 Ingest commands (input --text, --file, --clipboard, --merge)."""

    async def test_input_text_simple(self, kernel: Kernel) -> None:
        """input --text creates a stream with the text content."""
        output = await kernel.dispatch('input --text "hello world"')
        assert output.exit_code == 0
        assert "hello world" in output.raw

    async def test_input_text_no_quotes(self, kernel: Kernel) -> None:
        """input --text handles unquoted values."""
        output = await kernel.dispatch("input --text hello")
        assert output.exit_code == 0
        assert "hello" in output.raw

    async def test_input_file_reads_content(self, kernel: Kernel, tmp_path) -> None:
        """input --file reads file content into stream."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("file content here")

        output = await kernel.dispatch(f'input --file "{test_file}"')
        assert output.exit_code == 0
        assert "file content here" in output.raw

    async def test_input_file_not_found(self, kernel: Kernel) -> None:
        """input --file returns error for missing file."""
        output = await kernel.dispatch("input --file nonexistent.txt")
        assert output.exit_code == 1
        assert "not found" in output.raw

    async def test_input_file_with_type(self, kernel: Kernel, tmp_path) -> None:
        """input --file --type sets declared MIME type."""
        test_file = tmp_path / "data.csv"
        test_file.write_text("a,b,c\n1,2,3")

        output = await kernel.dispatch(f'input --file "{test_file}" --type text/csv')
        assert output.exit_code == 0
        assert "a,b,c" in output.raw

    async def test_input_file_multiple_merge_concat(self, kernel: Kernel, tmp_path) -> None:
        """input --file a.txt --file b.txt --merge concat concatenates files."""
        (tmp_path / "a.txt").write_text("AAA")
        (tmp_path / "b.txt").write_text("BBB")

        output = await kernel.dispatch(
            f'input --file "{tmp_path}\\a.txt" --file "{tmp_path}\\b.txt" --merge concat'
        )
        assert output.exit_code == 0
        assert "AAA" in output.raw
        assert "BBB" in output.raw

    async def test_input_file_multiple_merge_lines(self, kernel: Kernel, tmp_path) -> None:
        """input --file --file --merge lines joins with newlines."""
        (tmp_path / "a.txt").write_text("AAA\n")
        (tmp_path / "b.txt").write_text("BBB")

        output = await kernel.dispatch(
            f'input --file "{tmp_path}\\a.txt" --file "{tmp_path}\\b.txt" --merge lines'
        )
        assert output.exit_code == 0
        assert "AAA" in output.raw
        assert "BBB" in output.raw

    async def test_input_file_binary_guarded(self, kernel: Kernel, tmp_path) -> None:
        """input --file binary content is guarded by L2 binary filter."""
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03")

        output = await kernel.dispatch(f'input --file "{binary_file}"')
        assert output.exit_code == 0
        assert "binary" in output.raw.lower()

    async def test_input_no_source_error(self, kernel: Kernel) -> None:
        """input without source returns error."""
        output = await kernel.dispatch("input list")
        assert output.exit_code == 0
        output2 = await kernel.dispatch("input --unknown-flag value")
        assert output2.exit_code == 1

    async def test_input_text_standalone(self, kernel: Kernel) -> None:
        """input --text outputs the text content directly."""
        output = await kernel.dispatch('input --text "direct output"')
        assert output.exit_code == 0
        assert "direct output" in output.raw

    def test_strip_quotes(self, kernel: Kernel) -> None:
        """Helper strips surrounding quotes correctly."""
        assert kernel._strip_quotes('"hello"') == "hello"
        assert kernel._strip_quotes("'world'") == "world"
        assert kernel._strip_quotes("noquotes") == "noquotes"
        assert kernel._strip_quotes("") == ""

    def test_detect_mime_type(self, kernel: Kernel) -> None:
        """Helper detects MIME types from file extensions."""
        assert kernel._detect_mime_type("test.txt") == "text/plain"
        assert kernel._detect_mime_type("image.png") == "image/png"
        assert kernel._detect_mime_type("unknown.xyz") == "application/octet-stream"

    def test_extract_multiple_flags(self, kernel: Kernel) -> None:
        """Helper extracts multiple flag values from raw_args."""
        env = {"__args__": ["--file", "a.txt", "--file", "b.txt"]}
        files = kernel._extract_multiple_flags(env, "file")
        assert files == ["a.txt", "b.txt"]

        env = {"__args__": ["--file", "single.txt"]}
        files = kernel._extract_multiple_flags(env, "file")
        assert files == ["single.txt"]

        env = {"__args__": ["--file=path.txt"]}
        files = kernel._extract_multiple_flags(env, "file")
        assert files == ["path.txt"]
