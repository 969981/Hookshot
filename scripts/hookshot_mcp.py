"""Hookshot MCP server: scripted in-game tests with screenshots.

The server launches a built Hookshot, drives the game's live controller input file, asks the renderer for one PNG per
screenshot, and reads the game log for acknowledgements. It speaks MCP over stdio, so every host that supports local
MCP servers can use it (OpenCode, Claude Desktop, the MCP Inspector, ...).

Setup (from the repository root):

    python -m pip install "mcp==2.3.0"
    python -I scripts/hookshot_mcp.py --check

Then point the MCP host at:

    python -I C:/path/to/Hookshot/scripts/hookshot_mcp.py

The default build is work/sa/build/hookshot.exe with work/sa/image/image.bin and the romfs named by work/dump.txt.
Pass --game-dir (or --exe/--image/--romfs) for a game folder made by HookshotBuilder or make_game.py.

The game-side switches this uses:

    HOS_INPUT_LIVE       timed pad presses, appended while the game runs
    HOS_INPUT_SCRIPT_ONLY  the test sees no physical pad, keyboard or mouse
    RX_VK_SHOT_FILE      one PNG of the next Vulkan present (path in the file)
    RX_SCREENSHOT_FILE   the same for the WebGPU fallback
    !quit                a clean stop, written to the live input file

Screenshots and saves stay under work/mcp and are ignored by git. The game is never asked to load a file from outside
the paths the caller gave it.
"""

from __future__ import annotations

import argparse
import atexit
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import TextContent

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
DEFAULT_TIMEOUT_MS = 30_000

SCRIPT_BUTTONS = {
    "A", "B", "X", "Y", "L", "R", "ZL", "ZR", "PLUS", "MINUS", "LS", "RS", "LEFT", "UP", "RIGHT", "DOWN",
    "LUP", "LDOWN", "LLEFT", "LRIGHT", "RUP", "RDOWN", "RLEFT", "RRIGHT",
}
STICK_AXES = {"left": ("LX", "LY"), "right": ("RX", "RY")}

# The server owns these; a caller's extra_env must not break its control of the run.
CONTROL_ENV = {
    "HOOKSHOT_NO_ENV_FILE", "RX_BACKEND", "HOS_HEADLESS", "HOS_WINDOW_NOACTIVATE", "HOS_WINDOW_SIZE", "HOS_AUDIO",
    "HOS_INPUT_LIVE", "HOS_INPUT_SCRIPT_ONLY", "HOS_INPUT_SCRIPT_BACKGROUND", "HOS_INPUT_EXCLUSIVE",
    "HOS_INPUT_FRAME_CLOCK", "RX_VK_SHOT_FILE", "RX_SCREENSHOT_FILE",
}


def log(message: str) -> None:
    """Server diagnostics go to stderr: stdout is the MCP wire."""
    print(f"[hookshot-mcp] {message}", file=sys.stderr, flush=True)


def _strip_line(value: str) -> str:
    value = value.strip()
    if value.startswith("\ufeff"):
        value = value[1:]
    return value


def read_env_file(exe: Path) -> dict[str, str]:
    """The NAME=VALUE lines the game itself would read from hookshot_env.txt beside the executable."""
    path = exe.parent / "hookshot_env.txt"
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for raw in text.splitlines():
        line = _strip_line(raw)
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def dump_romfs() -> Path | None:
    """The dump folder recorded by nx2/dump.py --set, or None."""
    try:
        lines = (ROOT / "work" / "dump.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#"):
            return Path(line) / "romfs"
    return None


def exe_name() -> str:
    return "hookshot.exe" if os.name == "nt" else "hookshot"


def game_exe(game_dir: Path | None) -> Path | None:
    """The game in a folder made by HookshotBuilder or make_game.py: in bin\\, because that folder's own hookshot.exe is the
    launcher that opens the installed builder (builder/setup/setup.c), and starting it would not start the game. A
    development build has the game at the top of the folder."""
    if game_dir is None:
        return None
    nested = game_dir / "bin" / exe_name()
    return nested if nested.is_file() else game_dir / exe_name()


def resolve_paths(cfg: "Config", opts: "LaunchOptions") -> dict[str, Path]:
    game_dir = _path(opts.game_dir) or cfg.game_dir
    exe = _path(opts.exe) or cfg.exe or game_exe(game_dir) or ROOT / "work" / "sa" / "build" / exe_name()
    image = (_path(opts.image) or cfg.image or (game_dir / "image.bin" if game_dir else None)
             or ROOT / "work" / "sa" / "image" / "image.bin")
    romfs = _path(opts.romfs) or cfg.romfs or (game_dir / "romfs" if game_dir else None) or dump_romfs()
    run_dir = _path(opts.run_dir) or cfg.work_dir / "runs" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}")
    cache_dir = _path(opts.cache_dir) or cfg.work_dir / "cache"
    if opts.fresh_user and not opts.user and not cfg.user:
        user = run_dir / "user"
    else:
        user = _path(opts.user) or cfg.user or cfg.work_dir / "user"
    if romfs is None:
        raise ToolError("romfs folder not found: pass --romfs or --game-dir, or run nx2/dump.py --set")
    paths = {"exe": exe, "image": image, "romfs": romfs, "user": user, "run_dir": run_dir, "cache_dir": cache_dir}
    paths = {k: v.resolve() for k, v in paths.items()}
    if not paths["exe"].is_file():
        raise ToolError(f"hookshot executable not found: {paths['exe']} (build with nx2/build.py --standalone, or pass --exe)")
    if not paths["image"].is_file():
        raise ToolError(f"image.bin not found: {paths['image']} (pass --image, or --game-dir for a built game folder)")
    if not paths["romfs"].is_dir():
        raise ToolError(f"romfs folder not found: {paths['romfs']} (pass --romfs, or run nx2/dump.py --set)")
    for key in ("run_dir", "cache_dir", "user"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def _path(value: str | Path | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value).expanduser()


def _ascii(path: Path) -> Path:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ToolError(f"path must be ASCII for the game's file APIs: {path}") from exc
    return path


def other_game_running() -> str | None:
    """Best effort: another hookshot is using about 10 GB, so do not start a second one."""
    name = exe_name()
    try:
        if os.name == "nt":
            result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
                                    capture_output=True, text=True, timeout=5)
            if name.lower() in (result.stdout or "").lower():
                return name
        else:
            result = subprocess.run(["pgrep", "-x", Path(name).stem], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return Path(name).stem
    except (OSError, subprocess.SubprocessError):
        return None
    return None


@dataclass
class Config:
    work_dir: Path = field(default_factory=lambda: ROOT / "work" / "mcp")
    exe: Path | None = None
    image: Path | None = None
    romfs: Path | None = None
    game_dir: Path | None = None
    user: Path | None = None
    backend: Literal["auto", "vulkan", "webgpu"] = "auto"
    env_file: bool = True


class Step(BaseModel):
    """One press in a timed sequence."""

    at_ms: Annotated[int, Field(ge=0, le=600_000, description="Milliseconds after the game reads the line.")]
    buttons: Annotated[list[str], Field(default_factory=list, description="Switch buttons, e.g. ['A'] or ['PLUS'].")]
    stick: Annotated[Literal["left", "right"] | None, Field(description="Move this stick with x/y.")] = None
    x: Annotated[float, Field(ge=-1.0, le=1.0, description="Horizontal stick deflection; positive is right.")] = 0.0
    y: Annotated[float, Field(ge=-1.0, le=1.0, description="Vertical stick deflection; positive is up.")] = 0.0
    hold_ms: Annotated[int, Field(ge=1, le=600_000, description="How long the press is held.")] = 200


@dataclass
class LaunchOptions:
    game_dir: str | None = None
    exe: str | None = None
    image: str | None = None
    romfs: str | None = None
    user: str | None = None
    run_dir: str | None = None
    cache_dir: str | None = None
    window: bool = False
    focus: bool = False
    window_size: str | None = None
    audio: bool = False
    backend: Literal["auto", "vulkan", "webgpu"] | None = None
    script_only: bool = True
    frame_clock: bool = False
    exclusive: bool = True
    env_file: bool | None = None
    fresh_user: bool = False
    extra_env: dict[str, str] | None = None
    force: bool = False
    wait_ready: bool = False
    ready_timeout_s: float = 180.0


class LogStream:
    """Pumps the game's stderr into a bounded ring and work/mcp/run/log.txt."""

    def __init__(self, path: Path):
        self.path = path
        self._lines: deque[tuple[int, str]] = deque(maxlen=4000)
        self._seq = 0
        self._closed = False
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._file = None
        self._on_line = None

    def start(self, stream, on_line=None) -> None:
        self._on_line = on_line
        self._file = open(self.path, "wb")
        self._thread = threading.Thread(target=self._pump, args=(stream,), name="hookshot-log", daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                with self._cond:
                    self._seq += 1
                    self._lines.append((self._seq, line))
                    self._cond.notify_all()
                if self._on_line is not None:
                    try:
                        self._on_line(line)
                    except Exception as exc:  # (a bad line must not stop the log)
                        log(f"log callback failed: {exc}")
                if self._file is not None:
                    self._file.write(raw)
                    self._file.flush()
        finally:
            with self._cond:
                self._closed = True
                self._cond.notify_all()
            if self._file is not None:
                self._file.close()
            try:
                stream.close()
            except OSError:
                pass

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    def wait_for(self, predicate, timeout: float, after: int = 0) -> str | None:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for seq, line in self._lines:
                    if seq > after and predicate(line):
                        return line
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    return None
                self._cond.wait(min(remaining, 0.25))

    def tail(self, n: int = 200) -> list[str]:
        with self._cond:
            return [line for _, line in list(self._lines)[-n:]]


class GameSession:
    def __init__(self, cfg: Config, opts: LaunchOptions):
        self.cfg = cfg
        self.opts = opts
        self.paths = resolve_paths(cfg, opts)
        self.run_dir = self.paths["run_dir"]
        self.shots_dir = self.run_dir / "shots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.input_path = self.run_dir / "input.txt"
        self.request_path = self.run_dir / "shot.request"
        self.log_path = self.run_dir / "log.txt"
        self.input_path.write_bytes(b"")
        try:
            self.request_path.unlink()
        except FileNotFoundError:
            pass
        self.proc: subprocess.Popen | None = None
        self.log_stream = LogStream(self.log_path)
        self.started_at: float | None = None
        self.exit_code: int | None = None
        self.renderer: str | None = None
        self.last_screenshot: dict[str, Any] | None = None
        self._shot_seq = 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def require_running(self) -> None:
        if not self.alive():
            raise ToolError("no game is running; call launch first")

    def build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in CONTROL_ENV:
            env.pop(key, None)
        use_file = self.cfg.env_file if self.opts.env_file is None else self.opts.env_file
        if use_file:
            env.update(read_env_file(self.paths["exe"]))
        # (the file may name these too: the server's own values go on last, once.)
        for key in CONTROL_ENV:
            env.pop(key, None)
        backend = self.opts.backend or self.cfg.backend
        env.update({
            "HOOKSHOT_NO_ENV_FILE": "1",
            "RX_BACKEND": "webgpu" if backend == "webgpu" else "vulkan",
            "HOS_AUDIO": "1" if self.opts.audio else "0",
            "HOS_INPUT_LIVE": str(self.input_path),
            "HOS_INPUT_SCRIPT_BACKGROUND": "1",
            "RX_VK_SHOT_FILE": str(self.request_path),
            "RX_SCREENSHOT_FILE": str(self.request_path),
        })
        if self.opts.exclusive:
            env["HOS_INPUT_EXCLUSIVE"] = "1"
        if self.opts.script_only:
            env["HOS_INPUT_SCRIPT_ONLY"] = "1"
        if self.opts.frame_clock:
            env["HOS_INPUT_FRAME_CLOCK"] = "1"
        if self.opts.window:
            env.pop("HOS_HEADLESS", None)
            if not self.opts.focus:
                env["HOS_WINDOW_NOACTIVATE"] = "1"
            if self.opts.window_size:
                env["HOS_WINDOW_SIZE"] = self.opts.window_size
        else:
            env["HOS_HEADLESS"] = "1"
        for key, value in (self.opts.extra_env or {}).items():
            if key in CONTROL_ENV:
                raise ToolError(f"extra_env may not set {key}; use the dedicated launch argument")
            env[str(key)] = str(value)
        return env

    def launch(self) -> dict[str, Any]:
        if not self.opts.force:
            other = other_game_running()
            if other:
                raise ToolError(f"another game is running ({other}); stop it or pass force=true")
        _ascii(self.paths["exe"])
        _ascii(self.paths["image"])
        _ascii(self.paths["romfs"])
        _ascii(self.paths["user"])
        _ascii(self.paths["cache_dir"])
        _ascii(self.input_path)
        _ascii(self.request_path)
        env = self.build_env()
        command = [str(self.paths["exe"]), str(self.paths["image"]),
                   "--romfs", str(self.paths["romfs"]), "--user", str(self.paths["user"])]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        log(f"launching {command[0]} (run {self.run_dir})")
        self.proc = subprocess.Popen(command, cwd=str(self.paths["cache_dir"]), env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=flags, start_new_session=os.name != "nt")
        assert self.proc.stderr is not None
        self.log_stream.start(self.proc.stderr, self.note_line)
        self.started_at = time.monotonic()
        deadline = self.started_at + 0.8
        while time.monotonic() < deadline and self.proc.poll() is None:
            time.sleep(0.02)
        if self.proc.poll() is not None:
            self.exit_code = self.proc.returncode
            time.sleep(0.2)
            tail = "\n".join(self.log_stream.tail(60))
            raise ToolError(f"hookshot exited at once (code {self.exit_code}); log:\n{tail}")
        return self.result()

    def result(self) -> dict[str, Any]:
        return {
            "pid": self.proc.pid if self.proc else None,
            "run_dir": str(self.run_dir),
            "log": str(self.log_path),
            "input": str(self.input_path),
            "shot_request": str(self.request_path),
            "cache": str(self.paths["cache_dir"]),
            "user": str(self.paths["user"]),
        }

    def _sleep_ms(self, ms: int) -> None:
        end = time.monotonic() + ms / 1000.0
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0 or not self.alive():
                return
            time.sleep(min(remaining, 0.05))

    def append_live(self, line: str, ack_timeout_ms: int = 1500) -> bool:
        self.require_running()
        before = self.log_stream.seq
        with open(self.input_path, "ab") as handle:
            handle.write((line + "\n").encode("ascii"))
        ack = self.log_stream.wait_for(
            lambda text: "hid: live input at" in text or "hid: live command at" in text,
            ack_timeout_ms / 1000.0, before)
        return ack is not None

    def press(self, buttons: list[str] | str, hold_ms: int = 200, settle_ms: int = 100, wait: bool = True) -> dict[str, Any]:
        names = normalize_buttons(buttons)
        line = f"0:{'+'.join(names)}/{hold_ms}"
        acked = self.append_live(line)
        if wait:
            self._sleep_ms(hold_ms + settle_ms)
        return {"line": line, "buttons": names, "held_ms": hold_ms, "acked": acked,
                "queued": True, "waited": wait}

    def move_stick(self, stick: Literal["left", "right"], x: float, y: float,
                   hold_ms: int = 200, settle_ms: int = 100, wait: bool = True) -> dict[str, Any]:
        tokens = stick_tokens(stick, x, y)
        line = f"0:{'+'.join(tokens)}/{hold_ms}"
        acked = self.append_live(line)
        if wait:
            self._sleep_ms(hold_ms + settle_ms)
        return {"line": line, "stick": stick, "x": x, "y": y, "held_ms": hold_ms,
                "acked": acked, "queued": True, "waited": wait}

    def sequence(self, steps: list[Step], settle_ms: int = 100, wait: bool = True) -> dict[str, Any]:
        line, duration = sequence_line(steps)
        acked = self.append_live(line)
        if wait:
            self._sleep_ms(duration + settle_ms)
        return {"line": line, "steps": len(steps), "duration_ms": duration, "acked": acked,
                "queued": True, "waited": wait}

    def next_shot_path(self, path: str | None) -> Path:
        if path:
            out = Path(path).expanduser()
            if not out.is_absolute():
                out = self.run_dir / out
        else:
            self._shot_seq += 1
            out = self.shots_dir / f"shot_{self._shot_seq:04d}.png"
        out = _ascii(out.resolve())
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    def screenshot(self, path: str | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict[str, Any]:
        self.require_running()
        out = self.next_shot_path(path)
        try:
            out.unlink()
        except FileNotFoundError:
            pass
        before = self.log_stream.seq
        tmp = self.request_path.with_name(self.request_path.name + ".tmp")
        tmp.write_text(str(out), encoding="utf-8")
        os.replace(tmp, self.request_path)
        deadline = time.monotonic() + timeout_ms / 1000.0
        ack: str | None = None
        last_size = -1
        while time.monotonic() < deadline:
            ack = self.log_stream.wait_for(
                lambda text: out.name in text and ("written to" in text or "rx: screenshot" in text),
                0.1, before)
            if ack:
                break
            if not self.alive():
                break
            if out.is_file():
                size = out.stat().st_size
                if size > 0 and size == last_size:
                    # (the PNG is written before the ack line: give the log a moment so `present` is filled in)
                    ack = self.log_stream.wait_for(
                        lambda text: out.name in text and ("written to" in text or "rx: screenshot" in text),
                        0.25, before)
                    break
                last_size = size
            time.sleep(0.02)
        if not out.is_file() or out.stat().st_size <= 0:
            try:
                self.request_path.unlink()
            except FileNotFoundError:
                pass
            tail = "\n".join(self.log_stream.tail(30))
            raise ToolError(f"no screenshot after {timeout_ms} ms (is the game presenting frames?); log:\n{tail}")
        info: dict[str, Any] = {"path": str(out), "bytes": out.stat().st_size, "present": None}
        if ack:
            match = re.search(r"present (\d+) written to", ack) or re.search(r"\(frame (\d+)\)", ack)
            if match:
                info["present"] = int(match.group(1))
        self.last_screenshot = info
        return info

    def stop(self, force: bool = False, timeout_s: float = 10.0) -> dict[str, Any]:
        if self.proc is None:
            return {"stopped": False, "already_stopped": True}
        if self.proc.poll() is None and not force:
            try:
                self.append_live("!quit", ack_timeout_ms=500)
            except ToolError:
                pass
            try:
                self.proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        self.exit_code = self.proc.returncode
        deadline = time.monotonic() + 1.0
        while not self.log_stream.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        self.proc = None
        return {"stopped": True, "exit_code": self.exit_code, "run_dir": str(self.run_dir)}

    def status(self) -> dict[str, Any]:
        alive = self.alive()
        return {
            "launched": self.proc is not None or self.started_at is not None,
            "running": alive,
            "pid": self.proc.pid if self.proc else None,
            "exit_code": None if alive else self.exit_code,
            "uptime_s": round(time.monotonic() - self.started_at, 2) if self.started_at and alive else None,
            "run_dir": str(self.run_dir),
            "log": str(self.log_path),
            "renderer": self.renderer,
            "last_screenshot": self.last_screenshot,
            "ready": self.last_screenshot is not None,
            "log_tail": self.log_stream.tail(12),
        }

    def log_tail(self, tail: int, pattern: str | None) -> list[str]:
        lines = self.log_stream.tail(tail)
        if pattern:
            needle = pattern.lower()
            lines = [line for line in lines if needle in line.lower()]
        return lines

    def note_line(self, line: str) -> None:
        if "rx: renderer:" in line:
            self.renderer = line.split("rx: renderer:", 1)[1].strip()
        match = re.search(r"vk: present (\d+) written to (.+)$", line)
        if match:
            self.last_screenshot = {"path": match.group(2), "present": int(match.group(1))}
            return
        match = re.search(r"rx: screenshot (.+) \(frame (\d+)\)$", line)
        if match:
            self.last_screenshot = {"path": match.group(1), "present": int(match.group(2))}


def normalize_buttons(buttons: list[str] | str) -> list[str]:
    if isinstance(buttons, str):
        values = buttons.replace(",", "+").split("+")
    else:
        values = list(buttons)
    values = [value for value in values if str(value).strip()]
    if not values:
        raise ToolError("at least one button is required")
    names: list[str] = []
    for value in values:
        name = str(value).strip().upper()
        if name not in SCRIPT_BUTTONS:
            raise ToolError(f"unknown button {value!r}; use one of {', '.join(sorted(SCRIPT_BUTTONS))}")
        names.append(name)
    return names


def stick_tokens(stick: Literal["left", "right"], x: float, y: float) -> list[str]:
    if stick not in STICK_AXES:
        raise ToolError("stick must be left or right")
    ax, ay = STICK_AXES[stick]
    tokens: list[str] = []
    ix = max(-32767, min(32767, round(x * 32767)))
    iy = max(-32767, min(32767, round(y * 32767)))
    if ix:
        tokens.append(f"{ax}={ix}")
    if iy:
        tokens.append(f"{ay}={iy}")
    if not tokens:
        raise ToolError("x and y are both 0: the stick would not move")
    return tokens


def sequence_line(steps: list[Step]) -> tuple[str, int]:
    if not steps:
        raise ToolError("at least one step is required")
    if len(steps) > 64:
        raise ToolError("at most 64 steps fit in one live input line; split the sequence")
    ordered = sorted(enumerate(steps), key=lambda item: (item[1].at_ms, item[0]))
    parts: list[str] = []
    duration = 0
    for _, step in ordered:
        tokens = normalize_buttons(step.buttons) if step.buttons else []
        if step.stick is not None:
            tokens += stick_tokens(step.stick, step.x, step.y)
        if not tokens:
            raise ToolError("every step needs buttons or a non-zero stick")
        parts.append(f"{step.at_ms}:{'+'.join(tokens)}/{step.hold_ms}")
        duration = max(duration, step.at_ms + step.hold_ms)
    line = ",".join(parts)
    if len(line) > 3500:
        raise ToolError("sequence line is too long; split it into several calls")
    if duration > 600_000:
        raise ToolError("sequence lasts longer than 10 minutes; split it into several calls")
    return line, duration


class Controller:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session: GameSession | None = None

    def configure(self, cfg: Config) -> None:
        self.shutdown()
        self.cfg = cfg

    def launch(self, opts: LaunchOptions) -> dict[str, Any]:
        if self.session is not None and self.session.alive():
            raise ToolError("a game is already running; call stop first")
        self.session = GameSession(self.cfg, opts)
        result = self.session.launch()
        result["ready"] = False
        if opts.wait_ready:
            try:
                shot = self.session.screenshot(timeout_ms=int(opts.ready_timeout_s * 1000))
                result["ready"] = True
                result["first_screenshot"] = shot["path"]
            except ToolError as exc:
                result["warning"] = str(exc)
        return result

    def require_session(self) -> GameSession:
        if self.session is None:
            raise ToolError("no run yet; call launch first")
        return self.session

    def shutdown(self) -> None:
        if self.session is not None:
            try:
                self.session.stop()
            except Exception as exc:  # (never let shutdown mask the real error)
                log(f"stop on shutdown failed: {exc}")
            self.session = None


def _launch_options(**kwargs: Any) -> LaunchOptions:
    return LaunchOptions(**kwargs)


mcp = MCPServer(
    "hookshot",
    version=VERSION,
    instructions=(
        "Hookshot automated testing. Call launch, optionally wait for ready, then press / stick / sequence and "
        "screenshot. Screenshots are the game's presented frame (1280x720) returned inline. stop ends the run. "
        "The first launch can take a minute or two while shaders are prepared."
    ),
)
CONTROLLER = Controller(Config())
atexit.register(CONTROLLER.shutdown)


@mcp.tool(title="Launch Hookshot")
def launch(
    game_dir: Annotated[str | None, Field(description="A built game folder (hookshot.exe, image.bin, romfs).")] = None,
    exe: Annotated[str | None, Field(description="Path to hookshot.exe (overrides game_dir).")] = None,
    image: Annotated[str | None, Field(description="Path to image.bin (overrides game_dir).")] = None,
    romfs: Annotated[str | None, Field(description="Path to the romfs folder (overrides game_dir).")] = None,
    user: Annotated[str | None, Field(description="Save folder; defaults to work/mcp/user.")] = None,
    run_dir: Annotated[str | None, Field(description="Folder for this run's log and screenshots.")] = None,
    cache_dir: Annotated[str | None, Field(description="Working directory for shader/pipeline caches.")] = None,
    window: Annotated[bool, Field(description="Show the game window instead of running headless.")] = False,
    focus: Annotated[bool, Field(description="Let a windowed game take focus.")] = False,
    window_size: Annotated[str | None, Field(description="Window size, such as 1280x720.")] = None,
    audio: Annotated[bool, Field(description="Play game audio.")] = False,
    backend: Annotated[Literal["auto", "vulkan", "webgpu"] | None, Field(description="Renderer to request.")] = None,
    script_only: Annotated[bool, Field(description="Ignore physical pads, keyboard and mouse.")] = True,
    frame_clock: Annotated[bool, Field(description="Advance scripted input at 60 presents per second.")] = False,
    exclusive: Annotated[bool, Field(description="Do not merge physical pads while a press is active.")] = True,
    env_file: Annotated[bool | None, Field(description="Read hookshot_env.txt before applying the server's switches.")] = None,
    fresh_user: Annotated[bool, Field(description="Use a fresh save folder inside this run.")] = False,
    extra_env: Annotated[dict[str, str] | None, Field(description="Extra environment switches.")] = None,
    force: Annotated[bool, Field(description="Start even when another game process is detected.")] = False,
    wait_ready: Annotated[bool, Field(description="Wait for the first screenshot before returning.")] = False,
    ready_timeout_s: Annotated[float, Field(ge=1, le=900, description="How long wait_ready waits.")] = 180.0,
) -> dict[str, Any]:
    """Start a Hookshot test run. Returns immediately unless wait_ready is true."""
    opts = _launch_options(game_dir=game_dir, exe=exe, image=image, romfs=romfs, user=user, run_dir=run_dir,
                           cache_dir=cache_dir, window=window, focus=focus, window_size=window_size, audio=audio,
                           backend=backend, script_only=script_only, frame_clock=frame_clock, exclusive=exclusive,
                           env_file=env_file, fresh_user=fresh_user, extra_env=extra_env, force=force)
    opts.wait_ready = wait_ready
    opts.ready_timeout_s = ready_timeout_s
    return CONTROLLER.launch(opts)


@mcp.tool(title="Stop Hookshot")
def stop(
    force: Annotated[bool, Field(description="Terminate instead of asking the game to quit.")] = False,
    timeout_s: Annotated[float, Field(ge=0.1, le=60, description="How long a clean stop may take.")] = 10.0,
) -> dict[str, Any]:
    """Stop the current test run. Safe to call when nothing is running."""
    if CONTROLLER.session is None:
        return {"stopped": False, "already_stopped": True}
    result = CONTROLLER.session.stop(force=force, timeout_s=timeout_s)
    CONTROLLER.session = None
    return result


@mcp.tool(title="Hookshot status")
def status() -> dict[str, Any]:
    """Process state, renderer, last screenshot and recent log lines."""
    if CONTROLLER.session is None:
        return {"launched": False, "running": False}
    return CONTROLLER.session.status()


@mcp.tool(title="Press buttons")
def press(
    buttons: Annotated[list[str] | str, Field(description="Switch buttons, e.g. ['PLUS'] or 'A+LUP'.")],
    hold_ms: Annotated[int, Field(ge=1, le=600_000, description="How long to hold the press.")] = 200,
    settle_ms: Annotated[int, Field(ge=0, le=600_000, description="Extra wait after the hold.")] = 100,
    wait: Annotated[bool, Field(description="Wait for the hold and settle before returning.")] = True,
) -> dict[str, Any]:
    """Press one or more Switch buttons (A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN and stick directions)."""
    return CONTROLLER.require_session().press(buttons, hold_ms, settle_ms, wait)


@mcp.tool(name="stick", title="Move a stick")
def stick_move(
    which: Annotated[Literal["left", "right"], Field(description="Which stick moves.")] = "left",
    x: Annotated[float, Field(ge=-1.0, le=1.0, description="Horizontal deflection; positive is right.")] = 0.0,
    y: Annotated[float, Field(ge=-1.0, le=1.0, description="Vertical deflection; positive is up.")] = 0.0,
    hold_ms: Annotated[int, Field(ge=1, le=600_000, description="How long the stick is held.")] = 200,
    settle_ms: Annotated[int, Field(ge=0, le=600_000, description="Extra wait after the hold.")] = 100,
    wait: Annotated[bool, Field(description="Wait for the hold and settle before returning.")] = True,
) -> dict[str, Any]:
    """Move the left or right analog stick for a timed hold."""
    return CONTROLLER.require_session().move_stick(which, x, y, hold_ms, settle_ms, wait)


@mcp.tool(name="sequence", title="Timed input sequence")
def sequence_steps(
    steps: Annotated[list[Step], Field(description="Presses, relative to when the game reads the line.")],
    settle_ms: Annotated[int, Field(ge=0, le=600_000, description="Extra wait after the last step.")] = 100,
    wait: Annotated[bool, Field(description="Wait for the whole sequence before returning.")] = True,
) -> dict[str, Any]:
    """Queue a timed multi-step input sequence in one live input line."""
    return CONTROLLER.require_session().sequence(steps, settle_ms, wait)


@mcp.tool(title="Screenshot")
def screenshot(
    path: Annotated[str | None, Field(description="PNG path; defaults to work/mcp/.../shots/shot_NNNN.png.")] = None,
    inline: Annotated[bool, Field(description="Return the PNG as an MCP image block.")] = True,
    timeout_ms: Annotated[int, Field(ge=100, le=600_000, description="How long to wait for the frame.")] = DEFAULT_TIMEOUT_MS,
) -> list[TextContent | Image]:
    """Capture the game's current presented frame and return it inline (1280x720 PNG)."""
    session = CONTROLLER.require_session()
    info = session.screenshot(path=path, timeout_ms=timeout_ms)
    png = Path(info["path"]).read_bytes()
    text = f"screenshot {info['path']} ({info['bytes']} bytes"
    if info.get("present") is not None:
        text += f", present {info['present']}"
    text += ")"
    content: list[TextContent | Image] = [TextContent(type="text", text=text)]
    if inline and len(png) <= 8_000_000:
        content.append(Image(data=png, format="png"))
    elif not inline:
        content[0].text += "; inline=false"
    else:
        content[0].text += "; too large to inline"
    return content


@mcp.tool(title="Wait")
def wait(
    ms: Annotated[int, Field(ge=1, le=600_000, description="Wall-clock milliseconds to wait.")] = 1000,
) -> dict[str, Any]:
    """Wait for wall time while the game runs, then return status."""
    session = CONTROLLER.require_session()
    session._sleep_ms(ms)
    return session.status()


@mcp.tool(title="Game log")
def logs(
    tail: Annotated[int, Field(ge=1, le=2000, description="How many recent lines to return.")] = 200,
    pattern: Annotated[str | None, Field(description="Only lines containing this text (case-insensitive).")] = None,
) -> str:
    """Return recent game log lines, optionally filtered."""
    session = CONTROLLER.require_session()
    return "\n".join(session.log_tail(tail, pattern))


@mcp.resource("hookshot://log", mime_type="text/plain", description="Recent Hookshot game log lines.")
def log_resource() -> str:
    if CONTROLLER.session is None:
        return "no run yet\n"
    return "\n".join(CONTROLLER.session.log_tail(200, None)) + "\n"


@mcp.resource("hookshot://last-screenshot", mime_type="image/png", description="The most recent screenshot.")
def last_screenshot_resource() -> bytes:
    if CONTROLLER.session is None or not CONTROLLER.session.last_screenshot:
        raise ResourceNotFoundError("no screenshot yet")
    path = Path(CONTROLLER.session.last_screenshot["path"])
    if not path.is_file():
        raise ResourceNotFoundError(f"screenshot is gone: {path}")
    return path.read_bytes()


@mcp.resource("hookshot://status", mime_type="application/json", description="The current run's status.")
def status_resource() -> dict[str, Any]:
    return status()


def parse_args(argv: list[str] | None = None) -> tuple[Config, bool]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir", default=os.environ.get("HOOKSHOT_MCP_GAME_DIR"))
    parser.add_argument("--exe", default=os.environ.get("HOOKSHOT_MCP_EXE"))
    parser.add_argument("--image", default=os.environ.get("HOOKSHOT_MCP_IMAGE"))
    parser.add_argument("--romfs", default=os.environ.get("HOOKSHOT_MCP_ROMFS"))
    parser.add_argument("--user", default=os.environ.get("HOOKSHOT_MCP_USER"))
    parser.add_argument("--work-dir", default=os.environ.get("HOOKSHOT_MCP_WORK_DIR", str(ROOT / "work" / "mcp")))
    parser.add_argument("--backend", choices=("auto", "vulkan", "webgpu"), default="auto")
    parser.add_argument("--no-env-file", dest="env_file", action="store_false", default=True,
                        help="do not read hookshot_env.txt beside the game")
    parser.add_argument("--check", action="store_true", help="print the resolved paths and exit")
    args = parser.parse_args(argv)
    cfg = Config(work_dir=Path(args.work_dir).expanduser(), exe=_path(args.exe), image=_path(args.image),
                 romfs=_path(args.romfs), game_dir=_path(args.game_dir), user=_path(args.user),
                 backend=args.backend, env_file=args.env_file)
    return cfg, args.check


def check(cfg: Config) -> int:
    try:
        paths = resolve_paths(cfg, LaunchOptions())
    except ToolError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    for key in ("exe", "image", "romfs", "user", "run_dir", "cache_dir"):
        print(f"{key}: {paths[key]}")
    print(f"work dir: {cfg.work_dir}")
    return 0


def main(argv: list[str] | None = None) -> None:
    cfg, should_check = parse_args(argv)
    if should_check:
        raise SystemExit(check(cfg))
    CONTROLLER.configure(cfg)
    log(f"hookshot MCP server {VERSION} (work dir {cfg.work_dir})")
    try:
        mcp.run()
    finally:
        CONTROLLER.shutdown()


if __name__ == "__main__":
    main()
