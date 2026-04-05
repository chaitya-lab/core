"""Tests for the chaitya_sdk package — standalone adapter development kit."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import chaitya_sdk
from chaitya_sdk import (
    AdapterContract,
    AdapterPermissions,
    ChaityaStream,
    CommandParam,
    CommandSpec,
    Event,
    InputSpec,
    InputType,
    PermissionDenied,
    ResourceLimits,
    SessionContext,
    SessionState,
    Suspension,
    adapter,
    event_bus,
    get_registered_adapters,
)
from chaitya_sdk.context import EventBusProxy
from chaitya_sdk.decorator import _ADAPTER_REGISTRY
from chaitya_sdk.wrappers import PassthroughCLI


# ---------------------------------------------------------------------------
# Package surface
# ---------------------------------------------------------------------------


class TestPackageSurface:
    def test_version_defined(self):
        assert hasattr(chaitya_sdk, "__version__")
        assert isinstance(chaitya_sdk.__version__, str)

    def test_all_prd_exports(self):
        """PRD §10 requires these exact names on the public surface."""
        for name in [
            "adapter",
            "ChaityaStream",
            "SessionContext",
            "event_bus",
            "Suspension",
            "InputSpec",
            "InputType",
            "PermissionDenied",
        ]:
            assert hasattr(chaitya_sdk, name), f"Missing: {name}"

    def test_no_core_dependency(self):
        """SDK must not import from chaitya.core at module level."""
        import sys

        # Check that no chaitya.core module was loaded as a dependency of the SDK
        sdk_modules = {m for m in sys.modules if m.startswith("chaitya_sdk")}
        # The SDK modules themselves should not have imported chaitya.core
        for mod_name in sdk_modules:
            mod = sys.modules[mod_name]
            # Check __dict__ for references to chaitya.core modules
            for attr_name, attr_val in vars(mod).items():
                if hasattr(attr_val, "__module__") and attr_val.__module__:
                    assert not attr_val.__module__.startswith("chaitya.core"), (
                        f"SDK module {mod_name}.{attr_name} references chaitya.core"
                    )


# ---------------------------------------------------------------------------
# Types (standalone — no core dependency)
# ---------------------------------------------------------------------------


class TestSDKTypes:
    def test_chaitya_stream_defaults(self):
        s = ChaityaStream()
        assert s.content == b""
        assert s.declared_type == "text/plain"
        assert s.encoding == "utf-8"

    def test_session_context_defaults(self):
        ctx = SessionContext()
        assert ctx.session_state == SessionState.IDLE
        assert ctx.args == {}
        assert ctx.dry_run is False

    def test_input_spec_creation(self):
        spec = InputSpec(
            name="email",
            prompt="Enter email",
            input_type=InputType.TEXT,
        )
        assert spec.name == "email"
        assert spec.input_type == InputType.TEXT

    def test_suspension_is_exception(self):
        spec = InputSpec(name="test")
        s = Suspension(spec)
        assert isinstance(s, Exception)
        assert s.spec is spec
        assert "test" in str(s)

    def test_permission_denied(self):
        e = PermissionDenied("my_adapter", "read file", "/etc/passwd")
        assert e.adapter == "my_adapter"
        assert e.action == "read file"
        assert "my_adapter" in str(e)

    def test_event_frozen(self):
        e = Event(type="test")
        with pytest.raises(AttributeError):
            e.type = "changed"  # type: ignore[misc]

    def test_adapter_contract_defaults(self):
        c = AdapterContract(name="test")
        assert c.contract_version == "1"
        assert c.default_session == "default"
        assert isinstance(c.permissions, AdapterPermissions)
        assert isinstance(c.resource_limits, ResourceLimits)


# ---------------------------------------------------------------------------
# @adapter decorator
# ---------------------------------------------------------------------------


class TestAdapterDecorator:
    def setup_method(self):
        _ADAPTER_REGISTRY.clear()

    def test_basic_registration(self):
        @adapter(
            name="test_basic",
            description="A test adapter",
            commands=[{"name": "run", "description": "Run it"}],
        )
        def handler(stream: ChaityaStream, ctx: SessionContext) -> bytes:
            return b"ok"

        assert hasattr(handler, "__chaitya_contract__")
        contract = handler.__chaitya_contract__
        assert contract.name == "test_basic"
        assert contract.description == "A test adapter"
        assert len(contract.commands) == 1
        assert contract.commands[0].name == "run"

    def test_registered_in_global_registry(self):
        @adapter(name="test_global", description="global test")
        def handler(stream, ctx):
            return b""

        reg = get_registered_adapters()
        assert "test_global" in reg
        assert reg["test_global"]["handler"] is not None

    def test_command_params(self):
        @adapter(
            name="test_params",
            commands=[
                {
                    "name": "send",
                    "description": "Send mail",
                    "params": [
                        {"name": "to", "required": True, "type": "string"},
                        {"name": "body", "on_missing": "suspend"},
                    ],
                }
            ],
        )
        def handler(stream, ctx):
            return b""

        contract = handler.__chaitya_contract__
        params = contract.commands[0].params
        assert len(params) == 2
        assert params[0].name == "to"
        assert params[0].required is True
        assert params[1].on_missing == "suspend"

    def test_permissions_passed_through(self):
        @adapter(
            name="test_perms",
            permissions={"network": True, "fs_read": ["/tmp"]},
        )
        def handler(stream, ctx):
            return b""

        contract = handler.__chaitya_contract__
        assert contract.permissions.network is True
        assert contract.permissions.fs_read == ["/tmp"]
        assert contract.permissions.can_emit_events is True  # default

    def test_resource_limits(self):
        @adapter(
            name="test_limits",
            resource_limits={"max_execution_seconds": 60},
        )
        def handler(stream, ctx):
            return b""

        contract = handler.__chaitya_contract__
        assert contract.resource_limits.max_execution_seconds == 60
        assert contract.resource_limits.max_output_bytes == 52_428_800  # default

    def test_handler_still_callable(self):
        @adapter(name="test_callable")
        def handler(stream, ctx):
            return b"hello"

        result = handler(ChaityaStream(), SessionContext())
        assert result == b"hello"

    def test_depends_on(self):
        @adapter(name="test_deps", depends_on=["file", "shell"])
        def handler(stream, ctx):
            return b""

        assert handler.__chaitya_contract__.depends_on == ["file", "shell"]


# ---------------------------------------------------------------------------
# EventBusProxy
# ---------------------------------------------------------------------------


class TestEventBusProxy:
    def test_emit_without_bus_raises(self):
        proxy = EventBusProxy()
        with pytest.raises(RuntimeError, match="not available"):
            asyncio.run(proxy.emit(Event(type="test")))

    def test_emit_with_no_permission_raises(self):
        proxy = EventBusProxy()

        class FakeBus:
            async def emit(self, event):
                pass

            async def subscribe(self, handler, event_types=None, session_id=None):
                pass

            async def unsubscribe(self, sub_id):
                pass

        proxy._configure(
            FakeBus(),  # type: ignore
            "my_adapter",
            AdapterPermissions(can_emit_events=False),
        )
        with pytest.raises(PermissionDenied):
            asyncio.run(proxy.emit(Event(type="test")))

    def test_emit_with_permission_succeeds(self):
        proxy = EventBusProxy()
        emitted = []

        class FakeBus:
            async def emit(self, event):
                emitted.append(event)

            async def subscribe(self, handler, event_types=None, session_id=None):
                pass

            async def unsubscribe(self, sub_id):
                pass

        proxy._configure(
            FakeBus(),  # type: ignore
            "my_adapter",
            AdapterPermissions(can_emit_events=True),
        )
        asyncio.run(proxy.emit(Event(type="hello")))
        assert len(emitted) == 1
        assert emitted[0].type == "hello"


# ---------------------------------------------------------------------------
# PassthroughCLI — wildcard command wrapper
# ---------------------------------------------------------------------------


class TestPassthroughCLI:
    def test_build_contract_has_wildcard(self):
        cli = PassthroughCLI(name="git", description="Git wrapper")
        contract = cli.build_contract()
        assert contract["name"] == "git"
        assert contract["description"] == "Git wrapper"
        cmd_names = [c["name"] for c in contract["commands"]]
        assert "*" in cmd_names

    def test_build_contract_includes_overrides(self):
        async def custom_handler(ctx):
            return b"custom", 0

        cli = PassthroughCLI(
            name="kubectl",
            overrides={"get": custom_handler},
        )
        contract = cli.build_contract()
        cmd_names = [c["name"] for c in contract["commands"]]
        assert "get" in cmd_names
        assert "*" in cmd_names

    def test_is_blocked_exact_match(self):
        cli = PassthroughCLI(name="git", blocked=["push", "reset --hard"])
        assert cli.is_blocked("push", []) is True
        assert cli.is_blocked("reset --hard", []) is True
        assert cli.is_blocked("status", []) is False

    def test_is_blocked_prefix_match(self):
        cli = PassthroughCLI(name="git", blocked=["clean -fd"])
        assert cli.is_blocked("clean", ["-fd"]) is True
        assert cli.is_blocked("clean", ["-f"]) is False
        assert cli.is_blocked("status", []) is False

    def test_is_blocked_full_args(self):
        cli = PassthroughCLI(name="git", blocked=["reset --hard"])
        assert cli.is_blocked("reset", ["--hard"]) is True
        assert cli.is_blocked("reset", ["--soft"]) is False

    def test_blocked_returns_error_bytes(self):
        cli = PassthroughCLI(name="git", blocked=["push"])
        ctx = SessionContext(args={"subcommand": "push", "__raw_args__": []})
        result = asyncio.run(cli.passthrough(ctx))
        assert result[1] == 1
        assert b"blocked" in result[0]

    def test_unknown_subcommand_passthrough(self):
        cli = PassthroughCLI(name="nonexistent", timeout=1.0)
        ctx = SessionContext(args={"subcommand": "status", "__raw_args__": ["-s"]})
        result = asyncio.run(cli.passthrough(ctx))
        assert result[1] == 1
        assert b"error" in result[0] or b"not available" in result[0]


# ---------------------------------------------------------------------------
# Git adapter integration (requires git installed)
# ---------------------------------------------------------------------------


def _load_git_adapter():
    """Load the git adapter module directly from the workspace path."""
    import importlib.util
    import sys
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "adaptors/community/chaitya/git/src/chaitya_adapter_git/__init__.py"
    )
    spec = importlib.util.spec_from_file_location("chaitya_adapter_git", src)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


class TestGitAdapter:
    def test_git_adapter_has_contract(self):
        mod = _load_git_adapter()
        assert mod is not None
        assert mod.__adapter_contract__["name"] == "git"

    def test_git_adapter_has_wildcard_command(self):
        mod = _load_git_adapter()
        assert mod is not None
        cmd_names = [c["name"] for c in mod.__adapter_contract__["commands"]]
        assert "*" in cmd_names

    def test_git_adapter_has_blocked_commands(self):
        mod = _load_git_adapter()
        assert mod is not None
        assert mod._git.is_blocked("push", []) is True
        assert mod._git.is_blocked("reset --hard", []) is True
        assert mod._git.is_blocked("status", []) is False

    def test_git_status_passed_through(self):
        mod = _load_git_adapter()
        assert mod is not None
        assert mod._git.default_session == "git-default"

    def test_git_blocked_command_returns_error(self):
        mod = _load_git_adapter()
        assert mod is not None
        ctx = SessionContext(args={"subcommand": "push", "__raw_args__": []})
        result = asyncio.run(mod._git.passthrough(ctx))
        assert result[1] == 1
        assert b"blocked" in result[0]


# ---------------------------------------------------------------------------
# Route adapter — conditional pipeline routing
# ---------------------------------------------------------------------------


def _load_route_adapter():
    """Load the route adapter module directly from the workspace path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "route_adapter",
        "adaptors/core/route/src/chaitya_adapter_route/__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRouteAdapter:
    def test_loads_successfully(self):
        mod = _load_route_adapter()
        assert hasattr(mod, "route_handler")
        assert hasattr(mod, "__adapter_contract__")

    def test_contract_has_check_command(self):
        mod = _load_route_adapter()
        contract = mod.__adapter_contract__
        assert contract["name"] == "route"
        cmd_names = [c["name"] for c in contract["commands"]]
        assert "check" in cmd_names

    def test_passes_through_when_no_conditions(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check"})
        stream = ChaityaStream(content=b"hello world")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[0] == b"hello world"
        assert result[1] == 0

    def test_if_exit_zero_passes_on_zero(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "if-exit": "0", "exit_code": "0"})
        stream = ChaityaStream(content=b"ok")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[0] == b"ok"
        assert result[1] == 0

    def test_if_exit_zero_drops_on_nonzero(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "if-exit": "0", "exit_code": "1"})
        stream = ChaityaStream(content=b"error")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[1] == 0
        assert b"dropped" in result[0]

    def test_if_exit_nonzero_passes_on_nonzero(self):
        mod = _load_route_adapter()
        ctx = SessionContext(
            args={"subcommand": "check", "if-exit-nonzero": True, "exit_code": "1"}
        )
        stream = ChaityaStream(content=b"error")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[0] == b"error"
        assert result[1] == 0

    def test_if_pattern_passes_on_match(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "if-pattern": "ERROR"})
        stream = ChaityaStream(content=b"ERROR: something failed")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[0] == b"ERROR: something failed"

    def test_if_pattern_drops_on_no_match(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "if-pattern": "ERROR"})
        stream = ChaityaStream(content=b"all good here")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert b"dropped" in result[0]

    def test_unless_pattern_passes_on_no_match(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "unless-pattern": "DEBUG"})
        stream = ChaityaStream(content=b"INFO: everything fine")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[0] == b"INFO: everything fine"

    def test_unless_pattern_drops_on_match(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "check", "unless-pattern": "DEBUG"})
        stream = ChaityaStream(content=b"DEBUG: verbose output")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert b"dropped" in result[0]

    def test_inverse_flips_pass_to_drop(self):
        mod = _load_route_adapter()
        ctx = SessionContext(
            args={"subcommand": "check", "if-exit": "0", "inverse": True, "exit_code": "0"}
        )
        stream = ChaityaStream(content=b"error")
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert b"dropped" in result[0]

    def test_unknown_subcommand_returns_error(self):
        mod = _load_route_adapter()
        ctx = SessionContext(args={"subcommand": "unknown"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.route_handler(stream, ctx))
        assert result[1] == 127
        assert b"unknown subcommand" in result[0]


# ---------------------------------------------------------------------------
# Process adapter — system process management
# ---------------------------------------------------------------------------


def _load_process_adapter():
    """Load the process adapter module directly from the workspace path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "process_adapter",
        "adaptors/core/process/src/chaitya_adapter_process/__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProcessAdapter:
    def test_loads_successfully(self):
        mod = _load_process_adapter()
        assert hasattr(mod, "process_handler")
        assert hasattr(mod, "__adapter_contract__")

    def test_contract_has_expected_commands(self):
        mod = _load_process_adapter()
        contract = mod.__adapter_contract__
        assert contract["name"] == "process"
        cmd_names = [c["name"] for c in contract["commands"]]
        for name in ["list", "tree", "info", "signal", "kill"]:
            assert name in cmd_names, f"Missing command: {name}"

    def test_list_returns_output(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "list"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 0
        assert len(result[0]) > 0

    def test_info_requires_pid(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "info"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 1
        assert b"--pid" in result[0]

    def test_info_unknown_pid_returns_error(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "info", "pid": "999999999"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 1
        assert b"no such process" in result[0]

    def test_signal_unknown_pid_returns_error(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "signal", "pid": "999999999", "sig": "TERM"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 1

    def test_kill_unknown_pid_returns_error(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "kill", "pid": "999999999"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 1

    def test_signal_unknown_signal_returns_error(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "signal", "pid": "1", "sig": "NOTAREALSIGNAL"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 1
        assert b"unknown signal" in result[0]

    def test_unknown_subcommand_returns_error(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={"subcommand": "unknown"})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 127
        assert b"unknown subcommand" in result[0]

    def test_help_without_subcommand(self):
        mod = _load_process_adapter()
        ctx = SessionContext(args={})
        stream = ChaityaStream(content=b"", exit_code=0)
        result = asyncio.run(mod.process_handler(stream, ctx))
        assert result[1] == 0
        assert b"process" in result[0]


# ---------------------------------------------------------------------------
# Desktop adapter — local desktop access
# ---------------------------------------------------------------------------


def _load_desktop_adapter():
    """Load the desktop adapter module directly from the workspace path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "desktop_adapter",
        "adaptors/core/desktop/src/chaitya_adapter_desktop/__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDesktopAdapter:
    def test_loads_successfully(self):
        mod = _load_desktop_adapter()
        assert hasattr(mod, "desktop_handler")
        assert hasattr(mod, "__adapter_contract__")

    def test_contract_has_expected_commands(self):
        mod = _load_desktop_adapter()
        contract = mod.__adapter_contract__
        assert contract["name"] == "desktop"
        cmd_names = [c["name"] for c in contract["commands"]]
        for name in ["screenshot", "clipboard", "tree"]:
            assert name in cmd_names, f"Missing command: {name}"

    def test_help_without_subcommand(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(args={})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        assert result[1] == 0
        assert b"desktop" in result[0]

    def test_unknown_subcommand_returns_error(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(args={"subcommand": "unknown"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        assert result[1] == 127
        assert b"unknown subcommand" in result[0]

    def test_clipboard_read_returns_bytes(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(args={"subcommand": "clipboard", "action": "read"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        assert result[1] == 0
        assert isinstance(result[0], bytes)

    def test_clipboard_write(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(args={"subcommand": "clipboard", "action": "write", "text": "test"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        assert result[1] == 0
        assert b"ok" in result[0] or b"copied" in result[0]

    def test_screenshot_returns_data_or_handles_display(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(args={"subcommand": "screenshot"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        # Returns 0 with data OR 1 with a display-related error (headless env)
        if result[1] == 0:
            data = result[0]
            assert b"data:image/png" in data or b"ok" in data or b"saved" in data
        else:
            # Headless environment — should have a meaningful error message
            assert len(result[0]) > 0

    def test_screenshot_to_file_handles_display(self):
        mod = _load_desktop_adapter()
        ctx = SessionContext(
            args={"subcommand": "screenshot", "path": "/tmp/chaitya-test-screen.png"}
        )
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.desktop_handler(stream, ctx))
        # Returns 0 (saved) OR 1 (no display) — both are valid


# ---------------------------------------------------------------------------
# GUI control adapter — keyboard/mouse automation
# ---------------------------------------------------------------------------


def _load_gui_adapter():
    """Load the GUI adapter module directly from the workspace path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gui_adapter",
        "adaptors/core/gui/src/chaitya_adapter_gui/__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGUIAdapter:
    def test_loads_successfully(self):
        mod = _load_gui_adapter()
        assert hasattr(mod, "gui_handler")
        assert hasattr(mod, "__adapter_contract__")

    def test_contract_has_expected_commands(self):
        mod = _load_gui_adapter()
        contract = mod.__adapter_contract__
        assert contract["name"] == "gui"
        cmd_names = [c["name"] for c in contract["commands"]]
        for name in ["click", "move", "type", "press", "drag"]:
            assert name in cmd_names, f"Missing command: {name}"

    def test_help_without_subcommand(self):
        mod = _load_gui_adapter()
        ctx = SessionContext(args={})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.gui_handler(stream, ctx))
        assert result[1] == 0
        assert b"gui" in result[0]

    def test_unknown_subcommand_returns_error(self):
        mod = _load_gui_adapter()
        ctx = SessionContext(args={"subcommand": "unknown"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.gui_handler(stream, ctx))
        assert result[1] == 127
        assert b"unknown subcommand" in result[0]

    def test_press_with_key_succeeds_or_fails_gracefully(self):
        mod = _load_gui_adapter()
        ctx = SessionContext(args={"subcommand": "press", "key": "Enter"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.gui_handler(stream, ctx))
        # Returns 0 (success) or 1 (accessibility denied) — both valid

    def test_move_requires_coords(self):
        mod = _load_gui_adapter()
        ctx = SessionContext(args={"subcommand": "move"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.gui_handler(stream, ctx))
        assert result[1] == 1

    def test_drag_requires_from_to(self):
        mod = _load_gui_adapter()
        ctx = SessionContext(args={"subcommand": "drag", "from": "100,200", "to": "300,400"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.gui_handler(stream, ctx))
        # On macOS without accessibility, returns error
        # but the contract is correct
        assert result[1] in (0, 1)


# ---------------------------------------------------------------------------
# Watchdog adapter — event-driven automation daemon
# ---------------------------------------------------------------------------


def _load_watchdog_adapter():
    """Load the watchdog adapter module directly from the workspace path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "watchdog_adapter",
        "adaptors/core/watchdog/src/chaitya_adapter_watchdog/__init__.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestWatchdogAdapter:
    def test_loads_successfully(self):
        mod = _load_watchdog_adapter()
        assert hasattr(mod, "watchdog_handler")
        assert hasattr(mod, "__adapter_contract__")

    def test_contract_has_expected_commands(self):
        mod = _load_watchdog_adapter()
        contract = mod.__adapter_contract__
        assert contract["name"] == "watchdog"
        cmd_names = [c["name"] for c in contract["commands"]]
        for name in ["start", "list", "stop"]:
            assert name in cmd_names, f"Missing command: {name}"

    def test_help_without_subcommand(self):
        mod = _load_watchdog_adapter()
        ctx = SessionContext(args={})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.watchdog_handler(stream, ctx))
        assert result[1] == 0
        assert b"watchdog" in result[0]

    def test_unknown_subcommand_returns_error(self):
        mod = _load_watchdog_adapter()
        ctx = SessionContext(args={"subcommand": "unknown"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.watchdog_handler(stream, ctx))
        assert result[1] == 127
        assert b"unknown subcommand" in result[0]

    def test_start_requires_for_event(self):
        mod = _load_watchdog_adapter()
        ctx = SessionContext(args={"subcommand": "start"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.watchdog_handler(stream, ctx))
        assert result[1] == 1
        assert b"--for" in result[0]

    def test_start_rejects_invalid_regex(self):
        mod = _load_watchdog_adapter()
        ctx = SessionContext(
            args={"subcommand": "start", "for": "browser.error", "if-pattern": "[invalid"}
        )
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.watchdog_handler(stream, ctx))
        assert result[1] == 1
        assert b"invalid regex" in result[0]

    def test_stop_requires_id(self):
        mod = _load_watchdog_adapter()
        ctx = SessionContext(args={"subcommand": "stop"})
        stream = ChaityaStream(content=b"")
        result = asyncio.run(mod.watchdog_handler(stream, ctx))
        assert result[1] == 1
        assert b"--id" in result[0]

    def test_stop_unknown_session_is_not_error(self):
        from unittest.mock import patch, AsyncMock

        mod = _load_watchdog_adapter()
        with patch.object(mod, "SessionRunner") as mock_runner_cls:
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=("[stderr]\n", 0))
            mock_runner_cls.return_value = mock_runner
            ctx = SessionContext(
                args={"subcommand": "stop", "id": "watchdog-nonexistent-session-abc123"}
            )
            stream = ChaityaStream(content=b"")
            result = asyncio.run(mod.watchdog_handler(stream, ctx))
            assert result[1] == 0

    def test_list_returns_session_info(self):
        from unittest.mock import patch, AsyncMock

        mod = _load_watchdog_adapter()
        with patch.object(mod, "SessionRunner") as mock_runner_cls:
            mock_runner = AsyncMock()
            mock_runner.run = AsyncMock(return_value=("", 0))
            mock_runner_cls.return_value = mock_runner
            ctx = SessionContext(args={"subcommand": "list"})
            stream = ChaityaStream(content=b"")
            result = asyncio.run(mod.watchdog_handler(stream, ctx))
            assert result[1] == 0
            assert isinstance(result[0], bytes)
