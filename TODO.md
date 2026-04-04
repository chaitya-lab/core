# Chaitya Core — TODO

## Completed

### Registry is Built-In (no pip package required)
- [x] `registry list`, `registry info`, `registry validate` are built-in kernel commands
- [x] No `BootstrapLoader` — registry is handled directly by `_handle_registry` in kernel
- [x] `DEFAULT_SYSTEM_ADAPTERS` includes "registry" as a marker
- [x] Registry discovery skips pip entry point named "registry" (handled internally)
- [x] SDK context `registry_proxy` wired at boot for adapter access
- [x] `adaptors/core/registry/` pip package removed (was over-engineered)
- [x] Tests pass without mock registry adapter

### SQLite Event Bus Improvements
- [x] Batched writes: events buffered in memory, flushed to SQLite every 0.5s or 100 events
- [x] Stream-only events (stdout_chunk, stderr_chunk, progress_update, file_changed) NOT persisted to SQLite
- [x] Subscriber delivery always immediate regardless of batching
- [x] history() and event_count() flush before reading

### Tmux Edge Cases
- [x] Stuck monitor checks backend.exists() before marking STUCK — marks DEAD if pane gone
- [x] send_input and other operations protected by _ensure_exists()

### Request/Response Timeout
- [x] Background `_input_timeout_loop` checks every 10s for expired pending inputs
- [x] Expired inputs are removed from store and `input_timeout` event emitted
- [x] Configurable via `input_timeout_seconds` (default 300s)

---

## Pending (Medium Priority)

### L0 Input Ingest
**PRD §4, §5:** `input --text`, `--file`, `--clipboard`, `--merge`
- Currently: `_handle_input` only handles suspension resume
- Needed: all L0 ingest options for creating ChaityaStream

### Kernel Debug Log — File Output
**PRD §14:** `~/.chaitya/logs/kernel.log` rotating JSON-structured log
**PRD §12:** `kernel.debug_log` and `kernel.log_level` in config
- `_setup_kernel_logging()` in cli.py with `_JsonFormatter` (wired up)
- Needs: verify config `kernel.debug_log` path works correctly

### SDK Permission Enforcement
**PRD §9:** SDK checks permissions at API boundary
- Currently: only `can_emit_events` is enforced
- Needed: `fs_read` and `fs_write` permission checks

### Platform Support
- [x] Windows: `psmux` backend auto-detected on `win32` with psmux binary
- [x] macOS/Linux: `tmux` backend auto-detected when tmux available
- [x] Fallback: `LocalProcessBackend` on all platforms

---

## Testing Checklist (from PRD §15)

- [x] `chaitya` alone → comprehensive help
- [x] `chaitya <loaded-adapter>` → adapter info
- [x] `chaitya registry list` → adapter list (built-in, no pip needed)
- [x] Unknown command → actionable error
- [x] `chaitya info --kernel` → kernel status
- [x] Session create → list shows idle
- [x] `chaitya watch` → events
- [x] Pipeline: metadata footer on every output
- [ ] Session signal → delivered
- [ ] `chaitya watch --exit-after N` → exits after N events
- [ ] Kernel restart → sessions restored
- [ ] Adapter emits event → via `watch`
- [ ] Suspension: missing param → input_requested emitted

---

## Phase 1 Complete Criteria

| Criteria | Status |
|---|---|
| Section 15 integration tests | ~60% done |
| Registry built-in (no pip required) | ✅ |
| Bad adapter rejected | ✅ |
| Suspension (terminal + headless) | ✅ |
| Windows + macOS support | ✅ |

---

*Last updated: 2026-04-04*
