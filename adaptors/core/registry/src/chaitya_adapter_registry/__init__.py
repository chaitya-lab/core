"""Registry adapter for Chaitya Core.

Implements ``registry list``, ``registry info``, and ``registry validate`` commands.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter
from chaitya_sdk.context import event_bus, registry_proxy


@adapter(
    name="registry",
    description="Discover, list, and validate installed adapters.",
    commands=[
        {
            "name": "list",
            "description": "List all discovered adapter packages.",
            "params": [],
            "examples": ["chaitya registry list"],
        },
        {
            "name": "info",
            "description": "Show detailed contract for a named adapter.",
            "params": [
                {
                    "name": "name",
                    "required": True,
                    "description": "Adapter name",
                }
            ],
            "examples": ["chaitya registry info --name shell"],
        },
        {
            "name": "validate",
            "description": "Validate an adapter's contract.",
            "params": [
                {
                    "name": "name",
                    "required": True,
                    "description": "Adapter name",
                }
            ],
            "examples": ["chaitya registry validate --name file"],
        },
    ],
    permissions={"fs_read": [], "fs_write": [], "can_emit_events": True},
)
async def registry_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    subcommand = str(ctx.args.get("subcommand", ""))

    reg = registry_proxy.get_registry()
    if reg is None:
        return b"Registry not available (kernel may not be booted).", 1

    if subcommand == "list":
        loaded = reg.loaded_adapters
        if not loaded:
            return b"No adapters loaded.", 0
        lines = ["ADAPTER                STATUS      DESCRIPTION"]
        lines.append("-" * 72)
        for name, pkg in sorted(loaded.items()):
            desc = pkg.contract.description[:30] if pkg.contract else ""
            status = pkg.status.value
            lines.append(f"{name:22s} {status:12s} {desc}")
        lines.append("")
        lines.append(f"Total: {len(loaded)} adapter(s).")
        return "\n".join(lines).encode("utf-8"), 0

    if subcommand == "info":
        name = str(ctx.args.get("name", ""))
        if not name:
            return b"Usage: registry info --name <adapter>", 1
        pkg = reg.get_adapter(name)
        if pkg is None:
            return f"Adapter '{name}' not found.".encode(), 1
        if pkg.contract:
            c = pkg.contract
            lines = [
                f"Adapter: {c.name}",
                f"Description: {c.description}",
                f"Contract version: {c.contract_version}",
                f"Status: {pkg.status.value}",
                f"Entry point: {pkg.entry_point}",
            ]
            if c.commands:
                lines.append("Commands:")
                for cmd in c.commands:
                    lines.append(f"  {cmd.name:16s} {cmd.description}")
            if c.depends_on:
                lines.append(f"Dependencies: {', '.join(c.depends_on)}")
            perm = c.permissions
            lines.append("Permissions:")
            lines.append(
                f"  fs_read: {perm.fs_read or '(none)'}",
            )
            lines.append(
                f"  fs_write: {perm.fs_write or '(none)'}",
            )
            lines.append(f"  network: {perm.network}")
            lines.append(f"  can_emit_events: {perm.can_emit_events}")
            if pkg.error:
                lines.append(f"Load error: {pkg.error}")
            return "\n".join(lines).encode("utf-8"), 0
        return f"Adapter '{name}' has no contract.".encode(), 1

    if subcommand == "validate":
        name = str(ctx.args.get("name", ""))
        if not name:
            return b"Usage: registry validate --name <adapter>", 1
        pkg = reg.get_adapter(name)
        if pkg is None:
            return f"Adapter '{name}' not found.".encode(), 1
        result = reg.validate(pkg)
        if result.valid:
            return (
                f"Adapter '{name}': VALID".encode(),
                0,
            )
        lines = [f"Adapter '{name}': INVALID"]
        for err in result.errors:
            lines.append(f"  ERROR: {err}")
        for warn in result.warnings:
            lines.append(f"  WARNING: {warn}")
        return "\n".join(lines).encode("utf-8"), 1

    return f"Unknown registry subcommand: {subcommand}".encode(), 1


__chaitya_handler__ = registry_handler
__adapter_contract__ = asdict(registry_handler.__chaitya_contract__)
