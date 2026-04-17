"""Runtime context and event bus proxy for adapters.

At kernel boot time, the kernel injects a real event bus implementation
into this module via ``set_event_bus()``.  Adapters call ``event_bus.emit()``
and ``event_bus.subscribe()`` — they never know what backend is behind it.

This module also provides permission-checked wrappers.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

from chaitya_sdk.types import (
    AdapterPermissions,
    Event,
    PermissionDenied,
)

logger = logging.getLogger("chaitya_sdk")


# ---------------------------------------------------------------------------
# Event bus protocol (what the kernel must satisfy)
# ---------------------------------------------------------------------------


class EventBusLike(Protocol):
    """Minimal event bus interface the kernel must provide."""

    async def emit(self, event: Any) -> None: ...
    async def subscribe(
        self,
        handler: Callable,
        event_types: list[str] | None = None,
        session_id: str | None = None,
    ) -> Any: ...
    async def unsubscribe(self, subscription_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Event bus proxy — permission-checked wrapper
# ---------------------------------------------------------------------------


class EventBusProxy:
    """Adapter-facing event bus with permission enforcement.

    The kernel injects the real bus at boot time.  Adapter calls go
    through this proxy, which checks the adapter's declared permissions
    before forwarding.
    """

    def __init__(self) -> None:
        self._bus: EventBusLike | None = None
        self._adapter_name: str = ""
        self._permissions: AdapterPermissions = AdapterPermissions()

    def _configure(
        self,
        bus: EventBusLike,
        adapter_name: str,
        permissions: AdapterPermissions,
    ) -> None:
        """Called by the kernel — not by adapters."""
        self._bus = bus
        self._adapter_name = adapter_name
        self._permissions = permissions

    def _require_bus(self) -> EventBusLike:
        if self._bus is None:
            raise RuntimeError(
                "Event bus not available — adapter may only use event_bus "
                "inside a handler call, after the kernel has booted."
            )
        return self._bus

    async def emit(self, event: Event) -> None:
        """Emit an event onto the bus.

        Requires ``can_emit_events`` permission.
        """
        if not self._permissions.can_emit_events:
            raise PermissionDenied(
                self._adapter_name,
                "emit events",
                "Adapter contract does not grant can_emit_events.",
            )
        bus = self._require_bus()
        await bus.emit(event)

    async def subscribe(
        self,
        handler: Callable,
        event_types: list[str] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Subscribe to events.  Returns a subscription ID."""
        bus = self._require_bus()
        sub = await bus.subscribe(
            handler,
            event_types=event_types,
            session_id=session_id,
        )
        # Return the subscription_id string
        return sub.subscription_id if hasattr(sub, "subscription_id") else str(sub)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription."""
        bus = self._require_bus()
        await bus.unsubscribe(subscription_id)

    async def wait_for_response(
        self,
        request_id: str,
        event_types: list[str],
        timeout: float = 30.0,
    ) -> Event:
        """Wait for a response event with a matching request_id.

        This is the key primitive for the daemon pattern (PRD §8).
        The CLI adapter emits a request event and waits for the daemon's
        response on the same event bus.

        Usage::

            request_id = str(uuid.uuid4())
            await event_bus.emit(Event(
                type="browser.screenshot_requested",
                request_id=request_id,
                payload={},
            ))
            response = await event_bus.wait_for_response(
                request_id,
                event_types=["browser.screenshot_response"],
                timeout=30.0,
            )
            screenshot_bytes = response.payload["screenshot"]

        Args:
            request_id: The request_id from the emitted request event.
            event_types: Event types to subscribe to (e.g. ["browser.screenshot_response"]).
            timeout: Maximum seconds to wait. Raises TimeoutError on expiry.

        Returns:
            The matching Event with the response payload.

        Raises:
            TimeoutError: If no matching response arrives within timeout.
            RuntimeError: If the event bus is not available.
        """
        bus = self._require_bus()
        result: list[Event | None] = [None]
        done = asyncio.Event()

        async def handler(event: Event) -> None:
            if result[0] is None and event.request_id == request_id:
                result[0] = event
                done.set()

        sub = await bus.subscribe(handler, event_types=event_types, session_id=None)
        try:
            import time
            from chaitya.core.types import Event as CoreEvent
            import json

            last_check = time.time()
            poll_interval = 0.1

            while time.time() - last_check < timeout:
                if result[0] is not None:
                    return result[0]

                await asyncio.sleep(poll_interval)

                sqlite_bus = getattr(bus, "_event_bus", None)
                if sqlite_bus is not None and hasattr(sqlite_bus, "_db"):
                    db = sqlite_bus._db
                    if db is not None:
                        event_types_str = ",".join(f"'{et}'" for et in event_types)
                        query = f"SELECT * FROM events WHERE type IN ({event_types_str}) AND request_id = ? ORDER BY timestamp ASC LIMIT 1"
                        try:
                            async with db.execute(query, (request_id,)) as cursor:
                                rows = await cursor.fetchall()
                            if rows:
                                row = rows[0]
                                (
                                    event_id,
                                    event_type,
                                    source_adapter,
                                    timestamp,
                                    session_id,
                                    exit_code,
                                    duration_ms,
                                    payload_json,
                                    parent_event_id,
                                    req_id,
                                ) = row
                                result[0] = CoreEvent(
                                    event_id=event_id,
                                    type=event_type,
                                    source_adapter=source_adapter,
                                    timestamp=timestamp,
                                    session_id=session_id,
                                    exit_code=exit_code,
                                    duration_ms=duration_ms,
                                    payload=json.loads(payload_json) if payload_json else {},
                                    parent_event_id=parent_event_id,
                                    request_id=req_id,
                                )
                                return result[0]
                        except Exception:
                            pass

                last_check = time.time()

            if result[0] is None:
                raise TimeoutError(f"Timed out waiting for response to {request_id}")
            return result[0]
        except TimeoutError as exc:
            raise TimeoutError(f"Timed out waiting for response to {request_id}") from exc
        finally:
            await bus.unsubscribe(sub)


# ---------------------------------------------------------------------------
# Session Manager proxy — permission-checked wrapper
# ---------------------------------------------------------------------------


class SessionManagerProxy:
    """Adapter-facing session manager proxy.

    The kernel injects the real SessionManager at boot time via ``set_session_manager()``.
    This allows the session adapter to manage sessions without importing
    ``chaitya.core.session_manager``.
    """

    def __init__(self) -> None:
        self._mgr: Any = None

    def set_session_manager(self, mgr: Any) -> None:
        self._mgr = mgr

    def get_session_manager(self) -> Any:
        if self._mgr is None:
            raise RuntimeError(
                "SessionManager not available — adapter may only use session_manager "
                "inside a handler call, after the kernel has booted."
            )
        return self._mgr


# ---------------------------------------------------------------------------
# Store proxy — permission-checked wrapper
# ---------------------------------------------------------------------------


class StoreProxy:
    """Adapter-facing store proxy.

    The kernel injects the real store at boot time via ``set_store()``.
    This allows adapters to access session records and event history.
    """

    def __init__(self) -> None:
        self._store: Any = None

    def set_store(self, store: Any) -> None:
        self._store = store

    def get_store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "Store not available — adapter may only use store "
                "inside a handler call, after the kernel has booted."
            )
        return self._store


# ---------------------------------------------------------------------------
# Registry proxy — permission-checked wrapper
# ---------------------------------------------------------------------------


class RegistryProxy:
    """Adapter-facing registry access proxy.

    The kernel injects the real registry at boot time via ``set_registry()``.
    This allows the registry adapter (and any other adapter that needs to
    query the registry) to do so without importing ``chaitya.core.registry``.
    """

    def __init__(self) -> None:
        self._registry: Any = None

    def set_registry(self, registry: Any) -> None:
        self._registry = registry

    def get_registry(self) -> Any:
        if self._registry is None:
            raise RuntimeError(
                "Registry not available — adapter may only use registry "
                "inside a handler call, after the kernel has booted."
            )
        return self._registry


# Module-level singletons — adapters import these directly.
event_bus = EventBusProxy()
session_manager = SessionManagerProxy()
store = StoreProxy()
registry_proxy = RegistryProxy()

# Current adapter permissions context — set by kernel before invoking adapter
_current_permissions: AdapterPermissions = AdapterPermissions()
_current_adapter_name: str = "unknown"


def _configure_permissions(adapter_name: str, permissions: AdapterPermissions) -> None:
    """Called by kernel — sets the current adapter's name and permissions context."""
    global _current_permissions, _current_adapter_name
    _current_permissions = permissions
    _current_adapter_name = adapter_name


def check_fs_read(path: str) -> None:
    """Check if the current adapter may read the given path.

    Args:
        path: The file path to check.

    Raises:
        PermissionDenied: If the adapter's fs_read permissions do not cover the path.

    Usage in adapters::

        from chaitya_sdk.context import check_fs_read

        check_fs_read("/tmp/notes.txt")
        with open("/tmp/notes.txt") as f:
            content = f.read()
    """
    abs_path = Path(path).expanduser().resolve()

    if not _current_permissions.fs_read:
        raise PermissionDenied(
            _current_adapter_name,
            f"read file: {path}",
            "Adapter has no fs_read permission declared.",
        )

    for allowed in _current_permissions.fs_read:
        allowed_path = Path(allowed).expanduser().resolve()
        try:
            abs_path.relative_to(allowed_path)
            return
        except ValueError:
            continue

    raise PermissionDenied(
        _current_adapter_name,
        f"read file: {path}",
        f"Not in allowed fs_read paths: {_current_permissions.fs_read}",
    )


def check_fs_write(path: str) -> None:
    """Check if the current adapter may write the given path.

    Args:
        path: The file path to check.

    Raises:
        PermissionDenied: If the adapter's fs_write permissions do not cover the path.

    Usage in adapters::

        from chaitya_sdk.context import check_fs_write

        check_fs_write("/tmp/output.txt")
        with open("/tmp/output.txt", "w") as f:
            f.write(data)
    """
    abs_path = Path(path).expanduser().resolve()

    if not _current_permissions.fs_write:
        raise PermissionDenied(
            _current_adapter_name,
            f"write file: {path}",
            "Adapter has no fs_write permission declared.",
        )

    for allowed in _current_permissions.fs_write:
        allowed_path = Path(allowed).expanduser().resolve()
        try:
            abs_path.relative_to(allowed_path)
            return
        except ValueError:
            continue

    raise PermissionDenied(
        _current_adapter_name,
        f"write file: {path}",
        f"Not in allowed fs_write paths: {_current_permissions.fs_write}",
    )


def check_network() -> None:
    """Check if the current adapter may make network requests.

    Raises:
        PermissionDenied: If the adapter does not have network=True.

    Usage in adapters::

        from chaitya_sdk.context import check_network

        check_network()
        response = requests.get("https://api.example.com/data")
    """
    if not _current_permissions.network:
        raise PermissionDenied(
            _current_adapter_name,
            "make network requests",
            "Adapter contract does not grant network permission.",
        )


# ---------------------------------------------------------------------------
# Kernel info proxy — provides kernel version, uptime, adapter list
# ---------------------------------------------------------------------------


class KernelInfoProxy:
    """Adapter-facing kernel info proxy.

    The kernel injects kernel info at boot time via ``set_kernel_info()``.
    This allows adapters like ``info`` and ``watch`` to access kernel metadata.
    """

    def __init__(self) -> None:
        self._info: dict[str, Any] = {}

    def set_kernel_info(self, info: dict[str, Any]) -> None:
        self._info = info

    def get(self, key: str, default: Any = None) -> Any:
        return self._info.get(key, default)

    @property
    def version(self) -> str:
        return self._info.get("version", "")

    @property
    def cli_name(self) -> str:
        return self._info.get("cli_name", "chaitya")

    @property
    def uptime_seconds(self) -> float:
        return self._info.get("uptime_seconds", 0.0)

    @property
    def adapters_loaded(self) -> list[str]:
        return self._info.get("adapters_loaded", [])


kernel_info = KernelInfoProxy()
