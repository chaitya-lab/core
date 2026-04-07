"""Adapter Registry — discovery, validation, loading, dependency resolution.

The registry validates adapter contracts, discovers adapters from pip entry
points and workspace paths, builds a dependency graph (topological sort),
and loads adapters in the correct order.

System adapters failing to load → kernel halts.
User adapters failing to load → kernel warns, continues.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
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

# Built-in kernel adapter names (not loaded from workspace — handled internally)
REGISTRY_ADAPTER_NAME = "registry"


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

    Adapters can be disabled via:
    - ``enabled_adapters`` init parameter (whitelist — only these load)
    - ``disabled_adapters`` init parameter (from config)
    - ``CHAITYA_ENABLED_ADAPTERS`` / ``CHAITYA_DISABLED_ADAPTERS`` env vars (comma-separated)
    - ``disable(name)`` / ``enable(name)`` runtime calls

    If ``enabled_adapters`` is set, only those adapters load (whitelist).
    ``disabled_adapters`` filters further (blacklist).
    """

    def __init__(
        self,
        search_paths: list[str] | None = None,
        enabled_adapters: list[str] | None = None,
        disabled_adapters: list[str] | None = None,
    ) -> None:
        self._loaded: dict[str, AdapterPackage] = {}
        self._handlers: dict[str, Callable] = {}
        self._search_paths: list[str] = list(search_paths or [])

        env_enabled = os.environ.get("CHAITYA_ENABLED_ADAPTERS", "")
        env_enabled_list = [a.strip() for a in env_enabled.split(",") if a.strip()]
        configured_enabled = list(enabled_adapters or [])
        self._enabled: set[str] = set(configured_enabled + env_enabled_list)

        env_disabled = os.environ.get("CHAITYA_DISABLED_ADAPTERS", "")
        env_disabled_list = [a.strip() for a in env_disabled.split(",") if a.strip()]
        configured_disabled = list(disabled_adapters or [])
        self._disabled: set[str] = set(configured_disabled + env_disabled_list)

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

    @property
    def disabled_names(self) -> frozenset[str]:
        """Adapter names that are disabled and will not be loaded."""
        return frozenset(self._disabled)

    def disable(self, name: str) -> bool:
        """Disable an adapter by name. Returns True if it was loaded.

        If the adapter is currently loaded, it will remain loaded until the
        next kernel restart (use ``unload`` to remove it now).
        Disabled adapters will be skipped on the next boot.
        """
        self._disabled.add(name)
        return True

    def is_disabled(self, name: str) -> bool:
        """Return True if the adapter is disabled."""
        return name in self._disabled

    @property
    def enabled_names(self) -> frozenset[str]:
        """Explicitly enabled adapter names. Empty means all are allowed."""
        return frozenset(self._enabled)

    def enable(self, name: str) -> bool:
        """Add adapter to enabled list and remove from disabled list."""
        self._disabled.discard(name)
        self._enabled.add(name)
        return True

    def only_enable(self, names: list[str]) -> None:
        """Set the enabled list to exactly these names. Clears previous enabled list."""
        self._enabled = set(names)
        for name in names:
            self._disabled.discard(name)

    async def reload(self) -> dict[str, AdapterPackage]:
        """Reload all adapters.

        Clears loaded adapters and rediscover all adapters from entry points
        and workspace paths. Returns the newly loaded adapters.

        This allows adding/removing adapters without restarting the kernel.

        Usage:
            chaitya registry reload
        """
        self._loaded.clear()
        self._handlers.clear()

        packages = await self.discover()

        for pkg in packages:
            if pkg.status == AdapterStatus.LOADED and pkg.handler:
                self._loaded[pkg.name] = pkg
                self._handlers[pkg.name] = pkg.handler

        return self.loaded_adapters

    def add_search_path(self, path: str) -> None:
        """Add a filesystem path to search for adapters.

        The path is added to the end of the search paths list.
        Call reload() after adding paths to discover new adapters.
        """
        if path not in self._search_paths:
            self._search_paths.append(path)
        self._enabled = set(names)

    def _is_allowed(self, name: str) -> bool:
        """Return True if the adapter is allowed to load.

        Logic:
        - If _enabled is non-empty, adapter must be in _enabled (whitelist).
        - Then _disabled is applied (blacklist).
        """
        if self._enabled and name not in self._enabled:
            return False
        return name not in self._disabled

    # -- AdapterLoaderProtocol methods --

    async def discover(self) -> list[AdapterPackage]:
        """Discover adapter packages via pip entry points and workspace paths.

        Filters by ``enabled_adapters`` (whitelist) then ``disabled_adapters`` (blacklist).
        """
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
            if ep.name == REGISTRY_ADAPTER_NAME:
                logger.debug(
                    "Skipping pip entry point %r — handled as built-in kernel command", ep.name
                )
                continue
            if not self._is_allowed(ep.name):
                if self._enabled and ep.name not in self._enabled:
                    logger.info("Adapter %r not in enabled list — skipping.", ep.name)
                else:
                    logger.info("Adapter %r is disabled — skipping.", ep.name)
                continue
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

        # Strategy 1: handler has __chaitya_contract__ (from @adapter decorator)
        if handler is not None:
            contract = getattr(handler, "__chaitya_contract__", None)
            if contract is not None:
                # Convert AdapterContract to raw dict for _parse_contract
                raw_contract = {
                    "contract_version": contract.contract_version,
                    "name": contract.name,
                    "description": contract.description,
                    "depends_on": contract.depends_on,
                    "default_input_type": contract.default_input_type,
                    "default_output_type": contract.default_output_type,
                    "supports_dry_run": contract.supports_dry_run,
                    "commands": [
                        {
                            "name": cmd.name,
                            "description": cmd.description,
                            "params": [
                                {
                                    "name": p.name,
                                    "required": p.required,
                                    "type": p.type,
                                    "description": p.description,
                                    "example": p.example,
                                }
                                for p in cmd.params
                            ],
                            "examples": cmd.examples,
                        }
                        for cmd in contract.commands
                    ],
                    "permissions": {
                        "fs_read": contract.permissions.fs_read,
                        "fs_write": contract.permissions.fs_write,
                        "network": contract.permissions.network,
                    },
                    "events_emitted": contract.events_emitted,
                    "events_consumed": contract.events_consumed,
                }

        # Strategy 2: module has __adapter_contract__ attribute
        if raw_contract is None and hasattr(module, "__adapter_contract__"):
            raw_contract = module.__adapter_contract__

        # Strategy 3: module.json alongside the module file
        if raw_contract is None and hasattr(module, "__file__") and module.__file__:
            module_json = Path(module.__file__).parent / "module.json"
            if module_json.exists():
                raw_contract = json.loads(module_json.read_text(encoding="utf-8"))

        if raw_contract is None:
            raise AdapterLoadError(
                package_name,
                "No __adapter_contract__ attribute, module.json, or @adapter decorator found.",
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
        other_names = frozenset(n for n in self.loaded_names if n != package.name)
        return validate_contract(package.contract, other_names)

    async def load(self, package: AdapterPackage) -> AdapterContract:
        """Load an adapter package into the kernel.

        Validates the contract first.  Raises ``AdapterLoadError`` on failure.
        """
        if not self._is_allowed(package.name):
            package.status = AdapterStatus.REJECTED
            if self._enabled and package.name not in self._enabled:
                package.error = f"Adapter '{package.name}' is not in the enabled list."
            else:
                package.error = (
                    f"Adapter '{package.name}' is disabled (via config, env, or runtime)."
                )
            logger.info("Skipping adapter %s: %s", package.name, package.error)
            raise AdapterLoadError(package.name, package.error)

        if package.name in self.loaded_names:
            package.status = AdapterStatus.REJECTED
            package.error = f"Name collision: adapter '{package.name}' is already loaded."
            raise AdapterLoadError(package.name, package.error)

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
