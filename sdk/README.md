# Chaitya SDK

The **only** kernel surface adapters touch.

```python
from chaitya_sdk import (
    adapter,          # decorator for command registration
    ChaityaStream,    # typed input stream
    SessionContext,   # session state and args
    event_bus,        # emit and subscribe
    Suspension,       # yield to request input
    InputSpec,        # input field definition
    InputType,        # input type enum
    PermissionDenied, # raised on out-of-bounds access
)
```

## Quick Start

```python
from chaitya_sdk import adapter, ChaityaStream, SessionContext

@adapter(
    name="greet",
    description="A friendly greeter",
    commands=[{"name": "hello", "description": "Say hello"}],
)
def greet_hello(stream: ChaityaStream, ctx: SessionContext) -> bytes:
    name = ctx.args.get("name", "world")
    return f"Hello, {name}!".encode()
```

Install the SDK:

```bash
pip install chaitya-sdk
```

See the [Adapter Developer Guide](../docs/adapters-dev.md) for full documentation.

