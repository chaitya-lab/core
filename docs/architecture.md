# Architecture

This document describes the public architecture that exists in the repository today.

## Design Goal

Chaitya Core is a small automation kernel. It is responsible for:

1. boot and shutdown
2. command dispatch
3. session lifecycle
4. event routing
5. pipeline execution
6. adapter discovery and loading
7. persistence

Anything outside those boundaries should usually be implemented as an adapter.

## Main Runtime Pieces

### `Kernel`

`src/chaitya/core/kernel.py` composes the runtime:

- opens the store and event bus
- discovers and loads adapters
- restores persisted sessions
- dispatches kernel commands and adapter commands
- shuts everything down cleanly

The kernel directly handles six commands:

- `info`
- `session`
- `input`
- `output`
- `watch`
- `registry`

Everything else is routed to adapters.

### `SessionManager`

`src/chaitya/core/session_manager.py` manages named sessions:

- create, attach, detach, signal, and kill sessions
- send input and collect output
- persist session metadata
- restore sessions on boot
- mark sessions `stuck` or `dead`
- apply template-driven settings such as startup commands and health checks

Session states used by the runtime:

- `idle`
- `busy`
- `waiting`
- `stuck`
- `dead`

### Session Backends

Sessions run behind a backend protocol:

- `tmux` on non-Windows systems
- `psmux` on Windows

With `session.backend: auto`, the kernel picks the platform-appropriate default.

### `SqliteStore`

`src/chaitya/core/store.py` persists:

- session records
- event history
- pending suspended-input state

SQLite is the only built-in store today.

### `SqliteEventBus`

`src/chaitya/core/event_bus.py` provides:

- event emission
- live subscriptions
- event history queries

The current event bus implementation is also SQLite-backed.

### `PipelineOrchestrator`

`src/chaitya/core/pipeline.py` handles:

- parsing command chains
- passing input streams between stages
- running handlers
- L2 output normalization

This is where pipe expressions are interpreted.

### `AdapterRegistry`

`src/chaitya/core/registry.py` discovers and validates adapters from:

- Python entry points in `chaitya.adapters`
- `adaptors/core/`
- `adaptors/community/`
- configured filesystem search paths

It also handles dependency ordering, enable/disable filtering, validation, and runtime reload.

## Command Flow

At a high level:

1. The CLI builds a command expression.
2. The kernel parses the expression or pipeline.
3. The kernel or an adapter handles each stage.
4. The pipeline normalizes output into `CommandOutput`.
5. Events and session state changes are persisted.

For:

```bash
chaitya session create demo
```

the flow is:

1. CLI starts the kernel.
2. The kernel dispatches `session`.
3. `SessionManager.create()` is called.
4. The backend creates the terminal session.
5. The store and event bus record the change.

## Adapter Model

Adapters are packages that expose a contract and a handler.

Supported discovery paths:

- installed Python packages via the `chaitya.adapters` entry-point group
- local workspaces under configured search paths
- the repository's own `adaptors/core/` and `adaptors/community/` folders

### Adapter Categories In This Repository

System adapters in `adaptors/core/`:

- `file`
- `shell`
- `route`
- `process`
- `registry`

Optional first-party/community adapters in `adaptors/community/`:

- `browser`
- `browser2`
- `config`
- `desktop`
- `gui`
- `test`
- `watchdog`

The category matters operationally: core/system adapters are part of the normal kernel surface, while community adapters are optional extensions.

## SDK Boundary

The SDK in `sdk/src/chaitya_sdk/` is the public adapter boundary.

Adapter code should use the SDK for:

- `@adapter`
- stream and context types
- permission checks
- event helpers
- suspension types
- contract metadata
- `SessionRunner` for session-backed adapters

Adapters should not import from `chaitya.core`.

## Interaction Models

There are three important interactive patterns.

### Session Interaction

This is PTY-style interaction with a named shell session:

- `session create`
- `session send`
- `session send-input`
- `session output`
- `session view`
- `session signal`
- `session set-env`
- `session unset-env`
- `session exec enable|disable|readonly|status`
- `session kill`

Use this when a real shell process must stay alive across commands.

### Adapter Suspension

An adapter can request structured input by raising `Suspension(InputSpec(...))`.

Flow:

1. the adapter requests input
2. the kernel marks the session as waiting and emits `input_requested`
3. the user checks waiting sessions with `input list`
4. the user resumes the session with `session send-input <session> <value>`
5. the adapter resumes and the kernel emits `input_response`

In current builds, suspended adapter commands are resumed through the session layer rather than a separate kernel-managed pending-input queue.

### L0 Ingest

The `input` command also acts as the ingestion layer for pipelines. It can create streams from:

- `--text`
- `--file`
- repeated `--file` with `--merge concat|lines`
- `--clipboard`

That gives the pipeline a standard way to start from text, files, or clipboard content.

## Sessions, Templates, And Health

Session templates are loaded from `templates_dir` and can define:

- `identity.env_vars`
- `identity.working_dir`
- `identity.browser_profile`
- `startup_command`
- `auto_restart_on_kernel_start`
- `git_worktree`
- `health_check_interval`

When configured, the session manager can emit:

- `session_stuck`
- `session_health_check`

These events make long-running session behavior observable without putting session policy inside adapters.

## Events

Events are part of normal operation, not an add-on.

Examples include:

- `kernel_started`
- `kernel_shutting_down`
- `session_created`
- `session_state_changed`
- `session_stuck`
- `input_requested`
- `input_response`
- adapter-defined events

The `watch` command can query recent history or stream live events.

## Configuration Surface

Configuration is loaded in this order:

1. built-in defaults
2. `core.yaml`
3. environment variables

Important config areas:

- `kernel`
- `store`
- `event_bus`
- `session`
- `templates_dir`
- `adapters_config_dir`
- `adapter_search_paths`
- `enabled_adapters`
- `disabled_adapters`

The `config` adapter provides a user-facing way to inspect core config and persist adapter-specific config under `~/.chaitya/adapters/<name>.yaml`.

## Current Boundaries

The current architecture is shaped by a few rules:

- keep the kernel small
- route extension work through adapters
- use the SDK as the adapter boundary
- treat sessions as a first-class primitive
- keep persistence and events in the core runtime

If a proposed feature does not need one of those kernel boundaries, it probably belongs in an adapter.
