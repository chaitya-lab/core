"""Core Configuration — load, merge, validate.

Configuration hierarchy (highest priority first):
  1. Environment variables (``CHAITYA_*``)
  2. ``core.yaml`` file (``~/.chaitya/core.yaml`` by default)
  3. Built-in defaults

The kernel reads config exactly once at boot. Config is immutable after load.

Reference: PRD §12
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config data classes — frozen after creation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KernelConfig:
    """Kernel-level configuration."""

    debug_log: str = ""
    log_level: str = "info"
    tmp_dir: str = ""
    cli_name: str = "chaitya"


@dataclass(frozen=True)
class StoreConfig:
    """Persistent store configuration."""

    backend: str = "sqlite"
    path: str = ""
    max_events_per_second: int = 1000
    max_log_size_bytes: int = 1_073_741_824  # 1 GB


@dataclass(frozen=True)
class EventBusConfig:
    """Event bus configuration."""

    backend: str = "sqlite"
    url: str = ""


@dataclass(frozen=True)
class SessionConfig:
    """Session management configuration."""

    backend: str = "local"
    default_session_name: str = "default"
    stuck_threshold_seconds: int = 60


@dataclass(frozen=True)
class CoreConfig:
    """Top-level immutable configuration object.

    Constructed by ``load_config()`` — merges defaults, YAML, and env vars.
    """

    kernel: KernelConfig = field(default_factory=KernelConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    event_bus: EventBusConfig = field(default_factory=EventBusConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    templates_dir: str = ""
    adapters_config_dir: str = ""
    supported_contract_versions: list[str] = field(default_factory=lambda: ["1"])
    system_adapters: list[str] = field(
        default_factory=lambda: ["registry", "file", "shell", "route", "process"]
    )


# ---------------------------------------------------------------------------
# Defaults — resolved at runtime
# ---------------------------------------------------------------------------


def _default_chaitya_dir() -> Path:
    """``~/.chaitya`` — the canonical config/data directory."""
    return Path.home() / ".chaitya"


def _resolve_defaults() -> dict[str, Any]:
    """Build the complete defaults dict with platform-aware paths."""
    base = _default_chaitya_dir()
    cli = os.environ.get("CHAITYA_CLI_NAME", "chaitya")

    # tmp_dir: platform-aware
    if os.name == "nt":
        tmp_base = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")))
    else:
        tmp_base = Path("/tmp")
    tmp_dir = str(tmp_base / cli)

    return {
        "kernel": {
            "debug_log": str(base / "logs" / "kernel.log"),
            "log_level": "info",
            "tmp_dir": tmp_dir,
            "cli_name": cli,
        },
        "store": {
            "backend": "sqlite",
            "path": str(base / "chaitya.db"),
            "max_events_per_second": 1000,
            "max_log_size_bytes": 1_073_741_824,
        },
        "event_bus": {
            "backend": "sqlite",
            "url": "",
        },
        "session": {
            "backend": "local",
            "default_session_name": "default",
            "stuck_threshold_seconds": 60,
        },
        "templates_dir": str(base / "templates"),
        "adapters_config_dir": str(base / "adapters"),
        "supported_contract_versions": ["1"],
        "system_adapters": ["registry", "file", "shell", "route", "process"],
    }


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file if it exists. Returns empty dict on missing/error."""
    if not path.is_file():
        logger.debug("Config file not found: %s", path)
        return {}

    try:
        import yaml  # Optional dependency — graceful degradation
    except ImportError:
        logger.warning(
            "PyYAML not installed — skipping config file %s. "
            "Install with: pip install pyyaml",
            path,
        )
        return {}

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            logger.warning("Config file %s does not contain a mapping — ignoring", path)
            return {}
        logger.info("Loaded config from %s", path)
        return data
    except Exception as exc:
        logger.warning("Failed to parse config file %s: %s", path, exc)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*. Returns a new dict."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

# Mapping of env vars → config paths
_ENV_MAP: dict[str, tuple[str, ...]] = {
    "CHAITYA_CLI_NAME": ("kernel", "cli_name"),
    "CHAITYA_LOG_LEVEL": ("kernel", "log_level"),
    "CHAITYA_DEBUG_LOG": ("kernel", "debug_log"),
    "CHAITYA_TMP_DIR": ("kernel", "tmp_dir"),
    "CHAITYA_DB_PATH": ("store", "path"),
    "CHAITYA_DB_BACKEND": ("store", "backend"),
    "CHAITYA_MAX_EVENTS_PER_SECOND": ("store", "max_events_per_second"),
    "CHAITYA_MAX_LOG_SIZE_BYTES": ("store", "max_log_size_bytes"),
    "CHAITYA_EVENT_BUS_BACKEND": ("event_bus", "backend"),
    "CHAITYA_EVENT_BUS_URL": ("event_bus", "url"),
    "CHAITYA_SESSION_BACKEND": ("session", "backend"),
    "CHAITYA_STUCK_THRESHOLD": ("session", "stuck_threshold_seconds"),
    "CHAITYA_TEMPLATES_DIR": ("templates_dir",),
    "CHAITYA_ADAPTERS_CONFIG_DIR": ("adapters_config_dir",),
}

# Fields that should be coerced to int
_INT_FIELDS = {
    "max_events_per_second",
    "max_log_size_bytes",
    "stuck_threshold_seconds",
}


def _apply_env_overrides(config: dict) -> dict:
    """Apply CHAITYA_* environment variables as highest-priority overrides."""
    for env_var, path in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue

        # Navigate to the right place in config
        target = config
        for key in path[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]

        final_key = path[-1]

        # Type coercion for int fields
        if final_key in _INT_FIELDS:
            try:
                target[final_key] = int(value)
            except ValueError:
                logger.warning(
                    "Ignoring %s=%r — expected integer", env_var, value
                )
                continue
        else:
            target[final_key] = value

        logger.debug("Config override: %s=%s", env_var, value)

    return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(
    config_path: str | Path | None = None,
) -> CoreConfig:
    """Load, merge, and freeze configuration.

    Priority: env vars > YAML file > defaults.

    Parameters
    ----------
    config_path
        Path to ``core.yaml``.  Defaults to ``~/.chaitya/core.yaml``.
    """
    # 1. Defaults
    merged = _resolve_defaults()

    # 2. YAML overlay
    if config_path is None:
        config_path = _default_chaitya_dir() / "core.yaml"
    yaml_data = _load_yaml_file(Path(config_path))
    if yaml_data:
        merged = _deep_merge(merged, yaml_data)

    # 3. Env-var overlay (highest priority)
    merged = _apply_env_overrides(merged)

    # 4. Build frozen config objects
    return CoreConfig(
        kernel=KernelConfig(**{
            k: v for k, v in merged.get("kernel", {}).items()
            if k in KernelConfig.__dataclass_fields__
        }),
        store=StoreConfig(**{
            k: v for k, v in merged.get("store", {}).items()
            if k in StoreConfig.__dataclass_fields__
        }),
        event_bus=EventBusConfig(**{
            k: v for k, v in merged.get("event_bus", {}).items()
            if k in EventBusConfig.__dataclass_fields__
        }),
        session=SessionConfig(**{
            k: v for k, v in merged.get("session", {}).items()
            if k in SessionConfig.__dataclass_fields__
        }),
        templates_dir=merged.get("templates_dir", ""),
        adapters_config_dir=merged.get("adapters_config_dir", ""),
        supported_contract_versions=merged.get("supported_contract_versions", ["1"]),
        system_adapters=merged.get("system_adapters", []),
    )

