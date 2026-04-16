"""Session adapter — manage named running environments (sessions).

This is a core adapter that provides the ``session`` command.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter, session_manager, store


@adapter(
    name="session",
    description="Manage named running environments (sessions).",
    commands=[
        {
            "name": "list",
            "description": "List all sessions.",
            "params": [],
            "examples": ["chaitya session list"],
        },
        {
            "name": "create",
            "description": "Create a new session.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {"name": "template", "required": False, "description": "Template name."},
            ],
            "examples": ["chaitya session create my-session"],
        },
        {
            "name": "status",
            "description": "Show session state and info.",
            "params": [{"name": "name", "required": True, "description": "Session name."}],
            "examples": ["chaitya session status my-session"],
        },
        {
            "name": "attach",
            "description": "Attach to session terminal (tmux).",
            "params": [{"name": "name", "required": True, "description": "Session name."}],
            "examples": ["chaitya session attach my-session"],
        },
        {
            "name": "detach",
            "description": "Detach from session.",
            "params": [{"name": "name", "required": True, "description": "Session name."}],
            "examples": ["chaitya session detach my-session"],
        },
        {
            "name": "output",
            "description": "Read session terminal output.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {
                    "name": "idle-timeout",
                    "required": False,
                    "description": "Idle timeout in seconds.",
                },
            ],
            "examples": ["chaitya session output my-session"],
        },
        {
            "name": "send",
            "description": "Send input to session.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {"name": "text", "required": False, "description": "Text to send."},
                {"name": "newline", "required": False, "description": "Append newline."},
                {"name": "key", "required": False, "description": "Key name (enter/tab/space)."},
            ],
            "examples": ["chaitya session send my-session --text 'echo hello' --newline"],
        },
        {
            "name": "signal",
            "description": "Send signal to session.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {
                    "name": "sig",
                    "required": True,
                    "description": "Signal name (TERM, KILL, INT, HUP).",
                },
            ],
            "examples": ["chaitya session signal my-session TERM"],
        },
        {
            "name": "set-env",
            "description": "Set environment variable in session.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {"name": "key", "required": True, "description": "Variable name."},
                {"name": "value", "required": True, "description": "Variable value."},
            ],
            "examples": ["chaitya session set-env my-session --key FOO --value bar"],
        },
        {
            "name": "unset-env",
            "description": "Remove environment variable from session.",
            "params": [
                {"name": "name", "required": True, "description": "Session name."},
                {"name": "key", "required": True, "description": "Variable name."},
            ],
            "examples": ["chaitya session unset-env my-session --key FOO"],
        },
        {
            "name": "exec",
            "description": "Manage execution mode (enabled/disabled/readonly).",
            "params": [
                {
                    "name": "action",
                    "required": True,
                    "description": "Action: enable, disable, readonly, status.",
                },
                {"name": "name", "required": False, "description": "Session name."},
            ],
            "examples": ["chaitya session exec disable my-session"],
        },
        {
            "name": "kill",
            "description": "Kill a session.",
            "params": [{"name": "name", "required": True, "description": "Session name."}],
            "examples": ["chaitya session kill my-session"],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": ["/tmp"], "network": False, "can_emit_events": True},
)
async def session_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    args = ctx.args
    sub = args.get("subcommand", "list")
    positional = args.get("__raw_args__", [])

    sm = session_manager.get_session_manager()
    s = store.get_store()

    if sub == "list":
        sessions = await s.list_sessions()
        if not sessions:
            return b"No sessions. Use 'session create <name>' to create one.", 0
        lines = ["NAME                 STATE      TEMPLATE             LAST ACTIVITY"]
        lines.append("-" * 80)
        for r in sessions:
            tmpl = r.template or "-"
            lines.append(f"{r.name:20s} {r.state.value:10s} {tmpl:20s} {r.last_activity}")
        return "\n".join(lines).encode("utf-8"), 0

    if sub == "status":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session status <name>", 1
        rec = await s.get_session(name)
        if rec is None:
            return f"Session '{name}' not found.".encode("utf-8"), 1
        lines = [
            f"Name: {rec.name}",
            f"State: {rec.state.value}",
            f"Exec: {rec.exec_mode}",
            f"Created: {rec.created_at}",
        ]
        return "\n".join(lines).encode("utf-8"), 0

    if sub == "exec":
        action = positional[0] if positional else ""
        name = args.get("name") or (positional[1] if len(positional) > 1 else "")
        if action == "status":
            if not name:
                return b"Usage: session exec status <name>", 1
            mode = await sm.get_exec_mode(name)
            return f"Session '{name}' exec mode: {mode}\n".encode("utf-8"), 0
        if action in ("enable", "disable", "readonly"):
            if not name:
                return f"Usage: session exec {action} <name>".encode("utf-8"), 1
            mode_map = {"enable": "enabled", "disable": "disabled", "readonly": "readonly"}
            ok = await sm.set_exec_mode(name, mode_map[action])
            if not ok:
                return f"Session '{name}' not found.".encode("utf-8"), 1
            return f"Session '{name}' exec mode set to {action}.\n".encode("utf-8"), 0
        return (
            b"Usage: session exec <enable|disable|readonly|status> <name>\n"
            b"  enable   - allow commands to execute\n"
            b"  disable  - dry-run only (default)\n"
            b"  readonly - no execution, no dry-run\n"
            b"  status   - show current exec state\n"
        ), 1

    if sub == "create":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session create <name> [--template <template-name>]", 1
        template = args.get("template")
        try:
            await sm.create(name=name, template=template)
            msg = f"Session '{name}' created"
            if template:
                msg += f" (template: {template})"
            msg += "."
            return msg.encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub in ("send", "send-input"):
        name = (
            args.get("name") or (positional[0] if positional else "") or ctx.session_id or "default"
        )
        if not name:
            return b"Usage: session send-input [name] <text> [--newline] [--key <key>]", 1
        text_parts = [a for a in positional[1:] if not a.startswith("--")]
        text = str(args.get("text", "") or " ".join(text_parts) if text_parts else "")
        newline = bool(args.get("newline"))
        key = str(args.get("key", "")).lower()
        if key:
            key_map = {"enter": b"\n", "return": b"\n", "tab": b"\t", "space": b" "}
            data = key_map.get(key)
            if data is None:
                return f"Unsupported key: {key}".encode("utf-8"), 1
        else:
            data = text.encode("utf-8")
            if newline:
                data += b"\n"
        try:
            await sm.send_input(name, data)
            return f"Input sent to session '{name}'.".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "output":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session output <name> [--idle-timeout 0.2]", 1
        idle_timeout = float(args.get("idle_timeout", args.get("idle-timeout", "0.2")))
        try:
            output = await sm.read_output(name, idle_timeout_seconds=idle_timeout)
            return output, 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "attach":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session attach <name>", 1
        try:
            await sm.attach(name)
            return f"Attached to session '{name}'.".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "view":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session view <name>", 1
        try:
            content = await sm.view(name)
            if isinstance(content, bytes):
                return content, 0
            return content.encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "detach":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session detach <name>", 1
        try:
            await sm.detach(name)
            return f"Detached from session '{name}'.".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "signal":
        name = args.get("name") or (positional[0] if positional else "")
        sig = (
            args.get("sig") or args.get("signal") or (positional[1] if len(positional) > 1 else "")
        )
        if not name or not sig:
            return b"Usage: session signal <name> <signal>", 1
        try:
            await sm.signal(name, str(sig))
            return f"Signal {sig} sent to session '{name}'.".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "set-env":
        name = args.get("name") or (positional[0] if positional else "")
        key = str(args.get("key", "") or (positional[1] if len(positional) > 1 else ""))
        value = str(args.get("value", ""))
        if not name or not key:
            return b"Usage: session set-env <name> --key <KEY> --value <VALUE>", 1
        try:
            await sm.set_env(name, key, value)
            return f"Environment set for session '{name}': {key}".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "unset-env":
        name = args.get("name") or (positional[0] if positional else "")
        key = str(args.get("key", "") or (positional[1] if len(positional) > 1 else ""))
        if not name or not key:
            return b"Usage: session unset-env <name> --key <KEY>", 1
        try:
            await sm.unset_env(name, key)
            return f"Environment removed for session '{name}': {key}".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    if sub == "kill":
        name = args.get("name") or (positional[0] if positional else "")
        if not name:
            return b"Usage: session kill <name>", 1
        try:
            await sm.kill(name)
            return f"Session '{name}' killed.".encode("utf-8"), 0
        except Exception as exc:
            return str(exc).encode("utf-8"), 1

    return f"Unknown session subcommand: {sub}".encode("utf-8"), 1


__chaitya_handler__ = session_handler
__adapter_contract__ = asdict(session_handler.__chaitya_contract__)
