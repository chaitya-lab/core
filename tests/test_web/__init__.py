"""Test web module for fake browser testing.

This module provides a local test web server and fake web pages for testing
browser adapters without external dependencies.

## Contents

- server.py    - FastAPI server (port 18766) with multiple test pages
- pages/      - HTML page generators (search, dashboard, chat, login, etc.)
- shared.py    - Console capture, DOM event tracking, utilities
- conftest.py - Pytest fixtures

## Usage

```python
import pytest
from tests.test_web.conftest import test_web_server

def test_my_browser_feature(kernel, test_web_server):
    await kernel.dispatch(f"browser navigate --url {test_web_server}/search")
    # ...
```

## Test Pages

Available at / paths:
- /            - Home page
- /search      - Search page with input
- /dashboard   - Dashboard with widgets
- /chat       - Chat interface
- /login      - Login form
- /tabs       - Tabbed interface
- /scroll     - Infinite scroll
- /error      - Error simulation
- /slow       - Slow response
- /spa        - Client-side routing

## API Endpoints

- GET  /api/state       - Get server state
- POST /api/reset     - Reset server state
- GET  /api/console   - Get console logs
- GET  /api/events    - Get DOM events
- GET  /health       - Health check
"""
