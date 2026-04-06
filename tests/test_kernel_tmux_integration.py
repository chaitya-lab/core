"""Kernel-level tmux integration tests for interactive session flow."""

from __future__ import annotations

import os
import uuid

import pytest

from chaitya.core.backends.psmux import is_psmux_available
from chaitya.core.backends.tmux import TmuxSessionBackend
from chaitya.core.kernel import Kernel


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
    reason=(
        "tmux/psmux kernel integration tests require a compatible backend "
        "and CHAITYA_RUN_TMUX_TESTS=1"
    ),
)


class TestKernelTmuxInteractive:
    async def test_interactive_roundtrip_through_kernel(self) -> None:
        backend = "psmux" if os.name == "nt" else "tmux"
        kernel = Kernel(db_path=":memory:", session_backend=backend)
        await kernel.boot()
        session_name = f"prompt-{uuid.uuid4().hex[:8]}"
        try:
            created = await kernel.dispatch(f"session create {session_name}")
            assert created.exit_code == 0

            python_cmd = "python" if os.name == "nt" else "python3"
            start_py = await kernel.dispatch(
                f'session send-input {session_name} "{python_cmd}" --newline'
            )
            assert start_py.exit_code == 0
            await kernel.dispatch(
                f'session send-input {session_name} "name = input(\'NAME? \')" --newline'
            )
            prompt_output = await kernel.dispatch(
                f"session output {session_name} --idle-timeout 0.4"
            )
            assert "NAME?" in prompt_output.processed

            await kernel.dispatch(f'session send-input {session_name} "muku" --newline')
            await kernel.dispatch(
                f'session send-input {session_name} "print(f\'HELLO {{name}}\')" --newline'
            )
            final_output = await kernel.dispatch(
                f"session output {session_name} --idle-timeout 0.4"
            )
            assert "HELLO muku" in final_output.processed

            events = await kernel.dispatch(f"watch --session {session_name} --limit 5")
            assert "session_created" in events.processed
        finally:
            await kernel.shutdown()
