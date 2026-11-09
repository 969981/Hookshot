"""DLSS stays out of the resolution path until it is on, and the quality row's twelfths match the NGX modes.

python -I -B tests/test_dlss_scale.py [--cxx g++]
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
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    menu = (ROOT / "gpu" / "vk" / "vk_menu.cpp").read_text(encoding="utf-8")
    dlss = (ROOT / "gpu" / "vk" / "vk_dlss.cpp").read_text(encoding="utf-8")
    prep = (ROOT / "gpu" / "vk" / "shaders" / "dlss_prep.comp").read_text(encoding="utf-8")
    for gone in ("RX_DLSS_TAP", "dlss_probe", "dlss_tap(", "dlss_scene("):
        if gone in dlss or gone in menu:
            raise SystemExit("the experimental scene tap is still in the renderer (%s)" % gone)
    if "probe" in prep:
        raise SystemExit("dlss_prep.comp still has the tap's probe")
    ngx = ROOT / "gpu" / "vk" / "ngx"
    for banned in ngx.rglob("*"):
        if banned.suffix.lower() in {".dll", ".lib", ".h"} and banned.name.lower().startswith("nvsdk"):
            raise SystemExit("NVIDIA SDK material is in the tree: %s" % banned)
        if banned.name.lower() == "nvngx_dlss.dll":
            raise SystemExit("nvngx_dlss.dll must not be committed")
    choice = block(menu, "int dlss_choice()")
    twelfths = block(menu, "int dlss_quality_twelfths()")
    auto = block(menu, "bool resolution_auto()")
    scale = block(menu, "int resolution_scale()")
    quality = block(dlss, "int quality_of(uint32_t rw, uint32_t ow)")
    if "dlss_choice() > 0" not in twelfths:
        raise SystemExit("DLSS quality changes the 3D size even when DLSS is off")
    fixture = r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
struct Options { int dlss = 0, dlss_quality = 0, resolution = 0; } opts;
constexpr int kResPercent[] = {0, 100, 75, 67, 50};
int twelfths(float scale) { return std::min(std::max((int)(scale * 12.0f + 0.5f), 1), 12); }
''' + choice + twelfths + auto + scale + quality + r'''
int main() {
    if (std::getenv("RX_DLSS_OFF") && std::atoi(std::getenv("RX_DLSS_OFF"))) {
        opts.dlss = 2; opts.dlss_quality = 5; opts.resolution = 0;
        CHECK(dlss_choice() == 0);
        CHECK(dlss_quality_twelfths() == 0);
        CHECK(resolution_auto());
        CHECK(resolution_scale() == 0);
        std::printf("PASS: RX_DLSS_OFF leaves the resolution path alone\n");
        return 0;
    }
    CHECK(resolution_auto());
    CHECK(resolution_scale() == 0);
    opts.resolution = 1; CHECK(!resolution_auto()); CHECK(resolution_scale() == 10000);
    opts.resolution = 2; CHECK(resolution_scale() == 7500);
    opts.resolution = 3; CHECK(resolution_scale() == 6667);
    opts.resolution = 4; CHECK(resolution_scale() == 5000);
    opts.resolution = 0; opts.dlss = 2;
    CHECK(resolution_auto());
    CHECK(dlss_quality_twelfths() == 0);
    static const int tw[] = {0, 8, 7, 6, 4, 12};
    static const int ngx[] = {-1, 2, 1, 0, 3, 5};
    for (int q = 1; q <= 5; q++) {
        opts.dlss_quality = q;
        CHECK(dlss_quality_twelfths() == tw[q]);
        CHECK(!resolution_auto());
        CHECK(resolution_scale() == (int)(tw[q] * 10000.0f / 12.0f + 0.5f));
        CHECK(quality_of(1, 1) == ngx[q]);
    }
    opts.dlss = 0; opts.dlss_quality = 5; opts.resolution = 0;
    CHECK(dlss_quality_twelfths() == 0);
    CHECK(resolution_auto());
    CHECK(resolution_scale() == 0);
    opts.dlss_quality = 0; opts.dlss = 2;
    CHECK(quality_of(2560, 2560) == 5);
    CHECK(quality_of(1707, 2560) == 2);
    CHECK(quality_of(1494, 2560) == 1);
    CHECK(quality_of(1280, 2560) == 0);
    CHECK(quality_of(854, 2560) == 3);
    std::printf("PASS: DLSS scale and NGX modes\n");
}
'''
    with tempfile.TemporaryDirectory(prefix="dlss-scale-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        env = dict(os.environ)
        env["RX_DLSS_OFF"] = "1"
        subprocess.run([str(exe)], check=True, env=env)


if __name__ == "__main__":
    main()
