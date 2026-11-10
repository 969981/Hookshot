"""A stand-in for the game: reads the live input file and the screenshot request exactly as the host does.

    python -I tests/fake_game.py <image> --romfs <folder> --user <folder>

The MCP server's tests launch this in place of `hookshot.exe` (exe = the Python running the tests, image = this file).
It needs no game build, GPU or game files. It answers the live input lines, the settings and cheat commands and the
one-shot screenshot request with the same log lines as `host/nn_hid.c` and the renderer, so the server's parsing is
tested without a game. `tests/test_mcp_server.py` and `tests/test_mcp_scenario.py` use it.
"""
import base64
import os
import sys
import time
from pathlib import Path

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

SETTINGS = (
    "mcp: settings begin",
    "mcp: value|frame_generation|Frame generation|graphics|2|0|8|1|2x",
    "mcp: value|text_speed|Text speed|gameplay|0|0|3|1|Normal",
    "mcp: settings end",
    "mcp: cheats on",
    "mcp: cheats begin",
    "mcp: cheatrow|heart_protection|Heart protection|toggle|0|Off",
    "mcp: cheatrow|teleport|Teleport|choice|0|Kokiri Forest",
    "mcp: cheatrow|inventory|Inventory|action|0|",
    "mcp: cheats end",
    "mcp: pouch begin", "mcp: pouch|0|Deku Seeds", "mcp: pouch end",
    "mcp: teleports begin", "mcp: teleport|0|Kokiri Forest",
    "mcp: teleport|1|Hyrule Field", "mcp: teleports end",
    "mcp: items begin", "mcp: item|0|Kokiri Sword|0|1", "mcp: items end",
    "mcp: dump end",
)


def say(text):
    print(f"[hos fake] {text}", file=sys.stderr, flush=True)


def main():
    live = Path(os.environ["HOS_INPUT_LIVE"])
    request = Path(os.environ.get("RX_VK_SHOT_FILE") or os.environ["RX_SCREENSHOT_FILE"])
    say("started")
    offset = 0
    while True:
        try:
            data = live.read_bytes()
        except OSError:
            data = b""
        if len(data) > offset:
            chunk = data[offset:]
            offset = len(data)
            for line in chunk.decode("ascii", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                say(f"hid: live input at 0.1 s: {line}")
                if line.startswith("!"):
                    say(f"hid: live command at 0.1 s: {line}")
                if line == "!settings":
                    for text in SETTINGS:
                        say(text)
                if line.startswith("!setting "):
                    parts = line.split()
                    key = parts[1] if len(parts) > 1 else ""
                    value = parts[2] if len(parts) > 2 else "0"
                    say(f"mcp: setting ok|{key}|{value}")
                if line.startswith("!cheat "):
                    parts = line.split()
                    name = parts[1] if len(parts) > 1 else ""
                    value = parts[2] if len(parts) > 2 else "0"
                    say(f"mcp: cheat ok|{name}|{value}")
                if line.startswith("!quit"):
                    raise SystemExit(0)
        if request.exists():
            try:
                out = Path(request.read_text(encoding="utf-8").strip())
                request.unlink()
                out.write_bytes(PNG)
                say(f"vk: present 7 written to {out}")
            except FileNotFoundError:
                pass
        time.sleep(0.01)


if __name__ == "__main__":
    main()
