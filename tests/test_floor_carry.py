"""A moving floor carries Link above 100 fps as it does at 60 (host/oot_high_fps.h oot_floor_cut, nx2/oot_patches.py
patch_floor_carry).

The character controller's three follow-surface updates (f_8015d490, f_80159dc0, f_838baa40) keep only a tenth of the
floor's velocity when the physics' inverse step is over 100.0. With the frames' own time as the step the inverse step
is the frame rate (times the time scale's inverse), so every update above 100 fps cut it. The patch makes them compare
with oot_floor_cut: 100 over the frame step under one frame, so from 61 to 240 fps the cut comes at exactly the time
scales where it comes at 60 fps; 100 at 60 and 30 fps and with no frame time (a cap of 60).

The production patch is applied to authored gates of the lifted shape (the 100.0 into x8, then s1, the fcmp and the
branch to the tenth) and compiled with oot_high_fps.h: at 120, 144 and 240 fps a floor at the game's speed is carried
whole, a slow motion under 0.6 still cuts it, and the unpatched gates fail the same check. No game files or lifted
function body are used. After NX2navi (chrg6) commit c84724d.

python -I -B tests/test_floor_carry.py [--cc gcc]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import oot_patches  # noqa: E402

GATE = ('void %s(Ctx *__restrict A64_NOESC c) {\n'
        '    c->x[0] = 0;\n' + oot_patches.FLOOR_OLD +
        '    { if (!a64_fcmp32(c,c->vreg[0],c->vreg[1])) { fcmp_flags(c); } }\n'
        'if ((c->n == c->v) && !c->z) { goto %s; }\n'
        '    return;\n'
        '%s:;\n'
        '    c->x[0] = 1;   /* (a tenth of the floor\'s velocity) */\n'
        '}\n')
LIFTED = '#include "a64.h"\n' + "".join(GATE % (head[5:head.index("(")], label, label)
                                       for head, label in oot_patches.FLOOR_SITES)
CONTEXT = """#pragma once
#include <stdint.h>
#include <string.h>
#define A64_NOESC
typedef struct {
    uint64_t x[32], vreg[32][2];
    uint8_t n, z, v;
} Ctx;
static inline void a64_vset64(uint64_t *v, uint64_t x) { v[0] = x; v[1] = 0; }
/* (ordered here: the flags of fcmp for two numbers) */
static inline int a64_fcmp32(Ctx *c, const uint64_t *a, const uint64_t *b) { (void)c; (void)a; (void)b; return 0; }
static inline void fcmp_flags(Ctx *c) {
    float a, b;
    memcpy(&a, &c->vreg[0][0], 4);
    memcpy(&b, &c->vreg[1][0], 4);
    c->n = a < b, c->z = a == b, c->v = 0;
}
"""
FIXTURE = r'''#include "oot_high_fps.h"
#include <stdio.h>
#include "a64.h"

static uint32_t seconds_bits;   /* (the host's frame seconds: 0 at a cap of 60) */
static int failures;
#define CHECK(test, what) do { if (!(test)) { printf("FAIL: %s\n", what); failures++; } } while (0)
uint32_t hos_oot_floor_cut(uint32_t inverse_step_bits) { (void)inverse_step_bits; return oot_float_bits(oot_floor_cut(seconds_bits)); }
@GATES@
typedef void (*Gate)(Ctx *);
static const Gate gates[3] = {f_8015d490, f_80159dc0, f_838baa40};
/* 1: the gate keeps a tenth of the floor's velocity, at fps frames a second (0: no frame time) and the game at scale */
static int tenth(int g, double fps, double scale) {
    Ctx c = {0};
    seconds_bits = fps ? oot_float_bits((float)(1.0 / fps)) : 0;
    const double frame_rate = fps ? fps : 60.0;
    c.vreg[0][0] = oot_float_bits((float)(frame_rate / scale));   /* (the physics' inverse step) */
    gates[g](&c);
    return (int)c.x[0];
}

int main(void) {
    for (int g = 0; g < 3; g++) {
        CHECK(!tenth(g, 0, 1.0), "60 fps (no frame time): the floor carries whole");
        CHECK(tenth(g, 0, 0.5), "60 fps in a slow motion of 0.5: the game's tenth");
        CHECK(!tenth(g, 120, 1.0), "120 fps: the floor carries Link whole");
        CHECK(!tenth(g, 144, 1.0), "144 fps: the floor carries Link whole");
        CHECK(!tenth(g, 240, 1.0), "240 fps: the floor carries Link whole");
        CHECK(!tenth(g, 30, 1.0), "30 fps: whole, as the game has it");
        CHECK(tenth(g, 30, 0.25), "30 fps in a slow motion of 0.25: the game's tenth");
        for (int fps = 61; fps <= 240; fps++)
            for (int k = 0; k < 26; k++) {
                const double scale = 0.3 + 0.05 * k + 0.01;   /* (clear of 0.6, where float rounding decides either way) */
                if (tenth(g, fps, scale) != (60.0 / scale > 100.0)) {
                    printf("at %d fps, time scale %.2f\n", fps, scale);
                    CHECK(0, "above 60 fps the cut comes at the time scales where it comes at 60 fps");
                    fps = 241;
                    break;
                }
            }
    }
    CHECK(oot_floor_cut(0) == 100.0f && oot_floor_cut(oot_float_bits(1.0f / 30.0f)) == 100.0f, "100 at 60 and 30 fps");
    CHECK(oot_floor_cut(oot_float_bits(0.0f / 0.0f)) == 100.0f && oot_floor_cut(oot_float_bits(-1.0f)) == 100.0f,
          "a frame time that is not a positive number keeps the game's 100");
    if (!failures) puts("PASS: floor carry");
    return failures != 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    patched, count = oot_patches.patch_floor_carry(LIFTED)
    assert count == 3 and patched.count("hos_oot_floor_cut(") == 3 + 1, patched   # (the three gates and the declaration)
    assert oot_patches.patch_floor_carry(patched) == (patched, 3)   # (patched before: counted, left as it is)
    other = LIFTED.replace("goto L_8015dad4;", "goto L_8015dad8;", 1)   # (another game version: a gate elsewhere)
    assert oot_patches.patch_floor_carry(other)[1] == 2
    none = LIFTED.replace("void f_8", "void f_9")
    assert oot_patches.patch_floor_carry(none) == (none, 0)
    with tempfile.TemporaryDirectory(prefix="floor-carry-") as directory:
        directory = Path(directory)
        (directory / "a64.h").write_text(CONTEXT, encoding="utf-8")
        source, binary = directory / "fixture.c", directory / "fixture.exe"
        command = [args.cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-label", "-I", str(ROOT / "host"),
                   str(source), "-o", str(binary)]
        source.write_text(FIXTURE.replace("@GATES@", patched.replace('#include "a64.h"\n', "")), encoding="utf-8")
        subprocess.run(command, check=True)
        subprocess.run([str(binary)], check=True, timeout=60)
        # (the game's gates compile, then fail the same checks)
        source.write_text(FIXTURE.replace("@GATES@", LIFTED.replace('#include "a64.h"\n', "")), encoding="utf-8")
        subprocess.run(command, check=True)
        native = subprocess.run([str(binary)], text=True, capture_output=True, timeout=60)
        if native.returncode == 0 or "120 fps: the floor carries Link whole" not in native.stdout:
            raise SystemExit("FAIL: the game's gate did not fail the 120 fps check")
        print("PASS: the game's gate rejected by the same checks")


if __name__ == "__main__":
    main()
