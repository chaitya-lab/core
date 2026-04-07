# Chaitya SDK

The SDK is the public surface for adapter authors.

If you are writing an adapter, this is the package you should depend on.

## What It Exposes

Core pieces:

- `adapter`
- `ChaityaStream`
- `SessionContext`
- `Suspension`
- `InputSpec`
- `InputType`
- `PermissionDenied`

Runtime helpers:

- `event_bus`
- `registry_proxy`
- `check_fs_read`
- `check_fs_write`
- `check_network`
- `SessionRunner`

Contract types:

- `AdapterContract`
- `AdapterPermissions`
- `CommandSpec`
- `CommandParam`
- `OutputRoutingRule`
- `ResourceLimits`

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

## Session-Aware Work

If your adapter needs a persistent shell session, use `SessionRunner`. It is the supported high-level helper for session-backed adapter behavior.

## Suspended Input

Adapters can request missing input:

```python
from chaitya_sdk import InputSpec, InputType, Suspension

raise Suspension(
    InputSpec(
        name="token",
        prompt="Enter API token",
        input_type=InputType.PASSWORD,
    )
)
```

The kernel then resumes the handler after the user responds through the CLI.

## Permissions

Use the permission helpers before touching filesystem or network resources:

```python
from chaitya_sdk import check_fs_read, check_fs_write, check_network
```

This keeps adapter behavior aligned with the declared contract.

## Install

From this repository:

```bash
pip install -e ./sdk
```

## More

See:

- [Adapter Development Guide](../docs/adapters-dev.md)
- [Architecture](../docs/architecture.md)
