"""hookshot_ctl.py launch never leaves the game it started running when it gives up, and the bench's runs end their game.

A headless game could ignore SIGTERM (SDL's handlers turned it into a quit event nothing read), and launch starts the game in
a session of its own. So when a bench run's `timeout` ended the client, or the bridge never came up, the game went on, on the
GPU, through other runs' measurements. Four ways it happened, each with a stand-in game that ignores SIGTERM and writes its
pid (no game, no GPU):

- the bridge is off ("control: off" in the log, here a run folder others could write): launch says so at once and kills it;
- the bridge never answers: launch kills it at its --timeout;
- SIGTERM to launch while it waits (what `timeout` sends to its whole process group): the game is killed too;
- a run folder made beforehand with mode 775: launch makes it 0700, which the game's bridge needs.

python -I -B tests/test_launch_ends_game.py
"""
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUN_ALL_SKIP = {"windows": "the stand-in game is a POSIX script; Windows uses TerminateProcess through taskkill"}

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "scripts" / "hookshot_ctl.py"

GAME = """#!/bin/sh
trap '' TERM INT
echo $$ > "$PWD/game.pid"
[ -n "$FAKE_CONTROL_OFF" ] && echo "[hos host] control: off (HOS_CONTROL=$HOS_CONTROL could not be served)"
while :; do sleep 1; done
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # (a zombie no one reaped yet counts as gone: launch's parent, this test, is not its parent)
    try:
        return Path("/proc/%d/stat" % pid).read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def game_pid(game):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        p = game / "game.pid"
        if p.exists() and p.read_text().strip():
            return int(p.read_text())
        time.sleep(0.05)
    raise SystemExit("FAIL: the stand-in game never started")


def gone(pid, seconds=5):
    end = time.monotonic() + seconds
    while alive(pid) and time.monotonic() < end:
        time.sleep(0.05)
    return not alive(pid)


def finish(p, seconds=30):
    """launch's exit status and stderr; a launch still waiting after that long is killed (None: it did not give up)"""
    try:
        _, err = p.communicate(timeout=seconds)
        return p.returncode, err
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return None, "still waiting after %d s" % seconds


def main():
    fails = 0
    with tempfile.TemporaryDirectory(prefix="hookshot-launch-ends-") as tmp:
        tmp = Path(tmp)
        game = tmp / "game"
        game.mkdir()
        exe = game / "hookshot"
        exe.write_text(GAME)
        exe.chmod(0o755)

        def launch(name, extra_env=None, timeout=30, before=None):
            run = tmp / name
            if before:
                before(run)
            env = dict(os.environ, **(extra_env or {}))
            (game / "game.pid").unlink(missing_ok=True)
            p = subprocess.Popen([sys.executable, "-I", str(CTL), "launch", str(game), "--run", str(run), "--headless",
                                  "--timeout", str(timeout)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
            return p, run

        # 1: the bridge is off
        t0 = time.monotonic()
        p, _ = launch("off", {"FAKE_CONTROL_OFF": "1"})
        pid = game_pid(game)
        code, err = finish(p)
        took = time.monotonic() - t0
        if code != 2 or "could not serve its bridge" not in err or took > 15:
            print("FAIL: launch with the bridge off: exit %s after %.1f s: %s" % (code, took, err.strip())); fails += 1
        if not gone(pid):
            print("FAIL: launch with the bridge off left the game running"); fails += 1
            os.kill(pid, signal.SIGKILL)

        # 2: the bridge never answers
        p, _ = launch("timeout", timeout=2)
        pid = game_pid(game)
        code, err = finish(p)
        if code != 3 or "did not answer" not in err:
            print("FAIL: launch at its timeout: exit %s: %s" % (code, err.strip())); fails += 1
        if not gone(pid):
            print("FAIL: launch at its timeout left the game running"); fails += 1
            os.kill(pid, signal.SIGKILL)

        # 3: SIGTERM to launch while it waits
        p, _ = launch("sigterm", timeout=60)
        pid = game_pid(game)
        time.sleep(0.5)
        p.send_signal(signal.SIGTERM)
        finish(p)
        if not gone(pid):
            print("FAIL: SIGTERM to launch left the game running"); fails += 1
            os.kill(pid, signal.SIGKILL)

        # 4: a run folder made beforehand, group-writable
        def made_775(run):
            run.mkdir()
            run.chmod(0o775)
        p, run = launch("mode", timeout=2, before=made_775)
        pid = game_pid(game)
        mode = stat.S_IMODE(run.stat().st_mode)
        finish(p)
        if not gone(pid):
            os.kill(pid, signal.SIGKILL)
        if mode != 0o700:
            print("FAIL: the run folder is %03o while the game runs, 700 expected (the bridge refuses others' folders)" % mode)
            fails += 1
    if fails:
        sys.exit(1)
    print("PASS: launch kills the game it started when the bridge is off, at its timeout and on SIGTERM; its folder is 0700")


if __name__ == "__main__":
    main()
