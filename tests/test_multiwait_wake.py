"""A WaitAny is woken only by an object some holder links into a multi-wait (host/nn_os_sync.c poke_multiwait): the actual
function, compiled with a harness that counts WakeAllConditionVariable. Every change anywhere woke every WaitAny before.

python -I -B tests/test_multiwait_wake.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long LONG;
typedef struct { int x; } SRWLOCK;
typedef struct { int x; } CONDITION_VARIABLE;
#define SRWLOCK_INIT {0}
#define CONDITION_VARIABLE_INIT {0}
static LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c) { LONG v = *p; if (v == c) *p = x; return v; }
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static int wakes;
static void WakeAllConditionVariable(CONDITION_VARIABLE *cv) { (void)cv; wakes++; }
typedef struct Obj { volatile LONG mw_linked; } Obj;
static SRWLOCK mw_lock = SRWLOCK_INIT;
static CONDITION_VARIABLE mw_cv = CONDITION_VARIABLE_INIT;
static volatile LONG mw_waiting;
POKE
static int check(const char *what, int waiting, LONG linked, int expect) {
    Obj o = {linked};
    mw_waiting = waiting;
    wakes = 0;
    poke_multiwait(&o);
    if (wakes != expect) { printf("FAIL %s: %d wakes, expected %d\n", what, wakes, expect); return 1; }
    return 0;
}
int main(int argc, char **argv) {
    int all = argc > 1;   /* (HOS_MW_WAKE=all set by the caller) */
    int bad = 0;
    bad += check("a waiter, the object linked by no holder", 1, 0, all ? 1 : 0);
    bad += check("a waiter, the object linked", 1, 2, 1);
    bad += check("no waiter, the object linked", 0, 1, 0);
    bad += check("no waiter, not linked", 0, 0, 0);
    puts(bad ? "FAILED" : "ok");
    return bad != 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--source", type=Path, default=ROOT / "host/nn_os_sync.c")
    args = parser.parse_args()
    source = args.source.read_text(encoding="utf-8")

    # (the source: every change passes the object that changed; link and unlink count the holders watching it)
    calls = re.findall(r"poke_multiwait\(([^)]*)\);", source)
    assert calls and all(re.fullmatch(r"\w+", c) for c in calls), "a poke_multiwait call without the object: %r" % calls
    for name, op in (("LinkMultiWaitHolder", "InterlockedIncrement(&o->mw_linked)"),
                     ("UnlinkMultiWaitHolder", "InterlockedDecrement(&o->mw_linked)")):
        body = block(source, "HLE(_ZN2nn2os%d%sE" % (len(name), name))
        assert "holder_target(h)" in body and op in body, name + " no longer keeps the holder count"

    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="multiwait-wake-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(HARNESS.replace("POKE", block(source, "static void poke_multiwait(")),
                                            encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True,
                       timeout=120)
        for mode in ("targeted", "all"):
            env.pop("HOS_MW_WAKE", None)
            argv = [str(exe)]
            if mode == "all":
                env["HOS_MW_WAKE"] = "all"
                argv.append("all")
            result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
            print(mode + ":", result.stdout.strip())
            if result.returncode:
                raise SystemExit("FAIL: " + mode)
    print("PASS: a change wakes WaitAny only for an object a holder links (HOS_MW_WAKE=all: every change); link and unlink"
          " keep the count; every poke passes its object")


if __name__ == "__main__":
    main()
