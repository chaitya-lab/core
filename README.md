# Chaitya Core

Chaitya Core is a small automation kernel built around four ideas:

- commands are dispatched through a single CLI
- long-running work happens inside named sessions
- adapters provide capabilities
- events make automation observable and composable

The core is intentionally narrow. It owns boot, dispatch, sessions, events, pipeline execution, adapter discovery, and persistence. Everything else should live in adapters.

## Status

`0.1.0a1` alpha. The design is stable enough to build against, but interfaces may still change.

## What Exists Today

Chaitya Core currently provides:

- a `chaitya` CLI
- a `Kernel` orchestrator
- a SQLite-backed store and event bus
- persistent named sessions through `tmux` on Unix-like systems and `psmux` on Windows
- a pipeline that parses and runs chained commands
- adapter discovery from Python entry points and local workspaces
- an SDK package for adapter authors

Built-in kernel commands:

- `info`
- `session`
- `input`
- `output`
- `watch`
- `registry`

First-party adapters available in this repository:

- `file`
- `shell`
- `route`
- `process`
- `test`

Additional adapters in the workspace:

- `browser`
- `browser2`
- `desktop`
- `gui`

## Mental Model

Think of the system in layers:

1. The CLI receives a command expression.
2. The kernel parses it and routes each stage.
3. A built-in command or adapter runs.
4. Output is normalized by the pipeline.
5. Events and session state are persisted.

The kernel routes. Adapters do the work.

## Repository Map

```text
src/chaitya/core/          Kernel implementation
sdk/src/chaitya_sdk/       Public SDK for adapter authors
adaptors/core/             First-party adapters used by the repo
adaptors/community/        Experimental and community adapters
docs/                      User and contributor documentation
tests/                     Unit and integration tests
```

## Requirements

- Python 3.11+
- `tmux` for persistent sessions on macOS/Linux
- `psmux` for persistent sessions on Windows

With `session.backend: auto`, Chaitya selects `tmux` on Unix-like systems and `psmux` on Windows.

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

For Windows session support, make sure `psmux` is installed and available on `PATH`. See [Windows Support](docs/WINDOWS_SUPPORT.md).

## Quick Reference

```bash
chaitya info
chaitya registry list
chaitya session create demo
chaitya session send demo --text "echo hello" --newline
chaitya session output demo
```

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

### Use the command pipeline

```bash
chaitya file read --path README.md | chaitya route --if-pattern "Chaitya"
```

### Watch the event log

```bash
chaitya watch --limit 20
chaitya watch --session demo --limit 20
chaitya watch --live --session demo
```

### Handle suspended input

```bash
chaitya test ask
chaitya input list
chaitya input respond <request_id> Alice
```

## Configuration

By default the kernel reads configuration from `~/.chaitya/core.yaml` and stores state in `~/.chaitya/chaitya.db`.

Important environment variables:

- `CHAITYA_CLI_NAME`
- `CHAITYA_DB_PATH`
- `CHAITYA_SESSION_BACKEND`
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

adapters_config_dir: ~/.chaitya/adapters
adapter_search_paths:
  - /abs/path/to/my-adapters
```

## Documentation

- [Usage](docs/usage.md)
- [Architecture](docs/architecture.md)
- [Adapter Development](docs/adapters-dev.md)
- [Windows Support](docs/WINDOWS_SUPPORT.md)
- [SDK Guide](sdk/README.md)
- [Contributing](CONTRIBUTING.md)

## Contributing

The project is aiming for a small, understandable core. If you add capability, prefer doing it in an adapter unless the change clearly belongs to the kernel boundary itself.

Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
