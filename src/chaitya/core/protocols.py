"""The Five Extension Points — Protocol interfaces for Chaitya Core.

All swappable via core.yaml. Default implementations ship with the kernel.
The protocol boundaries keep concerns isolated for testing and future replacement.

Reference: PRD §13
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable

from chaitya.core.types import (
    AdapterContract,
    AdapterPackage,
    CommandChain,
    DependencyGraph,
    Event,
    EventFilter,
    OutputChunk,
    PipelineContext,
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    Subscription,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# 1. SessionBackend Protocol (PRD §3.3, §13)
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionBackend(Protocol):
    """Protocol for session substrate implementations.

    The concrete substrate provides PTY allocation, named session creation,
    attach/detach, persistent process hosting, and I/O streaming.

    Default implementation: tmux (Mac/Linux).
    """

    async def create(self, name: str, identity: SessionIdentity) -> SessionHandle:
        """Create a new named session.

        Args:
            name: Unique session name.
            identity: Environment and identity configuration.

        Returns:
            Handle to the created session.
        """
        ...

    async def attach(self, name: str) -> None:
        """Attach to an existing session (interactive)."""
        ...

    async def detach(self, name: str) -> None:
        """Detach from a session without killing it."""
        ...

    async def kill(self, name: str) -> None:
        """Terminate a session and its processes."""
        ...

    async def signal(self, name: str, sig: str) -> None:
        """Send a signal to the session's process.

        Args:
            name: Session name.
            sig: Signal name (SIGTERM, SIGKILL, SIGINT, SIGHUP, etc.).
        """
        ...

    async def list(self) -> list[SessionRecord]:
        """List all sessions known to the backend."""
        ...

    async def exists(self, name: str) -> bool:
        """Check if a session exists in the backend."""
        ...

    async def stream_output(self, name: str) -> AsyncIterator[bytes]:
        """Stream output from a session as an async iterator."""
        ...

    async def send_input(self, name: str, data: bytes) -> None:
        """Send input bytes to a session's stdin."""
        ...

    async def set_env(self, name: str, key: str, value: str) -> None:
        """Set an environment variable in the session."""
        ...

    async def unset_env(self, name: str, key: str) -> None:
        """Remove an environment variable from the session."""
        ...


# ---------------------------------------------------------------------------
# 2. EventBus Protocol (PRD §3.4, §13)
# ---------------------------------------------------------------------------


@runtime_checkable
class EventBusProtocol(Protocol):
    """Protocol for event routing between adapters.

    Routes JSON-line events. Adapters communicate exclusively through
    the event bus — adapters never call each other directly.

    Default implementation: SQLite-backed pub/sub.
    """

    async def emit(self, event: Event) -> None:
        """Publish an event to the bus.

        The event is persisted and delivered to all matching subscribers.
        """
        ...

    async def subscribe(
        self,
        event_filter: EventFilter,
        handler: Callable[[Event], None],
    ) -> Subscription:
        """Subscribe to events matching the filter.

        Args:
            event_filter: Criteria for which events to receive.
            handler: Callback invoked for each matching event.

        Returns:
            Subscription handle for later unsubscribe.
        """
        ...

    async def unsubscribe(self, subscription: Subscription) -> None:
        """Remove a subscription."""
        ...

    async def history(self, event_filter: EventFilter) -> list[Event]:
        """Query historical events matching the filter."""
        ...



# ---------------------------------------------------------------------------
# 3. Store Protocol (PRD §3.7, §13)
# ---------------------------------------------------------------------------


@runtime_checkable
class StoreProtocol(Protocol):
    """Protocol for persistent storage.

    One database with two logical sections: session records and event log.
    Cross-section queries must be possible.

    Default implementation: SQLite.
    """

    async def save_session(self, record: SessionRecord) -> None:
        """Create or update a session record."""
        ...

    async def get_session(self, name: str) -> SessionRecord | None:
        """Retrieve a session record by name. Returns None if not found."""
        ...

    async def list_sessions(self) -> list[SessionRecord]:
        """List all session records."""
        ...

    async def delete_session(self, name: str) -> None:
        """Remove a session record."""
        ...

    async def append_event(self, event: Event) -> None:
        """Append an event to the event log (append-only)."""
        ...

    async def search_events(
        self, query: str, event_filter: EventFilter | None = None
    ) -> list[Event]:
        """Full-text search on event payloads with optional filter."""
        ...

    async def get_events(self, event_filter: EventFilter) -> list[Event]:
        """Query events by structured filter criteria."""
        ...

    async def open(self) -> None:
        """Open the store connection. Called during kernel boot."""
        ...

    async def close(self) -> None:
        """Close the store connection. Called during kernel shutdown."""
        ...


# ---------------------------------------------------------------------------
# 4. PipelineOrchestrator Protocol (PRD §3.5, §13)
# ---------------------------------------------------------------------------


@runtime_checkable
class PipelineOrchestratorProtocol(Protocol):
    """Protocol for pipeline execution.

    Manages data flow through L0 (ingest) → L1 (execute) → L2 (present).
    Parses and executes command chains (|, &&, ||, ;).
    Ensures L2 runs exactly once after the full chain completes.

    Default implementation ships with the kernel.
    """

    def parse_chain(self, expression: str) -> CommandChain:
        """Parse a command expression into a structured chain.

        Handles: | (pipe), && (and), || (or), ; (sequence)
        """
        ...

    async def execute(
        self, chain: CommandChain, ctx: PipelineContext
    ) -> AsyncIterator[OutputChunk]:
        """Execute a command chain through L0 → L1 → L2.

        Yields output chunks as they become available.
        L2 presentation runs exactly once after the full chain completes.
        """
        ...


# ---------------------------------------------------------------------------
# 5. AdapterLoader Protocol (PRD §3.6, §13)
# ---------------------------------------------------------------------------


@runtime_checkable
class AdapterLoaderProtocol(Protocol):
    """Protocol for adapter discovery, validation, and loading.

    The kernel contains a minimal bootstrap loader (~50 lines) that finds
    and loads the registry adapter. All subsequent loading goes through
    the registry adapter, which implements this protocol.
    """

    async def discover(self) -> list[AdapterPackage]:
        """Discover all installed adapter packages.

        Scans entry points, validates availability.
        """
        ...

    def validate(self, package: AdapterPackage) -> ValidationResult:
        """Validate an adapter's contract against kernel requirements.

        Checks: contract_version, command descriptions, parameters,
        examples, name collisions, dependencies, permissions.
        """
        ...

    async def load(self, package: AdapterPackage) -> AdapterContract:
        """Load an adapter package into the kernel.

        Returns the validated contract on success.
        Raises AdapterLoadError on failure.
        """
        ...

    def build_dependency_graph(
        self, packages: list[AdapterPackage]
    ) -> DependencyGraph:
        """Build topological ordering of adapter dependencies.

        Resolves depends_on references. Rejects circular dependencies.
        """
        ...

