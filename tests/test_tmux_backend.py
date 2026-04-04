"""Integration tests for the tmux-backed session substrate."""

from __future__ import annotations

import asyncio
import os

import pytest

from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.backends.psmux import PsmuxBackend, is_psmux_available
from chaitya.core.protocols import SessionBackend
from chaitya.core.types import SessionIdentity


def _backend_available() -> bool:
    if os.name == "nt":
        return is_psmux_available()
    try:
        TmuxSessionBackend()
        return True
    except RuntimeError:
        return False


pytestmark = pytest.mark.skipif(
    (not _backend_available()) or os.environ.get("CHAITYA_RUN_TMUX_TESTS") != "1",
    reason="tmux/psmux integration tests require a compatible backend and CHAITYA_RUN_TMUX_TESTS=1",
)


@pytest.fixture
def backend() -> SessionBackend:
    if os.name == "nt":
        return PsmuxBackend(poll_interval=0.05)
    return TmuxSessionBackend(poll_interval=0.05)


async def _wait_for_output(backend: SessionBackend, name: str, expected: str) -> str:
    async def _collect() -> str:
        chunks: list[bytes] = []
        async for chunk in backend.stream_output(name):
            chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if expected in text:
                return text
        return ""

    return await asyncio.wait_for(_collect(), timeout=5.0)


class TestTmuxBackend:
    def test_tmux_backend_is_session_backend(self, backend: SessionBackend) -> None:
        assert isinstance(backend, SessionBackend)

    async def test_create_and_exists(self, backend: SessionBackend) -> None:
        await backend.create("tmux-test-create", SessionIdentity())
        assert await backend.exists("tmux-test-create")
        await backend.kill("tmux-test-create")

    async def test_send_input_and_stream_output(self, backend: SessionBackend) -> None:
        name = "tmux-test-output"
        await backend.create(name, SessionIdentity())
        try:
            cmd = b"echo 'CHAITYA_TMUX_OK'\r\n" if os.name == "nt" else b"printf 'CHAITYA_TMUX_OK\\n'\n"
            await backend.send_input(name, cmd)
            output = await _wait_for_output(backend, name, "CHAITYA_TMUX_OK")
            assert "CHAITYA_TMUX_OK" in output
        finally:
            await backend.kill(name)

    async def test_set_and_unset_env(self, backend: SessionBackend) -> None:
        name = "tmux-test-env"
        await backend.create(name, SessionIdentity())
        try:
            await backend.set_env(name, "CHAITYA_ENV_TEST", "present")
            if os.name == "nt":
                cmd = b"echo $env:CHAITYA_ENV_TEST\r\n"
            else:
                cmd = b"printf '%s\\n' \"$CHAITYA_ENV_TEST\"\n"
            await backend.send_input(name, cmd)
            output = await _wait_for_output(backend, name, "present")
            assert "present" in output

            await backend.unset_env(name, "CHAITYA_ENV_TEST")
            if os.name == "nt":
                cmd = b"if (-not $env:CHAITYA_ENV_TEST) { echo 'unset' }\r\n"
            else:
                cmd = b"printf '%s\\n' \"${CHAITYA_ENV_TEST-unset}\"\n"
            await backend.send_input(name, cmd)
            output = await _wait_for_output(backend, name, "unset")
            assert "unset" in output
        finally:
            await backend.kill(name)
