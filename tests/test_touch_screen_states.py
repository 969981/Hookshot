"""The touch screen's states (host/nn_hid.c): no touch, in every form a game reads them with.

The three handlers and their sampling counter are cut out of production unmodified and run against fake guest memory:

  every N    both reads, GetTouchScreenState<N> and GetTouchScreenStates<N>, have a handler for every N from 1 to 16
             (a game uses whichever it was built with).
  state<16>  Kirby Air Riders' read: one state of 16 + 16 * 0x28 bytes, zero but its sampling number, nothing past it;
             a buffer not wholly the game's is not written.
  states<1>  one state (16 + 0x28 bytes) and the answer 1; a count below 1, or a buffer past the end, writes nothing.
  states<16> and state<1>: as before (Mario Kart World's and Donkey Kong Bananza's); the sampling numbers of every
             read go up together.

python -I -B tests/test_touch_screen_states.py [--cc gcc]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]

FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { uint64_t x[32]; } Ctx;
typedef long long LONG64;
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return ++*p; }
#define HLE(sym) static void sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
enum { MEMORY = 2048 };
static unsigned char memory[MEMORY];
#define GP(a) ((void *)(memory + (a)))
#define G64(a) (*(uint64_t *)GP(a))
static void *hos_guest_range(uint64_t a, uint64_t n) { return a < MEMORY && n <= MEMORY - a ? GP(a) : NULL; }
@HANDLERS@
#define CALL(fn, a0, a1) do { Ctx c = {{0}}; c.x[0] = (a0); c.x[1] = (a1); fn(&c); r = c.x[0]; } while (0)
int main(void) {
    uint64_t r;
    memset(memory, 0xa5, sizeof memory);
    CALL(_ZN2nn3hid19GetTouchScreenStateILm16EEEvPNS0_16TouchScreenStateIXT_EEE, 64, 0);
    CHECK(G64(64) == 1 && memory[64 + 16 + 16 * 0x28] == 0xa5);
    for (int i = 8; i < 16 + 16 * 0x28; i++) CHECK(memory[64 + i] == 0);
    memset(memory, 0xa5, sizeof memory);
    CALL(_ZN2nn3hid19GetTouchScreenStateILm16EEEvPNS0_16TouchScreenStateIXT_EEE, MEMORY - 64, 0);
    CHECK(memory[MEMORY - 64] == 0xa5);   /* (past the end: nothing) */
    CALL(_ZN2nn3hid20GetTouchScreenStatesILm1EEEiPNS0_16TouchScreenStateIXT_EEEi, 64, 4);
    CHECK(r == 1 && G64(64) == 2 && memory[64 + 16 + 0x28] == 0xa5);
    for (int i = 8; i < 16 + 0x28; i++) CHECK(memory[64 + i] == 0);
    memset(memory, 0xa5, sizeof memory);
    CALL(_ZN2nn3hid20GetTouchScreenStatesILm1EEEiPNS0_16TouchScreenStateIXT_EEEi, 64, 0);
    CHECK(r == 0 && memory[64] == 0xa5);
    CALL(_ZN2nn3hid20GetTouchScreenStatesILm1EEEiPNS0_16TouchScreenStateIXT_EEEi, MEMORY - 16, 1);
    CHECK(r == 0 && memory[MEMORY - 16] == 0xa5);
    CALL(_ZN2nn3hid20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi, 128, 16);
    CHECK(r == 1 && G64(128) == 3 && memory[128 + 16 + 16 * 0x28] == 0xa5 && memory[128 + 15 + 16 * 0x28] == 0);
    CALL(_ZN2nn3hid19GetTouchScreenStateILm1EEEvPNS0_16TouchScreenStateIXT_EEE, 1536, 0);
    CHECK(G64(1536) == 4);
    printf("PASS: the touch screen's states, no touch, in each form\n");
    return 0;
}
'''


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = p.parse_args()
    hid = (ROOT / "host" / "nn_hid.c").read_text(encoding="utf-8")
    if "static volatile LONG64 touch_sampling;" not in hid:
        raise SystemExit("host/nn_hid.c: the touch screen's sampling counter is not where this check expects it")
    for n in range(1, 17):
        for form in ("19GetTouchScreenStateILm%dEEEvPNS0_16TouchScreenStateIXT_EEE)" % n,
                     "20GetTouchScreenStatesILm%dEEEiPNS0_16TouchScreenStateIXT_EEEi)" % n):
            if not re.search(r"^HLE\(_ZN2nn3hid" + re.escape(form), hid, re.M):
                raise SystemExit("FAIL: no handler for nn::hid %s" % form)
    parts = ["static volatile LONG64 touch_sampling;", block(hid, "static void touch_state(Ctx *c, int n)"),
             block(hid, "static void touch_states(Ctx *c, int n)")] + [block(hid, "HLE(_ZN2nn3hid" + n) for n in (
        "19GetTouchScreenStateILm16EEEvPNS0_16TouchScreenStateIXT_EEE)",
        "19GetTouchScreenStateILm1EEEvPNS0_16TouchScreenStateIXT_EEE)",
        "20GetTouchScreenStatesILm1EEEiPNS0_16TouchScreenStateIXT_EEEi)",
        "20GetTouchScreenStatesILm16EEEiPNS0_16TouchScreenStateIXT_EEEi)")]
    with tempfile.TemporaryDirectory(prefix="touch-states-") as tmp:
        src, exe = Path(tmp, "f.c"), Path(tmp, "f")
        src.write_text(FIXTURE.replace("@HANDLERS@", "\n".join(parts)), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(src)],
                       check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
