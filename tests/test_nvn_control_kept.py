"""A program keeps a copy of each stage's control section from when its shaders were set (host/nvn_native.c
kept_control), so the replay, which reads the section at the draw, finds it whatever the game did with that memory.

kept_control is cut out of production unmodified and run against stand-in guest memory and system region:

  copy        a control section (the magic 0x98761234 first) is copied: the copy holds what the section held, and still
              does after the game writes something else there (Kirby Air Riders reuses the memory within frames: the
              replay read another object and left every draw out).
  same        the same content set again, from anywhere, is the same copy: the game sets the same shaders again and again.
  other       other content is a copy of its own.
  not one     a pointer without the magic, null, or outside the guest window stays as it is.

python -I -B tests/test_nvn_control_kept.py [--cc gcc]
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
#include <string.h>
#include <stdarg.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { int x; } SRWLOCK;
#define SRWLOCK_INIT {0}
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
enum { MEMORY = 1 << 20, SYSTEM = 1 << 19 };
static unsigned char memory[MEMORY];
#define GP(a) ((void *)(memory + (a)))
#define G32(a) (*(uint32_t *)GP(a))
static void *hos_guest_range(uint64_t a, uint64_t n) { return a < MEMORY && n <= MEMORY - a ? GP(a) : NULL; }
static uint64_t sys_next = SYSTEM;
static uint64_t hos_sys_alloc(uint64_t size, uint64_t align) {
    uint64_t a = (sys_next + align - 1) & ~(align - 1);
    sys_next = a + size;
    CHECK(sys_next <= MEMORY);
    memset(GP(a), 0, size);
    return a;
}
static void hos_log(const char *f, ...) { (void)f; }
@KEPT@
static void section(uint64_t at, uint8_t fill) {
    memset(GP(at), fill, NVN_CONTROL_KEEP);
    G32(at) = 0x98761234u;
}
int main(void) {
    section(0x1000, 0x11);
    const uint64_t a = kept_control(0x1000);
    CHECK(a >= SYSTEM && a != 0x1000 && !memcmp(GP(a), GP(0x1000), NVN_CONTROL_KEEP));
    unsigned char was[NVN_CONTROL_KEEP];
    memcpy(was, GP(0x1000), sizeof was);
    memset(GP(0x1000), 0xee, NVN_CONTROL_KEEP);                 /* (the game's next object there) */
    CHECK(!memcmp(GP(a), was, sizeof was));
    section(0x3000, 0x11);
    CHECK(kept_control(0x3000) == a);                            /* same */
    section(0x5000, 0x22);
    const uint64_t b = kept_control(0x5000);
    CHECK(b != a && b >= SYSTEM && ((unsigned char *)GP(b))[8] == 0x22);   /* other */
    memset(GP(0x7000), 0x33, NVN_CONTROL_KEEP);
    CHECK(kept_control(0x7000) == 0x7000 && kept_control(0) == 0 && kept_control(MEMORY - 16) == MEMORY - 16);   /* not one */
    printf("PASS: each stage's control section is kept from when its shaders were set\n");
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
    src = (ROOT / "host" / "nvn_native.c").read_text(encoding="utf-8")
    sizes = src[src.index("enum { NVN_CONTROL_KEEP"):src.index(";", src.index("enum { NVN_CONTROL_KEEP")) + 1]
    kept = sizes + "\n" + block(src, "static uint64_t kept_control(uint64_t ctl)")
    ss = block(src, "static int set_shaders(")
    if "kept_control(o->sd[i].control)" not in ss:
        raise SystemExit("FAIL: set_shaders does not keep its stages' control sections")
    with tempfile.TemporaryDirectory(prefix="nvn-control-") as tmp:
        c, exe = Path(tmp, "f.c"), Path(tmp, "f")
        c.write_text(FIXTURE.replace("@KEPT@", kept), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-o", str(exe), str(c)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
