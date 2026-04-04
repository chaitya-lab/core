# Changelog

## Unreleased

### Registry Built-In (breaking change)
- **Registry is now a built-in kernel command** — no `chaitya-adapter-registry` pip package needed.
  `registry list`, `registry info`, `registry validate` work out of the box.
- Removed `BootstrapLoader` class from registry module.
- `Kernel` no longer requires `registry_adapter_pkg` parameter.

### SQLite Event Bus
- **Batched writes**: events are buffered in memory and flushed to SQLite every 0.5s or 100 events (was per-event commit).
- **Stream events excluded**: `stdout_chunk`, `stderr_chunk`, `progress_update`, `file_changed` are delivered to subscribers but NOT written to SQLite, preventing lock contention under high load.
- `history()` and `event_count()` flush pending events before reading.

### Tmux Edge Cases
- Stuck monitor now verifies backend pane exists before marking sessions STUCK (marks DEAD if pane gone).

### Request/Response Timeout
- New `_input_timeout_loop` background task expires pending input requests after `input_timeout_seconds` (default 300s).
- Emits `input_timeout` event when requests expire.

### Windows Support
- New `psmux` session backend for Windows (native tmux clone using ConPTY).
- Auto-detected when `os.name == "nt"` and `psmux` binary is on PATH.
- Added `docs/WINDOWS_SUPPORT.md` with installation instructions.

### General
- Added configurable filesystem adaptor discovery via `adapter_search_paths` and `CHAITYA_ADAPTER_PATHS`.
- Added adaptor suspension/resume handling with `input list` and `input respond`.
- Added kernel-level tests for custom adaptor discovery and resumable input flow.
- Added executable workspace adaptor loading from `adaptors/`.
- Added first-party `file` and `shell` adaptors and wired loaded adaptor handlers into kernel dispatch.
- Added a real `tmux` session backend for macOS/Linux and wired config/kernel selection to it.
- Added repository-root test discovery and opt-in tmux/psmux integration tests.
- Fixed SDK tests for Python 3.14 event-loop behavior.
- Fixed kernel dispatch to preserve pipeline exit codes.
- Fixed kernel session and registry handlers to accept positional CLI arguments.
- Added contributor and adapter development documentation.
