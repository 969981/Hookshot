"""The load-at-create start's builds before the title are waited for with progress and a bound (gpu/vk/bounded_wait.h).

On an RX 5600 XT and an RX 5700 XT (AMD's Windows driver 26.5.2 and 26.10.1, the mesh programs through the compute fallback)
every start of rc.7 sat at 0%, "Loading the shader cache", until the window was closed (Longshot #1373, #1405), while
RX_VK_LOAD=start reached the title on the same install. Under RX_VK_LOAD=create (the default) vk_draw.cpp's
precompile_at_create built every mesh fallback program's compute pipelines in the driver before the title, joined its
threads with no bound, and told the start-up screen nothing; precompile_run, RX_VK_LOAD=start's, goes on after
RX_VK_PRECOMPILE_STALL_S seconds without progress. This checks bounded_wait.h's wait (tests/bounded_wait_test.cpp: done,
the window closed, builds that finish slowly, builds that never finish, the switch at 0), and its wiring: precompile_at_create
takes the start-up screen's progress and hands it on, waits through bounded_wait::wait with RX_VK_PRECOMPILE_STALL_S, leaves
the builds still in the driver to run when it went on (its workers' state on the heap, not freed then), writes the pipeline
cache before the title after a long pass as precompile_run does, and precompile_run gives it its progress.
python -I -B tests/test_bounded_wait.py [--cxx c++]
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


def check_sources():
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    problems = []
    if '#include "bounded_wait.h"' not in draw:
        problems.append("vk_draw.cpp does not include bounded_wait.h")
    progress = "void (*progress)(size_t done, size_t total)"
    if "void precompile_at_create(" + progress + ") {" not in draw:
        return problems + ["precompile_at_create takes no progress: the start-up screen shows 0% for as long as it builds"]
    at_create = block(draw, "void precompile_at_create(" + progress + ") {")
    if "bounded_wait::wait(" not in at_create:
        problems.append("precompile_at_create does not wait through bounded_wait::wait: a build that never ends holds the start")
    if 'getenv("RX_VK_PRECOMPILE_STALL_S")' not in at_create:
        problems.append("precompile_at_create's bound is not RX_VK_PRECOMPILE_STALL_S, precompile_run's")
    if "progress(" not in at_create:
        problems.append("precompile_at_create never hands the start-up screen its progress")
    if ".detach()" not in at_create:
        problems.append("precompile_at_create does not leave its builds still in the driver running when it goes on")
    if "delete " in at_create and "if (!gone_on) delete " not in at_create:
        problems.append("precompile_at_create frees its workers' state when they may still run")
    run = block(draw, "void precompile_run(" + progress + ") {")
    if "precompile_at_create(progress)" not in run:
        problems.append("precompile_run does not give precompile_at_create its progress")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    problems = check_sources()
    if not (ROOT / "gpu" / "vk" / "bounded_wait.h").exists():
        problems.append("gpu/vk/bounded_wait.h is missing")
    else:
        with tempfile.TemporaryDirectory(prefix="hookshot-bounded-wait-") as folder:
            exe = Path(folder) / "t"
            r = subprocess.run([cxx, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror",
                                str(ROOT / "tests" / "bounded_wait_test.cpp"), "-o", str(exe)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode:
                raise SystemExit("FAIL: bounded_wait_test.cpp does not compile\n" + r.stdout + r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            print(r.stdout, end="")
            if r.returncode:
                problems.append("bounded_wait_test failed")
    if problems:
        raise SystemExit("FAIL:\n  " + "\n  ".join(problems))
    print("PASS: the load-at-create start's builds before the title have progress and a bound")


if __name__ == "__main__":
    main()
