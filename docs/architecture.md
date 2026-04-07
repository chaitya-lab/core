# Architecture

This document describes the code that exists in the repository today.

## Design Goal

Chaitya Core is a small kernel for automation systems. It is responsible for:

1. boot and shutdown
2. command dispatch
3. session lifecycle
4. event routing
5. pipeline execution
6. adapter discovery and loading
7. persistence

Anything outside those responsibilities should usually be implemented as an adapter.

## Main Runtime Pieces

### `Kernel`

`src/chaitya/core/kernel.py` composes the system and owns the runtime lifecycle:

- open store and event bus
- discover and load adapters
- restore sessions
- register command handlers
- dispatch command expressions
- shut everything down cleanly

Built-in kernel commands:

- `info`
- `session`
- `input`
- `output`
- `watch`
- `registry`

Everything else is routed to adapters.

### `SessionManager`

`src/chaitya/core/session_manager.py` manages named sessions:

- create, attach, detach, kill, and signal sessions
- send input and read output
- persist session metadata
- restore sessions after boot
- detect stuck or dead sessions

Session states used by the system:

- `idle`
- `busy`
- `waiting`
- `stuck`
- `dead`

### Session Backends

The session backend is swappable behind a common protocol.

- `tmux` is the default path on Unix-like systems
- `psmux` is the default path on Windows

Both backends expose the same session operations to the rest of the kernel.

### `SqliteStore`

`src/chaitya/core/store.py` persists:

- session records
- event history
- pending suspended-input state

SQLite is the current built-in storage backend.

### `SqliteEventBus`

`src/chaitya/core/event_bus.py` provides:

- event emission
- live subscriptions
- event history queries

The event bus is also backed by SQLite today.

### `PipelineOrchestrator`

`src/chaitya/core/pipeline.py` is responsible for:

- parsing command chains
- wiring stages together
- executing handlers
- normalizing output in the L2 presentation step

The pipeline is where command expressions such as pipes are interpreted.

### `AdapterRegistry`

`src/chaitya/core/registry.py` discovers and validates adapters from:

- Python entry points in `chaitya.adapters`
- configured filesystem search paths
- local development workspaces

The registry also handles dependency ordering and validation.

## Command Flow

At a high level:

1. The CLI builds a command expression.
2. The kernel asks the pipeline to parse it.
3. The pipeline invokes built-in handlers or adapter handlers.
4. Output is normalized into a `CommandOutput`.
5. Events and session state changes are persisted.

For a command such as:

```bash
chaitya session create demo
```

the flow is:

1. CLI starts the kernel
2. kernel dispatches `session`
3. session handler calls `SessionManager.create()`
4. the backend creates the real terminal session
5. the store and event bus record the result

## Adapter Model

Adapters are packages that expose a contract and a handler.

The repository supports two common paths:

- installed Python packages using the `chaitya.adapters` entry-point group
- local adapter workspaces discovered from configured paths

Adapter authors should import from `chaitya_sdk`, not from `chaitya.core`.

## SDK Boundary

The SDK in `sdk/src/chaitya_sdk/` is the public surface for adapter development.

It exposes:

- `@adapter`
- stream and context types
- event helpers
- permission checks
- suspension types
- contract and command definition types
- `SessionRunner` for adapters that need session-backed execution

This keeps adapter code isolated from kernel internals.

## Interactive Patterns

There are two distinct interaction models.

### Session Interaction

This is PTY-style interaction with a named shell session:

- `session create`
- `session send`
- `session output`
- `session signal`
- `session set-env`
- `session unset-env`
- `session kill`

Use this when a real terminal process must stay alive across commands.

### Adapter Suspension

An adapter can pause and request input by raising `Suspension(InputSpec(...))`.

Flow:

1. adapter requests input
2. kernel stores the pending request and emits `input_requested`
3. user or client responds with `input respond <request_id> <value>`
4. kernel resumes the adapter and emits `input_response`

Use this when the adapter needs structured input rather than terminal input.

## Events

Events make the system observable and automatable.

Examples include:

- `kernel_started`
- `kernel_shutting_down`
- `session_created`
- `session_state_changed`
- `input_requested`
- `input_response`
- adapter-defined events

The `watch` command queries history or streams events live.

## First-Party Adapters In This Repository

Core workspace adapters:

- `file`: read and write local text files
- `shell`: execute shell commands
- `route`: filter pipeline output and emit actions
- `process`: inspect and control OS processes
- `test`: exercise kernel features during development

Additional workspace adapters:

- `browser`
- `browser2`
- `desktop`
- `gui`

## Configuration Surface

Configuration is loaded in this order:

1. built-in defaults
2. `core.yaml`
3. environment variables

Important areas:

- `kernel`
- `store`
- `event_bus`
- `session`
- adapter search paths and enable/disable lists

## Current Boundaries

A few principles define the current architecture:

- the kernel should stay small
- adapter code should go through the SDK
- sessions are a first-class primitive
- persistence is built in, not bolted on
- events are part of normal operation, not an afterthought

If a proposed feature does not need one of those kernel boundaries, it probably belongs in an adapter.
