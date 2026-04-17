"""Test harness for fake terminal apps and core adapter testing."""

from tests.test_harness.fake_terminals import (
    FakeClaudeCode,
    FakeCodex,
    FakeGenericTerminal,
    FakeOpenCode,
    FakeTerminalApp,
    MultiTerminalTestHarness,
    TerminalEvent,
    TerminalResponse,
)

__all__ = [
    "FakeTerminalApp",
    "FakeClaudeCode",
    "FakeCodex",
    "FakeOpenCode",
    "FakeGenericTerminal",
    "MultiTerminalTestHarness",
    "TerminalEvent",
    "TerminalResponse",
]
