# Chaitya Core

**Event-driven microkernel for personal automation.**

Chaitya Core provides stable primitives — command dispatch, session management, event bus, pipeline mechanics, adapter registry, and persistent store — and nothing else. Every capability is an independently installable adapter.

> **Status:** Alpha — under active development. APIs will change.

## Architecture

The kernel has exactly seven responsibilities:

1. **Boot Sequence** — deterministic startup with clear failure modes
2. **Command Dispatch** — routes `chaitya <adapter> <subcommand>` to registered adapters
3. **Session Management** — named running environments behind a `SessionBackend` protocol
4. **Event Bus** — JSON-line event routing between adapters (SQLite-backed pub/sub)
5. **Pipeline Mechanics** — L0 (ingest) → L1 (execute) → L2 (present) data flow
6. **Adapter Registry** — discovers, validates, loads Python adapter packages
7. **Persistent Store** — session records + event log in one SQLite database

The kernel routes. Adapters act. The kernel has **six built-in commands**: `info`, `session`, `input`, `output`, `watch`, and `registry`. All other commands come from adapters.

`registry` is built into the kernel (no pip install needed): `registry list`, `registry info`, `registry validate` work out of the box.

## Core Tenets

- **Everything is a stream.** Data flows through L0 → L1 → L2.
- **Human-agent symmetry.** A human and an AI agent are indistinguishable at the kernel level.
- **Designed for small LLMs.** All info responses under 500 tokens, predictably structured.
- **Persistence across restarts.** Session state survives reboots.

## Quick Start

```bash
# Install from source (macOS/Linux)
pip install -e ".[dev]"
pip install -e ./sdk

# Install from source (Windows/PowerShell)
pip install -e . -e ./sdk

# Run the default suite
python3 -m pytest tests -q

# Run the real tmux/psmux integration suite
CHAITYA_RUN_TMUX_TESTS=1 python3 -m pytest tests/test_tmux_backend.py -q

# Run (no adapters installed = functional but empty)
chaitya info
```

Current status:
- macOS/Linux: uses `tmux` backend by default
- Windows: uses `psmux` backend by default (auto-detected)
- Sessions are persistent named environments with PTY support

The repository ships first-party workspace adaptors for `file`, `shell`, `route`, `process`, and `test`. They are discovered directly from `adaptors/core/` during development, so the kernel can boot and execute real adaptor commands from the repo without extra packaging steps.

External projects can add custom adaptors in two ways:

- install Python packages that expose the `chaitya.adapters` entry point
- point the kernel at filesystem workspaces via `adapters_config_dir` or `adapter_search_paths`

Filesystem adaptor workspaces use this shape:

```text
my-adaptors/
  mytool/
    src/chaitya_adapter_mytool/__init__.py
```

## For Adapter Developers

See [Adapter Development Guide](docs/adapters-dev.md) for the complete contract specification, examples, and best practices.

Core reference docs:

- [Architecture](docs/architecture.md)
- [Usage](docs/usage.md)

```bash
# Install the SDK
pip install chaitya-sdk
```

```python
from chaitya_sdk import adapter, ChaityaStream, SessionContext

@adapter(name="my-tool", subcommand="run")
def my_tool_run(stream: ChaityaStream, ctx: SessionContext):
    # Your logic here
    return b"result"
```

## Project Structure

```
src/chaitya/core/     # The kernel
  types.py            # All data types and enums
  protocols.py        # 5 extension-point protocols
  event_bus.py        # SQLite-backed event bus
  store.py            # Persistent store
  pipeline.py         # Pipeline orchestrator
  session.py          # Session management
  registry.py         # Adapter registry & loader
  kernel.py           # Main kernel orchestrator
  config.py           # Configuration
  cli.py              # CLI entry point
sdk/                  # chaitya-sdk package (separate pip install)
tests/                # Test suite
docs/                 # Documentation
adaptors/             # Future first-party and community adaptor workspace
```

## Five Extension Points

All swappable via `core.yaml`:

| Protocol | Default | Purpose |
|---|---|---|
| `SessionBackend` | tmux (macOS/Linux), psmux (Windows), local fallback | Named session management |
| `EventBus` | SQLite | Pub/sub event routing |
| `Store` | SQLite | Session records + event log |
| `PipelineOrchestrator` | Built-in | L0→L1→L2 data flow |
| `AdapterLoader` | Entry points | Adapter discovery & loading |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and contribution guidelines. Adapter authors should also read [docs/adapters-dev.md](docs/adapters-dev.md). Repository direction for future packages lives in [ROADMAP.md](ROADMAP.md) and [adaptors/README.md](adaptors/README.md).

## Interactive Flow

The kernel now supports two interactive patterns:

- tmux-backed session control with `session send-input`, `session output`, `session signal`, `session set-env`, and `session unset-env`
- adaptor suspension/resume with `input list` and `input respond <request_id> <value>`

That means a subsystem can pause for input, emit an `input_requested` event, and continue later from the main terminal or another client.

## License

[MIT](LICENSE)
