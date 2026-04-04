"""Chaitya SDK — The only kernel surface adapters touch.

This package exposes the complete adapter development API.
Adapters import from here and nowhere else in the kernel ecosystem.

Usage::

    from chaitya_sdk import (
        adapter,          # decorator for command registration
        ChaityaStream,    # typed input stream
        SessionContext,   # session state and args
        event_bus,        # emit and subscribe
        Suspension,       # yield to request input
        InputSpec,        # input field definition
        InputType,        # input type enum
        PermissionDenied, # raised on out-of-bounds access
    )
"""

__version__ = "0.1.0a1"

# --- Public API surface (PRD §10) ---

from chaitya_sdk.context import event_bus, registry_proxy  # noqa: F401
from chaitya_sdk.decorator import adapter, get_registered_adapters  # noqa: F401
from chaitya_sdk.types import (  # noqa: F401
    # Event type constants
    ADAPTER_LOADED,
    ADAPTER_REJECTED,
    CONFIRMATION_REQUIRED,
    CONFIRMATION_RESPONSE,
    ERROR,
    FILE_CHANGED,
    INPUT_REQUESTED,
    INPUT_RESPONSE,
    KERNEL_SHUTTING_DOWN,
    KERNEL_STARTED,
    PROCESS_EXIT,
    PROGRESS_UPDATE,
    SESSION_CREATED,
    SESSION_DEAD,
    SESSION_KILLED,
    SESSION_STATE_CHANGED,
    SESSION_STUCK,
    STDERR_CHUNK,
    STDOUT_CHUNK,
    # Contract types (for advanced adapters)
    AdapterContract,
    AdapterPermissions,
    # Enums
    BusyInputPolicy,
    # Stream & Context
    ChaityaStream,
    CommandParam,
    CommandSpec,
    # Event (read-only view)
    Event,
    # Suspension system
    InputSpec,
    InputType,
    OutputRoutingRule,
    # Errors
    PermissionDenied,
    PipelineOperator,
    ResourceLimits,
    SessionContext,
    SessionState,
    ShutdownPolicy,
    Suspension,
)

__all__ = [
    # Decorator
    "adapter",
    "get_registered_adapters",
    # Core types
    "ChaityaStream",
    "SessionContext",
    "event_bus",
    "registry_proxy",
    "Suspension",
    "InputSpec",
    "InputType",
    "PermissionDenied",
    # Enums
    "BusyInputPolicy",
    "PipelineOperator",
    "SessionState",
    "ShutdownPolicy",
    # Contract types
    "AdapterContract",
    "AdapterPermissions",
    "CommandParam",
    "CommandSpec",
    "OutputRoutingRule",
    "ResourceLimits",
    # Event
    "Event",
    # Event constants
    "ADAPTER_LOADED",
    "ADAPTER_REJECTED",
    "CONFIRMATION_REQUIRED",
    "CONFIRMATION_RESPONSE",
    "ERROR",
    "FILE_CHANGED",
    "INPUT_REQUESTED",
    "INPUT_RESPONSE",
    "KERNEL_STARTED",
    "KERNEL_SHUTTING_DOWN",
    "PROCESS_EXIT",
    "PROGRESS_UPDATE",
    "SESSION_CREATED",
    "SESSION_DEAD",
    "SESSION_KILLED",
    "SESSION_STATE_CHANGED",
    "SESSION_STUCK",
    "STDERR_CHUNK",
    "STDOUT_CHUNK",
]
