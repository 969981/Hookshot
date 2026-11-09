# Automated tests through MCP

`scripts/hookshot_mcp.py` is a [Model Context Protocol](https://modelcontextprotocol.io) server for automated in-game
tests. It launches a built Hookshot, drives the same timed controller input a test script uses, and asks the renderer
for a screenshot on demand. Screenshots come back as MCP image content, so an agent can look at the game after every
step.

The server is a separate Python process. It is not linked into `hookshot.exe` and needs no game files of its own.

## Install

The MCP server uses the official Python SDK (Python 3.10 or newer):

```powershell
python -m pip install "mcp==2.3.0"
python -I scripts/hookshot_mcp.py --check
```

`--check` prints the executable, image, romfs, save folder and run folder it would use, and exits non-zero if one is
missing.

The defaults are the standalone build:

| What | Default |
|---|---|
| Executable | `work/sa/build/hookshot.exe` (or `hookshot` on Linux) |
| Image | `work/sa/image/image.bin` |
| RomFS | the dump recorded in `work/dump.txt`, plus `/romfs` |
| Saves | `work/mcp/user` |
| Run files | `work/mcp/runs/<timestamp>-<pid>/` |
| Shader/pipeline caches | `work/mcp/cache/` |

For a game folder made by HookshotBuilder or `nx2/make_game.py`, pass `--game-dir`, or set
`HOOKSHOT_MCP_GAME_DIR`. Individual paths can be set with `--exe`, `--image`, `--romfs`, `--user` or their
`HOOKSHOT_MCP_*` environment variables.

## MCP host configuration

OpenCode (`opencode.json`, V2): servers live under `mcp.servers`, and a server is connected unless it is `disabled`.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "hookshot": {
        "type": "local",
        "command": ["python", "-I", "C:/path/to/Hookshot/scripts/hookshot_mcp.py"],
        "environment": {
          "HOOKSHOT_MCP_GAME_DIR": "C:/Games/Hookshot"
        }
      }
    }
  }
}
```

Claude Desktop:

```json
{
  "mcpServers": {
    "hookshot": {
      "command": "python",
      "args": ["-I", "C:/path/to/Hookshot/scripts/hookshot_mcp.py", "--game-dir", "C:/Games/Hookshot"]
    }
  }
}
```

The script finds the repository from its own location, so the host's working directory does not matter. Use an
absolute path to the script.

## Tools

| Tool | What it does |
|---|---|
| `launch` | Start a run. Headless, muted, Vulkan and script-only by default. |
| `stop` | Ask the game to quit (`!quit`), then terminate if it does not. Safe when nothing is running. |
| `status` | Process state, renderer, last screenshot and recent log lines. |
| `press` | Press or hold Switch buttons: `A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN`, plus the stick direction names `LUP` ... `RRIGHT`. |
| `stick` | Move the left or right analog stick with `x`/`y` in `-1..1` (positive y is up). |
| `sequence` | Queue a timed multi-step input sequence in one live input line. |
| `screenshot` | Capture the current presented frame as a PNG (1280x720 on Vulkan), inline and on disk. |
| `wait` | Wait for wall time while the game runs. |
| `logs` | Return recent log lines, optionally filtered. |
| `settings` | Every setting the Insert menu shows, the cheat rows when the cheat menu is on, and the lists they use. |
| `set_setting` | Change one of the player's settings by its key, as the menu does; kept in `hookshot_options.ini`. |
| `cheat` | Act on a cheat row (Gameplay → Cheats) directly: toggles, time, teleport, age, pouch and inventory. |

Resources: `hookshot://log`, `hookshot://last-screenshot` and `hookshot://status`.

A typical session:

```text
launch(wait_ready=true)
press(["PLUS"], hold_ms=200)
wait(800)
screenshot()
sequence([{"at_ms": 0, "buttons": ["A"], "hold_ms": 200}])
settings()
set_setting("text_speed", 3)
cheat("heart_protection", 1)
stop()
```

`press` waits for the game to acknowledge the live input line, then for the hold plus `settle_ms`. For a long hold
where screenshots are wanted in the middle, pass `wait=false`, then call `wait` and `screenshot` at the chosen times.

## Launch options

| Option | Meaning |
|---|---|
| `game_dir`, `exe`, `image`, `romfs`, `user` | Paths for this run. |
| `run_dir`, `cache_dir` | Where the log/screenshots and the shader caches go. |
| `window`, `focus`, `window_size` | Show the game window; headless otherwise. |
| `audio` | Play sound. Off by default. |
| `backend` | `auto`, `vulkan` or `webgpu`. |
| `script_only` | Ignore physical pads, keyboard and mouse (default true). |
| `frame_clock` | Advance scripted input at 60 presents per second instead of wall time. |
| `exclusive` | Do not merge physical pads while a press is active. |
| `env_file` | Read `hookshot_env.txt` first (the server then overrides its own control switches). |
| `fresh_user` | Use a save folder inside this run instead of `work/mcp/user`. |
| `extra_env` | Extra switches such as `RX_VK_FLASH=1`. The server's control switches are reserved. |
| `cheats` | Turn the cheat menu on (`HOS_OOT_CHEATS=1`) so `cheat` works. Off by default, as the game is. |
| `force` | Start even if another `hookshot` process is detected. |
| `wait_ready` | Wait for the first screenshot before `launch` returns. |

## Settings and cheats

`settings()` reads the Insert menu: every row of its Graphics, Gameplay and Controls tabs (the row's key, its label, the
value, its range, and the text the player sees), and — when the cheat menu is on — the Cheats page's rows with the
teleport destinations, the pouch's items and the inventory's items and counts. The keys are the names
`hookshot_options.ini` uses.

`set_setting(name, value)` goes through the menu's own setter: it clamps to the row's range, applies a quality preset or a
control layout the way the menu does, marks the settings Custom, and writes `hookshot_options.ini`. A row whose text says
"(restart)" keeps its value but takes effect at the next launch, exactly as when a player changes it.

```text
settings()                               # the rows, their values and their ranges
set_setting("frame_generation", 0)               # frame generation off
set_setting("resolution", 1)             # a fixed 100%, not Auto
set_setting("text_speed", 3)             # instant dialogue
set_setting("show_fps", 1)
```

`cheat(name, value=0, item=None)` acts on the cheat menu directly. The game keeps that menu behind `HOS_OOT_CHEATS=1`, so
the run must be started with `launch(cheats=true)` (or with the setting in `hookshot_env.txt`, which the server reads).

| Cheat | What to pass |
|---|---|
| `heart_protection`, `no_clip_flight`, `free_camera` | `value` 1 on, 0 off |
| `mark_position`, `return_to_mark`, `switch_age` | nothing else |
| `set_time` | `value`, the hour 0 to 23 |
| `teleport` | `item`, a destination's name or index (from `settings()`) |
| `pouch` | `item`, a pouch item, and `value`, the amount to add or remove (-10 to 10) |
| `inventory` | `item`, an inventory item, and `value`, the count wanted (-1 unlocks or relocks it) |

```text
launch(cheats=true, wait_ready=true)
cheat("heart_protection", 1)
cheat("set_time", 18)
cheat("teleport", item="Kakariko Village")
cheat("inventory", item="Hookshot")          # unlocked
cheat("inventory", item="Bombs", value=20)   # twenty bombs
cheat("pouch", item="Arrows", value=5)
```

The server writes these as live input lines (`!settings`, `!setting <key> <value>`, `!cheat <name> <value> <extra>`) and
the game answers in its log, which the server reads back. Settings and cheats are therefore the Vulkan back end's: the
WebGPU fallback has no Insert menu and `settings()` says so. A cheat acts on the game in play; at the title screen there is
no player to give hearts or a destination to travel to yet.

## How it works

The server sets these for the game:

```text
HOS_INPUT_LIVE=<run>/input.txt
HOS_INPUT_SCRIPT_ONLY=1
HOS_INPUT_SCRIPT_BACKGROUND=1
HOS_INPUT_EXCLUSIVE=1
HOS_HEADLESS=1              (unless window=true)
HOS_AUDIO=0                 (unless audio=true)
HOS_OOT_CHEATS=1            (when cheats=true)
RX_BACKEND=vulkan           (or webgpu)
HOOKSHOT_NO_ENV_FILE=1
RX_VK_SHOT_FILE=<run>/shot.request
RX_SCREENSHOT_FILE=<run>/shot.request
```

`press`, `stick` and `sequence` append lines in the game's existing script syntax. Analog values use the `LX=`, `LY=`,
`RX=` and `RY=` tokens added for this server. A screenshot writes the requested output path into `shot.request`; the
Vulkan renderer (or the WebGPU fallback) deletes the request, writes the PNG on its next present, and logs
`vk: present <n> written to <path>` (or `rx: screenshot ...`). The server waits for that line, returns the image, and
reads the present/frame number from it.

`HOOKSHOT_NO_ENV_FILE=1` stops the game from applying `hookshot_env.txt` a second time. The server reads that file
first and then applies its own control switches, so useful test switches survive without letting a stale
`HOS_HEADLESS` or `HOS_INPUT_LIVE` take over.

The game's own `hookshot_options.ini` still applies: frame generation, resolution and similar settings can change what
a screenshot looks like. Use a dedicated game folder or a clean options file when a test must be reproducible.

## Safety and limits

- Keep game files, saves and screenshots out of git. The default run folder is under `work/`, which is ignored.
- Only one game runs at a time; `launch` refuses when another `hookshot` process is found unless `force=true`.
- The server uses the game's ANSI file APIs: run, screenshot and game paths should be ASCII.
- A screenshot is the game's presented frame, not the game window. Renderer overlays (the Insert menu, the FPS
  counter) are not in it.
- Input is controller input. The keyboard and mouse mappings are not simulated.
- Live input is applied when the game next reads the controller; a loading screen can delay a queued press.
- A cold launch can take a minute or two while shaders are prepared. `wait_ready` returns on the first presented frame, which
  on a cold cache can be a black boot frame; call it again, or `wait` and `screenshot` until the title appears, before
  asserting on the picture. `wait_ready` has its own timeout; call it again if the first call times out.
- The server is developer tooling in `scripts/`, not a file shipped with the game.

## Game-free test

```powershell
python -I -B tests/test_mcp_server.py
```

The test speaks MCP to the server in memory, checks the input-line encoding and environment handling, and runs a
fake Python game that answers live input and screenshot requests exactly as the host does. It needs the `mcp` package
but no game build, GPU or game files.

`tests/test_shot_request.py` compiles the renderer's `read_shot_request()` and checks the request-file rules, and
`tests/test_docs.py` checks this page's tool/resource tables against the server. Both are part of
`python -I -B tests/run_all.py --strict`.
