"""Tests for chaitya.core.types — all data types, enums, and error classes."""

from __future__ import annotations

import uuid

import pytest

from chaitya.core.types import (
    AdapterContract,
    AdapterLoadError,
    AdapterPackage,
    AdapterPermissions,
    AdapterStatus,
    AdapterType,
    BusyInputPolicy,
    ChaityaStream,
    CommandChain,
    CommandOutput,
    CommandParam,
    CommandSpec,
    DependencyGraph,
    Event,
    EventFilter,
    InputSpec,
    InputType,
    KernelBootError,
    OutputChunk,
    OutputRoutingRule,
    PermissionDenied,
    PipelineCommand,
    PipelineContext,
    PipelineOperator,
    ResourceLimits,
    SessionContext,
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    SessionState,
    ShutdownPolicy,
    StreamHeader,
    Subscription,
    Suspension,
    ValidationResult,
    # Event type constants
    KERNEL_STARTED,
    ADAPTER_LOADED,
    INPUT_REQUESTED,
    SESSION_STATE_CHANGED,
)


# ---------------------------------------------------------------------------
# Enum Tests
# ---------------------------------------------------------------------------


class TestSessionState:
    def test_values(self) -> None:
        assert SessionState.IDLE.value == "idle"
        assert SessionState.BUSY.value == "busy"
        assert SessionState.WAITING.value == "waiting"
        assert SessionState.STUCK.value == "stuck"
        assert SessionState.DEAD.value == "dead"

    def test_all_states_present(self) -> None:
        assert len(SessionState) == 5


class TestInputType:
    def test_all_types(self) -> None:
        expected = {"text", "number", "password", "select", "confirm", "file"}
        actual = {t.value for t in InputType}
        assert actual == expected


class TestPipelineOperator:
    def test_operators(self) -> None:
        assert PipelineOperator.PIPE.value == "|"
        assert PipelineOperator.AND.value == "&&"
        assert PipelineOperator.OR.value == "||"
        assert PipelineOperator.SEQUENCE.value == ";"


# ---------------------------------------------------------------------------
# Event Tests
# ---------------------------------------------------------------------------


class TestEvent:
    def test_default_event(self) -> None:
        e = Event()
        assert e.event_id  # auto-generated UUID
        assert e.timestamp  # auto-generated
        assert e.type == ""
        assert e.payload == {}
        assert e.session_id is None
        assert e.exit_code is None
        assert e.request_id is None

    def test_event_with_values(self) -> None:
        e = Event(
            type=KERNEL_STARTED,
            source_adapter="kernel",
            payload={"version": "0.1.0"},
        )
        assert e.type == "kernel_started"
        assert e.source_adapter == "kernel"
        assert e.payload["version"] == "0.1.0"

    def test_event_is_frozen(self) -> None:
        e = Event()
        with pytest.raises(AttributeError):
            e.type = "modified"  # type: ignore[misc]

    def test_event_type_constants(self) -> None:
        assert KERNEL_STARTED == "kernel_started"
        assert ADAPTER_LOADED == "adapter_loaded"
        assert INPUT_REQUESTED == "input_requested"
        assert SESSION_STATE_CHANGED == "session_state_changed"


class TestEventFilter:
    def test_empty_filter(self) -> None:
        f = EventFilter()
        assert f.event_types is None
        assert f.source_adapter is None
        assert f.limit is None

    def test_filter_with_types(self) -> None:
        f = EventFilter(event_types=("kernel_started", "adapter_loaded"))
        assert len(f.event_types) == 2


# ---------------------------------------------------------------------------
# Session Tests
# ---------------------------------------------------------------------------


class TestSessionRecord:
    def test_default_record(self) -> None:
        r = SessionRecord(name="test-session")
        assert r.name == "test-session"
        assert r.state == SessionState.IDLE
        assert r.template is None
        assert r.created_at  # auto-generated

    def test_record_with_identity(self) -> None:
        identity = SessionIdentity(
            env_vars={"KEY": "value"},
            working_dir="~/projects",
        )
        r = SessionRecord(name="work", identity=identity)
        assert r.identity.env_vars["KEY"] == "value"
        assert r.identity.working_dir == "~/projects"


class TestCommandChain:
    def test_empty_chain(self) -> None:
        c = CommandChain()
        assert c.steps == []

    def test_chain_with_steps(self) -> None:
        cmd1 = PipelineCommand(adapter="file", subcommand="cat")
        cmd2 = PipelineCommand(adapter="output", subcommand="filter")
        chain = CommandChain(
            steps=[
                (cmd1, PipelineOperator.PIPE),
                (cmd2, None),
            ]
        )
        assert len(chain.steps) == 2
        assert chain.steps[0][1] == PipelineOperator.PIPE
        assert chain.steps[1][1] is None


# ---------------------------------------------------------------------------
# Adapter Contract Tests
# ---------------------------------------------------------------------------


class TestAdapterContract:
    def test_default_contract(self) -> None:
        c = AdapterContract()
        assert c.contract_version == "1"
        assert c.on_busy_input == BusyInputPolicy.QUEUE
        assert c.on_shutdown == ShutdownPolicy.DETACH
        assert c.commands == []

    def test_full_contract(self) -> None:
        c = AdapterContract(
            name="browser",
            description="Control a browser",
            depends_on=["file"],
            commands=[
                CommandSpec(
                    name="open",
                    description="Open a URL",
                    params=[
                        CommandParam(
                            name="url",
                            required=True,
                            example="https://example.com",
                            on_missing="suspend",
                        )
                    ],
                )
            ],
            permissions=AdapterPermissions(
                network=True,
                fs_read=["~/projects"],
            ),
        )
        assert c.name == "browser"
        assert len(c.commands) == 1
        assert c.commands[0].params[0].on_missing == "suspend"
        assert c.permissions.network is True


class TestValidationResult:
    def test_valid(self) -> None:
        r = ValidationResult()
        assert r.valid is True
        assert r.errors == []

    def test_invalid(self) -> None:
        r = ValidationResult(valid=False, errors=["missing description"])
        assert not r.valid


class TestDependencyGraph:
    def test_empty(self) -> None:
        g = DependencyGraph()
        assert g.load_order == []
        assert g.rejected == {}


# ---------------------------------------------------------------------------
# Suspension Tests
# ---------------------------------------------------------------------------


class TestSuspension:
    def test_suspension_is_exception(self) -> None:
        spec = InputSpec(name="body", prompt="Message body")
        s = Suspension(spec)
        assert isinstance(s, Exception)
        assert s.spec.name == "body"
        assert "body" in str(s)

    def test_input_spec_select(self) -> None:
        spec = InputSpec(
            name="choice",
            input_type=InputType.SELECT,
            options=["a", "b", "c"],
        )
        assert spec.input_type == InputType.SELECT
        assert len(spec.options) == 3

    def test_input_spec_number_range(self) -> None:
        spec = InputSpec(
            name="count",
            input_type=InputType.NUMBER,
            min_value=1.0,
            max_value=100.0,
        )
        assert spec.min_value == 1.0
        assert spec.max_value == 100.0


# ---------------------------------------------------------------------------
# Error Tests
# ---------------------------------------------------------------------------


class TestPermissionDenied:
    def test_basic(self) -> None:
        err = PermissionDenied("browser", "read file /etc/passwd")
        assert err.adapter == "browser"
        assert err.action == "read file /etc/passwd"
        assert "browser" in str(err)

    def test_with_detail(self) -> None:
        err = PermissionDenied("browser", "write", "/secret")
        assert "/secret" in str(err)


class TestAdapterLoadError:
    def test_message(self) -> None:
        err = AdapterLoadError("bad-plugin", "missing module.json")
        assert "bad-plugin" in str(err)
        assert "missing module.json" in str(err)


class TestKernelBootError:
    def test_is_exception(self) -> None:
        err = KernelBootError("registry adapter missing")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# Output Tests
# ---------------------------------------------------------------------------


class TestCommandOutput:
    def test_default(self) -> None:
        o = CommandOutput()
        assert o.exit_code == 0
        assert o.type == "text/plain"
        assert o.timestamp  # auto-generated
