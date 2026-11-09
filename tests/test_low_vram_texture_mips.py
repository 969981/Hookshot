"""Minimum quality on a small GPU trims large sampled ASTC textures, not render targets.

Exercises the production mip-selection block with real NVN/Vulkan types and no GPU.
Each policy runs in its own process, like a new game launch.
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    p.add_argument("--headers", default=os.environ.get("HOOKSHOT_VULKAN_HEADERS"))
    p.add_argument("--sanitize", default="")
    a = p.parse_args()
    source = (ROOT / "gpu/vk/vk_texture.cpp").read_text()
    mip = block(source, "{   // huge textures leave out their top levels:")
    policy = block(source, "bool low_vram_texture_mips(") if "bool low_vram_texture_mips(" in source else ""
    fixture = r'''
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vulkan/vulkan.h>
#include "gpu/render/rx_nvn.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL:%d %s\n",__LINE__,#x); std::exit(1); } } while(0)
struct Texture {
    uint32_t width, height, depth = 1, levels, layers = 1, skip = 0;
    bool sparse = false;
    VkFormat format = VK_FORMAT_R8G8B8A8_UNORM;
    VkImageType type = VK_IMAGE_TYPE_2D;
};
struct { bool sparse_images = false; VkPhysicalDevice physical{}; } dev;
struct { int preset = 1; } opts;
uint64_t allowed = 3674ull << 20;
bool vram_usage(uint64_t* used, uint64_t* budget, uint64_t* = nullptr) {
    *used = 2000ull << 20; *budget = allowed; return allowed != 0;
}
void logf(const char*, ...) {}
bool is_bc(VkFormat f) { return f >= VK_FORMAT_BC1_RGB_UNORM_BLOCK && f <= VK_FORMAT_BC7_SRGB_BLOCK; }
extern "C" VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceSparseImageFormatProperties(
        VkPhysicalDevice, VkFormat, VkImageType, VkSampleCountFlagBits, VkImageUsageFlags, VkImageTiling,
        uint32_t* n, VkSparseImageFormatProperties*) { *n = 0; }
@POLICY@
void trim(Texture& x, const nvn_tex* t, bool astc = true, bool depth = false,
          int bpb = 4, int bw = 1, int bh = 1) {
    const nvn_texdesc& d = t->d;
    @MIP@
}
int main(int argc, char** argv) {
    const int mode = argc > 1 ? std::atoi(argv[1]) : 0;
    if (mode == 1) allowed = 7500ull << 20;
    if (mode == 2) opts.preset = 2;
    if (mode == 3) allowed = 0;
    nvn_tex t{}; t.d.samples = 1;
    Texture single{3072,3072,1,12}; trim(single,&t);
    if (mode == 7) {
        CHECK(single.skip == 3 && single.width == 384 && single.height == 384 && single.levels == 9);
        Texture array{1365,1365,1,11,90}; trim(array,&t);
        CHECK(array.skip == 2 && array.width == 341 && array.height == 341 && array.layers == 90 && array.levels == 9);
    } else if (mode == 0 || mode == 6 || mode >= 8) {
        CHECK(single.skip == 2 && single.width == 768 && single.height == 768 && single.levels == 10);
        Texture array{1365,1365,1,11,90}; trim(array,&t);
        if (mode == 0) {
            CHECK(array.skip == 1 && array.width == 682 && array.height == 682 && array.layers == 90 && array.levels == 10);
        } else {
            CHECK(array.skip == 2 && array.width == 341 && array.height == 341 && array.layers == 90 && array.levels == 9);
        }
    } else if (mode == 5) {
        CHECK(single.skip == 1 && single.width == 1536 && single.levels == 11);
    } else {
        CHECK(single.skip == 0 && single.width == 3072 && single.levels == 12);
    }
    Texture ui{512,512,1,10}; trim(ui,&t);
    CHECK(ui.skip == 0 && ui.width == 512 && ui.levels == 10);
    Texture last_mip{3072,3072,1,1}; trim(last_mip,&t);
    CHECK(last_mip.skip == 0 && last_mip.levels == 1);
    Texture target{4096,4096,1,13}; trim(target,&t,false);
    CHECK(target.skip == 0 && target.width == 4096 && target.levels == 13);
    Texture depth{12288,12288,1,1}; trim(depth,&t,false,true,2);
    CHECK(depth.skip == 0 && depth.width == 12288 && depth.levels == 1);
    std::printf("PASS mip policy %d: sampled-texture budget, UI, final mip and render targets\n",mode);
}
'''.replace("@POLICY@", policy).replace("@MIP@", mip)
    with tempfile.TemporaryDirectory(prefix="low-vram-mips-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture)
        cmd = [a.cxx, "-std=c++20", "-O2", "-I", str(ROOT)]
        if a.headers:
            cmd += ["-I", a.headers]
        if a.sanitize:
            cmd += ["-fsanitize=" + a.sanitize, "-fno-omit-frame-pointer"]
        subprocess.run(cmd + [str(cpp), "-o", str(exe)], check=True)
        for mode in range(12):
            env = os.environ.copy()
            for key in ["RX_VK_LOW_VRAM_MIPS", "RX_VK_TEX_MAX_DIM", "RX_VK_TEX_MAX_MB", "RX_VK_TEX_TRIM_MB"]:
                env.pop(key, None)
            if mode == 4:
                env["RX_VK_LOW_VRAM_MIPS"] = "0"
            if mode == 5:
                env["RX_VK_TEX_MAX_DIM"] = "2048"
            if mode >= 6:
                env["RX_VK_TEX_MAX_DIM"] = "512"
            if mode >= 7:
                env["RX_VK_TEX_TRIM_MB"] = ["2", "-1", "0", "2invalid", "999999999999999999999"][mode - 7]
            subprocess.run([str(exe), str(mode)], env=env, check=True)


if __name__ == "__main__":
    main()
