"""Validate the opt-in base-scale hook and its resource-refresh contract.

The default fixture is game-free. --lifted-source additionally executes the
actual mode-selection/notification blocks from the user's own translated game;
it does not copy game code into this repository or require running the game.
"""
import argparse
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import oot_native_resolution as patcher


def check(condition, message):
    if not condition:
        raise AssertionError(message)


FIXTURE = '#include "a64.h"\n' + patcher.HEAD + '\n' + patcher.ANCHOR + '''
    c->x[13] = recomp_load8(c, c->x[0] + 1353);
    if (c->x[13] != c->x[14]) goto L_80670648;
    return;
L_80670648:;
    recomp_store8(c, c->x[0] + 1354, c->x[13]);
    notifications++;
}
'''

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define A64_NOESC
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { uint64_t x[32]; int z; } Ctx;
static unsigned char mem[8192];
static unsigned notifications, logs;
volatile int hos_native_scene;
void hos_log(const char *format, ...) { (void)format; ++logs; }
static uint64_t readmem(uint64_t a, unsigned n) {
    CHECK(a <= sizeof(mem) - n); uint64_t v = 0; memcpy(&v, mem + a, n); return v;
}
static void writemem(uint64_t a, uint64_t v, unsigned n) {
    CHECK(a <= sizeof(mem) - n); memcpy(mem + a, &v, n);
}
#define LOAD(n) static uint64_t recomp_load##n(Ctx *c, uint64_t a) { (void)c; return readmem(a, n/8); }
#define STORE(n) static void recomp_store##n(Ctx *c, uint64_t a, uint64_t v) { (void)c; writemem(a, v, n/8); }
LOAD(8) LOAD(32) LOAD(64) STORE(8) STORE(32) STORE(64)
#define recomp_store32_sp recomp_store32
#define recomp_store64_sp recomp_store64
static void recomp_ldp32(Ctx *c, uint64_t a, uint64_t *x, uint64_t *y) {
    *x = recomp_load32(c, a); *y = recomp_load32(c, a+4);
}
static void recomp_stp64_sp(Ctx *c, uint64_t a, uint64_t x, uint64_t y) {
    recomp_store64(c, a, x); recomp_store64(c, a+8, y);
}
static void mock_call(Ctx *c, uint64_t target) {
    if (target == 1) { c->x[0] = 1; return; }
    CHECK(target == 2);
    CHECK((readmem(256+8, 4) & 0x200) != 0);
    CHECK(readmem(c->x[1], 8) == 256);
    ++notifications; c->x[0] = 0;
}
#define A64_ICALL(c, t) mock_call(c, t)
'''

CASES = r'''
static void initialize(unsigned mode) {
    memset(mem, 0x5a, sizeof(mem)); notifications = logs = 0;
    writemem(256+8, 0, 4); writemem(256+12, 0, 4);
    writemem(256+1350, 0, 1); writemem(256+1352, 0, 1);
    writemem(256+1353, mode, 1); writemem(256+1354, mode, 1); writemem(256+1355, mode, 1);
    writemem(256+1392, 4000, 8); writemem(4000, 2, 8); writemem(4016, 1, 8);
    for (unsigned p=0; p<2; ++p) {
        unsigned t = 256+724+520*p;
        writemem(t, 0x3f8000003f800000ULL, 8);
        writemem(t+8, 0x3f4ccccd3f4ccccdULL, 8);
        writemem(t+16, 0x3f2aaaab3f2aaaabULL, 8);
    }
}
static void run(void) {
    Ctx c = {0}; c.x[0] = 256; c.x[1] = 4500; c.x[31] = 8192;
    f_806702e0(&c);
}
static void check_table(unsigned profile, uint64_t expected) {
    unsigned t = 256+724+520*profile;
    CHECK(readmem(t, 8) == 0x3f8000003f800000ULL);
    CHECK(readmem(t+8, 8) == expected);
    CHECK(readmem(t+16, 8) == 0x3f2aaaab3f2aaaabULL);
    CHECK(readmem(t-8, 8) == 0x5a5a5a5a5a5a5a5aULL);
    CHECK(readmem(t+24, 8) == 0x5a5a5a5a5a5a5a5aULL);
}
int main(void) {
    initialize(1); hos_native_scene = 0; run();
    CHECK(notifications == 0 && logs == 0);
    check_table(0, 0x3f4ccccd3f4ccccdULL); check_table(1, 0x3f4ccccd3f4ccccdULL);

    hos_native_scene = 1; run();
    CHECK(notifications == 1 && logs == 2);
    check_table(0, 0x3f8000003f800000ULL); check_table(1, 0x3f8000003f800000ULL);
    CHECK(readmem(256+1354, 1) == 1);
    run(); CHECK(notifications == 1 && logs == 2);  // no refresh every frame

    for (unsigned mode=0; mode<3; ++mode) {
        initialize(mode); run(); CHECK(readmem(256+1354, 1) == mode);
        CHECK(notifications == 1); check_table(0, 0x3f8000003f800000ULL);
    }
    // Alternate mode-selector branch uses +1355 without changing its choice.
    initialize(1); writemem(256+12, 0x20, 4); writemem(256+1353, 2, 1); run();
    CHECK(readmem(256+1354, 1) == 1 && notifications == 1);

    // Reject an unknown table independently; do not overwrite custom settings.
    initialize(1); writemem(256+724, 0, 8); run();
    CHECK(readmem(256+732, 8) == 0x3f4ccccd3f4ccccdULL);
    check_table(1, 0x3f8000003f800000ULL); CHECK(logs == 1);
    initialize(1); writemem(256+732, 0, 8); writemem(256+1260, 0, 8); run();
    CHECK(notifications == 0 && logs == 0);

    // When disabled, existing mode transitions still propagate unchanged.
    initialize(1); hos_native_scene = 0; writemem(256+1353, 2, 1); run();
    CHECK(readmem(256+1354, 1) == 2 && notifications == 1 && logs == 0);
    check_table(0, 0x3f4ccccd3f4ccccdULL);
    puts("PASS: opt-in isolation, both profiles, stock-table guards, unchanged modes, one refresh, normal mode transition");
    return 0;
}
'''


def run_compiled(cc, body, folder, name):
    source = folder / (name + ".c")
    binary = folder / (name + ".exe")
    source.write_text(HARNESS + body + CASES, encoding="utf-8")
    subprocess.run([cc, "-std=c11", "-O2", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--lifted-source", type=Path)
    args = parser.parse_args()
    patched = patcher.patch(FIXTURE)
    check(patcher.patch(patched) == patched, "source patch must be idempotent")
    check(patcher.patch("unrelated source") is None, "unrelated file must be skipped")
    for invalid in (FIXTURE.replace(patcher.ANCHOR, ""), FIXTURE + FIXTURE,
                    FIXTURE.replace("goto L_80670648;", "return;")):
        try:
            patcher.patch(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("different layout must fail closed")
    with tempfile.TemporaryDirectory(prefix="hookshot-native-resolution-") as temp:
        folder = Path(temp)
        # Duplicate definitions must not partially patch an earlier chunk.
        files = [folder / "code_000.c", folder / "code_001.c"]
        for path in files:
            path.write_text(FIXTURE, encoding="utf-8")
        try:
            patcher.apply(folder, strict=True)
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate configuration must be rejected")
        check(all(p.read_text(encoding="utf-8") == FIXTURE for p in files), "failed scan changed code")
        for path in files:
            path.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            patcher.apply(folder)
        # The simple fixture does not implement the alternate selector; add it
        # explicitly, while the optional owned-code case exercises its real logic.
        body = patched[patched.index(patcher.HEAD):].replace(
            'c->x[13] = recomp_load8(c, c->x[0] + 1353);',
            'c->x[13] = recomp_load8(c, c->x[0] + ((recomp_load32(c, c->x[0] + 12) & 0x20) ? 1355 : 1353));')
        run_compiled(args.cc, body, folder, "contract")
        if args.lifted_source:
            owned = patcher.patch(args.lifted_source.read_text(encoding="utf-8"))
            check(owned is not None, "configuration function absent from owned chunk")
            start = owned.index(patcher.HEAD)
            first = owned[start:owned.index("L_80670364:;", start)]
            mode_start = owned.index("L_80670648:;", start)
            mode = owned[mode_start:owned.index("L_806706b4:;", mode_start)]
            # Stop at the join after notification, before unrelated engine work.
            run_compiled(args.cc, first + "L_80670364:; return;\n" + mode + "}\n", folder, "owned-mode-blocks")
            print("PASS: user's actual lifted mode-selection and notification blocks")
    print("PASS: layout guards, atomic duplicate rejection, source idempotence")


if __name__ == "__main__":
    main()
