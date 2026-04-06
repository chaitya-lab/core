# Chaitya Core Windows Support

Chaitya Core uses `psmux` as the intended Windows session substrate.
The Windows design should be treated as `tmux on macOS/Linux` and `psmux on Windows`.

## Requirements

1.  **psmux**: A native Windows tmux clone built in Rust. It provides the PTY and session management needed for Chaitya's interactive features.
2.  **PowerShell 7 (`pwsh`)**: Recommended as the default shell inside psmux sessions. Windows PowerShell also works.

## Installation

### 1. Install psmux

You can install `psmux` via several Windows package managers:

```powershell
# Using WinGet (Recommended)
winget install psmux

# Using Cargo (if you have Rust installed)
cargo install psmux

# Using Scoop
scoop bucket add psmux https://github.com/psmux/scoop-psmux
scoop install psmux
```

**IMPORTANT**: After installation, restart your terminal to ensure `psmux` is on your `PATH`.

### 2. Configure Chaitya

Configure `psmux` explicitly in `core.yaml`:

```yaml
session:
  backend: psmux
```

That keeps the session story aligned with the project architecture:

- macOS/Linux: `tmux`
- Windows: `psmux`

## What To Verify

On Windows, validate the backend with real `psmux` flows instead of only unit tests:

```powershell
psmux --version
$env:CHAITYA_RUN_TMUX_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```

Recommended manual checks:

- `chaitya session create win-dev`
- `chaitya session send-input win-dev "Write-Output hello" --newline`
- `chaitya session output win-dev`
- `chaitya session set-env win-dev DEMO=value`
- `chaitya session send-input win-dev "Write-Output $env:DEMO" --newline`
- `chaitya session signal win-dev SIGINT`

## Running Tests

To run the full suite including interactive session tests on Windows:

```powershell
# In PowerShell
$env:CHAITYA_RUN_TMUX_TESTS="1"
.\.venv\Scripts\python.exe -m pytest
```

## Troubleshooting

### psmux binary not found
If you get a `RuntimeError: psmux binary 'psmux' not found on PATH`, verify your installation and ensure that `psmux` is executable from your terminal by running `psmux --version`.

### psmux: no server running
Chaitya includes built-in retries and stabilization delays for `psmux` to handle Windows ConPTY startup characteristics. If you frequently encounter this error, increase the backend `poll_interval` or verify the installed `psmux` version matches the command set Chaitya expects.
