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

The kernel routes. Adapters act. The kernel has **zero built-in commands** — a freshly started kernel with no adapters is functional but capability-free.

## Core Tenets

- **Everything is a stream.** Data flows through L0 → L1 → L2.
- **Human-agent symmetry.** A human and an AI agent are indistinguishable at the kernel level.
- **Designed for small LLMs.** All info responses under 500 tokens, predictably structured.
- **Persistence across restarts.** Session state survives reboots.

## Quick Start

```bash
# Install from source
pip install -e ".[dev]"
pip install -e ./sdk

# Run the default suite
python3 -m pytest tests -q

# Run the real tmux integration suite on macOS/Linux
CHAITYA_RUN_TMUX_TESTS=1 python3 -m pytest tests/test_tmux_backend.py -q

# Run (no adapters installed = functional but empty)
chaitya info
```

Current status: macOS/Linux use a real `tmux` backend by default when `tmux` is available. `LocalProcessBackend` remains in-tree for fallback and focused unit testing.

## For Adapter Developers

See [Adapter Development Guide](docs/adapters-dev.md) for the complete contract specification, examples, and best practices.

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
| `SessionBackend` | tmux on macOS/Linux, local fallback elsewhere | Named session management |
| `EventBus` | SQLite | Pub/sub event routing |
| `Store` | SQLite | Session records + event log |
| `PipelineOrchestrator` | Built-in | L0→L1→L2 data flow |
| `AdapterLoader` | Entry points | Adapter discovery & loading |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and contribution guidelines. Adapter authors should also read [docs/adapters-dev.md](docs/adapters-dev.md). Repository direction for future packages lives in [ROADMAP.md](ROADMAP.md) and [adaptors/README.md](adaptors/README.md).

## License

[MIT](LICENSE)
