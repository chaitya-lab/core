# Usage

## Install

From source (macOS/Linux):

```bash
pip install -e .
pip install -e ./sdk
```

From source (Windows/PowerShell):

```powershell
pip install -e . -e ./sdk
# Or with venv:
.venv\Scripts\python.exe -m pip install -e . -e ./sdk
```

Quick smoke test without writing a database:

```bash
chaitya --memory info
```

## Sessions

Create a session:

```bash
chaitya session create dev
```

Send input:

```bash
chaitya session send-input dev "python3" --newline
```

Read output:

```bash
chaitya session output dev --idle-timeout 0.4
```

Set environment:

```bash
chaitya session set-env dev API_KEY=value
```

## Watch Events

Recent session events:

```bash
chaitya watch --session dev --limit 20
```

Filter by type:

```bash
chaitya watch --on input_requested --limit 20
```

## Suspension Flow

List pending requests:

```bash
chaitya input list
```

Respond:

```bash
chaitya input respond <request_id> yes
```

## Custom Adaptors

Configure filesystem discovery with:

```yaml
adapters_config_dir: "~/.chaitya/adapters"
adapter_search_paths:
  - "/abs/path/to/my-adaptors"
```

Or with an environment variable:

```bash
export CHAITYA_ADAPTER_PATHS="/abs/path/to/my-adaptors"
```
