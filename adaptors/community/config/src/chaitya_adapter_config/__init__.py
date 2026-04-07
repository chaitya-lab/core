"""Chaitya Core Config Adapter.

Provides commands to read and write configuration for Core and adapters.

Config structure (per PRD §12):
    ~/.chaitya/core.yaml                    - Core kernel settings
    ~/.chaitya/adapters/<name>.yaml        - Adapter-specific settings

Commands:
    get     — Get a configuration value
    set     — Set a configuration value (writes to file)
    list    — List configuration (core + adapters)
    paths   — Show config paths and loaded adapters

Usage:
    chaitya config get session.backend           # Core config
    chaitya config get llm.model                 # Adapter config
    chaitya config set llm.model gpt-4           # Set adapter config
    chaitya config list                          # List all
    chaitya config list llm                      # List adapter config
"""

import os
from pathlib import Path
from typing import Any

import yaml

from chaitya_sdk import ChaityaStream, SessionContext, adapter


# Default config directory
DEFAULT_CHAITYA_DIR = Path.home() / ".chaitya"


@adapter(
    name="config",
    description="Read and write Chaitya Core and adapter configuration",
    commands=[
        {
            "name": "get",
            "description": "Get a configuration value",
            "params": [
                {
                    "name": "key",
                    "required": True,
                    "description": "Config key (e.g., 'session.backend', 'llm.model')",
                    "example": "session.backend",
                },
            ],
            "examples": [
                "chaitya config get session.backend",
                "chaitya config get llm.model",
            ],
        },
        {
            "name": "set",
            "description": "Set a configuration value (persisted to file)",
            "params": [
                {
                    "name": "key",
                    "required": True,
                    "description": "Config key (e.g., 'llm.model')",
                },
                {
                    "name": "value",
                    "required": True,
                    "description": "Value to set",
                },
            ],
            "examples": [
                "chaitya config set llm.model gpt-4",
                "chaitya config set kernel.log_level debug",
            ],
        },
        {
            "name": "list",
            "description": "List configuration (all or for a specific adapter)",
            "params": [
                {
                    "name": "adapter",
                    "required": False,
                    "description": "Adapter name to list config for",
                },
            ],
            "examples": [
                "chaitya config list",
                "chaitya config list llm",
            ],
        },
        {
            "name": "paths",
            "description": "Show config paths and loaded adapters",
            "params": [],
            "examples": [
                "chaitya config paths",
            ],
        },
    ],
)
def config_handler(stream: ChaityaStream, ctx: SessionContext) -> tuple[bytes, int]:
    subcommand = ctx.args.get("subcommand", "")
    raw_args = list(ctx.args.get("__raw_args__", []))
    
    # If no subcommand provided, check if first arg is an adapter name
    if not subcommand and raw_args and raw_args[0] not in ["get", "set", "list", "paths"]:
        # First arg might be an adapter name, treat as "list <adapter>"
        subcommand = "list"
    elif not subcommand:
        subcommand = "list"

    if subcommand == "get":
        return _handle_get(ctx, raw_args)
    elif subcommand == "set":
        return _handle_set(ctx, raw_args)
    elif subcommand == "list":
        return _handle_list(ctx, raw_args)
    elif subcommand == "paths":
        return _handle_paths(ctx)
    else:
        return f"Unknown subcommand: {subcommand}\n".encode(), 1


def _get_kernel(ctx: SessionContext):
    return getattr(ctx, "_kernel", None)


def _get_config_dir(ctx: SessionContext) -> Path:
    """Get the Chaitya config directory."""
    kernel = _get_kernel(ctx)
    if kernel:
        config = getattr(kernel, "_config", None)
        if config and hasattr(config, "adapters_config_dir"):
            path = config.adapters_config_dir
            if path:
                return Path(path).expanduser()
    return DEFAULT_CHAITYA_DIR


def _get_core_config(ctx: SessionContext) -> dict[str, Any]:
    """Get core kernel config."""
    kernel = _get_kernel(ctx)
    if kernel:
        config = getattr(kernel, "_config", None)
        if config:
            return _dataclass_to_dict(config)
    return {}


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Convert dataclass to dict recursively."""
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    elif hasattr(obj, "__dataclass_fields__"):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in obj.__dataclass_fields__.values()}
    elif hasattr(obj, "__dict__"):
        return {k: _dataclass_to_dict(v) for k, v in vars(obj).items()}
    elif isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(item) for item in obj]
    else:
        return obj


def _get_adapter_config(adapter_name: str, config_dir: Path) -> dict[str, Any] | None:
    """Load adapter config from file."""
    adapters_dir = config_dir / "adapters"
    config_file = adapters_dir / f"{adapter_name}.yaml"
    if config_file.exists():
        try:
            return yaml.safe_load(config_file.read_text()) or {}
        except Exception:
            return None
    return None


def _save_adapter_config(adapter_name: str, config_dir: Path, data: dict[str, Any]) -> bool:
    """Save adapter config to file."""
    adapters_dir = config_dir / "adapters"
    config_file = adapters_dir / f"{adapter_name}.yaml"
    try:
        adapters_dir.mkdir(parents=True, exist_ok=True)
        config_file.write_text(yaml.dump(data, default_flow_style=False))
        return True
    except Exception:
        return False


def _handle_get(ctx: SessionContext, raw_args: list) -> tuple[bytes, int]:
    key = raw_args[0] if raw_args else None
    if not key:
        return b"Usage: config get <key>\nExample: config get session.backend\n", 1

    config_dir = _get_config_dir(ctx)
    keys = str(key).split(".")
    
    # Try core config first (no adapter prefix)
    core_config = _get_core_config(ctx)
    value = core_config
    found_in_core = True
    
    for k in keys:
        if isinstance(value, dict):
            next_val = value.get(k)
        elif hasattr(value, k):
            next_val = getattr(value, k)
        else:
            found_in_core = False
            break
        value = next_val
    
    if found_in_core and value is not None:
        return f"{key} = {value}\n".encode(), 0
    
    # Try adapter config (first key is adapter name, must have a config file)
    if len(keys) > 1:
        potential_adapter = keys[0]
        adapter_config = _get_adapter_config(potential_adapter, config_dir)
        
        if adapter_config is not None:
            value = adapter_config
            for k in keys[1:]:
                if isinstance(value, dict):
                    value = value.get(k)
                else:
                    return f"Key not found: {key}\n".encode(), 1
            if value is not None:
                return f"{key} = {value}\n".encode(), 0

    return f"Key not found: {key}\n".encode(), 1


def _handle_set(ctx: SessionContext, raw_args: list) -> tuple[bytes, int]:
    if len(raw_args) < 2:
        return b"Usage: config set <key> <value>\nExample: config set llm.model gpt-4\n", 1

    key = raw_args[0]
    value_str = raw_args[1]
    config_dir = _get_config_dir(ctx)
    keys = str(key).split(".")
    
    if len(keys) < 2:
        return f"Error: Key must be in format '<adapter>.<setting>'\nExample: config set llm.model gpt-4\n".encode(), 1

    # Parse value
    value = _parse_value(value_str)

    adapter_name = keys[0]
    remaining_keys = keys[1:]

    # Load existing adapter config or start fresh
    adapter_config = _get_adapter_config(adapter_name, config_dir) or {}

    # Navigate to the right nested level
    target = adapter_config
    for k in remaining_keys[:-1]:
        if k not in target:
            target[k] = {}
        target = target[k]

    # Set the value
    target[remaining_keys[-1]] = value

    if _save_adapter_config(adapter_name, config_dir, adapter_config):
        return f"Set {key} = {value}\n".encode(), 0
    else:
        return f"Error: Could not save config to {config_dir}/adapters/{adapter_name}.yaml\n".encode(), 1


def _handle_list(ctx: SessionContext, raw_args: list) -> tuple[bytes, int]:
    adapter_name = raw_args[0] if raw_args else None
    config_dir = _get_config_dir(ctx)
    lines = []

    if adapter_name:
        # List specific adapter config
        adapter_config = _get_adapter_config(adapter_name, config_dir)
        if adapter_config:
            lines.append(f"=== {adapter_name} config ===")
            lines.append(_format_dict(adapter_config))
        else:
            lines.append(f"No config file found for adapter '{adapter_name}'")
            lines.append(f"Expected: {config_dir}/adapters/{adapter_name}.yaml")
    else:
        # List all configs
        lines.append("=== Core Config ===")
        core_config = _get_core_config(ctx)
        lines.append(_format_dict(core_config))

        # List all adapter configs
        adapters_dir = config_dir / "adapters"
        if adapters_dir.exists():
            adapter_files = sorted(adapters_dir.glob("*.yaml"))
            if adapter_files:
                lines.append("")
                lines.append("=== Adapter Configs ===")
                for config_file in adapter_files:
                    name = config_file.stem
                    adapter_config = _get_adapter_config(name, config_dir)
                    if adapter_config:
                        lines.append(f"--- {name} ---")
                        lines.append(_format_dict(adapter_config))
                        lines.append("")

    return "\n".join(lines).encode() + b"\n", 0


def _handle_paths(ctx: SessionContext) -> tuple[bytes, int]:
    kernel = _get_kernel(ctx)
    config_dir = _get_config_dir(ctx)
    lines = []

    lines.append("=== Config Directories ===")
    lines.append(f"  Chaitya dir: {DEFAULT_CHAITYA_DIR}")
    lines.append(f"  Config dir:  {config_dir}")
    lines.append(f"  Adapters:    {config_dir}/adapters/")

    lines.append("")
    lines.append("=== Loaded Adapters ===")
    if kernel:
        registry = getattr(kernel, "_registry", None)
        if registry:
            adapters = registry.loaded_adapters
            if adapters:
                for name in sorted(adapters.keys()):
                    lines.append(f"  - {name}")
            else:
                lines.append("  (none)")

    lines.append("")
    lines.append("=== Config Files ===")
    adapters_dir = config_dir / "adapters"
    if adapters_dir.exists():
        config_files = sorted(adapters_dir.glob("*.yaml"))
        for config_file in config_files:
            lines.append(f"  - {config_file.name}")

    return "\n".join(lines).encode() + b"\n", 0


def _parse_value(value_str: str) -> str | int | float | bool:
    """Parse string value to appropriate type."""
    if value_str.lower() == "true":
        return True
    if value_str.lower() == "false":
        return False
    if value_str.isdigit():
        return int(value_str)
    try:
        return float(value_str)
    except ValueError:
        return value_str


def _format_dict(d: dict, prefix: str = "") -> str:
    if not isinstance(d, dict):
        return str(d)
    lines = []
    for key in sorted(d.keys()):
        value = d[key]
        full_key = f"{prefix}{key}" if prefix else key
        if isinstance(value, dict):
            lines.append(f"{full_key}:")
            lines.append(_indent(_format_dict(value, prefix + "  ")))
        else:
            lines.append(f"{full_key} = {value}")
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.split("\n"))
