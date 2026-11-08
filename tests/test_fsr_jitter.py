"""AMD FSR 4.1's jitter (gpu/vk/vk_draw.cpp): Halton (2, 3) offsets in [-0.5, 0.5), part of the state a draw is compared by, moved
on only at the network's pass, and the AMD edge repeat in the colour and motion passes.

python -I -B tests/test_fsr_jitter.py [--cxx g++]
"""
import argparse
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
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    source = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    pack = block(source, "int dyn_pack(")
    # (a frame whose draws match the last frame's would keep its jitter if the offset were not compared)
    if "fsr_jitter_for(at, fp[13], fp[14])" not in pack:
        raise SystemExit("dyn_pack leaves FSR's jitter out of the state a draw is compared by")
    if "constexpr int kDynF = 15" not in source:
        raise SystemExit("the compared floats do not hold FSR's jitter (fp[13], fp[14])")
    dyn = block(source, "void dynamic_state(")
    if "d.vp.x += df[13], d.vp.y += df[14]" not in dyn:
        raise SystemExit("the viewport is not moved by the jitter dyn_pack compared")
    dispatch = block(source, "bool dlss_dispatch(VkCommandBuffer cmd, const NvnState& s, const Program& p) {")
    if "fsr_frame_end(drew, rw, ow)" not in dispatch or "fj.used ? fj.x : 0.0f" not in dispatch:
        raise SystemExit("the jitter does not move on at the network's pass, or FSR is told an offset no draw had")
    jitter_for = block(source, "bool fsr_jitter_for(const Targets& at, float& jx, float& jy) {")
    for need in ("fj.live", "par_active", "ds.cam.w", "fj.net_present"):
        if need not in jitter_for:
            raise SystemExit(f"fsr_jitter_for moves draws without checking {need}")
    shaders = ROOT / "gpu" / "vk" / "shaders"
    if "imageSize(dlss_color) - 2" not in (shaders / "dlss_color.comp").read_text(encoding="utf-8"):
        raise SystemExit("FSR's colour does not repeat the column and row before the last (AMD's edge line)")
    if "extent - 2" not in (shaders / "dlss_motion.comp").read_text(encoding="utf-8"):
        raise SystemExit("the motion's depth does not repeat the column and row before the last (AMD's edge line)")
    fn = block(source, "float halton(")
    fixture = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
''' + fn + r'''
int main() {
    CHECK(halton(1, 2) == 0.5f && halton(2, 2) == 0.25f && halton(3, 2) == 0.75f);
    CHECK(std::fabs(halton(1, 3) - 1.0f / 3) < 1e-6f && std::fabs(halton(2, 3) - 2.0f / 3) < 1e-6f);
    float sx = 0, sy = 0;
    for (uint32_t i = 1; i <= 16; i++) {   // (the 16 phases of a 75% render: each offset in [-0.5, 0.5), centred overall)
        const float x = halton(i, 2) - 0.5f, y = halton(i, 3) - 0.5f;
        CHECK(x >= -0.5f && x < 0.5f && y >= -0.5f && y < 0.5f);
        sx += x, sy += y;
    }
    CHECK(std::fabs(sx / 16) < 0.05f && std::fabs(sy / 16) < 0.05f);
    std::printf("PASS: FSR's jitter is compared, moved on at the network's pass, and balanced\n");
}
'''
    with tempfile.TemporaryDirectory(prefix="fsr-jitter-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
