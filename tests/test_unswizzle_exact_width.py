"""The Vulkan renderer's swizzles take a texture's exact width (gpu/vk/vk_texture.cpp, the 2026-11-14 audit's V3).

suyu's UnswizzleTexture and SwizzleTexture take a stride alignment as a log2: 1 (their default) rounds the width up to
even before counting a row's 64-byte GOBs. NVN lays a texture out by its exact width (host/nvn_layout.c), and the two
counts differ only for 3, 6 or 12 bytes a texel (RGB8, RGB16, RGB32F) at some odd widths: for those, each row of blocks
was read one GOB too wide, from the wrong place and past the texture's data. Checks:

- every UnswizzleTexture / SwizzleTexture call in vk_texture.cpp passes 0 (the exact width);
- suyu's decoder (compiled): a 21x16 RGB8 texture of one-GOB blocks, NVN's 1,024 bytes, unswizzled at the exact width
  gives every texel from the address NVN's GOB layout names, and swizzled back gives the same bytes.

python -I -B tests/test_unswizzle_exact_width.py [--cxx g++]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <cstdint>
#include <cstdio>
#include <span>
#include <vector>
#include "video_core/textures/decoders.h"
/* a byte's address in a block-linear texture of one-GOB-high blocks: GOBs of 64 bytes x 8 rows, a row of them in order;
   in a GOB, from the lowest bit: x bits 0-3, y bit 0, y bit 1, x bit 4, y bit 2, x bit 5 */
static uint32_t gob_address(uint32_t xb, uint32_t y, uint32_t gobs_in_x) {
    const uint32_t gob = (y / 8) * gobs_in_x + xb / 64, x = xb % 64, r = y % 8;
    return gob * 512 + (x % 16) + (r % 2) * 16 + ((r / 2) % 2) * 32 + ((x / 16) % 2) * 64 + (r / 4) * 128 + (x / 32) * 256;
}
int main() {
    const uint32_t w = 21, h = 16, bpp = 3, gobs_in_x = (w * bpp + 63) / 64;   /* 63 bytes a row: one GOB */
    std::vector<uint8_t> in(gobs_in_x * 512 * (h / 8)), out(w * h * bpp), back(in.size(), 0);
    for (size_t i = 0; i < in.size(); i++) in[i] = (uint8_t)(i * 7 + 3);
    Tegra::Texture::UnswizzleTexture(std::span<uint8_t>(out), std::span<const uint8_t>(in), bpp, w, h, 1, 0, 0, 0);
    int wrong = 0;
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t xb = 0; xb < w * bpp; xb++)
            wrong += out[y * w * bpp + xb] != in[gob_address(xb, y, gobs_in_x)];
    Tegra::Texture::SwizzleTexture(std::span<uint8_t>(back), std::span<const uint8_t>(out), bpp, w, h, 1, 0, 0, 0);
    int back_wrong = 0;
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t xb = 0; xb < w * bpp; xb++)
            back_wrong += back[gob_address(xb, y, gobs_in_x)] != in[gob_address(xb, y, gobs_in_x)];
    printf("%d of %u texel bytes from another place, %d wrong swizzled back\n", wrong, w * h * bpp, back_wrong);
    return wrong || back_wrong;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    bad = []
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    calls = re.findall(r"Tegra::Texture::(?:Unswizzle|Swizzle)Texture\(([^;]*?)\);", src, re.S)
    if len(calls) < 4:
        bad.append("vk_texture.cpp has %d swizzle calls (4 known): update this check with the source" % len(calls))
    for c in calls:
        last = c.rsplit(",", 1)[-1].strip()
        if last != "0":
            bad.append("a swizzle call rounds the width up to even (stride alignment %s): %s" % (last, " ".join(c.split())[:120]))
    suyu = ROOT / "gpu" / "third_party" / "suyu"
    with tempfile.TemporaryDirectory(prefix="unswizzle-width-") as tmp:
        folder = Path(tmp)
        (folder / "t.cpp").write_text(HARNESS, encoding="utf-8")
        exe = folder / "t"
        subprocess.run([args.cxx, "-std=c++23", "-O1", "-w", "-I" + str(suyu), "-I" + str(ROOT / "gpu" / "vk" / "compat"),
                        str(folder / "t.cpp"), str(suyu / "video_core" / "textures" / "decoders.cpp"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        print(out.stdout.strip())
        if out.returncode:
            bad.append("suyu's unswizzle at the exact width does not follow NVN's GOB layout")
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: the swizzles take the texture's exact width, as NVN lays it out")


if __name__ == "__main__":
    main()
