"""Pytest fixtures for test_web module."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest


def _start_test_web_server() -> subprocess.Popen:
    """Start the enhanced test web server on port 18766."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.test_web.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18766",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parent.parent.parent,
    )
    for _ in range(30):
        try:
            import urllib.request

            urllib.request.urlopen("http://127.0.0.1:18766/health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("Test web server failed to start")


@pytest.fixture(scope="session")
def test_web_server() -> Generator[str, None, None]:
    """Start the enhanced test web server and yield its URL."""
    proc = _start_test_web_server()
    yield "http://127.0.0.1:18766"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def test_web_api() -> Generator[str, None, None]:
    """Provide base API URL for state verification."""
    yield "http://127.0.0.1:18766"
