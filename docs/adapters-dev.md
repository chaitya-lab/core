# Adapter Development

Adapters are the main extension mechanism for Chaitya Core. The kernel stays small by pushing capability into adapter packages.

## Rule One

Import from `chaitya_sdk`, not `chaitya.core`.

That is the public boundary for adapter authors.

## What An Adapter Contains

An adapter package needs:

- a handler function
- an adapter contract
- command metadata
- a package entry point or a discoverable local workspace path

The easiest path is the `@adapter` decorator.

## Minimal Example

```python
from chaitya_sdk import ChaityaStream, SessionContext, adapter


@adapter(
    name="greet",
    description="Simple greeting adapter",
    commands=[
        {
            "name": "hello",
            "description": "Say hello",
            "params": [
                {"name": "name", "required": False, "description": "Name to greet"},
            ],
            "examples": ["chaitya greet hello --name Alice"],
        }
    ],
)
def greet_handler(stream: ChaityaStream, ctx: SessionContext) -> tuple[bytes, int]:
    name = str(ctx.args.get("name", "world"))
    return f"Hello, {name}\n".encode("utf-8"), 0
```

## Handler Shape

Handlers can be synchronous or asynchronous. The normal shape is:

```python
def handler(stream: ChaityaStream, ctx: SessionContext) -> tuple[bytes, int]:
    ...
```

or:

```python
async def handler(stream: ChaityaStream, ctx: SessionContext) -> tuple[bytes, int]:
    ...
```

The two arguments are:

- `ChaityaStream`: input content and metadata from the pipeline
- `SessionContext`: parsed args, environment, session state, and dry-run info

## Contract Fields

The decorator builds an `AdapterContract` from your metadata.

The most important fields are:

- `name`
- `description`
- `commands`
- `permissions`
- `depends_on`
- `on_busy_input`
- `on_shutdown`
- `events_emitted`
- `events_consumed`

Each command should include:

- `name`
- `description`
- `params`
- `examples`

## Permissions

Declare permissions conservatively.

Common permission fields:

- `fs_read`
- `fs_write`
- `network`
- `can_emit_events`
- `can_read_all_events`
- `can_access_sessions`

The SDK exposes helpers that enforce these boundaries:

```python
from chaitya_sdk import check_fs_read, check_fs_write, check_network
```

## Events

Adapters can emit events through the SDK event bus proxy.

```python
from chaitya_sdk import event_bus
from chaitya_sdk.types import Event

await event_bus.emit(
    Event(
        type="greet.hello",
        source_adapter="greet",
        payload={"name": "Alice"},
    )
)
```

Events are useful for observability, automation, and coordination with `watch`.

## Suspended Input

If an adapter needs input that is missing, it can suspend.

```python
from chaitya_sdk import InputSpec, InputType, Suspension

raise Suspension(
    InputSpec(
        name="name",
        prompt="What is your name?",
        input_type=InputType.TEXT,
    )
)
```

The runtime flow is:

1. the adapter suspends
2. the kernel stores the pending request
3. the user runs `chaitya input list`
4. the user runs `chaitya input respond <request_id> <value>`
5. the kernel resumes the adapter

## Session-Backed Adapters

If an adapter needs to drive a long-lived session, use `SessionRunner` from the SDK instead of talking to the backend directly.

That keeps adapter code aligned with the kernel session model.

## Packaging

For installed packages, expose the adapter via the `chaitya.adapters` entry-point group.

Example:

```toml
[project.entry-points."chaitya.adapters"]
greet = "chaitya_adapter_greet"
```

## Local Workspace Development

Chaitya can also discover adapters from filesystem paths.

Expected shape:

```text
my-adapters/
  greet/
    pyproject.toml
    src/chaitya_adapter_greet/__init__.py
```

Then point the kernel at that workspace with `core.yaml` or environment variables.

Example:

```yaml
adapters_config_dir: ~/.chaitya/adapters
adapter_search_paths:
  - /abs/path/to/my-adapters
```

or:

```bash
export CHAITYA_ADAPTER_PATHS="/abs/path/to/my-adapters"
```

## Good Adapter Behavior

- keep command names simple
- return clear text output
- declare examples for every command
- emit events when meaningful state changes happen
- request only the permissions you need
- prefer using the pipeline rather than reinventing chaining

## Testing

Adapter tests should normally cover:

- contract metadata
- happy-path command behavior
- error handling
- permission enforcement
- suspension flows, if used
- event emission, if used

For first-party adapters in this repo, prefer real kernel integration tests when behavior crosses sessions, events, or the pipeline.
