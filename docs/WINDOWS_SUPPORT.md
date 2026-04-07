# Windows Support

Chaitya Core supports Windows through the `psmux` session backend.

## Backend Model

The kernel uses different terminal substrates per platform:

| Platform | Default backend | Session shell |
|---|---|---|
| macOS/Linux | `tmux` | `$SHELL` |
| Windows | `psmux` | `pwsh`, `powershell`, or `cmd.exe` |

The rest of the kernel stays the same across platforms. Only the concrete session backend changes.

## Requirements

Install:

- Python 3.11+
- PowerShell
- `psmux` on your `PATH`

## Install `psmux`

Examples:

```powershell
winget install psmux
```

```powershell
cargo install psmux
```

After installation, restart the shell and verify:

```powershell
psmux --version
```

## Session Backend Selection

With `session.backend: auto`, the kernel chooses:

- `tmux` on non-Windows systems when available
- `psmux` on Windows when available

If `psmux` is not installed on Windows, backend selection still resolves to `psmux`; session commands will then fail with a clear missing-binary error until `psmux` is installed.

You can force Windows to use `psmux` explicitly:

```yaml
session:
  backend: psmux
```

or:

```powershell
$env:CHAITYA_SESSION_BACKEND = "psmux"
```

## Session Behavior

The Windows backend provides the same session commands:

- `session create`
- `session list`
- `session status`
- `session send`
- `session output`
- `session set-env`
- `session unset-env`
- `session signal`
- `session kill`

Shell selection order inside a Windows session:

1. `pwsh`
2. `powershell`
3. `cmd.exe`

Session templates and session exec gating work the same way on Windows as on other platforms.

## Smoke Test

```powershell
chaitya session create win-demo
chaitya session send win-demo --text "Write-Output 'hello'" --newline
chaitya session output win-demo
chaitya session set-env win-demo --key CHAITYA_TEST --value value
chaitya session send win-demo --text "Write-Output `$env:CHAITYA_TEST" --newline
chaitya session output win-demo
chaitya session kill win-demo
```

## Tests

Run the backend integration tests:

```powershell
$env:CHAITYA_RUN_TMUX_TESTS = "1"
pytest tests/test_tmux_backend.py tests/test_kernel_tmux_integration.py -q
```

## Troubleshooting

### `psmux` not found

Check:

```powershell
where.exe psmux
psmux --version
```

### Session creation or output is flaky

`psmux` uses ConPTY underneath. Short delays between rapid interactive writes can help in tests and manual debugging.

### PowerShell-specific behavior

Environment mutation and signal semantics are different from Unix shells. If a workflow behaves differently on Windows, verify it directly inside a `psmux` session before changing the kernel contract.
