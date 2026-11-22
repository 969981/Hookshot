"""The start reports the drivers' own shader caches as the environment chooses them (gpu/vk/driver_cache_env.h).

A Steam Deck start from Steam compiled for 20 minutes the 32,438 noted pipelines a start from a shell of the same install found
in 2 s, and the log's "driver shader cache" line read "0 MB (no folder found)" beside a full cache: it looked for Mesa's
mesa_shader_cache_db alone. This checks:
 - driver_cache_env.h (tests/driver_cache_env_test.cpp): Mesa's folder and layouts, NVIDIA's, RADV's VRS switches as RADV reads
   them, the records of the environments that built the pipelines and what a start says against them;
 - vk_draw.cpp: the count goes through it and is bounded; the start logs the environment and compares it with the records; the
   precompile, the warm pass and the prediction record theirs;
 - docs/development.md says what the lines mean.
python -I -B tests/test_driver_cache_env.py [--cxx c++]
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
    if '#include "driver_cache_env.h"' not in draw:
        return ["vk_draw.cpp does not include driver_cache_env.h"]
    count = block(draw, "uint64_t driver_cache_bytes(std::string* where, bool* partial) {")
    if "driver_cache_env::folders(" not in count:
        problems.append("driver_cache_bytes does not count the folders driver_cache_env.h names (Mesa's layouts)")
    if "kMaxEntries" not in count or "deadline" not in count:
        problems.append("driver_cache_bytes walks the folders without a bound on files or time")
    log = block(draw, "void driver_cache_log(const char* when) {")
    for what in ("driver_cache_env::describe(", "driver_cache_env::compare(", "predicted_keys_path()"):
        if what not in log:
            problems.append("driver_cache_log does not use " + what)
    if "docs/" in log or "minutes" in log:
        problems.append("driver_cache_log's words point at a docs file or promise minutes")
    fp = block(draw, "driver_cache_env::Fingerprint driver_fingerprint(")
    if "radv_vrs_capable()" not in fp or "filesystem::exists" not in fp:
        problems.append("the fingerprint does not ask whether RADV can force VRS here and whether the file is there")
    if "VK_DRIVER_ID_MESA_RADV" not in block(draw, "bool radv_vrs_capable() {"):
        problems.append("radv_vrs_capable does not ask for RADV")
    for fn in ("void precompile_run(void (*progress)(size_t done, size_t total)) {",
               "void precompile_warm(void (*progress)(size_t done, size_t total)) {",
               "void predict_run(void (*progress)(size_t done, size_t total)) {"):
        if "driver_env_note()" not in block(draw, fn):
            problems.append(fn.split("(")[0] + " does not record the environment it built in (driver_env_note)")
    docs = (ROOT / "docs" / "development.md").read_text(encoding="utf-8")
    if "vk: driver shader cache:" not in docs or "hookshot_driver_env.txt" not in docs:
        problems.append("docs/development.md does not explain the driver shader cache lines")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    problems = check_sources()
    if not (ROOT / "gpu" / "vk" / "driver_cache_env.h").exists():
        problems.append("gpu/vk/driver_cache_env.h is missing")
    else:
        with tempfile.TemporaryDirectory(prefix="hookshot-driver-env-") as folder:
            exe = Path(folder) / "t"
            r = subprocess.run([cxx, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror",
                                str(ROOT / "tests" / "driver_cache_env_test.cpp"), "-o", str(exe)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode:
                raise SystemExit("FAIL: driver_cache_env_test.cpp does not compile\n" + r.stdout + r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            print(r.stdout, end="")
            if r.returncode:
                problems.append("driver_cache_env_test failed")
    if problems:
        raise SystemExit("FAIL:\n  " + "\n  ".join(problems))
    print("PASS: the start counts and names the drivers' caches, and compares its environment with the builds'")


if __name__ == "__main__":
    main()
