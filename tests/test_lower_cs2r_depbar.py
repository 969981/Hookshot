"""The Ampere -> Maxwell lowering takes DEPBAR's scoreboard-list form and CS2R SRZ into a high register pair.

Both stopped OoT mesh programs from lowering: DEPBAR.LE SB0, 0x0, {4,3,2,1} as "native dependency barrier mode", and
CS2R R238/R250, SRZ as "native special-register read" or "native program uses lowering scratch registers". DEPBAR is a
scoreboard wait (nothing in the IR); CS2R's pair now goes through the extended registers (REGMAP_nx2) like the ALU forms.
python -I -B tests/test_lower_cs2r_depbar.py [--cxx clang++]
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "gpu" / "vk" / "eden" / "shader_recompiler" / "frontend" / "ampere" / "lower_to_maxwell.h"

FIXTURE = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
using Shader::Ampere::Instruction;
static Instruction insn(uint32_t a, uint32_t b, uint32_t c, uint32_t d) { Instruction i{}; i.words[0] = a; i.words[1] = b; i.words[2] = c; i.words[3] = d; return i; }
static const Instruction exit_insn = insn(0x0000794d, 0, 0x03800000, 0x000fea00);   // EXIT
static Shader::Ampere::LoweredProgram lower(std::vector<Instruction> code) {
    code.push_back(exit_insn);
    return Shader::Ampere::LowerToMaxwell(code);
}
int main() {
    // DEPBAR.LE SB5, 0x3 (the form taken before) and DEPBAR.LE SB0, 0x0, {4,3,2,1} / {3,2,1}
    CHECK(lower({insn(0x0000791a, 0x0000d0c0, 0, 0x000fce00)}));
    CHECK(lower({insn(0x0000791a, 0x0000801e, 0, 0x000fce00)}));
    CHECK(lower({insn(0x0000791a, 0x0000800e, 0, 0x000fce00)}));
    // a DEPBAR mode outside those bits is still refused
    CHECK(!lower({insn(0x0000791a, 0x0001801e, 0, 0x000fce00)}));
    // CS2R R238, SRZ below the reserved registers (no uniform bank here): two plain MOV32I of 0
    {
        const auto r = lower({insn(0x00007805 | (238u << 16), 0, 0x0001ff00, 0x000fe200)});
        CHECK(r);
        CHECK(r.register_map_words == 0);
        CHECK(r.instructions.size() >= 2);
        CHECK(r.instructions[0] == (0x0100000000000000ULL | (7ULL << 16) | (15ULL << 12) | 238));
        CHECK(r.instructions[1] == (0x0100000000000000ULL | (7ULL << 16) | (15ULL << 12) | 239));
    }
    // CS2R Rn, SRZ in the scratch registers: two MOV32I of 0, each after a REGMAP_nx2 prefix that maps a marker pair to Rn
    for (uint32_t reg : {246u, 248u, 250u, 252u}) {
        const auto r = lower({insn(0x00007805 | (reg << 16), 0, 0x0001ff00, 0x000fe200)});
        CHECK(r);
        CHECK(r.register_map_words == 2);
        int movs = 0;
        uint32_t marker = 255;
        for (size_t i = 0; i + 1 < r.instructions.size(); ++i) {
            const uint64_t w = r.instructions[i];
            if ((w >> 48) != 0xefe0) continue;
            CHECK(((w >> 8) & 0xff) == reg);   // the run's target
            const uint64_t mov = r.instructions[i + 1];
            CHECK((mov >> 56) == 0x01);   // MOV32I
            CHECK(((mov >> 20) & 0xffffffffULL) == 0);
            const uint32_t dest = static_cast<uint32_t>(mov & 0xff);
            if (marker == 255) marker = dest;
            CHECK(dest == marker + static_cast<uint32_t>(movs));
            CHECK((w & 0xff) == marker);   // the run's number
            movs++;
        }
        CHECK(movs == 2);
        CHECK(marker >= 216 && marker < 246 && marker != 239);
    }
    std::printf("PASS: DEPBAR's list form and CS2R SRZ into high registers lower\n");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=shutil.which("clang++") and "clang++" or "g++")
    a = p.parse_args()
    if not HEADER.exists():
        raise SystemExit(f"missing {HEADER}")
    with tempfile.TemporaryDirectory(prefix="lower-cs2r-depbar-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wno-everything" if "clang" in a.cxx else "-w",
                        "-I" + str(ROOT / "gpu" / "vk" / "eden"), "-I" + str(ROOT / "gpu" / "vk" / "compat"),
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
