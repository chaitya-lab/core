"""Tests for chaitya.core.registry — adapter discovery, validation, loading, dependency graph."""

from __future__ import annotations

import pytest

from chaitya.core.protocols import AdapterLoaderProtocol
from chaitya.core.registry import (
    AdapterRegistry,
    _parse_contract,
    build_dependency_graph,
    validate_contract,
)
from chaitya.core.types import (
    AdapterContract,
    AdapterLoadError,
    AdapterPackage,
    AdapterStatus,
    AdapterType,
    CommandSpec,
    DependencyGraph,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_registry_satisfies_protocol(self) -> None:
        assert isinstance(AdapterRegistry(), AdapterLoaderProtocol)


# ---------------------------------------------------------------------------
# Contract Parsing
# ---------------------------------------------------------------------------


def _sample_contract_dict(**overrides):
    """Return a minimal valid contract dict."""
    base = {
        "contract_version": "1",
        "name": "test-adapter",
        "description": "A test adapter",
        "depends_on": [],
        "commands": [
            {
                "name": "run",
                "description": "Run the test",
                "params": [{"name": "target", "required": True}],
                "examples": ["test-adapter run --target foo"],
            }
        ],
    }
    base.update(overrides)
    return base


class TestContractParsing:
    def test_parse_minimal(self) -> None:
        raw = _sample_contract_dict()
        contract = _parse_contract(raw)
        assert contract.name == "test-adapter"
        assert contract.contract_version == "1"
        assert len(contract.commands) == 1
        assert contract.commands[0].name == "run"
        assert contract.commands[0].examples == ["test-adapter run --target foo"]

    def test_parse_with_dependencies(self) -> None:
        raw = _sample_contract_dict(depends_on=["file", "shell"])
        contract = _parse_contract(raw)
        assert contract.depends_on == ["file", "shell"]

    def test_parse_permissions(self) -> None:
        raw = _sample_contract_dict(
            permissions={
                "fs_read": ["/tmp"],
                "network": True,
                "can_emit_events": False,
            }
        )
        contract = _parse_contract(raw)
        assert contract.permissions.fs_read == ["/tmp"]
        assert contract.permissions.network is True
        assert contract.permissions.can_emit_events is False

    def test_parse_empty_commands(self) -> None:
        raw = _sample_contract_dict(commands=[])
        contract = _parse_contract(raw)
        assert contract.commands == []

    def test_parse_resource_limits(self) -> None:
        raw = _sample_contract_dict(
            resource_limits={"max_execution_seconds": 60, "max_output_bytes": 1024}
        )
        contract = _parse_contract(raw)
        assert contract.resource_limits.max_execution_seconds == 60
        assert contract.resource_limits.max_output_bytes == 1024


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_contract(self) -> None:
        contract = _parse_contract(_sample_contract_dict())
        result = validate_contract(contract)
        assert result.valid is True
        assert result.errors == []

    def test_unsupported_contract_version(self) -> None:
        contract = _parse_contract(_sample_contract_dict(contract_version="99"))
        result = validate_contract(contract)
        assert result.valid is False
        assert any("contract_version" in e for e in result.errors)

    def test_empty_name_rejected(self) -> None:
        contract = _parse_contract(_sample_contract_dict(name=""))
        result = validate_contract(contract)
        assert result.valid is False
        assert any("name" in e.lower() for e in result.errors)

    def test_name_collision(self) -> None:
        contract = _parse_contract(_sample_contract_dict(name="existing"))
        result = validate_contract(contract, loaded_names=frozenset({"existing"}))
        assert result.valid is False
        assert any("collision" in e.lower() for e in result.errors)

    def test_command_no_description(self) -> None:
        raw = _sample_contract_dict(
            commands=[{"name": "bad", "description": "", "examples": ["ex"]}]
        )
        contract = _parse_contract(raw)
        result = validate_contract(contract)
        assert result.valid is False
        assert any("description" in e for e in result.errors)

    def test_command_no_examples(self) -> None:
        raw = _sample_contract_dict(
            commands=[{"name": "bad", "description": "Desc", "examples": []}]
        )
        contract = _parse_contract(raw)
        result = validate_contract(contract)
        assert result.valid is False
        assert any("example" in e.lower() for e in result.errors)



# ---------------------------------------------------------------------------
# Dependency Graph
# ---------------------------------------------------------------------------


def _pkg(name: str, depends_on: list[str] | None = None) -> AdapterPackage:
    """Helper to create an AdapterPackage with given name and deps."""
    contract = AdapterContract(
        name=name,
        depends_on=depends_on or [],
        commands=[
            CommandSpec(
                name="cmd",
                description="A command",
                examples=["ex"],
            )
        ],
    )
    return AdapterPackage(name=name, entry_point=f"{name}:main", contract=contract)


class TestDependencyGraph:
    def test_no_deps(self) -> None:
        pkgs = [_pkg("a"), _pkg("b"), _pkg("c")]
        graph = build_dependency_graph(pkgs)
        assert set(graph.load_order) == {"a", "b", "c"}
        assert graph.rejected == {}

    def test_linear_deps(self) -> None:
        pkgs = [_pkg("c", ["b"]), _pkg("b", ["a"]), _pkg("a")]
        graph = build_dependency_graph(pkgs)
        assert graph.load_order == ["a", "b", "c"]

    def test_diamond_deps(self) -> None:
        pkgs = [
            _pkg("d", ["b", "c"]),
            _pkg("b", ["a"]),
            _pkg("c", ["a"]),
            _pkg("a"),
        ]
        graph = build_dependency_graph(pkgs)
        a_idx = graph.load_order.index("a")
        b_idx = graph.load_order.index("b")
        c_idx = graph.load_order.index("c")
        d_idx = graph.load_order.index("d")
        assert a_idx < b_idx
        assert a_idx < c_idx
        assert b_idx < d_idx
        assert c_idx < d_idx

    def test_circular_deps_rejected(self) -> None:
        pkgs = [_pkg("a", ["b"]), _pkg("b", ["a"])]
        graph = build_dependency_graph(pkgs)
        assert "a" in graph.rejected
        assert "b" in graph.rejected
        assert "circular" in graph.rejected["a"].lower()

    def test_missing_dep_rejected(self) -> None:
        pkgs = [_pkg("a", ["nonexistent"])]
        graph = build_dependency_graph(pkgs)
        assert "a" in graph.rejected
        assert "missing" in graph.rejected["a"].lower()

    def test_mixed_valid_and_invalid(self) -> None:
        pkgs = [_pkg("a"), _pkg("b", ["nonexistent"]), _pkg("c", ["a"])]
        graph = build_dependency_graph(pkgs)
        assert "a" in graph.load_order
        assert "c" in graph.load_order
        assert "b" in graph.rejected

    def test_empty_packages(self) -> None:
        graph = build_dependency_graph([])
        assert graph.load_order == []
        assert graph.rejected == {}


# ---------------------------------------------------------------------------
# AdapterRegistry — load / validate / load_all
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    async def test_validate(self) -> None:
        reg = AdapterRegistry()
        pkg = _pkg("test")
        result = reg.validate(pkg)
        assert result.valid is True

    async def test_load_valid(self) -> None:
        reg = AdapterRegistry()
        pkg = _pkg("test")
        contract = await reg.load(pkg)
        assert contract.name == "test"
        assert "test" in reg.loaded_names
        assert reg.get_adapter("test") is pkg

    async def test_load_invalid_raises(self) -> None:
        reg = AdapterRegistry()
        bad_contract = AdapterContract(name="", contract_version="99")
        pkg = AdapterPackage(name="", contract=bad_contract)
        with pytest.raises(AdapterLoadError):
            await reg.load(pkg)

    async def test_load_duplicate_rejected(self) -> None:
        reg = AdapterRegistry()
        pkg1 = _pkg("test")
        pkg2 = _pkg("test")
        await reg.load(pkg1)
        with pytest.raises(AdapterLoadError, match="collision"):
            await reg.load(pkg2)

    async def test_unload(self) -> None:
        reg = AdapterRegistry()
        pkg = _pkg("test")
        await reg.load(pkg)
        assert reg.unload("test") is True
        assert reg.get_adapter("test") is None
        assert reg.unload("test") is False

    async def test_load_all_ordering(self) -> None:
        reg = AdapterRegistry()
        pkgs = [_pkg("b", ["a"]), _pkg("a")]
        loaded, failed = await reg.load_all(pkgs)
        assert loaded == ["a", "b"]
        assert failed == []

    async def test_load_all_system_failure_halts(self) -> None:
        reg = AdapterRegistry()
        pkgs = [_pkg("sys", ["nonexistent"])]
        with pytest.raises(AdapterLoadError):
            await reg.load_all(pkgs, system_adapters=frozenset({"sys"}))

    async def test_load_all_user_failure_continues(self) -> None:
        reg = AdapterRegistry()
        pkgs = [_pkg("a"), _pkg("bad", ["nonexistent"])]
        loaded, failed = await reg.load_all(pkgs)
        assert "a" in loaded
        assert "bad" in failed

    async def test_discover_workspace_packages(self) -> None:
        reg = AdapterRegistry()
        packages = reg._discover_workspace_packages()
        names = {pkg.name for pkg in packages}
        assert "file" in names
        assert "shell" in names

    async def test_workspace_packages_include_handlers(self) -> None:
        reg = AdapterRegistry()
        packages = reg._discover_workspace_packages()
        shell_pkg = next(pkg for pkg in packages if pkg.name == "shell")
        assert shell_pkg.handler is not None
