"""Quads with 8-bit indices are drawn on a device without uint8 index support.

draw() skipped every indexed draw with 8-bit indices (d.itype == 0) when the device has no VK_EXT_index_type_uint8,
before the quads path. Quads do not need it: their indices are read on the CPU (one byte each for 8-bit ones) and the
draw binds 32-bit indices made in the stream. Now only draws that give 8-bit indices to the GPU are skipped.

The real code of draw(), from the 8-bit skip to the end of the quads path, is compiled with fakes (no Vulkan, no GPU)
on a device without uint8 index support: 8-bit quads, with indices 0x80 and 0xFF among them, must give their triangles
as 32-bit indices, bound as UINT32 and drawn; 8-bit triangles must still be skipped.

python -I -B tests/test_uint8_quads.py [--cxx g++]
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r"""
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
enum VkIndexType { VK_INDEX_TYPE_UINT16 = 0, VK_INDEX_TYPE_UINT32 = 1, VK_INDEX_TYPE_UINT8_EXT = 1000265000 };
enum { NVN_DRAW_PRIMITIVE_TRIANGLES = 0x4, NVN_DRAW_PRIMITIVE_QUADS = 0x7 };
using VkCommandBuffer = int;
using VkBuffer = int;
int skipped, draws, binds, bound_type = -1, drawn_count, drawn_instances;
#define SKIP_HERE() { ++skipped; return; }
struct Dev { bool index_uint8 = false; } dev;
struct Occ { void issue() {} } occ;
struct Stream { uint8_t* mapped; VkBuffer stream = 1; };
uint8_t mapped[4096];
Stream stream{mapped};
Stream* cur = &stream;
struct Stats { int draws = 0; } ps;
int st_draws;
std::vector<uint8_t> game;   // (the game's index buffer)
bool memory_read_as_submitted(uint64_t, void* dst, uint64_t n) {
    if (n > game.size()) return false;
    std::memcpy(dst, game.data(), n);
    return true;
}
uint64_t stream_alloc(uint64_t, uint64_t) { return 0; }
void bind_index(VkCommandBuffer, VkBuffer, uint64_t, VkIndexType type) { ++binds, bound_type = type; }
void vkCmdDrawIndexed(VkCommandBuffer, uint32_t count, uint32_t instances, uint32_t, int32_t, uint32_t) {
    ++draws, drawn_count = (int)count, drawn_instances = (int)instances;
}
struct DrawArgs {
    bool indexed = true, indirect = false;
    int itype = 0, prim = NVN_DRAW_PRIMITIVE_QUADS, count = 0, first = 0, instances = 1, base_vertex = 0, base_instance = 0;
    uint64_t ibuf = 0x1000;
};
void draw(const DrawArgs& d) {
    VkCommandBuffer cmd = 0;
@BLOCK@
}
int failures;
void check(bool ok, const char* label) {
    if (!ok) ++failures;
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", label);
}
void reset() { skipped = draws = binds = drawn_count = drawn_instances = 0, bound_type = -1; std::memset(mapped, 0xCD, sizeof mapped); }
int main() {
    reset();
    game = {0x00, 0x80, 0xFF, 0x01, 0x7F, 0x80, 0xFE, 0xFF};
    DrawArgs quads;
    quads.count = 8;
    draw(quads);
    const uint32_t want[12] = {0x00, 0x80, 0xFF, 0x00, 0xFF, 0x01, 0x7F, 0x80, 0xFE, 0x7F, 0xFE, 0xFF};
    check(!skipped && draws == 1, "8-bit quads are drawn without uint8 index support");
    check(binds == 1 && bound_type == VK_INDEX_TYPE_UINT32, "their indices are bound as UINT32");
    check(drawn_count == 12 && drawn_instances == 1, "two quads draw 12 indices");
    check(std::memcmp(mapped, want, sizeof want) == 0, "each byte index (0x80 and 0xFF too) becomes one 32-bit index, two triangles a quad");
    reset();
    DrawArgs tris;
    tris.prim = NVN_DRAW_PRIMITIVE_TRIANGLES, tris.count = 3;
    draw(tris);
    check(skipped == 1 && !draws && !binds, "8-bit triangles are still skipped without uint8 index support");
    std::printf("%d failures\n", failures);
    return failures ? 1 : 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--source", type=Path, default=ROOT)
    args = parser.parse_args()
    src = (args.source / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    try:
        start = src.index("    const VkIndexType itype = d.itype == 0 ? VK_INDEX_TYPE_UINT8_EXT", src.index("void draw(VkCommandBuffer"))
        quads = src.index("    if (d.prim == NVN_DRAW_PRIMITIVE_QUADS) {", start)
        end = src.index("\n        return;\n    }\n", quads) + len("\n        return;\n    }\n")
    except ValueError:
        raise SystemExit("the 8-bit skip or the quads path in draw() moved: update this test's anchors") from None
    with tempfile.TemporaryDirectory(prefix="uint8-quads-", ignore_cleanup_errors=True) as tmp:
        cpp, exe = Path(tmp) / "test.cpp", Path(tmp) / "test.exe"
        cpp.write_text(FIXTURE.replace("@BLOCK@", src[start:end]), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Wno-unused-variable", str(cpp), "-o", str(exe)],
                       check=True, timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    print(result.stdout + result.stderr, end="")
    if result.returncode != 0 or "0 failures" not in result.stdout.splitlines():
        raise SystemExit("quads with 8-bit indices are not drawn right without uint8 index support")
    print("quads with 8-bit indices are drawn without uint8 index support")


if __name__ == "__main__":
    main()
