"""The upscaler input contract: which of a game network's sampled bindings are colour, motion and depth, which Vulkan
formats mean which, and the sets it has to refuse instead of guessing. An upscaler fed a wrong guess ghosts, and the
ghosting is reported as a renderer bug. Compiles tests/upscale_contract_test.cpp against gpu/vk/upscale_contract.h and
gpu/vk/upscale_format.h; needs the Vulkan headers, no game, GPU or SDK.

python -I -B tests/test_upscale_contract.py [--cxx clang++] [--headers <vulkan include dir>]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    p.add_argument("--headers", help="a Vulkan headers include folder; the compiler's own path by default")
    a = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="upscale-contract-") as tmp:
        exe = Path(tmp) / "upscale_contract_test"
        cmd = [a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "gpu/vk")]
        if a.headers:
            cmd.append("-I" + a.headers)
        cmd += [str(ROOT / "tests/upscale_contract_test.cpp"), "-o", str(exe)]
        subprocess.run(cmd, check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
