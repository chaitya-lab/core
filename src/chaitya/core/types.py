"""Core types and data structures for Chaitya Core.

This module defines every data type used across the kernel.
Types are plain dataclasses — no business logic, no I/O.
All are immutable where possible (frozen=True).

Reference: PRD Sections 3, 4, 6, 7, 8, 13
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SessionState(enum.Enum):
    """Session lifecycle states (PRD §3.3)."""

    IDLE = "idle"
    """Running, no active process, accepting input."""

    BUSY = "busy"
    """Running, stdin locked by active process."""

    WAITING = "waiting"
    """Paused, expecting external input to resume."""

    STUCK = "stuck"
    """No output for threshold duration (default 60s)."""

    DEAD = "dead"
    """Process has exited or pane is gone."""


class InputType(enum.Enum):
    """Suspension input types (PRD §8)."""

    TEXT = "text"
    NUMBER = "number"
    PASSWORD = "password"
    SELECT = "select"
    CONFIRM = "confirm"
    FILE = "file"


class BusyInputPolicy(enum.Enum):
    """What to do when input arrives on a busy session (PRD §7)."""

    QUEUE = "queue"
    REJECT = "reject"


class ShutdownPolicy(enum.Enum):
    """What happens to sessions on kernel shutdown (PRD §7)."""

    DETACH = "detach"
    KILL = "kill"


class AdapterType(enum.Enum):
    """Adapter classification (PRD §3.6)."""

    SYSTEM = "system"
    USER = "user"


class AdapterStatus(enum.Enum):
    """Adapter load status."""

    LOADED = "loaded"
    REJECTED = "rejected"
    DEPENDENCY_MISSING = "dependency_missing"
    LOAD_ERROR = "load_error"


class PipelineOperator(enum.Enum):
    """Command chain operators (PRD §3.5)."""

    PIPE = "|"
    AND = "&&"
    OR = "||"
    SEQUENCE = ";"


# ---------------------------------------------------------------------------
# Event Types (PRD §6)
# ---------------------------------------------------------------------------


# Kernel-emitted event types (PRD §6)
KERNEL_STARTED = "kernel_started"
KERNEL_SHUTTING_DOWN = "kernel_shutting_down"
ADAPTER_LOADED = "adapter_loaded"
ADAPTER_REJECTED = "adapter_rejected"
SESSION_CREATED = "session_created"
SESSION_KILLED = "session_killed"
SESSION_STATE_CHANGED = "session_state_changed"
SESSION_STUCK = "session_stuck"
SESSION_DEAD = "session_dead"
SESSION_WAITING = "session_waiting"
SESSION_RESUMED = "session_resumed"
SESSION_INPUT_RECEIVED = "session_input_received"

# Standard adapter event conventions (PRD §6)
PROCESS_EXIT = "process_exit"
STDOUT_CHUNK = "stdout_chunk"
STDERR_CHUNK = "stderr_chunk"
PROGRESS_UPDATE = "progress_update"
INPUT_REQUESTED = "input_requested"
CONFIRMATION_REQUIRED = "confirmation_required"
CONFIRMATION_RESPONSE = "confirmation_response"
INPUT_RESPONSE = "input_response"
FILE_CHANGED = "file_changed"
CRON_TRIGGERED = "cron_triggered"
ERROR = "error"


@dataclass(frozen=True)
class Event:
    """Kernel event — the universal message on the event bus.

    Fixed at v1. Fields never removed in minor versions. Only added.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    source_adapter: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session_id: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class EventFilter:
    """Filter criteria for event queries and subscriptions."""

    event_types: list[str] | None = None
    source_adapter: str | None = None
    session_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    request_id: str | None = None
    limit: int | None = None


@dataclass
class Subscription:
    """Handle to an active event bus subscription."""

    subscription_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    filter: EventFilter = field(default_factory=EventFilter)
    active: bool = True


# ---------------------------------------------------------------------------
# Session Types (PRD §3.3, §11)
# ---------------------------------------------------------------------------


@dataclass
class SessionIdentity:
    """Identity/environment for a session (from template or create args)."""

    env_vars: dict[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    browser_profile: str | None = None
    git_worktree: bool = False


@dataclass(frozen=True)
class SessionHandle:
    """Opaque handle returned by SessionBackend.create()."""

    name: str
    backend_id: str = ""
    """Backend-specific identifier (e.g., tmux session:window.pane)."""


@dataclass
class SessionRecord:
    """Persistent session record stored in the database."""

    name: str
    state: SessionState = SessionState.IDLE
    template: str | None = None
    identity: SessionIdentity = field(default_factory=SessionIdentity)
    metadata: dict[str, Any] = field(default_factory=dict)
    exec_mode: str = "enabled"
    """Execution gate: 'enabled' | 'disabled' | 'readonly'.
    
    - enabled:  commands execute normally.
    - disabled: commands return dry-run preview only (--dry-run or --confirm bypasses).
    - readonly: no execution at all, not even dry-run.
    
    Stored directly on the record for simplicity — no separate enum or metadata key.
    """
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_activity: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Stream & Pipeline Types (PRD §3.5, §4)
# ---------------------------------------------------------------------------


@dataclass
class StreamHeader:
    """Frame header for inter-command data (PRD §3.5).

    Newline-delimited JSON header followed by payload.
    """

    type: str = "text/plain"
    """MIME type of the payload."""

    encoding: str = "utf-8"
    size: int | None = None
    """Byte size. Optional for streams."""

    stream: bool = False
    """True = continuous stream. False = fixed-size payload."""


@dataclass
class ChaityaStream:
    """Typed input stream — the L0 output (PRD §4, L0).

    Carries content through the pipeline with type metadata.
    """

    content: bytes = b""
    declared_type: str = "text/plain"
    detected_type: str | None = None
    source: str = ""
    size_bytes: int = 0
    encoding: str = "utf-8"
    exit_code: int = 0


@dataclass
class OutputChunk:
    """A chunk of pipeline output."""

    data: bytes = b""
    is_stderr: bool = False
    is_final: bool = False


@dataclass(frozen=True)
class PipelineCommand:
    """A single command in a pipeline chain."""

    adapter: str = ""
    subcommand: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    raw_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommandChain:
    """Parsed pipeline expression (PRD §3.5).

    Represents: cmd1 | cmd2 && cmd3 || cmd4 ; cmd5
    """

    steps: list[tuple[PipelineCommand, PipelineOperator | None]] = field(default_factory=list)
    """List of (command, operator_to_next). Last operator is None."""


@dataclass
class PipelineContext:
    """Runtime context for pipeline execution."""

    session_id: str | None = None
    input_stream: ChaityaStream | None = None
    env: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


@dataclass
class SessionContext:
    """Context passed to adapter command handlers (PRD §10).

    This is what the adapter receives alongside the ChaityaStream.
    """

    session_id: str | None = None
    session_state: SessionState = SessionState.IDLE
    args: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Adapter Contract Types (PRD §7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterPermissions:
    """Declared permissions for an adapter (PRD §7, §9)."""

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
    """'error' or 'suspend'."""


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
    """Declarative event emission rule (PRD §7)."""

    condition: str = ""
    """e.g., 'exit_code != 0'"""
    emit_event_type: str = ""


@dataclass(frozen=True)
class ResourceLimits:
    """Per-adapter resource limits (PRD §7)."""

    max_execution_seconds: int = 300
    max_output_bytes: int = 52_428_800  # 50 MB


@dataclass(frozen=True)
class AdapterContract:
    """Full adapter contract declaration (PRD §7).

    Declared in module.json or via Python decorators.
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
# Adapter Registry Types (PRD §3.6, §13)
# ---------------------------------------------------------------------------


@dataclass
class AdapterPackage:
    """Discovered adapter package before loading."""

    name: str = ""
    entry_point: str = ""
    module_name: str = ""
    module_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    contract: AdapterContract = field(default_factory=AdapterContract)
    handler: Callable[..., Any] | None = None
    adapter_type: AdapterType = AdapterType.USER
    status: AdapterStatus = AdapterStatus.LOADED
    error: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Result of adapter contract validation."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DependencyGraph:
    """Topological ordering of adapter dependencies."""

    load_order: list[str] = field(default_factory=list)
    """Adapter names in dependency-resolved load order."""

    rejected: dict[str, str] = field(default_factory=dict)
    """Adapter name → rejection reason (circular deps, missing deps)."""


# ---------------------------------------------------------------------------
# Suspension Types (PRD §8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputSpec:
    """Input field definition for a Suspension (PRD §8)."""

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
    """Yielded when an adapter needs input it does not have (PRD §8).

    The kernel catches this, emits input_requested,
    and resumes the adapter when the value is provided.
    """

    def __init__(self, spec: InputSpec) -> None:
        self.spec = spec
        super().__init__(f"Input required: {spec.name}")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PermissionDenied(Exception):
    """Raised when an adapter attempts an out-of-bounds action (PRD §9)."""

    def __init__(self, adapter: str, action: str, detail: str = "") -> None:
        self.adapter = adapter
        self.action = action
        self.detail = detail
        super().__init__(
            f"PermissionDenied: adapter '{adapter}' cannot {action}"
            + (f" — {detail}" if detail else "")
        )


class AdapterLoadError(Exception):
    """Raised when an adapter fails to load."""

    def __init__(self, adapter: str, reason: str) -> None:
        self.adapter = adapter
        self.reason = reason
        super().__init__(f"Failed to load adapter '{adapter}': {reason}")


class KernelBootError(Exception):
    """Raised when the kernel fails to boot."""

    pass


# ---------------------------------------------------------------------------
# L2 Output Types (PRD §4, L2)
# ---------------------------------------------------------------------------


@dataclass
class CommandOutput:
    """Structured output from L2 presentation layer (PRD §4, L2)."""

    raw: str = ""
    processed: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    type: str = "text/plain"
    stderr: str = ""
    session_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
