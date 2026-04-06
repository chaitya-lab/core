# Usage

## Setup (Windows/PowerShell)

```powershell
# Create virtual environment
python -m venv .venv

# Activate it
.venv\Scripts\Activate.ps1

# Install chaitya core and SDK
pip install -e . -e ./sdk

# Install playwright for browser tests (optional)
pip install playwright
playwright install chromium

# Run tests
pytest tests\test_tmux_backend.py -v
```

## Setup (macOS/Linux)

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install chaitya core and SDK
pip install -e . -e ./sdk

# Run tests
pytest tests/test_tmux_backend.py -v
```

## Quick Start

```bash
# Show help and installed adapters
chaitya info

# Or with --memory to skip database
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
