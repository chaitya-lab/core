# Feature: On-Load Adapter Lifecycle Hooks

Date: 2026-04-20
Status: Planned (Not Started)
Ticket: feature_onload_adaptor_2003

## Problem

Currently adapters are loaded at boot but services must be started explicitly via commands:

```bash
browser2 launch --headless  # Must explicitly start
apache start            # Must explicitly start
```

This requires users to remember to start services before using adapter commands.

## Goal

Add optional `on_load` hook that runs when adapter loads, enabling adapters to auto-start background services if available.

## Use Cases

1. **Browser2 daemon** - Auto-start Playwright daemon when browser2 loads
2. **Web server adapter** - Auto-start nginx/apache when web adapter loads  
3. **Database adapter** - Auto-connect to database when db adapter loads
4. **External service adapter** - Auto-connect to external API service

## Design Options

### Option A: Simple on_load in Contract

```python
@adapter(
    name="browser2",
    on_load=["launch", "--headless"],  # Commands to run on load
    commands=[...],
)
async def handler(stream, ctx):
    ...
```

### Option B: on_load Hook Function

```python
async def _on_load():
    """Called when adapter loads."""
    await start_daemon()

@adapter(
    name="browser2",
    on_load=_on_load,  # Hook function
    commands=[...],
)
async def handler(stream, ctx):
    ...
```

### Option C: Background Service Flag

```python
@adapter(
    name="browser2",
    background_service=True,  # Auto-start in tmux session
    commands=[...],
)
async def handler(stream, ctx):
    ...
```

## Recommended Design

### Option A (Simple) with fallback:

1. Add `on_load` field to `AdapterContract`
2. If service fails to start, log warning but continue loading
3. Adapter commands check if service running and prompt user

```python
@dataclass
class AdapterContract:
    on_load: list[str] = field(default_factory=list)  # commands to run
    on_unload: list[str] = field(default_factory=list)  # cleanup commands
    auto_start: bool = False  # whether to auto-start
```

## Implementation Steps

1. Add `on_load` field to `AdapterContract` in `sdk/types.py`
2. Modify kernel boot to call `on_load` commands after adapter loads
3. Add `on_unload` for cleanup on shutdown
4. Handle errors gracefully (log warning, don't fail boot)
5. Add health check for background services

## Related Files

- `sdk/src/chaitya_sdk/types.py` - AdapterContract
- `src/chaitya/core/kernel.py` - boot sequence
- `src/chaitya/core/session_manager.py` - session lifecycle
- `sdk/src/chaitya_sdk/session.py` - SessionRunner

## Priority

Medium - Enhances usability but explicit start pattern works fine

## Dependencies

None - Can be implemented independently