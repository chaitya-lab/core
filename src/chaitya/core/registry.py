"""Adapter Registry — discovery, validation, loading, dependency resolution.

The kernel contains a bootstrap loader that finds adapters via pip entry
points.  The registry validates contracts, builds a dependency graph
(topological sort), and loads adapters in the correct order.

System adapters failing to load → kernel halts.
User adapters failing to load → kernel warns, continues.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from chaitya.core.types import (
    AdapterContract,
    AdapterLoadError,
    AdapterPackage,
    AdapterPermissions,
    AdapterStatus,
    CommandSpec,
    DependencyGraph,
    OutputRoutingRule,
    ResourceLimits,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# Kernel-supported contract major versions
SUPPORTED_CONTRACT_VERSIONS: frozenset[str] = frozenset({"1"})

# Entry-point group name for adapter discovery
ENTRY_POINT_GROUP = "chaitya.adapters"

# Registry adapter entry point name (PRD §3.6: bootstrap loader)
REGISTRY_ADAPTER_NAME = "registry"


# ---------------------------------------------------------------------------
# Bootstrap Loader (PRD §3.6, §13 — ~50 lines)
# ---------------------------------------------------------------------------


class BootstrapLoader:
    """Minimal bootstrap loader — finds and loads the registry adapter.

    This is the kernel's only touchpoint with pip entry points.
    All subsequent adapter loading goes through the registry adapter.

    PRD §3.6:
        "The kernel contains a minimal bootstrap loader (~50 lines).
        It finds and loads the registry adapter from a known path
        (chaitya.adapters pip entry point). If the registry adapter
        is missing, the kernel halts with installation instructions."

    This class is intentionally minimal. It does NOT do discovery,
    validation, or dependency resolution — those are the registry adapter's
    responsibilities.
    """

    def __init__(self) -> None:
        self._registry_adapter: Any = None

    def load_registry_adapter(self) -> Any:
        """Find and load the registry adapter from pip entry points.

        Returns:
            The registry adapter package (AdapterPackage) with a handler.

        Raises:
            AdapterLoadError: if the registry adapter is not found or invalid.
        """
        if self._registry_adapter is not None:
            return self._registry_adapter

        from importlib.metadata import entry_points

        eps = entry_points()
        if hasattr(eps, "select"):
            registry_eps = list(eps.select(group=ENTRY_POINT_GROUP, name=REGISTRY_ADAPTER_NAME))
        else:
            registry_eps = [
                ep for ep in eps.get(ENTRY_POINT_GROUP, []) if ep.name == REGISTRY_ADAPTER_NAME
            ]

        if not registry_eps:
            raise AdapterLoadError(
                REGISTRY_ADAPTER_NAME,
                f"Registry adapter not found. "
                f"Install with: pip install chaitya-adapter-registry\n"
                f"The registry adapter is required for adapter discovery.",
            )

        ep = registry_eps[0]
        try:
            module = ep.load()
            pkg = self._package_from_module(
                package_name=REGISTRY_ADAPTER_NAME,
                entry_point=str(ep.value) if hasattr(ep, "value") else str(ep),
                module=module,
            )
            self._registry_adapter = pkg
            return pkg
        except Exception as exc:
            raise AdapterLoadError(REGISTRY_ADAPTER_NAME, str(exc)) from exc

    def _package_from_module(
        self,
        *,
        package_name: str,
        entry_point: str,
        module: Any,
    ) -> Any:
        raw_contract: dict[str, Any] | None = None
        handler: Any = None

        if hasattr(module, "__adapter_contract__"):
            raw_contract = module.__adapter_contract__

        if hasattr(module, "__file__") and module.__file__:
            module_json = Path(module.__file__).parent / "module.json"
            if module_json.exists():
                raw_contract = json.loads(module_json.read_text(encoding="utf-8"))

        if raw_contract is None:
            raise AdapterLoadError(
                package_name,
                "No __adapter_contract__ attribute or module.json found.",
            )

        contract = _parse_contract(raw_contract)

        if not contract.name:
            contract = AdapterContract(**{**contract.__dict__, "name": package_name})

        explicit = getattr(module, "__chaitya_handler__", None)
        if callable(explicit):
            handler = explicit
        else:
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                c = getattr(attr, "__chaitya_contract__", None)
                if callable(attr) and c is not None and c.name == package_name:
                    handler = attr
                    break

        return AdapterPackage(
            name=contract.name,
            entry_point=entry_point,
            contract=contract,
            handler=handler,
            status=AdapterStatus.LOADED,
        )


# ---------------------------------------------------------------------------
# Contract Parsing
# ---------------------------------------------------------------------------


def _parse_contract(raw: dict[str, Any]) -> AdapterContract:
    """Parse a raw dict (from module.json or decorator metadata) into an
    ``AdapterContract``.  Unknown keys are silently ignored so that future
    minor-version additions don't break older kernels.
    """
    commands: list[CommandSpec] = []
    for c in raw.get("commands", []):
        # Parse params from raw dicts
        params: list[Any] = []
        for p in c.get("params", c.get("parameters", [])):
            if isinstance(p, dict):
                from chaitya.core.types import CommandParam

                params.append(
                    CommandParam(
                        name=p.get("name", ""),
                        required=p.get("required", False),
                        type=p.get("type", "string"),
                        description=p.get("description", ""),
                        example=p.get("example", ""),
                        on_missing=p.get("on_missing", "error"),
                    )
                )
            else:
                params.append(p)

        commands.append(
            CommandSpec(
                name=c.get("name", ""),
                description=c.get("description", ""),
                params=params,
                examples=c.get("examples", []),
            )
        )

    perms = raw.get("permissions", {})
    permissions = AdapterPermissions(
        fs_read=perms.get("fs_read", []),
        fs_write=perms.get("fs_write", []),
        network=perms.get("network", False),
        can_emit_events=perms.get("can_emit_events", True),
        can_read_all_events=perms.get("can_read_all_events", False),
        can_access_sessions=perms.get("can_access_sessions", []),
    )

    output_routing: list[OutputRoutingRule] = []
    for r in raw.get("output_routing", []):
        output_routing.append(
            OutputRoutingRule(
                condition=r.get("condition", ""),
                emit_event_type=r.get("emit_event_type", r.get("event_type", "")),
            )
        )

    resource_raw = raw.get("resource_limits", {})
    resource_limits = ResourceLimits(
        max_execution_seconds=resource_raw.get("max_execution_seconds", 300),
        max_output_bytes=resource_raw.get("max_output_bytes", 50 * 1024 * 1024),
    )

    return AdapterContract(
        contract_version=str(raw.get("contract_version", "1")),
        name=raw.get("name", ""),
        description=raw.get("description", ""),
        depends_on=list(raw.get("depends_on", [])),
        default_session=raw.get("default_session", "default"),
        default_input_type=raw.get("default_input_type", "text/plain"),
        default_output_type=raw.get("default_output_type", "text/plain"),
        supports_dry_run=bool(raw.get("supports_dry_run", False)),
        commands=commands,
        permissions=permissions,
        output_routing=output_routing,
        events_emitted=list(raw.get("events_emitted", [])),
        events_consumed=list(raw.get("events_consumed", [])),
        resource_limits=resource_limits,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_contract(
    contract: AdapterContract,
    loaded_names: frozenset[str] | None = None,
) -> ValidationResult:
    """Validate an adapter contract against kernel requirements.

    Checks (PRD §3.6):
    - contract_version supported
    - All commands have description, parameters, ≥1 example
    - No name collision with already-loaded adapters
    - depends_on references all resolvable (checked later in graph)
    - permissions block present (warn if missing — permissive default v1)
    """
    errors: list[str] = []
    warnings: list[str] = []
    loaded = loaded_names or frozenset()

    # 1. Contract version
    major = contract.contract_version.split(".")[0]
    if major not in SUPPORTED_CONTRACT_VERSIONS:
        errors.append(
            f"Unsupported contract_version '{contract.contract_version}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_CONTRACT_VERSIONS))}."
        )

    # 2. Name required
    if not contract.name:
        errors.append("Adapter name is empty.")

    # 3. Name collision
    if contract.name in loaded:
        errors.append(
            f"Name collision: adapter '{contract.name}' is already loaded. "
            f"The newer adapter is rejected."
        )

    # 4. Commands validation
    if not contract.commands:
        warnings.append("Adapter declares no commands.")

    for cmd in contract.commands:
        if not cmd.description:
            errors.append(f"Command '{cmd.name}' has no description.")
        if not cmd.params:
            warnings.append(f"Command '{cmd.name}' declares no parameters.")
        if not cmd.examples:
            errors.append(
                f"Command '{cmd.name}' has no examples. At least one example is required."
            )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Dependency Graph Builder
# ---------------------------------------------------------------------------


def build_dependency_graph(packages: list[AdapterPackage]) -> DependencyGraph:
    """Build a topological ordering of adapter dependencies.

    Uses Kahn's algorithm.  Circular dependencies → both adapters rejected
    with a clear error message.
    """
    name_map: dict[str, AdapterPackage] = {p.name: p for p in packages}
    available_names = set(name_map.keys())

    # Check for missing dependencies first
    rejected: dict[str, str] = {}
    for pkg in packages:
        for dep in pkg.contract.depends_on:
            if dep not in available_names:
                rejected[pkg.name] = (
                    f"Missing dependency: '{dep}' is not installed. "
                    f"Install it with: pip install chaitya-adapter-{dep}"
                )
                break

    # Build adjacency for remaining packages
    remaining = {n for n in available_names if n not in rejected}
    # in_degree[node] = number of deps that must load before node
    in_degree: dict[str, int] = {n: 0 for n in remaining}
    # dependents[dep] = list of nodes that depend on dep
    dependents: dict[str, list[str]] = {n: [] for n in remaining}

    for name in remaining:
        deps = [d for d in name_map[name].contract.depends_on if d in remaining]
        in_degree[name] = len(deps)
        for d in deps:
            dependents[d].append(name)

    # Kahn's algorithm
    queue: list[str] = [n for n in remaining if in_degree[n] == 0]
    load_order: list[str] = []

    while queue:
        # Sort for deterministic ordering
        queue.sort()
        node = queue.pop(0)
        load_order.append(node)
        for dependent in dependents.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Anything left with in_degree > 0 is in a cycle
    for name in remaining:
        if name not in load_order:
            rejected[name] = (
                "Circular dependency detected. This adapter and its dependency "
                "chain form a cycle. Both are rejected."
            )

    return DependencyGraph(load_order=load_order, rejected=rejected)


# ---------------------------------------------------------------------------
# AdapterRegistry — implements AdapterLoaderProtocol
# ---------------------------------------------------------------------------


class AdapterRegistry:
    """Discovers, validates, and loads adapter packages.

    Implements ``AdapterLoaderProtocol``.

    Discovery uses ``importlib.metadata.entry_points`` for the
    ``chaitya.adapters`` group.  Each entry point should reference a module
    that exposes either:
    - A ``module.json`` file alongside it, OR
    - A ``__adapter_contract__`` dict attribute on the module.
    """

    def __init__(self, search_paths: list[str] | None = None) -> None:
        self._loaded: dict[str, AdapterPackage] = {}
        self._handlers: dict[str, Callable] = {}
        self._search_paths: list[str] = list(search_paths or [])

    def set_search_paths(self, search_paths: list[str]) -> None:
        """Replace additional filesystem search paths for adaptors."""
        self._search_paths = list(search_paths)

    @property
    def loaded_adapters(self) -> dict[str, AdapterPackage]:
        """Currently loaded adapters by name."""
        return dict(self._loaded)

    @property
    def loaded_names(self) -> frozenset[str]:
        return frozenset(self._loaded.keys())

    # -- AdapterLoaderProtocol methods --

    async def discover(self) -> list[AdapterPackage]:
        """Discover adapter packages via pip entry points."""
        packages: list[AdapterPackage] = []
        packages.extend(self._discover_workspace_packages())
        eps = entry_points()

        # Python 3.12+: eps is SelectableGroups / dict-like
        adapter_eps = (
            eps.select(group=ENTRY_POINT_GROUP)
            if hasattr(eps, "select")
            else eps.get(ENTRY_POINT_GROUP, [])
        )

        for ep in adapter_eps:
            try:
                pkg = self._load_entry_point(ep)
                packages.append(pkg)
            except Exception as exc:
                logger.warning(
                    "Failed to discover adapter from entry point %s: %s",
                    ep.name,
                    exc,
                )
                packages.append(
                    AdapterPackage(
                        name=ep.name,
                        entry_point=str(ep.value) if hasattr(ep, "value") else str(ep),
                        status=AdapterStatus.LOAD_ERROR,
                        error=str(exc),
                    )
                )

        return packages

    def _discover_workspace_packages(self) -> list[AdapterPackage]:
        """Discover adaptors from workspace and configured filesystem paths."""
        repo_root = Path(__file__).resolve().parents[3]
        sdk_src = repo_root / "sdk" / "src"
        if str(sdk_src) not in sys.path:
            sys.path.insert(0, str(sdk_src))

        packages: list[AdapterPackage] = []
        workspaces = [
            repo_root / "adaptors" / "core",
            repo_root / "adaptors" / "community",
        ]
        workspaces.extend(Path(path).expanduser() for path in self._search_paths)

        for workspace in workspaces:
            packages.extend(self._discover_workspace_path(workspace, skip_registry=True))
        return packages

    def _discover_workspace_path(
        self, workspace: Path, *, skip_registry: bool = False
    ) -> list[AdapterPackage]:
        packages: list[AdapterPackage] = []
        if not workspace.is_dir():
            return packages

        for adaptor_dir in self._iter_adaptor_dirs(workspace):
            if skip_registry and adaptor_dir.name == REGISTRY_ADAPTER_NAME:
                continue
            src_dir = adaptor_dir / "src"
            if not src_dir.is_dir():
                continue
            for init_file in src_dir.glob("*/__init__.py"):
                module_name = init_file.parent.name
                try:
                    packages.append(
                        self._load_module_from_path(
                            package_name=adaptor_dir.name,
                            module_name=module_name,
                            module_path=init_file,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to discover workspace adaptor %s: %s",
                        adaptor_dir,
                        exc,
                    )
        return packages

    def _iter_adaptor_dirs(self, workspace: Path) -> list[Path]:
        direct = [path for path in workspace.iterdir() if path.is_dir()]
        if any((path / "src").is_dir() for path in direct):
            return sorted(path for path in direct if path.is_dir())

        nested: list[Path] = []
        for parent in direct:
            nested.extend(sorted(path for path in parent.iterdir() if path.is_dir()))
        return [path for path in nested if path.is_dir()]

    def _load_module_from_path(
        self,
        *,
        package_name: str,
        module_name: str,
        module_path: Path,
    ) -> AdapterPackage:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise AdapterLoadError(package_name, f"Cannot load module at {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return self._package_from_module(
            package_name=package_name,
            entry_point=str(module_path),
            module=module,
        )

    def _load_entry_point(self, ep: Any) -> AdapterPackage:
        """Load a single entry point into an AdapterPackage."""
        module = ep.load()
        return self._package_from_module(
            package_name=ep.name,
            entry_point=str(ep.value) if hasattr(ep, "value") else str(ep),
            module=module,
        )

    def _package_from_module(
        self,
        *,
        package_name: str,
        entry_point: str,
        module: Any,
    ) -> AdapterPackage:
        raw_contract: dict[str, Any] | None = None
        handler = self._discover_handler(module, package_name)

        # Strategy 1: module has __adapter_contract__ attribute
        if hasattr(module, "__adapter_contract__"):
            raw_contract = module.__adapter_contract__

        # Strategy 2: module.json alongside the module file
        if raw_contract is None and hasattr(module, "__file__") and module.__file__:
            module_json = Path(module.__file__).parent / "module.json"
            if module_json.exists():
                raw_contract = json.loads(module_json.read_text(encoding="utf-8"))

        if raw_contract is None:
            raise AdapterLoadError(
                package_name,
                "No __adapter_contract__ attribute or module.json found.",
            )

        contract = _parse_contract(raw_contract)
        # Override name from entry point if not set
        if not contract.name:
            contract = AdapterContract(
                **{**contract.__dict__, "name": package_name}  # type: ignore[arg-type]
            )

        return AdapterPackage(
            name=contract.name,
            entry_point=entry_point,
            contract=contract,
            handler=handler,
            status=AdapterStatus.LOADED,
        )

    def _discover_handler(self, module: Any, package_name: str) -> Callable | None:
        explicit = getattr(module, "__chaitya_handler__", None)
        if callable(explicit):
            return explicit

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            contract = getattr(attr, "__chaitya_contract__", None)
            if callable(attr) and contract is not None and contract.name == package_name:
                return attr
        return None

    def validate(self, package: AdapterPackage) -> ValidationResult:
        """Validate an adapter's contract against kernel requirements."""
        return validate_contract(package.contract, self.loaded_names)

    async def load(self, package: AdapterPackage) -> AdapterContract:
        """Load an adapter package into the kernel.

        Validates the contract first.  Raises ``AdapterLoadError`` on failure.
        """
        result = self.validate(package)
        if not result.valid:
            package.status = AdapterStatus.REJECTED
            package.error = "; ".join(result.errors)
            raise AdapterLoadError(package.name, package.error)

        for warning in result.warnings:
            logger.warning("Adapter '%s': %s", package.name, warning)

        # Register the adapter
        package.status = AdapterStatus.LOADED
        self._loaded[package.name] = package
        if package.handler is not None:
            self._handlers[package.name] = package.handler
        logger.info(
            "Loaded adapter '%s' (v%s)",
            package.name,
            package.contract.contract_version,
        )
        return package.contract

    def build_dependency_graph(self, packages: list[AdapterPackage]) -> DependencyGraph:
        """Build topological ordering of adapter dependencies."""
        return build_dependency_graph(packages)

    # -- Convenience methods --

    async def load_all(
        self,
        packages: list[AdapterPackage],
        system_adapters: frozenset[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Discover → validate → build graph → load in order.

        Returns ``(loaded, failed)`` adapter name lists.

        Args:
            packages: Pre-discovered adapter packages.
            system_adapters: Names of system adapters.  If a system adapter
                fails to load, ``AdapterLoadError`` is raised immediately.
        """
        sys_adapters = system_adapters or frozenset()
        graph = self.build_dependency_graph(packages)
        pkg_map = {p.name: p for p in packages}

        loaded: list[str] = []
        failed: list[str] = []

        # Mark rejected packages
        for name, reason in graph.rejected.items():
            pkg = pkg_map.get(name)
            if pkg:
                pkg.status = AdapterStatus.REJECTED
                pkg.error = reason
            failed.append(name)
            logger.error("Adapter '%s' rejected: %s", name, reason)
            if name in sys_adapters:
                raise AdapterLoadError(name, reason)

        # Load in dependency order
        for name in graph.load_order:
            pkg = pkg_map.get(name)
            if pkg is None:
                continue
            try:
                await self.load(pkg)
                loaded.append(name)
            except AdapterLoadError as exc:
                failed.append(name)
                if name in sys_adapters:
                    raise
                logger.warning("User adapter '%s' failed to load: %s", name, exc)

        return loaded, failed

    def get_adapter(self, name: str) -> AdapterPackage | None:
        """Get a loaded adapter by name."""
        return self._loaded.get(name)

    def get_handler(self, name: str) -> Callable | None:
        """Get a loaded adaptor handler by name."""
        return self._handlers.get(name)

    def unload(self, name: str) -> bool:
        """Unload an adapter by name.  Returns True if it was loaded."""
        self._handlers.pop(name, None)
        return self._loaded.pop(name, None) is not None
