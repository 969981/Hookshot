"""A video buffer acquired before its picture is reserved: the next picture decoded goes into it, and its sync is reached
then, not before (host/nn_video.c, from SYNC_RESERVED_TAG to dec_submit).

Before, such a buffer's sync was reached at the next submission while that submission's picture went to the next
acquire: Donkey Kong Bananza's guide-movie player counted one buffer done more than the pictures it had sent, found no
entry for it (main+0xe1ada0) and aborted after a loop or a few. The section is cut from the source and compiled with
stand-ins for the decoding (decode_pending, put_picture) and the system events. Checks:

- a reservation's handle is not reached; a sync point event asked for it waits;
- with nothing decoded, nothing is filled; a decoded picture goes into the reserved buffer, the handle is reached and
  the event signalled;
- two reservations are filled in the order they were made, whatever their buffer indexes;
- a reservation given up (the buffer released) is reached, and its event signalled;
- any other handle (0, the hardware's done fences) is reached, and its event due at once.

python -I -B tests/test_video_reserved_buffers.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef long long LONG64;
typedef struct { int locked; } SRWLOCK;
#define SRWLOCK_INIT {0}
static int locks_held;
static void AcquireSRWLockExclusive(SRWLOCK *l) { if (l->locked) { puts("FAIL: a lock taken twice"); } l->locked = 1; locks_held++; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { l->locked = 0; locks_held--; }
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return ++*p; }
#define MAX_SLOTS 16
#define POOL_MAGIC 0x504f4f4c
typedef struct {
    uint64_t slots[MAX_SLOTS];      /* registered pools: nonzero alive, 0xdead a pool finalized */
    uint64_t reserved[MAX_SLOTS];
    int qn;                 /* pictures decoded, not in a buffer yet */
    int put[8], nput;       /* the buffers put_picture filled, in order */
} Dec;
static void decode_pending(Dec *d) { (void)d; }
static int pool_alive = 1;
static void *host_of(uint64_t obj, uint32_t magic) { return magic == POOL_MAGIC && obj && obj != 0xdead ? &pool_alive : NULL; }
static int signalled[8], nsignalled;
void hos_system_event_signal(uint64_t key) { if (nsignalled < 8) signalled[nsignalled++] = (int)key; }
static volatile LONG64 submissions;

@SECTION@

static void put_picture(Dec *d, int slot, int reserved) {
    if (!reserved) puts("FAIL: a reserved buffer filled as an acquire");
    d->put[d->nput++] = slot;
    d->qn--;
}

static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
int main(void) {
    static Dec d;
    for (int i = 0; i < MAX_SLOTS; i++) d.slots[i] = 0x1000 + i;
    const uint64_t h = sync_reserve();
    d.reserved[1] = h;
    check(h != 0 && !hos_video_sync_reached(h), "a reservation's handle is not reached");
    check(hos_video_sync_defer(7, h) == 1 && hos_video_sync_waiting(7), "an event asked for it waits");
    fill_reserved(&d);
    check(d.nput == 0 && !hos_video_sync_reached(h) && nsignalled == 0, "nothing decoded: nothing filled");
    d.qn = 1;
    fill_reserved(&d);
    check(d.nput == 1 && d.put[0] == 1 && d.reserved[1] == 0, "a decoded picture goes into the reserved buffer");
    check(hos_video_sync_reached(h) && nsignalled == 1 && signalled[0] == 7 && !hos_video_sync_waiting(7),
          "its handle reached, the event signalled");

    d.nput = 0, nsignalled = 0;
    const uint64_t a = sync_reserve(), b = sync_reserve();
    d.reserved[5] = a, d.reserved[2] = b;   /* (the older one in the higher buffer index) */
    d.qn = 1;
    fill_reserved(&d);
    check(d.nput == 1 && d.put[0] == 5 && hos_video_sync_reached(a) && !hos_video_sync_reached(b),
          "the older reservation gets the picture first");
    d.qn = 1;
    fill_reserved(&d);
    check(d.nput == 2 && d.put[1] == 2 && hos_video_sync_reached(b), "then the next one");

    nsignalled = 0;
    const uint64_t g = sync_reserve();
    d.reserved[3] = g;
    check(hos_video_sync_defer(9, g) == 1, "an event for a buffer that will be released waits");
    drop_reserved(&d, 3);
    check(d.reserved[3] == 0 && hos_video_sync_reached(g) && nsignalled == 1 && signalled[0] == 9,
          "released before its picture: reached, the event signalled");

    /* a reserved buffer whose pool was finalized while the buffer stayed registered: given up, not filled */
    nsignalled = 0, d.nput = 0;
    const uint64_t dead = sync_reserve(), alive = sync_reserve();
    d.reserved[6] = dead, d.slots[6] = 0xdead;
    d.reserved[7] = alive;
    check(hos_video_sync_defer(13, dead) == 1, "an event for a buffer whose pool will go waits");
    d.qn = 1;
    fill_reserved(&d);
    check(d.reserved[6] == 0 && hos_video_sync_reached(dead) && nsignalled == 1 && signalled[0] == 13,
          "its pool gone: the reservation given up, its sync reached, its event signalled");
    check(d.nput == 1 && d.put[0] == 7 && hos_video_sync_reached(alive) && d.qn == 0,
          "the picture goes to the next reserved buffer instead");
    check(hos_video_sync_reached(0) && hos_video_sync_defer(11, 0) == 0 && !hos_video_sync_waiting(11),
          "a done fence (0) is reached, its event due at once");
    check(locks_held == 0, "every lock let go");
    if (!bad) puts("ok");
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++"))
    parser.add_argument("--source", type=Path, default=ROOT / "host/nn_video.c")
    args = parser.parse_args()
    src = args.source.read_text(encoding="utf-8")
    start = src.index("#define SYNC_RESERVED_TAG")
    end = src.index("static void dec_submit(Ctx *c) {")
    if "static int submit_refused(" in src[start:end]:   # (Longshot #110's input limit, checked by test_video_input_limit.py)
        end = src.rindex("\n/*", start, src.index("static int submit_refused("))
    section = src[start:end].replace("void hos_system_event_signal(uint64_t key);\n", "")
    fixture = HARNESS.replace("@SECTION@", section)
    with tempfile.TemporaryDirectory(prefix="video-reserved-") as tmp:
        folder = Path(tmp)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / "fixture"
        subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        print(out.stdout, end="")
        if out.returncode or out.stdout.strip() != "ok":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
