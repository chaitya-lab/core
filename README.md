# Chaitya Core

Chaitya Core is a small automation kernel with three main primitives:

- a single CLI for dispatch
- persistent named sessions for long-running work
- adapters for everything outside the kernel boundary

The kernel owns boot, dispatch, sessions, events, pipeline execution, adapter discovery, and persistence. Capabilities should usually be added as adapters, not by expanding the core.

## Status

`0.1.0a1` alpha. The project is usable, but public interfaces may still change.

## What Exists Today

Chaitya currently provides:

- a `chaitya` CLI
- a `Kernel` orchestrator
- a SQLite-backed store and event bus
- persistent session backends: `tmux` on non-Windows systems and `psmux` on Windows
- a pipeline for chained commands and normalized output
- adapter discovery from local workspaces and Python entry points
- an SDK for adapter authors

Kernel-dispatched commands (also available as core adapters in `adaptors/core/`):

- `info` - Show system, adapter, or kernel information
- `session` - Manage named running environments (sessions)
- `input` - L0 Ingest: provide input data to the pipeline
- `output` - L2 Present: format and filter command output
- `watch` - Observe events: query history or stream live
- `registry` - Adapter discovery and validation

First-party adapters in this repository:

- system adapters in `adaptors/core/`: `info`, `session`, `input`, `output`, `watch`, `file`, `shell`, `route`, `process`, `registry`
- community adapters in `adaptors/community/`: `browser`, `browser2`, `config`, `desktop`, `gui`, `test`

## Mental Model

1. The CLI receives a command expression.
2. The kernel parses the command or pipeline.
3. A kernel command or adapter runs.
4. Output is normalized by the pipeline.
5. Events and session state are persisted.

The kernel routes and records. Adapters do the actual work.

## Repository Map

```text
src/chaitya/core/          Kernel implementation
sdk/src/chaitya_sdk/       Public SDK for adapter authors
adaptors/core/             System adapters required for normal boot
adaptors/community/        Optional first-party/community adapters
docs/                      Public documentation
tests/                     Unit and integration tests
```

## Requirements

- Python 3.11+
- `tmux` for persistent sessions on macOS/Linux
- `psmux` for persistent sessions on Windows

With `session.backend: auto`, Chaitya selects `tmux` on non-Windows systems and `psmux` on Windows.

## Quick Start

### 1. Install from source

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -m pip install -e ./sdk
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e . -e ./sdk
```

If you want persistent Windows sessions, install `psmux` first. See [Windows Support](docs/WINDOWS_SUPPORT.md).

### 2. Check the kernel

```bash
chaitya info
chaitya registry list
```

### 3. Try a few commands

```bash
chaitya test hello
chaitya shell run --command "echo hello"
chaitya file read --path README.md
chaitya config paths
```

### 4. Create a persistent session

```bash
chaitya session create demo
chaitya session send-input demo "echo session-ok" --newline
chaitya session output demo
chaitya session view demo
```

## Common Workflows

### Run one-off commands

```bash
chaitya shell run --command "git status --short"
chaitya process list
```

### Use the pipeline

```bash
chaitya file read --path README.md | chaitya route --if-pattern "Chaitya"
chaitya input --text "ok" | chaitya shell run --command "cat"
```

### Work with config

```bash
chaitya config get session.backend
chaitya config set llm.model gpt-5
chaitya config set kernel.log_level debug
chaitya config list
chaitya config paths
```

### Watch the event log

```bash
chaitya watch --limit 20
chaitya watch --session demo --limit 20
chaitya watch --live --session demo --timeout 30
```

### Handle suspended input

```bash
chaitya test ask
chaitya input list
chaitya session send-input default Alice --newline
```

## Configuration

Chaitya loads configuration from a YAML file with this priority (highest first):

1. Environment variables (`CHAITYA_*`)
2. Config file (`~/.chaitya/core.yaml` by default)
3. Built-in defaults

If no config file exists at the default location, Chaitya uses built-in defaults. You don't need a config file to get started.

### Default Paths

| Path | Default |
|------|---------|
| Config | `~/.chaitya/core.yaml` |
| Database | `~/.chaitya/chaitya.db` |
| Templates | `~/.chaitya/templates/` |
| Adapter configs | `~/.chaitya/adapters/` |

### Using a Custom Config File

Override with `--config` flag:

```bash
chaitya --config /path/to/my/core.yaml info
```

Or environment variable `CHAITYA_CONFIG`:

```bash
CHAITYA_CONFIG=/path/to/my/core.yaml chaitya info
```

### Example Configuration

```yaml
kernel:
  cli_name: chaitya
  log_level: info

store:
  path: ~/.chaitya/chaitya.db

session:
  backend: auto
  stuck_threshold_seconds: 60

templates_dir: ~/.chaitya/templates
adapters_config_dir: ~/.chaitya/adapters
adapter_search_paths:
  - /abs/path/to/my-adapters

enabled_adapters:
  - session
  - browser
  - browser2

disabled_adapters:
  - browser2

adapter_options:
  browser:
    headless: true
    viewport_width: 1920
```

### Adapter Versioning

Adapters can declare the minimum core version they require:

```yaml
# In adapter's module.json or @adapter decorator
requires_core: ">=0.1.0"
```

Supported version specs:

| Spec | Meaning |
|------|---------|
| `>=0.1.0` | Minimum version 0.1.0 |
| `^0.1.0` | Compatible (same major version) |
| `~0.1.0` | Compatible (same minor version) |
| `0.1.0` | Exact version |

If an adapter requires a newer core version than what's installed, the kernel rejects it with a clear error.

### Programmatic Use

Use Chaitya Core as a library in your Python code:

```python
from chaitya.core import Kernel
from chaitya.core.config import load_config

# Load from default location (~/.chaitya/core.yaml)
config = load_config()
kernel = Kernel(config=config)

# Use a custom config file
config = load_config("/path/to/my/core.yaml")
kernel = Kernel(config=config)

# Or pass config dict directly
kernel = Kernel(config={"kernel": {"log_level": "debug"}})
```

The `Kernel` class provides:

- `run(command)` — Execute a command expression
- `adapters` — Registry of loaded adapters
- `store` — SQLite-backed persistence
- `event_bus` — Event streaming

## Documentation

- [Docs Index](docs/README.md)
- [Usage](docs/usage.md)
- [Architecture](docs/architecture.md)
- [Adapter Development](docs/adapters-dev.md)
- [Adapters Workspace](adaptors/README.md)
- [Windows Support](docs/WINDOWS_SUPPORT.md)
- [Acknowledgements](docs/ACKNOWLEDGEMENTS.md)
- [SDK Guide](sdk/README.md)
- [Contributing](CONTRIBUTING.md)

## Contributing

The project is trying to stay small and understandable. If a feature can live in an adapter, put it in an adapter.

Start with [Docs Index](docs/README.md) if you want a map of the docs, then read [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
