"""scripts/hookshot_ctl.py, the control bridge's client, against a tiny fake game: a Python stand-in for the executable
that serves the bridge's protocol on $HOS_CONTROL (docs/control.md) and writes down each request. The commands' requests
and plain output, --json, exit codes (an error reply, a timeout, no game), watch, and launch (which starts the stand-in
as a game folder's hookshot and waits for its socket). Linux only: a Unix socket.

python -I -B tests/test_hookshot_ctl.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "hookshot_ctl.py"

FAKE = r'''
import json, os, socket, sys, threading, time
path = os.environ["HOS_CONTROL"]
log = open(os.path.join(os.path.dirname(path), "requests.jsonl"), "a", buffering=1)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path)
srv.listen(4)
frame = [100]
def ticker():
    while True:
        time.sleep(0.01)
        frame[0] += 1
threading.Thread(target=ticker, daemon=True).start()
def serve(c):
    f = c.makefile("rwb", buffering=0)
    for raw in f:
        req = json.loads(raw)
        log.write(json.dumps(req) + "\n")
        rid, cmd = req.get("id"), req.get("cmd")
        r = {"id": rid, "ok": True, "frame": frame[0]}
        if cmd == "status":
            r.update(protocol=1, fps=59.9, state="play", draws=2400.0, window={"width": 1280, "height": 720}, version="fake")
        elif cmd in ("press", "stick", "sequence"):
            r.update(start=frame[0], end=frame[0] + req.get("frames", 8))
        elif cmd == "shot":
            r.update(present=frame[0] + 1, path=req.get("path") or "/run/shots/shot_0001.png")
        elif cmd == "wait":
            if req.get("event") == "loading_end":
                r = {"id": rid, "ok": False, "frame": frame[0], "error": "timeout", "message": "not met in time"}
            else:
                r.update(state="play", waited=3)
        elif cmd == "option_set":
            if req["name"] == "nope":
                r = {"id": rid, "ok": False, "frame": frame[0], "error": "not_found", "message": "no setting nope"}
            else:
                r.update(was=0, option={"key": req["name"], "value": req["value"], "text": "On"})
        elif cmd == "option_get":
            r.update(options=[{"key": "text_speed", "value": 3, "text": "Instant"}, {"key": "fps_counter", "value": 0, "text": "Off"}])
        elif cmd == "subscribe":
            f.write((json.dumps({"id": rid, "ok": True, "frame": frame[0], "events": req.get("events")}) + "\n").encode())
            for i in range(5):
                f.write((json.dumps({"event": "present", "sub": rid, "frame": frame[0] + i, "fps": 60.0, "state": "play"}) + "\n").encode())
            continue
        elif cmd == "quit":
            f.write((json.dumps(dict(r, quitting=True)) + "\n").encode())
            os.unlink(path)
            os._exit(0)
        elif cmd == "release":
            r.update(released=2)
        else:
            r = {"id": rid, "ok": False, "frame": frame[0], "error": "unknown_cmd", "message": "no command"}
        # (an event first: the client has to skip it)
        f.write(b'{"event":"present","sub":0,"frame":1,"fps":60,"state":"play"}\n')
        f.write((json.dumps(r) + "\n").encode())
while True:
    c, _ = srv.accept()
    threading.Thread(target=serve, args=(c,), daemon=True).start()
'''


def ctl(*args, env=None, check=None):
    r = subprocess.run([sys.executable, "-I", str(CTL)] + list(args), capture_output=True, text=True, env=env, timeout=60)
    if check is not None and r.returncode != check:
        raise AssertionError("exit %d (wanted %d): %s %s" % (r.returncode, check, r.stdout, r.stderr))
    return r


@unittest.skipIf(os.name == "nt", "a Unix socket")
class HookshotCtl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ctl-", dir="/tmp" if os.path.isdir("/tmp") else None)
        cls.dir = cls.tmp.name
        cls.game = os.path.join(cls.dir, "game")
        os.mkdir(cls.game)
        exe = os.path.join(cls.game, "hookshot")
        with open(exe, "w") as f:
            f.write("#!%s -I\n%s" % (sys.executable, FAKE))
        os.chmod(exe, 0o755)
        # (launch: the stand-in started as the game folder's executable, its socket in the run folder)
        cls.run_dir = os.path.join(cls.dir, "run")
        r = ctl("--json", "launch", cls.game, "--run", cls.run_dir, "--headless", "--timeout", "20", check=0)
        cls.info = json.loads(r.stdout)
        cls.sock = cls.info["socket"]
        cls.env = dict(os.environ, HOS_CONTROL=cls.sock)

    @classmethod
    def tearDownClass(cls):
        try:
            os.kill(cls.info["pid"], 9)
        except OSError:
            pass
        cls.tmp.cleanup()

    def last(self, cmd):
        with open(os.path.join(self.run_dir, "requests.jsonl")) as f:
            reqs = [json.loads(line) for line in f]
        return [r for r in reqs if r["cmd"] == cmd][-1]

    def test_launch(self):
        self.assertEqual(self.sock, os.path.join(self.run_dir, "control.sock"))
        self.assertEqual(os.stat(self.run_dir).st_mode & 0o777, 0o700)
        self.assertTrue(os.path.isfile(self.info["log"]))

    def test_status(self):
        r = ctl("status", env=self.env, check=0)
        self.assertRegex(r.stdout, r"^frame \d+  fps 59\.9  state play  draws 2400  window 1280x720  fake")
        r = ctl("--sock", self.sock, "--json", "status", check=0)
        self.assertEqual(json.loads(r.stdout)["state"], "play")

    def test_inputs(self):
        r = ctl("press", "A+B", "--frames", "8", "--exclusive", env=self.env, check=0)
        self.assertRegex(r.stdout, r"^frames \d+ to \d+")
        req = self.last("press")
        self.assertEqual((req["buttons"], req["frames"], req["exclusive"]), ("A+B", 8, True))
        ctl("stick", "l", "0", "1", "--frames", "60", env=self.env, check=0)
        req = self.last("stick")
        self.assertEqual((req["stick"], req["x"], req["y"], req["frames"]), ("L", 0.0, 1.0, 60))
        ctl("sequence", "0:A/8,60:A/8", env=self.env, check=0)
        self.assertEqual(self.last("sequence")["script"], "0:A/8,60:A/8")
        self.assertNotIn("units", self.last("sequence"))
        ctl("sequence", "0:LUP/2000,2:A", "--hos", env=self.env, check=0)
        self.assertEqual(self.last("sequence")["units"], "hos")
        self.assertEqual(ctl("release", env=self.env, check=0).stdout.strip(), "released 2")

    def test_shot(self):
        r = ctl("shot", "out.png", env=self.env, check=0)
        self.assertEqual(r.stdout.strip(), os.path.abspath("out.png"))   # (the client's folder, made absolute)
        r = ctl("shot", "--after", "500", env=self.env, check=0)
        self.assertNotIn("path", self.last("shot"))
        self.assertEqual(self.last("shot")["after"], 500)

    def test_wait_and_errors(self):
        r = ctl("wait", "in_play", "--timeout", "120", env=self.env, check=0)
        self.assertIn("play after 3 frames", r.stdout)
        self.assertEqual(self.last("wait")["timeout"], 120)
        r = ctl("wait", "loading_end", "--timeout", "1", env=self.env, check=3)
        self.assertIn("timeout", r.stderr)
        r = ctl("option", "set", "nope", "1", env=self.env, check=1)
        self.assertIn("not_found", r.stderr)
        r = ctl("raw", '{"cmd":"fly"}', env=self.env, check=1)
        self.assertEqual(json.loads(r.stdout)["error"], "unknown_cmd")
        r = ctl("--sock", os.path.join(self.dir, "none.sock"), "status", check=2)
        self.assertIn("no game listens", r.stderr)
        env = {k: v for k, v in os.environ.items() if k != "HOS_CONTROL"}
        self.assertIn("HOS_CONTROL", ctl("status", env=env, check=2).stderr)

    def test_options(self):
        r = ctl("option", "get", env=self.env, check=0)
        self.assertRegex(r.stdout, r"text_speed\s+3  Instant")
        r = ctl("option", "set", "fps_counter", "on", env=self.env, check=0)
        self.assertEqual(self.last("option_set")["value"], 1)
        self.assertIn("fps_counter 0 -> 1 (On)", r.stdout)

    def test_watch(self):
        r = ctl("watch", "--count", "3", "--every", "30", "--log", "vk:", env=self.env, check=0)
        self.assertEqual(len(r.stdout.splitlines()), 3)
        req = self.last("subscribe")
        self.assertEqual((req["events"], req["every"], req["log"]), (["present", "state", "log"], 30, "vk:"))

    def test_zz_quit(self):   # (last: the stand-in ends)
        r = ctl("quit", env=self.env, check=0)
        self.assertEqual(r.stdout.strip(), "quitting")
        for _ in range(100):
            if not os.path.exists(self.sock):
                break
            time.sleep(0.05)
        self.assertFalse(os.path.exists(self.sock))


if __name__ == "__main__":
    unittest.main()
