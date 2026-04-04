# Chaitya Core Windows Support

Chaitya Core is now fully compatible with Windows using `psmux` as the session substrate. 

## Requirements

1.  **psmux**: A native Windows tmux clone built in Rust. It provides the PTY and session management needed for Chaitya's interactive features.
2.  **PowerShell 7 (pwsh)**: Recommended, but Windows PowerShell or cmd.exe will also work as the default shell within sessions.

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

Chaitya automatically detects Windows and defaults to the `psmux` backend if available. You can also explicitly set it in your `core.yaml`:

```yaml
session:
  backend: psmux
```

## Running Tests

To run the full suite including interactive session tests on Windows:

```powershell
# In PowerShell
$env:CHAITYA_RUN_TMUX_TESTS="1"
pytest
```

## Troubleshooting

### psmux binary not found
If you get a `RuntimeError: psmux binary 'psmux' not found on PATH`, verify your installation and ensure that `psmux` is executable from your terminal by running `psmux --version`.

### psmux: no server running
Chaitya includes built-in retries and stabilization delays for `psmux` to handle Windows ConPTY startup characteristics. If you frequently encounter this error, you may want to increase the `poll_interval` in your configuration.
