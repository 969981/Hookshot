"""Compile and run decoded ASTC's reuse rules (gpu/vk/astc_reuse.h: the blocks' hash, the shape, the list; no game/GPU), and
check that vk_texture.cpp still lists finalized textures, copies from them and retires them."""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "g++"]

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    for needle in ('#include "astc_reuse.h"',
                   'getenv("RX_VK_ASTC_REUSE")',
                   "if (!dirty && !tex.uploaded && reuse_on() && reuse_upload(tex, total)) return up_route = UP_ASTC, true;",
                   "freed.list(x->blocks_hash, x);",
                   "if (tex->freed_listed || tex->reuse_retire) reuse_drop(*tex);",
                   "if (tex->freed_listed || tex->reuse_retire) return false;",
                   "vkCmdCopyImage(cmd, from->image, VK_IMAGE_LAYOUT_GENERAL, tex.image, VK_IMAGE_LAYOUT_GENERAL",
                   "from->last_value = std::max(from->last_value, rec_value);",
                   "went.swap(reuse_gone);"):
        if needle not in src:
            raise SystemExit("vk_texture.cpp no longer has: " + needle)
    # (hookshot: only 2D, non-sparse textures with an image are hashed, before anything else; a source made BC1 is found by
    # the BC3 it was made, and the new one is made BC1 before the copy)
    up = src[src.index("bool reuse_upload(Texture& tex, uint64_t total) {"):]
    up = up[:up.index("\n}\n")]
    if up.index("if (tex.sparse || tex.type != VK_IMAGE_TYPE_2D || !tex.image) return false;") > up.index("astc_reuse::Hasher h;"):
        raise SystemExit("reuse_upload hashes a texture it never serves (sparse, not 2D, or without an image)")
    if "bc_reformat(tex, VK_FORMAT_BC1_RGBA_UNORM_BLOCK)" not in up:
        raise SystemExit("reuse_upload copies a BC1 source into a BC3 image")
    shape = src[src.index("astc_reuse::Shape reuse_shape(const Texture& t) {"):]
    if "t.bc1 == 2 && t.format == VK_FORMAT_BC1_RGBA_UNORM_BLOCK ? VK_FORMAT_BC3_UNORM_BLOCK" not in shape[:shape.index("\n}\n")]:
        raise SystemExit("reuse_shape compares a texture made BC1 on its first upload by its BC1: no new one ever matches it")
    # (hookshot: off by default, RX_VK_ASTC_REUSE=1 on: its hashing made one frame of MKW's boot 627-711 ms)
    on = src[src.index("bool reuse_on() {"):]
    if 'getenv("RX_VK_ASTC_REUSE") && atoi(getenv("RX_VK_ASTC_REUSE")) != 0' not in on[:on.index("\n}\n")]:
        raise SystemExit("the ASTC reuse is on by default again: its hashing costs MKW's boot a 627-711 ms frame")
    forget = src[src.index("void forget_gpu(Texture* tex) {"):]
    if "reuse_drop(*tex);" not in forget[:200]:
        raise SystemExit("forget_gpu no longer takes a destroyed texture out of the reuse list")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="astc-reuse-") as tmp:
        exe = Path(tmp) / "astc-reuse"
        subprocess.run([args.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_astc_reuse.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
