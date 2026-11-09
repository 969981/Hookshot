"""Compile and run the BC3 upload parts (vk_texture.cpp's upload_astc_gpu in runs of layers; no game/GPU required)."""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The upload still takes its runs from bc_parts.h, and the next recordings still continue what it left"""
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    for needle in ('#include "bc_parts.h"', "bc_parts::run_end(", "bc_parts::run_fits(", 'getenv("RX_VK_BC_PART_MTEXELS")',
                   "void texture_bc_continue()", "bc_part_texels = 0;"):
        if needle not in tex:
            raise SystemExit("vk_texture.cpp no longer has: " + needle)
    # (a new image's deferred layers are filled from the last one encoded, not left with the image pool's old memory)
    for needle in ("bc_parts::fill_source(", "if (!tex.uploaded && src != ~0u)",
                   "vkCmdCopyImage(cmd, tex.image, VK_IMAGE_LAYOUT_GENERAL, tex.image, VK_IMAGE_LAYOUT_GENERAL"):
        if needle not in tex:
            raise SystemExit("vk_texture.cpp no longer fills a new image's deferred BC3 layers: " + needle)
    # (a continuation that could not record goes back on the list, and is not logged as finished)
    cont = tex[tex.index("void texture_bc_continue()"):tex.index("bool texture_bc_pending()")]
    if "const bool ok = upload_astc_gpu(" not in cont or "bc_parts::retry_continue(" not in cont:
        raise SystemExit("texture_bc_continue drops a continuation that could not record")
    if cont.index("if (!ok)") > cont.index("deferred layers are in"):
        raise SystemExit("texture_bc_continue logs a failed continuation as finished")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    for needle in ("texture_bc_continue();", "texture_bc_pending()"):
        if needle not in replay:
            raise SystemExit("vk_replay.cpp no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="bc-parts-") as tmp:
        exe = Path(tmp) / "bc-parts"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/test_bc_parts.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
