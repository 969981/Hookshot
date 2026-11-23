"""The tree-shadow mask's history images and views are given back when a new size replaces them (RX_VK_MASK_BLEND; no game, no GPU).

gpu/vk/vk_draw.cpp's mask_stabilize made its two history images again at every new mask size over the same static handles, so the old
images (their memory and views) were never destroyed, and kept a view for every texture image it was blended into in a map nothing
emptied. The pass is off by default (a GPU hang near Hyrule Castle was seen with it on). This cuts the retiring helpers out of the
source and runs them over 40 size changes, then reads mask_stabilize for the wiring: the old pair retired before the new one is made, and
every view retired when a texture went.

    python -I -B tests/test_mask_history_retire.py [--cxx g++]
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import between, block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "g++"]

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++", help="C++ compiler")
    a = p.parse_args()
    cxx = shutil.which(a.cxx)
    if not cxx:
        raise SystemExit("test_mask_history_retire: no C++ compiler %r (pass --cxx)" % a.cxx)
    src = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    helpers = between(src, "struct MaskOld {", "void mask_stabilize(VkCommandBuffer cmd, Texture* tex, uint32_t level, uint32_t layer, float weight) {")
    body = block(src, "void mask_stabilize(VkCommandBuffer cmd, Texture* tex, uint32_t level, uint32_t layer, float weight) {")
    fails = []

    def need(cond, what):
        if not cond:
            fails.append(what)

    size_change = body[body.index("if (hist_w != w || hist_h != h) {"):]
    need(size_change.index("mask_retire(hist[i], hist_mem[i], hist_view[i], texture_frame_no());") < size_change.index("create_image(ic, hist[i], hist_mem[i])"),
         "the old pair is retired before a new one is made over its handles")
    need("mask_reap(texture_frame_no());" in body, "the retired ones are given back from mask_stabilize")
    need("if (views_gen != texture_destroy_generation()) {" in body and "mask_retire(none, no_mem, kv.second, texture_frame_no());" in body
         and "views.clear();" in body, "the views are retired and the map emptied when a texture went")
    for f in fails:
        print("FAIL: " + f)
    if fails:
        raise SystemExit("test_mask_history_retire: failed")
    with tempfile.TemporaryDirectory(prefix="mask-retire-") as temp:
        d = Path(temp)
        (d / "cut.inc").write_text("struct MaskOld_forward;\n" + helpers, encoding="utf-8")
        exe = d / "mask"
        subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", '-DCUT="%s"' % (d / "cut.inc"),
                        str(ROOT / "tests" / "test_mask_history_retire.cpp"), "-o", str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        print(result.stdout, end="")
        if result.returncode:
            raise SystemExit("test_mask_history_retire: failed")


if __name__ == "__main__":
    main()
