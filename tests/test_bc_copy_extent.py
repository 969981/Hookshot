"""A copy into a texture kept as BC (big ASTC, gpu/vk/vk_texture.cpp) is made of the region's whole 4x4 blocks, cut at the
level's edge: bc_copy_extent, compiled from the source and run over regions of every size and place.

Vulkan takes a BC copy's extent only as whole blocks or reaching the level's edge (VUID-vkCmdCopyBufferToImage-dstImage-
00207/00208), and the encoder writes ceil(w / 4) x ceil(h / 4) blocks, tightly, for the copy to read. MK World copies
textures of any size into the corner of its terrain's 2048 x 2048 material arrays (2048 x 1759, 509 x 511, and each of
their levels down to 3 x 3): Longshot #672 refused those regions, and the road was drawn from layers never filled. The
cases below include them.

    python -I -B tests/test_bc_copy_extent.py --cxx clang++
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#include <algorithm>
#include <cstdint>
#include <cstdio>
struct VkExtent2D { uint32_t width, height; };

/* EXTENT */

static int failures;
static void check(bool ok, const char* what, uint32_t w, uint32_t lw, int dx, uint32_t got) {
    if (!ok && failures++ < 20) std::printf("FAIL: %s: region %u at %d of a level %u: extent %u\n", what, w, dx, lw, got);
}

// one axis: the region w at dx of a level lw, with dx on a block (texture_copy_into_bc refuses another)
static void axis(uint32_t w, uint32_t lw, int dx) {
    const VkExtent2D e = bc_copy_extent(w, 1, lw, 1, dx, 0);
    const uint32_t x = e.width;
    check(x >= w, "the region is not all copied", w, lw, dx, x);
    check((uint32_t)dx + x <= lw, "the copy runs past the level", w, lw, dx, x);
    check(x % 4 == 0 || (uint32_t)dx + x == lw, "the extent is neither whole blocks nor reaches the edge (VUID 00207)", w, lw,
          dx, x);
    check((x + 3) / 4 == (w + 3) / 4, "the copy reads another number of blocks than the encoder wrote", w, lw, dx, x);
}

int main() {
    for (uint32_t lw = 1; lw <= 70; lw++)
        for (int dx = 0; (uint32_t)dx < lw; dx += 4)
            for (uint32_t w = 1; (uint32_t)dx + w <= lw; w++) axis(w, lw, dx);
    // MK World's (dst level, region): the material arrays' corner, every level
    const uint32_t mkw[][4] = {{2048, 2048, 2048, 1759}, {2048, 2048, 2047, 1303}, {2048, 2048, 1018, 1023},
                               {1024, 1024, 509, 511}, {512, 512, 254, 255}, {256, 256, 127, 127}, {64, 64, 31, 31},
                               {8, 8, 3, 3}, {8, 8, 4, 3}, {32, 32, 16, 13}};
    for (const auto& c : mkw) {
        const VkExtent2D e = bc_copy_extent(c[2], c[3], c[0], c[1], 0, 0);
        check(e.width == ((c[2] + 3) & ~3u) && e.height == ((c[3] + 3) & ~3u), "MK World's region is not its whole blocks",
              c[2], c[0], 0, e.width);
    }
    // whole blocks, and regions reaching the edge, are copied as they are
    check(bc_copy_extent(64, 64, 2048, 2048, 128, 128).width == 64, "a whole-block region changes", 64, 2048, 128, 64);
    check(bc_copy_extent(6, 6, 6, 6, 0, 0).width == 6, "a whole level of 6 changes", 6, 6, 0, 6);
    check(bc_copy_extent(2, 2, 6, 6, 4, 4).width == 2, "a region reaching the edge changes", 2, 6, 4, 2);
    if (failures) return 1;
    std::printf("ok: a BC copy is the region's whole blocks, cut at the level's edge\n");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    tex = (a.source / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    extent = block(tex, "VkExtent2D bc_copy_extent(")
    if "bc_copy_extent(w, h, " not in block(tex, "void bc_encode_into("):
        raise SystemExit("FAIL: bc_encode_into does not copy with bc_copy_extent")
    with tempfile.TemporaryDirectory(prefix="bc-copy-extent-") as folder:
        cpp, exe = Path(folder) / "fixture.cpp", Path(folder) / "fixture.exe"
        cpp.write_text(FIXTURE.replace("/* EXTENT */", extent), encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        sys.exit(subprocess.run([str(exe)], timeout=60).returncode)


if __name__ == "__main__":
    main()
