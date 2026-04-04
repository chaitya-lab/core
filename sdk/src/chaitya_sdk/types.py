"""Standalone type definitions for the Chaitya SDK.

These types are the adapter developer's interface to the kernel.
They are defined here — not imported from chaitya.core — so that
adapter packages depend only on ``chaitya-sdk``.

At kernel load time, the kernel maps between its internal types
and these SDK types.  The shapes are identical by design.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SessionState(enum.Enum):
    """Session lifecycle states."""

    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    STUCK = "stuck"
    DEAD = "dead"


class InputType(enum.Enum):
    """Suspension input types."""

    TEXT = "text"
    NUMBER = "number"
    PASSWORD = "password"
    SELECT = "select"
    CONFIRM = "confirm"
    FILE = "file"


class BusyInputPolicy(enum.Enum):
    """What to do when input arrives on a busy session."""

    QUEUE = "queue"
    REJECT = "reject"


class ShutdownPolicy(enum.Enum):
    """What happens to sessions on kernel shutdown."""

    DETACH = "detach"
    KILL = "kill"


class PipelineOperator(enum.Enum):
    """Command chain operators."""

    PIPE = "|"
    AND = "&&"
    OR = "||"
    SEQUENCE = ";"


# ---------------------------------------------------------------------------
# Stream & Context
# ---------------------------------------------------------------------------


@dataclass
class ChaityaStream:
    """Typed input stream — the L0 output.

    Carries content through the pipeline with type metadata.
    This is the first argument every adapter handler receives.
    """

    content: bytes = b""
    declared_type: str = "text/plain"
    detected_type: str | None = None
    source: str = ""
    size_bytes: int = 0
    encoding: str = "utf-8"


@dataclass
class SessionContext:
    """Context passed to adapter command handlers.

    This is the second argument every adapter handler receives.
    It provides session state, arguments, and environment.
    """

    session_id: str | None = None
    session_state: SessionState = SessionState.IDLE
    args: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Suspension System
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputSpec:
    """Input field definition for a Suspension.

    Used when an adapter needs to request input from
    a human or an AI agent.
    """

    name: str
    prompt: str = ""
    input_type: InputType = InputType.TEXT
    multiline: bool = False
    options: list[str] = field(default_factory=list)
    """For SELECT type — available options."""

    min_value: float | None = None
    max_value: float | None = None
    """For NUMBER type — optional range."""

    default: str | None = None


class Suspension(Exception):
    """Raised when an adapter needs input it does not have.

    The kernel catches this, emits ``input_requested``,
    and resumes the adapter when the value is provided.

    Usage::

        from chaitya_sdk import Suspension, InputSpec, InputType

        raise Suspension(InputSpec(
            name="body",
            prompt="Message body",
            input_type=InputType.TEXT,
            multiline=True,
        ))
    """

    def __init__(self, spec: InputSpec) -> None:
        self.spec = spec
        super().__init__(f"Input required: {spec.name}")


# ---------------------------------------------------------------------------
# Contract Types — used by the @adapter decorator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterPermissions:
    """Declared permissions for an adapter."""

    fs_read: list[str] = field(default_factory=list)
    fs_write: list[str] = field(default_factory=list)
    network: bool = False
    can_emit_events: bool = True
    can_read_all_events: bool = False
    can_access_sessions: list[str] = field(default_factory=lambda: ["own"])
    can_read_vault: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommandParam:
    """Parameter definition for an adapter command."""

    name: str
    required: bool = False
    type: str = "string"
    description: str = ""
    example: str = ""
    on_missing: str = "error"
    """'error' or 'suspend' — suspend yields a Suspension."""


@dataclass(frozen=True)
class CommandSpec:
    """Specification for a single adapter command."""

    name: str
    description: str = ""
    params: list[CommandParam] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    output_type: str = "text/plain"
    supports_dry_run: bool = False


@dataclass(frozen=True)
class OutputRoutingRule:
    """Declarative event emission rule."""

    condition: str = ""
    emit_event_type: str = ""


@dataclass(frozen=True)
class ResourceLimits:
    """Per-adapter resource limits."""

    max_execution_seconds: int = 300
    max_output_bytes: int = 52_428_800  # 50 MB


@dataclass(frozen=True)
class AdapterContract:
    """Full adapter contract declaration.

    Built automatically by the ``@adapter`` decorator, or loaded
    from ``module.json`` at package root.
    """

    contract_version: str = "1"
    name: str = ""
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    default_session: str = "default"
    default_input_type: str = "text/plain"
    default_output_type: str = "text/plain"
    supports_dry_run: bool = False
    on_busy_input: BusyInputPolicy = BusyInputPolicy.QUEUE
    on_error: str = "stderr_attach"
    on_timeout: str = "emit_stuck_event"
    on_shutdown: ShutdownPolicy = ShutdownPolicy.DETACH
    permissions: AdapterPermissions = field(default_factory=AdapterPermissions)
    commands: list[CommandSpec] = field(default_factory=list)
    output_routing: list[OutputRoutingRule] = field(default_factory=list)
    events_emitted: list[str] = field(default_factory=list)
    events_consumed: list[str] = field(default_factory=list)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)


# ---------------------------------------------------------------------------
# Event (read-only view for adapters)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Kernel event — read-only view for adapter subscriptions."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    source_adapter: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    session_id: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Standard event type constants
# ---------------------------------------------------------------------------

# Kernel events (read-only for adapters)
KERNEL_STARTED = "kernel_started"
KERNEL_SHUTTING_DOWN = "kernel_shutting_down"
ADAPTER_LOADED = "adapter_loaded"
ADAPTER_REJECTED = "adapter_rejected"
SESSION_CREATED = "session_created"
SESSION_KILLED = "session_killed"
SESSION_STATE_CHANGED = "session_state_changed"
SESSION_STUCK = "session_stuck"
SESSION_DEAD = "session_dead"

# Adapter convention events
PROCESS_EXIT = "process_exit"
STDOUT_CHUNK = "stdout_chunk"
STDERR_CHUNK = "stderr_chunk"
PROGRESS_UPDATE = "progress_update"
INPUT_REQUESTED = "input_requested"
INPUT_RESPONSE = "input_response"
CONFIRMATION_REQUIRED = "confirmation_required"
CONFIRMATION_RESPONSE = "confirmation_response"
FILE_CHANGED = "file_changed"
ERROR = "error"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PermissionDenied(Exception):
    """Raised when an adapter attempts an out-of-bounds action.

    The SDK checks permissions at the API boundary.
    """

    def __init__(self, adapter: str, action: str, detail: str = "") -> None:
        self.adapter = adapter
        self.action = action
        self.detail = detail
        super().__init__(
            f"PermissionDenied: adapter '{adapter}' cannot {action}"
            + (f" — {detail}" if detail else "")
        )

