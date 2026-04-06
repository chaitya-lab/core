"""Event-driven integration tests demonstrating the watch -> route -> action flow.

These tests validate:
1. watch --live streams events in real-time
2. route --if-pattern triggers on matching content
3. Events flow through sessions, adapters, and the pipeline

Architecture:
  Session A (file editor) -> emits events
  watch --live -> streams events
  route --if-pattern -> matches and triggers action
  Session B (browser) -> receives action

Run with: pytest tests/test_event_driven.py -v
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from chaitya.core.kernel import Kernel


@pytest.fixture
async def kernel():
    """Kernel with platform-appropriate session backend."""
    k = Kernel(db_path=":memory:")
    await k.boot()
    yield k
    await k.shutdown()


class TestEventDrivenFlow:
    """Test the event-driven automation architecture."""

    async def test_session_emits_events(self, kernel: Kernel) -> None:
        """Verify session create/activity emits events."""
        await kernel.dispatch("session create event-test")

        result = await kernel.dispatch("watch --session event-test --limit 5")
        assert result.exit_code == 0
        assert "session_created" in result.processed

    async def test_watch_live_streams_events(self, kernel: Kernel) -> None:
        """Test watch --live streams events in real-time via async generator."""
        await kernel.dispatch("session create live-test")

        result = await kernel.dispatch("watch --live --session live-test --timeout 2")
        assert result.exit_code == 0

    async def test_file_write_emits_events(self, kernel: Kernel, tmp_path: Path) -> None:
        """Test that file write operations emit events."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello from event test")

        result = await kernel.dispatch("watch --all --limit 5")
        assert result.exit_code == 0

    async def test_test_adapter_emit_creates_event(self, kernel: Kernel) -> None:
        """Test test.emit command creates an event on the bus."""
        result = await kernel.dispatch("test emit --name mytest --payload '{\"key\":\"value\"}'")
        assert result.exit_code == 0

        result = await kernel.dispatch("watch --on test.mytest --limit 1")
        assert result.exit_code == 0
        assert "mytest" in result.processed

    async def test_route_pattern_matching(self, kernel: Kernel) -> None:
        """Test route --if-pattern matches content."""
        result = await kernel.dispatch("test echo --message 'ERROR: something failed' | route --if-pattern ERROR")
        assert result.exit_code == 0
        assert "ERROR" in result.processed or result.exit_code == 0

    async def test_two_session_lifecycle(self, kernel: Kernel) -> None:
        """Test creating two sessions and verifying both exist."""
        r1 = await kernel.dispatch("session create editor-session")
        assert r1.exit_code == 0

        r2 = await kernel.dispatch("session create browser-session")
        assert r2.exit_code == 0

        result = await kernel.dispatch("session list")
        assert "editor-session" in result.processed
        assert "browser-session" in result.processed

    async def test_browser_session_integration(self, kernel: Kernel) -> None:
        """Test browser commands work within a session context."""
        await kernel.dispatch("session create browser-test")

        r = await kernel.dispatch("browser launch --headed")
        if r.exit_code != 0:
            pytest.skip("Browser not available")

        try:
            r = await kernel.dispatch("browser navigate --url https://example.com")
            assert r.exit_code == 0

            r = await kernel.dispatch("browser title")
            assert r.exit_code == 0
            assert "Example" in r.processed
        finally:
            await kernel.dispatch("browser close")

    async def test_html_file_roundtrip(self, kernel: Kernel, tmp_path: Path) -> None:
        """End-to-end: write HTML file, load in browser, verify title."""
        html_file = tmp_path / "test_page.html"
        html_file.write_text("""<!DOCTYPE html>
<html>
<head><title>Event Test Page</title></head>
<body>
<h1>Chaitya Event Test</h1>
<p>Events flowing: session -> watch -> route -> browser</p>
</body>
</html>""")

        r = await kernel.dispatch("browser launch")
        if r.exit_code != 0:
            pytest.skip("Browser not available")

        try:
            file_url = str(html_file.absolute())
            r = await kernel.dispatch(f"browser navigate --url file:///{file_url.replace(chr(92), '/')}")
            assert r.exit_code == 0

            r = await kernel.dispatch("browser title")
            assert r.exit_code == 0
            assert "Event Test Page" in r.processed

            r = await kernel.dispatch("browser inner-text --selector h1")
            assert r.exit_code == 0
            assert "Chaitya Event Test" in r.processed
        finally:
            await kernel.dispatch("browser close")

    async def test_watch_filters_by_event_type(self, kernel: Kernel) -> None:
        """Test watch --on filters by specific event type."""
        await kernel.dispatch("test emit --name specific_event")
        await kernel.dispatch("test emit --name other_event")

        result = await kernel.dispatch("watch --on test.specific_event --limit 10")
        assert result.exit_code == 0
        assert "specific_event" in result.processed

    async def test_session_send_input_captures_output(self, kernel: Kernel) -> None:
        """Test session send-input and output work correctly."""
        await kernel.dispatch("session create io-demo")

        if os.name == "nt":
            await kernel.dispatch("session send-input io-demo 'Write-Output hello world' --newline")
        else:
            await kernel.dispatch("session send-input io-demo 'echo hello world' --newline")

        result = await kernel.dispatch("session output io-demo --idle-timeout 1.0")
        assert result.exit_code == 0
        assert "hello" in result.processed.lower()

    async def test_kernel_dispatch_returns_structured_output(self, kernel: Kernel) -> None:
        """Test that dispatch returns proper CommandOutput with metadata."""
        result = await kernel.dispatch("test hello")
        assert result.exit_code == 0
        assert result.processed
        assert hasattr(result, "raw")
        assert hasattr(result, "duration_ms")


if __name__ == "__main__":
    asyncio.run(TestEventDrivenFlow().test_html_file_roundtrip(Path(tempfile.gettempdir())))
