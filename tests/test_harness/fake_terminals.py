"""Fake Terminal Apps - Simulate different CLI TUI patterns.

This module provides fake terminal apps that simulate various CLI patterns
(Codex, OpenCode, Claude Code, etc.) for testing the core adapters and session
management. Each fake app has different TUI patterns and interaction styles.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class TerminalEvent:
    """Event from the terminal app."""

    type: str
    payload: dict[str, Any]
    timestamp: float = 0.0


@dataclass
class TerminalResponse:
    """Response from the terminal app."""

    output: str
    exit_code: int
    events: list[TerminalEvent] = field(default_factory=list)


class FakeTerminalApp(ABC):
    """Base class for fake terminal apps."""

    def __init__(self, name: str, session_name: str = "default"):
        self.name = name
        self.session_name = session_name
        self.events: list[TerminalEvent] = []
        self._buffer = ""

    @abstractmethod
    async def start(self) -> None:
        """Start the terminal session."""
        pass

    @abstractmethod
    async def send(self, text: str) -> TerminalResponse:
        """Send text to the terminal and get response."""
        pass

    @abstractmethod
    async def read_output(self, timeout: float = 0.5) -> str:
        """Read accumulated output."""
        pass

    async def stop(self) -> None:
        """Stop the terminal session."""
        pass

    def add_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record an event."""
        import time

        self.events.append(TerminalEvent(type=event_type, payload=payload, timestamp=time.time()))


class FakeClaudeCode(FakeTerminalApp):
    """Simulates Claude Code's TUI patterns.

    Claude Code patterns:
    - ANSI color codes for syntax highlighting
    - Progress indicators with spinning chars
    - Thinking blocks with <thinking> tags
    - Confirmation prompts with [y/n] choices
    - Error messages with red text
    """

    THINKING_PATTERN = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)
    CONFIRM_PATTERN = re.compile(r"\[(Yes|No|y/n)\] \(([^)]+)\):?\s*$")
    ERROR_PATTERN = re.compile(r"\x1b\[31m(.*?)\x1b\[0m")
    PROGRESS_PATTERN = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")

    def __init__(self, session_name: str = "claude-code"):
        super().__init__("Claude Code", session_name)
        self._task_queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._buffer = f"\x1b[1m\x1b[36mthropic/claude-code\x1b[0m\n"
        self._buffer += f"\x1b[90mSession: {self.session_name}\x1b[0m\n\n"
        self.add_event("session_started", {"session": self.session_name})

    async def send(self, text: str) -> TerminalResponse:
        if not text.strip():
            return TerminalResponse(output=self._buffer, exit_code=0)

        events = []
        output = ""

        # Check for thinking blocks
        if text.strip().startswith("think:"):
            thinking = text.replace("think:", "").strip()
            output += f"<thinking>\n{thinking}\n</thinking>\n"
            events.append(TerminalEvent("thinking_completed", {"thinking": thinking}))

        # Check for confirmations
        confirm_match = self.CONFIRM_PATTERN.search(text)
        if confirm_match:
            output += f"\x1b[90mAuto-confirming: {confirm_match.group(2)}\x1b[0m\n"
            events.append(TerminalEvent("confirmed", {"response": "y"}))

        # Simulate command execution
        if text.startswith("!"):
            cmd = text[1:].strip()
            output += f"\x1b[90m$ {cmd}\x1b[0m\n"
            output += f"Executed: {cmd}\n"
            events.append(TerminalEvent("command_executed", {"command": cmd}))

        # Simulate file operations
        if text.startswith("Write"):
            output += f"\x1b[32m✓\x1b[0m Wrote file\n"
            events.append(TerminalEvent("file_written", {}))

        # Error simulation
        if "error" in text.lower() or "fail" in text.lower():
            output += "\x1b[31mError: Something went wrong\x1b[0m\n"
            events.append(TerminalEvent("error", {"message": "Simulated error"}))

        # Default echo
        if not output:
            output += f"You: {text}\n"

        self._buffer = output
        for event in events:
            self.add_event(event.type, event.payload)

        return TerminalResponse(output=output, exit_code=0, events=events)

    async def read_output(self, timeout: float = 0.5) -> str:
        await asyncio.sleep(0.01)  # Simulate small delay
        output = self._buffer
        self._buffer = ""
        return output


class FakeCodex(FakeTerminalApp):
    """Simulates OpenAI Codex CLI patterns.

    Codex patterns:
    - Simple prompts without ANSI
    - HTTP API style responses
    - JSON-like output
    - Status indicators
    """

    def __init__(self, session_name: str = "codex"):
        super().__init__("Codex", session_name)
        self._api_calls: list[dict[str, Any]] = []

    async def start(self) -> None:
        self._buffer = f"[Codex v1.0] Session: {self.session_name}\n"
        self._buffer += "Type 'help' for commands.\n\n"
        self.add_event("session_started", {"session": self.session_name})

    async def send(self, text: str) -> TerminalResponse:
        if not text.strip():
            return TerminalResponse(output=self._buffer, exit_code=0)

        output = ""
        events = []

        # Help command
        if text.strip().lower() == "help":
            output += "[Codex Commands]\n"
            output += "  /complete <prompt>  - Generate completion\n"
            output += "  /edit <file>        - Edit file\n"
            output += "  /explain <code>     - Explain code\n"
            return TerminalResponse(output=output, exit_code=0)

        # API style commands
        if text.startswith("/complete"):
            prompt = text[9:].strip()
            output += f"[API Call] completion\n"
            output += f"  prompt: {prompt[:50]}...\n"
            output += "[Response] Simulated completion\n"
            self._api_calls.append({"type": "complete", "prompt": prompt})
            events.append(TerminalEvent("api_call", {"type": "complete"}))

        elif text.startswith("/edit"):
            output += "[API Call] edit\n"
            output += "[Response] File edited\n"
            events.append(TerminalEvent("api_call", {"type": "edit"}))

        else:
            output += f"[Query] {text}\n"
            output += "[Response] Simulated response\n"

        self._buffer = output
        for event in events:
            self.add_event(event.type, event.payload)

        return TerminalResponse(output=output, exit_code=0, events=events)

    async def read_output(self, timeout: float = 0.5) -> str:
        output = self._buffer
        self._buffer = ""
        return output


class FakeOpenCode(FakeTerminalApp):
    """Simulates OpenCode CLI patterns.

    OpenCode patterns:
    - Markdown formatted output
    - Block code with ```
    - Status badges
    - Progress spinners
    """

    SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    STATUS_BADGES = {
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️",
    }

    def __init__(self, session_name: str = "opencode"):
        super().__init__("OpenCode", session_name)
        self._spinner_index = 0

    async def start(self) -> None:
        self._buffer = "# OpenCode\n"
        self._buffer += f"Session: `{self.session_name}`\n\n"
        self.add_event("session_started", {"session": self.session_name})

    async def send(self, text: str) -> TerminalResponse:
        if not text.strip():
            return TerminalResponse(output=self._buffer, exit_code=0)

        output = ""
        events = []

        # Markdown code blocks
        if text.startswith("```"):
            output += "```\nSimulated code output\n```\n"
            events.append(TerminalEvent("code_block", {}))

        # Task execution
        elif text.startswith("task:"):
            task = text[5:].strip()
            output += f"**Task:** {task}\n\n"
            for i in range(3):
                spinner = self.SPINNERS[self._spinner_index % len(self.SPINNERS)]
                output += f"\r{spinner} Running..."
                self._spinner_index += 1
                await asyncio.sleep(0.01)
            output += f"\r{self.STATUS_BADGES['success']} Done!\n"
            events.append(TerminalEvent("task_completed", {"task": task}))

        # Status checks
        elif text.startswith("status"):
            output += f"| Component | Status |\n"
            output += f"|-----------|--------|\n"
            output += f"| Session | {self.STATUS_BADGES['success']} Active |\n"
            output += f"| API | {self.STATUS_BADGES['success']} Connected |\n"

        else:
            output += f"**You:** {text}\n\n"
            output += "**OpenCode:** Simulated response\n"

        self._buffer = output
        for event in events:
            self.add_event(event.type, event.payload)

        return TerminalResponse(output=output, exit_code=0, events=events)

    async def read_output(self, timeout: float = 0.5) -> str:
        output = self._buffer
        self._buffer = ""
        return output


class FakeGenericTerminal(FakeTerminalApp):
    """A generic terminal that can simulate various patterns.

    Useful for testing edge cases and generic scenarios.
    """

    def __init__(self, session_name: str = "generic"):
        super().__init__("Generic", session_name)
        self._custom_handlers: dict[str, callable] = {}

    async def start(self) -> None:
        self._buffer = f"[Terminal] Session: {self.session_name}\n"
        self.add_event("session_started", {"session": self.session_name})

    async def send(self, text: str) -> TerminalResponse:
        if not text.strip():
            return TerminalResponse(output=self._buffer, exit_code=0)

        # Check custom handlers
        for pattern, handler in self._custom_handlers.items():
            if re.match(pattern, text):
                result = await handler(text)
                self._buffer = result.output
                for event in result.events:
                    self.add_event(event.type, event.payload)
                return result

        output = f"[{self.session_name}] {text}\n"
        output += "Response: Simulated\n"
        self._buffer = output

        return TerminalResponse(output=output, exit_code=0)

    async def read_output(self, timeout: float = 0.5) -> str:
        output = self._buffer
        self._buffer = ""
        return output

    def add_handler(self, pattern: str, handler: callable) -> None:
        """Add a custom handler for matching patterns."""
        self._custom_handlers[pattern] = handler


class MultiTerminalTestHarness:
    """Test harness for running multiple terminal apps simultaneously.

    This harness allows testing:
    - Multiple concurrent sessions
    - Cross-session communication
    - Event observation across sessions
    - Session coordination
    """

    def __init__(self, kernel):
        self.kernel = kernel
        self._terminals: dict[str, FakeTerminalApp] = {}

    async def create_terminal(self, terminal_type: str, session_name: str) -> FakeTerminalApp:
        """Create and register a terminal session."""
        terminal_map = {
            "claude": FakeClaudeCode(session_name),
            "codex": FakeCodex(session_name),
            "opencode": FakeOpenCode(session_name),
            "generic": FakeGenericTerminal(session_name),
        }

        if terminal_type not in terminal_map:
            raise ValueError(f"Unknown terminal type: {terminal_type}")

        terminal = terminal_map[terminal_type]
        self._terminals[session_name] = terminal

        # Create session in kernel
        await self.kernel.dispatch(f"session create {session_name}")
        await terminal.start()

        return terminal

    async def send_to_terminal(self, session_name: str, text: str) -> TerminalResponse:
        """Send text to a terminal session."""
        terminal = self._terminals.get(session_name)
        if not terminal:
            raise ValueError(f"Terminal not found: {session_name}")

        # Send via kernel
        result = await self.kernel.dispatch(f"session send-input {session_name} {text} --newline")

        # Read response
        response = await terminal.read_output()

        return TerminalResponse(output=response, exit_code=result.exit_code, events=terminal.events)

    async def read_terminal(self, session_name: str, timeout: float = 0.5) -> str:
        """Read output from a terminal session."""
        terminal = self._terminals.get(session_name)
        if not terminal:
            raise ValueError(f"Terminal not found: {session_name}")
        return await terminal.read_output(timeout)

    async def close_terminal(self, session_name: str) -> None:
        """Close a terminal session."""
        terminal = self._terminals.pop(session_name, None)
        if terminal:
            await terminal.stop()
        await self.kernel.dispatch(f"session kill {session_name}")

    async def close_all(self) -> None:
        """Close all terminal sessions."""
        for session_name in list(self._terminals.keys()):
            await self.close_terminal(session_name)

    def get_events(self, session_name: str | None = None) -> list[TerminalEvent]:
        """Get events from terminals."""
        if session_name:
            terminal = self._terminals.get(session_name)
            return terminal.events if terminal else []
        return [e for t in self._terminals.values() for e in t.events]
