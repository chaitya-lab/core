"""Kernel-level tmux integration tests for interactive session flow."""

from __future__ import annotations

import os

import pytest

from chaitya.core.kernel import Kernel
from chaitya.core.backends.tmux import TmuxSessionBackend


def _tmux_available() -> bool:
    try:
        TmuxSessionBackend()
    except RuntimeError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    (not _tmux_available()) or os.environ.get("CHAITYA_RUN_TMUX_TESTS") != "1",
    reason="tmux kernel integration tests require tmux and CHAITYA_RUN_TMUX_TESTS=1",
)


class TestKernelTmuxInteractive:
    async def test_interactive_roundtrip_through_kernel(self) -> None:
        kernel = Kernel(db_path=":memory:", session_backend="tmux")
        await kernel.boot()
        try:
            created = await kernel.dispatch("session create prompt")
            assert created.exit_code == 0

            start_py = await kernel.dispatch('session send-input prompt "python3" --newline')
            assert start_py.exit_code == 0
            await kernel.dispatch('session send-input prompt "name = input(\'NAME? \')" --newline')
            prompt_output = await kernel.dispatch("session output prompt --idle-timeout 0.4")
            assert "NAME?" in prompt_output.processed

            await kernel.dispatch('session send-input prompt "muku" --newline')
            await kernel.dispatch('session send-input prompt "print(f\'HELLO {name}\')" --newline')
            final_output = await kernel.dispatch("session output prompt --idle-timeout 0.4")
            assert "HELLO muku" in final_output.processed

            events = await kernel.dispatch("watch --session prompt --limit 5")
            assert "session_created" in events.processed
        finally:
            await kernel.shutdown()
