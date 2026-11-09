"""The upscaler seam: which backend takes the game's own upscale dispatch, and every reason a temporal backend is
refused it. The stand-in is a spatial resolve and has to keep running where the contract is not met, which is the
property that makes introducing the seam a no-op. Compiles tests/upscale_seam_test.cpp against
gpu/vk/upscale_seam.h; no game, GPU, Vulkan or SDK.

python -I -B tests/test_upscale_seam.py [--cxx clang++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="upscale-seam-") as tmp:
        exe = Path(tmp) / "upscale_seam_test"
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(ROOT / "gpu/vk"),
                        str(ROOT / "tests/upscale_seam_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
