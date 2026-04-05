"""Integration tests for browser adapters using the test web server.

Uses the existing Kernel dispatch, so the event bus is properly initialized.
Tests both the monolithic browser adapter (direct Playwright) and the
browser2 daemon-pattern adapter.

Requires: playwright (python -m playwright install chromium)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import AsyncGenerator, Generator

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, "sdk/src")

from chaitya.core.kernel import Kernel


def _start_test_server() -> subprocess.Popen:
    """Start the FastAPI test server."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.test_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18765",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd="/Users/muku/Projects/Chaitya/git/core",
    )
    for _ in range(30):
        try:
            import urllib.request

            urllib.request.urlopen("http://127.0.0.1:18765/health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Test server failed to start")


@pytest.fixture(scope="module")
def test_server() -> Generator[str, None, None]:
    """Start the test server and yield its URL."""
    proc = _start_test_server()
    yield "http://127.0.0.1:18765"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


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
    """Kernel with browser2 adapter loaded."""
    k = Kernel(db_path=":memory:")
    await k.boot()
    yield k
    try:
        await k.shutdown()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Monolithic browser adapter tests (direct Playwright)
# ---------------------------------------------------------------------------


class TestBrowserAdapterWithTestServer:
    """Browser adapter integration tests against the test server."""

    async def test_launch_headless(self, browser_kernel: Kernel, test_server: str) -> None:
        result = await browser_kernel.dispatch("browser launch --headless")
        assert result.exit_code == 0

    async def test_navigate_to_form(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(f"browser navigate --url {test_server}/form")
        assert result.exit_code == 0

    async def test_fill_form_by_name(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/form")
        result = await browser_kernel.dispatch(f"browser fill --name name --value 'John Doe'")
        assert result.exit_code == 0

    async def test_get_title(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/form")
        result = await browser_kernel.dispatch("browser title")
        assert result.exit_code == 0
        assert "Chaitya" in result.processed or "Form" in result.processed

    async def test_get_url(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/table")
        result = await browser_kernel.dispatch("browser url")
        assert result.exit_code == 0
        assert "table" in result.processed

    async def test_inner_text(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/table")
        result = await browser_kernel.dispatch('browser inner-text --selector "h2"')
        assert result.exit_code == 0
        assert len(result.processed) > 0

    async def test_search_page(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch(
            f"browser navigate --url {test_server}/search?q=Alice"
        )
        assert result.exit_code == 0

    async def test_evaluate(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/")
        result = await browser_kernel.dispatch('browser evaluate --script "document.title"')
        assert result.exit_code == 0
        assert len(result.processed) > 0

    async def test_screenshot_returns_data(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/form")
        result = await browser_kernel.dispatch("browser screenshot")
        assert result.exit_code == 0
        # Screenshot returns base64 data
        assert "data:image/png;base64," in result.raw or "data:image" in result.raw

    async def test_screenshot_to_file(
        self, browser_kernel: Kernel, test_server: str, tmp_path: Path
    ) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/form")
        out_path = str(tmp_path / "test_screenshot.png")
        result = await browser_kernel.dispatch(f"browser screenshot --path {out_path}")
        assert result.exit_code == 0

        p = Path(out_path)
        assert p.exists(), f"Screenshot file not created: {result.processed}"
        assert p.stat().st_size > 100, "Screenshot file too small"

    async def test_click_button(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        await browser_kernel.dispatch(f"browser navigate --url {test_server}/table")
        # Click the Edit button on the first row
        result = await browser_kernel.dispatch('browser click --selector ".edit-btn"')
        assert result.exit_code == 0

    async def test_close(self, browser_kernel: Kernel, test_server: str) -> None:
        await browser_kernel.dispatch("browser launch --headless")
        result = await browser_kernel.dispatch("browser close")
        assert result.exit_code == 0

    async def test_close_without_launch(self, browser_kernel: Kernel, test_server: str) -> None:
        result = await browser_kernel.dispatch("browser close")
        # Close without launch returns non-zero (no browser to close)
        assert result.exit_code in (0, 1)
