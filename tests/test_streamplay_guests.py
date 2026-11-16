"""GameShare's guest displays (host/nn_applet.c): no guest ever joins, so they are made and let go as nothing, and each
guest id has a native window of its own.

The handlers are cut out of production unmodified and run with stubs: CreateIndividualDisplays and
DestroyIndividualDisplays succeed; GetNativeWindowHandle gives each guest id one window, the same one each time, and
different ids different ones (Kirby Air Riders keeps three). Left to the lifted SDK, CreateIndividualDisplays read
guest 0 inside nn::vi::sdk::Initialize.

python -I -B tests/test_streamplay_guests.py [--cc gcc]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]

FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { uint64_t x[32]; } Ctx;
typedef long LONG;
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static LONG InterlockedExchange(volatile LONG *p, LONG v) { LONG o = *p; *p = v; return o; }
static uint64_t next = 0x1000;
static uint64_t hos_sys_alloc(uint64_t size, uint64_t align) { (void)align; uint64_t a = next; next += size; return a; }
static void hos_log(const char *f, ...) { (void)f; }
#define HLE(sym) static void sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
@HANDLERS@
static uint64_t window(uint64_t id) {
    Ctx c = {{0}};
    c.x[0] = id;
    _ZN2nn10streamplay21GetNativeWindowHandleENS0_7GuestIdE(&c);
    return c.x[0];
}
int main(void) {
    Ctx c = {{0}};
    c.x[0] = 3;
    _ZN2nn10streamplay24CreateIndividualDisplaysEi(&c);
    CHECK(c.x[0] == 0);
    const uint64_t w0 = window(0), w1 = window(1), w2 = window(2);
    CHECK(w0 && w1 && w2 && w0 != w1 && w1 != w2 && w0 != w2);
    CHECK(window(1) == w1);
    Ctx d = {{0}};
    _ZN2nn10streamplay25DestroyIndividualDisplaysEv(&d);
    CHECK(d.x[0] == 0);
    printf("PASS: GameShare's guest displays: made and let go as nothing, a window of its own for each guest\n");
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
    src_text = (ROOT / "host" / "nn_applet.c").read_text(encoding="utf-8")
    handlers = "\n".join(block(src_text, "HLE(_ZN2nn10streamplay" + n) for n in (
        "24CreateIndividualDisplaysEi)", "25DestroyIndividualDisplaysEv)", "21GetNativeWindowHandleENS0_7GuestIdE)"))
    with tempfile.TemporaryDirectory(prefix="streamplay-") as tmp:
        src, exe = Path(tmp, "f.c"), Path(tmp, "f")
        src.write_text(FIXTURE.replace("@HANDLERS@", handlers), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-o", str(exe), str(src)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
