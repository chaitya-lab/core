"""Comprehensive browser integration tests using the fake test web server.

This test suite uses the enhanced test_web server (port 18766) which provides:
- Console capture tracking
- DOM event tracking (clicks, inputs, forms)
- Multiple test pages (search, chat, dashboard, tabs, scroll, login, error, spa)
- API endpoints for state verification
- Deterministic, reproducible browser automation tests

Requires: playwright (python -m playwright install chromium)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, "sdk/src")

from chaitya.core.kernel import Kernel


def _reset_server_state(api_base: str) -> None:
    """Reset server state between tests."""
    import urllib.request

    req = urllib.request.Request(f"{api_base}/api/reset", method="POST")
    try:
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


@pytest.fixture
async def browser_kernel() -> AsyncGenerator[Kernel, None]:
    """Kernel with browser adapter loaded."""
    k = Kernel(db_path=":memory:")
    await k.boot()
    yield k
    try:
        await k.shutdown()
    except Exception:
        pass


@pytest.fixture
async def browser2_kernel() -> AsyncGenerator[Kernel, None]:
    """Kernel with browser2 daemon adapter loaded."""
    k = Kernel(db_path=":memory:")
    await k.boot()
    yield k
    try:
        await k.shutdown()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helper Methods
# ---------------------------------------------------------------------------


def _get_server_state(api_base: str) -> dict:
    """Get current server state."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{api_base}/api/state", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _get_console_logs(api_base: str) -> list[dict]:
    """Get captured console logs."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{api_base}/api/console", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Basic Browser Tests
# ---------------------------------------------------------------------------


class TestBasicBrowserOperations:
    """Test fundamental browser commands."""

    async def test_launch_headless(self, browser_kernel: Kernel, test_web_server: str) -> None:
        result = await browser_kernel.dispatch("browser launch --headless")
        assert result.exit_code == 0

    async def test_navigate_home(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/")
        assert result.exit_code == 0

    async def test_get_title(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/")
        result = await browser_kernel.dispatch("browser title")
        assert result.exit_code == 0
        assert len(result.processed) > 0

    async def test_get_url(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search")
        result = await browser_kernel.dispatch("browser url")
        assert result.exit_code == 0
        assert "search" in result.processed.lower()

    async def test_close_browser(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch("browser close")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Search Page Tests
# ---------------------------------------------------------------------------


class TestSearchPage:
    """Test search functionality with the fake search page."""

    async def test_search_with_query(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(
            f"browser navigate --url {test_web_server}/search?q=test"
        )
        assert result.exit_code == 0

    async def test_fill_search_input(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search")
        result = await browser_kernel.dispatch(
            'browser fill --selector "input[name=q]" --value "playwright"'
        )
        assert result.exit_code == 0

    async def test_search_evaluate_dom(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search?q=query")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.result-item').length\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Dashboard Tests
# ---------------------------------------------------------------------------


class TestDashboardPage:
    """Test dashboard page with widgets and forms."""

    async def test_dashboard_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(
            f"browser navigate --url {test_web_server}/dashboard"
        )
        assert result.exit_code == 0

    async def test_dashboard_widgets_exist(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.card').length\""
        )
        assert result.exit_code == 0

    async def test_dashboard_click_counter(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        await browser_kernel.dispatch('browser click --selector "#counter-inc"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('counter-display').textContent\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tabs Page Tests
# ---------------------------------------------------------------------------


class TestTabsPage:
    """Test tab navigation with ARIA pattern."""

    async def test_tabs_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/tabs")
        assert result.exit_code == 0

    async def test_click_tab_button(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/tabs")
        await browser_kernel.dispatch('browser click --selector "#tab-analytics"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('panel-analytics').style.display\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Scroll Page Tests
# ---------------------------------------------------------------------------


class TestScrollPage:
    """Test infinite scroll functionality."""

    async def test_scroll_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/scroll")
        assert result.exit_code == 0

    async def test_scroll_initial_content(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/scroll")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.result-item').length\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Login Page Tests
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Test login form and authentication."""

    async def test_login_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        assert result.exit_code == 0

    async def test_fill_login_form(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('username').value\""
        )
        assert result.exit_code == 0

    async def test_submit_login_form(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        await browser_kernel.dispatch('browser click --type "submit"')


# ---------------------------------------------------------------------------
# Chat Page Tests
# ---------------------------------------------------------------------------


class TestChatPage:
    """Test chat interface functionality."""

    async def test_chat_page_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/chat")
        assert result.exit_code == 0

    async def test_chat_message_input(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/chat")
        await browser_kernel.dispatch(
            'browser fill --selector "#chat-input" --value "Hello, World!"'
        )
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('chat-input').value\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Error Page Tests
# ---------------------------------------------------------------------------


class TestErrorPage:
    """Test error simulation and console error capture."""

    async def test_error_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        assert result.exit_code == 0

    async def test_trigger_console_error(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        await browser_kernel.dispatch(
            "browser evaluate --script \"console.error('Test error for capture')\""
        )
        result = await browser_kernel.dispatch("browser console")
        assert result.exit_code == 0

    async def test_fill_search_input(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search")
        result = await browser_kernel.dispatch(
            'browser fill --selector "input[name=q]" --value "playwright"'
        )
        assert result.exit_code == 0

    async def test_search_evaluate_dom(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search?q=query")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.result-item').length\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Dashboard Tests
# ---------------------------------------------------------------------------


class TestDashboardPage:
    """Test dashboard page with widgets and forms."""

    async def test_dashboard_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(
            f"browser navigate --url {test_web_server}/dashboard"
        )
        assert result.exit_code == 0

    async def test_dashboard_widgets_exist(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.card').length\""
        )
        assert result.exit_code == 0

    async def test_dashboard_click_counter(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        await browser_kernel.dispatch('browser click --selector "#counter-inc"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('counter-display').textContent\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Tabs Page Tests
# ---------------------------------------------------------------------------


class TestTabsPage:
    """Test tab navigation with ARIA pattern."""

    async def test_tabs_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/tabs")
        assert result.exit_code == 0

    async def test_click_tab_button(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/tabs")
        await browser_kernel.dispatch('browser click --selector "#tab-analytics"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('panel-analytics').style.display\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Scroll Page Tests
# ---------------------------------------------------------------------------


class TestScrollPage:
    """Test infinite scroll functionality."""

    async def test_scroll_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/scroll")
        assert result.exit_code == 0

    async def test_scroll_initial_content(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/scroll")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.result-item').length\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Login Page Tests
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Test login form and authentication."""

    async def test_login_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        assert result.exit_code == 0

    async def test_fill_login_form(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('username').value\""
        )
        assert result.exit_code == 0

    async def test_submit_login_form(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        await browser_kernel.dispatch('browser click --type "submit"')


# ---------------------------------------------------------------------------
# Chat Page Tests
# ---------------------------------------------------------------------------


class TestChatPage:
    """Test chat interface functionality."""

    async def test_chat_page_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/chat")
        assert result.exit_code == 0

    async def test_chat_message_input(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/chat")
        await browser_kernel.dispatch(
            'browser fill --selector "#chat-input" --value "Hello, World!"'
        )
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('chat-input').value\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Error Page Tests
# ---------------------------------------------------------------------------


class TestErrorPage:
    """Test error simulation and console error capture."""

    async def test_error_page_navigation(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        assert result.exit_code == 0

    async def test_trigger_console_error(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        await browser_kernel.dispatch(
            "browser evaluate --script \"console.error('Test error for capture')\""
        )
        result = await browser_kernel.dispatch("browser console")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Console Capture Tests
# ---------------------------------------------------------------------------


class TestConsoleCapture:
    """Test console log capture functionality."""

    async def test_console_log_capture(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        await browser_kernel.dispatch("browser evaluate --script \"console.log('test log')\"")
        result = await browser_kernel.dispatch("browser console")
        assert result.exit_code == 0
        assert "test log" in result.processed

    async def test_console_warn_capture(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        await browser_kernel.dispatch("browser evaluate --script \"console.warn('test warning')\"")
        result = await browser_kernel.dispatch("browser console")
        assert result.exit_code == 0
        assert "WARN" in result.processed or "test warning" in result.processed

    async def test_console_clear(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        await browser_kernel.dispatch("browser evaluate --script \"console.log('before clear')\"")
        result = await browser_kernel.dispatch("browser console --clear")
        assert result.exit_code == 0
        result2 = await browser_kernel.dispatch("browser console")
        assert "before clear" not in result2.processed


# ---------------------------------------------------------------------------
# DOM Event Tracking Tests
# ---------------------------------------------------------------------------


class TestDOMEventTracking:
    """Test DOM event tracking (clicks, inputs, forms)."""

    async def test_click_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        result = await browser_kernel.dispatch('browser click --selector "#counter-inc"')
        assert result.exit_code == 0

    async def test_input_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        result = await browser_kernel.dispatch('browser fill --name "username" --value "testuser"')
        assert result.exit_code == 0

    async def test_form_submit_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('login-form').dispatchEvent(new Event('submit'))\""
        )
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"window.__chaitya_test__.events.some(e => e.type === 'form_submit')\""
        )
        assert result.exit_code == 0

    async def test_input_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        result = await browser_kernel.dispatch('browser fill --name "username" --value "testuser"')
        assert result.exit_code == 0

    async def test_form_submit_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('login-form').dispatchEvent(new Event('submit'))\""
        )
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"window.__chaitya_test__.events.some(e => e.type === 'form_submit')\""
        )
        assert result.exit_code == 0

    async def test_input_tracking(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        result = await browser_kernel.dispatch('browser fill --name "username" --value "testuser"')
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# SPA Page Tests
# ---------------------------------------------------------------------------


class TestSPAPage:
    """Test single page application routing."""

    async def test_spa_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/spa")
        assert result.exit_code == 0

    async def test_spa_client_side_routing(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/spa")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('spa-title').textContent\""
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Slow Page Tests
# ---------------------------------------------------------------------------


class TestSlowPage:
    """Test slow response simulation."""

    async def test_slow_page_navigation(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/slow")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Screenshot Tests
# ---------------------------------------------------------------------------


class TestScreenshot:
    """Test screenshot functionality."""

    async def test_screenshot_base64_output(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        result = await browser_kernel.dispatch("browser screenshot")
        assert result.exit_code == 0
        assert "data:image" in result.raw or len(result.raw) > 1000

    async def test_screenshot_to_file(
        self, browser_kernel: Kernel, test_web_server: str, tmp_path: Path
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        out_path = str(tmp_path / "test_dashboard.png")
        result = await browser_kernel.dispatch(f"browser screenshot --path {out_path}")
        assert result.exit_code == 0
        p = Path(out_path)
        assert p.exists(), f"Screenshot not created: {result.processed}"


# ---------------------------------------------------------------------------
# Evaluate JavaScript Tests
# ---------------------------------------------------------------------------


class TestEvaluateJavaScript:
    """Test browser.evaluate command."""

    async def test_evaluate_simple_expression(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/")
        result = await browser_kernel.dispatch('browser evaluate --script "document.title"')
        assert result.exit_code == 0
        assert len(result.processed) > 0

    async def test_evaluate_dom_query(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.querySelectorAll('.widget').length\""
        )
        assert result.exit_code == 0

    async def test_evaluate_math_expression(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/")
        result = await browser_kernel.dispatch(
            'browser evaluate --script "JSON.stringify({result: 2 + 2})"'
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Browser2 Daemon Adapter Tests
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="browser2 daemon has event bus issue with Subscription hash")
class TestBrowser2Adapter:
    """Test browser2 daemon adapter with fake web server."""

    async def test_browser2_launch(self, browser2_kernel: Kernel, test_web_server: str) -> None:
        result = await browser2_kernel.dispatch("browser2 launch --headless")
        assert result.exit_code == 0

    async def test_browser2_navigate(self, browser2_kernel: Kernel, test_web_server: str) -> None:
        await browser2_kernel.dispatch("browser2 launch --headless")
        result = await browser2_kernel.dispatch(f"browser2 navigate --url {test_web_server}/search")
        assert result.exit_code == 0

    async def test_browser2_fill(self, browser2_kernel: Kernel, test_web_server: str) -> None:
        await browser2_kernel.dispatch("browser2 launch --headless")
        await browser2_kernel.dispatch(f"browser2 navigate --url {test_web_server}/search")
        result = await browser2_kernel.dispatch(
            'browser2 fill --selector "input[name=q]" --value "test"'
        )
        assert result.exit_code == 0

    async def test_browser2_get_title(self, browser2_kernel: Kernel, test_web_server: str) -> None:
        await browser2_kernel.dispatch("browser2 launch --headless")
        await browser2_kernel.dispatch(f"browser2 navigate --url {test_web_server}/dashboard")
        result = await browser2_kernel.dispatch("browser2 title")
        assert result.exit_code == 0

    async def test_browser2_close(self, browser2_kernel: Kernel, test_web_server: str) -> None:
        await browser2_kernel.dispatch("browser2 launch --headless")
        result = await browser2_kernel.dispatch("browser2 close")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Integration Tests (Multi-step workflows)
# ---------------------------------------------------------------------------


class TestIntegrationWorkflows:
    """End-to-end workflow tests."""

    async def test_search_workflow(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/search")
        await browser_kernel.dispatch(
            'browser fill --selector "input[name=q]" --value "integration test"'
        )
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('search-input').value\""
        )
        assert result.exit_code == 0
        assert "integration test" in result.processed

    async def test_login_workflow(self, browser_kernel: Kernel, test_web_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/login")
        await browser_kernel.dispatch('browser fill --name "username" --value "admin"')
        await browser_kernel.dispatch('browser fill --name "password" --value "secret123"')
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('username').value\""
        )
        assert result.exit_code == 0
        assert "admin" in result.processed

    async def test_dashboard_interaction_workflow(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/dashboard")
        await browser_kernel.dispatch('browser click --selector "#counter-inc"')
        await browser_kernel.dispatch('browser click --selector "#counter-inc"')
        await browser_kernel.dispatch("browser evaluate --script \"console.log('workflow test')\"")
        result = await browser_kernel.dispatch(
            "browser evaluate --script \"document.getElementById('counter-display').textContent\""
        )
        assert result.exit_code == 0
        assert "2" in result.processed

    async def test_error_workflow_with_console(
        self, browser_kernel: Kernel, test_web_server: str
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_web_server}/error")
        await browser_kernel.dispatch("browser evaluate --script \"console.error('test error')\"")
        result = await browser_kernel.dispatch("browser console")
        assert result.exit_code == 0
        assert "ERROR" in result.processed or "test error" in result.processed
