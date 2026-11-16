"""nvnCommandBufferPushGraphicsConstants is checked against what the blit pass gives (host/nvn_native.c).

NVN2's full-screen blit shaders read graphics root table 0 as constant bank 24. The replay does not take the pushed bytes:
the recompiler lowers bank 24 to bank 17 and BlitConstantsPass (gpu/vk/eden/shader_recompiler/ir_opt/position_pass.cpp)
gives a blit's reads the values of a blit covering the whole render target. Donkey Kong Bananza pushes exactly those, so
the HLE only checks each push and logs once when a game pushes something else. blit_block_word and push_graphics_check
are cut from the source and compiled. Checks:

- blit_block_word's table is BlitConstantsPass's word(), offset by offset (read from position_pass.cpp, so a change to
  the pass fails here until the check follows it);
- the three blocks Bananza pushed in a run (2304x1296, 1536x864, 1676x943, full viewports) are what the pass gives;
- a block for another size, on that target, is not, and the first differing word is named;
- no target yet, a viewport that is not the whole target, and another table are "nothing to compare with".

python -I -B tests/test_push_graphics_constants.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block, between   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

# offset -> what the pass returns there, by name; every other offset is 0
TABLE = {0x0: "1/w", 0x10: "1/w", 0x4: "1/h", 0x14: "1/h", 0x8: "w", 0xc: "h",
         0x18: "1", 0x1c: "1", 0x38: "1", 0x3c: "1", 0x58: "w-1", 0x5c: "h-1"}


def pass_table(src):
    """BlitConstantsPass's word() as {offset: name}, read from position_pass.cpp"""
    body = between(src, "const auto word = [&](u32 offset) -> IR::U32 {", "default:")
    names = [
        (r"FPRecip\(width\)", "1/w"), (r"FPRecip\(height\)", "1/h"),
        (r"ConvertFToU\(32, ir\.FPAdd\(width", "w-1"), (r"ConvertFToU\(32, ir\.FPAdd\(height", "h-1"),
        (r"BitCast<IR::U32>\(width\)", "w"), (r"BitCast<IR::U32>\(height\)", "h"), (r"Imm32\(0x3f800000U\)", "1"),
    ]
    table, pending = {}, []
    for line in body.splitlines():
        for m in re.finditer(r"case (0x[0-9a-f]+):", line):
            pending.append(int(m.group(1), 16))
        if "return" in line:
            name = next((n for pat, n in names if re.search(pat, line)), None)
            if name is None:
                raise SystemExit("FAIL: a return in BlitConstantsPass's word() this check does not know: %s" % line.strip())
            for o in pending:
                table[o] = name
            pending = []
    return table


HARNESS = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
@WORD@
@CHECK@
static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
static void block_for(float w, float h, uint32_t out[4]) {   /* (as the game computes it: main 0x808ca7a8 / 0x808ca7cc) */
    const float v[4] = {1.0f / w, 1.0f / h, w, h};
    memcpy(out, v, 16);
}
static const char *name_of(uint32_t o, uint32_t v, float w, float h) {
    float f;
    memcpy(&f, &v, 4);
    if (o == 0x58 && v == (uint32_t)(w - 1)) return "w-1";
    if (o == 0x5c && v == (uint32_t)(h - 1)) return "h-1";
    if (v == 0) return "0";
    if (f == 1.0f / w) return "1/w";
    if (f == 1.0f / h) return "1/h";
    if (f == w) return "w";
    if (f == h) return "h";
    if (f == 1.0f) return "1";
    return "?";
}
int main(void) {
    for (uint32_t o = 0; o < 0x60; o += 4)
        printf("word 0x%x %s\n", o, name_of(o, blit_block_word(o, 2880.0f, 1620.0f), 2880.0f, 1620.0f));
    const uint32_t sizes[3][2] = {{2304, 1296}, {1536, 864}, {1676, 943}};
    for (int i = 0; i < 3; i++) {
        uint32_t b[4], at = 99;
        const float vp[4] = {0, 0, (float)sizes[i][0], (float)sizes[i][1]};
        block_for((float)sizes[i][0], (float)sizes[i][1], b);
        check(push_graphics_check(0, 0, 16, b, sizes[i][0], sizes[i][1], vp, &at) == 1, "Bananza's push is what the pass gives");
    }
    {   /* (a 1920x1080 block on a 2880x1620 target: the pass would give 2880x1620's) */
        uint32_t b[4], at = 99;
        const float vp[4] = {0, 0, 2880, 1620};
        block_for(1920.0f, 1080.0f, b);
        check(push_graphics_check(0, 0, 16, b, 2880, 1620, vp, &at) == 0 && at == 0, "another size: not, at word 0");
        uint32_t tail[2] = {b[2], b[3]};
        at = 99;
        check(push_graphics_check(0, 8, 8, tail, 2880, 1620, vp, &at) == 0 && at == 8, "an offset push: the first word named");
    }
    {
        uint32_t b[4], at;
        block_for(2880.0f, 1620.0f, b);
        const float full[4] = {0, 0, 2880, 1620}, part[4] = {0, 0, 1440, 1620};
        check(push_graphics_check(0, 0, 16, b, 0, 0, full, &at) == -1, "no target yet: nothing to compare with");
        check(push_graphics_check(0, 0, 16, b, 2880, 1620, part, &at) == -1, "a partial viewport: nothing to compare with");
        check(push_graphics_check(1, 0, 16, b, 2880, 1620, full, &at) == -1, "another table: nothing to compare with");
    }
    if (!bad) puts("ok");
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++"))
    args = parser.parse_args()
    src = (ROOT / "host/nvn_native.c").read_text(encoding="utf-8")
    passes = (ROOT / "gpu/vk/eden/shader_recompiler/ir_opt/position_pass.cpp").read_text(encoding="utf-8")
    want = pass_table(passes)
    if want != TABLE:
        raise SystemExit("FAIL: BlitConstantsPass's word() changed: %s (this check knows %s); make blit_block_word follow it"
                         % (sorted(want.items()), sorted(TABLE.items())))
    fixture = (HARNESS.replace("@WORD@", block(src, "static uint32_t blit_block_word(uint32_t offset, float w, float h) {"))
               .replace("@CHECK@", block(src, "static int push_graphics_check(")))
    with tempfile.TemporaryDirectory(prefix="push-graphics-") as tmp:
        folder = Path(tmp)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / "fixture"
        subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        got = {}
        for line in out.stdout.splitlines():
            m = re.match(r"word (0x[0-9a-f]+) (\S+)$", line)
            if m and m.group(2) != "0":
                got[int(m.group(1), 16)] = m.group(2)
        if got != TABLE:
            raise SystemExit("FAIL: blit_block_word is not BlitConstantsPass's word(): %s, want %s"
                             % (sorted(got.items()), sorted(TABLE.items())))
        print("\n".join(l for l in out.stdout.splitlines() if not l.startswith("word ")))
        if out.returncode or not out.stdout.rstrip().endswith("ok"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
