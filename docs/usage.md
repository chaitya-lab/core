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
```

## Sessions

Sessions are persistent named terminal environments.

Create a session:

```bash
chaitya session create demo
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
```

Search event history:

```bash
chaitya watch --search demo --limit 20
```

Stream live events:

```bash
chaitya watch --live --session demo
chaitya watch --live --on input_requested --timeout 30
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

## First-Party Adapters

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
- Windows: PowerShell

### Process

```bash
chaitya process list
chaitya process tree
chaitya process info --pid 1234
chaitya process children --pid 1234
chaitya process signal --pid 1234 --sig TERM
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
chaitya test emit --name custom --payload "{\"ok\":true}"
```

### Config

```bash
chaitya config list                 # List all config
chaitya config get session.backend  # Get a config value
chaitya config set browser.headless true  # Set adapter config (persisted)
chaitya config paths                # Show config directories
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

adapters_config_dir: ~/.chaitya/adapters
adapter_search_paths:
  - /abs/path/to/my-adapters
```

Useful environment variables:

- `CHAITYA_CLI_NAME`
- `CHAITYA_DB_PATH`
- `CHAITYA_SESSION_BACKEND`
- `CHAITYA_ADAPTERS_CONFIG_DIR`
- `CHAITYA_ADAPTER_PATHS`
- `CHAITYA_ENABLED_ADAPTERS`
- `CHAITYA_DISABLED_ADAPTERS`
- `CHAITYA_DEBUG_LOG`
- `CHAITYA_LOG_LEVEL`

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
