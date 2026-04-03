# Adapter Development Guide

## Contract

Adapters are separate Python packages discovered through the `chaitya.adapters` entry-point group.

Each adapter must declare:

- `name`
- `description`
- `contract_version`
- `commands`
- `permissions`

Each command should include:

- `name`
- `description`
- `params` or `parameters`
- at least one example

## SDK Rule

Adapters should import only from `chaitya_sdk`.

```python
from chaitya_sdk import adapter, ChaityaStream, SessionContext


@adapter(
    name="example",
    description="Example adapter",
    commands=[{"name": "run", "description": "Run the adapter"}],
)
def run(stream: ChaityaStream, ctx: SessionContext) -> bytes:
    return b"ok"
```

## Packaging

Example `pyproject.toml` entry point:

```toml
[project.entry-points."chaitya.adapters"]
example = "chaitya_adapter_example"
```

The registry currently reads adapter metadata from either:

- `__adapter_contract__` on the module
- `module.json` next to the module

For local development without packaging, the kernel can also discover adaptor workspaces from configured filesystem paths. Expected layout:

```text
my-adaptors/
  mytool/
    src/chaitya_adapter_mytool/__init__.py
```

## Permissions

Declare permissions conservatively. The SDK exposes permission-aware helpers such as `event_bus`.

Typical fields:

- `fs_read`
- `fs_write`
- `network`
- `can_emit_events`
- `can_read_all_events`
- `can_access_sessions`

## Testing

At minimum, adapter packages should test:

- contract generation
- handler behavior
- permission failures
- event emission and subscription behavior where relevant

For system adapters, prefer integration tests against a real kernel instance.

## Suspension And Resume

An adaptor can request missing input by declaring `on_missing: suspend` on a parameter, or by raising `Suspension(InputSpec(...))`.

From the CLI, the flow is:

```bash
chaitya mytool run
chaitya input list
chaitya input respond <request_id> <value>
```

The kernel emits `input_requested` and `input_response` events so headless clients can also drive the flow.
