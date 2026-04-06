# Architecture

## Goal

Chaitya Core is a small event-driven kernel for automation systems.

The kernel should stay responsible for:

1. boot and shutdown
2. command dispatch
3. session management
4. event routing
5. pipeline execution
6. adaptor discovery and loading
7. persistence

Everything else should prefer to live in adaptors.

## Main Runtime Pieces

- `Kernel`: top-level orchestrator (handles 6 built-in commands: info, session, input, output, watch, registry)
- `SessionManager`: session lifecycle and state
- `SessionBackend`: concrete substrate — tmux on macOS/Linux, psmux on Windows. Both implement the same Protocol — the kernel session management is identical.
- `SqliteEventBus`: event persistence and pub/sub (stream events like stdout_chunk NOT persisted to DB)
- `SqliteStore`: session records, events, and pending suspension requests
- `PipelineOrchestrator`: command parsing and execution flow
- `AdapterRegistry`: discovery from entry points and configured filesystem paths

## Adaptor Model

Adaptors are discovered from:

- installed Python entry points in `chaitya.adapters`
- workspace folders under `adaptors/`
- configured filesystem paths from `adapters_config_dir` and `adapter_search_paths`

Adaptors should import only from `chaitya_sdk`.

## Interactive Flows

There are two interaction paths:

### Session Interaction

PTY-style control over a running subprocess in a named session.

Commands:

- `session send` — send text, newline, or key to session
- `session output` — read session terminal output
- `session signal` — send signals (SIGTERM, SIGINT, etc.)
- `session set-env` / `session unset-env` — manage environment variables

Use this for tools that prompt on stdin/stdout inside tmux.

### Adaptor Suspension

Kernel-managed logical input for adaptor handlers.

Flow:

1. adaptor needs missing input → suspends
2. kernel emits `input_requested`
3. user responds with `input respond <request_id> <value>`
4. kernel resumes adaptor with injected value

## Streaming Pipeline

The pipeline supports async streaming handlers. `watch --live` streams events through the pipeline:

```bash
chaitya watch --live --session my-session | \
  chaitya route --if-pattern "ERROR" --do "session send alert --text 'Error!'"
```

`route --do` triggers actions when patterns match.

## System Adaptors

Core adapters shipped with the kernel:

- `file` — read/write files
- `shell` — run shell commands
- `route` — filter content, trigger actions
- `process` — system process management
- `test` — testing utilities

Community adapters:

- `browser`, `browser2` — browser automation
- `desktop` — screenshot, clipboard, accessibility tree
- `gui` — mouse/keyboard control
