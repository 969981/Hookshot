"""Syntax-check the complete macOS WebGPU renderer against the packaged wgpu-native headers."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "tools"))
sys.path.insert(0, os.path.join(ROOT, "nx2"))
import build
import toolchain


def main():
    wgpu = os.environ["WGPU_NATIVE"]
    cc = toolchain.need(toolchain.find("clang++"))
    gpu = build.GPU
    host = build.HOST
    rt = os.path.join(ROOT, "third_party", "a64-kit", "rt")
    with tempfile.TemporaryDirectory(prefix="hookshot-macos-generated-") as generated:
        for name in ("a64_suyu.h", "a64_fpx.h"):
            with open(os.path.join(generated, name), "w", encoding="utf-8") as f:
                f.write("/* Generated game-specific helpers are unused by renderer sources. */\n")
        flags = [
            *build.target(), "-mcpu=apple-m1", "-std=c++20", "-g0", "-DNDEBUG", "-Wno-everything",
            "-DA64_WRITE_TRACK",
            "-I" + os.path.join(gpu, "compat"),
            "-I" + os.path.join(gpu, "third_party", "suyu"),
            "-I" + os.path.join(wgpu, "include"),
            "-I" + host, "-I" + rt, "-I" + build.POSIX, "-I" + generated,
            "-include", os.path.join(gpu, "render", "rx_wgpu_names.h"),
        ]
        for source in build.gpu_sources():
            result = subprocess.run([cc] + flags + ["-fsyntax-only", source], text=True)
            if result.returncode:
                raise SystemExit("macOS renderer syntax check failed: " + os.path.relpath(source, ROOT))
    print("macOS WebGPU renderer sources compile against wgpu-native.")


if __name__ == "__main__":
    main()
