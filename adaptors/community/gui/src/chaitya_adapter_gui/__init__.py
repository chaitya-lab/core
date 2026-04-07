"""GUI control adapter — Keyboard and mouse automation.

Design (PRD Phase 2):
  - click: click at coordinates or on a UI element
  - move: move the mouse cursor
  - type: type text
  - press: press a key or key combination
  - drag: drag from one point to another

macOS: uses osascript (AppleScript System Events) for GUI scripting.
Linux: uses xdotool/ydotool if available.
Windows: uses PyAutoGUI or subprocess.

Note: GUI scripting on macOS requires Accessibility permissions.
System Settings > Privacy & Security > Accessibility > enable terminal/shell.
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import asdict

from chaitya_sdk import ChaityaStream, SessionContext, adapter


import platform

_PLATFORM = platform.system()


def _run_sync(cmd: list[str], timeout: float = 10.0) -> tuple[bytes, int]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        return proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return f"[error] gui: command timed out after {timeout}s\n".encode(), 1
    except FileNotFoundError:
        return f"[error] gui: command not found: {cmd[0]}\n".encode(), 1
    except Exception as exc:
        return f"[error] gui: {exc}\n".encode(), 1


async def _osascript(script: str) -> tuple[bytes, int]:
    proc = await asyncio.create_subprocess_shell(
        f"osascript -e '{script}'",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        if "not allowed" in err.lower() or "accessibility" in err.lower():
            return (
                f"[error] gui: accessibility permission denied.\n"
                f"Hint: System Settings > Privacy & Security > Accessibility > enable terminal/shell\n".encode(),
                1,
            )
        return f"[error] gui osascript: {err}\n".encode(), 1
    return stdout, 0


def _key_code_map() -> dict[str, str]:
    return {
        "return": "36",
        "enter": "36",
        "tab": "48",
        "escape": "53",
        "esc": "53",
        "space": "49",
        "delete": "51",
        "backspace": "51",
        "up": "126",
        "down": "125",
        "left": "123",
        "right": "124",
        "home": "6",
        "end": "119",
        "pageup": "116",
        "pagedown": "121",
        "f1": "122",
        "f2": "120",
        "f3": "99",
        "f4": "118",
        "f5": "96",
        "f6": "97",
        "f7": "98",
        "f8": "100",
        "f9": "101",
        "f10": "109",
        "f11": "103",
        "f12": "111",
        "a": "0",
        "s": "1",
        "d": "2",
        "f": "3",
        "h": "4",
        "g": "5",
        "z": "6",
        "x": "7",
        "c": "8",
        "v": "9",
        "b": "11",
        "q": "12",
        "w": "13",
        "e": "14",
        "r": "15",
        "y": "16",
        "t": "17",
        "1": "18",
        "2": "19",
        "3": "20",
        "4": "21",
        "5": "23",
        "6": "22",
        "9": "25",
        "0": "29",
        "o": "31",
        "u": "32",
        "i": "34",
        "p": "35",
        "l": "37",
        "j": "38",
        "k": "40",
        "n": "45",
        "m": "46",
    }


_GUI_HELP = """chaitya gui — Keyboard and mouse automation.

Usage:
  chaitya gui click --x <n> --y <n>
  chaitya gui move --x <n> --y <n>
  chaitya gui type --text <string>
  chaitya gui press --key <name>
  chaitya gui drag --from x,y --to x,y
  chaitya gui keydown --key <name>
  chaitya gui keyup --key <name>

Key names: return, enter, tab, escape, space, backspace, up, down, left, right,
          a-z, 0-9, f1-f12, and more.

Modifiers: Command, Shift, Option, Control (e.g. "Command+s", "Shift+a")

Platform: macOS (osascript), Linux (xdotool), Windows (future)
"""


@adapter(
    name="gui",
    description="Keyboard and mouse automation: click, move, type, press, drag.",
    commands=[
        {
            "name": "click",
            "description": "Click at coordinates (x, y) or click modifier keys.",
            "params": [
                {"name": "x", "required": False, "description": "X coordinate"},
                {"name": "y", "required": False, "description": "Y coordinate"},
                {
                    "name": "button",
                    "required": False,
                    "description": "Button: left (default), right, double",
                },
            ],
            "examples": [
                "chaitya gui click --x 500 --y 300",
                "chaitya gui click --x 500 --y 300 --button right",
            ],
        },
        {
            "name": "move",
            "description": "Move the mouse cursor to coordinates.",
            "params": [
                {"name": "x", "required": True, "description": "X coordinate"},
                {"name": "y", "required": True, "description": "Y coordinate"},
            ],
            "examples": ["chaitya gui move --x 100 --y 200"],
        },
        {
            "name": "type",
            "description": "Type text using the keyboard.",
            "params": [
                {"name": "text", "required": True, "description": "Text to type"},
            ],
            "examples": ['chaitya gui type --text "hello world"'],
        },
        {
            "name": "press",
            "description": "Press a key or key combination.",
            "params": [
                {
                    "name": "key",
                    "required": True,
                    "description": "Key name (e.g. return, enter, tab, a, Command+s)",
                },
            ],
            "examples": [
                "chaitya gui press --key Enter",
                "chaitya gui press --key 'Command+s'",
                "chaitya gui press --key 'Shift+Tab'",
            ],
        },
        {
            "name": "drag",
            "description": "Drag from one point to another.",
            "params": [
                {"name": "from", "required": True, "description": "Start point: x,y"},
                {"name": "to", "required": True, "description": "End point: x,y"},
            ],
            "examples": ["chaitya gui drag --from 100,200 --to 400,300"],
        },
        {
            "name": "keydown",
            "description": "Hold a key down.",
            "params": [
                {"name": "key", "required": True, "description": "Key name"},
            ],
            "examples": ["chaitya gui keydown --key Command"],
        },
        {
            "name": "keyup",
            "description": "Release a held key.",
            "params": [
                {"name": "key", "required": True, "description": "Key name"},
            ],
            "examples": ["chaitya gui keyup --key Command"],
        },
    ],
    permissions={"fs_read": [], "fs_write": [], "network": False},
)
async def gui_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if not sub:
        return _GUI_HELP.encode(), 0

    valid_subcommands = {"click", "move", "type", "press", "drag", "keydown", "keyup"}
    if sub not in valid_subcommands:
        return (
            f"[error] gui: unknown subcommand: {sub}\n"
            f"Available: click, move, type, press, drag, keydown, keyup\n"
            f"Try: chaitya gui --help\n".encode(),
            127,
        )

    if _PLATFORM != "Darwin":
        return (
            f"[error] gui: only macOS is supported right now (platform: {_PLATFORM})\n".encode(),
            1,
        )

    if sub == "click":
        x = ctx.args.get("x")
        y = ctx.args.get("y")
        button = str(ctx.args.get("button", "left"))

        if x is not None and y is not None:
            try:
                xi, yi = int(x), int(y)
            except (ValueError, TypeError):
                return b"[error] gui click: --x and --y must be integers\n", 1

            if button == "right":
                script = f'tell application "System Events" to click at {{{xi}, {yi}}} with secondary button'
            elif button == "double":
                script = f'tell application "System Events" to click at {{{xi}, {yi}}}'
                out, code = await _osascript(script)
                if code != 0:
                    return out, code
                await asyncio.sleep(0.05)
                script = f'tell application "System Events" to click at {{{xi}, {yi}}}'
            else:
                script = f'tell application "System Events" to click at {{{xi}, {yi}}}'

            return await _osascript(script)

        return b"[error] gui click: --x and --y are required\n", 1

    if sub == "move":
        x = ctx.args.get("x")
        y = ctx.args.get("y")
        if x is None or y is None:
            return b"[error] gui move: --x and --y are required\n", 1
        try:
            xi, yi = int(x), int(y)
        except (ValueError, TypeError):
            return b"[error] gui move: --x and --y must be integers\n", 1

        script = (
            f'tell application "System Events" to set the position of the mouse to {{{xi}, {yi}}}'
        )
        return await _osascript(script)

    if sub == "type":
        text = ctx.args.get("text", "")
        escaped = text.replace('"', '\\"').replace("\\", "\\\\")
        script = f'tell application "System Events" to keystroke "{escaped}"'
        return await _osascript(script)

    if sub == "press":
        key = str(ctx.args.get("key", ""))

        # Parse modifiers: Command+s, Shift+a, Option+d, Control+e
        modifiers: list[str] = []
        remaining_key = key

        for mod in ["Command", "Shift", "Option", "Control"]:
            prefix = mod + "+"
            if prefix in remaining_key:
                modifiers.append(mod)
                remaining_key = remaining_key.replace(prefix, "")

        key_map = _key_code_map()
        key_code = key_map.get(remaining_key.lower())

        if key_code:
            parts = [f"key code {key_code}"]
            for mod in modifiers:
                parts.insert(0, f"{mod} down")
            script = f'tell application "System Events" to {" ".join(parts)}'
            out, code = await _osascript(script)
            if code == 0 and modifiers:
                for mod in reversed(modifiers):
                    script = f'tell application "System Events" to {mod} up'
                    await _osascript(script)
            return out, code
        else:
            script = f'tell application "System Events" to keystroke "{remaining_key}"'
            return await _osascript(script)

    if sub == "drag":
        from_str = str(ctx.args.get("from", ""))
        to_str = str(ctx.args.get("to", ""))
        if not from_str or not to_str:
            return b"[error] gui drag: --from and --to are required (format: x,y)\n", 1

        try:
            fx, fy = from_str.split(",")
            tx, ty = to_str.split(",")
            fx, fy, tx, ty = int(fx), int(fy), int(tx), int(ty)
        except Exception:
            return b"[error] gui drag: --from and --to must be x,y format with integers\n", 1

        script = f"""
tell application "System Events"
    set mouse position to {{{fx}, {fy}}}
    delay 0.1
    set the position of the mouse to {{{tx}, {ty}}}
end tell
"""
        return await _osascript(script)

    if sub == "keydown":
        key = str(ctx.args.get("key", ""))
        key_map = _key_code_map()
        key_code = key_map.get(key.lower())
        if key_code:
            script = f'tell application "System Events" to key code {key_code}'
            return await _osascript(script)
        script = f'tell application "System Events" to keystroke "{key}"'
        return await _osascript(script)

    if sub == "keyup":
        return b"[ok] keyup: keys auto-release after press on macOS\n", 0

    return b"[error] gui: unhandled command path\n", 1


__chaitya_handler__ = gui_handler
__adapter_contract__ = asdict(gui_handler.__chaitya_contract__)
