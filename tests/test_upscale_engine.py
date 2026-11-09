"""The FSR upscaler row's choice: which engine each value resolves to, what the row says, and the camera read out of a
view projection. Compiles tests/upscale_engine_test.cpp against gpu/vk/upscale_engine.h; no game, GPU, Vulkan or SDK.

python -I -B tests/test_upscale_engine.py [--cxx clang++]
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
    with tempfile.TemporaryDirectory(prefix="upscale-engine-") as tmp:
        exe = Path(tmp) / "upscale_engine_test"
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(ROOT / "gpu/vk"),
                        str(ROOT / "tests/upscale_engine_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
