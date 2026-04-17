"""Enhanced test web server for browser automation testing.

This server provides deterministic fake web pages for testing browser adapters.
Features:
- Console capture tracking
- DOM event tracking (clicks, inputs, forms)
- State management
- Multiple test pages (search, chat, dashboard, tabs, etc.)
- Error simulation
- Slow response simulation

Run standalone:
    python tests/test_web/server.py

Server binds to 127.0.0.1:18766 (configurable via PORT env var).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from tests.test_web.pages import (
    chat_page,
    dashboard_page,
    error_page,
    home_page,
    login_page,
    scroll_page,
    search_page,
    slow_page,
    spa_page,
    tabs_page,
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Chaitya Test Web Server", version="2.0.0")

# ---------------------------------------------------------------------------
# State Tracking
# ---------------------------------------------------------------------------


class State:
    """Server-side state for tracking interactions."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.console_logs: list[dict[str, Any]] = []
        self.clicks: list[dict[str, Any]] = []
        self.inputs: list[dict[str, Any]] = []
        self.forms: list[dict[str, Any]] = []
        self.navigations: list[dict[str, Any]] = []
        self.api_calls: list[dict[str, Any]] = []
        self.login_attempts: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "console_logs": self.console_logs[-50:],  # Last 50 logs
            "clicks": self.clicks[-20:],
            "inputs": self.inputs[-20:],
            "forms": self.forms[-10:],
            "navigations": self.navigations[-20:],
            "api_calls": self.api_calls[-20:],
            "login_attempts": self.login_attempts[-10:],
        }


state = State()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home():
    return home_page()


@app.get("/search", response_class=HTMLResponse)
async def search(q: str = Query("")):
    return search_page(q)


@app.get("/chat", response_class=HTMLResponse)
async def chat():
    return chat_page()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return dashboard_page()


@app.get("/tabs", response_class=HTMLResponse)
async def tabs():
    return tabs_page()


@app.get("/scroll", response_class=HTMLResponse)
async def scroll():
    return scroll_page()


@app.get("/login", response_class=HTMLResponse)
async def login():
    return login_page()


@app.get("/slow", response_class=HTMLResponse)
async def slow():
    return slow_page()


@app.get("/error", response_class=HTMLResponse)
async def error_page_route():
    return error_page()


@app.get("/spa", response_class=HTMLResponse)
async def spa():
    return spa_page()


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/console")
async def get_console():
    """Get captured console logs from state."""
    return JSONResponse(state.console_logs[-100:])


@app.get("/api/state")
async def get_state():
    """Get full interaction state."""
    return JSONResponse(state.to_dict())


@app.post("/api/reset")
async def reset_state():
    """Reset all state."""
    state.reset()
    return JSONResponse({"status": "reset", "message": "All state cleared"})


@app.post("/api/clear-console")
async def clear_console():
    """Clear console logs only."""
    state.console_logs.clear()
    return JSONResponse({"status": "cleared"})


@app.get("/api/state/clicks")
async def get_clicks():
    return JSONResponse(state.clicks[-20:])


@app.get("/api/state/inputs")
async def get_inputs():
    return JSONResponse(state.inputs[-20:])


@app.get("/api/state/navigations")
async def get_navigations():
    return JSONResponse(state.navigations[-20:])


# Chat messages (server-side for multi-message test)
CHAT_MESSAGES = [
    {"id": 1, "user": "System", "text": "Welcome to the chat!", "time": "10:00"},
    {"id": 2, "user": "Alice", "text": "Hello!", "time": "10:01"},
]


@app.get("/api/chat/messages")
async def get_chat_messages():
    return JSONResponse(CHAT_MESSAGES)


@app.post("/api/chat/send")
async def send_chat_message(message: dict):
    """Send a chat message."""
    msg_id = len(CHAT_MESSAGES) + 1
    msg = {
        "id": msg_id,
        "user": message.get("user", "You"),
        "text": message.get("text", ""),
        "time": time.strftime("%H:%M"),
    }
    CHAT_MESSAGES.append(msg)
    state.api_calls.append({"type": "chat_send", "message": msg, "timestamp": time.time()})
    return JSONResponse(msg)


# Login endpoint
@app.post("/api/login")
async def login_api(username: str = Form(...), password: str = Form(...)):
    """Handle login."""
    ts = time.strftime("%H:%M:%S")
    attempt = {"username": username, "timestamp": ts}
    state.login_attempts.append(attempt)

    if username == "admin" and password == "secret123":
        return JSONResponse(
            {
                "success": True,
                "message": f"Welcome, {username}!",
                "user": {"username": username, "role": "admin"},
            }
        )
    elif username == "user" and password == "password":
        return JSONResponse(
            {
                "success": True,
                "message": f"Welcome, {username}!",
                "user": {"username": username, "role": "user"},
            }
        )
    else:
        return JSONResponse({"success": False, "message": "Invalid username or password"})


# Slow response endpoint
@app.get("/api/slow")
async def slow_endpoint(delay: int = Query(1000)):
    """Simulate slow response."""
    actual_delay = min(delay, 15000)  # Max 15 seconds
    await asyncio.sleep(actual_delay / 1000)
    return JSONResponse(
        {
            "delay": delay,
            "actual_delay": actual_delay,
            "message": "Response received",
            "timestamp": time.time(),
        }
    )


# Error simulation endpoints
@app.get("/api/error/{error_type}")
async def error_endpoint(error_type: str):
    """Simulate various HTTP errors."""
    if error_type == "404":
        return JSONResponse({"error": "Not Found"}, status_code=404)
    elif error_type == "500":
        return JSONResponse({"error": "Internal Server Error"}, status_code=500)
    elif error_type == "timeout":
        await asyncio.sleep(35)  # Long delay
        return JSONResponse({"message": "Finally!"})
    elif error_type == "slow":
        await asyncio.sleep(5)
        return JSONResponse({"message": "Slow but successful"})
    return JSONResponse({"error": "Unknown error"}, status_code=400)


# SPA data endpoints
@app.get("/api/spa/data1")
async def spa_data1():
    return JSONResponse(
        {
            "data": "Data Set 1",
            "items": [{"id": 1, "name": "Item A"}, {"id": 2, "name": "Item B"}],
            "timestamp": time.time(),
        }
    )


@app.get("/api/spa/data2")
async def spa_data2():
    return JSONResponse(
        {
            "data": "Data Set 2",
            "items": [{"id": 3, "name": "Item C"}, {"id": 4, "name": "Item D"}],
            "timestamp": time.time(),
        }
    )


# Health check
@app.get("/health")
async def health():
    return JSONResponse(
        {
            "status": "ok",
            "version": "2.0.0",
            "time": time.time(),
            "state_summary": {
                "console_logs": len(state.console_logs),
                "clicks": len(state.clicks),
                "inputs": len(state.inputs),
            },
        }
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    port = int(__import__("os").environ.get("PORT", "18766"))
    host = "127.0.0.1"

    print("=" * 60)
    print("Chaitya Test Web Server v2.0")
    print("=" * 60)
    print(f"Starting on http://{host}:{port}")
    print()
    print("Pages:")
    print(f"  http://{host}:{port}/           - Home")
    print(f"  http://{host}:{port}/search     - Search page")
    print(f"  http://{host}:{port}/chat       - Chat page")
    print(f"  http://{host}:{port}/dashboard   - Dashboard")
    print(f"  http://{host}:{port}/tabs       - Tab navigation")
    print(f"  http://{host}:{port}/scroll     - Infinite scroll")
    print(f"  http://{host}:{port}/login      - Login form")
    print(f"  http://{host}:{port}/slow        - Slow loading")
    print(f"  http://{host}:{port}/error      - Error simulation")
    print(f"  http://{host}:{port}/spa         - Single Page App")
    print()
    print("API Endpoints:")
    print(f"  GET  http://{host}:{port}/api/console   - Console logs")
    print(f"  GET  http://{host}:{port}/api/state      - Full state")
    print(f"  POST http://{host}:{port}/api/reset      - Reset state")
    print()

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
