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
import signal
import time
from pathlib import Path
from typing import Any

from chaitya.core import __version__
from chaitya.core.backends.local import LocalProcessBackend
from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.config import CoreConfig, load_config
from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.pipeline import AdapterHandler, PipelineOrchestrator
from chaitya.core.registry import AdapterRegistry
from chaitya.core.session_manager import SessionManager
from chaitya.core.store import SqliteStore
from chaitya.core.types import (
    ADAPTER_LOADED,
    ADAPTER_REJECTED,
    KERNEL_STARTED,
    KERNEL_SHUTTING_DOWN,
    AdapterLoadError,
    AdapterPackage,
    AdapterStatus,
    ChaityaStream,
    CommandChain,
    CommandOutput,
    Event,
    EventFilter,
    KernelBootError,
    PipelineContext,
    SessionState,
    AdapterPermissions,
)
from chaitya_sdk.context import event_bus as sdk_event_bus
from chaitya_sdk.types import (
    AdapterPermissions as SdkAdapterPermissions,
    ChaityaStream as SdkChaityaStream,
    SessionContext as SdkSessionContext,
    SessionState as SdkSessionState,
)

logger = logging.getLogger(__name__)

# The six kernel-dispatched commands (PRD §5)
KERNEL_COMMANDS = frozenset({"info", "session", "input", "output", "watch", "registry"})

# System adapters whose load failure halts boot (PRD §3.6)
DEFAULT_SYSTEM_ADAPTERS = frozenset({"file", "shell"})


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
        session_backend: str = "local",
        system_adapters: frozenset[str] | None = None,
        overflow_dir: str | None = None,
        stuck_threshold_seconds: int = 60,
        max_events_per_second: int = 1000,
        max_log_size_bytes: int = 1_073_741_824,
    ) -> None:
        self._config = config
        self.cli_name = cli_name
        self._system_adapters = system_adapters or DEFAULT_SYSTEM_ADAPTERS
        self._booted = False
        self._shutting_down = False
        self._boot_time: float | None = None

        # --- Subsystem composition ---
        self._store = SqliteStore(
            db_path=db_path,
            max_events_per_second=max_events_per_second,
            max_log_size_bytes=max_log_size_bytes,
        )
        self._event_bus: SqliteEventBus = self._store.event_bus
        self._adapter_bus = _AdapterEventBusBridge(self._event_bus)
        self._backend = self._build_session_backend(session_backend)
        self._session_mgr = SessionManager(
            backend=self._backend,
            store=self._store,
            event_bus=self._event_bus,
            stuck_threshold_seconds=stuck_threshold_seconds,
        )
        self._pipeline = PipelineOrchestrator(overflow_dir=overflow_dir)
        self._registry = AdapterRegistry()

    @classmethod
    def from_config(cls, config: CoreConfig) -> "Kernel":
        """Create a Kernel from a CoreConfig object."""
        return cls(
            config=config,
            db_path=config.store.path or ":memory:",
            cli_name=config.kernel.cli_name,
            session_backend=config.session.backend,
            system_adapters=frozenset(config.system_adapters),
            overflow_dir=config.kernel.tmp_dir or None,
            stuck_threshold_seconds=config.session.stuck_threshold_seconds,
            max_events_per_second=config.store.max_events_per_second,
            max_log_size_bytes=config.store.max_log_size_bytes,
        )

    @staticmethod
    def _build_session_backend(session_backend: str) -> Any:
        backend = session_backend.strip().lower()
        if backend == "local":
            return LocalProcessBackend()
        if backend == "tmux":
            return TmuxSessionBackend()
        raise ValueError(
            f"Unsupported session backend {session_backend!r}. "
            "Supported backends: local, tmux."
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
        if self._booted:
            raise KernelBootError("Kernel is already booted")

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

            # Step 4: Bootstrap adapter registry (discover installed adapters)
            packages = await self._registry.discover()
            logger.info(
                "Step 4/10: Discovered %d adapter package(s)", len(packages)
            )

            # Step 5: Build dependency graph
            graph = self._registry.build_dependency_graph(packages)
            logger.info(
                "Step 5/10: Dependency graph built (%d in order, %d rejected)",
                len(graph.load_order),
                len(graph.rejected),
            )

            # Steps 6-7: Load system adapters then user adapters
            loaded, failed = await self._registry.load_all(
                packages, system_adapters=self._system_adapters
            )
            for name in loaded:
                await self._event_bus.emit(
                    Event(type=ADAPTER_LOADED, payload={"adapter": name})
                )
            for name in failed:
                await self._event_bus.emit(
                    Event(type=ADAPTER_REJECTED, payload={"adapter": name})
                )
            logger.info(
                "Steps 6-7/10: Loaded %d adapter(s), %d failed",
                len(loaded),
                len(failed),
            )

            # Step 8: Restore session state from store
            await self._session_mgr.start()
            logger.info("Step 8/10: Session state restored")

            # Step 9: Register kernel command handlers in pipeline
            self._register_kernel_handlers()
            self._register_loaded_adapter_handlers()
            logger.info("Step 9/10: Kernel command handlers registered")

            # Step 10: Emit kernel_started, accept commands
            self._boot_time = time.monotonic()
            self._booted = True
            await self._event_bus.emit(
                Event(
                    type=KERNEL_STARTED,
                    source_adapter="kernel",
                    payload={
                        "version": __version__,
                        "adapters_loaded": loaded,
                        "adapters_failed": failed,
                        "boot_ms": int(
                            (time.monotonic() - boot_start) * 1000
                        ),
                    },
                )
            )
            logger.info("Step 10/10: Kernel started — accepting commands")

        except AdapterLoadError as exc:
            raise KernelBootError(
                f"System adapter failed to load: {exc}"
            ) from exc
        except KernelBootError:
            raise
        except Exception as exc:
            raise KernelBootError(f"Boot failed: {exc}") from exc

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
        if self._shutting_down:
            return
        self._shutting_down = True
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

        Returns the final L2-processed CommandOutput.
        """
        if not self._booted:
            raise RuntimeError("Kernel not booted — call boot() first")
        if self._shutting_down:
            raise RuntimeError("Kernel is shutting down")

        chain = self._pipeline.parse_chain(expression)
        ctx = PipelineContext()
        return await self._pipeline.run(chain, ctx)

    # -- Kernel Command Handlers (PRD §5) --

    def _register_kernel_handlers(self) -> None:
        """Register the 6 kernel commands as pipeline handlers."""
        self._pipeline.register_handler("info", self._handle_info)
        self._pipeline.register_handler("session", self._handle_session)
        self._pipeline.register_handler("input", self._handle_input)
        self._pipeline.register_handler("output", self._handle_output)
        self._pipeline.register_handler("watch", self._handle_watch)
        self._pipeline.register_handler("registry", self._handle_registry)

    def _register_loaded_adapter_handlers(self) -> None:
        for name, package in self._registry.loaded_adapters.items():
            if package.handler is None:
                continue
            self._pipeline.register_handler(
                name,
                self._make_adapter_handler(name, package.handler, package.contract.permissions),
            )

    def _make_adapter_handler(
        self,
        name: str,
        handler: Any,
        permissions: AdapterPermissions,
    ) -> AdapterHandler:
        async def _wrapped(
            input_stream: ChaityaStream,
            ctx: PipelineContext,
        ) -> tuple[bytes, int]:
            sdk_event_bus._configure(
                self._adapter_bus,
                name,
                SdkAdapterPermissions(**permissions.__dict__),
            )
            sdk_stream = SdkChaityaStream(
                content=input_stream.content,
                declared_type=input_stream.declared_type,
                detected_type=input_stream.detected_type,
                source=input_stream.source,
                size_bytes=input_stream.size_bytes,
                encoding=input_stream.encoding,
            )
            sdk_ctx = SdkSessionContext(
                session_id=ctx.session_id,
                session_state=SdkSessionState.IDLE,
                args={
                    "subcommand": ctx.env.get("__subcommand__", ""),
                    "__raw_args__": ctx.env.get("__args__", []),
                    **{k: v for k, v in ctx.env.items() if not k.startswith("__")},
                },
                env={k: str(v) for k, v in ctx.env.items() if not k.startswith("__")},
                dry_run=ctx.dry_run,
            )
            result = handler(sdk_stream, sdk_ctx)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, tuple):
                output, exit_code = result
            else:
                output, exit_code = result, 0
            if isinstance(output, str):
                output = output.encode("utf-8")
            if not isinstance(output, bytes):
                output = str(output).encode("utf-8")
            return output, int(exit_code)

        return _wrapped


    async def _handle_info(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``info [adapter] [subcommand]``.

        - No args → kernel overview + adapter list
        - ``info <adapter>`` → adapter contract details
        - ``info --kernel`` → kernel status
        """
        args = ctx.env
        adapter_name = args.get("__subcommand__", "")

        if adapter_name == "--kernel" or args.get("--kernel"):
            info = {
                "kernel_version": __version__,
                "cli_name": self.cli_name,
                "uptime_seconds": round(self.uptime_seconds, 1),
                "adapters_loaded": len(self._registry.loaded_adapters),
                "active_sessions": await self._store.list_sessions(),
                "event_bus_backend": "sqlite",
                "store_backend": "sqlite",
            }
            lines = [f"{k}: {v}" for k, v in info.items()]
            return "\n".join(lines).encode("utf-8"), 0

        if adapter_name and adapter_name in self._registry.loaded_names:
            pkg = self._registry.get_adapter(adapter_name)
            if pkg and pkg.contract:
                c = pkg.contract
                lines = [
                    f"Adapter: {c.name}",
                    f"Description: {c.description}",
                    f"Version: {c.contract_version}",
                    f"Commands: {', '.join(cmd.name for cmd in c.commands)}",
                ]
                if c.depends_on:
                    lines.append(f"Dependencies: {', '.join(c.depends_on)}")
                return "\n".join(lines).encode("utf-8"), 0

        # Default: list all adapters
        adapters = self._registry.loaded_adapters
        if not adapters:
            msg = (
                f"No adapters installed.\n"
                f"Install adapters with: pip install chaitya-adapter-<name>\n"
                f"Then run: {self.cli_name} info"
            )
            return msg.encode("utf-8"), 0

        lines = [f"Chaitya Core v{__version__}", ""]
        lines.append("Installed adapters:")
        for name, pkg in sorted(adapters.items()):
            desc = pkg.contract.description if pkg.contract else ""
            lines.append(f"  {name:20s} {desc}")
        lines.append("")
        lines.append(f"Run '{self.cli_name} info <adapter>' for details.")
        return "\n".join(lines).encode("utf-8"), 0

    async def _handle_session(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``session <subcommand>``."""
        sub = ctx.env.get("__subcommand__", "list")
        positional = ctx.env.get("__args__", [])

        if sub == "list":
            records = await self._store.list_sessions()
            if not records:
                return b"No active sessions.", 0
            lines = [f"{'NAME':20s} {'STATE':10s}"]
            for r in records:
                lines.append(f"{r.name:20s} {r.state.value:10s}")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "status":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session status <name>", 1
            rec = await self._store.get_session(name)
            if rec is None:
                return f"Session '{name}' not found.".encode("utf-8"), 1
            lines = [
                f"Name: {rec.name}",
                f"State: {rec.state.value}",
                f"Created: {rec.created_at}",
            ]
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "create":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session create <name>", 1
            try:
                await self._session_mgr.create(name=name)
                return f"Session '{name}' created.".encode("utf-8"), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "kill":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session kill <name>", 1
            try:
                await self._session_mgr.kill(name)
                return f"Session '{name}' killed.".encode("utf-8"), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        return f"Unknown session subcommand: {sub}".encode("utf-8"), 1

    async def _handle_input(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``input <options>`` — provide input to a suspended command."""
        return b"input: not yet implemented", 0

    async def _handle_output(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``output <options>`` — configure output formatting."""
        return b"output: not yet implemented", 0

    async def _handle_watch(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``watch <options>`` — subscribe to events."""
        sub = ctx.env.get("__subcommand__", "")
        # Simple: return recent events
        events = await self._event_bus.history(EventFilter(limit=20))
        if not events:
            return b"No events recorded.", 0
        lines = []
        for ev in events:
            lines.append(f"[{ev.timestamp}] {ev.type}: {ev.payload}")
        return "\n".join(lines).encode("utf-8"), 0

    async def _handle_registry(
        self, input_stream: ChaityaStream, ctx: PipelineContext
    ) -> tuple[bytes, int]:
        """Handle ``registry <subcommand>``."""
        sub = ctx.env.get("__subcommand__", "list")
        positional = ctx.env.get("__args__", [])

        if sub == "list":
            adapters = self._registry.loaded_adapters
            if not adapters:
                return b"No adapters loaded.", 0
            lines = [f"{'NAME':20s} {'STATUS':12s}"]
            for name, pkg in sorted(adapters.items()):
                lines.append(
                    f"{name:20s} {pkg.status.value:12s}"
                )
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "validate":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            pkg = self._registry.get_adapter(name)
            if pkg is None:
                return f"Adapter '{name}' not found.".encode("utf-8"), 1
            result = self._registry.validate(pkg)
            lines = [f"Valid: {result.valid}"]
            for e in result.errors:
                lines.append(f"  ERROR: {e}")
            for w in result.warnings:
                lines.append(f"  WARNING: {w}")
            return "\n".join(lines).encode("utf-8"), 0

        return f"Unknown registry subcommand: {sub}".encode("utf-8"), 1
