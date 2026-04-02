"""Integration tests for the tmux-backed session substrate."""

from __future__ import annotations

import asyncio
import os

import pytest

from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.protocols import SessionBackend
from chaitya.core.types import SessionIdentity


def _tmux_available() -> bool:
    try:
        TmuxSessionBackend()
    except RuntimeError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    (not _tmux_available()) or os.environ.get("CHAITYA_RUN_TMUX_TESTS") != "1",
    reason="tmux integration tests require tmux and CHAITYA_RUN_TMUX_TESTS=1",
)


@pytest.fixture
def backend() -> TmuxSessionBackend:
    return TmuxSessionBackend(poll_interval=0.05)


async def _wait_for_output(backend: TmuxSessionBackend, name: str, expected: str) -> str:
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
    def test_tmux_backend_is_session_backend(self, backend: TmuxSessionBackend) -> None:
        assert isinstance(backend, SessionBackend)

    async def test_create_and_exists(self, backend: TmuxSessionBackend) -> None:
        await backend.create("tmux-test-create", SessionIdentity())
        assert await backend.exists("tmux-test-create")
        await backend.kill("tmux-test-create")

    async def test_send_input_and_stream_output(self, backend: TmuxSessionBackend) -> None:
        name = "tmux-test-output"
        await backend.create(name, SessionIdentity())
        try:
            await backend.send_input(name, b"printf 'CHAITYA_TMUX_OK\\n'\n")
            output = await _wait_for_output(backend, name, "CHAITYA_TMUX_OK")
            assert "CHAITYA_TMUX_OK" in output
        finally:
            await backend.kill(name)

    async def test_set_and_unset_env(self, backend: TmuxSessionBackend) -> None:
        name = "tmux-test-env"
        await backend.create(name, SessionIdentity())
        try:
            await backend.set_env(name, "CHAITYA_ENV_TEST", "present")
            await backend.send_input(name, b"printf '%s\\n' \"$CHAITYA_ENV_TEST\"\n")
            output = await _wait_for_output(backend, name, "present")
            assert "present" in output

            await backend.unset_env(name, "CHAITYA_ENV_TEST")
            await backend.send_input(
                name,
                b"printf '%s\\n' \"${CHAITYA_ENV_TEST-unset}\"\n",
            )
            output = await _wait_for_output(backend, name, "unset")
            assert "unset" in output
        finally:
            await backend.kill(name)
