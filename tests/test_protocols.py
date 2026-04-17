"""Tests for chaitya.core.protocols — protocol conformance checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chaitya.core.protocols import (
    AdapterLoaderProtocol,
    EventBusProtocol,
    PipelineOrchestratorProtocol,
    SessionBackend,
    StoreProtocol,
)
from chaitya.core.types import (
    AdapterContract,
    AdapterPackage,
    CommandChain,
    DependencyGraph,
    Event,
    EventFilter,
    OutputChunk,
    PipelineContext,
    SessionHandle,
    SessionIdentity,
    SessionRecord,
    Subscription,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Minimal conforming implementations for protocol checks
# ---------------------------------------------------------------------------


class MinimalSessionBackend:
    """Minimal implementation satisfying SessionBackend protocol."""

    async def create(self, name: str, identity: SessionIdentity) -> SessionHandle:
        return SessionHandle(name=name)

    async def attach(self, name: str) -> None:
        pass

    async def view(self, name: str) -> str:
        return ""

    async def detach(self, name: str) -> None:
        pass

    async def kill(self, name: str) -> None:
        pass

    async def signal(self, name: str, sig: str) -> None:
        pass

    async def list(self) -> list[SessionRecord]:
        return []

    async def exists(self, name: str) -> bool:
        return False

    async def stream_output(self, name: str) -> AsyncIterator[bytes]:
        return
        yield  # type: ignore[misc]

    async def send_input(self, name: str, data: bytes) -> None:
        pass

    async def set_env(self, name: str, key: str, value: str) -> None:
        pass

    async def unset_env(self, name: str, key: str) -> None:
        pass


class MinimalEventBus:
    """Minimal implementation satisfying EventBusProtocol."""

    async def emit(self, event: Event) -> None:
        pass

    async def subscribe(self, event_filter: EventFilter, handler: Any) -> Subscription:
        return Subscription()

    async def unsubscribe(self, subscription: Subscription) -> None:
        pass

    async def history(self, event_filter: EventFilter) -> list[Event]:
        return []


class MinimalStore:
    """Minimal implementation satisfying StoreProtocol."""

    async def save_session(self, record: SessionRecord) -> None:
        pass

    async def get_session(self, name: str) -> SessionRecord | None:
        return None

    async def list_sessions(self) -> list[SessionRecord]:
        return []

    async def delete_session(self, name: str) -> None:
        pass

    async def append_event(self, event: Event) -> None:
        pass

    async def search_events(
        self, query: str, event_filter: EventFilter | None = None
    ) -> list[Event]:
        return []

    async def get_events(self, event_filter: EventFilter) -> list[Event]:
        return []

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass


class MinimalPipelineOrchestrator:
    """Minimal implementation satisfying PipelineOrchestratorProtocol."""

    def parse_chain(self, expression: str) -> CommandChain:
        return CommandChain()

    async def execute(
        self, chain: CommandChain, ctx: PipelineContext
    ) -> AsyncIterator[OutputChunk]:
        return
        yield  # type: ignore[misc]


class MinimalAdapterLoader:
    """Minimal implementation satisfying AdapterLoaderProtocol."""

    async def discover(self) -> list[AdapterPackage]:
        return []

    def validate(self, package: AdapterPackage) -> ValidationResult:
        return ValidationResult()

    async def load(self, package: AdapterPackage) -> AdapterContract:
        return AdapterContract()

    def build_dependency_graph(self, packages: list[AdapterPackage]) -> DependencyGraph:
        return DependencyGraph()


# ---------------------------------------------------------------------------
# Protocol Conformance Tests
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify that minimal implementations satisfy runtime_checkable protocols."""

    def test_session_backend(self) -> None:
        backend = MinimalSessionBackend()
        assert isinstance(backend, SessionBackend)

    def test_event_bus(self) -> None:
        bus = MinimalEventBus()
        assert isinstance(bus, EventBusProtocol)

    def test_store(self) -> None:
        store = MinimalStore()
        assert isinstance(store, StoreProtocol)

    def test_pipeline_orchestrator(self) -> None:
        orch = MinimalPipelineOrchestrator()
        assert isinstance(orch, PipelineOrchestratorProtocol)

    def test_adapter_loader(self) -> None:
        loader = MinimalAdapterLoader()
        assert isinstance(loader, AdapterLoaderProtocol)


class TestProtocolNonConformance:
    """Verify that incomplete implementations fail the check."""

    def test_empty_class_not_session_backend(self) -> None:
        class Empty:
            pass

        assert not isinstance(Empty(), SessionBackend)

    def test_empty_class_not_event_bus(self) -> None:
        class Empty:
            pass

        assert not isinstance(Empty(), EventBusProtocol)

    def test_empty_class_not_store(self) -> None:
        class Empty:
            pass

        assert not isinstance(Empty(), StoreProtocol)


# ---------------------------------------------------------------------------
# Functional Tests (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMinimalSessionBackendFunctional:
    async def test_create_returns_handle(self) -> None:
        backend = MinimalSessionBackend()
        handle = await backend.create("test", SessionIdentity())
        assert handle.name == "test"

    async def test_exists_returns_false(self) -> None:
        backend = MinimalSessionBackend()
        assert not await backend.exists("nonexistent")

    async def test_list_returns_empty(self) -> None:
        backend = MinimalSessionBackend()
        sessions = await backend.list()
        assert sessions == []


@pytest.mark.asyncio
class TestMinimalEventBusFunctional:
    async def test_emit_no_error(self) -> None:
        bus = MinimalEventBus()
        await bus.emit(Event(type="test"))

    async def test_subscribe_returns_subscription(self) -> None:
        bus = MinimalEventBus()
        sub = await bus.subscribe(EventFilter(), lambda e: None)
        assert sub.active is True

    async def test_history_returns_empty(self) -> None:
        bus = MinimalEventBus()
        events = await bus.history(EventFilter())
        assert events == []


@pytest.mark.asyncio
class TestMinimalStoreFunctional:
    async def test_save_and_get(self) -> None:
        store = MinimalStore()
        await store.save_session(SessionRecord(name="test"))
        result = await store.get_session("test")
        # Minimal impl returns None — that's fine for conformance
        assert result is None

    async def test_open_close(self) -> None:
        store = MinimalStore()
        await store.open()
        await store.close()
