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

- `Kernel`: top-level orchestrator
- `SessionManager`: session lifecycle and state
- `SessionBackend`: concrete substrate, `tmux` on macOS/Linux and local fallback
- `SqliteEventBus`: event persistence and pub/sub
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

There are two different interaction paths:

### Session Interaction

This is PTY-style control over a running subprocess in a named session.

Commands:

- `session send-input`
- `session output`
- `session signal`
- `session set-env`
- `session unset-env`

Use this for tools that already know how to prompt on stdin/stdout inside tmux.

### Adaptor Suspension

This is kernel-managed logical input for adaptor handlers.

Flow:

1. adaptor needs missing input
2. adaptor suspends
3. kernel emits `input_requested`
4. user or client responds with `input respond <request_id> <value>`
5. kernel emits `input_response`
6. adaptor resumes with the injected value

Use this for structured approval and headless human-in-the-loop flows.

## Stability Notes

The current kernel is a usable base for other projects.

Still intentionally evolving:

- richer live watch streaming
- stronger runtime permission enforcement
- more first-party adaptors such as `process` and `route`
- broader persistence and restart semantics for long-lived workflows
