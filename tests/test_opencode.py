"""Integration tests for the chaitya-adapter-opencode.

Uses Chaitya's real tmux session backend. OpenCode runs as a real CLI
inside tmux sessions, controlled via kernel session commands.
"""

from __future__ import annotations

import asyncio

import pytest

from chaitya.core.kernel import Kernel
from chaitya.core.types import EventFilter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def kernel():
    k = Kernel(db_path=":memory:", session_backend="tmux")
    await k.boot()
    yield k
    r = await k.dispatch("session list")
    for line in r.processed.splitlines():
        parts = line.split()
        if parts and parts[0] not in ("NAME", "-" * 16, ""):
            name = parts[0]
            if not name.startswith("-"):
                try:
                    await k.dispatch(f"session kill {name}")
                except Exception:
                    pass
    await k.shutdown()


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------


class TestOpenCodeDiscovery:
    async def test_opencode_adapter_loaded(self, kernel: Kernel) -> None:
        assert "opencode" in kernel.registry.loaded_adapters

    async def test_opencode_in_registry_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry list")
        assert "opencode" in result.processed.lower()

    async def test_opencode_registry_info(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry info opencode")
        assert result.exit_code == 0
        assert "opencode" in result.processed.lower()


# ---------------------------------------------------------------------------
# opencode session command (delegates to kernel session)
# ---------------------------------------------------------------------------


class TestOpenCodeSession:
    async def test_session_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("opencode session list")
        assert result.exit_code == 0

    async def test_session_create(self, kernel: Kernel) -> None:
        import uuid

        name = f"oc-test-{uuid.uuid4().hex[:8]}"
        result = await kernel.dispatch(f"opencode session create --session {name}")
        assert result.exit_code == 0

    async def test_session_create_emits_event(self, kernel: Kernel) -> None:
        await kernel.dispatch("opencode session create --session evt-test-oc")
        await asyncio.sleep(0.3)
        history = await kernel.event_bus.history(EventFilter(event_types=["session_created"]))
        backend_ids = [e.payload.get("backend_id", "") for e in history]
        assert any("evt-test-oc" in bid for bid in backend_ids)

    async def test_session_status(self, kernel: Kernel) -> None:
        await kernel.dispatch("opencode session create --session status-test-oc")
        result = await kernel.dispatch("opencode session status --session status-test-oc")
        assert result.exit_code == 0
        assert "status-test-oc" in result.processed

    async def test_session_kill(self, kernel: Kernel) -> None:
        await kernel.dispatch("opencode session create --session kill-test-oc")
        result = await kernel.dispatch("opencode session kill --session kill-test-oc")
        assert result.exit_code == 0

    async def test_session_kill_unknown_returns_error(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("opencode session kill --session does-not-exist-xyz")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# opencode run (real opencode in tmux)
# ---------------------------------------------------------------------------


class TestOpenCodeRun:
    async def test_run_requires_prompt(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("opencode run")
        assert result.exit_code != 0
        assert "prompt" in result.processed.lower()

    async def test_run_creates_session_and_returns_response(self, kernel: Kernel) -> None:
        import uuid

        name = f"ocrun-{uuid.uuid4().hex[:8]}"
        result = await kernel.dispatch(
            f'opencode run --prompt "say hi in 3 words" --session {name} --timeout 30'
        )
        assert result.exit_code == 0
        processed = result.processed.lower()
        assert len(processed) > 5

    async def test_run_with_custom_session(self, kernel: Kernel) -> None:
        import uuid

        name = f"customoc-{uuid.uuid4().hex[:8]}"
        result = await kernel.dispatch(
            f'opencode run --session {name} --prompt "what is 1+1" --timeout 30'
        )
        assert result.exit_code == 0

    @pytest.mark.skip(reason="Flaky: depends on opencode LLM session memory, not core correctness")
    async def test_run_multiturn_same_session(self, kernel: Kernel) -> None:
        import uuid

        name = f"mtoc-{uuid.uuid4().hex[:8]}"
        r1 = await kernel.dispatch(
            f'opencode run --session {name} --prompt "remember the word apple" --timeout 60'
        )
        assert r1.exit_code == 0

        r2 = await kernel.dispatch(
            f'opencode run --session {name} --prompt "what word did I ask you to remember just now" --timeout 60'
        )
        assert r2.exit_code == 0
        output_lower = r2.processed.lower()
        assert "apple" in output_lower or "remember" in output_lower

    async def test_run_emits_events(self, kernel: Kernel) -> None:
        import uuid

        name = f"evttest-{uuid.uuid4().hex[:8]}"
        await kernel.dispatch(f'opencode run --session {name} --prompt "hello world" --timeout 30')
        await asyncio.sleep(1)
        history = await kernel.event_bus.history(
            EventFilter(event_types=["opencode.prompt_sent", "opencode.response_received"])
        )
        assert len(history) >= 2
        assert all(e.source_adapter == "opencode" for e in history)


# ---------------------------------------------------------------------------
# opencode watch (delegates to kernel watch via event bus)
# ---------------------------------------------------------------------------


class TestOpenCodeWatch:
    async def test_watch_returns_json_lines(self, kernel: Kernel) -> None:
        import uuid

        name = f"watchoc-{uuid.uuid4().hex[:8]}"
        watch_task = asyncio.create_task(kernel.dispatch("opencode watch --limit 5"))
        await asyncio.sleep(0.5)
        await kernel.dispatch(f"opencode session create --session {name}")
        result = await watch_task
        assert result.exit_code == 0
        lines = result.processed.strip().split("\n")
        json_lines = [l for l in lines if l.startswith("{")]
        assert len(json_lines) >= 1, f"Expected JSON lines, got: {result.processed[:200]}"

    async def test_watch_with_session_filter(self, kernel: Kernel) -> None:
        import uuid

        name = f"filteroc-{uuid.uuid4().hex[:8]}"
        watch_task = asyncio.create_task(
            kernel.dispatch(f"opencode watch --session {name} --limit 3")
        )
        await asyncio.sleep(0.5)
        await kernel.dispatch(f"opencode session create --session {name}")
        result = await watch_task
        assert result.exit_code == 0
