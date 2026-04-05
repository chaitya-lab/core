"""Desktop adapter — Local desktop access: screenshot, clipboard, accessibility tree.

Design (PRD Phase 2):
  - screenshot: captures the screen using native tools (screencapture on macOS)
  - clipboard: read/write system clipboard (pbcopy/pbpaste on macOS)
  - tree: inspect UI hierarchy using OS accessibility APIs

macOS uses:
  - screencapture for screenshots
  - pbcopy/pbpaste for clipboard
  - osascript (AppleScript) for accessibility tree on macOS

Linux uses:
  - scrot/gnome-screenshot for screenshots
  - xclip/xsel for clipboard
  - at-spi2 / accessibility bus for tree (if available)

Windows uses:
  - Pillow/PIL for screenshots
  - subprocess clipboard commands
  - pywin32 for accessibility
"""

from __future__ import annotations

import asyncio
import base64
import json
import platform
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from chaitya_sdk import ChaityaStream, SessionContext, adapter


_PLATFORM = platform.system()


def _platform_error(msg: str) -> tuple[bytes, int]:
    return f"[error] desktop: {msg} (platform: {_PLATFORM})\n".encode(), 1


def _run_sync(cmd: list[str], timeout: float = 10.0) -> tuple[bytes, int]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        return proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return f"[error] desktop: command timed out after {timeout}s\n".encode(), 1
    except FileNotFoundError:
        return f"[error] desktop: command not found: {cmd[0]}\n".encode(), 1
    except Exception as exc:
        return f"[error] desktop: {exc}\n".encode(), 1


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


async def _clipboard_read() -> tuple[bytes, int]:
    if _PLATFORM == "Darwin":
        proc = await asyncio.create_subprocess_shell(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return stderr, 1
        return stdout, 0

    if _PLATFORM == "Linux":
        for cmd in [
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ]:
            proc = await asyncio.create_subprocess_shell(
                " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout, 0

        return b"[error] desktop clipboard: xclip/xsel not available\n", 1

    if _PLATFORM == "Windows":
        proc = await asyncio.create_subprocess_shell(
            "powershell -Command Get-Clipboard",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout if proc.returncode == 0 else stderr, proc.returncode

    return _platform_error("clipboard not supported on this platform")


async def _clipboard_write(text: str) -> tuple[bytes, int]:
    if _PLATFORM == "Darwin":
        proc = await asyncio.create_subprocess_shell(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(input=text.encode("utf-8"))
        if proc.returncode != 0:
            return stderr, 1
        return b"[ok] copied to clipboard\n", 0

    if _PLATFORM == "Linux":
        safe = text.replace("\\", "\\\\").replace("'", "'\"'\"'")
        for cmd_str in [
            f"echo -n '{safe}' | xclip -selection clipboard",
            f"echo -n '{safe}' | xsel --clipboard --input",
        ]:
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return b"[ok] copied to clipboard\n", 0
        return b"[error] desktop clipboard: xclip/xsel not available\n", 1

    if _PLATFORM == "Windows":
        proc = await asyncio.create_subprocess_shell(
            f'powershell -Command "Set-Clipboard -Value \\"{text}\\""',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return stderr, 1
        return b"[ok] copied to clipboard\n", 0

    return _platform_error("clipboard write not supported on this platform")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


async def _screenshot(path: str | None, region: str | None) -> tuple[bytes, int]:
    tmp_path = path

    if not tmp_path:
        tmp_path = f"/tmp/chaitya-screenshot-{int(time.time())}.png"

    if _PLATFORM == "Darwin":
        args = ["screencapture", "-x"]
        if region:
            try:
                x, y, w, h = [int(v) for v in region.split(",")]
                args.extend(["-R", f"{x},{y},{w},{h}"])
            except Exception:
                return (
                    f"[error] desktop screenshot: invalid region format (use x,y,w,h)\n".encode(),
                    1,
                )
        args.append(tmp_path)
        data, code = _run_sync(args, timeout=15.0)
        if code != 0:
            return data, code
        try:
            img_data = Path(tmp_path).read_bytes()
            b64 = base64.b64encode(img_data).decode("ascii")
            if path:
                return f"[ok] screenshot saved to {path} ({len(img_data)} bytes)\n".encode(), 0
            return f"data:image/png;base64,{b64}\n".encode(), 0
        except Exception as exc:
            return f"[error] desktop screenshot: {exc}\n".encode(), 1

    if _PLATFORM == "Linux":
        for cmd in [
            ["scrot", tmp_path],
            ["gnome-screenshot", "-f", tmp_path],
            ["import", "-window", "root", tmp_path],
        ]:
            data, code = _run_sync(cmd, timeout=15.0)
            if code == 0:
                try:
                    img_data = Path(tmp_path).read_bytes()
                    b64 = base64.b64encode(img_data).decode("ascii")
                    if path:
                        return (
                            f"[ok] screenshot saved to {path} ({len(img_data)} bytes)\n".encode(),
                            0,
                        )
                    return f"data:image/png;base64,{b64}\n".encode(), 0
                except Exception as exc:
                    return f"[error] desktop screenshot: {exc}\n".encode(), 1
        return (
            b"[error] desktop screenshot: scrot/gnome-screenshot/import not available\n",
            1,
        )

    if _PLATFORM == "Windows":
        try:
            from PIL import Image, ImageGrab

            img = ImageGrab.grab()
            img.save(tmp_path)
            img_data = Path(tmp_path).read_bytes()
            b64 = base64.b64encode(img_data).decode("ascii")
            if path:
                return f"[ok] screenshot saved to {path} ({len(img_data)} bytes)\n".encode(), 0
            return f"data:image/png;base64,{b64}\n".encode(), 0
        except ImportError:
            return b"[error] desktop screenshot: Pillow not installed (pip install Pillow)\n", 1
        except Exception as exc:
            return f"[error] desktop screenshot: {exc}\n".encode(), 1

    return _platform_error("screenshot not supported on this platform")


# ---------------------------------------------------------------------------
# Accessibility Tree
# ---------------------------------------------------------------------------


async def _accessibility_tree() -> tuple[bytes, int]:
    if _PLATFORM == "Darwin":
        script = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    set winCount to count of windows of frontApp
    
    set output to "Application: " & appName & return
    repeat with w from 1 to winCount
        set winName to name of window w of frontApp
        set output to output & "  Window " & w & ": " & winName & return
        
        -- Get UI elements of the window
        try
            tell window w of frontApp
                set elemCount to count of UI elements
                repeat with e from 1 to min(elemCount, 20)
                    set elemRole to role of UI element e
                    set elemName to name of UI element e
                    set elemValue to value of UI element e
                    if elemName is not "" then
                        set output to output & "    [" & elemRole & "] " & elemName
                        if elemValue is not "" then
                            set output to output & " = " & elemValue
                        end if
                        set output to output & return
                    end if
                end repeat
            end tell
        end try
    end repeat
    return output
end tell
"""
        proc = await asyncio.create_subprocess_shell(
            f"osascript -e '{script}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return (
                b"[error] desktop tree: accessibility denied or not available.\n"
                b"Hint: System Settings > Privacy & Security > Accessibility > enable terminal/shell\n",
            ), 1
        return stdout.strip() + b"\n", 0

    if _PLATFORM == "Linux":
        return (
            b"[error] desktop tree: Linux accessibility tree not yet implemented.\n"
            b"Consider using: at-spi2-atk, at-spi2-core, or accerciser.\n",
            1,
        )

    if _PLATFORM == "Windows":
        return (
            b"[error] desktop tree: Windows accessibility tree not yet implemented.\n"
            b"Consider using: pywin32 or UI Automation APIs.\n",
            1,
        )

    return _platform_error("accessibility tree not supported on this platform")


_DESKTOP_HELP = """chaitya desktop — Local desktop access.

Usage:
  chaitya desktop screenshot [--path <file>] [--region x,y,w,h]
  chaitya desktop clipboard read
  chaitya desktop clipboard write --text <content>
  chaitya desktop tree

Platform: auto-detected (macOS/Linux/Windows)
"""


@adapter(
    name="desktop",
    description="Local desktop access: screenshot, clipboard read/write, accessibility tree.",
    commands=[
        {
            "name": "screenshot",
            "description": "Capture the screen. Returns base64 PNG or saves to file.",
            "params": [
                {
                    "name": "path",
                    "required": False,
                    "description": "File path to save PNG (default: return base64)",
                },
                {
                    "name": "region",
                    "required": False,
                    "description": "Region as x,y,w,h (macOS only)",
                },
            ],
            "examples": [
                "chaitya desktop screenshot",
                "chaitya desktop screenshot --path /tmp/screen.png",
                "chaitya desktop screenshot --region 0,0,800,600",
            ],
        },
        {
            "name": "clipboard",
            "description": "Read or write the system clipboard.",
            "params": [
                {
                    "name": "action",
                    "required": False,
                    "description": "read or write (default: read)",
                },
                {
                    "name": "text",
                    "required": False,
                    "description": "Text to write to clipboard (for action=write)",
                },
            ],
            "examples": [
                "chaitya desktop clipboard read",
                "chaitya desktop clipboard write --text 'hello world'",
            ],
        },
        {
            "name": "tree",
            "description": "Inspect the UI hierarchy of the frontmost window (macOS).",
            "params": [],
            "examples": ["chaitya desktop tree"],
        },
    ],
    permissions={"fs_read": ["."], "fs_write": ["/tmp"], "network": False},
)
async def desktop_handler(
    stream: ChaityaStream,
    ctx: SessionContext,
) -> tuple[bytes, int]:
    sub = str(ctx.args.get("subcommand", ""))

    if not sub:
        return _DESKTOP_HELP.encode(), 0

    if sub == "screenshot":
        return await _screenshot(
            ctx.args.get("path"),
            ctx.args.get("region"),
        )

    if sub == "clipboard":
        action = str(ctx.args.get("action", "read"))
        if action == "write":
            text = ctx.args.get("text", "")
            return await _clipboard_write(str(text))
        return await _clipboard_read()

    if sub == "tree":
        return await _accessibility_tree()

    return (
        f"[error] desktop: unknown subcommand: {sub}\n"
        f"Available: screenshot, clipboard, tree\n"
        f"Try: chaitya desktop --help\n".encode(),
        127,
    )


__chaitya_handler__ = desktop_handler
__adapter_contract__ = asdict(desktop_handler.__chaitya_contract__)
