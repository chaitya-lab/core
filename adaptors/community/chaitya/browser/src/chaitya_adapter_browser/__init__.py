"""Browser automation adapter for Chaitya Core via Playwright.

Commands:
    launch       — Start a Chromium browser (headless or headed)
    navigate     — Open a URL in the current browser/page
    click        — Click an element by selector
    fill         — Fill an input field by selector or label
    press        — Press a key or key combination
    screenshot   — Take a screenshot (full page or viewport)
    search       — Perform a web search (defaults to Google)
    evaluate     — Execute JavaScript in the page context
    inner-text   — Get inner text of an element
    inner-html   — Get inner HTML of an element
    title        — Get page title
    url          — Get current page URL
    close        — Close the browser
    screenshot   — Take a screenshot (saves to file or returns base64)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict
from typing import Any

from chaitya_sdk import (
    ChaityaStream,
    SessionContext,
    adapter,
    event_bus,
)
from chaitya_sdk.types import Event


_browser_lock = asyncio.Lock()
_browser: Any = None
_page: Any = None
_context: Any = None
_headed = False
_browser_id: str = ""


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

_browser_contract = {
    "name": "browser",
    "description": (
        "Browser automation via Playwright (Chromium). Controls a real browser "
        "for web scraping, testing, and UI automation. Supports headless and "
        "headed modes so you can watch actions happen."
    ),
    "commands": [
        {
            "name": "launch",
            "description": "Start Chromium browser. Defaults to headed so you can watch.",
            "params": [
                {
                    "name": "headless",
                    "required": False,
                    "description": "Run headless (no visible window). Default: false",
                },
                {
                    "name": "viewport_width",
                    "required": False,
                    "description": "Viewport width in pixels. Default: 1280",
                },
                {
                    "name": "viewport_height",
                    "required": False,
                    "description": "Viewport height in pixels. Default: 800",
                },
                {
                    "name": "browser_id",
                    "required": False,
                    "description": "Named browser instance ID. Default: 'default'",
                },
            ],
            "examples": [
                "chaitya browser launch",
                "chaitya browser launch --headless",
                "chaitya browser launch --browser_id my-browser",
            ],
        },
        {
            "name": "navigate",
            "description": "Open a URL in the current browser page.",
            "params": [
                {"name": "url", "required": True, "description": "URL to open"},
                {
                    "name": "wait_until",
                    "required": False,
                    "description": "Wait until: load, domcontentloaded, networkidle. Default: load",
                },
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Navigation timeout in ms. Default: 30000",
                },
            ],
            "examples": [
                "chaitya browser navigate --url https://example.com",
            ],
        },
        {
            "name": "click",
            "description": "Click an element by selector.",
            "params": [
                {
                    "name": "selector",
                    "required": True,
                    "description": "CSS selector or text selector",
                },
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Timeout in ms. Default: 5000",
                },
            ],
            "examples": [
                'chaitya browser click --selector "#submit-button"',
                'chaitya browser click --selector "text=Submit"',
            ],
        },
        {
            "name": "fill",
            "description": "Fill an input field by selector, label, or name.",
            "params": [
                {
                    "name": "selector",
                    "required": False,
                    "description": "CSS selector for the input element",
                },
                {
                    "name": "label",
                    "required": False,
                    "description": "Label text to locate the input",
                },
                {
                    "name": "name",
                    "required": False,
                    "description": 'Input name attribute (e.g. "q", "email")',
                },
                {
                    "name": "value",
                    "required": True,
                    "description": "Text value to type into the field",
                },
            ],
            "examples": [
                'chaitya browser fill --selector "#search-box" --value "hello world"',
                'chaitya browser fill --label "Email" --value "test@example.com"',
                'chaitya browser fill --name q --value "search term"',
            ],
        },
        {
            "name": "press",
            "description": "Press a key or key combination (e.g. Enter, Control+a).",
            "params": [
                {
                    "name": "selector",
                    "required": False,
                    "description": "CSS selector to focus first (optional)",
                },
                {
                    "name": "key",
                    "required": True,
                    "description": "Key name (Enter, Escape, Tab, etc.) or shortcut (Control+a)",
                },
            ],
            "examples": [
                "chaitya browser press --key Enter",
                'chaitya browser press --selector "#search" --key Enter',
            ],
        },
        {
            "name": "search",
            "description": "Search the web via Google.",
            "params": [
                {
                    "name": "query",
                    "required": True,
                    "description": "Search query",
                },
                {
                    "name": "engine",
                    "required": False,
                    "description": "Search engine: google (default), bing, ddg",
                },
                {
                    "name": "click_first",
                    "required": False,
                    "description": "Click the first result. Default: false",
                },
            ],
            "examples": [
                'chaitya browser search --query "python async await"',
                'chaitya browser search --query "opencode" --click_first',
            ],
        },
        {
            "name": "screenshot",
            "description": "Take a screenshot. Saves to file or returns base64.",
            "params": [
                {
                    "name": "path",
                    "required": False,
                    "description": "File path to save (PNG). If omitted, returns base64.",
                },
                {
                    "name": "full_page",
                    "required": False,
                    "description": "Capture full scrollable page. Default: false",
                },
            ],
            "examples": [
                "chaitya browser screenshot --path /tmp/screen.png",
                "chaitya browser screenshot --full_page",
            ],
        },
        {
            "name": "evaluate",
            "description": "Execute JavaScript in the page context.",
            "params": [
                {
                    "name": "script",
                    "required": True,
                    "description": "JavaScript code to execute",
                },
            ],
            "examples": [
                'chaitya browser evaluate --script "document.title"',
                'chaitya browser evaluate --script "window.scrollBy(0, 200)"',
            ],
        },
        {
            "name": "inner-text",
            "description": "Get inner text of an element by selector.",
            "params": [
                {"name": "selector", "required": True, "description": "CSS selector"},
            ],
            "examples": ['chaitya browser inner-text --selector "h1"'],
        },
        {
            "name": "inner-html",
            "description": "Get inner HTML of an element by selector.",
            "params": [
                {"name": "selector", "required": True, "description": "CSS selector"},
            ],
            "examples": ['chaitya browser inner-html --selector "#content"'],
        },
        {
            "name": "title",
            "description": "Get the current page title.",
            "params": [],
            "examples": ["chaitya browser title"],
        },
        {
            "name": "url",
            "description": "Get the current page URL.",
            "params": [],
            "examples": ["chaitya browser url"],
        },
        {
            "name": "close",
            "description": "Close the browser instance.",
            "params": [
                {
                    "name": "browser_id",
                    "required": False,
                    "description": "Browser ID to close. Default: current",
                },
            ],
            "examples": ["chaitya browser close"],
        },
        {
            "name": "wait-for-selector",
            "description": "Wait for an element to appear in the DOM.",
            "params": [
                {"name": "selector", "required": True, "description": "CSS selector"},
                {
                    "name": "timeout",
                    "required": False,
                    "description": "Timeout in ms. Default: 10000",
                },
                {
                    "name": "state",
                    "required": False,
                    "description": "State: attached (default), visible, hidden, detached",
                },
            ],
            "examples": [
                'chaitya browser wait-for-selector --selector ".loading" --state hidden',
            ],
        },
        {
            "name": "select",
            "description": "Select options in a <select> element.",
            "params": [
                {
                    "name": "selector",
                    "required": True,
                    "description": "CSS selector for the <select> element",
                },
                {
                    "name": "value",
                    "required": False,
                    "description": "Option value to select",
                },
                {
                    "name": "label",
                    "required": False,
                    "description": "Option label to select",
                },
                {
                    "name": "index",
                    "required": False,
                    "description": "Option index to select (0-based)",
                },
            ],
            "examples": [
                'chaitya browser select --selector "#country" --value "US"',
            ],
        },
        {
            "name": "check",
            "description": "Check (or uncheck) a checkbox or radio button.",
            "params": [
                {"name": "selector", "required": True, "description": "CSS selector"},
                {
                    "name": "checked",
                    "required": False,
                    "description": "True to check, False to uncheck. Default: true",
                },
            ],
            "examples": [
                'chaitya browser check --selector "#agree-terms"',
            ],
        },
    ],
    "permissions": {
        "fs_read": ["."],
        "fs_write": ["."],
        "network": True,
        "can_emit_events": True,
    },
}


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------


async def _ensure_browser(headed: bool = False, viewport: tuple[int, int] | None = None) -> Any:
    global _browser, _page, _context, _headed, _browser_id
    vp = viewport or (1280, 800)

    if _browser is not None and _browser_id:
        return _browser, _page

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=not headed,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        viewport={"width": vp[0], "height": vp[1]},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()

    _browser = browser
    _context = context
    _page = page
    _headed = headed

    await event_bus.emit(
        Event(
            type="browser.launched",
            source_adapter="browser",
            payload={
                "headed": headed,
                "viewport": vp,
                "user_agent": "Chrome/120",
            },
        )
    )

    return browser, page


def _get_page() -> Any:
    if _page is None:
        raise RuntimeError("Browser not launched. Run 'browser launch' first.")
    return _page


# ---------------------------------------------------------------------------
# Adapter handler
# ---------------------------------------------------------------------------


@adapter(**_browser_contract)  # type: ignore[arg-type]
async def browser_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if sub == "launch":
        return await _handle_launch(ctx)
    if sub == "navigate":
        return await _handle_navigate(ctx)
    if sub == "click":
        return await _handle_click(ctx)
    if sub == "fill":
        return await _handle_fill(ctx)
    if sub == "press":
        return await _handle_press(ctx)
    if sub == "search":
        return await _handle_search(ctx)
    if sub == "screenshot":
        return await _handle_screenshot(ctx)
    if sub == "evaluate":
        return await _handle_evaluate(ctx)
    if sub == "inner-text":
        return await _handle_inner_text(ctx)
    if sub == "inner-html":
        return await _handle_inner_html(ctx)
    if sub == "title":
        return await _handle_title(ctx)
    if sub == "url":
        return await _handle_url(ctx)
    if sub == "close":
        return await _handle_close(ctx)
    if sub == "wait-for-selector":
        return await _handle_wait_for_selector(ctx)
    if sub == "select":
        return await _handle_select(ctx)
    if sub == "check":
        return await _handle_check(ctx)

    return f"Unknown browser command: {sub}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------


async def _handle_launch(ctx: SessionContext) -> tuple[bytes, int]:
    global _browser_id

    headless = ctx.args.get("headless", False)
    width = int(ctx.args.get("viewport_width") or 1280)
    height = int(ctx.args.get("viewport_height") or 800)
    browser_id = str(ctx.args.get("browser_id") or "default")

    if _browser is not None and _browser_id == browser_id:
        return f"Browser '{browser_id}' already running.\n".encode("utf-8"), 0

    if _browser is not None:
        await _cleanup_browser()

    await _ensure_browser(headed=not headless, viewport=(width, height))
    _browser_id = browser_id

    mode = "headless" if headless else "headed (visible window)"
    msg = f"Browser '{browser_id}' launched in {mode} mode ({width}x{height})\n"
    return msg.encode("utf-8"), 0


# ---------------------------------------------------------------------------
# navigate
# ---------------------------------------------------------------------------


async def _handle_navigate(ctx: SessionContext) -> tuple[bytes, int]:
    url = str(ctx.args.get("url") or "")
    if not url:
        return b"navigate: --url is required\n", 1

    page = _get_page()
    wait_str = str(ctx.args.get("wait_until") or "load")
    timeout_ms = float(ctx.args.get("timeout") or 30000)

    wait_map = {
        "load": "load",
        "domcontentloaded": "domcontentloaded",
        "networkidle": "networkidle",
        "commit": "commit",
    }
    wait_until = wait_map.get(wait_str, "load")

    await event_bus.emit(
        Event(
            type="browser.navigating",
            source_adapter="browser",
            payload={"url": url, "wait_until": wait_until},
        )
    )

    try:
        response = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        final_url = page.url
        status = response.status if response else 0

        await event_bus.emit(
            Event(
                type="browser.navigated",
                source_adapter="browser",
                payload={
                    "url": final_url,
                    "status": status,
                    "initial_url": url,
                },
            )
        )
        return (
            f"Navigated to {final_url} (status: {status})\n".encode("utf-8"),
            0,
        )
    except Exception as exc:
        return f"Navigation failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------


async def _handle_click(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    if not selector:
        return b"click: --selector is required\n", 1

    page = _get_page()
    timeout_ms = float(ctx.args.get("timeout") or 5000)

    try:
        await page.click(selector, timeout=timeout_ms)
        return f"Clicked: {selector}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"Click failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# fill
# ---------------------------------------------------------------------------


async def _handle_fill(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    label = str(ctx.args.get("label") or "")
    name = str(ctx.args.get("name") or "")
    value = str(ctx.args.get("value") or "")

    if not value:
        return b"fill: --value is required\n", 1
    if not selector and not label and not name:
        return b"fill: one of --selector, --label, or --name is required\n", 1

    page = _get_page()

    try:
        if label:
            await page.fill(f"label={label}", value)
            return f"Filled (by label '{label}'): {value[:50]}\n".encode("utf-8"), 0
        elif name:
            await page.fill(f"[name={name}]", value)
            return f"Filled (by name '{name}'): {value[:50]}\n".encode("utf-8"), 0
        else:
            await page.fill(selector, value)
            return f"Filled (by selector '{selector}'): {value[:50]}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"Fill failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# press
# ---------------------------------------------------------------------------


async def _handle_press(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    key = str(ctx.args.get("key") or "")
    if not key:
        return b"press: --key is required\n", 1

    page = _get_page()

    try:
        if selector:
            await page.locator(selector).press(key)
        else:
            await page.keyboard.press(key)
        return f"Pressed: {key}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"Press failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def _handle_search(ctx: SessionContext) -> tuple[bytes, int]:
    query = str(ctx.args.get("query") or "")
    if not query:
        return b"search: --query is required\n", 1

    engine = str(ctx.args.get("engine") or "google")
    click_first = str(ctx.args.get("click_first") or "false").lower() in ("true", "1", "yes")

    engines = {
        "google": "https://www.google.com/search?q=",
        "bing": "https://www.bing.com/search?q=",
        "ddg": "https://duckduckgo.com/?q=",
    }
    search_url = engines.get(engine, engines["google"]) + query.replace(" ", "+")

    page = _get_page()
    try:
        await page.goto(search_url, wait_until="load", timeout=30000)
        title = await page.title()

        if click_first:
            try:
                await page.click("a[href][data-ved], h3 a", timeout=5000)
                await page.wait_for_load_state("load", timeout=10000)
                final_url = page.url
                result = f"Clicked first result. Now at: {final_url}\nTitle: {await page.title()}\n"
            except Exception:
                result = (
                    f"Clicked first result but navigation failed.\nTitle: {await page.title()}\n"
                )
        else:
            result = f"Search results for '{query}' loaded.\nTitle: {title}\nURL: {page.url}\n"

        return result.encode("utf-8"), 0
    except Exception as exc:
        return f"Search failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


async def _handle_screenshot(ctx: SessionContext) -> tuple[bytes, int]:
    path = str(ctx.args.get("path") or "")
    full_page = str(ctx.args.get("full_page") or "false").lower() in ("true", "1", "yes")

    page = _get_page()

    try:
        if path:
            await page.screenshot(path=path, full_page=full_page)
            return f"Screenshot saved to: {path}\n".encode("utf-8"), 0
        else:
            data = await page.screenshot(full_page=full_page)
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:image/png;base64,{b64}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"Screenshot failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


async def _handle_evaluate(ctx: SessionContext) -> tuple[bytes, int]:
    script = str(ctx.args.get("script") or "")
    if not script:
        return b"evaluate: --script is required\n", 1

    page = _get_page()

    try:
        result = await page.evaluate(script)
        if isinstance(result, (dict, list)):
            result_str = json.dumps(result, default=str)
        elif result is None:
            result_str = "(undefined / void)"
        else:
            result_str = str(result)
        prefix = "> " if result is not None else ""
        return f"{prefix}{result_str}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"Evaluate failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# inner-text
# ---------------------------------------------------------------------------


async def _handle_inner_text(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    if not selector:
        return b"inner-text: --selector is required\n", 1

    page = _get_page()

    try:
        text = await page.locator(selector).inner_text()
        return f"{text}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"inner-text failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# inner-html
# ---------------------------------------------------------------------------


async def _handle_inner_html(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    if not selector:
        return b"inner-html: --selector is required\n", 1

    page = _get_page()

    try:
        html = await page.locator(selector).inner_html()
        return f"{html}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"inner-html failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# title
# ---------------------------------------------------------------------------


async def _handle_title(ctx: SessionContext) -> tuple[bytes, int]:
    page = _get_page()
    try:
        title = await page.title()
        return f"{title}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"title failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# url
# ---------------------------------------------------------------------------


async def _handle_url(ctx: SessionContext) -> tuple[bytes, int]:
    page = _get_page()
    try:
        return f"{page.url}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"url failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


async def _handle_close(ctx: SessionContext) -> tuple[bytes, int]:
    browser_id = str(ctx.args.get("browser_id") or "default")

    if _browser is None:
        return b"No browser is running.\n".encode("utf-8"), 1

    if browser_id != _browser_id and browser_id != "default":
        return f"Browser '{browser_id}' is not running. Current: '{_browser_id}'\n".encode(
            "utf-8"
        ), 1

    await _cleanup_browser()

    await event_bus.emit(
        Event(
            type="browser.closed",
            source_adapter="browser",
            payload={"browser_id": browser_id},
        )
    )

    return f"Browser '{browser_id}' closed.\n".encode("utf-8"), 0


async def _cleanup_browser() -> None:
    global _browser, _page, _context, _browser_id, _headed
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    _browser = None
    _page = None
    _context = None
    _browser_id = ""
    _headed = False


# ---------------------------------------------------------------------------
# wait-for-selector
# ---------------------------------------------------------------------------


async def _handle_wait_for_selector(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    if not selector:
        return b"wait-for-selector: --selector is required\n", 1

    timeout_ms = float(ctx.args.get("timeout") or 10000)
    state = str(ctx.args.get("state") or "attached")
    valid_states = ("attached", "detached", "visible", "hidden")
    if state not in valid_states:
        return f"wait-for-selector: --state must be one of {valid_states}\n".encode("utf-8"), 1

    page = _get_page()

    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return f"Selector '{selector}' is {state}.\n".encode("utf-8"), 0
    except Exception as exc:
        return f"wait-for-selector timeout: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


async def _handle_select(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    value = str(ctx.args.get("value") or "")
    label = str(ctx.args.get("label") or "")
    index_str = str(ctx.args.get("index") or "")

    if not selector:
        return b"select: --selector is required\n", 1

    page = _get_page()
    select_args = {"value": value} if value else {}
    if label:
        select_args = {"label": label}
    if index_str:
        try:
            select_args = {"index": int(index_str)}
        except ValueError:
            return f"select: invalid index '{index_str}'\n".encode("utf-8"), 1

    if not select_args:
        return b"select: one of --value, --label, or --index is required\n", 1

    try:
        await page.select_option(selector, **select_args)
        return f"Selected in '{selector}': {select_args}\n".encode("utf-8"), 0
    except Exception as exc:
        return f"select failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


async def _handle_check(ctx: SessionContext) -> tuple[bytes, int]:
    selector = str(ctx.args.get("selector") or "")
    if not selector:
        return b"check: --selector is required\n", 1

    checked = str(ctx.args.get("checked") or "true").lower() in ("true", "1", "yes")
    page = _get_page()

    try:
        if checked:
            await page.check(selector)
        else:
            await page.uncheck(selector)
        state = "checked" if checked else "unchecked"
        return f"Element '{selector}' {state}.\n".encode("utf-8"), 0
    except Exception as exc:
        return f"check failed: {exc}\n".encode("utf-8"), 1


# ---------------------------------------------------------------------------
# Module exports (Chaitya SDK contract)
# ---------------------------------------------------------------------------

__chaitya_handler__ = browser_handler
__adapter_contract__ = asdict(browser_handler.__chaitya_contract__)
