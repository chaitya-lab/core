# Chaitya Core — TODO

## Completed

### Registry Adapter as Pip Package
- [x] Created `adaptors/core/registry/` with proper pip package structure
- [x] `chaitya-adapter-registry` installs via pip with `chaitya.adapters` entry point
- [x] Registry adapter implements `list`, `info`, `validate` commands
- [x] SDK context module exposes `registry_proxy` for registry access from adapters
- [x] Kernel wires registry into SDK context at boot via `registry_proxy.set_registry()`
- [x] Kernel accepts `registry_adapter_pkg` fallback parameter (for test fixtures)
- [x] Workspace discovery excludes registry adapter (loaded separately via bootstrap loader)

### Command Dispatch Fix
- [x] Fixed `_parse_command_token` to handle `--on`, `--session` etc. as flags
- [x] Previously: `watch --on input_requested` parsed `--on` as subcommand
- [x] Now: `watch --on input_requested` correctly parses as `args={'on': 'input_requested'}`
- [x] Fixed `BootstrapLoader.load_registry_adapter()` to use `list(eps.select(...))`
- [x] Fixed kernel error handling to show full exception traceback

### Test Infrastructure
- [x] Test fixtures provide mock registry adapter for in-process testing
- [x] Custom adaptor tests fixed to properly raise `Suspension` for missing params
- [x] All 237 tests pass

---

## Pending (Medium Priority)

### L0 Input Ingest
**PRD §4, §5:** `input --text`, `--file`, `--clipboard`, `--merge`
- Currently: `_handle_input` only handles suspension resume
- Needed: all L0 ingest options for creating ChaityaStream

### Kernel Debug Log — File Output
**PRD §14:** `~/.chaitya/logs/kernel.log` rotating JSON-structured log
**PRD §12:** `kernel.debug_log` and `kernel.log_level` in config
- Currently: `_setup_kernel_logging()` in cli.py with `_JsonFormatter` (wired up)
- Needed: verify config `kernel.debug_log` path works correctly

### SDK Permission Enforcement
**PRD §9:** SDK checks permissions at API boundary
- Currently: only `can_emit_events` is enforced
- Needed: `fs_read` and `fs_write` permission checks on file operations

---

## System Adapters (Phase 1 extras — separate pip packages)

These are NOT core — separate pip packages:

| Adapter | Purpose | Status |
|---|---|---|
| `chaitya-adapter-registry` | pip package for registry discovery | Done (installed editable) |
| `chaitya-adapter-vault` | OS keychain integration | Not started |
| `chaitya-adapter-route` | Conditional routing | Not started |
| `chaitya-adapter-process` | Process spawning | Not started |

---

## Testing Checklist

From PRD §15 — Integration Tests (run after each session):

- [x] `chaitya` alone → comprehensive help (not just adapter list)
- [x] `chaitya <loaded-adapter>` → adapter info (same as `chaitya info <adapter>`)
- [x] Unknown command → actionable error with adapter list
- [x] `chaitya info --kernel` → kernel status
- [x] Session create → list shows idle
- [x] Session set-env → env var available
- [x] `chaitya watch` → events
- [x] `chaitya watch --search` → searched events
- [x] Pipeline: metadata footer on every output
- [x] `chaitya registry list` → adapter list (requires installed registry adapter)
- [ ] Session signal → delivered
- [ ] Session send-input → received by running process
- [ ] `chaitya watch --exit-after N` → exits after N events
- [ ] Kernel restart → sessions restored
- [ ] Adapter emits event → via `watch`
- [ ] Suspension: missing param → input_requested emitted
- [ ] Suspension: input_response → adapter resumes

---

## Phase 1 Complete Criteria

From PRD §17:

| Criteria | Status |
|---|---|
| Section 15 integration tests | ~60% done |
| Zero built-in commands | ✅ Kernel has 6 commands, dispatches to adapters |
| Bad adapter rejected | ✅ |
| Suspension (terminal + headless) | ✅ |

---

*Last updated: 2026-04-04*
