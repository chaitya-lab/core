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

Kernel-dispatched commands:

- `info`
- `session`
- `input`
- `output`
- `watch`
- `registry`

First-party adapters in this repository:

- system adapters in `adaptors/core/`: `file`, `shell`, `route`, `process`, `registry`
- community adapters in `adaptors/community/`: `browser`, `browser2`, `config`, `desktop`, `gui`, `test`, `watchdog`

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
chaitya session send demo --text "echo session-ok" --newline
chaitya session output demo
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
chaitya input respond <request_id> Alice
```

## Configuration

By default Chaitya uses:

- config: `~/.chaitya/core.yaml`
- database: `~/.chaitya/chaitya.db`
- templates: `~/.chaitya/templates/`
- adapter configs: `~/.chaitya/adapters/`

Important environment variables:

- `CHAITYA_CLI_NAME`
- `CHAITYA_DB_PATH`
- `CHAITYA_SESSION_BACKEND`
- `CHAITYA_TEMPLATES_DIR`
- `CHAITYA_ADAPTERS_CONFIG_DIR`
- `CHAITYA_ADAPTER_PATHS`
- `CHAITYA_ENABLED_ADAPTERS`
- `CHAITYA_DISABLED_ADAPTERS`
- `CHAITYA_DEBUG_LOG`
- `CHAITYA_LOG_LEVEL`

Example `core.yaml`:

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
disabled_adapters:
  - browser2
```

## Documentation

- [Usage](docs/usage.md)
- [Architecture](docs/architecture.md)
- [Adapter Development](docs/adapters-dev.md)
- [Windows Support](docs/WINDOWS_SUPPORT.md)
- [SDK Guide](sdk/README.md)
- [Contributing](CONTRIBUTING.md)

## Contributing

The project is trying to stay small and understandable. If a feature can live in an adapter, put it in an adapter.

Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
