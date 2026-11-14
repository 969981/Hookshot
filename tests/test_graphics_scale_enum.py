"""nn::oe::GetCurrentGraphicsScale's enum comes from the game profile when it names one (host/nn_applet.c).

Donkey Kong Bananza sizes its render targets by the enum (0 the full scale, 1 x0.8, 2 x0.667): with the host's 1 it drew at
80% of its 2880x1620. graphics_scale_enum is cut from the source and compiled with each GAME_GRAPHICS_SCALE. Checks:

- no profile answer (-1): 1, or 0 when an output size is chosen (Ocarina's, as before);
- a profile answer (Bananza's 0): that one, output size or not;
- HOS_GRAPHICS_SCALE_ENUM=0|1|2 overrides both; anything else there is ignored.

python -I -B tests/test_graphics_scale_enum.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#define GAME_GRAPHICS_SCALE @SCALE@
@FN@
int main(int argc, char **argv) {
    (void)argc;
    printf("%d\n", graphics_scale_enum(atoi(argv[1])));
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++"))
    args = parser.parse_args()
    fn = block((ROOT / "host/nn_applet.c").read_text(encoding="utf-8"), "static int graphics_scale_enum(int output_chosen) {")
    bad = []
    with tempfile.TemporaryDirectory(prefix="graphics-scale-") as tmp:
        exes = {}
        for scale in (-1, 0):
            src = Path(tmp) / ("s%d.cpp" % (scale + 1))
            src.write_text(HARNESS.replace("@SCALE@", str(scale)).replace("@FN@", fn), encoding="utf-8")
            exes[scale] = Path(tmp) / ("s%d" % (scale + 1))
            subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", str(src), "-o", str(exes[scale])], check=True)

        def answer(scale, output_chosen, env=None):
            e = {k: v for k, v in os.environ.items() if k != "HOS_GRAPHICS_SCALE_ENUM"}
            if env is not None:
                e["HOS_GRAPHICS_SCALE_ENUM"] = env
            return int(subprocess.run([str(exes[scale]), str(output_chosen)], capture_output=True, text=True, env=e).stdout)

        cases = [
            ((-1, 0, None), 1, "no profile answer, no output size: x0.8 as before"),
            ((-1, 1, None), 0, "no profile answer, an output size: the full scale as before"),
            ((0, 0, None), 0, "Bananza's profile: the full scale"),
            ((0, 1, None), 0, "Bananza's profile with an output size: still its own"),
            ((0, 0, "2"), 2, "HOS_GRAPHICS_SCALE_ENUM=2 over the profile"),
            ((-1, 1, "1"), 1, "HOS_GRAPHICS_SCALE_ENUM=1 over the output size"),
            ((0, 0, "7"), 0, "HOS_GRAPHICS_SCALE_ENUM=7 is ignored"),
            ((-1, 0, "x"), 1, "HOS_GRAPHICS_SCALE_ENUM=x is ignored"),
        ]
        for (scale, out, env), want, what in cases:
            got = answer(scale, out, env)
            if got != want:
                bad.append("%s: %d, want %d" % (what, got, want))
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: GetCurrentGraphicsScale's enum: the profile's, else the host's own; HOS_GRAPHICS_SCALE_ENUM over both")


if __name__ == "__main__":
    main()
