# Contributing

## Development Setup

Chaitya Core currently targets Python 3.11+.

```bash
python3 -m pip install -e ".[dev]"
python3 -m pip install -e ./sdk
```

Run the test suite from the repository root:

```bash
python3 -m pytest tests -q
```

Run real tmux integration tests on macOS/Linux:

```bash
CHAITYA_RUN_TMUX_TESTS=1 python3 -m pytest tests/test_tmux_backend.py -q
```

If you are developing external adaptors from a local folder, point the kernel at that workspace with either:

```bash
export CHAITYA_ADAPTER_PATHS="/abs/path/to/my-adaptors"
```

or `core.yaml`:

```yaml
adapters_config_dir: "~/.chaitya/adapters"
adapter_search_paths:
  - "/abs/path/to/my-adaptors"
```

## Architecture Guardrails

- Keep the kernel small. New capabilities belong in adapters unless they are one of the seven kernel responsibilities in `prd.md`.
- Adapters should import only from `chaitya_sdk`, never from `chaitya.core`.
- Prefer protocol boundaries over direct coupling between subsystems.
- Preserve deterministic boot and shutdown behavior.
- Prefer adding capabilities through adaptors or config-driven discovery before changing the kernel itself.

## macOS Workflow

- The default macOS/Linux substrate is `TmuxSessionBackend`.
- `LocalProcessBackend` remains for fallback and focused unit tests.
- Changes in session behavior should include integration tests that exercise real `tmux` sessions instead of mocks.
- Before proposing tmux-backed features, verify behavior on macOS with actual subprocess or tmux execution.

## Quality Bar

- Add or update tests for every behavior change.
- Keep README and contributor docs aligned with the implementation.
- Prefer small, reviewable commits.

## Pull Requests

Include:

1. The problem statement.
2. The architectural constraint or PRD section affected.
3. Test evidence, including the exact command you ran.
4. Any platform-specific notes for macOS, Linux, or Windows.
