"""The ``@adapter`` decorator — command registration for Chaitya adapters.

Usage::

    from chaitya_sdk import adapter, ChaityaStream, SessionContext

    @adapter(
        name="greet",
        description="Say hello",
        commands=[{"name": "hello", "description": "Say hello"}],
    )
    def greet_hello(stream: ChaityaStream, ctx: SessionContext) -> bytes:
        name = ctx.args.get("name", "world")
        return f"Hello, {name}!".encode()

The decorator attaches metadata to the function.  The kernel's adapter
loader reads this metadata when discovering entry points.
"""

from __future__ import annotations

import functools
from dataclasses import asdict
from typing import Any, Callable

from chaitya_sdk.types import (
    AdapterContract,
    AdapterPermissions,
    BusyInputPolicy,
    CommandParam,
    CommandSpec,
    OutputRoutingRule,
    ResourceLimits,
    ShutdownPolicy,
)

# Module-level registry of all decorated handlers in this package.
# The kernel's adapter loader collects these at load time.
_ADAPTER_REGISTRY: dict[str, dict[str, Any]] = {}


def _build_command_specs(raw: list[dict[str, Any]]) -> list[CommandSpec]:
    """Convert raw dicts from decorator kwargs into CommandSpec objects."""
    specs = []
    for cmd in raw:
        params = [
            CommandParam(**p) if isinstance(p, dict) else p
            for p in cmd.get("params", [])
        ]
        specs.append(CommandSpec(
            name=cmd["name"],
            description=cmd.get("description", ""),
            params=params,
            examples=cmd.get("examples", []),
            output_type=cmd.get("output_type", "text/plain"),
            supports_dry_run=cmd.get("supports_dry_run", False),
        ))
    return specs


def _build_permissions(raw: dict[str, Any] | None) -> AdapterPermissions:
    """Convert raw dict into AdapterPermissions."""
    if raw is None:
        return AdapterPermissions()
    return AdapterPermissions(**{
        k: v for k, v in raw.items()
        if k in AdapterPermissions.__dataclass_fields__
    })


def _build_resource_limits(raw: dict[str, Any] | None) -> ResourceLimits:
    if raw is None:
        return ResourceLimits()
    return ResourceLimits(**{
        k: v for k, v in raw.items()
        if k in ResourceLimits.__dataclass_fields__
    })


def adapter(
    *,
    name: str,
    description: str = "",
    depends_on: list[str] | None = None,
    commands: list[dict[str, Any]] | None = None,
    default_session: str = "default",
    default_input_type: str = "text/plain",
    default_output_type: str = "text/plain",
    supports_dry_run: bool = False,
    on_busy_input: str = "queue",
    on_error: str = "stderr_attach",
    on_timeout: str = "emit_stuck_event",
    on_shutdown: str = "detach",
    permissions: dict[str, Any] | None = None,
    output_routing: list[dict[str, Any]] | None = None,
    events_emitted: list[str] | None = None,
    events_consumed: list[str] | None = None,
    resource_limits: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that registers a function as a Chaitya adapter handler.

    The decorated function's signature must be::

        def handler(stream: ChaityaStream, ctx: SessionContext) -> bytes:
            ...

    Or an async variant::

        async def handler(stream: ChaityaStream, ctx: SessionContext) -> bytes:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        contract = AdapterContract(
            name=name,
            description=description,
            depends_on=depends_on or [],
            default_session=default_session,
            default_input_type=default_input_type,
            default_output_type=default_output_type,
            supports_dry_run=supports_dry_run,
            on_busy_input=BusyInputPolicy(on_busy_input),
            on_error=on_error,
            on_timeout=on_timeout,
            on_shutdown=ShutdownPolicy(on_shutdown),
            permissions=_build_permissions(permissions),
            commands=_build_command_specs(commands or []),
            output_routing=[
                OutputRoutingRule(**r) for r in (output_routing or [])
            ],
            events_emitted=events_emitted or [],
            events_consumed=events_consumed or [],
            resource_limits=_build_resource_limits(resource_limits),
        )

        # Attach metadata to the function
        fn.__chaitya_contract__ = contract  # type: ignore[attr-defined]
        fn.__chaitya_adapter_name__ = name  # type: ignore[attr-defined]

        # Register globally
        _ADAPTER_REGISTRY[name] = {
            "handler": fn,
            "contract": contract,
        }

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        # Copy metadata to wrapper
        wrapper.__chaitya_contract__ = contract  # type: ignore[attr-defined]
        wrapper.__chaitya_adapter_name__ = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


def get_registered_adapters() -> dict[str, dict[str, Any]]:
    """Return all adapters registered via ``@adapter`` in this process."""
    return dict(_ADAPTER_REGISTRY)

