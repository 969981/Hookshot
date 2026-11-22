"""The replay's sizes and ranges from the game are bounded before they size a loop, a copy or a draw (the 2026-11-14 audit's
PR-B: V1, V5, V6, V7).

- shadow_drop_range and guest_cpu_wrote (gpu/vk/vk_memory.cpp), compiled with stubs: a range that ends past guest memory, or
  a size of 2^50 bytes, walks guest memory's pages at most, not 2^43 regions or 2^38 pages (each run here has 20 seconds;
  before, they did not end);
- staging() (gpu/vk/vk_texture.cpp) gives nothing past NVN_TEX_MAX_BYTES and compares an offset and a size by subtracting:
  a wrapped size rounded up to 0, took the 64 MB spare arena, and its caller wrote past it;
- draw() (gpu/vk/vk_draw.cpp) leaves out a direct draw of no or a negative count: as an unsigned count, -1 was 4.29
  billion vertices;
- the pool and buffer ranges stay in guest memory: import_pool, memory_pool_created and gpu_buffer_bind compare with
  host->guest_size() without a sum that wraps.

python -I -B tests/test_replay_bounds.py [--cxx g++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#define NVN_TEX_MAX_BYTES (16ull << 30)
static std::map<uint32_t, int> shadow_regions;
static long long erased, guest_asked, written;
static void shadow_erase(uint32_t) { erased++; }
static bool wb_diff_on() { return true; }
static bool mirror_on() { return true; }   // (#590: the bookkeeping is only kept with mirrors)
constexpr int kPageShift = 12;
static int shadow_written_by_replay;
static void shadow_written_to(int, const uint32_t*, int k) { written += k; }
struct Host { uint64_t gpu_guest(uint64_t a) { guest_asked++; return a < (1ull << 40) ? a : 0; } };
static Host host_, *host = &host_;
@DROP@
@WROTE@
int main(int argc, char** argv) {
    const int which = atoi(argv[1]);
    if (which == 0) {   /* a range from a wrapped size: ends at guest memory's end */
        shadow_regions[(uint32_t)((0x200000000ull) >> 21)] = 1;
        shadow_drop_range(0x200000000ull, ~0ull);
        printf("dropped %lld\n", erased);
    } else if (which == 1) {   /* 2^50 bytes from the game: no page of it is a write the game made */
        guest_cpu_wrote(0x10000000ull, 1ull << 50);
        printf("asked %lld\n", guest_asked);
    } else {   /* an ordinary 64 KB write is still reported, page by page */
        guest_cpu_wrote(0x10000000ull, 64u << 10);
        printf("asked %lld written %lld\n", guest_asked, written);
        return guest_asked == 16 && written == 16 ? 0 : 1;
    }
    return 0;
}
"""


def sources_checked(tex, draw, mem):
    bad = []
    st = block(tex, "Staging staging(uint64_t n, uint64_t align = 256) {")
    head = st.split("Arena a;")[0]
    if "if (n > NVN_TEX_MAX_BYTES)" not in head:
        bad.append("staging() takes any size (a wrapped one took the spare arena)")
    if "at + n <= a.size" in head or head.count("n <= a.size && at <= a.size - n") != 2:
        bad.append("staging() adds an offset to a size before comparing (it wraps)")
    dr = block(draw, "void draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d) {")
    guard = dr.find("if (!d.indirect && !d.mesh && d.count <= 0) return;")
    if guard < 0 or guard > dr.find("mesh_indirect_convert(cmd, d);"):
        bad.append("draw() records a direct draw of a negative count (4.29 billion vertices as unsigned)")
    ip = block(mem, "bool import_pool(uint64_t lo, uint64_t hi, Pool& p) {")
    if "if (hi > host->guest_size())" not in ip:
        bad.append("import_pool imports a range past guest memory")
    pc = block(mem, "void memory_pool_created(uint64_t gpu, uint64_t size) {")
    if "size > host->guest_size() || gpu > host->guest_size() - size" not in pc:
        bad.append("memory_pool_created queues a range past guest memory")
    bb = block(mem, "BufferRef gpu_buffer_bind(uint64_t gpu, uint64_t size, bool written) {")
    if "size > host->guest_size() || gpu > host->guest_size() - size" not in bb:
        bad.append("gpu_buffer_bind binds a range past guest memory")
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    vk = ROOT / "gpu" / "vk"
    mem = (vk / "vk_memory.cpp").read_text(encoding="utf-8")
    bad = sources_checked((vk / "vk_texture.cpp").read_text(encoding="utf-8"), (vk / "vk_draw.cpp").read_text(encoding="utf-8"), mem)
    fixture = (HARNESS.replace("@DROP@", block(mem, "void shadow_drop_range(uint64_t lo, uint64_t hi) {"))
               .replace("@WROTE@", block(mem, "void guest_cpu_wrote(uint64_t gpu, uint64_t n) {")))
    with tempfile.TemporaryDirectory(prefix="replay-bounds-") as tmp:
        folder = Path(tmp)
        (folder / "t.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / "t"
        subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", str(folder / "t.cpp"), "-o", str(exe)], check=True)
        for which, what in ((0, "shadow_drop_range over a range ending past guest memory"),
                            (1, "guest_cpu_wrote of 2^50 bytes"), (2, "guest_cpu_wrote of an ordinary 64 KB write")):
            try:
                out = subprocess.run([str(exe), str(which)], capture_output=True, text=True, timeout=20)
                print("%s: %s" % (what, out.stdout.strip()))
                if out.returncode:
                    bad.append(what + ": not as before")
            except subprocess.TimeoutExpired:
                bad.append(what + ": still walking after 20 s")
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: the replay's sizes and ranges from the game are bounded before they size a loop, a copy or a draw")


if __name__ == "__main__":
    main()
