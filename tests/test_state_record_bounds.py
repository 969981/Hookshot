"""A state list record's counts are checked where they are used (gpu/vk/vk_draw.cpp state_v1_portable), not only by its caller:
a record is a file's (hookshot_pipeline_states*.bin: written by the game, shipped, or a damaged or malicious copy), and
state_v1_portable reads and writes answers[i] for i below the record's nanswers, an array of 24. It relied on state_list_read
having refused nanswers > 24 first. Now it refuses such a record itself.

Game-free: StateRec and state_v1_portable cut from the source by their text and compiled with stubs, then run on records with
24 answers (made portable), 25 and 4000 (refused, mesh or not), and none (a mesh record that cannot be told apart, as before).

    python -I -B tests/test_state_record_bounds.py [--cxx g++] [--source gpu/vk/vk_draw.cpp] [--expect-unbounded]

--expect-unbounded: the source is one that relies on its caller (main before this check): the check must fail on it.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block  # noqa: E402

HEAD = r"""
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <type_traits>
#include <vector>
enum VkPrimitiveTopology { VK_PRIMITIVE_TOPOLOGY_POINT_LIST, VK_PRIMITIVE_TOPOLOGY_LINE_LIST, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST };
constexpr VkPrimitiveTopology kMeshDrawTopology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
struct Program {
    struct Answer {
        uint16_t stage, desc;
        uint8_t type;
        bool image = false;
    };
};
uint64_t answers_hash_of(const std::vector<Program::Answer>& a) { return a.size(); }
"""

MAIN = r"""
// (the record and what follows it in memory: a write past answers[24] lands in `after`, which the check watches)
struct Guarded { StateRec r; uint32_t after[64]; };
static int failed;
#define CHECK(x) do { if (!(x)) { std::printf("FAIL: %s\n", #x); failed++; } } while (0)
static Guarded record(uint32_t nanswers, bool mesh) {
    Guarded g;
    std::memset(&g, 0, sizeof g);
    g.r.mesh = mesh, g.r.code[2] = 0, g.r.topo = 0;   // (topo not the mesh draw's: the fallback's record, answers renumbered)
    g.r.nanswers = nanswers;
    for (uint32_t i = 0; i < 24; i++) g.r.answers[i] = 3u << 28 | i << 12;   // (stage 3, past the generated vertex stage at 1)
    for (uint32_t& a : g.after) a = 0xA5A5A5A5u;
    return g;
}
static bool after_intact(const Guarded& g) {
    for (uint32_t a : g.after) if (a != 0xA5A5A5A5u) return false;
    return true;
}
int main() {
    Guarded g = record(24, true);
    CHECK(state_v1_portable(g.r) && g.r.topo == (uint32_t)kMeshDrawTopology && (g.r.answers[23] >> 28) == 2 && after_intact(g));
    for (uint32_t n : {25u, 26u, 4000u, 0xFFFFFFFFu}) {
        g = record(n, true);
        const bool used = state_v1_portable(g.r);
        if (used) std::printf("FAIL: a mesh record with %u answers is used\n", n), failed++;
        if (!after_intact(g)) std::printf("FAIL: a mesh record with %u answers wrote past its answers\n", n), failed++;
        g = record(n, false);
        if (state_v1_portable(g.r)) std::printf("FAIL: a record with %u answers is used\n", n), failed++;
    }
    g = record(0, true);
    CHECK(!state_v1_portable(g.r));   // (a mesh record with no answers cannot be told apart: as before)
    g = record(3, false);
    CHECK(state_v1_portable(g.r));
    if (!failed) std::puts("ok");
    return failed ? 1 : 0;
}
"""


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cxx", default=shutil.which("clang++") or shutil.which("g++") or "c++")
    p.add_argument("--source", type=Path, default=ROOT / "gpu" / "vk" / "vk_draw.cpp")
    p.add_argument("--expect-unbounded", action="store_true", help="the source relies on its caller: the check must fail")
    a = p.parse_args()
    src = a.source.read_text(encoding="utf-8")
    rec = block(src, "struct StateRec {") + ";\n"
    fn = block(src, "bool state_v1_portable(StateRec& r) {")
    with tempfile.TemporaryDirectory(prefix="state-bounds-") as tmp:
        cpp, exe = Path(tmp) / "t.cpp", Path(tmp) / "t"
        cpp.write_text(HEAD + rec + fn + MAIN, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", str(cpp), "-o", str(exe)], check=True)
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print(r.stdout, end="")
        if r.returncode and not r.stdout.strip():   # (it died before a line: a record past its bounds, unchecked)
            print("FAIL: the check died (exit %d): %s" % (r.returncode, r.stderr.strip()[-300:]))
    if a.expect_unbounded:
        if not r.returncode:
            raise SystemExit("FAIL: --expect-unbounded, but the record's counts were checked")
        print("PASS: the check catches a record whose counts its function does not check")
        return
    if r.returncode:
        raise SystemExit(1)
    print("PASS: state_v1_portable refuses a record with more answers than it holds, and writes nothing past them")


if __name__ == "__main__":
    main()
