"""Runtime context and event bus proxy for adapters.

At kernel boot time, the kernel injects a real event bus implementation
into this module via ``set_event_bus()``.  Adapters call ``event_bus.emit()``
and ``event_bus.subscribe()`` — they never know what backend is behind it.

This module also provides permission-checked wrappers.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

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
                self._adapter_name, "emit events",
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
            handler, event_types=event_types, session_id=session_id,
        )
        # Return the subscription_id string
        return sub.subscription_id if hasattr(sub, "subscription_id") else str(sub)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription."""
        bus = self._require_bus()
        await bus.unsubscribe(subscription_id)


# Module-level singleton — adapters import this directly.
event_bus = EventBusProxy()

