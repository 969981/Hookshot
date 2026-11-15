"""The Ampere -> Maxwell lowering takes a pass-through geometry stage's body (lower_to_maxwell.h passthrough_geometry_stage).

Donkey Kong Bananza's destructible terrain (the cave's ground and walls) is drawn by indexed indirect draws whose records a
pass-through geometry stage fills: it culls each triangle and appends the ones left (an atomic add on the record's count,
stores of the indices). Its program stopped the lowering at its first instruction, so it was drawn with no body, every
record kept the 0 the reset gave it and the ground stayed black. What it needed, checked here on programs of this check's
own (the shapes, not the game's code):

- the exit handler: LEPC Rd (0x34e) becomes MOV32I Rd, 0; 0x357, OUT RZ, R0, RZ (0x324) and 0x957 are dropped (Eden's
  frontend emits a pass-through stage's vertices at its end itself); other forms of them, or the same ops outside a
  pass-through stage, still fail;
- ISBERD Rd, [Ra] of the map region (0x923, the handle of input vertex Ra) becomes MOV Rd, Ra, as Eden's Maxwell ISBERD
  gives Ra back, only in a pass-through stage (a task or mesh program's ISBERD is lowered as before);
- F2I.U16 with any rounding (the terrain's indices: F2I.U16.TRUNC, mode 0x0021e800) is Maxwell's F2I.U16 with that
  rounding; the round-to-nearest form (0x00212800) is as before.

python -I -B tests/test_lower_passthrough_geometry.py [--cxx clang++]
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "g++"]

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
using Shader::Ampere::Instruction;
static Instruction insn(uint32_t a, uint32_t b, uint32_t c, uint32_t d) { Instruction i{}; i.words[0] = a; i.words[1] = b; i.words[2] = c; i.words[3] = d; return i; }
static const Instruction exit_insn = insn(0x0000794d, 0, 0x03800000, 0x000fea00);              // EXIT
static const Instruction lepc = insn(0x0006734e, 0, 0, 0x000fe400);                            // LEPC R6
static const Instruction mov_end = insn(0x00077802, 0x00000100, 0x00000f00, 0x000fc800);       // MOV R7, 0x100
static const Instruction add_end = insn(0x06067210, 0x00000007, 0x07f1e0ff, 0x000fca00);       // IADD3 R6, R6, R7, RZ
static const Instruction handler = insn(0x00007357, 0, 0, 0x0001e400);                         // 0x357 (reads R0)
static const Instruction isberd = insn(0x05097923, 0, 0, 0x000e2800);                          // ISBERD R9, [R5]
static const Instruction ald = insn(0xff0a7321, 0x00007009, 0, 0x000e2800);                    // ALD R10, a[0x70], R9
static const Instruction f2i_u16_trunc = insn(0x000b7305, 0x0000000a, 0x0021e800, 0x002fe200); // F2I.U16.TRUNC.FTZ R11, R10
static const Instruction out = insn(0x00ff7324, 0x000000ff, 0, 0x0001e200);                    // OUT RZ, R0, RZ
static const Instruction handler_end = insn(0x00007957, 0, 0, 0x000fe200);                     // 0x957
static Shader::Ampere::LoweredProgram lower(std::vector<Instruction> v, bool passthrough) {
    Shader::Ampere::passthrough_geometry_stage = passthrough;
    auto r = Shader::Ampere::LowerToMaxwell(v);
    Shader::Ampere::passthrough_geometry_stage = false;
    return r;
}
static const uint64_t PT = 7ULL << 16;
static uint64_t mov32i(uint32_t d, uint32_t v) { return 0x0100000000000000ULL | PT | d | (15ULL << 12) | (uint64_t{v} << 20); }
static uint64_t mov(uint32_t d, uint32_t s) { return 0x5c98000000000000ULL | PT | d | (uint64_t{s} << 20) | (15ULL << 39); }
static uint64_t f2i_u16(uint32_t d, uint32_t b, uint32_t rounding, bool ftz) {
    return 0x5cb0000000000000ULL | PT | d | (1ULL << 8) | (2ULL << 10) | (uint64_t{b} << 20) | (uint64_t{rounding} << 39) |
           (uint64_t{ftz} << 44);
}
static bool has(const std::vector<uint64_t>& v, uint64_t w) { for (uint64_t x : v) if (x == w) return true; return false; }
static int count_op(const std::vector<uint64_t>& v, uint64_t top12) { int n = 0; for (uint64_t x : v) n += (x >> 52) == top12; return n; }
int main() {
    const std::vector<Instruction> body{lepc, mov_end, add_end, handler, isberd, ald, f2i_u16_trunc, out, handler_end, exit_insn};
    {   // a pass-through stage: lowered; LEPC is MOV32I R6, 0, the handler is gone, ISBERD is MOV R9, R5
        const auto r = lower(body, true);
        if (!r) std::fprintf(stderr, "error at +%#x: %.*s\n", r.error_offset, (int)r.error.size(), r.error.data());
        CHECK(r);
        CHECK(r.instructions[0] == mov32i(6, 0));
        CHECK(has(r.instructions, mov(9, 5)));
        CHECK(count_op(r.instructions, 0xefc) == 0);   // (not the task and mesh programs' stage buffer read)
        CHECK(count_op(r.instructions, 0xf6e) == 0 && count_op(r.instructions, 0xfbe) == 0);   // (no Maxwell OUT)
        CHECK(has(r.instructions, f2i_u16(11, 250, 3, true)));   // (unsigned: from R250, NaN cleared)
    }
    {   // the same ops outside a pass-through stage: refused at the first, as before
        const auto r = lower(body, false);
        CHECK(!r);
        CHECK(r.error_offset == 0);
    }
    {   // an OUT of another form (OUT.EMIT R2, R3, RZ) in a pass-through stage: refused
        std::vector<Instruction> v{lepc, isberd, insn(0x03027324, 0x000000ff, 0x00000100, 0x0001e200), exit_insn};
        const auto r = lower(v, true);
        CHECK(!r);
        CHECK(r.error_offset == 2 * 16);
    }
    {   // an ISBERD with an offset in a pass-through stage: not the identity, refused
        std::vector<Instruction> v{insn(0x05097923, 0x00000400, 0, 0x000e2800), exit_insn};
        CHECK(!lower(v, true));
    }
    {   // F2I.U16 rounded to nearest (0x00212800), floor (bits 78-79 = 1) and ceil (2), FTZ or not
        const uint32_t modes[4] = {0x00212800, 0x00216800, 0x0021a800, 0x0020a800};
        const uint32_t want[4] = {0, 1, 2, 2};
        const bool ftz[4] = {true, true, true, false};
        for (int i = 0; i < 4; i++) {
            const auto r = lower({insn(0x00037305, 0x00000004, modes[i], 0x000fe400), exit_insn}, false);
            CHECK(r);
            CHECK(has(r.instructions, f2i_u16(3, 250, want[i], ftz[i])));
        }
    }
    std::puts("ok");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lower-gs-pass-") as tmp:
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
