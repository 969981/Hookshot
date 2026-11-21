"""The game's memory kept in RAM: the soft working-set minimum (host/ws_floor.h, HOS_WS_FLOOR_MB), Windows only.

Under memory pressure Windows trimmed a player's game to about 1.1 GB of its 20 GB, and the game's frames waited on its
threads' page faults. The host now asks for a working-set minimum at start: a quarter of the RAM, at most 4096 MB, never over
half; HOS_WS_FLOOR_MB=<MB> another (at most half the RAM), 0/off/no/false none (Windows' own minimum), anything else the
default.

The rule (the header, compiled alone): the default by the RAM, the switch's values, the half-RAM cap. On Windows the call too,
in a process of its own: the minimum Windows reports afterwards is the rule's and is a soft one (QUOTA_LIMITS_HARDWS_MIN_DISABLE,
so it cannot hold memory the PC needs), the maximum is soft and at least the minimum; with 0 nothing is asked for and the
minimum stays Windows' own. And the host calls it at start, with the switch's value.

python -I -B tests/test_ws_floor.py [--cc <C compiler>]   (default: clang, gcc or cc on PATH; on Windows also HOOKSHOT_MINGW's
                                                       clang or Visual Studio's own)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"

RULE = r"""
#include "ws_floor.h"
#include <stdio.h>
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
int main(void) {
    /* (the default: a quarter of the RAM, at most 4096 MB) */
    CHECK(ws_floor_mb(32768, NULL) == 4096);
    CHECK(ws_floor_mb(65536, "") == 4096);
    CHECK(ws_floor_mb(16384, NULL) == 4096);
    CHECK(ws_floor_mb(12288, NULL) == 3072);
    CHECK(ws_floor_mb(8192, NULL) == 2048);
    CHECK(ws_floor_mb(0, NULL) == 0);
    /* (the switch: a number of MB, at most half the RAM; 0 and the words for off: none; anything else the default) */
    CHECK(ws_floor_mb(32768, "6000") == 6000);
    CHECK(ws_floor_mb(32768, "20000") == 16384);
    CHECK(ws_floor_mb(8192, "4096") == 4096);
    CHECK(ws_floor_mb(8192, "5000") == 4096);
    CHECK(ws_floor_mb(32768, "0") == 0);
    CHECK(ws_floor_mb(32768, "off") == 0);
    CHECK(ws_floor_mb(32768, "no") == 0);
    CHECK(ws_floor_mb(32768, "false") == 0);
    CHECK(ws_floor_mb(32768, "on") == 4096);
    CHECK(ws_floor_mb(32768, "lots") == 4096);
#ifdef _WIN32
    {   /* (the call, in this process: the minimum Windows reports is the rule's, soft; the maximum soft and above it) */
        uint64_t mb = 0, ram = 0;
        unsigned long err = 0;
        SIZE_T lo0 = 0, hi0 = 0, lo = 0, hi = 0;
        DWORD fl = 0;
        GetProcessWorkingSetSizeEx(GetCurrentProcess(), &lo0, &hi0, &fl);
        CHECK(ws_floor_apply("0", &mb, &ram, &err) == 0 && mb == 0 && ram > 0);
        GetProcessWorkingSetSizeEx(GetCurrentProcess(), &lo, &hi, &fl);
        CHECK(lo == lo0);   /* (0: nothing asked for) */
        const int r = ws_floor_apply("512", &mb, &ram, &err);
        if (r < 0) printf("refused, error %lu\n", err);
        CHECK(r == 1 && mb == 512);
        GetProcessWorkingSetSizeEx(GetCurrentProcess(), &lo, &hi, &fl);
        CHECK(lo == ((SIZE_T)512 << 20));
        CHECK(hi >= lo);
        CHECK(fl & QUOTA_LIMITS_HARDWS_MIN_DISABLE);
        CHECK(fl & QUOTA_LIMITS_HARDWS_MAX_DISABLE);
        CHECK(ws_floor_apply(NULL, &mb, &ram, &err) == 1 && mb == ws_floor_mb(ram, NULL));
        GetProcessWorkingSetSizeEx(GetCurrentProcess(), &lo, &hi, &fl);
        CHECK(lo == ((SIZE_T)mb << 20));
    }
#endif
    printf(fails ? "%d failed\n" : "ok\n", fails);
    return fails != 0;
}
"""


def compiler(name):
    """the C compiler: the one asked for, else clang, gcc or cc on PATH, else (Windows) HOOKSHOT_MINGW's clang, else Visual
    Studio's own clang"""
    for n in ([name] if name else ["clang", "gcc", "cc"]):
        found = shutil.which(n)
        if found:
            return found
    if name or not WINDOWS:
        return None
    mingw = os.environ.get("HOOKSHOT_MINGW")
    if mingw and os.path.isfile(os.path.join(mingw, "bin", "clang.exe")):
        return os.path.join(mingw, "bin", "clang.exe")
    vs = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft Visual Studio"
    for c in sorted(vs.glob("*/*/VC/Tools/Llvm/x64/bin/clang.exe"), reverse=True):
        return str(c)
    return None


def check_rule(cc):
    with tempfile.TemporaryDirectory() as d:
        src, exe = Path(d) / "ws_floor_test.c", Path(d) / ("ws_floor_test" + (".exe" if WINDOWS else ""))
        src.write_text(RULE, encoding="utf-8")
        libs = ["-ladvapi32"] if WINDOWS else []
        b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-I", str(ROOT / "host"), str(src), "-o", str(exe)]
                           + libs, capture_output=True, text=True)
        if b.returncode:
            print("FAIL: the fixture did not build:\n" + b.stdout + b.stderr)
            return False
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print(r.stdout.strip())
        return r.returncode == 0


def check_wired():
    """the host asks for it at start on Windows, with the switch's value, and logs what it got"""
    src = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    ok = True
    for needle, what in (('#include "ws_floor.h"', "the header included"),
                         ('ws_floor_apply(getenv("HOS_WS_FLOOR_MB")', "the call with the switch's value"),
                         ("ws_floor();", "the call at start")):
        if needle not in src:
            print("FAIL: hos_main.c: %s (%s)" % (what, needle))
            ok = False
    start = src.find("no_power_throttling();\n", src.find("timeBeginPeriod(1);"))
    if start < 0 or "ws_floor();" not in src[start:start + 400]:
        print("FAIL: hos_main.c: ws_floor() is not called beside no_power_throttling() at start")
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", default="")
    args = ap.parse_args()
    ok = check_wired()
    cc = compiler(args.cc)
    if not cc:
        print("SKIP: no C compiler (%s) for the rule" % (args.cc or "clang, gcc, cc"))
    else:
        ok = check_rule(cc) and ok
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
