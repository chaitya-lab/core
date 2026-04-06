from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from chaitya_sdk import ChaityaStream, SessionContext, adapter, PermissionDenied
from chaitya_sdk.context import check_fs_read, check_fs_write


@adapter(
    name="file",
    description="Read and write local files.",
    commands=[
        {
            "name": "read",
            "description": "Read a UTF-8 text file from disk.",
            "params": [{"name": "path", "required": True, "description": "File path"}],
            "examples": ["chaitya file read --path README.md"],
        },
        {
            "name": "write",
            "description": "Write UTF-8 text to a file on disk.",
            "params": [
                {"name": "path", "required": True, "description": "File path"},
                {"name": "text", "required": True, "description": "Text to write"},
            ],
            "examples": ["chaitya file write --path notes.txt --text hello"],
        },
    ],
    permissions={
        "fs_read": [".", tempfile.gettempdir()],
        "fs_write": [".", tempfile.gettempdir()],
    },
)
def file_handler(stream: ChaityaStream, ctx: SessionContext) -> tuple[bytes, int]:
    subcommand = str(ctx.args.get("subcommand", ""))
    path_arg = ctx.args.get("path")
    if not path_arg:
        return b"Missing required argument: --path", 1

    path = Path(str(path_arg)).expanduser()
    if subcommand == "read":
        try:
            check_fs_read(str(path))
            return path.read_text(encoding="utf-8").encode("utf-8"), 0
        except PermissionDenied as exc:
            return str(exc).encode("utf-8"), 1
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if subcommand == "write":
        text = ctx.args.get("text")
        if text is None:
            return b"Missing required argument: --text", 1
        try:
            check_fs_write(str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(text), encoding="utf-8")
            return f"Wrote {path}".encode(), 0
        except PermissionDenied as exc:
            return str(exc).encode("utf-8"), 1
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    return f"Unknown file subcommand: {subcommand}".encode(), 1


__chaitya_handler__ = file_handler
__adapter_contract__ = asdict(file_handler.__chaitya_contract__)
