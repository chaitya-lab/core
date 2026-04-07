# Usage

This guide covers the current CLI surface and the most useful workflows.

## Install

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

## Basic CLI

Show help:

```bash
chaitya --help
```

Show version:

```bash
chaitya --version
```

Use an in-memory database:

```bash
chaitya --memory info
```

Use a custom database path:

```bash
chaitya --db ./chaitya.db info
```

## Inspect The System

```bash
chaitya info
chaitya info shell
chaitya registry list
chaitya registry info file
chaitya registry validate shell
chaitya registry reload
```

## Sessions

Sessions are persistent named terminal environments.

Create sessions:

```bash
chaitya session create demo
chaitya session create python-dev --template python-dev
```

List sessions:

```bash
chaitya session list
```

Check session status:

```bash
chaitya session status demo
```

Send input:

```bash
chaitya session send demo --text "echo hello"
chaitya session send demo --newline
```

Use keys for simple control input:

```bash
chaitya session send demo --key enter
chaitya session send demo --key tab
```

Read recent output:

```bash
chaitya session output demo --idle-timeout 0.4
```

Manage environment variables:

```bash
chaitya session set-env demo --key MODE --value dev
chaitya session unset-env demo --key MODE
```

Control whether adapter execution is allowed in a session:

```bash
chaitya session exec status demo
chaitya session exec readonly demo
chaitya session exec enable demo
```

Signal or kill a session:

```bash
chaitya session signal demo SIGINT
chaitya session kill demo
```

## Events

Query recent events:

```bash
chaitya watch --limit 20
chaitya watch --session demo --limit 20
chaitya watch --on session_created --limit 20
chaitya watch --exit-after 3
```

Search event history:

```bash
chaitya watch --search demo --limit 20
```

Stream live events:

```bash
chaitya watch --live --session demo
chaitya watch --live --on input_requested --timeout 30
chaitya watch --live --session demo --exit-after 5
```

## Suspended Input

Some adapters can pause and request structured input.

Trigger an example request:

```bash
chaitya test ask
```

List pending requests:

```bash
chaitya input list
```

Respond to a request:

```bash
chaitya input respond <request_id> Alice
```

## L0 Ingest

Create input streams from various sources:

```bash
chaitya input --text "hello world"
chaitya input --file data.csv
chaitya input --file a.txt --file b.txt
chaitya input --file a.txt --file b.txt --merge concat
chaitya input --file a.txt --file b.txt --merge lines
chaitya input --clipboard
```

Use with pipelines:

```bash
chaitya input --file data.csv | chaitya shell run --command "grep pattern"
```

## First-Party Adapters

Repository adapter layout:

- `adaptors/core/`: `file`, `shell`, `route`, `process`, `registry`
- `adaptors/community/`: `browser`, `browser2`, `config`, `desktop`, `gui`, `test`, `watchdog`

### File

```bash
chaitya file read --path README.md
chaitya file write --path notes.txt --text "hello"
```

If the write would overwrite an existing file or target a dangerous path, add confirmation:

```bash
chaitya file write --path notes.txt --text "replace" --confirm
```

### Shell

```bash
chaitya shell run --command "pwd"
chaitya shell run --command "git status --short"
```

The shell adapter uses the platform shell:

- Unix-like systems: `$SHELL -lc`
- Windows: PowerShell-family shell, then `cmd.exe` fallback

### Process

```bash
chaitya process list
chaitya process tree
chaitya process info --pid 1234
chaitya process children --pid 1234
chaitya process signal --pid 1234 --signal TERM
chaitya process kill --pid 1234
```

### Route

`route` is most useful inside pipelines:

```bash
chaitya file read --path README.md | chaitya route --if-pattern "Chaitya"
chaitya shell run --command "make test" | chaitya route --if-exit 0
```

Trigger an action when a condition matches:

```bash
chaitya watch --live --session demo | chaitya route --if-pattern "ERROR" --do "session send alert --text notify --newline"
```

### Test

```bash
chaitya test hello
chaitya test ping
chaitya test echo --message "hi"
chaitya test ask
chaitya test confirm
chaitya test emit --name custom --payload "{\"ok\":true}"
```

### Config

```bash
chaitya config get session.backend
chaitya config get llm.model
chaitya config set browser.headless true
chaitya config list
chaitya config list browser
chaitya config paths
```

## Pipelines

Chaitya supports command chaining through the pipeline orchestrator.

Examples:

```bash
chaitya file read --path README.md | chaitya shell run --command "grep Kernel"
chaitya shell run --command "printf 'ok\n'" | chaitya route --if-pattern ok
```

## Configuration

Default config path:

```text
~/.chaitya/core.yaml
```

Default database path:

```text
~/.chaitya/chaitya.db
```

Default templates path:

```text
~/.chaitya/templates/
```

Example configuration:

```yaml
kernel:
  cli_name: chaitya
  debug_log: ~/.chaitya/logs/kernel.log
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

Useful environment variables:

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

`CHAITYA_ADAPTER_PATHS` uses the platform path separator:

- `:` on macOS/Linux
- `;` on Windows

## Running Tests

Full suite:

```bash
pytest tests -q
```

tmux or psmux integration tests:

macOS/Linux:

```bash
CHAITYA_RUN_TMUX_TESTS=1 pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```

Windows PowerShell:

```powershell
$env:CHAITYA_RUN_TMUX_TESTS = "1"
pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```
