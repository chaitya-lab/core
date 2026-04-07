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
adaptors/core/             First-party adapters
adaptors/community/        Experimental and community adapters
docs/                      User and architecture docs
tests/                     Test suite
```

## Working Rules

### Kernel Changes

Kernel changes should usually be limited to:

- lifecycle and boot logic
- dispatch and pipeline behavior
- session management
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

### Documentation

If behavior changes, update:

- `README.md`
- the relevant files in `docs/`
- adapter or SDK docs when the public surface changes

## Tests

Run the main suite:

```bash
pytest tests -q
```

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
- adapter loading
- persistence
- cross-platform compatibility

## Pull Requests

A good change should make it easy to answer:

- what changed
- why it belongs in the kernel or adapter layer
- how it was tested
- whether there are platform-specific notes

## Style

- keep changes focused
- prefer clear names over clever abstractions
- preserve cross-platform behavior where possible
- avoid expanding the core surface without a strong reason
