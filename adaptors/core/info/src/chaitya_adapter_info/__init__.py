"""Info adapter — show system or adapter information.

This is a core adapter that provides the ``info`` command.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from chaitya_sdk import ChaityaStream, SessionContext, adapter, kernel_info, registry_proxy


@adapter(
    name="info",
    description="Show system or adapter information.",
    commands=[
        {
            "name": "info",
            "description": "Show overview, adapter details, or kernel status.",
            "params": [
                {"name": "kernel", "required": False, "description": "Show kernel status."},
                {
                    "name": "adapter",
                    "required": False,
                    "description": "Show specific adapter details.",
                },
            ],
            "examples": ["chaitya info", "chaitya info --kernel", "chaitya info file"],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": [], "network": False, "can_emit_events": False},
)
async def info_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    kernel_flag = args.get("kernel", False)
    adapter_name = args.get("adapter", "")

    if kernel_flag:
        registry = registry_proxy.get_registry()
        sessions = await asyncio.get_event_loop().run_in_executor(None, registry._loaded.get, None)
        lines = [
            f"Kernel version: {kernel_info.version}",
            f"CLI name: {kernel_info.cli_name}",
            f"Uptime: {kernel_info.uptime_seconds:.1f}s",
            f"Loaded adapters: {len(kernel_info.adapters_loaded)}",
        ]
        return "\n".join(lines).encode("utf-8"), 0

    if adapter_name:
        registry = registry_proxy.get_registry()
        pkg = registry.get_adapter(adapter_name)
        if pkg is None:
            return f"Adapter '{adapter_name}' not found.".encode("utf-8"), 1
        if pkg.contract:
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
            return "\n".join(lines).encode("utf-8"), 0
        return f"Adapter '{adapter_name}' has no contract.".encode("utf-8"), 1

    lines = [
        f"Chaitya Core v{kernel_info.version}  (uptime: {kernel_info.uptime_seconds:.1f}s)",
        "",
    ]
    lines.append("info [name]    Show adapter or kernel details")
    lines.append(
        "session       list|create|status|attach|view|detach|output|send|set-env|signal|kill"
    )
    lines.append("input         --text|--file|--clipboard")
    lines.append("output        --format|--filter")
    lines.append("watch         --all|--session|--search|--live")
    lines.append("registry      list|info|enable|disable")
    lines.append("")
    registry = registry_proxy.get_registry()
    lines.append(f"{len(registry.loaded_adapters)} adapter(s) loaded:")
    for name in sorted(registry.loaded_adapters.keys()):
        lines.append(f"  {name}")
    return "\n".join(lines).encode("utf-8"), 0


__chaitya_handler__ = info_handler
__adapter_contract__ = asdict(info_handler.__chaitya_contract__)
