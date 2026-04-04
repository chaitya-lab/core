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
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chaitya_sdk.context import event_bus as sdk_event_bus
from chaitya_sdk.context import registry_proxy
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
from chaitya.core.backends.local import LocalProcessBackend
from chaitya.core.backends.psmux import PsmuxBackend
from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.config import CoreConfig
from chaitya.core.event_bus import SqliteEventBus
from chaitya.core.pipeline import AdapterHandler, PipelineOrchestrator
from chaitya.core.registry import AdapterRegistry, BootstrapLoader, REGISTRY_ADAPTER_NAME
from chaitya.core.session_manager import SessionManager
from chaitya.core.store import SqliteStore
from chaitya.core.types import (
    ADAPTER_LOADED,
    ADAPTER_REJECTED,
    INPUT_REQUESTED,
    INPUT_RESPONSE,
    KERNEL_SHUTTING_DOWN,
    KERNEL_STARTED,
    SESSION_RESUMED,
    AdapterLoadError,
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


@dataclass
class _PendingInputRequest:
    request_id: str
    adapter_name: str
    handler: Any
    contract: Any
    permissions: AdapterPermissions
    input_stream: ChaityaStream
    ctx: PipelineContext
    spec: SdkInputSpec
    args: dict[str, Any]
    created_at: str = ""


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
        adapter_search_paths: list[str] | None = None,
        system_adapters: frozenset[str] | None = None,
        overflow_dir: str | None = None,
        stuck_threshold_seconds: int = 60,
        max_events_per_second: int = 1000,
        max_log_size_bytes: int = 1_073_741_824,
        templates_dir: str | None = None,
        registry_adapter_pkg: Any = None,
        input_timeout_seconds: int = 300,
    ) -> None:
        self._config = config
        self.cli_name = cli_name
        self._system_adapters = system_adapters or DEFAULT_SYSTEM_ADAPTERS
        self._booted = False
        self._shutting_down = False
        self._boot_time: float | None = None
        self._pending_inputs: dict[str, _PendingInputRequest] = {}
        self._input_timeout_seconds: int = input_timeout_seconds
        self._input_timeout_task: asyncio.Task[None] | None = None
        self._bootstrap_loader = BootstrapLoader()
        self._registry_adapter_pkg: Any = registry_adapter_pkg

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
            templates_dir=templates_dir,
        )
        self._pipeline = PipelineOrchestrator(overflow_dir=overflow_dir)
        self._registry = AdapterRegistry(search_paths=adapter_search_paths or [])

    @classmethod
    def from_config(cls, config: CoreConfig) -> Kernel:
        """Create a Kernel from a CoreConfig object."""
        return cls(
            config=config,
            db_path=config.store.path or ":memory:",
            cli_name=config.kernel.cli_name,
            session_backend=config.session.backend,
            adapter_search_paths=[
                config.adapters_config_dir,
                *config.adapter_search_paths,
            ],
            system_adapters=frozenset(config.system_adapters),
            overflow_dir=config.kernel.tmp_dir or None,
            stuck_threshold_seconds=config.session.stuck_threshold_seconds,
            max_events_per_second=config.store.max_events_per_second,
            max_log_size_bytes=config.store.max_log_size_bytes,
            templates_dir=config.templates_dir or None,
        )

    @staticmethod
    def _build_session_backend(session_backend: str) -> Any:
        backend = session_backend.strip().lower()
        if backend == "local":
            return LocalProcessBackend()
        if backend == "tmux":
            return TmuxSessionBackend()
        if backend == "psmux":
            return PsmuxBackend()
        raise ValueError(
            f"Unsupported session backend {session_backend!r}. Supported backends: local, tmux, psmux."
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

            # Step 4: Bootstrap loader finds and loads registry adapter (PRD §3.6)
            # If registry adapter is missing: kernel halts with installation instructions
            if self._registry_adapter_pkg is None:
                try:
                    self._registry_adapter_pkg = self._bootstrap_loader.load_registry_adapter()
                except AdapterLoadError as exc:
                    raise KernelBootError(
                        f"Registry adapter not found. Install with:\n"
                        f"  pip install chaitya-adapter-registry\n"
                        f"Details: {exc}"
                    ) from exc
            logger.info("Step 4/10: Registry adapter loaded")

            # Step 5: Registry: discover all installed adapters
            packages = await self._registry.discover()
            logger.info("Step 5/10: Discovered %d adapter package(s)", len(packages))

            # Step 6: Dependency graph: topological sort
            graph = self._registry.build_dependency_graph(packages)
            logger.info(
                "Step 6/10: Dependency graph built (%d in order, %d rejected)",
                len(graph.load_order),
                len(graph.rejected),
            )

            # Step 7: Load system adapters in order
            # Step 8: Load user adapters in order
            loaded, failed = await self._registry.load_all(
                packages, system_adapters=self._system_adapters
            )
            for name in loaded:
                await self._event_bus.emit(Event(type=ADAPTER_LOADED, payload={"adapter": name}))
            for name in failed:
                await self._event_bus.emit(Event(type=ADAPTER_REJECTED, payload={"adapter": name}))
            logger.info(
                "Steps 7-8/10: Loaded %d adapter(s), %d failed",
                len(loaded),
                len(failed),
            )

            # Step 9: Restore session state from store
            await self._session_mgr.start()
            logger.info("Step 9/10: Session state restored")

            # Step 10: Register kernel command handlers in pipeline
            self._register_kernel_handlers()
            self._register_loaded_adapter_handlers()
            await self._restore_pending_inputs()
            logger.info("Step 10/10: Kernel command handlers registered")

            # Step 11: Emit kernel_started, accept commands
            registry_proxy.set_registry(self._registry)
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
            logger.info("Step 11/11: Kernel started — accepting commands")

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

            # Cancel input timeout monitor
            if self._input_timeout_task and not self._input_timeout_task.done():
                self._input_timeout_task.cancel()
                try:
                    await self._input_timeout_task
                except asyncio.CancelledError:
                    pass
                self._input_timeout_task = None

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

    def _build_sdk_context(
        self,
        ctx: PipelineContext,
        args: dict[str, Any] | None = None,
        *,
        session_state: SdkSessionState = SdkSessionState.IDLE,
    ) -> SdkSessionContext:
        return SdkSessionContext(
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
            request_id = str(uuid.uuid4())
            created_at = datetime.now(UTC).isoformat()
            self._pending_inputs[request_id] = _PendingInputRequest(
                request_id=request_id,
                adapter_name=name,
                handler=handler,
                contract=contract,
                permissions=permissions,
                input_stream=input_stream,
                ctx=ctx,
                spec=suspension.spec,
                args=dict(sdk_args),
                created_at=created_at,
            )
            await self._store.save_pending_input(
                request_id=request_id,
                adapter_name=name,
                session_id=ctx.session_id,
                spec={
                    "name": suspension.spec.name,
                    "prompt": suspension.spec.prompt,
                    "input_type": suspension.spec.input_type.value,
                    "multiline": suspension.spec.multiline,
                    "options": suspension.spec.options,
                    "min_value": suspension.spec.min_value,
                    "max_value": suspension.spec.max_value,
                    "default": suspension.spec.default,
                },
                args=dict(sdk_args),
                ctx_env=dict(ctx.env),
                input_stream={
                    "content": input_stream.content.decode("utf-8", errors="surrogateescape"),
                    "declared_type": input_stream.declared_type,
                    "detected_type": input_stream.detected_type,
                    "source": input_stream.source,
                    "size_bytes": input_stream.size_bytes,
                    "encoding": input_stream.encoding,
                },
                created_at=created_at,
            )
            await self._store.save_pending_input(
                request_id=request_id,
                adapter_name=name,
                session_id=ctx.session_id,
                spec={
                    "name": suspension.spec.name,
                    "prompt": suspension.spec.prompt,
                    "input_type": suspension.spec.input_type.value,
                    "multiline": suspension.spec.multiline,
                    "options": suspension.spec.options,
                    "min_value": suspension.spec.min_value,
                    "max_value": suspension.spec.max_value,
                    "default": suspension.spec.default,
                },
                args=dict(sdk_args),
                ctx_env=dict(ctx.env),
                input_stream={
                    "content": input_stream.content.decode("utf-8", errors="surrogateescape"),
                    "declared_type": input_stream.declared_type,
                    "detected_type": input_stream.detected_type,
                    "source": input_stream.source,
                    "size_bytes": input_stream.size_bytes,
                    "encoding": input_stream.encoding,
                },
                created_at=datetime.now(UTC).isoformat(),
            )
            if ctx.session_id:
                try:
                    await self._session_mgr.update_state(ctx.session_id, SessionState.WAITING)
                except Exception:
                    pass
            await self._event_bus.emit(
                Event(
                    type=INPUT_REQUESTED,
                    source_adapter=name,
                    session_id=ctx.session_id,
                    request_id=request_id,
                    payload={
                        "name": suspension.spec.name,
                        "prompt": suspension.spec.prompt,
                        "input_type": suspension.spec.input_type.value,
                        "multiline": suspension.spec.multiline,
                        "options": suspension.spec.options,
                        "default": suspension.spec.default,
                    },
                )
            )
            prompt = suspension.spec.prompt or f"Input required: {suspension.spec.name}"
            return (
                f"[waiting:{request_id}] {prompt}\nRespond with: {self.cli_name} input respond {request_id} <value>".encode(),
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

    async def _resume_pending_input(self, request_id: str, value: str) -> tuple[bytes, int]:
        pending = self._pending_inputs.pop(request_id, None)
        if pending is None:
            return f"Unknown input request: {request_id}".encode(), 1
        await self._store.delete_pending_input(request_id)

        resumed_args = dict(pending.args)
        resumed_args[pending.spec.name] = value
        await self._event_bus.emit(
            Event(
                type=INPUT_RESPONSE,
                source_adapter="kernel",
                session_id=pending.ctx.session_id,
                request_id=request_id,
                payload={"name": pending.spec.name, "value": value},
            )
        )
        if pending.ctx.session_id:
            try:
                await self._session_mgr.update_state(pending.ctx.session_id, SessionState.IDLE)
                await self._event_bus.emit(
                    Event(
                        type=SESSION_RESUMED,
                        source_adapter="kernel",
                        session_id=pending.ctx.session_id,
                        request_id=request_id,
                        payload={"name": pending.spec.name},
                    )
                )
            except Exception:
                pass

        return await self._invoke_adapter(
            pending.adapter_name,
            pending.handler,
            pending.permissions,
            pending.contract,
            pending.input_stream,
            pending.ctx,
            args=resumed_args,
        )

    async def _restore_pending_inputs(self) -> None:
        for item in await self._store.list_pending_inputs():
            package = self._registry.get_adapter(item["adapter_name"])
            if package is None or package.handler is None:
                logger.warning(
                    "Pending input request %s skipped because adaptor %s is unavailable",
                    item["request_id"],
                    item["adapter_name"],
                )
                continue

            spec_raw = item["spec"]
            input_stream_raw = item["input_stream"]
            self._pending_inputs[item["request_id"]] = _PendingInputRequest(
                request_id=item["request_id"],
                adapter_name=item["adapter_name"],
                handler=package.handler,
                contract=package.contract,
                permissions=package.contract.permissions,
                input_stream=ChaityaStream(
                    content=input_stream_raw["content"].encode("utf-8", errors="surrogateescape"),
                    declared_type=input_stream_raw["declared_type"],
                    detected_type=input_stream_raw.get("detected_type"),
                    source=input_stream_raw.get("source", ""),
                    size_bytes=input_stream_raw.get("size_bytes", 0),
                    encoding=input_stream_raw.get("encoding", "utf-8"),
                ),
                ctx=PipelineContext(
                    session_id=item["session_id"],
                    env=item["ctx_env"],
                ),
                spec=SdkInputSpec(
                    name=spec_raw["name"],
                    prompt=spec_raw.get("prompt", ""),
                    input_type=SdkInputType[spec_raw.get("input_type", "text").upper()],
                    multiline=spec_raw.get("multiline", False),
                    options=spec_raw.get("options", []),
                    min_value=spec_raw.get("min_value"),
                    max_value=spec_raw.get("max_value"),
                    default=spec_raw.get("default"),
                ),
                args=item["args"],
                created_at=item.get("created_at", ""),
            )

    async def _input_timeout_loop(self) -> None:
        """Background monitor: expire pending input requests after timeout.

        If a suspension's request is not responded to within
        ``self._input_timeout_seconds``, the pending request is cancelled and
        the ``input_timeout`` event is emitted.  This prevents adapters from
        hanging indefinitely when a daemon crashes or the user never responds.
        """
        while True:
            await asyncio.sleep(10)
            if self._shutting_down:
                return
            now = datetime.now(UTC)
            expired: list[str] = []
            for request_id, pending in list(self._pending_inputs.items()):
                created_str = pending.created_at
                if not created_str:
                    continue
                try:
                    created = datetime.fromisoformat(created_str)
                except (ValueError, TypeError):
                    continue
                age_seconds = (now - created).total_seconds()
                if age_seconds >= self._input_timeout_seconds:
                    expired.append(request_id)
                    logger.warning(
                        "Input request %s expired after %ds (adapter=%s, field=%s)",
                        request_id,
                        int(age_seconds),
                        pending.adapter_name,
                        pending.spec.name,
                    )
                    await self._store.delete_pending_input(request_id)
                    await self._event_bus.emit(
                        Event(
                            type="input_timeout",
                            source_adapter="kernel",
                            session_id=pending.ctx.session_id,
                            request_id=request_id,
                            payload={
                                "adapter": pending.adapter_name,
                                "field": pending.spec.name,
                                "age_seconds": int(age_seconds),
                            },
                        )
                    )

            for request_id in expired:
                self._pending_inputs.pop(request_id, None)

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

        if adapter_name == "--kernel" or args.get("--kernel"):
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

        # Default: comprehensive overview
        sessions = await self._store.list_sessions()
        active = [s for s in sessions if s.state.value not in ("dead",)]
        lines: list[str] = []

        lines.append(f"Chaitya Core v{__version__}  (uptime: {round(self.uptime_seconds, 1)}s)")
        lines.append("=" * 60)
        lines.append("")

        lines.append("KERNEL COMMANDS")
        lines.append("-" * 40)
        lines.append(f"  {self.cli_name}                          Show this help")
        lines.append(f"  {self.cli_name} info [adapter]          System or adapter details")
        lines.append(f"  {self.cli_name} info --kernel           Kernel status")
        lines.append("")
        lines.append("  session list                           List all sessions")
        lines.append("  session create <name> [--template]    Create a new session")
        lines.append("  session status <name>                  Show session status")
        lines.append("  session attach <name>                  Attach to a session (tmux)")
        lines.append("  session detach <name>                  Detach from a session")
        lines.append("  session output <name>                  Read session output")
        lines.append("  session send-input <name> <text>       Send input to session")
        lines.append("  session send-input <name> --newline    Send newline only")
        lines.append("  session send-input <name> --key <key> Send key (enter/tab/space)")
        lines.append("  session set-env <name> KEY=VALUE      Set environment variable")
        lines.append("  session unset-env <name> KEY           Unset environment variable")
        lines.append("  session signal <name> <signal>         Send signal (SIGTERM/SIGINT)")
        lines.append("  session kill <name>                   Kill a session")
        lines.append("")
        lines.append("  watch --all                            Stream all events")
        lines.append("  watch --session <name> [--on <type>]   Stream session events")
        lines.append("  watch --search <query>                 Search event log")
        lines.append("")
        lines.append("  input list                            List pending input requests")
        lines.append("  input respond <id> <value>            Respond to a suspended command")
        lines.append("")
        lines.append("  output --filter <pattern>             Filter output")
        lines.append("  output --format json|text             Set output format")
        lines.append("")
        lines.append("  registry list                         List loaded adapters")
        lines.append("  registry validate <name>             Validate an adapter contract")
        lines.append("")
        lines.append("  <adapter> <subcommand> [args]        Run adapter command")
        lines.append("  <adapter>                             Show adapter info")
        lines.append("")

        lines.append("ACTIVE SESSIONS")
        lines.append("-" * 40)
        if active:
            lines.append(f"  {'NAME':20s} {'STATE':10s} {'TEMPLATE'}")
            for s in active:
                tmpl = s.template or "-"
                lines.append(f"  {s.name:20s} {s.state.value:10s} {tmpl}")
        else:
            lines.append("  No active sessions.")
        lines.append("")
        lines.append("  Use 'session create <name>' to create a new session.")
        lines.append("  Use 'session attach <name>' to attach to a session (tmux).")
        lines.append("  Use 'session list' to see all sessions.")

        adapters = self._registry.loaded_adapters
        lines.append("")
        lines.append("INSTALLED ADAPTERS")
        lines.append("-" * 40)
        if adapters:
            lines.append("  NAME                 DESCRIPTION                COMMANDS")
            for name, pkg in sorted(adapters.items()):
                desc = (
                    (pkg.contract.description[:24] + "...")
                    if pkg.contract and len(pkg.contract.description) > 24
                    else (pkg.contract.description if pkg.contract else "")
                )
                cmds = (
                    ", ".join(cmd.name for cmd in (pkg.contract.commands if pkg.contract else []))
                    or "-"
                )
                lines.append(f"  {name:20s} {desc:25s} {cmds}")
            lines.append("")
            lines.append(f"  Run '{self.cli_name} info <adapter>' for adapter details.")
            lines.append(f"  Run '{self.cli_name} <adapter>' without subcommand to see its info.")
        else:
            lines.append("  No adapters installed.")
            lines.append("  Install adapters with: pip install chaitya-adapter-<name>")

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
                return b"No sessions. Use 'session create <name>' to create one.", 0
            lines = ["NAME                 STATE      TEMPLATE             LAST ACTIVITY"]
            lines.append("-" * 80)
            for r in records:
                tmpl = r.template or "-"
                lines.append(f"{r.name:20s} {r.state.value:10s} {tmpl:20s} {r.last_activity}")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "status":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session status <name>", 1
            rec = await self._store.get_session(name)
            if rec is None:
                return f"Session '{name}' not found.".encode(), 1
            lines = [
                f"Name: {rec.name}",
                f"State: {rec.state.value}",
                f"Created: {rec.created_at}",
            ]
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "create":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session create <name> [--template <template-name>]", 1
            template = ctx.env.get("template")
            try:
                handle = await self._session_mgr.create(name=name, template=template)
                msg = f"Session '{name}' created"
                if template:
                    msg += f" (template: {template})"
                msg += "."
                return msg.encode("utf-8"), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "send-input":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session send-input <name> <text>|--newline|--key <key>", 1
            raw_text = positional[1] if len(positional) > 1 else ""
            newline = bool(ctx.env.get("newline"))
            key = str(ctx.env.get("key", "")).lower()
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
                data = raw_text.encode("utf-8")
                if newline:
                    data += b"\n"
            try:
                await self._session_mgr.send_input(name, data)
                return f"Input sent to session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "output":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session output <name> [--idle-timeout 0.2]", 1
            idle_timeout = float(ctx.env.get("idle-timeout", "0.2"))
            try:
                output = await self._session_mgr.read_output(
                    name,
                    idle_timeout_seconds=idle_timeout,
                )
                return output, 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "attach":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session attach <name>", 1
            try:
                await self._session_mgr.attach(name)
                return f"Attached to session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "detach":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            if not name:
                return b"Usage: session detach <name>", 1
            try:
                await self._session_mgr.detach(name)
                return f"Detached from session '{name}'.".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "signal":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            sig = (
                ctx.env.get("sig")
                or ctx.env.get("signal")
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
            name = ctx.env.get("name") or (positional[0] if positional else "")
            pair = positional[1] if len(positional) > 1 else ""
            if not name or "=" not in pair:
                return b"Usage: session set-env <name> KEY=VALUE", 1
            key, value = pair.split("=", 1)
            try:
                await self._session_mgr.set_env(name, key, value)
                return f"Environment set for session '{name}': {key}".encode(), 0
            except Exception as exc:
                return str(exc).encode("utf-8"), 1

        if sub == "unset-env":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            key = positional[1] if len(positional) > 1 else ""
            if not name or not key:
                return b"Usage: session unset-env <name> KEY", 1
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
        """Handle ``input <options>`` — provide input to a suspended command."""
        sub = ctx.env.get("__subcommand__", "list")
        positional = ctx.env.get("__args__", [])

        if sub == "list":
            if not self._pending_inputs:
                return b"No pending input requests.", 0
            lines = [f"{'REQUEST_ID':36s} {'ADAPTER':12s} {'FIELD':16s} PROMPT"]
            for request_id, pending in sorted(self._pending_inputs.items()):
                lines.append(
                    f"{request_id:36s} {pending.adapter_name:12s} {pending.spec.name:16s} {pending.spec.prompt}"
                )
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "respond":
            request_id = positional[0] if positional else ""
            value = positional[1] if len(positional) > 1 else str(ctx.env.get("value", ""))
            if not request_id:
                return b"Usage: input respond <request_id> <value>", 1
            return await self._resume_pending_input(request_id, value)

        return f"Unknown input subcommand: {sub}".encode(), 1

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
        output_format = str(ctx.env.get("format", "text")).lower()
        filter_pattern = ctx.env.get("filter")
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
    ) -> tuple[bytes, int]:
        """Handle ``watch <options>`` — subscribe to events.

        PRD §5:
            watch --session <name> [--on <event-type>] [--exit-after N]
            watch --all [--on <event-type>]
            watch --search <query> [--since <duration>]

        Streams JSON-lines on stdout. Pipeable to any adapter.
        """
        import json
        from datetime import datetime, timedelta

        search_query = ctx.env.get("search")
        session_id = ctx.env.get("session")
        exit_after = int(str(ctx.env.get("exit-after", "0")))
        limit = int(str(ctx.env.get("limit", "100")))
        since_str = ctx.env.get("since")
        event_types: list[str] | None = None
        if ctx.env.get("on"):
            event_types = [str(ctx.env["on"])]

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
                lines.append(f"{name:20s} {pkg.status.value:12s}")
            return "\n".join(lines).encode("utf-8"), 0

        if sub == "validate":
            name = ctx.env.get("name") or (positional[0] if positional else "")
            pkg = self._registry.get_adapter(name)
            if pkg is None:
                return f"Adapter '{name}' not found.".encode(), 1
            result = self._registry.validate(pkg)
            lines = [f"Valid: {result.valid}"]
            for e in result.errors:
                lines.append(f"  ERROR: {e}")
            for w in result.warnings:
                lines.append(f"  WARNING: {w}")
            return "\n".join(lines).encode("utf-8"), 0

        return f"Unknown registry subcommand: {sub}".encode(), 1
