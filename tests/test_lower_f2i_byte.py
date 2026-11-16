"""The Ampere -> Maxwell lowering takes F2I to an 8-bit destination (F2I.S8 / F2I.U8) to F2I.32 and a clamp.

Five of Donkey Kong Bananza's vertex programs stopped at "native float-to-integer conversion mode (native op 0x305)",
each at F2I.S8.TRUNC (mode word 0x0021e100: the destination size bits 75-76 clear). Maxwell's F2I has no 8-bit form, so
the lowering converts to 32 bits with the same rounding and clamps: IMNMX with 127 (the minimum), then -128 (the maximum)
for S8; with 255 for U8, whose 32-bit conversion already saturates at 0. The 32-bit forms are unchanged (no clamp).

python -I -B tests/test_lower_f2i_byte.py [--cxx clang++]
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "g++"]

ROOT = Path(__file__).resolve().parents[1]
EDEN = ROOT / "gpu" / "vk" / "eden" / "shader_recompiler"

FIXTURE = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
using Shader::Ampere::Instruction;
static Instruction insn(uint32_t a, uint32_t b, uint32_t c, uint32_t d) { Instruction i{}; i.words[0] = a; i.words[1] = b; i.words[2] = c; i.words[3] = d; return i; }
static const Instruction exit_insn = insn(0x0000794d, 0, 0x03800000, 0x000fea00);   // EXIT
static Shader::Ampere::LoweredProgram lower(Instruction i) { return Shader::Ampere::LowerToMaxwell(std::vector<Instruction>{i, exit_insn}); }
static const uint64_t PT = 7ULL << 16;
// Maxwell: F2I.S32/U32 Rd, Rb (rounding at 39-40), IMNMX Rd, Rd, R252 (PT: the minimum; bit 42: the maximum), MOV32I R252
static uint64_t f2i32(uint32_t d, uint32_t b, bool sign, uint32_t rounding, bool ftz) {
    return 0x5cb0000000000000ULL | PT | d | (2ULL << 8) | (2ULL << 10) | (uint64_t{sign} << 12) | (uint64_t{b} << 20) |
           (uint64_t{rounding} << 39) | (uint64_t{ftz} << 44);
}
static uint64_t mov32i(uint32_t d, uint32_t v) { return 0x0100000000000000ULL | PT | d | (15ULL << 12) | (uint64_t{v} << 20); }
static uint64_t imnmx(uint32_t d, bool max, bool sign) {
    return 0x5c20000000000000ULL | PT | d | (uint64_t{d} << 8) | (252ULL << 20) | (7ULL << 39) | (uint64_t{max} << 42) |
           (uint64_t{sign} << 48);
}
static bool has(const std::vector<uint64_t>& v, uint64_t w) { for (uint64_t x : v) if (x == w) return true; return false; }
static int count_imnmx(const std::vector<uint64_t>& v) { int n = 0; for (uint64_t x : v) n += (x >> 52) == 0x5c2; return n; }
int main() {
    {   // F2I.S8.TRUNC.FTZ R21, R3 (Bananza, program 1ce503e856c52fda +0xa0): F2I.S32.TRUNC, then min 127, max -128
        const auto r = lower(insn(0x00157305, 0x00000003, 0x0021e100, 0x0001e400));
        CHECK(r);
        const auto& v = r.instructions;
        CHECK(v.size() >= 5);
        CHECK(v[0] == f2i32(21, 3, true, 3, true));
        CHECK(v[1] == mov32i(252, 127));
        CHECK(v[2] == imnmx(21, false, true));
        CHECK(v[3] == mov32i(252, 0xffffff80));
        CHECK(v[4] == imnmx(21, true, true));
    }
    {   // F2I.S8 rounded to nearest (bits 78-79 = 0), floor (1), ceil (2): the same, with that rounding
        for (uint32_t rnd = 0; rnd < 3; rnd++) {
            const auto r = lower(insn(0x00037305, 0x00000013, 0x00202100 | (rnd << 14), 0x000fe600));
            CHECK(r);
            CHECK(r.instructions[0] == f2i32(3, 0x13, true, rnd, false));
            CHECK(count_imnmx(r.instructions) == 2);
        }
    }
    {   // F2I.U8.TRUNC R5, R7: unsigned, clamped to 255 only (the 32-bit conversion saturates at 0)
        const auto r = lower(insn(0x00057305, 0x00000007, 0x0020e000, 0x000fe400));
        CHECK(r);
        CHECK(has(r.instructions, mov32i(252, 255)));
        CHECK(has(r.instructions, imnmx(5, false, false)));
        CHECK(count_imnmx(r.instructions) == 1);
    }
    {   // F2I.S32.TRUNC (0x0020f100): as before, no clamp
        const auto r = lower(insn(0x00057305, 0x00000007, 0x0020f100, 0x000fe400));
        CHECK(r);
        CHECK(r.instructions[0] == f2i32(5, 7, true, 3, false));
        CHECK(count_imnmx(r.instructions) == 0);
    }
    {   // an F2I mode still unknown (the size bits 75-76 = 1 with an F32 source) is still refused
        const auto r = lower(insn(0x00057305, 0x00000007, 0x0020e900, 0x000fe400));
        CHECK(!r);
    }
    std::puts("ok");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lower-f2i-byte-") as tmp:
        folder = Path(tmp)
        (folder / "t.cpp").write_text(FIXTURE, encoding="utf-8")
        exe = folder / "t"
        subprocess.run([args.cxx, "-std=c++20", "-O1", "-Wno-everything" if "clang" in args.cxx else "-w",
                        "-I" + str(ROOT / "gpu" / "vk" / "eden"), "-I" + str(ROOT / "gpu" / "vk" / "compat"),
                        str(folder / "t.cpp"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        print(out.stdout + out.stderr, end="")
        if out.returncode or out.stdout.strip() != "ok":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
