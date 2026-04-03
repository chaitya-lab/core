"""Tests for custom adaptor discovery paths and suspension handling."""

from __future__ import annotations

import textwrap
from pathlib import Path

from chaitya.core.config import CoreConfig, EventBusConfig, KernelConfig, SessionConfig, StoreConfig
from chaitya.core.kernel import Kernel
from chaitya.core.registry import AdapterRegistry


def _write_custom_adaptor(base: Path, name: str = "asker") -> Path:
    package_dir = base / name / "src" / f"chaitya_adapter_{name}"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        textwrap.dedent(
            f"""\
            from dataclasses import asdict
            from chaitya_sdk import adapter, ChaityaStream, SessionContext

            @adapter(
                name="{name}",
                description="Custom test adaptor",
                commands=[{{
                    "name": "hello",
                    "description": "Greets after asking for a name",
                    "params": [{{
                        "name": "name",
                        "required": True,
                        "description": "Person name",
                        "on_missing": "suspend",
                    }}],
                    "examples": ["chaitya {name} hello --name muku"],
                }}],
            )
            def {name}_handler(stream: ChaityaStream, ctx: SessionContext):
                return f"Hello {{ctx.args['name']}}".encode("utf-8")

            __chaitya_handler__ = {name}_handler
            __adapter_contract__ = asdict({name}_handler.__chaitya_contract__)
            """
        ),
        encoding="utf-8",
    )
    return base


class TestCustomAdaptorPaths:
    async def test_registry_discovers_custom_search_path(self, tmp_path: Path) -> None:
        root = _write_custom_adaptor(tmp_path)
        registry = AdapterRegistry(search_paths=[str(root)])
        packages = registry._discover_workspace_packages()
        names = {pkg.name for pkg in packages}
        assert "asker" in names

    async def test_kernel_suspension_and_input_resume(self, tmp_path: Path) -> None:
        root = _write_custom_adaptor(tmp_path)
        config = CoreConfig(
            kernel=KernelConfig(),
            store=StoreConfig(path=":memory:"),
            event_bus=EventBusConfig(),
            session=SessionConfig(backend="local"),
            adapter_search_paths=[str(root)],
            system_adapters=["file", "shell"],
        )
        kernel = Kernel.from_config(config)
        await kernel.boot()
        try:
            suspended = await kernel.dispatch("asker hello")
            assert suspended.exit_code == 0
            assert "[waiting:" in suspended.processed

            pending = await kernel.dispatch("input list")
            assert "asker" in pending.processed
            request_id = pending.processed.splitlines()[1].split()[0]

            resumed = await kernel.dispatch(f"input respond {request_id} muku")
            assert resumed.exit_code == 0
            assert "Hello muku" in resumed.processed

            watched = await kernel.dispatch("watch --on input_requested --limit 5")
            assert "input_requested" in watched.processed
        finally:
            await kernel.shutdown()
