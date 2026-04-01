"""Tests for chaitya.core.config — load, merge, env overrides."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from chaitya.core.config import (
    CoreConfig,
    EventBusConfig,
    KernelConfig,
    SessionConfig,
    StoreConfig,
    _apply_env_overrides,
    _deep_merge,
    _resolve_defaults,
    load_config,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_defaults_have_all_sections(self):
        d = _resolve_defaults()
        assert "kernel" in d
        assert "store" in d
        assert "event_bus" in d
        assert "session" in d
        assert "templates_dir" in d
        assert "adapters_config_dir" in d

    def test_default_cli_name(self, monkeypatch):
        monkeypatch.delenv("CHAITYA_CLI_NAME", raising=False)
        d = _resolve_defaults()
        assert d["kernel"]["cli_name"] == "chaitya"

    def test_custom_cli_name(self, monkeypatch):
        monkeypatch.setenv("CHAITYA_CLI_NAME", "mybot")
        d = _resolve_defaults()
        assert d["kernel"]["cli_name"] == "mybot"

    def test_default_store_limits(self):
        d = _resolve_defaults()
        assert d["store"]["max_events_per_second"] == 1000
        assert d["store"]["max_log_size_bytes"] == 1_073_741_824


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_override_replaces_non_dict(self):
        base = {"x": "old"}
        override = {"x": {"new": True}}
        result = _deep_merge(base, override)
        assert result == {"x": {"new": True}}

    def test_base_unchanged(self):
        base = {"a": 1}
        override = {"a": 2}
        _deep_merge(base, override)
        assert base == {"a": 1}


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    def test_cli_name_override(self, monkeypatch):
        monkeypatch.setenv("CHAITYA_CLI_NAME", "testbot")
        config = {"kernel": {"cli_name": "chaitya"}}
        result = _apply_env_overrides(config)
        assert result["kernel"]["cli_name"] == "testbot"

    def test_int_coercion(self, monkeypatch):
        monkeypatch.setenv("CHAITYA_MAX_EVENTS_PER_SECOND", "500")
        config = {"store": {"max_events_per_second": 1000}}
        result = _apply_env_overrides(config)
        assert result["store"]["max_events_per_second"] == 500

    def test_invalid_int_ignored(self, monkeypatch):
        monkeypatch.setenv("CHAITYA_MAX_EVENTS_PER_SECOND", "not-a-number")
        config = {"store": {"max_events_per_second": 1000}}
        result = _apply_env_overrides(config)
        assert result["store"]["max_events_per_second"] == 1000

    def test_top_level_override(self, monkeypatch):
        monkeypatch.setenv("CHAITYA_TEMPLATES_DIR", "/custom/templates")
        config = {"templates_dir": "original"}
        result = _apply_env_overrides(config)
        assert result["templates_dir"] == "/custom/templates"

    def test_missing_env_no_change(self, monkeypatch):
        monkeypatch.delenv("CHAITYA_CLI_NAME", raising=False)
        config = {"kernel": {"cli_name": "original"}}
        result = _apply_env_overrides(config)
        assert result["kernel"]["cli_name"] == "original"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_defaults_only(self, monkeypatch, tmp_path):
        # Point to non-existent yaml so only defaults apply
        monkeypatch.delenv("CHAITYA_CLI_NAME", raising=False)
        cfg = load_config(config_path=tmp_path / "nonexistent.yaml")
        assert isinstance(cfg, CoreConfig)
        assert isinstance(cfg.kernel, KernelConfig)
        assert isinstance(cfg.store, StoreConfig)
        assert isinstance(cfg.event_bus, EventBusConfig)
        assert isinstance(cfg.session, SessionConfig)
        assert cfg.kernel.cli_name == "chaitya"
        assert cfg.store.backend == "sqlite"
        assert cfg.session.stuck_threshold_seconds == 60

    def test_env_override_in_load(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAITYA_LOG_LEVEL", "debug")
        cfg = load_config(config_path=tmp_path / "nonexistent.yaml")
        assert cfg.kernel.log_level == "debug"

    def test_yaml_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CHAITYA_CLI_NAME", raising=False)
        monkeypatch.delenv("CHAITYA_LOG_LEVEL", raising=False)
        yaml_file = tmp_path / "core.yaml"
        yaml_file.write_text(textwrap.dedent("""\
            kernel:
              log_level: debug
              cli_name: yamlbot
            store:
              max_events_per_second: 42
        """), encoding="utf-8")
        cfg = load_config(config_path=yaml_file)
        assert cfg.kernel.log_level == "debug"
        assert cfg.kernel.cli_name == "yamlbot"
        assert cfg.store.max_events_per_second == 42

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "core.yaml"
        yaml_file.write_text("kernel:\n  cli_name: yamlbot\n", encoding="utf-8")
        monkeypatch.setenv("CHAITYA_CLI_NAME", "envbot")
        cfg = load_config(config_path=yaml_file)
        assert cfg.kernel.cli_name == "envbot"

    def test_frozen_config(self, tmp_path):
        cfg = load_config(config_path=tmp_path / "nonexistent.yaml")
        with pytest.raises(AttributeError):
            cfg.kernel.cli_name = "mutated"  # type: ignore[misc]

    def test_unknown_yaml_keys_ignored(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CHAITYA_CLI_NAME", raising=False)
        yaml_file = tmp_path / "core.yaml"
        yaml_file.write_text("kernel:\n  unknown_key: value\n", encoding="utf-8")
        cfg = load_config(config_path=yaml_file)
        assert cfg.kernel.cli_name == "chaitya"  # defaults still work

