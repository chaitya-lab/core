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
            "adapter", "ChaityaStream", "SessionContext", "event_bus",
            "Suspension", "InputSpec", "InputType", "PermissionDenied",
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
                    assert not attr_val.__module__.startswith("chaitya.core"), \
                        f"SDK module {mod_name}.{attr_name} references chaitya.core"


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
            commands=[{
                "name": "send",
                "description": "Send mail",
                "params": [
                    {"name": "to", "required": True, "type": "string"},
                    {"name": "body", "on_missing": "suspend"},
                ],
            }],
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
