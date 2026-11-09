"""The game's user is the launcher's profile: HOOKSHOT_PROFILE=<32 hex digits> is the save folder's name (save/<digits>),
and anything else plays the default profile, the user every build had before profiles.

This builds host/nn_applet.c's user_uid and pick_user as they are in the source, with nn_fs.c's folder naming, and checks
which save folder each value leads to.

python -I -B tests/test_account_profile.py [--cc gcc]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = "5a3c9e1f0b7d24680000000000000001"   # (builder/linux/profiles.py's DEFAULT)

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long LONG;
static LONG InterlockedExchange(volatile LONG *p, LONG v) { LONG o = *p; *p = v; return o; }
static void hos_log(const char *f, ...) { (void)f; }
%s
int main(void) {
    pick_user();
    /* (host/nn_fs.c's MountSaveData: the uid's second word, then its first) */
    printf("%%016llx%%016llx\n", (unsigned long long)user_uid[1], (unsigned long long)user_uid[0]);
    return 0;
}
'''


def source():
    text = (ROOT / "host" / "nn_applet.c").read_text(encoding="utf-8")
    start = text.index("static uint64_t user_uid[2]")
    end = text.index("HLE(_ZN2nn7account10InitializeEv)", start)
    return text[start:end]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", default="cc")
    args = ap.parse_args()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="hookshot-account-") as tmp:
        c, exe = Path(tmp) / "t.c", Path(tmp) / "t"
        c.write_text(HARNESS % source(), encoding="utf-8")
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(exe), str(c)], check=True)
        cases = {
            None: DEFAULT,
            "": DEFAULT,
            "5a3c9e1f0b7d24680000000000000002": "5a3c9e1f0b7d24680000000000000002",
            "45EB517681BCEAF4B834E0FA511E3212": "45eb517681bceaf4b834e0fa511e3212",
            "0" * 32: DEFAULT,
            "5a3c9e1f0b7d2468000000000000002": DEFAULT,   # (31 digits)
            "5a3c9e1f0b7d24680000000000000002x": DEFAULT,
            "../../../../../../etc/passwd/xx": DEFAULT,
        }
        for value, want in cases.items():
            env = {k: v for k, v in os.environ.items() if k != "HOOKSHOT_PROFILE"}
            if value is not None:
                env["HOOKSHOT_PROFILE"] = value
            got = subprocess.run([str(exe)], env=env, capture_output=True, text=True, check=True).stdout.strip()
            ok = got == want
            failures += not ok
            print("%s HOOKSHOT_PROFILE=%r: save/%s%s" % ("PASS" if ok else "FAIL", value, got, "" if ok else " (want %s)" % want))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
