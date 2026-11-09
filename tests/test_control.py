"""The control bridge (host/control.c) against a fake game, over a real Unix socket: tests/control_test.c is built with
host/control.c (HOS_CONTROL_NO_GAME: no game behind it) and drives it as a client. Valid commands, malformed JSON, unknown
commands, oversized lines, clients that disconnect mid-command or never read, too many clients, the socket's mode, a file
in the way, and quit. Linux only: the Windows named pipe is not built here.

python -I -B tests/test_control.py [--cc gcc]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    if os.name == "nt":
        print("SKIP: the bridge's Unix socket (Windows has the named pipe, not tested here)")
        return 0
    # (a short folder: a Unix socket's path is at most 107 bytes)
    with tempfile.TemporaryDirectory(prefix="ctl-", dir="/tmp" if os.path.isdir("/tmp") else None) as tmp:
        exe = os.path.join(tmp, "control_test")
        subprocess.run([a.cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                        "-Wno-format-truncation", "-D_GNU_SOURCE", "-DHOS_CONTROL_NO_GAME",
                        str(ROOT / "tests" / "control_test.c"), str(ROOT / "host" / "control.c"), "-o", exe,
                        "-lpthread", "-lm"], check=True)
        # (the general cases, quit, the level unit, and the memory cases at each HOS_CONTROL_MEM level)
        runs = [([], None), (["quit"], None), (["level"], None),
                (["mem"], None), (["mem"], "ro"), (["mem"], "rw"), (["mem"], "bogus")]
        for case, mem in runs:
            folder = tempfile.mkdtemp(dir=tmp)
            env = dict(os.environ)
            env.pop("HOS_CONTROL_MEM", None)
            if mem is not None:
                env["HOS_CONTROL_MEM"] = mem
            r = subprocess.run([exe, folder] + case, capture_output=True, text=True, timeout=180, env=env)
            print(r.stdout, end="")
            print(r.stderr, end="", file=sys.stderr)
            if r.returncode:
                label = " ".join(case) or "(the cases)"
                print("FAIL: control_test %s%s exited %d" % (label, " HOS_CONTROL_MEM=%s" % mem if mem else "", r.returncode),
                      file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
