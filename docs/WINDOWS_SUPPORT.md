# Windows Support

Chaitya Core on Windows uses `psmux` as the session substrate — the same architectural role that `tmux` plays on macOS and Linux.

## Architecture

| Platform | Session Substrate | Shell Inside Sessions |
|----------|------------------|---------------------|
| macOS / Linux | `tmux` | `$SHELL` (bash, zsh, etc.) |
| Windows | `psmux` | PowerShell (pwsh) or PowerShell 5 |

Both substrates implement the same `SessionBackend` Protocol. The kernel's session management, event routing, and pipeline mechanics are identical across platforms. Only the concrete PTY allocation and signal delivery differ.

## Requirements

1. **psmux** — Native Windows terminal multiplexer written in Rust. Provides ConPTY-backed PTY allocation, named session management, and the same command protocol as tmux.
2. **PowerShell** — Chaitya targets PowerShell as the shell inside psmux sessions on Windows. pwsh 7+ is recommended; Windows PowerShell 5.x is also supported.

## Installation

### 1. Install psmux

```powershell
# WinGet (Recommended)
winget install psmux

# Cargo (requires Rust toolchain)
cargo install psmux

# Scoop
scoop bucket add psmux https://github.com/psmux/scoop-psmux
scoop install psmux
```

Restart your terminal after installation to ensure `psmux` is on your `PATH`.

### 2. Verify psmux

```powershell
psmux --version
```

### 3. Configure core.yaml (optional)

The psmux backend is auto-detected on Windows when the `psmux` binary is on PATH. To be explicit:

```yaml
session:
  backend: psmux
```

## Validation Strategy

Windows session functionality is validated through:

1. **Real backend tests** — Full psmux integration tests that create sessions, send input, stream output, and manage environment variables
2. **Manual smoke checklist** — Quick manual verification of core session operations

### Automated Tests

```powershell
$env:CHAITYA_RUN_TMUX_TESTS = "1"
python -m pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -v
```

Expected output: all 5 tests pass

| Test | What It Validates |
|------|-------------------|
| `test_tmux_backend_is_session_backend` | PsmuxBackend satisfies SessionBackend Protocol |
| `test_create_and_exists` | Session creation and existence check |
| `test_send_input_and_stream_output` | Input/output roundtrip through psmux |
| `test_set_and_unset_env` | Environment variable management via PowerShell |
| `test_interactive_roundtrip_through_kernel` | End-to-end kernel dispatch with psmux sessions |

### Manual Smoke Checklist

Run these commands after installing psmux:

```powershell
# 1. Create a named session
chaitya session create win-smoke

# 2. Send input and read output
chaitya session send-input win-smoke "Write-Output 'hello from psmux'" --newline
chaitya session output win-smoke

# 3. Set and verify environment variable
chaitya session set-env win-smoke CHAITYA_TEST=value
chaitya session send-input win-smoke "Write-Output `$env:CHAITYA_TEST" --newline
chaitya session output win-smoke

# 4. Send Ctrl-C (SIGINT)
chaitya session signal win-smoke SIGINT

# 5. Kill session
chaitya session kill win-smoke

# 6. Verify session is gone
chaitya session status win-smoke
```

Expected: each command succeeds; `session status` returns an error for the killed session.

## Session Behavior on Windows

### Shell Selection

The psmux backend prefers shells in this order:
1. `pwsh` (PowerShell 7+)
2. `powershell` (Windows PowerShell 5.x)
3. `cmd.exe` (fallback)

### Signal Mapping

Windows lacks POSIX signals. Chaitya maps signals to the closest Windows equivalent:

| Chaitya Signal | Windows Equivalent | Implementation |
|----------------|-------------------|----------------|
| `SIGKILL` / `SIGTERM` | Terminate session | `psmux kill-session` |
| `SIGINT` | Interrupt | `psmux send-keys C-c` |
| `SIGHUP` | Hangup | `psmux send-keys C-break` |

### Line Endings

Input sent through `send-input` is normalized to LF before being passed to psmux. The backend handles CRLF normalization internally.

## Troubleshooting

### "psmux binary 'psmux' not found on PATH"

Verify installation:
```powershell
where.exe psmux
psmux --version
```

Restart your terminal. If still missing, add psmux to PATH manually.

### "no server running"

Chaitya includes retry logic (10 retries, 200ms delay) to handle ConPTY startup characteristics. If persistent, verify your psmux version is current:

```powershell
psmux --version  # Should be >= 2.0
```

### Sessions not streaming output

Ensure the session is `idle` before sending input. A busy session (waiting for stdin) will queue input per the adapter's `on_busy_input` policy.

## Test Notes

Some tests in the test suite use the `local` backend (asyncio subprocesses) rather than psmux. These tests may not fully exercise Windows PTY behavior. The dedicated psmux tests in `tests/test_tmux_backend.py` and `tests/test_kernel_tmux_integration.py` are the authoritative validation for Windows session functionality.
