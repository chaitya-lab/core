# Contributing

This repository is trying to produce a small, dependable core. Contributions should improve clarity as much as capability.

## Principles

- keep the kernel small
- prefer adapters over core expansion
- use `chaitya_sdk` as the adapter boundary
- preserve predictable boot, dispatch, and shutdown behavior
- keep docs aligned with implementation

## Development Setup

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m pip install -e ./sdk
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e . -e ./sdk
```

## Repository Layout

```text
src/chaitya/core/          Kernel code
sdk/src/chaitya_sdk/       Public SDK
adaptors/core/             System adapters
adaptors/community/        Optional first-party and community adapters
docs/                      User and architecture docs
tests/                     Test suite
tests/test_harness/        Fake terminal apps for testing
```

## Working Rules

### Kernel Changes

Kernel changes should usually be limited to:

- lifecycle and boot logic
- dispatch and pipeline behavior
- session management
- template loading and session lifecycle policies
- event bus and persistence
- adapter loading and validation
- public configuration

If a feature can reasonably live in an adapter, it should.

### Adapter Changes

Adapters should:

- import from `chaitya_sdk`
- declare a clear contract
- keep permissions narrow
- include examples in command metadata
- live in `adaptors/core/` only if core operation depends on them
- otherwise live in `adaptors/community/`

### Documentation

If behavior changes, update:

- `README.md`
- the relevant files in `docs/`
- `adaptors/README.md` if adapter layout or categories changed
- adapter or SDK docs when the public surface changes

Public docs should describe the implementation that exists in the repo today.

## Tests

Run the main suite:

```bash
pytest tests -q
```

Run test harness (fake terminal apps):

```bash
pytest tests/test_harness -v
```

The test harness provides fake terminal apps for testing core adapters:
- `FakeREPL` - ANSI colors, prompts, thinking blocks
- `FakeAPICLI` - Structured output, API-style commands
- `FakeMarkdownCLI` - Markdown formatting, badges, spinners
- `FakeShell` - Simple shell output
- `FakeGenericTerminal` - Custom handler support

Run fake web tests (browser automation):

```bash
pytest tests/test_web -v
```

The fake web test suite provides a local web server (port 18766) for testing browser adapters without external dependencies:

- `test_web_server.py` - FastAPI server with multiple test pages (home, search, dashboard, chat, login, etc.)
- Console capture - tracks console.log, console.warn, console.error from browser
- DOM event tracking - captures clicks, inputs, form submissions
- Useful for testing browser adapter commands: navigate, click, fill, evaluate, screenshot

Run session backend integration tests when changing session behavior:

macOS/Linux:

```bash
CHAITYA_RUN_TMUX_TESTS=1 pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```

Windows PowerShell:

```powershell
$env:CHAITYA_RUN_TMUX_TESTS = "1"
pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```

Add or update tests for every behavior change that affects:

- CLI behavior
- pipeline execution
- sessions
- templates or session recovery behavior
- adapter loading
- persistence
- cross-platform compatibility

## Pull Requests

A good change should make it easy to answer:

- what changed
- why it belongs in the kernel or adapter layer
- how it was tested
- whether there are platform-specific notes
- which public docs were updated

## Style

- keep changes focused
- prefer clear names over clever abstractions
- preserve cross-platform behavior where possible
- avoid expanding the core surface without a strong reason
