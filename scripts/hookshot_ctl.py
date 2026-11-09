"""A client for the game's control bridge (host/control.c, docs/control.md): start the game with HOS_CONTROL=<socket>, then
steer it and read its state from a shell, a test or a coding agent. Standard library only.

    python -I scripts/hookshot_ctl.py [--sock S] [--json] COMMAND ...

    launch GAME_DIR [--data DIR] [--headless]   start the game with the bridge on; prints the socket's path
    status                                      frame, fps, draws a frame, state, window, version
    press A+B [--frames 8] [--exclusive]        buttons held for some frames (the reply once they are let go)
    stick L 0 1 [--frames 60]                   a stick at x, y (-1 to 1)
    sequence '0:A/8,60:A/8' [--hos]             steps, <at>:<buttons>[/<frames held>] (--hos: HOS_INPUT_SCRIPT's s and ms)
    release                                     every queued input let go
    shot [out.png] [--after FRAME]              a PNG of the next present; prints its path
    wait in_play [--timeout 120]                also: present --frames N, frame --frame N, state --state S,
                                                loading_start, loading_end, change
    option get [NAME]  /  option set NAME VALUE the Insert menu's settings
    watch [--events present,state,log] [--every 60] [--log TEXT] [--count N]
    quit                                        the game exits
    raw '{"cmd": "status"}'                     any request

The socket defaults to $HOS_CONTROL. Plain lines by default, --json for the replies as they come. Exit status: 0 done,
1 an error reply, 2 no game or bad usage, 3 a timeout.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

WINDOWS = os.name == "nt"


class Failed(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Bridge:
    """One connection: requests with ids, replies matched to them, events kept for watch"""

    def __init__(self, path, timeout=10.0):
        self.path, self.next_id, self.pending = path, 0, b""
        try:
            if WINDOWS:
                name = path if path.startswith("\\\\.\\pipe\\") else "\\\\.\\pipe\\" + path
                self.pipe = open(name, "r+b", buffering=0)
                self.sock = None
            else:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(timeout)
                self.sock.connect(path)
        except OSError as e:
            raise Failed("no game listens on %s (%s)" % (path, e.strerror or e), 2)

    def close(self):
        (self.sock or self.pipe).close()

    def send(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        if self.sock:
            self.sock.sendall(data)
        else:
            self.pipe.write(data)

    def line(self, timeout):
        """The next line as an object; None when the game went away"""
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Failed("no answer from the game in %.0f s" % timeout, 3)
            try:
                if self.sock:
                    self.sock.settimeout(left)
                    chunk = self.sock.recv(65536)
                else:
                    chunk = self.pipe.read(1)   # (a pipe opened as a file: no timeout on Windows)
            except socket.timeout:
                raise Failed("no answer from the game in %.0f s" % timeout, 3)
            except OSError:
                chunk = b""
            if not chunk:
                return None
            self.pending += chunk
        text, self.pending = self.pending.split(b"\n", 1)
        return json.loads(text.decode("utf-8", "replace"))

    def request(self, cmd, timeout=None, **args):
        """The reply to one request (events that come meanwhile are dropped)"""
        self.next_id += 1
        rid = self.next_id
        req = {"id": rid, "cmd": cmd}
        req.update({k: v for k, v in args.items() if v is not None})
        if timeout is not None:
            req["timeout"] = timeout
        self.send(req)
        wait = (timeout if timeout is not None else 60) + 10
        while True:
            msg = self.line(wait)
            if msg is None:
                if cmd == "quit":
                    return {"id": rid, "ok": True, "quitting": True}
                raise Failed("the game closed the connection", 2)
            if msg.get("id") == rid and "ok" in msg:
                return msg


def connect(a, timeout=10.0):
    path = a.sock or os.environ.get("HOS_CONTROL")
    if not path:
        raise Failed("no socket: --sock, or HOS_CONTROL", 2)
    return Bridge(path, timeout)


def check(reply):
    if not reply.get("ok"):
        code = 3 if reply.get("error") == "timeout" else 1
        raise Failed("%s: %s" % (reply.get("error", "error"), reply.get("message", "")), code)
    return reply


def show(a, reply, plain):
    if a.json:
        print(json.dumps(reply))
    else:
        print(plain(reply))


def run(a):
    if a.command == "launch":
        return launch(a)
    b = connect(a)
    try:
        if a.command == "status":
            r = check(b.request("status"))
            win = r.get("window") or {}
            show(a, r, lambda r: "frame %d  fps %.1f  state %s  draws %s  window %s  %s" % (
                r["frame"], r["fps"], r["state"], "-" if r.get("draws") is None else "%.0f" % r["draws"],
                "%dx%d" % (win["width"], win["height"]) if win else "-", r.get("version", "")))
        elif a.command in ("press", "stick", "sequence"):
            args = {"exclusive": True if a.exclusive else None, "wait": False if a.no_wait else None}
            if a.command == "press":
                args.update(buttons=a.buttons, frames=a.frames, at=a.at)
            elif a.command == "stick":
                args.update(stick=a.which.upper(), x=a.x, y=a.y, frames=a.frames, at=a.at)
            else:
                args.update(script=a.script.strip(), units="hos" if a.hos else None)
            r = check(b.request(a.command, timeout=a.timeout, **args))
            show(a, r, lambda r: "queued %d input(s) at frame %d" % (r["queued"], r["frame"]) if "queued" in r
                 else "frames %d to %d (now %d)" % (r["start"], r["end"], r["frame"]))
        elif a.command == "release":
            show(a, check(b.request("release")), lambda r: "released %d" % r["released"])
        elif a.command == "shot":
            path = os.path.abspath(a.path) if a.path else None   # (the bridge would take it as the socket folder's)
            r = check(b.request("shot", timeout=a.timeout, path=path, after=a.after))
            show(a, r, lambda r: r["path"])
        elif a.command == "wait":
            r = check(b.request("wait", timeout=a.timeout, event=a.event, frames=a.frames, frame=a.frame, state=a.state))
            show(a, r, lambda r: "%s after %d frames (frame %d)" % (r["state"], r["waited"], r["frame"]))
        elif a.command == "option":
            if a.action == "get":
                r = check(b.request("option_get", name=a.name))
                rows = [r["option"]] if "option" in r else r["options"]
                show(a, r, lambda r: "\n".join("%-28s %4d  %s" % (o["key"], o["value"], o["text"]) for o in rows))
            else:
                if a.value is None:
                    raise Failed("option set NAME VALUE", 2)
                v = {"on": 1, "true": 1, "off": 0, "false": 0}.get(a.value.lower())
                if v is None:
                    try:
                        v = int(a.value)
                    except ValueError:
                        raise Failed("VALUE: a number, on or off", 2)
                r = check(b.request("option_set", name=a.name, value=v))
                show(a, r, lambda r: "%s %d -> %d (%s)" % (r["option"]["key"], r["was"], r["option"]["value"], r["option"]["text"]))
        elif a.command == "quit":
            show(a, check(b.request("quit")), lambda r: "quitting")
        elif a.command == "raw":
            req = json.loads(a.request)
            b.next_id += 1
            req.setdefault("id", b.next_id)
            b.send(req)
            while True:
                msg = b.line(float(req.get("timeout", 60)) + 10)
                if msg is None:
                    raise Failed("the game closed the connection", 2)
                if msg.get("id") == req["id"] and "ok" in msg:
                    print(json.dumps(msg))
                    return 0 if msg["ok"] else 1
        elif a.command == "watch":
            events = [e for e in a.events.split(",") if e]
            if a.log and "log" not in events:
                events.append("log")
            check(b.request("subscribe", events=events, every=a.every, log=a.log))
            n = 0
            while a.count is None or n < a.count:
                msg = b.line(3600 * 24)
                if msg is None:
                    return 0
                if "event" not in msg:
                    continue
                n += 1
                if a.json:
                    print(json.dumps(msg), flush=True)
                elif msg["event"] == "log":
                    print("log    %s" % msg["line"], flush=True)
                elif msg["event"] == "state":
                    print("state  frame %d: %s -> %s" % (msg["frame"], msg["from"], msg["state"]), flush=True)
                elif msg["event"] == "present":
                    print("frame  %d  fps %.1f  %s" % (msg["frame"], msg["fps"], msg["state"]), flush=True)
                else:
                    print(json.dumps(msg), flush=True)
        return 0
    finally:
        b.close()


def launch(a):
    """The game in GAME_DIR with HOS_CONTROL set, its socket in a run folder of its own; waits until it answers"""
    game = os.path.abspath(a.game_dir)
    exe = os.path.join(game, "hookshot.exe" if WINDOWS else "hookshot")
    if not os.path.isfile(exe):
        raise Failed("no game at %s" % exe, 2)
    run_dir = os.path.abspath(a.run) if a.run else tempfile.mkdtemp(prefix="hookshot-control-",
                                                                     dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    sock = a.sock or ("hookshot-control-%d" % os.getpid() if WINDOWS else os.path.join(run_dir, "control.sock"))
    env = dict(os.environ, HOS_CONTROL=sock)
    if a.data:
        env["XDG_DATA_HOME"] = os.path.abspath(a.data)
    if a.headless:
        env["HOS_HEADLESS"] = "1"
    for kv in a.env:
        if "=" not in kv:
            raise Failed("--env NAME=VALUE", 2)
        k, v = kv.split("=", 1)
        env[k] = v
    log = os.path.join(run_dir, "log.txt")
    with open(log, "wb") as out:
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
        p = subprocess.Popen([exe], cwd=game, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kw)
    deadline = time.monotonic() + a.timeout
    while True:
        if p.poll() is not None:
            raise Failed("the game ended (%d) before its bridge answered: %s" % (p.returncode, log), 2)
        try:
            b = Bridge(sock, 5)
            r = b.request("status")
            b.close()
            if r.get("ok"):
                break
        except Failed:
            pass
        if time.monotonic() > deadline:
            raise Failed("the game's bridge did not answer in %.0f s: %s" % (a.timeout, log), 3)
        time.sleep(0.2)
    info = {"socket": sock, "pid": p.pid, "run": run_dir, "log": log}
    print(json.dumps(info) if a.json else sock)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sock", help="the bridge's socket (default $HOS_CONTROL)")
    p.add_argument("--json", action="store_true", help="the replies as JSON lines")
    sub = p.add_subparsers(dest="command", required=True)

    def inputs(q):
        q.add_argument("--frames", type=int, default=8)
        q.add_argument("--at", type=int, default=0, help="frames from the next controller read")
        q.add_argument("--exclusive", action="store_true", help="the player's own devices left out meanwhile")
        q.add_argument("--no-wait", action="store_true", help="reply as soon as it is queued")
        q.add_argument("--timeout", type=float)

    q = sub.add_parser("launch", help="start a game folder with the bridge on")
    q.add_argument("game_dir")
    q.add_argument("--data", help="XDG_DATA_HOME for the run (its saves)")
    q.add_argument("--run", help="the run folder (socket, log, shots); default a new one")
    q.add_argument("--headless", action="store_true")
    q.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    q.add_argument("--timeout", type=float, default=600, help="seconds to wait for the bridge (shaders may be prepared first)")
    sub.add_parser("status")
    q = sub.add_parser("press")
    q.add_argument("buttons", help="A, A+B, ZL+LUP, LX=-16000 ...")
    inputs(q)
    q = sub.add_parser("stick")
    q.add_argument("which", choices=["L", "R", "l", "r"])
    q.add_argument("x", type=float)
    q.add_argument("y", type=float)
    inputs(q)
    q = sub.add_parser("sequence")
    q.add_argument("script", help="<at>:<buttons>[/<frames>],...")
    q.add_argument("--hos", action="store_true", help="times in seconds and holds in ms, as HOS_INPUT_SCRIPT")
    q.add_argument("--exclusive", action="store_true")
    q.add_argument("--no-wait", action="store_true")
    q.add_argument("--timeout", type=float)
    sub.add_parser("release")
    q = sub.add_parser("shot")
    q.add_argument("path", nargs="?")
    q.add_argument("--after", type=int, help="the first present after this frame")
    q.add_argument("--timeout", type=float)
    q = sub.add_parser("wait")
    q.add_argument("event", choices=["present", "frame", "in_play", "state", "loading_start", "loading_end", "change"])
    q.add_argument("--frames", type=int)
    q.add_argument("--frame", type=int)
    q.add_argument("--state")
    q.add_argument("--timeout", type=float, default=60)
    q = sub.add_parser("option")
    q.add_argument("action", choices=["get", "set"])
    q.add_argument("name", nargs="?")
    q.add_argument("value", nargs="?")
    q = sub.add_parser("watch")
    q.add_argument("--events", default="present,state")
    q.add_argument("--every", type=int, default=60)
    q.add_argument("--log", help="log lines that contain this text (adds log to the events)")
    q.add_argument("--count", type=int, help="stop after this many events")
    sub.add_parser("quit")
    q = sub.add_parser("raw")
    q.add_argument("request", help="a JSON request object")
    a = p.parse_args(argv)
    try:
        return run(a)
    except Failed as e:
        print("hookshot_ctl: %s" % e, file=sys.stderr)
        return e.code
    except (BrokenPipeError, ConnectionResetError):
        print("hookshot_ctl: the game closed the connection", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print("hookshot_ctl: not JSON: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
