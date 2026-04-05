"""Test web server for browser adapter integration tests.

This is a FastAPI app that provides a controlled web environment for
testing browser automation: forms, buttons, tables, search, dynamic JS.

Run standalone:
    python tests/test_server.py

The server binds to 127.0.0.1 with a random port (default 18765).
The PORT env var overrides the default.
"""

from __future__ import annotations

import random
import time
from typing import Any

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Chaitya Test Server", version="1.0.0")

ITEMS = [
    {"id": 1, "name": "Alice Smith", "email": "alice@example.com", "role": "admin"},
    {"id": 2, "name": "Bob Jones", "email": "bob@example.com", "role": "editor"},
    {"id": 3, "name": "Carol White", "email": "carol@example.com", "role": "viewer"},
    {"id": 4, "name": "Dave Brown", "email": "dave@example.com", "role": "editor"},
    {"id": 5, "name": "Eve Davis", "email": "eve@example.com", "role": "admin"},
]

COUNTER = {"value": 0}
LOG: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

HOME_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Chaitya Test Server</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
  h1 { color: #333; }
  nav { margin: 20px 0; }
  nav a { margin-right: 15px; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin: 20px 0; }
  table { width: 100%; border-collapse: collapse; }
  th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
  th { background: #f5f5f5; }
  form { display: flex; flex-direction: column; gap: 12px; max-width: 400px; }
  label { font-weight: 600; }
  input, select, textarea { padding: 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
  button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
  button:hover { background: #0056b3; }
  .success { color: green; font-weight: 600; }
  .counter { font-size: 48px; text-align: center; margin: 20px; }
  .dynamic-text { color: #666; }
  #search-results { margin-top: 15px; }
  .result-item { padding: 8px; border-bottom: 1px solid #eee; }
</style>
</head>
<body>
  <h1>Chaitya Test Server</h1>
  <nav>
    <a href="/">Home</a>
    <a href="/form">Form</a>
    <a href="/table">Table</a>
    <a href="/search">Search</a>
    <a href="/counter">Counter</a>
    <a href="/login">Login</a>
  </nav>
  <hr>
"""


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return (
        HOME_PAGE
        + """
  <h2>Welcome</h2>
  <div class="card">
    <p>This is a test server for browser automation.</p>
    <p>Use the links above to navigate to different test pages:</p>
    <ul>
      <li><strong>Form</strong> — input fields, select, textarea, submit</li>
      <li><strong>Table</strong> — data table with rows</li>
      <li><strong>Search</strong> — search with results</li>
      <li><strong>Counter</strong> — dynamic JS counter</li>
      <li><strong>Login</strong> — login form</li>
    </ul>
  </div>
  <p><em>Chaitya Test Server v1.0</em></p>
</body></html>"""
    )


@app.get("/form", response_class=HTMLResponse)
async def form_page() -> str:
    return (
        HOME_PAGE
        + """
  <h2>Form Test Page</h2>
  <div class="card">
    <form id="test-form" action="/form" method="post">
      <div>
        <label for="name">Full Name</label>
        <input type="text" id="name" name="name" placeholder="Enter your name" required>
      </div>
      <div>
        <label for="email">Email</label>
        <input type="email" id="email" name="email" placeholder="you@example.com" required>
      </div>
      <div>
        <label for="role">Role</label>
        <select id="role" name="role">
          <option value="">-- Select --</option>
          <option value="admin">Admin</option>
          <option value="editor">Editor</option>
          <option value="viewer">Viewer</option>
        </select>
      </div>
      <div>
        <label for="message">Message</label>
        <textarea id="message" name="message" rows="4" placeholder="Your message"></textarea>
      </div>
      <div>
        <label>
          <input type="checkbox" id="subscribe" name="subscribe" value="yes">
          Subscribe to newsletter
        </label>
      </div>
      <div>
        <button type="submit" id="submit-btn">Submit Form</button>
        <button type="reset" id="reset-btn">Reset</button>
      </div>
    </form>
    <div id="form-feedback" class="success" style="display:none;"></div>
  </div>
</body></html>"""
    )


@app.post("/form", response_class=HTMLResponse)
async def form_submit(
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(""),
    message: str = Form(""),
    subscribe: str = Form(""),
) -> str:
    ts = time.strftime("%H:%M:%S")
    LOG.append({"ts": ts, "name": name, "email": email, "role": role, "action": "form_submit"})
    return (
        HOME_PAGE
        + f"""
  <h2>Form Submitted</h2>
  <div class="card">
    <p class="success">Form submitted successfully at {ts}!</p>
    <table>
      <tr><th>Field</th><th>Value</th></tr>
      <tr><td>Name</td><td>{name}</td></tr>
      <tr><td>Email</td><td>{email}</td></tr>
      <tr><td>Role</td><td>{role}</td></tr>
      <tr><td>Message</td><td>{message}</td></tr>
      <tr><td>Subscribed</td><td>{subscribe}</td></tr>
    </table>
    <p><a href="/form">Submit another</a></p>
  </div>
</body></html>"""
    )


@app.get("/table", response_class=HTMLResponse)
async def table_page() -> str:
    rows = ""
    for item in ITEMS:
        rows += f"""
      <tr id="row-{item["id"]}">
        <td class="item-id">{item["id"]}</td>
        <td class="item-name">{item["name"]}</td>
        <td class="item-email">{item["email"]}</td>
        <td class="item-role">{item["role"]}</td>
        <td>
          <button class="edit-btn" data-id="{item["id"]}">Edit</button>
          <button class="delete-btn" data-id="{item["id"]}">Delete</button>
        </td>
      </tr>"""

    return (
        HOME_PAGE
        + f"""
  <h2>Data Table</h2>
  <div class="card">
    <table id="data-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Name</th>
          <th>Email</th>
          <th>Role</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </div>
</body></html>"""
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(q: str = Query("")) -> str:
    results = ""
    if q:
        results_data = [
            i for i in ITEMS if q.lower() in i["name"].lower() or q.lower() in i["email"].lower()
        ]
        for item in results_data:
            results += f"""
        <div class="result-item">
          <strong class="result-name">{item["name"]}</strong> —
          <span class="result-email">{item["email"]}</span>
          <br><small>Role: <span class="result-role">{item["role"]}</span></small>
        </div>"""
        if not results_data:
            results = "<p>No results found.</p>"

    return (
        HOME_PAGE
        + f"""
  <h2>Search</h2>
  <div class="card">
    <form id="search-form">
      <input type="search" id="search-input" name="q" value="{q}" placeholder="Search by name or email">
      <button type="submit">Search</button>
    </form>
    <div id="search-results">
      {results}
    </div>
  </div>
</body></html>"""
    )


@app.get("/counter", response_class=HTMLResponse)
async def counter_page() -> str:
    return (
        HOME_PAGE
        + f"""
  <h2>Dynamic Counter</h2>
  <div class="card">
    <p>Click the buttons to increment or decrement the counter.</p>
    <div class="counter" id="counter-display">{COUNTER["value"]}</div>
    <div style="text-align:center;">
      <button id="inc-btn" onclick="fetch('/counter/inc', {{method:'POST'}}).then(r=>r.json()).then(d=>document.getElementById('counter-display').textContent=d.value)">+ Increment</button>
      <button id="dec-btn" onclick="fetch('/counter/dec', {{method:'POST'}}).then(r=>r.json()).then(d=>document.getElementById('counter-display').textContent=d.value)">- Decrement</button>
      <button id="reset-btn" onclick="fetch('/counter/reset', {{method:'POST'}}).then(r=>r.json()).then(d=>document.getElementById('counter-display').textContent=d.value)">Reset</button>
    </div>
    <p id="counter-msg" class="dynamic-text"></p>
  </div>
  <script>
    document.getElementById('counter-display').addEventListener('click', function() {{
      document.getElementById('counter-msg').textContent = 'Counter clicked at ' + new Date().toLocaleTimeString();
    }});
  </script>
</body></html>"""
    )


@app.post("/counter/inc")
async def counter_inc() -> JSONResponse:
    COUNTER["value"] += 1
    return JSONResponse({"value": COUNTER["value"]})


@app.post("/counter/dec")
async def counter_dec() -> JSONResponse:
    COUNTER["value"] -= 1
    return JSONResponse({"value": COUNTER["value"]})


@app.post("/counter/reset")
async def counter_reset() -> JSONResponse:
    COUNTER["value"] = 0
    return JSONResponse({"value": COUNTER["value"]})


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    return (
        HOME_PAGE
        + """
  <h2>Login</h2>
  <div class="card">
    <form id="login-form" action="/login" method="post">
      <div>
        <label for="username">Username</label>
        <input type="text" id="username" name="username" autocomplete="username" required>
      </div>
      <div>
        <label for="password">Password</label>
        <input type="password" id="password" name="password" autocomplete="current-password" required>
      </div>
      <div>
        <button type="submit" id="login-btn">Sign In</button>
      </div>
    </form>
    <p id="login-feedback" style="color:red;display:none;"></p>
  </div>
</body></html>"""
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
) -> str:
    ts = time.strftime("%H:%M:%S")
    LOG.append({"ts": ts, "name": username, "action": "login_attempt"})
    if username == "admin" and password == "secret123":
        return (
            HOME_PAGE
            + f"""
  <h2>Welcome, {username}!</h2>
  <div class="card">
    <p class="success">Login successful!</p>
    <p>Logged in at {ts}.</p>
    <p><a href="/login">Logout</a></p>
  </div>
</body></html>"""
        )
    return (
        HOME_PAGE
        + """
  <h2>Login Failed</h2>
  <div class="card">
    <p style="color:red;">Invalid username or password.</p>
    <p><a href="/login">Try again</a></p>
  </div>
</body></html>"""
    )


@app.get("/api/items")
async def api_items() -> JSONResponse:
    return JSONResponse(ITEMS)


@app.get("/api/items/{item_id}")
async def api_item(item_id: int) -> JSONResponse:
    item = next((i for i in ITEMS if i["id"] == item_id), None)
    if item:
        return JSONResponse(item)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "time": time.time()})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    port = int(__import__("os").environ.get("PORT", "18765"))
    host = "127.0.0.1"
    print(f"Starting test server on {host}:{port}")
    print(f"  http://{host}:{port}/form   — form test")
    print(f"  http://{host}:{port}/table  — table test")
    print(f"  http://{host}:{port}/search — search test")
    print(f"  http://{host}:{port}/counter — counter test")
    print(f"  http://{host}:{port}/login  — login test")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
