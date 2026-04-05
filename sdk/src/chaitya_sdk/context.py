"""Runtime context and event bus proxy for adapters.

At kernel boot time, the kernel injects a real event bus implementation
into this module via ``set_event_bus()``.  Adapters call ``event_bus.emit()``
and ``event_bus.subscribe()`` — they never know what backend is behind it.

This module also provides permission-checked wrappers.
"""

from __future__ import annotations

import asyncio
import logging
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
            await asyncio.wait_for(done.wait(), timeout=timeout)
            if result[0] is None:
                raise TimeoutError(f"Timed out waiting for response to {request_id}")
            return result[0]
        except TimeoutError as exc:
            raise TimeoutError(f"Timed out waiting for response to {request_id}") from exc
        finally:
            await bus.unsubscribe(sub)


# Module-level singletons — adapters import these directly.
event_bus = EventBusProxy()
_registry_proxy: RegistryProxy | None = None


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


registry_proxy = RegistryProxy()
