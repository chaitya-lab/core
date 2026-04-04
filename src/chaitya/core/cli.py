"""CLI entry point for Chaitya Core.

The CLI name is dynamic — read from ``CHAITYA_CLI_NAME`` env var (default:
``chaitya``). All help text uses the resolved name so embedding projects
can rebrand without forking.

Usage::

    chaitya                         # same as ``chaitya info``
    chaitya info                    # system overview + adapter list
    chaitya info <adapter>          # adapter contract details
    chaitya <adapter> <subcommand>  # dispatch to adapter

Reference: PRD §3.2, §5, §12
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from chaitya.core import __version__
from chaitya.core.config import load_config
from chaitya.core.kernel import Kernel
from chaitya.core.types import KernelBootError


def _resolve_cli_name() -> str:
    """Read ``CHAITYA_CLI_NAME`` or default to ``chaitya``."""
    return os.environ.get("CHAITYA_CLI_NAME", "chaitya")


def _resolve_db_path(*, create_parent: bool = True) -> Path:
    """Resolve the default database path.

    Uses ``CHAITYA_DB_PATH`` if set, otherwise ``~/.chaitya/chaitya.db``.
    Creates the directory if needed.
    """
    env_path = os.environ.get("CHAITYA_DB_PATH")
    if env_path:
        p = Path(env_path)
    else:
        p = Path.home() / ".chaitya" / "chaitya.db"
    if create_parent:
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


class _JsonFormatter(logging.Formatter):
    """Format log records as JSON lines (PRD §14: kernel debug log)."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        return json.dumps(
            {
                "timestamp": ts,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            separators=(",", ":"),
        )


def _setup_kernel_logging(config: Any) -> None:
    """Set up kernel debug log file (PRD §14).

    The debug log is a rotating JSON-structured log at the path from
    ``kernel.debug_log`` in config. Records: boot sequence steps,
    adapter load/reject, pipeline errors, event bus errors, session state
    transitions, permission denials, shutdown sequence.
    """
    debug_path = getattr(config, "debug_log", None) or ""
    if not debug_path:
        return

    log_path = Path(debug_path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)


def _setup_logging(level: str = "WARNING") -> None:
    """Configure stderr logging for CLI use.

    The kernel debug log (file) is set up separately by _setup_kernel_logging.
    This only configures the stderr output.
    """
    root = logging.getLogger()
    numeric = getattr(logging, level.upper(), logging.WARNING)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(numeric)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stderr_handler)


async def _run(cli_name: str, args: list[str]) -> int:
    """Boot kernel, dispatch command, print output, shutdown."""
    # Parse global flags before the command expression
    log_level = "WARNING"
    db_path: str | Path | None = None

    filtered_args: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--version", "-V"):
            print(f"{cli_name} {__version__}")
            return 0
        if arg in ("--help", "-h"):
            _print_help(cli_name)
            return 0
        if arg == "--debug":
            log_level = "DEBUG"
            i += 1
            continue
        if arg == "--verbose":
            log_level = "INFO"
            i += 1
            continue
        if arg == "--db" and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
            continue
        if arg == "--memory":
            db_path = ":memory:"
            i += 1
            continue
        filtered_args.append(arg)
        i += 1

    # Load config first — needed for kernel logging setup
    config = load_config()

    # Set up kernel debug log to file (PRD §14)
    _setup_kernel_logging(config.kernel)

    # Stderr logging — controlled by --debug/--verbose flags
    _setup_logging(log_level)

    if db_path is None:
        db_path = _resolve_db_path()

    # Build command expression — empty means "info"
    expression = " ".join(filtered_args) if filtered_args else "info"

    # Create kernel from config
    kernel = Kernel.from_config(config)
    # Override from CLI flags
    default_db_path = _resolve_db_path(create_parent=False)
    if db_path != default_db_path:
        kernel = Kernel(config=config, db_path=db_path, cli_name=cli_name)
    elif cli_name != config.kernel.cli_name:
        kernel = Kernel(config=config, db_path=config.store.path or ":memory:", cli_name=cli_name)

    try:
        await kernel.boot()
    except KernelBootError as exc:
        print(f"{cli_name}: boot failed — {exc}", file=sys.stderr)
        return 1

    try:
        result = await kernel.dispatch(expression)
        if result.processed:
            print(result.processed)
        return result.exit_code
    except Exception as exc:
        print(f"{cli_name}: error — {exc}", file=sys.stderr)
        return 1
    finally:
        await kernel.shutdown()


def _print_help(cli_name: str) -> None:
    """Print usage help."""
    print(f"""\
{cli_name} — event-driven microkernel for personal automation

Usage:
  {cli_name}                              Show system info (same as info)
  {cli_name} info [adapter]               System or adapter details
  {cli_name} <adapter> <subcommand>       Run an adapter command
  {cli_name} session list|create|kill      Manage sessions
  {cli_name} watch                        View recent events
  {cli_name} registry list|validate       Manage adapters

Options:
  -h, --help       Show this help
  -V, --version    Show version
  --debug          Enable debug logging
  --verbose        Enable info logging
  --db <path>      Override database path
  --memory         Use in-memory database (no persistence)

Version: {__version__}
""")


def main() -> None:
    """Console script entry point."""
    cli_name = _resolve_cli_name()
    exit_code = asyncio.run(_run(cli_name, sys.argv[1:]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
