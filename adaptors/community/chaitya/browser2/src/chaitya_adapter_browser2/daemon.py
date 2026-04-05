"""Browser daemon — persistent Playwright process for browser2 adapter.

This is NOT an adapter. Started by the browser2 CLI adapter via SessionRunner.
Runs indefinitely in a tmux session, holding browser/page state.
Listens to the SQLite event bus for request events.

Usage:
    python -m chaitya_adapter_browser2.daemon

The daemon signals readiness by printing "browser2 daemon ready".
After that it stays alive until killed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import signal
import sys
from typing import Any

from chaitya_sdk import event_bus
from chaitya_sdk.types import Event

BROWSER: Any = None
PAGE: Any = None
CONTEXT: Any = None
PLAYWRIGHT: Any = None
HEADLESS = False
VIEWPORT = (1280, 800)
DAEMON_ID = ""
SHUTTING_DOWN = False


async def _emit_response(
    event_type: str, request_id: str | None, payload: dict, error: str | None = None
) -> None:
    rid = request_id if request_id is not None else ""
    await event_bus.emit(
        Event(
            type=event_type,
            source_adapter="browser2-daemon",
            request_id=rid,
            payload={**payload, "error": error} if error else payload,
        )
    )


async def _handle_launch(event: Event) -> None:
    global BROWSER, PAGE, CONTEXT, PLAYWRIGHT, HEADLESS, VIEWPORT, DAEMON_ID

    request_id = event.request_id
    payload = event.payload or {}

    headless = payload.get("headless", False)
    width = payload.get("viewport_width", 1280)
    height = payload.get("viewport_height", 800)
    daemon_id = payload.get("daemon_id", "default")

    HEADLESS = headless
    VIEWPORT = (width, height)
    DAEMON_ID = daemon_id

    try:
        if BROWSER is not None:
            await BROWSER.close()

        PLAYWRIGHT = None
        from playwright.async_api import async_playwright

        PLAYWRIGHT = await async_playwright().start()
        BROWSER = await PLAYWRIGHT.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        CONTEXT = await BROWSER.new_context(
            viewport={"width": width, "height": height},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        PAGE = await CONTEXT.new_page()

        await _emit_response(
            "browser2.launch_response",
            request_id,
            {
                "daemon_id": daemon_id,
                "mode": "headless" if headless else "headed",
                "viewport": VIEWPORT,
            },
        )
    except Exception as exc:
        await _emit_response("browser2.launch_response", request_id, {}, error=str(exc))


async def _handle_navigate(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.navigate_response", request_id, {}, error="Browser not launched"
        )
        return

    url = payload.get("url", "")
    wait_until = payload.get("wait_until", "load")
    timeout_ms = payload.get("timeout", 30000)

    if not url:
        await _emit_response("browser2.navigate_response", request_id, {}, error="url is required")
        return

    try:
        response = await PAGE.goto(url, wait_until=wait_until, timeout=timeout_ms)
        await _emit_response(
            "browser2.navigate_response",
            request_id,
            {
                "url": PAGE.url,
                "status": response.status if response else 0,
                "initial_url": url,
            },
        )
    except Exception as exc:
        await _emit_response("browser2.navigate_response", request_id, {}, error=str(exc))


async def _handle_screenshot(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.screenshot_response", request_id, {}, error="Browser not launched"
        )
        return

    full_page = payload.get("full_page", False)

    try:
        data = await PAGE.screenshot(full_page=full_page)
        b64 = base64.b64encode(data).decode("ascii")
        await _emit_response(
            "browser2.screenshot_response",
            request_id,
            {
                "base64": b64,
                "size_bytes": len(data),
                "full_page": full_page,
            },
        )
    except Exception as exc:
        await _emit_response("browser2.screenshot_response", request_id, {}, error=str(exc))


async def _handle_click(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.click_response", request_id, {}, error="Browser not launched"
        )
        return

    selector = payload.get("selector", "")
    timeout_ms = payload.get("timeout", 5000)

    if not selector:
        await _emit_response(
            "browser2.click_response", request_id, {}, error="selector is required"
        )
        return

    try:
        await PAGE.click(selector, timeout=timeout_ms)
        await _emit_response("browser2.click_response", request_id, {"selector": selector})
    except Exception as exc:
        await _emit_response("browser2.click_response", request_id, {}, error=str(exc))


async def _handle_fill(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response("browser2.fill_response", request_id, {}, error="Browser not launched")
        return

    selector = payload.get("selector", "")
    label = payload.get("label", "")
    name = payload.get("name", "")
    value = payload.get("value", "")

    if not value:
        await _emit_response("browser2.fill_response", request_id, {}, error="value is required")
        return

    if not selector and not label and not name:
        await _emit_response(
            "browser2.fill_response",
            request_id,
            {},
            error="one of selector, label, or name is required",
        )
        return

    try:
        if label:
            await PAGE.fill(f"label={label}", value)
            sel = f"label={label}"
        elif name:
            await PAGE.fill(f"[name={name}]", value)
            sel = f"[name={name}]"
        else:
            await PAGE.fill(selector, value)
            sel = selector
        await _emit_response(
            "browser2.fill_response", request_id, {"selector": sel, "value_len": len(value)}
        )
    except Exception as exc:
        await _emit_response("browser2.fill_response", request_id, {}, error=str(exc))


async def _handle_press(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.press_response", request_id, {}, error="Browser not launched"
        )
        return

    selector = payload.get("selector", "")
    key = payload.get("key", "")

    if not key:
        await _emit_response("browser2.press_response", request_id, {}, error="key is required")
        return

    try:
        if selector:
            await PAGE.locator(selector).press(key)
        else:
            await PAGE.keyboard.press(key)
        await _emit_response("browser2.press_response", request_id, {"key": key})
    except Exception as exc:
        await _emit_response("browser2.press_response", request_id, {}, error=str(exc))


async def _handle_evaluate(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.evaluate_response", request_id, {}, error="Browser not launched"
        )
        return

    script = payload.get("script", "")

    if not script:
        await _emit_response(
            "browser2.evaluate_response", request_id, {}, error="script is required"
        )
        return

    try:
        result = await PAGE.evaluate(script)
        if isinstance(result, (dict, list)):
            result_str = json.dumps(result, default=str)
        elif result is None:
            result_str = "(undefined)"
        else:
            result_str = str(result)
        await _emit_response("browser2.evaluate_response", request_id, {"result": result_str})
    except Exception as exc:
        await _emit_response("browser2.evaluate_response", request_id, {}, error=str(exc))


async def _handle_inner_text(event: Event) -> None:
    global PAGE

    request_id = event.request_id
    payload = event.payload or {}

    if PAGE is None:
        await _emit_response(
            "browser2.inner_text_response", request_id, {}, error="Browser not launched"
        )
        return

    selector = payload.get("selector", "")

    if not selector:
        await _emit_response(
            "browser2.inner_text_response", request_id, {}, error="selector is required"
        )
        return

    try:
        text = await PAGE.locator(selector).inner_text()
        await _emit_response("browser2.inner_text_response", request_id, {"text": text})
    except Exception as exc:
        await _emit_response("browser2.inner_text_response", request_id, {}, error=str(exc))


async def _handle_title(event: Event) -> None:
    global PAGE

    request_id = event.request_id

    if PAGE is None:
        await _emit_response(
            "browser2.title_response", request_id, {}, error="Browser not launched"
        )
        return

    try:
        title = await PAGE.title()
        await _emit_response("browser2.title_response", request_id, {"title": title})
    except Exception as exc:
        await _emit_response("browser2.title_response", request_id, {}, error=str(exc))


async def _handle_url(event: Event) -> None:
    global PAGE

    request_id = event.request_id

    if PAGE is None:
        await _emit_response("browser2.url_response", request_id, {}, error="Browser not launched")
        return

    try:
        await _emit_response("browser2.url_response", request_id, {"url": PAGE.url})
    except Exception as exc:
        await _emit_response("browser2.url_response", request_id, {}, error=str(exc))


async def _handle_close(event: Event) -> None:
    global BROWSER, PAGE, CONTEXT, PLAYWRIGHT

    request_id = event.request_id

    if BROWSER is not None:
        await BROWSER.close()
    BROWSER = None
    PAGE = None
    CONTEXT = None
    PLAYWRIGHT = None

    await _emit_response("browser2.close_response", request_id, {"closed": True})


async def _request_handler(event: Event) -> None:
    if event.type == "browser2.launch_requested":
        await _handle_launch(event)
    elif event.type == "browser2.navigate_requested":
        await _handle_navigate(event)
    elif event.type == "browser2.screenshot_requested":
        await _handle_screenshot(event)
    elif event.type == "browser2.click_requested":
        await _handle_click(event)
    elif event.type == "browser2.fill_requested":
        await _handle_fill(event)
    elif event.type == "browser2.press_requested":
        await _handle_press(event)
    elif event.type == "browser2.evaluate_requested":
        await _handle_evaluate(event)
    elif event.type == "browser2.inner_text_requested":
        await _handle_inner_text(event)
    elif event.type == "browser2.title_requested":
        await _handle_title(event)
    elif event.type == "browser2.url_requested":
        await _handle_url(event)
    elif event.type == "browser2.close_requested":
        await _handle_close(event)


async def _heartbeat() -> None:
    while not SHUTTING_DOWN:
        await asyncio.sleep(30)
        await event_bus.emit(
            Event(
                type="browser2.daemon_heartbeat",
                source_adapter="browser2-daemon",
                payload={"daemon_id": DAEMON_ID, "alive": True},
            )
        )


async def main() -> None:
    await event_bus.subscribe(
        _request_handler,
        event_types=[
            "browser2.launch_requested",
            "browser2.navigate_requested",
            "browser2.screenshot_requested",
            "browser2.click_requested",
            "browser2.fill_requested",
            "browser2.press_requested",
            "browser2.evaluate_requested",
            "browser2.inner_text_requested",
            "browser2.title_requested",
            "browser2.url_requested",
            "browser2.close_requested",
        ],
    )

    asyncio.create_task(_heartbeat())

    print("browser2 daemon ready")
    sys.stdout.flush()

    await asyncio.Event().wait()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        SHUTTING_DOWN = True
    finally:
        loop.run_until_complete(asyncio.sleep(0.1))
        loop.close()
