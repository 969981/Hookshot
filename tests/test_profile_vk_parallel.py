"""The renderer's parallel replay default comes from the game profile, at run time (nx2/game_profile.py vk_parallel).

The parallel replay (gpu/vk/vk_replay.cpp par_threads, RX_VK_PARALLEL) helps a game that records most of a frame in one
command buffer and costs one that does not: Mario Kart World 1.0.0's race gains (54-55 -> 58-59 fps median on 4 threads),
Ocarina loses a fifth (docs/performance.md). So the default is the game's. The renderer is built once for every game, so
the runtime hands it over, as it does the program keys: HOOKSHOT_PROFILE_VK_PARALLEL, "-" for none.

Checked here, game-free:
  - the profiles in main leave it unset (Ocarina's and Donkey Kong Bananza's: nothing changes for them), and the header
    writes it, -1 for unset;
  - host/hos_main.c's game_profile_env, cut out and compiled: the profile's count when the profile is live, "-" when it
    names none or the image is not its build;
  - par_threads, cut out and compiled, run once a case (it reads its switches once): none -> 0; the profile's 4 -> 4;
    "-" -> 0; RX_VK_PARALLEL=0 over the profile's 4 -> 0 (the player's switch wins); RX_VK_PARALLEL=3 -> 3; 12 -> 8.

python -I -B tests/test_profile_vk_parallel.py [--cc gcc] [--cxx g++]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py)
import game_profile  # noqa: E402
from srccut import block  # noqa: E402

RUN_ALL_ARGS = ["--cc", "gcc", "--cxx", "g++"]


def fail(msg):
    raise SystemExit("FAIL: " + msg)


def check_profiles():
    for key, p in game_profile.GAMES.items():
        if key in ("oot", "dkb", "none") and p.vk_parallel is not None:
            fail("the %s profile sets vk_parallel: the renderer's default must stay its own there" % key)
    head = game_profile.emit_header(game_profile.GAMES[game_profile.DEFAULT])
    if "#define GAME_VK_PARALLEL -1" not in head:
        fail("an unset vk_parallel is not written as -1 in the header")
    four = game_profile.Profile(key="zz", game="Another Game", vk_parallel=4)
    if "#define GAME_VK_PARALLEL 4" not in game_profile.emit_header(four):
        fail("a profile's vk_parallel is not written into its header")


ENV_C = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef GAME_VK_PARALLEL
#error "the case sets GAME_VK_PARALLEL"
#endif
#define GAME_UPSCALE_TAAU_KEYS ""
#define GAME_NETWORK_KEEP_KEYS ""
#define GAME_GAMEPLAY_NETWORK_KEYS {0}
#define GAME_GAMEPLAY_NETWORK_KEY_COUNT 0
static int hos_game_profile_live(void) { return LIVE; }
CUT
int main(void) {
    game_profile_env();
    const char *v = getenv("HOOKSHOT_PROFILE_VK_PARALLEL");
    printf("%s\n", v ? v : "(unset)");
    return 0;
}
'''

PAR_CPP = r'''
#include <algorithm>
#include <cstdio>
#include <cstdlib>
CUT
int main() { std::printf("%d\n", par_threads()); return 0; }
'''


def run(exe, env):
    e = {k: v for k, v in os.environ.items() if k not in ("RX_VK_PARALLEL", "HOOKSHOT_PROFILE_VK_PARALLEL")}
    e.update(env)
    return subprocess.run([str(exe)], capture_output=True, text=True, env=e, timeout=30, check=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", default="gcc")
    ap.add_argument("--cxx", default="g++")
    a = ap.parse_args()
    check_profiles()
    hos = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    env_fn = block(hos, "static void game_profile_env(void) {", "host/hos_main.c")
    rep = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    par_fn = block(rep, "int par_threads() {", "gpu/vk/vk_replay.cpp")
    with tempfile.TemporaryDirectory(prefix="profile-vk-parallel-") as tmp:
        tmp = Path(tmp)
        src = tmp / "env.c"
        src.write_text(ENV_C.replace("CUT", env_fn), encoding="utf-8")
        for live, par, want in ((1, 4, "4"), (1, -1, "-"), (0, 4, "-")):
            exe = tmp / ("env_%d_%d" % (live, par + 1))
            subprocess.run([a.cc, "-std=c11", "-Wall", "-Werror", "-DLIVE=%d" % live, "-DGAME_VK_PARALLEL=%d" % par,
                            str(src), "-o", str(exe)], check=True)
            got = run(exe, {})
            if got != want:
                fail("game_profile_env with the profile %s and GAME_VK_PARALLEL %d hands over %r, not %r"
                     % ("live" if live else "not live", par, got, want))
        src = tmp / "par.cpp"
        src.write_text(PAR_CPP.replace("CUT", par_fn), encoding="utf-8")
        exe = tmp / "par"
        subprocess.run([a.cxx, "-std=c++17", "-Wall", "-Werror", str(src), "-o", str(exe)], check=True)
        for env, want in (({}, "0"), ({"HOOKSHOT_PROFILE_VK_PARALLEL": "4"}, "4"),
                          ({"HOOKSHOT_PROFILE_VK_PARALLEL": "-"}, "0"),
                          ({"HOOKSHOT_PROFILE_VK_PARALLEL": "4", "RX_VK_PARALLEL": "0"}, "0"),
                          ({"RX_VK_PARALLEL": "3"}, "3"), ({"HOOKSHOT_PROFILE_VK_PARALLEL": "12"}, "8")):
            got = run(exe, env)
            if got != want:
                fail("par_threads with %r gives %s, not %s" % (env, got, want))
    print("PASS: the parallel replay's default is the game profile's, handed over at run time; RX_VK_PARALLEL wins")


if __name__ == "__main__":
    main()
