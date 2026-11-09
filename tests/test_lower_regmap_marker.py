"""The Ampere -> Maxwell lowering never gives a renamed high GPR a marker number another field of the same instruction
names (issues 696 and 667: overexposed stalks, grass and flowers).

A native GPR in the scratch range (R246 up) is renamed to a marker, a number of the uniform bank (R216-R245), and the
REGMAP_nx2 prefix maps the marker to the extended register. The marker was chosen among the numbers no field seen so
far named, so a later field could name it too. OoT's foliage mesh d38b0d52 has FFMA R247, R180, R216, R247: R247 took
the marker 216, so the prefix mapped 216 to R247 and the Maxwell FFMA read R247 for its R216 (or, with R216 an extended
register itself, its run came after and took the marker back to R216, and the FFMA wrote over R216). The light probes'
weighted average then divided by data, not by the weights. Each case here lowers one instruction, applies the prefix
as the translator does (ApplyRegisterMap: runs in order, the last one wins) and checks every field of the Maxwell word
names the native register it had.
python -I -B tests/test_lower_regmap_marker.py [--cxx clang++]
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
using Shader::Ampere::Instruction;
static int failed = 0;
#define CHECK(x, what) do { if (!(x)) { std::fprintf(stderr, "FAIL: %s: %s\n", what, #x); failed++; } } while (0)
static Instruction insn(uint32_t a, uint32_t b, uint32_t c, uint32_t d) { Instruction i{}; i.words[0] = a; i.words[1] = b; i.words[2] = c; i.words[3] = d; return i; }
static const Instruction exit_insn = insn(0x0000794d, 0, 0x03800000, 0x000fea00);   // EXIT
// FFMA Rd, Ra, Rb, Rc (register form), as the native programs encode it
static Instruction ffma(uint32_t d, uint32_t a, uint32_t b, uint32_t c) {
    return insn(0x00007223 | (d << 16) | (a << 24), b, 0x00010000 | c, 0x000fe200);
}
// The register a Maxwell field names once the prefix before the word is applied (register_map_nx2.cpp)
struct Map {
    uint16_t to[256]{};
    void apply(uint64_t w) {
        const uint32_t runs = (uint32_t)((w >> 40) & 3);
        const uint32_t number_at[2]{0, 20}, target_at[2]{8, 28};
        for (uint32_t i = 0; i < runs && i < 2; ++i) {
            const uint32_t number = (uint32_t)((w >> number_at[i]) & 0xff), target = (uint32_t)((w >> target_at[i]) & 0xff);
            const uint32_t length = (uint32_t)((w >> (36 + 2 * i)) & 3) + 1;
            for (uint32_t k = 0; k < length && number + k < 255 && target + k < 255; ++k) to[number + k] = (uint16_t)(256 + target + k);
        }
    }
    uint32_t operator()(uint32_t r) const { return r < 255 && to[r] ? to[r] : r; }
};
static void check_ffma(uint32_t d, uint32_t a, uint32_t b, uint32_t c, const char* what) {
    const auto r = Shader::Ampere::LowerToMaxwell(std::vector<Instruction>{ffma(d, a, b, c), exit_insn});
    CHECK(r, what);
    if (!r) return;
    // the prefix words, then the FFMA (0x5980...: Rd 0-7, Ra 8-15, Rb 20-27, Rc 39-46)
    Map map;
    size_t at = 0;
    while (at < r.instructions.size() && (r.instructions[at] >> 52) == 0xefe) map.apply(r.instructions[at++]);
    CHECK(at < r.instructions.size() && (r.instructions[at] >> 55) == (0x5980000000000000ULL >> 55), what);
    if (at >= r.instructions.size()) return;
    const uint64_t w = r.instructions[at];
    const uint32_t fd = map((uint32_t)(w & 0xff)), fa = map((uint32_t)((w >> 8) & 0xff));
    const uint32_t fb = map((uint32_t)((w >> 20) & 0xff)), fc = map((uint32_t)((w >> 39) & 0xff));
    // each field the native register itself, as an ordinary or an extended register (256 + n)
    const auto same = [](uint32_t got, uint32_t native) { return got == native || got == 256 + native; };
    CHECK(same(fd, d), what);
    CHECK(same(fa, a), what);
    CHECK(same(fb, b), what);
    CHECK(same(fc, c), what);
    if (failed) std::fprintf(stderr, "  %s: Rd %u Ra %u Rb %u Rc %u\n", what, fd, fa, fb, fc);
}
int main() {
    // (d38b0d52, OoT's foliage mesh) the destination renamed, the multiplier R216 the marker block's first number
    check_ffma(247, 180, 216, 247, "FFMA R247, R180, R216, R247");
    // a pair's marker block (R246: two numbers) over R216 and R217
    check_ffma(246, 65, 216, 220, "FFMA R246, R65, R216, R220");
    check_ffma(246, 53, 217, 246, "FFMA R246, R53, R217, R246");
    // the scratch registers in both sources too
    check_ffma(248, 216, 218, 250, "FFMA R248, R216, R218, R250");
    if (failed) return 1;
    std::printf("PASS: no REGMAP marker is a number another field of the instruction names\n");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=shutil.which("clang++") and "clang++" or "g++")
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="lower-regmap-marker-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wno-everything" if "clang" in a.cxx else "-w",
                        "-I" + str(ROOT / "gpu" / "vk" / "eden"), "-I" + str(ROOT / "gpu" / "vk" / "compat"),
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
