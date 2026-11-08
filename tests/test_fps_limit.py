"""The Insert menu's Frame rate limit paces the game's frames to at most 30, 40, 45 or 50 fps, and only when asked for.

host/nn_vi.c's fps_limit() and frame_period() are compiled out of the file with stand-ins for getenv and rx_option and
checked: no limit is the display's interval as before; a limit lengthens the period, never shortens it; HOS_FPS_LIMIT
wins over the menu; a value outside 15-59 is off. The menu side is read from the sources: the row, its labels, the
index-to-fps table and the option the host reads (18).

python -I -B tests/test_fps_limit.py [--cc gcc]
"""
import argparse
import re
import shutil
import subprocess
import sys
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


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *env_value;
static int option_value;
static char *test_getenv(const char *name) { return !strcmp(name, "HOS_FPS_LIMIT") ? (char *)env_value : NULL; }
#define getenv test_getenv
int rx_option(int id, int otherwise) { return id == 18 ? option_value : otherwise; }
static volatile int present_interval = 1;
%s
%s
#undef getenv
int main(int argc, char **argv) {
    (void)argc;
    env_value = strcmp(argv[1], "-") ? argv[1] : NULL;
    option_value = atoi(argv[2]);
    present_interval = atoi(argv[3]);
    printf("%%d %%lld\n", fps_limit(), (long long)frame_period());
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()

    vi = (ROOT / "host" / "nn_vi.c").read_text(encoding="utf-8")
    limit_fn = block(vi, "static int fps_limit(void)")
    period_fn = block(vi, "static int64_t frame_period(void)")
    acquire = block(vi, "void hos_vi_native_acquire(int active)")
    presented = block(vi, "void hos_vi_native_presented(void)")
    check("!fps_limit()" in acquire, "the acquire still queues limited frames behind the display queue")
    check("period = frame_period()" in presented, "the presents are not paced by frame_period()")

    menu = (ROOT / "gpu" / "vk" / "vk_menu.cpp").read_text(encoding="utf-8")
    header = (ROOT / "gpu" / "vk" / "vk_menu.h").read_text(encoding="utf-8")
    replay = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    check(re.search(r'\{TAB_GRAPHICS, "Frame rate limit", &opts\.fps_limit, 0, 4, 1, tx_fps_limit,', menu) is not None,
          "no Frame rate limit row with five choices")
    check('{"Off", "30 fps", "40 fps", "45 fps", "50 fps"}' in block(menu, "const char* tx_fps_limit(int v)"),
          "the row's labels changed")
    check("{0, 30, 40, 45, 50}" in block(menu, "int menu_fps_limit()"), "the row's fps table changed")
    check("int fps_limit = 0;" in header, "the limit is not off by default")
    check("id == 18 ? menu_fps_limit()" in replay, "the host cannot read the limit (option 18)")

    cc = shutil.which(a.cc)
    if not cc:
        print("SKIP: no %s" % a.cc)
        return
    with tempfile.TemporaryDirectory(prefix="fps-limit-") as tmp:
        src, exe = Path(tmp) / "t.c", Path(tmp) / "t.exe"
        src.write_text(HARNESS % (limit_fn, period_fn), encoding="utf-8")
        r = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(src)],
                           capture_output=True, text=True)
        check(r.returncode == 0, "the fixture did not compile:\n" + r.stdout + r.stderr)

        def run(env, option, interval):
            out = subprocess.run([str(exe), env, str(option), str(interval)], capture_output=True, text=True, check=True)
            fps, period = out.stdout.split()
            return int(fps), int(period)

        frame60 = 1000000000 // 60
        check(run("-", 0, 1) == (0, frame60), "no limit is not the display's interval")
        check(run("-", 0, 2) == (0, frame60 * 2), "the game's own interval is no longer kept")
        check(run("-", 30, 1) == (30, 1000000000 // 30), "the menu's 30 fps is not a 33 ms period")
        check(run("-", 45, 1) == (45, 1000000000 // 45), "the menu's 45 fps is not a 22 ms period")
        check(run("40", 30, 1) == (40, 1000000000 // 40), "HOS_FPS_LIMIT does not win over the menu")
        check(run("-", 40, 2) == (40, frame60 * 2), "a limit shortened a longer interval the game asked for")
        check(run("60", 0, 1) == (0, frame60), "60 is not off")
        check(run("10", 0, 1) == (0, frame60), "a limit under 15 is not off")
    print("ok: the frame rate limit")


if __name__ == "__main__":
    main()
