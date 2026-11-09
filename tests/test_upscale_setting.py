"""The Upscaling row's two halves, and the clamp that hides DLSS from a build that cannot do it.

The row carries five values: 0-2 are filters over the finished picture and 3-4 name a backend that
resolves the 3D scene instead. Two inline accessors in gpu/vk/vk_menu.h say which half is meant, and
everything that used to read `opts.upscale` raw now goes through them -- because the filter's shader
takes a mode of 0..2 and handing it a 3 would be a setting that corrupts the picture rather than one
that does nothing.

This compiles the real accessors (extracted, not retyped, so a change to them changes the test) and
checks the mapping both ways. It also checks the source facts the accessors cannot: that the row
offers all five values, that menu_init clamps it where the build has no NGX SDK, and that no
consumer reads opts.upscale raw again.

python -I -B tests/test_upscale_setting.py [--cxx g++]
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    """The function beginning at marker, braces balanced."""
    if marker not in text:
        raise SystemExit(f"{marker!r} is gone: this test no longer checks what it says it does")
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


PROGRAM = r'''
#include <cstdio>
static int failures;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL: %%s\n", #c); ++failures; } } while (0)
struct Options { int upscale = 0; };
static Options opts;
%s

%s

int main() {
    // the three filter values pass through untouched
    for (int v = 0; v <= 2; v++) { opts.upscale = v; CHECK(window_filter() == v); CHECK(scene_upscaler() == 0); }
    // the two scene upscalers are not filter modes, and must not reach the filter's shader as one
    opts.upscale = 3; CHECK(window_filter() == 0); CHECK(scene_upscaler() == 1);
    opts.upscale = 4; CHECK(window_filter() == 0); CHECK(scene_upscaler() == 2);
    // a value from an older or newer ini than this build is not a filter mode either
    opts.upscale = 99; CHECK(window_filter() == 0);
    opts.upscale = -1; CHECK(window_filter() == 0); CHECK(scene_upscaler() == 0);
    if (failures) return 1;
    std::printf("PASS: the Upscaling row's filter half and scene half stay apart\n");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()

    header = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")

    source = PROGRAM % (block(header, "inline int window_filter() {"),
                        block(header, "inline int scene_upscaler() {"))
    with tempfile.TemporaryDirectory(prefix="upscale-setting-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        cpp.write_text(source, encoding="utf-8")
        exe = Path(tmp) / "fixture"
        subprocess.run([a.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)

    # the row itself: all five values on offer, and the clamp for a build that cannot do DLSS
    if '"Upscaling", &opts.upscale, 0, 4, 1, tx_upscale' not in menu:
        raise SystemExit("the Upscaling row no longer offers 0..4")
    if "dlss::built_with_sdk()" not in menu or "r.hi = 3" not in menu:
        raise SystemExit("menu_init no longer clamps the row where the build has no NGX SDK")

    # nothing reads the raw value any more, or the filter gets a mode it does not have
    raw = []
    for name in ("gpu/vk/vk_framegen.cpp", "gpu/vk/vk_replay.cpp", "gpu/vk/vk_draw.cpp", "gpu/vk/vk_dlss.cpp"):
        for n, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
            if "opts.upscale" in line:
                raw.append(f"{name}:{n}: {line.strip()}")
    if raw:
        raise SystemExit("these read opts.upscale raw instead of window_filter()/scene_upscaler():\n"
                         + "\n".join(raw))
    print("PASS: the row offers five values, clamps without an SDK, and no consumer reads it raw")


if __name__ == "__main__":
    main()
