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


# The link count through the real Link / Unlink / UnlinkAll / MoveAll (several holders, two multi-waits): each object's count
# must equal the holders linking it after every step, or WaitAny goes back to waking on every change (a phantom watcher)
LINKS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef long LONG;
typedef struct { int x; } SRWLOCK;
static SRWLOCK mw_lock;
static int lock_depth, checkpoints_locked;   /* (obj is a virtual-core scheduling point: never under mw_lock) */
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; lock_depth++; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; lock_depth--; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static LONG InterlockedDecrement(volatile LONG *p) { return --*p; }
typedef struct Obj {
    uint64_t event; int sem_max; struct Obj **links; int nlinks, caplinks; struct Obj *mw; volatile LONG mw_linked; int kind;
} Obj;
enum { K_EVENT = 1, K_TIMER, K_SEMA, K_QUEUE, K_MULTIWAIT, K_HOLDER };
HOLDER_ENUM
static Obj objs[64];
static Obj *obj_lookup(uint64_t key, int kind) { objs[key].kind = kind; return &objs[key]; }
static Obj *obj(uint64_t key, int kind) { if (lock_depth) checkpoints_locked++; return obj_lookup(key, kind); }
typedef struct { uint64_t x[8]; } Ctx;
#define ARG(i) (c->x[i])
#define HLE(name) static void name(Ctx *c)
FUNCTIONS
static int bad;
static void expect(const char *step, LONG e1, LONG e2) {
    if (objs[1].mw_linked != e1 || objs[2].mw_linked != e2) {
        printf("FAIL after %s: event1 %ld (want %ld), event2 %ld (want %ld)\n", step, objs[1].mw_linked, e1, objs[2].mw_linked, e2);
        bad = 1;
    }
}
static void call(void (*f)(Ctx *), uint64_t a, uint64_t b) { Ctx c = {{a, b}}; f(&c); }
int main(void) {
    const uint64_t A = 10, B = 11, h1 = 20, h2 = 21, h3 = 22, h4 = 23;
    objs[h1].event = 1; objs[h1].sem_max = H_EVENT;   /* (two holders over event 1, one over event 2, one over a thread) */
    objs[h2].event = 1; objs[h2].sem_max = H_EVENT;
    objs[h3].event = 2; objs[h3].sem_max = H_EVENT;
    objs[h4].event = 3; objs[h4].sem_max = H_THREAD;
    for (uint64_t h = h1; h <= h4; h++) call(LINK, A, h);
    expect("four links", 2, 1);
    call(UNLINK, h2, 0);
    expect("unlink one", 1, 1);
    call(UNLINK, h2, 0);
    expect("unlink it again", 1, 1);
    call(MOVEALL, B, A);
    expect("move all", 1, 1);
    if (objs[h1].mw != &objs[B] || objs[A].nlinks) { puts("FAIL: move all did not move the holders"); bad = 1; }
    call(UNLINKALL, B, 0);
    expect("unlink all", 0, 0);
    call(LINK, A, h1);
    call(UNLINKALL, B, 0);
    expect("link again, unlink all of the other", 1, 0);
    call(UNLINK, h1, 0);
    expect("unlink the last", 0, 0);
    if (checkpoints_locked) { printf("FAIL: %d scheduling points (obj) under mw_lock\n", checkpoints_locked); bad = 1; }
    puts(bad ? "FAILED" : "ok");
    return bad;
}
"""


def links_fixture(source):
    names = {"LINK": "LinkMultiWaitHolder", "UNLINK": "UnlinkMultiWaitHolder", "UNLINKALL": "UnlinkAllMultiWaitHolder",
             "MOVEALL": "MoveAllMultiWaitHolder"}
    parts, defines = [block(source, "static Obj *holder_target(")], []
    for alias, name in names.items():
        marker = re.search(r"HLE\((_ZN2nn2os\d+%sE\w*)\)" % name, source)
        parts.append(block(source, marker.group(0)))
        defines.append("#define %s %s" % (alias, marker.group(1)))
    enum = re.search(r"enum \{ H_EVENT[^}]*\};", source).group(0)
    return LINKS.replace("HOLDER_ENUM", enum).replace("FUNCTIONS", "\n".join(parts + defines))


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
        (folder / "links.cpp").write_text(links_fixture(source), encoding="utf-8")
        links = folder / ("links.exe" if os.name == "nt" else "links")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "links.cpp"), "-o", str(links)], check=True, timeout=120)
        result = subprocess.run([str(links)], capture_output=True, text=True, timeout=30)
        print("links:", result.stdout.strip())
        if result.returncode:
            raise SystemExit("FAIL: link counts")
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
    print("PASS: a change wakes WaitAny only for an object a holder links (HOS_MW_WAKE=all: every change); link, unlink,"
          " unlink all and move all keep the count; every poke passes its object")


if __name__ == "__main__":
    main()
