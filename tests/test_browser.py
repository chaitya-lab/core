"""Integration tests for the chaitya-adapter-browser.

Uses Playwright's Chromium in headed mode so you can watch actions happen.
Browser is launched fresh for each test to ensure clean state.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile

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
    try:
        await k.dispatch("browser close")
    except Exception:
        pass
    await k.shutdown()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestBrowserDiscovery:
    async def test_browser_adapter_loaded(self, kernel: Kernel) -> None:
        assert "browser" in kernel.registry.loaded_adapters

    async def test_browser_in_registry_list(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry list")
        assert "browser" in result.processed.lower()

    async def test_browser_registry_info(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("registry info browser")
        assert result.exit_code == 0
        assert "browser" in result.processed.lower()


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


class TestBrowserLaunch:
    async def test_launch_defaults_headed(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("browser launch")
        assert result.exit_code == 0
        assert "launched" in result.processed.lower()
        assert "headed" in result.processed.lower()

    async def test_launch_headless(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("browser launch --headless")
        assert result.exit_code == 0
        assert "headless" in result.processed.lower()

    async def test_launch_custom_viewport(self, kernel: Kernel) -> None:
        result = await kernel.dispatch(
            "browser launch --viewport_width 1920 --viewport_height 1080"
        )
        assert result.exit_code == 0
        assert "1920" in result.processed

    async def test_launch_twice_returns_already_running(self, kernel: Kernel) -> None:
        r1 = await kernel.dispatch("browser launch")
        assert r1.exit_code == 0
        r2 = await kernel.dispatch("browser launch")
        assert r2.exit_code == 0
        assert "already" in r2.processed.lower()


# ---------------------------------------------------------------------------
# Navigate
# ---------------------------------------------------------------------------


class TestBrowserNavigate:
    async def test_navigate_to_example(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        result = await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        assert result.exit_code == 0
        assert "example.com" in result.processed.lower()

    async def test_navigate_emits_event(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        await asyncio.sleep(0.3)
        history = await kernel.event_bus.history(EventFilter(event_types=["browser.navigated"]))
        assert len(history) >= 1
        assert any("example.com" in str(e.payload) for e in history)

    async def test_navigate_requires_url(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        result = await kernel.dispatch("browser navigate")
        assert result.exit_code != 0
        assert "url" in result.processed.lower()

    async def test_navigate_without_launch_fails(self, kernel: Kernel) -> None:
        result = await kernel.dispatch("browser navigate --url https://example.com")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Page info
# ---------------------------------------------------------------------------


class TestBrowserPageInfo:
    async def test_title(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser title")
        assert result.exit_code == 0
        assert len(result.processed.strip()) > 0

    async def test_url(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser url")
        assert result.exit_code == 0
        assert "example.com" in result.processed


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


class TestBrowserScreenshot:
    async def test_screenshot_to_file(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        tmp = tempfile.mktemp(suffix=".png")
        result = await kernel.dispatch(f"browser screenshot --path {tmp}")
        assert result.exit_code == 0
        assert os.path.exists(tmp)
        assert os.path.getsize(tmp) > 1000
        os.unlink(tmp)

    async def test_screenshot_base64(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser screenshot")
        assert result.exit_code == 0
        assert result.processed.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Fill & Click on example.com
# ---------------------------------------------------------------------------


class TestBrowserInteraction:
    async def test_fill_search_form(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://duckduckgo.com/html --timeout 15000")
        await asyncio.sleep(1)
        result = await kernel.dispatch("browser fill --name q --value 'playwright python'")
        assert result.exit_code == 0

    async def test_fill_by_selector(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://duckduckgo.com/html --timeout 15000")
        await asyncio.sleep(1)
        result = await kernel.dispatch(
            "browser fill --selector input[name=q] --value 'test content'"
        )
        assert result.exit_code == 0

    async def test_press_enter(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://www.google.com --timeout 15000")
        await asyncio.sleep(1)
        await kernel.dispatch("browser fill --label Search --value 'hello world'")
        result = await kernel.dispatch("browser press --key Enter")
        assert result.exit_code == 0
        await asyncio.sleep(2)
        title_result = await kernel.dispatch("browser title")
        assert (
            "hello" in title_result.processed.lower() or "google" in title_result.processed.lower()
        )

    async def test_click_text(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser click --selector 'text=Example Domain'")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------


class TestBrowserEvaluate:
    async def test_evaluate_document_title(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser evaluate --script 'document.title'")
        assert result.exit_code == 0
        processed = result.processed.replace("> ", "")
        assert "Example" in processed or "Domain" in processed

    async def test_evaluate_scroll(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch(
            "browser evaluate --script 'JSON.stringify({scrolled: true, y: window.scrollY})'"
        )
        assert result.exit_code == 0
        assert "scrolled" in result.processed


# ---------------------------------------------------------------------------
# Inner text/html
# ---------------------------------------------------------------------------


class TestBrowserDom:
    async def test_inner_text(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser inner-text --selector h1")
        assert result.exit_code == 0
        assert len(result.processed.strip()) > 0

    async def test_inner_html(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch("browser inner-html --selector body")
        assert result.exit_code == 0
        assert "<" in result.processed


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestBrowserSearch:
    async def test_search_google(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        result = await kernel.dispatch(
            "browser search --query 'what is playwright' --timeout 20000"
        )
        assert result.exit_code == 0
        assert "playwright" in result.processed.lower()


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestBrowserClose:
    async def test_close(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        result = await kernel.dispatch("browser close")
        assert result.exit_code == 0
        assert "closed" in result.processed.lower()

    async def test_close_then_navigate_fails(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser close")
        result = await kernel.dispatch("browser navigate --url https://example.com")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Wait for selector
# ---------------------------------------------------------------------------


class TestBrowserWait:
    async def test_wait_for_selector_visible(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch("browser navigate --url https://example.com --timeout 15000")
        result = await kernel.dispatch(
            "browser wait-for-selector --selector h1 --state visible --timeout 5000"
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Select & Check
# ---------------------------------------------------------------------------


class TestBrowserFormControls:
    async def test_check_checkbox(self, kernel: Kernel) -> None:
        await kernel.dispatch("browser launch")
        await kernel.dispatch(
            "browser evaluate --script \"document.body.innerHTML='<input type=checkbox id=c1>Test'\""
        )
        result = await kernel.dispatch("browser check --selector #c1")
        assert result.exit_code == 0
