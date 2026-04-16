"""Kernel Orchestrator — boot, dispatch, shutdown.

The Kernel composes all subsystems (Store, EventBus, SessionManager,
PipelineOrchestrator, AdapterRegistry) and orchestrates the boot sequence,
command dispatch, and shutdown.

The kernel has exactly six commands it dispatches itself:
  info, session, input, output, watch, registry

Everything else is dispatched to adapters.

Reference: PRD §3.1, §3.2, §5
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import mimetypes
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from chaitya_sdk.context import (
    _configure_permissions,
    event_bus as sdk_event_bus,
    kernel_info as sdk_kernel_info,
    registry_proxy,
    session_manager as sdk_session_manager,
    store as sdk_store,
)
from chaitya_sdk.types import (
    AdapterPermissions as SdkAdapterPermissions,
)
from chaitya_sdk.types import (
    ChaityaStream as SdkChaityaStream,
)
from chaitya_sdk.types import (
    InputSpec as SdkInputSpec,
)
from chaitya_sdk.types import (
    InputType as SdkInputType,
)
from chaitya_sdk.types import (
    SessionContext as SdkSessionContext,
)
from chaitya_sdk.types import (
    SessionState as SdkSessionState,
)
from chaitya_sdk.types import (
    Suspension as SdkSuspension,
)

from chaitya.core import __version__
from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.config import CoreConfig, EventBusConfig, KernelConfig, SessionConfig
from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.pipeline import AdapterHandler, PipelineOrchestrator
from chaitya.core.protocols import EventBusProtocol, StoreProtocol
from chaitya.core.registry import AdapterRegistry
from chaitya.core.session_manager import SessionManager
from chaitya.core.store import SqliteStore
from chaitya.core.types import (
    ADAPTER_LOADED,
    ADAPTER_REJECTED,
    KERNEL_SHUTTING_DOWN,
    KERNEL_STARTED,
    SESSION_RESUMED,
    SESSION_WAITING,
    AdapterContract,
    AdapterLoadError,
    AdapterPackage,
    AdapterPermissions,
    ChaityaStream,
    CommandOutput,
    Event,
    EventFilter,
    KernelBootError,
    PipelineContext,
    SessionState,
)

logger = logging.getLogger(__name__)

# The six kernel-dispatched commands (PRD §5)
KERNEL_COMMANDS = frozenset({"info", "session", "input", "output", "watch", "registry"})

# Kernel debug log configuration (PRD §14)
# Default location: ~/.chaitya/logs/kernel.log
# Can be overridden via core.yaml: kernel.debug_log
_KERNEL_LOG_DIR = Path.home() / ".chaitya" / "logs"
_kernel_log_path: Path | None = None


def _configure_kernel_logging(log_file: str = "", log_level: str = "info") -> None:
    """Configure kernel logger for file-based debug logging.

    Args:
        log_file: Custom log file path (empty = default ~/.chaitya/logs/kernel.log)
        log_level: Logging level (debug, info, warning, error)
    """
    global _kernel_log_path

    if log_file:
        _kernel_log_path = Path(log_file).expanduser()
        _kernel_log_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        _kernel_log_path = _KERNEL_LOG_DIR / "kernel.log"
        _KERNEL_LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(_kernel_log_path)
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level_map.get(log_level.lower(), logging.INFO))


# Call once at module load with defaults
_configure_kernel_logging()

_KERNEL_COMMAND_CONTRACTS: dict[str, dict] = {
    "info": {
        "name": "info",
        "description": "Show system or adapter information.",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "info",
                "description": "Show overview, adapter details, or kernel status.",
                "params": [
                    {"name": "kernel", "required": False, "description": "Show kernel status."},
                ],
                "examples": ["chaitya info", "chaitya info file", "chaitya info --kernel"],
            },
        ],
        "permissions": {
            "fs_read": ["."],
            "fs_write": [],
            "network": False,
            "can_emit_events": False,
        },
    },
    "session": {
        "name": "session",
        "description": "Manage named running environments (sessions).",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "list",
                "description": "List all sessions.",
                "params": [],
                "examples": ["chaitya session list"],
            },
            {
                "name": "create",
                "description": "Create a new session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {"name": "template", "required": False, "description": "Template name."},
                ],
                "examples": [
                    "chaitya session create my-session",
                    "chaitya session create dev --template claude-code",
                ],
            },
            {
                "name": "status",
                "description": "Show session state and info.",
                "params": [{"name": "name", "required": True, "description": "Session name."}],
                "examples": ["chaitya session status my-session"],
            },
            {
                "name": "attach",
                "description": "Attach to session terminal (tmux).",
                "params": [{"name": "name", "required": True, "description": "Session name."}],
                "examples": ["chaitya session attach my-session"],
            },
            {
                "name": "detach",
                "description": "Detach from session.",
                "params": [{"name": "name", "required": True, "description": "Session name."}],
                "examples": ["chaitya session detach my-session"],
            },
            {
                "name": "output",
                "description": "Read session terminal output.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {
                        "name": "idle-timeout",
                        "required": False,
                        "description": "Idle timeout in seconds.",
                    },
                ],
                "examples": [
                    "chaitya session output my-session",
                    "chaitya session output my-session --idle-timeout 1.0",
                ],
            },
            {
                "name": "send",
                "description": "Send input to session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {"name": "text", "required": False, "description": "Text to send."},
                    {"name": "newline", "required": False, "description": "Append newline."},
                    {
                        "name": "key",
                        "required": False,
                        "description": "Key name (enter/tab/space).",
                    },
                ],
                "examples": ["chaitya session send my-session --text 'echo hello' --newline"],
            },
            {
                "name": "signal",
                "description": "Send signal to session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {
                        "name": "sig",
                        "required": True,
                        "description": "Signal name (TERM, KILL, INT, HUP).",
                    },
                ],
                "examples": ["chaitya session signal my-session SIGTERM"],
            },
            {
                "name": "set-env",
                "description": "Set environment variable in session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {"name": "key", "required": True, "description": "Variable name."},
                    {"name": "value", "required": True, "description": "Variable value."},
                ],
                "examples": ["chaitya session set-env my-session --key FOO --value bar"],
            },
            {
                "name": "unset-env",
                "description": "Remove environment variable from session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {"name": "key", "required": True, "description": "Variable name."},
                ],
                "examples": ["chaitya session unset-env my-session --key FOO"],
            },
            {
                "name": "exec",
                "description": "Manage execution mode (enabled/disabled/readonly).",
                "params": [
                    {
                        "name": "action",
                        "required": True,
                        "description": "Action: enable, disable, readonly, status.",
                    },
                    {"name": "name", "required": False, "description": "Session name."},
                ],
                "examples": [
                    "chaitya session exec disable my-session",
                    "chaitya session exec status my-session",
                ],
            },
            {
                "name": "kill",
                "description": "Kill a session.",
                "params": [{"name": "name", "required": True, "description": "Session name."}],
                "examples": ["chaitya session kill my-session"],
            },
        ],
        "permissions": {
            "fs_read": ["."],
            "fs_write": ["/tmp"],
            "network": False,
            "can_emit_events": True,
        },
    },
    "input": {
        "name": "input",
        "description": "L0 Ingest: provide input data to the pipeline.",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "--text",
                "description": "Create stream from text.",
                "params": [{"name": "text", "required": False, "description": "Text content."}],
                "examples": ["chaitya input --text 'hello world'"],
            },
            {
                "name": "--file",
                "description": "Create stream from file.",
                "params": [
                    {
                        "name": "file",
                        "required": False,
                        "description": "File path.",
                        "multiple": True,
                    }
                ],
                "examples": [
                    "chaitya input --file myfile.txt",
                    "chaitya input --file a.txt --file b.txt --merge concat",
                ],
            },
            {
                "name": "--clipboard",
                "description": "Create stream from clipboard.",
                "params": [],
                "examples": ["chaitya input --clipboard"],
            },
        ],
        "permissions": {
            "fs_read": ["."],
            "fs_write": [],
            "network": False,
            "can_emit_events": False,
        },
    },
    "output": {
        "name": "output",
        "description": "L2 Present: format and filter command output.",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "--format",
                "description": "Set output format.",
                "params": [
                    {"name": "format", "required": False, "description": "Format: text, json."}
                ],
                "examples": ["chaitya output --format json"],
            },
            {
                "name": "--filter",
                "description": "Filter output lines by pattern.",
                "params": [{"name": "filter", "required": False, "description": "Filter pattern."}],
                "examples": ["chaitya output --filter ERROR"],
            },
        ],
        "permissions": {"fs_read": [], "fs_write": [], "network": False, "can_emit_events": False},
    },
    "watch": {
        "name": "watch",
        "description": "Observe events: query history or stream live.",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "--all",
                "description": "Watch all events (history query).",
                "params": [
                    {"name": "on", "required": False, "description": "Event type filter."},
                    {
                        "name": "exit-after",
                        "required": False,
                        "description": "Exit after N events.",
                    },
                    {"name": "limit", "required": False, "description": "Max events to return."},
                ],
                "examples": [
                    "chaitya watch --all",
                    "chaitya watch --all --on session_created --limit 10",
                ],
            },
            {
                "name": "--session",
                "description": "Watch events for a session.",
                "params": [
                    {"name": "name", "required": True, "description": "Session name."},
                    {"name": "on", "required": False, "description": "Event type filter."},
                    {
                        "name": "exit-after",
                        "required": False,
                        "description": "Exit after N events.",
                    },
                ],
                "examples": ["chaitya watch --session my-session"],
            },
            {
                "name": "--search",
                "description": "Full-text search of event log.",
                "params": [
                    {"name": "query", "required": True, "description": "Search query."},
                    {
                        "name": "since",
                        "required": False,
                        "description": "Since duration (e.g. 60s).",
                    },
                ],
                "examples": [
                    "chaitya watch --search ERROR",
                    "chaitya watch --search timeout --since 300s",
                ],
            },
            {
                "name": "--live",
                "description": "Stream live events (real-time).",
                "params": [
                    {"name": "session", "required": False, "description": "Session name."},
                    {"name": "on", "required": False, "description": "Event type filter."},
                    {
                        "name": "exit-after",
                        "required": False,
                        "description": "Exit after N events.",
                    },
                    {"name": "timeout", "required": False, "description": "Timeout in seconds."},
                ],
                "examples": [
                    "chaitya watch --live --session my-session",
                    "chaitya watch --live --all --exit-after 5",
                ],
            },
        ],
        "permissions": {"fs_read": [], "fs_write": [], "network": False, "can_emit_events": False},
    },
    "registry": {
        "name": "registry",
        "description": "Manage adapter registry.",
        "contract_version": "1",
        "depends_on": [],
        "commands": [
            {
                "name": "list",
                "description": "List all installed adapters.",
                "params": [],
                "examples": ["chaitya registry list"],
            },
            {
                "name": "disable",
                "description": "Disable an adapter.",
                "params": [{"name": "name", "required": True, "description": "Adapter name."}],
                "examples": ["chaitya registry disable my-adapter"],
            },
            {
                "name": "enable",
                "description": "Enable an adapter.",
                "params": [{"name": "name", "required": True, "description": "Adapter name."}],
                "examples": ["chaitya registry enable my-adapter"],
            },
            {
                "name": "validate",
                "description": "Validate an adapter contract.",
                "params": [{"name": "name", "required": True, "description": "Adapter name."}],
                "examples": ["chaitya registry validate my-adapter"],
            },
        ],
        "permissions": {"fs_read": [], "fs_write": [], "network": False, "can_emit_events": False},
    },
}

# System adapters whose load failure halts boot (PRD §3.6)
# Kernel commands are always available (registered at boot):
# info, session, input, output, watch, registry
DEFAULT_SYSTEM_ADAPTERS = frozenset({"file", "shell", "route", "process", "registry"})


class _AdapterEventBusBridge:
    """Adapter-facing wrapper over the core event bus."""

    def __init__(self, event_bus: SqliteEventBus) -> None:
        self._event_bus = event_bus
        self._subscriptions: dict[str, Any] = {}

    async def emit(self, event: Any) -> None:
        if not isinstance(event, Event):
            event = Event(
                type=getattr(event, "type", ""),
                source_adapter=getattr(event, "source_adapter", ""),
                session_id=getattr(event, "session_id", None),
                exit_code=getattr(event, "exit_code", None),
                duration_ms=getattr(event, "duration_ms", None),
                payload=dict(getattr(event, "payload", {})),
                parent_event_id=getattr(event, "parent_event_id", None),
                request_id=getattr(event, "request_id", None),
            )
        await self._event_bus.emit(event)

    async def subscribe(
        self,
        handler: Any,
        event_types: list[str] | None = None,
        session_id: str | None = None,
    ) -> Any:
        subscription = await self._event_bus.subscribe(
            EventFilter(event_types=event_types, session_id=session_id),
            handler,
        )
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    async def unsubscribe(self, subscription_id: str) -> None:
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is not None:
            await self._event_bus.unsubscribe(subscription)


_instance: "Kernel | None" = None


class Kernel:
    """Chaitya microkernel — the central orchestrator.

    Composes: Store, EventBus, SessionManager, PipelineOrchestrator,
    AdapterRegistry.

    Lifecycle: ``boot()`` → accept commands via ``dispatch()`` → ``shutdown()``.
    """

    def __init__(
        self,
        *,
        config: CoreConfig | None = None,
        db_path: str | Path = ":memory:",
        cli_name: str = "chaitya",
        session_backend: str = "auto",
        adapter_search_paths: list[str] | None = None,
        enabled_adapters: list[str] | None = None,
        disabled_adapters: list[str] | None = None,
        system_adapters: frozenset[str] | None = None,
        overflow_dir: str | None = None,
        stuck_threshold_seconds: int = 60,
        max_events_per_second: int = 1000,
        max_log_size_bytes: int = 1_073_741_824,
        templates_dir: str | None = None,
        input_timeout_seconds: int = 300,
        event_bus: EventBusProtocol | None = None,
    ) -> None:
        # Build a default config if none provided (for programmatic use)
        if config is None:
            config = CoreConfig(
                kernel=KernelConfig(cli_name=cli_name),
                session=SessionConfig(
                    backend=session_backend, stuck_threshold_seconds=stuck_threshold_seconds
                ),
                adapter_search_paths=adapter_search_paths or [],
                enabled_adapters=enabled_adapters or [],
                disabled_adapters=disabled_adapters or [],
            )
        self._config = config
        self.cli_name = cli_name
        self._system_adapters = system_adapters or DEFAULT_SYSTEM_ADAPTERS
        self._booted = False
        self._shutting_down = False
        self._boot_time: float | None = None
        self._input_timeout_seconds: int = input_timeout_seconds
        self._input_timeout_task: asyncio.Task[None] | None = None
        self._route_action_sub: asyncio.Task[None] | None = None
        self._subscriptions: dict[str, Any] = {}

        # --- Subsystem composition ---
        # Use provided event bus, or build one from config (default: SqliteEventBus)
        self._event_bus: EventBusProtocol = event_bus or SqliteEventBus(
            db_path=db_path,
            max_events_per_second=max_events_per_second,
        )
        self._store = SqliteStore(
            db_path=db_path,
            max_events_per_second=max_events_per_second,
            max_log_size_bytes=max_log_size_bytes,
            event_bus=self._event_bus,  # type: ignore[arg-type]
        )
        self._adapter_bus = _AdapterEventBusBridge(self._event_bus)
        self._backend = self._build_session_backend(session_backend)
        self._session_mgr = SessionManager(
            backend=self._backend,
            store=self._store,
            event_bus=self._event_bus,
            stuck_threshold_seconds=stuck_threshold_seconds,
            templates_dir=templates_dir,
        )
        self._pipeline = PipelineOrchestrator(overflow_dir=overflow_dir)
        self._registry = AdapterRegistry(
            search_paths=adapter_search_paths or [],
            enabled_adapters=enabled_adapters or [],
            disabled_adapters=disabled_adapters or [],
        )
        self._pipeline._registry = self._registry  # type: ignore[attr-defined]

    @classmethod
    def from_config(cls, config: CoreConfig) -> Kernel:
        """Create a Kernel from a CoreConfig object."""
        event_bus = cls._build_event_bus(
            config.event_bus,
            config.store.path or ":memory:",
            config.store.max_events_per_second,
        )
        return cls(
            config=config,
            db_path=config.store.path or ":memory:",
            cli_name=config.kernel.cli_name,
            session_backend=config.session.backend,
            adapter_search_paths=[
                config.adapters_config_dir,
                *config.adapter_search_paths,
            ],
            enabled_adapters=config.enabled_adapters,
            disabled_adapters=config.disabled_adapters,
            system_adapters=frozenset(config.system_adapters),
            overflow_dir=config.kernel.tmp_dir or None,
            stuck_threshold_seconds=config.session.stuck_threshold_seconds,
            max_events_per_second=config.store.max_events_per_second,
            max_log_size_bytes=config.store.max_log_size_bytes,
            templates_dir=config.templates_dir or None,
            event_bus=event_bus,
        )

    @staticmethod
    def _build_session_backend(session_backend: str) -> Any:
        backend = session_backend.strip().lower()
        if backend == "auto":
            if sys.platform == "win32":
                from chaitya.core.backends.psmux import PsmuxBackend

                return PsmuxBackend()
            else:
                return TmuxSessionBackend()
        if backend == "tmux":
            return TmuxSessionBackend()
        if backend == "psmux":
            from chaitya.core.backends.psmux import PsmuxBackend  # type: ignore[import]

            return PsmuxBackend()
        raise ValueError(
            f"Unsupported session backend {session_backend!r}. Supported: auto (default), tmux, psmux."
        )

    @staticmethod
    def _build_event_bus(
        config: EventBusConfig,
        db_path: str,
        max_events_per_second: int,
    ) -> SqliteEventBus:
        """Build an EventBus implementation from config.

        Currently supports 'sqlite' (default). Other backends can be added
        by extending this factory. The returned bus can be shared between
        the store and the kernel.

        Reference: PRD §3.4, §12 — event bus is a swappable extension point.
        """
        backend = config.backend.strip().lower()
        if backend == "sqlite":
            return SqliteEventBus(
                db_path=db_path,
                max_events_per_second=max_events_per_second,
            )
        if backend == "redis":
            raise NotImplementedError(
                "Redis event bus: implement and register via "
                "chaitya.core.protocols.EventBusProtocol"
            )
        if backend == "memory":
            raise NotImplementedError(
                "In-memory event bus: implement and register via "
                "chaitya.core.protocols.EventBusProtocol"
            )
        raise ValueError(
            f"Unsupported event bus backend {config.backend!r}. "
            f"Supported: sqlite. Others: implement EventBusProtocol."
        )

    # -- Public properties --

    @property
    def is_booted(self) -> bool:
        return self._booted

    @property
    def store(self) -> SqliteStore:
        return self._store

    @property
    def event_bus(self) -> SqliteEventBus:
        return self._event_bus

    @property
    def session_manager(self) -> SessionManager:
        return self._session_mgr

    @property
    def pipeline(self) -> PipelineOrchestrator:
        return self._pipeline

    @property
    def registry(self) -> AdapterRegistry:
        return self._registry

    @property
    def uptime_seconds(self) -> float:
        if self._boot_time is None:
            return 0.0
        return time.monotonic() - self._boot_time

    # -- Boot Sequence (PRD §3.1) --

    async def boot(self) -> None:
        """Execute the 10-step boot sequence.

        Raises KernelBootError on system-level failures.
        """
        global _instance
        if self._booted:
            raise KernelBootError("Kernel is already booted")

        _instance = self

        # Reconfigure logging with config values if provided
        debug_log = self._config.kernel.debug_log
        log_level = self._config.kernel.log_level
        if debug_log or log_level != "info":
            _configure_kernel_logging(debug_log, log_level)

        logger.info("Kernel boot sequence starting...")
        boot_start = time.monotonic()

        try:
            # Step 1: Load core config (handled by caller / core.yaml loader)
            logger.info("Step 1/10: Core config loaded")

            # Step 2: Open persistent store
            await self._store.open()
            logger.info("Step 2/10: Store opened")

            # Step 3: Open event bus (shared DB with store)
            await self._event_bus.open()
            logger.info("Step 3/10: Event bus opened")

            # Step 4: Registry: discover all installed adapters
            packages = await self._registry.discover()
            logger.info("Step 4/10: Discovered %d adapter package(s)", len(packages))

            # Step 6: Dependency graph: topological sort
            graph = self._registry.build_dependency_graph(packages)
            logger.info(
                "Step 6/10: Dependency graph built (%d in order, %d rejected)",
                len(graph.load_order),
                len(graph.rejected),
            )

            # Step 5: Load adapters (system + user) in dependency order
            loaded, failed = await self._registry.load_all(
                packages, system_adapters=self._system_adapters
            )
            for name in loaded:
                await self._event_bus.emit(Event(type=ADAPTER_LOADED, payload={"adapter": name}))
            for name in failed:
                await self._event_bus.emit(Event(type=ADAPTER_REJECTED, payload={"adapter": name}))
            logger.info(
                "Step 5/10: Loaded %d adapter(s), %d failed",
                len(loaded),
                len(failed),
            )

            # Step 6: Restore session state from store
            await self._session_mgr.start()
            logger.info("Step 6/10: Session state restored")

            # Step 7: Register kernel command handlers in pipeline
            self._register_kernel_handlers()
            self._register_loaded_adapter_handlers()
            self._register_event_subscribers()
            logger.info("Step 7/10: Kernel command handlers registered")

            # Step 8: Wire registry into SDK context
            registry_proxy.set_registry(self._registry)
            logger.info("Step 8/10: SDK registry context wired")

            # Step 9: Emit kernel_started, accept commands
            self._boot_time = time.monotonic()
            self._booted = True
            self._input_timeout_task = asyncio.create_task(self._input_timeout_loop())
            await self._event_bus.emit(
                Event(
                    type=KERNEL_STARTED,
                    source_adapter="kernel",
                    payload={
                        "version": __version__,
                        "adapters_loaded": loaded,
                        "adapters_failed": failed,
                        "boot_ms": int((time.monotonic() - boot_start) * 1000),
                    },
                )
            )
            logger.info("Step 9/9: Kernel started — accepting commands")

        except AdapterLoadError as exc:
            raise KernelBootError(f"System adapter failed to load: {exc}") from exc
        except KernelBootError:
            raise
        except Exception as exc:
            import traceback

            raise KernelBootError(
                f"Boot failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            ) from exc

    # -- Shutdown (PRD §3.1) --

    async def shutdown(self) -> None:
        """Graceful shutdown sequence.

        1. Stop accepting new commands
        2. Drain pipeline queue
        3. Flush event log
        4. Handle sessions per on_shutdown policy
        5. Write final state to store
        6. Emit kernel_shutting_down
        7. Close all subsystems
        """
        global _instance
        if self._shutting_down:
            return
        self._shutting_down = True
        _instance = None
        logger.info("Kernel shutdown sequence starting...")

        try:
            # Emit shutdown event before closing bus
            if self._booted:
                await self._event_bus.emit(
                    Event(
                        type=KERNEL_SHUTTING_DOWN,
                        source_adapter="kernel",
                        payload={"uptime_seconds": self.uptime_seconds},
                    )
                )

            # Cancel input timeout monitor
            if self._input_timeout_task and not self._input_timeout_task.done():
                self._input_timeout_task.cancel()
                try:
                    await self._input_timeout_task
                except asyncio.CancelledError:
                    pass
                self._input_timeout_task = None

            # Cancel route action subscriber
            if self._route_action_sub and not self._route_action_sub.done():
                self._route_action_sub.cancel()
                try:
                    await self._route_action_sub
                except asyncio.CancelledError:
                    pass
                self._route_action_sub = None

            # Stop session manager (cancels stuck monitor)
            await self._session_mgr.stop()

            # Close store (which closes event bus too)
            await self._store.close()

        except Exception as exc:
            logger.error("Error during shutdown: %s", exc)
        finally:
            self._booted = False
            self._shutting_down = False
            self._boot_time = None
            logger.info("Kernel shutdown complete")

    # -- Command Dispatch (PRD §3.2) --

    async def dispatch(self, expression: str) -> CommandOutput:
        """Parse and execute a command expression.

        Routes kernel commands internally. Routes everything else
        through the pipeline to adapter handlers.

        PRD §3.2: ``{{CLI_NAME}} <adapter>`` with no subcommand is
        identical to ``{{CLI_NAME}} info <adapter>``.

        All adapter commands run in session context. If no session
        is specified, an ephemeral session is created/used.
        """
        if not self._booted:
            raise RuntimeError("Kernel not booted — call boot() first")
        if self._shutting_down:
            raise RuntimeError("Kernel is shutting down")

        chain = self._pipeline.parse_chain(expression)
        ctx = PipelineContext()

        expr = expression.strip()
        parts = expr.split()
        if (
            len(parts) == 1
            and parts[0] not in KERNEL_COMMANDS
            and parts[0] in self._registry.loaded_names
        ):
            chain = self._pipeline.parse_chain(f"info {parts[0]}")

        first_adapter = self._get_first_adapter_in_chain(chain)
        if first_adapter:
            session_id = await self._ensure_session_for_command(chain, ctx)
            if session_id:
                ctx.session_id = session_id

        return await self._pipeline.run(chain, ctx)

    def _get_first_adapter_in_chain(self, chain: Any) -> str | None:
        """Get the first adapter name in a command chain."""
        if not chain or not chain.steps:
            return None
        cmd = chain.steps[0][0]
        if cmd.adapter in KERNEL_COMMANDS:
            return None
        return cmd.adapter

    async def _ensure_session_for_command(self, chain: Any, ctx: PipelineContext) -> str | None:
        """Ensure an adapter command has a session. Use/create 'default' session."""
        if not chain or not chain.steps:
            return None
        cmd = chain.steps[0][0]
        if cmd.adapter in KERNEL_COMMANDS:
            return None
        session_id = cmd.args.get("session")
        if session_id:
            return session_id
        session_id = ctx.session_id
        if session_id:
            return session_id
        try:
            record = await self._session_mgr.status("default")
            return "default"
        except Exception:
            pass
        await self._session_mgr.create("default")
        return "default"

    # -- Kernel Command Handlers (PRD §5) --

    def _register_kernel_handlers(self) -> None:
        """Register the 6 kernel commands as adapter handlers in the registry.

        These handlers are kernel-owned but registered as adapter handlers so they
        participate in the same pipeline flow as regular adapters. The kernel
        injects its internals (session_manager, store) into the SDK context
        so these handlers can access them.
        """
        from dataclasses import asdict

        for name, method, contract_dict in [
            ("info", self._handle_info, _KERNEL_COMMAND_CONTRACTS.get("info", {})),
            ("session", self._handle_session, _KERNEL_COMMAND_CONTRACTS.get("session", {})),
            ("input", self._handle_input, _KERNEL_COMMAND_CONTRACTS.get("input", {})),
            ("output", self._handle_output, _KERNEL_COMMAND_CONTRACTS.get("output", {})),
            ("watch", self._handle_watch, _KERNEL_COMMAND_CONTRACTS.get("watch", {})),
            ("registry", self._handle_registry, _KERNEL_COMMAND_CONTRACTS.get("registry", {})),
        ]:
            pkg = AdapterPackage(
                name=name,
                module_name="kernel",
                module_path=Path(__file__),
                adapter_type="kernel_command",
                contract=None,
                handler=method,
                metadata={},
            )
            if contract_dict:
                perms_dict = contract_dict.get("permissions", {})
                if perms_dict:
                    perms_dict = AdapterPermissions(**perms_dict)
                else:
                    perms_dict = AdapterPermissions()
                pkg.contract = AdapterContract(
                    name=name,
                    description=contract_dict.get("description", ""),
                    permissions=perms_dict,
                )
            else:
                pkg.contract = None
            self._registry.loaded_adapters[name] = pkg
            self._pipeline.register_handler(
                name,
                self._make_adapter_handler(
                    name,
                    method,
                    pkg.contract.permissions if pkg.contract else AdapterPermissions(),
                    pkg.contract,
                ),
            )

    def _register_loaded_adapter_handlers(self) -> None:
        for name, package in self._registry.loaded_adapters.items():
            if package.handler is None:
                continue
            self._pipeline.register_handler(
                name,
                self._make_adapter_handler(
                    name,
                    package.handler,
                    package.contract.permissions,
                    package.contract,
                ),
            )

    def _make_adapter_handler(
        self,
        name: str,
        handler: Any,
        permissions: AdapterPermissions,
        contract: Any,
    ) -> AdapterHandler:
        async def _wrapped(
            input_stream: ChaityaStream,
            ctx: PipelineContext,
        ) -> tuple[bytes, int]:
            return await self._invoke_adapter(
                name, handler, permissions, contract, input_stream, ctx
            )

        return _wrapped

    def _get_ctx_args(self, ctx: Any) -> dict[str, Any]:
        """Get args dict from context, supporting both PipelineContext and SdkSessionContext."""
        return getattr(ctx, "args", None) or getattr(ctx, "env", {})

    def _build_sdk_context(
        self,
        ctx: PipelineContext,
        args: dict[str, Any] | None = None,
        *,
        session_state: SdkSessionState = SdkSessionState.IDLE,
    ) -> SdkSessionContext:
        session_ctx = SdkSessionContext(
            session_id=ctx.session_id,
            session_state=session_state,
            args=args
            or {
                "subcommand": ctx.env.get("__subcommand__", ""),
                "__raw_args__": ctx.env.get("__args__", []),
                **{k: v for k, v in ctx.env.items() if not k.startswith("__")},
            },
            env={k: str(v) for k, v in ctx.env.items() if not k.startswith("__")},
            dry_run=ctx.dry_run,
        )
        session_ctx._kernel = self  # type: ignore[attr-defined]
        session_ctx.kernel_uptime = self.uptime_seconds  # type: ignore[attr-defined]
        return session_ctx

    def _inject_missing_suspend_args(
        self,
        contract: Any,
        args: dict[str, Any],
    ) -> None:
        subcommand = str(args.get("subcommand", ""))
        for command in getattr(contract, "commands", []):
            if command.name != subcommand:
                continue
            for param in command.params:
                if param.required and param.on_missing == "suspend" and not args.get(param.name):
                    raise SdkSuspension(
                        SdkInputSpec(
                            name=param.name,
                            prompt=param.description or f"Provide {param.name}",
                        )
                    )
            return

    async def _invoke_adapter(
        self,
        name: str,
        handler: Any,
        permissions: AdapterPermissions,
        contract: Any,
        input_stream: ChaityaStream,
        ctx: PipelineContext,
        *,
        args: dict[str, Any] | None = None,
    ) -> tuple[bytes, int]:
        is_dry_run = bool(ctx.env.get("dry-run"))
        is_confirm = bool(ctx.env.get("confirm"))

        if ctx.session_id:
            exec_mode = await self._session_mgr.get_exec_mode(ctx.session_id)
            if exec_mode == "readonly":
                sub = ctx.env.get("__subcommand__", "")
                return (
                    f"[readonly] Session '{ctx.session_id}' is in readonly mode. "
                    f"Execution blocked for '{name} {sub}'. "
                    f"Use 'session exec enable {ctx.session_id}' to enable.\n".encode("utf-8"),
                    1,
                )
            if exec_mode == "disabled" and not is_dry_run and not is_confirm:
                sub = ctx.env.get("__subcommand__", "")
                raw = ctx.env.get("__args__", [])
                args_str = " ".join(str(a) for a in raw)
                supports_dry = getattr(contract, "supports_dry_run", False)
                if supports_dry:
                    dry_hint = f"\n[dry-run] This command supports --dry-run for a preview."
                else:
                    dry_hint = ""
                return (
                    f"[dry-run] Session '{ctx.session_id}' exec is disabled.\n"
                    f"  Would execute: {name} {sub} {args_str}\n"
                    f"  Run 'session exec enable {ctx.session_id}' to enable execution."
                    f"{dry_hint}\n".encode("utf-8"),
                    0,
                )

        sdk_perms = SdkAdapterPermissions(**permissions.__dict__)
        sdk_event_bus._configure(self._adapter_bus, name, sdk_perms)
        sdk_session_manager.set_session_manager(self._session_mgr)
        sdk_store.set_store(self._store)
        sdk_kernel_info.set_kernel_info(
            {
                "version": __version__,
                "cli_name": self.cli_name,
                "uptime_seconds": self.uptime_seconds,
                "adapters_loaded": list(self._registry.loaded_names),
            }
        )
        _configure_permissions(name, sdk_perms)
        sdk_stream = SdkChaityaStream(
            content=input_stream.content,
            declared_type=input_stream.declared_type,
            detected_type=input_stream.detected_type,
            source=input_stream.source,
            size_bytes=input_stream.size_bytes,
            encoding=input_stream.encoding,
            exit_code=getattr(input_stream, "exit_code", 0),
        )
        sdk_args = args or {
            "subcommand": ctx.env.get("__subcommand__", ""),
            "__raw_args__": ctx.env.get("__args__", []),
            **{k: v for k, v in ctx.env.items() if not k.startswith("__")},
        }
        try:
            self._inject_missing_suspend_args(contract, sdk_args)
            sdk_ctx = self._build_sdk_context(ctx, sdk_args)
            result = handler(sdk_stream, sdk_ctx)
            if inspect.isawaitable(result):
                result = await result
        except SdkSuspension as suspension:
            if ctx.session_id:
                try:
                    await self._session_mgr.update_state(ctx.session_id, SessionState.WAITING)
                    self._session_mgr.store_suspended_command(
                        ctx.session_id,
                        {
                            "adapter_name": name,
                            "handler": handler,
                            "contract": contract,
                            "permissions": permissions,
                            "input_stream": input_stream,
                            "ctx": ctx,
                            "spec": suspension.spec,
                            "args": dict(sdk_args),
                        },
                    )
                except Exception:
                    pass
            await self._event_bus.emit(
                Event(
                    type=SESSION_WAITING,
                    source_adapter=name,
                    session_id=ctx.session_id,
                    payload={
                        "name": suspension.spec.name,
                        "prompt": suspension.spec.prompt,
                    },
                )
            )
            prompt = suspension.spec.prompt or f"Input required: {suspension.spec.name}"
            return (
                f"[waiting] {prompt}\nSend input with: session send-input {ctx.session_id or '<session>'} <value>".encode(),
                0,
            )

        if isinstance(result, tuple):
            output, exit_code = result
        else:
            output, exit_code = result, 0
        if isinstance(output, str):
            output = output.encode("utf-8")
        if not isinstance(output, bytes):
            output = str(output).encode("utf-8")

        await self._evaluate_output_routing(
            name,
            output,
            int(exit_code),
            ctx.session_id,
        )

        return output, int(exit_code)

    async def _evaluate_output_routing(
        self,
        adapter_name: str,
        output: bytes,
        exit_code: int,
        session_id: str | None,
    ) -> None:
        """Evaluate adapter output_routing rules and emit events (PRD §7).

        Rules are simple conditions evaluated after L1 completes.
        Supported patterns:
            - "exit_code != 0"
            - "exit_code == 0"
            - "stdout contains 'text'"
            - "stderr contains 'text'"
        """
        import re

        pkg = self._registry.get_adapter(adapter_name)
        if pkg is None or pkg.contract is None:
            return

        for rule in pkg.contract.output_routing:
            condition = rule.condition.strip()
            emit_type = rule.emit_event_type
            if not condition or not emit_type:
                continue

            fired = False
            try:
                stdout_text = output.decode("utf-8", errors="replace")
                if " contains " in condition:
                    match = re.match(r"(stdout|stderr)\s+contains\s+'([^']*)'", condition)
                    if match:
                        source_text = stdout_text
                        if match.group(1) == "stderr":
                            source_text = ""
                        fired = match.group(2) in source_text
                elif condition.startswith("exit_code"):
                    fired = eval(condition, {"exit_code": exit_code})
            except Exception:
                pass

            if fired:
                await self._event_bus.emit(
                    Event(
                        type=emit_type,
                        source_adapter=adapter_name,
                        session_id=session_id,
                        exit_code=exit_code,
                        payload={"output_length": len(output)},
                    )
                )

    def _register_event_subscribers(self) -> None:
        """Register kernel-level event bus subscribers.

        These handle cross-cutting concerns that need to react to events
        without going through adapter dispatch.
        """
        self._route_action_sub = asyncio.create_task(self._subscribe_route_action())
        self._input_resume_sub = asyncio.create_task(self._subscribe_input_resume())

    async def _subscribe_input_resume(self) -> None:
        """Subscribe to session_input_received events and resume suspended commands."""
        from chaitya.core.types import SESSION_INPUT_RECEIVED

        async def _on_input_received(event: Any) -> None:
            session_id = event.session_id
            if not session_id:
                return
            suspended = self._session_mgr.get_suspended_command(session_id)
            if not suspended:
                return
            input_value = event.payload.get("input", "")
            adapter_name = suspended["adapter_name"]
            handler = suspended["handler"]
            contract = suspended["contract"]
            permissions = suspended["permissions"]
            input_stream = suspended["input_stream"]
            ctx = suspended["ctx"]
            spec = suspended["spec"]
            args = suspended["args"]
            resumed_args = dict(args)
            resumed_args[spec.name] = input_value
            try:
                await self._session_mgr.update_state(session_id, SessionState.IDLE)
            except Exception:
                pass
            try:
                await self._invoke_adapter(
                    adapter_name,
                    handler,
                    permissions,
                    contract,
                    input_stream,
                    ctx,
                    args=resumed_args,
                )
            except Exception as exc:
                logger.error("Failed to resume suspended command: %s", exc)

        sub = await self._event_bus.subscribe(
            EventFilter(event_types=[SESSION_INPUT_RECEIVED]),
            _on_input_received,
        )
        self._subscriptions[sub.subscription_id] = sub

    async def _subscribe_route_action(self) -> None:
        """Subscribe to route.action_requested events and dispatch actions."""

        async def _on_route_action(event: Any) -> None:
            action = str(event.payload.get("action", ""))
            matched = event.payload.get("matched_content", "")
            if not action:
                return
            logger.info("[route --do] dispatching: %s", action)
            try:
                result = await self.dispatch(action)
                logger.info(
                    "[route --do] completed: %s (exit=%d)",
                    action,
                    result.exit_code,
                )
            except Exception as exc:
                logger.error("[route --do] failed: %s — %s", action, exc)

        sub = await self._event_bus.subscribe(
            EventFilter(event_types=["route.action_requested"]),
            _on_route_action,
        )
        self._subscriptions[sub.subscription_id] = sub

    async def _input_timeout_loop(self) -> None:
        """Background monitor: expire WAITING sessions after timeout.

        If a session is WAITING for input and no response within
        ``self._input_timeout_seconds``, the session is marked timed-out.
        """
        while True:
            await asyncio.sleep(10)
            if self._shutting_down:
                return

    async def _handle_info(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``info [adapter] [subcommand]``.

        - No args → kernel overview + kernel commands + adapter list
        - ``info <adapter>`` → adapter contract details
        - ``info --kernel`` → kernel status
        """
        args = ctx.env
        adapter_name = args.get("__subcommand__", "")

        if adapter_name == "--kernel" or args.get("kernel"):
            sessions = await self._store.list_sessions()
            active = [s for s in sessions if s.state.value != "dead"]
            info = {
                "kernel_version": __version__,
                "cli_name": self.cli_name,
                "uptime_seconds": round(self.uptime_seconds, 1),
                "adapters_loaded": len(self._registry.loaded_adapters),
                "adapters_failed": 0,
                "active_sessions": len(active),
                "total_sessions": len(sessions),
                "event_bus_backend": "sqlite",
                "store_backend": "sqlite",
            }
            info_lines = [f"{k}: {v}" for k, v in info.items()]
            return "\n".join(info_lines).encode("utf-8"), 0

        if adapter_name and adapter_name in self._registry.loaded_names:
            pkg = self._registry.get_adapter(adapter_name)
            if pkg and pkg.contract:
                c = pkg.contract
                lines = [
                    f"Adapter: {c.name}",
                    f"Description: {c.description}",
                    f"Version: {c.contract_version}",
                    "Commands:",
                ]
                for cmd in c.commands:
                    lines.append(f"  {cmd.name:16s} {cmd.description}")
                    for p in cmd.params:
                        req = "[required] " if p.required else ""
                        lines.append(f"    --{p.name}: {req}{p.description}")
                if c.depends_on:
                    lines.append(f"Dependencies: {', '.join(c.depends_on)}")
                return "\n".join(lines).encode("utf-8"), 0

        # Default: compact overview for LLM consumption
        sessions = await self._store.list_sessions()
        active = [s for s in sessions if s.state.value not in ("dead",)]
        lines: list[str] = []

        lines.append(f"Chaitya Core v{__version__}  (uptime: {round(self.uptime_seconds, 1)}s)")
        lines.append("")
        lines.append("info [name]    Show adapter or kernel details")
        lines.append(
            "session       list|create|status|attach|view|detach|output|send|set-env|signal|kill"
        )
        lines.append(
            "watch         --all|--session <name>|--search <query>|--live --session <name>"
        )
        lines.append("input         list|respond <id> <value>")
        lines.append("output        --filter <pattern>|--format json|text")
        lines.append("registry      list|disable|enable|validate <name>")
        lines.append(f"<adapter> <cmd>  Run adapter command")
        lines.append("")
        lines.append(f"SESSIONS: {', '.join(s.name for s in active) if active else 'none'}")

        adapters = self._registry.loaded_adapters
        if adapters:
            lines.append("ADAPTERS:")
            for name, pkg in sorted(adapters.items()):
                cmds = (
                    ", ".join(cmd.name for cmd in (pkg.contract.commands if pkg.contract else []))
                    or "-"
                )
                lines.append(f"  {name:12} {cmds}")

        return "\n".join(lines).encode("utf-8"), 0

    async def _handle_session(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``session <subcommand>``."""
        args = self._get_ctx_args(ctx)
        sub = args.get("subcommand", args.get("__subcommand__", "list"))
        positional = args.get("__raw_args__", args.get("__args__", []))

        if sub == "list":
            records = await self._store.list_sessions()
            if not records:
                return b"No sessions. Use 'session create <name>' to create one.", 0
            lines = ["NAME                 STATE      TEMPLATE             LAST ACTIVITY"]
            lines.append("-" * 80)
            for r in records:
                tmpl = r.template or "-"
                lines.append(f"{r.name:20s} {r.state.value:10s} {tmpl:20s} {r.last_activity}")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "status":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session status <name>", 1
            rec = await self._store.get_session(name)
            if rec is None:
                return f"Session '{name}' not found.".encode(), 1
            exec_state = rec.metadata.get("exec_state", "disabled")
            lines = [
                f"Name: {rec.name}",
                f"State: {rec.state.value}",
                f"Exec: {rec.exec_mode}",
                f"Created: {rec.created_at}",
            ]
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "exec":
            action = positional[0] if positional else ""
            name = args.get("name") or (positional[1] if len(positional) > 1 else "")
            if action == "status":
                if not name:
                    return b"Usage: session exec status <name>", 1
                mode = await self._session_mgr.get_exec_mode(name)
                return f"Session '{name}' exec mode: {mode}\n".encode("utf-8"), 0
            if action in ("enable", "disable", "readonly"):
                if not name:
                    return f"Usage: session exec {action} <name>".encode("utf-8"), 1
                mode_map = {
                    "enable": "enabled",
                    "disable": "disabled",
                    "readonly": "readonly",
                }
                ok = await self._session_mgr.set_exec_mode(name, mode_map[action])
                if not ok:
                    return f"Session '{name}' not found.".encode("utf-8"), 1
                return f"Session '{name}' exec mode set to {action}.\n".encode("utf-8"), 0
            return (
                b"Usage: session exec <enable|disable|readonly|status> <name>\n"
                b"  enable   - allow commands to execute\n"
                b"  disable  - dry-run only (default)\n"
                b"  readonly - no execution, no dry-run\n"
                b"  status   - show current exec state\n"
            ), 1

        if sub == "create":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session create <name> [--template <template-name>]", 1
            template = args.get("template")
            try:
                handle = await self._session_mgr.create(name=name, template=template)
                msg = f"Session '{name}' created"
                if template:
                    msg += f" (template: {template})"
                msg += "."
                return msg.encode("utf-8"), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub in ("send", "send-input"):
            name = (
                args.get("name")
                or (positional[0] if positional else "")
                or ctx.session_id
                or "default"
            )
            if not name:
                return b"Usage: session send-input [name] <text> [--newline] [--key <key>]", 1
            text_parts = [a for a in positional[1:] if not a.startswith("--")]
            text = str(args.get("text", "") or " ".join(text_parts) if text_parts else "")
            newline = bool(args.get("newline"))
            key = str(args.get("key", "")).lower()
            if key:
                key_map = {
                    "enter": b"\n",
                    "return": b"\n",
                    "tab": b"\t",
                    "space": b" ",
                }
                data = key_map.get(key)
                if data is None:
                    return f"Unsupported key: {key}".encode(), 1
            else:
                data = text.encode("utf-8")
                if newline:
                    data += b"\n"
            try:
                await self._session_mgr.send_input(name, data)
                return f"Input sent to session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "output":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session output <name> [--idle-timeout 0.2]", 1
            idle_timeout = float(args.get("idle_timeout", args.get("idle-timeout", "0.2")))
            try:
                output = await self._session_mgr.read_output(
                    name,
                    idle_timeout_seconds=idle_timeout,
                )
                return output, 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "attach":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session attach <name>", 1
            try:
                await self._session_mgr.attach(name)
                return f"Attached to session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "view":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session view <name>", 1
            try:
                content = await self._session_mgr.view(name)
                if isinstance(content, bytes):
                    return content, 0
                return content.encode("utf-8"), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "detach":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session detach <name>", 1
            try:
                await self._session_mgr.detach(name)
                return f"Detached from session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "signal":
            name = args.get("name") or (positional[0] if positional else "")
            sig = (
                args.get("sig")
                or args.get("signal")
                or (positional[1] if len(positional) > 1 else "")
            )
            if not name or not sig:
                return b"Usage: session signal <name> <signal>", 1
            try:
                await self._session_mgr.signal(name, str(sig))
                return f"Signal {sig} sent to session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "set-env":
            name = args.get("name") or (positional[0] if positional else "")
            key = str(args.get("key", "") or (positional[1] if len(positional) > 1 else ""))
            value = str(args.get("value", ""))
            if not name or not key:
                return b"Usage: session set-env <name> --key <KEY> --value <VALUE>", 1
            try:
                await self._session_mgr.set_env(name, key, value)
                return f"Environment set for session '{name}': {key}".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "unset-env":
            name = args.get("name") or (positional[0] if positional else "")
            key = str(args.get("key", "") or (positional[1] if len(positional) > 1 else ""))
            if not name or not key:
                return b"Usage: session unset-env <name> --key <KEY>", 1
            try:
                await self._session_mgr.unset_env(name, key)
                return f"Environment removed for session '{name}': {key}".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "kill":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session kill <name>", 1
            try:
                await self._session_mgr.kill(name)
                return f"Session '{name}' killed.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        return f"Unknown session subcommand: {sub}".encode(), 1

    async def _handle_input(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``input <options>`` — L0 Ingest and input request responses.

        L0 Ingest (PRD §4):
            input --text "content"          Create stream from text
            input --file <path>             Read file into stream
            input --file <path> --type <m>  Read file with specified MIME type
            input --clipboard               Read clipboard into stream
            input --file a.txt --file b.txt --merge concat  Merge multiple files

        Input Request Responses:
            input list                      List pending input requests
            input respond <id> <value>      Respond to a pending request
        """
        args = self._get_ctx_args(ctx)
        sub = args.get("subcommand", "list")
        positional = args.get("__raw_args__", [])

        if sub == "list":
            sessions = await self._store.list_sessions()
            waiting = [s for s in sessions if s.state == SessionState.WAITING]
            if not waiting:
                return (
                    b"No sessions waiting for input. Use 'session status' to check all sessions.",
                    0,
                )
            lines = [f"{'SESSION':30s} {'STATE':10s} LAST ACTIVITY"]
            for s in waiting:
                lines.append(f"{s.name:30s} {s.state.value:10s} {s.last_activity}")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "respond":
            return (
                b"Use 'session send-input <name> <value>' instead.\n"
                b"Input responses are handled directly by sessions.",
                0,
            )

        if sub in ("list", "respond"):
            pass
        elif sub == "ingest":
            return await self._handle_input_ingest(ctx)
        elif args.get("text") or args.get("file") or args.get("clipboard"):
            return await self._handle_input_ingest(ctx)

        return f"Unknown input subcommand: {sub}".encode(), 1

    async def _handle_input_ingest(self, ctx: PipelineContext) -> tuple[bytes, int]:
        """Handle L0 Ingest — create ChaityaStream from various sources."""
        args = self._get_ctx_args(ctx)
        text = self._strip_quotes(args.get("text", ""))
        clipboard = args.get("clipboard")
        declared_type = self._strip_quotes(args.get("type", ""))
        merge = self._strip_quotes(args.get("merge", "concat"))

        files = self._extract_multiple_flags(args, "file")
        clipboard_flag = args.get("clipboard")

        content = b""
        mime_type = declared_type or "text/plain"
        sources: list[str] = []

        if text:
            content = text.encode("utf-8")
            mime_type = "text/plain"
            sources.append("<text>")

        if clipboard:
            content = await self._read_clipboard()
            mime_type = "text/plain"
            sources.append("<clipboard>")

        if files:
            merged = b""
            for file_path in files:
                try:
                    path = Path(file_path)
                    if not path.exists():
                        return f"[error] file not found: {file_path}\n".encode(), 1
                    file_content = path.read_bytes()
                    merged += file_content
                    sources.append(str(path))
                except PermissionError:
                    return f"[error] permission denied: {file_path}\n".encode(), 1
                except Exception as exc:
                    return f"[error] cannot read {file_path}: {exc}\n".encode(), 1

            if merge == "concat":
                content = merged
            elif merge == "lines":
                content = b"\n".join(merged.splitlines(keepends=True))
            elif merge == "json":
                import json

                content = json.dumps(
                    {"parts": [str(len(files)), merged.decode("utf-8", errors="replace")]}
                ).encode()
            else:
                content = merged

            if not declared_type:
                mime_type = self._detect_mime_type(files[0])

        if not content:
            return b"[error] no input source specified (--text, --file, or --clipboard)\n", 1

        ingest_stream = ChaityaStream(
            content=content,
            declared_type=mime_type,
            size_bytes=len(content),
            source=", ".join(sources) if sources else "",
        )

        ctx.input_stream = ingest_stream
        return content, 0

    def _strip_quotes(self, value: str) -> str:
        """Strip leading/trailing quotes from a string value."""
        if not value:
            return value
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            return value[1:-1]
        return value

    def _extract_multiple_flags(self, env: dict[str, Any], flag: str) -> list[str]:
        """Extract multiple values for a flag from raw_args (handles duplicates)."""
        raw_args = env.get("__raw_args__", env.get("__args__", []))
        values = []
        i = 0
        while i < len(raw_args):
            arg = raw_args[i]
            if arg == f"--{flag}":
                if i + 1 < len(raw_args):
                    next_val = raw_args[i + 1]
                    if not next_val.startswith("--"):
                        values.append(self._strip_quotes(next_val))
                        i += 2
                        continue
            elif arg.startswith(f"--{flag}="):
                values.append(self._strip_quotes(arg[len(flag) + 3 :]))
            i += 1
        return values

    def _detect_mime_type(self, file_path: str) -> str:
        """Detect MIME type from file extension."""
        mime, _ = mimetypes.guess_type(file_path)
        return mime or "application/octet-stream"

    async def _read_clipboard(self) -> bytes:
        """Read content from system clipboard."""
        system = os.name

        if system == "posix":
            try:
                result = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
                if result.returncode == 0:
                    return result.stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            for cmd in [
                "xclip",
                "-selection",
                "clipboard",
                "-o",
                "xsel",
                "--clipboard",
                "--output",
            ]:
                try:
                    result = subprocess.run(cmd.split(), capture_output=True, timeout=5)
                    if result.returncode == 0:
                        return result.stdout
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue

        elif system == "nt":
            try:
                import platform

                ps_version = platform.version()
                if int(platform.version().split(".")[0]) >= 10:
                    result = subprocess.run(
                        ["powershell", "-Command", "Get-Clipboard"], capture_output=True, timeout=5
                    )
                    if result.returncode == 0:
                        return result.stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

            try:
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Content", "clipboard:"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return result.stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return b"[error] clipboard not available on this platform\n"

    async def _handle_output(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``output <options>`` — configure output formatting.

        PRD §5:
            output --filter <pattern>    Filter output lines matching pattern
            output --format json|text     Set output format (json or text)

        The output command is a pipeline modifier. When used standalone,
        it shows the current output configuration.
        """
        args = self._get_ctx_args(ctx)
        output_format = str(args.get("format", "text")).lower()
        filter_pattern = args.get("filter")
        if not output_format and not filter_pattern:
            return (
                b"output configuration:\n"
                b"  --format json|text    Set output format\n"
                b"  --filter <pattern>    Filter output lines\n"
                b"Current format: text (JSON-lines also supported)\n"
                b"Use as pipeline modifier: pipe to filter output",
                0,
            )

        if filter_pattern:
            lines = (input_stream.content or b"").decode("utf-8", errors="replace").splitlines()
            import re

            try:
                pattern = re.compile(str(filter_pattern))
                filtered = [l for l in lines if pattern.search(l)]
                result = "\n".join(filtered)
            except re.error:
                result = f"Invalid regex pattern: {filter_pattern}"
            return result.encode("utf-8"), 0

        if output_format == "json":
            import json

            return json.dumps(
                {
                    "content": (input_stream.content or b"").decode("utf-8", errors="replace"),
                    "type": input_stream.declared_type,
                    "size_bytes": input_stream.size_bytes,
                },
                separators=(",", ":"),
            ).encode("utf-8"), 0

        return input_stream.content or b"", 0

    async def _handle_watch(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int] | AsyncIterator[bytes]:
        """Handle ``watch <options>`` — query or stream events.

        PRD §5:
            watch --session <name> [--on <event-type>] [--exit-after N] [--limit N]
            watch --all [--on <event-type>] [--exit-after N] [--limit N]
            watch --search <query> [--since <duration>] [--limit N]
            watch --live --session <name> [--on <event-type>] [--exit-after N] [--timeout N]

        Default mode (no --live): query history, return JSON lines. Pipeable.
        --live mode: streams events as JSON lines (AsyncIterator[bytes]).
        Chunks are yielded in real-time and flow through the pipeline for piping.
        """
        import json
        from datetime import datetime, timedelta

        args = self._get_ctx_args(ctx)
        is_live = bool(args.get("live"))
        search_query = args.get("search")
        session_id = args.get("session")
        exit_after = int(str(args.get("exit_after", args.get("exit-after", "0"))))
        limit = int(str(args.get("limit", "100")))
        since_str = args.get("since")
        timeout_str = args.get("timeout", "0")
        event_types: list[str] | None = None
        if args.get("on"):
            event_types = [str(args["on"])]

        if is_live:
            return self._watch_live(
                session_id=str(session_id) if session_id else None,
                event_types=event_types,
                exit_after=exit_after,
                timeout_seconds=int(timeout_str) if timeout_str else 0,
            )

        if search_query:
            since_dt = None
            if since_str:
                try:
                    secs = int(since_str.rstrip("s"))
                    since_dt = datetime.now(UTC) - timedelta(seconds=secs)
                except ValueError:
                    pass
            ef = EventFilter(since=since_dt, limit=limit)
            events = await self._store.search_events(str(search_query), ef)
            if event_types:
                events = [e for e in events if e.type in event_types]
        else:
            since_dt = None
            if since_str:
                try:
                    secs = int(since_str.rstrip("s"))
                    since_dt = datetime.now(UTC) - timedelta(seconds=secs)
                except ValueError:
                    pass
            events = await self._event_bus.history(
                EventFilter(
                    event_types=event_types,
                    session_id=str(session_id) if session_id else None,
                    since=since_dt,
                    limit=limit,
                )
            )

        if exit_after > 0:
            events = events[:exit_after]

        if not events:
            return b"No events match the query.", 0

        lines: list[str] = []
        for ev in events:
            event_dict = {
                "event_id": ev.event_id,
                "type": ev.type,
                "source_adapter": ev.source_adapter,
                "timestamp": ev.timestamp,
                "session_id": ev.session_id,
                "exit_code": ev.exit_code,
                "duration_ms": ev.duration_ms,
                "payload": ev.payload,
                "parent_event_id": ev.parent_event_id,
                "request_id": ev.request_id,
            }
            lines.append(json.dumps(event_dict, separators=(",", ":")))
        return "\n".join(lines).encode("utf-8"), 0

    async def _watch_live(
        self,
        session_id: str | None,
        event_types: list[str] | None,
        exit_after: int,
        timeout_seconds: int,
    ) -> AsyncIterator[bytes]:
        """Stream live events as JSON line chunks.

        Async generator. Each yield is one event as a JSON line.
        When piped through the pipeline, chunks arrive in real-time.
        The pipeline's _handle_result accumulates all chunks for the final
        L2 output while yielding them for piping.

        Blocks until exit_after events are received, timeout expires,
        or the kernel is shutting down.
        """
        import json

        q: asyncio.Queue[Event | None] = asyncio.Queue()
        count = 0

        def _make_handler() -> Any:
            async def _on_event(event: Event) -> None:
                await q.put(event)

            return _on_event

        subscription = await self._event_bus.subscribe(
            EventFilter(event_types=event_types, session_id=session_id),
            _make_handler(),
        )

        async def _cleanup() -> None:
            await self._event_bus.unsubscribe(subscription)

        try:
            while True:
                try:
                    if timeout_seconds > 0:
                        ev = await asyncio.wait_for(q.get(), timeout=timeout_seconds)
                    else:
                        ev = await q.get()
                except TimeoutError:
                    break

                if ev is None:
                    break

                event_dict = {
                    "event_id": ev.event_id,
                    "type": ev.type,
                    "source_adapter": ev.source_adapter,
                    "timestamp": ev.timestamp,
                    "session_id": ev.session_id,
                    "exit_code": ev.exit_code,
                    "duration_ms": ev.duration_ms,
                    "payload": ev.payload,
                    "parent_event_id": ev.parent_event_id,
                    "request_id": ev.request_id,
                }
                line = json.dumps(event_dict, separators=((",", ":")))
                yield (line + "\n").encode("utf-8")

                count += 1
                if exit_after > 0 and count >= exit_after:
                    break

                if self._shutting_down:
                    break
        finally:
            await _cleanup()

    async def _handle_registry(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``registry list``, ``registry info``, ``registry validate``."""
        args = self._get_ctx_args(ctx)
        sub = args.get("subcommand", "list")
        positional = args.get("__raw_args__", [])

        if sub == "disable":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: registry disable <adapter-name>\n", 1
            self._registry.disable(name)
            return (
                f"Adapter '{name}' disabled.\n".encode(),
                0,
            )

        if sub == "enable":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: registry enable <adapter-name>\n", 1
            self._registry.enable(name)
            return f"Adapter '{name}' added to enabled list.\n".encode(), 0

        if sub == "list":
            adapters = self._registry.loaded_adapters
            disabled = self._registry.disabled_names
            lines = [f"{'NAME':20s} {'STATUS':12s} DESCRIPTION"]
            lines.append("-" * 70)
            for name, pkg in sorted(adapters.items()):
                status = "disabled" if name in disabled else pkg.status.value
                desc = pkg.contract.description[:30] if pkg.contract else ""
                lines.append(f"{name:20s} {status:12s} {desc}")
            if disabled:
                lines.append("")
                lines.append("Disabled adapters:")
                for name in sorted(disabled):
                    if name not in adapters:
                        lines.append(f"  {name:18s} (not loaded)")
            enabled = self._registry.enabled_names
            if enabled:
                lines.append("")
                lines.append("Enabled adapters (whitelist):")
                for name in sorted(enabled):
                    status = "loaded" if name in adapters else "(none)"
                    lines.append(f"  {name:18s} {status}")
            lines.append(f"\n{len(adapters)} adapter(s) loaded.")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "info":
            name = args.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: registry info <adapter-name>", 1
            pkg = self._registry.get_adapter(name)
            if pkg is None:
                return f"Adapter '{name}' not found.".encode(), 1
            if pkg.contract:
                c = pkg.contract
                is_disabled = self._registry.is_disabled(name)
                is_enabled = name in self._registry.enabled_names
                if is_disabled:
                    status = "disabled"
                elif is_enabled:
                    status = "loaded (enabled)"
                else:
                    status = pkg.status.value
                lines = [
                    f"Adapter: {c.name}",
                    f"Description: {c.description}",
                    f"Contract version: {c.contract_version}",
                    f"Status: {status}",
                    f"Entry point: {pkg.entry_point}",
                ]
                if c.commands:
                    lines.append("Commands:")
                    for cmd in c.commands:
                        lines.append(f"  {cmd.name:16s} {cmd.description}")
                if c.depends_on:
                    lines.append(f"Dependencies: {', '.join(c.depends_on)}")
                if pkg.error:
                    lines.append(f"Load error: {pkg.error}")
                return "\n".join(lines).encode("utf-8"), 0
            return f"Adapter '{name}' has no contract.".encode(), 1

        if sub == "validate":
            name = args.get("name") or (positional[0] if positional else "")
            pkg = self._registry.get_adapter(name)
            if pkg is None:
                return f"Adapter '{name}' not found.".encode(), 1
            result = self._registry.validate(pkg)
            if result.valid:
                return f"Adapter '{name}': VALID".encode(), 0
            lines = [f"Adapter '{name}': INVALID"]
            for e in result.errors:
                lines.append(f"  ERROR: {e}")
            for w in result.warnings:
                lines.append(f"  WARNING: {w}")
            return "\n".join(lines).encode("utf-8"), 1

        if sub == "reload":
            loaded = await self._registry.reload()
            count = len(loaded)
            return f"Reloaded {count} adapter(s).\n".encode(), 0

        return (
            f"Unknown registry subcommand: {sub}\n"
            f"Usage: registry <list|info|disable|enable|validate|reload>".encode(),
            1,
        )
