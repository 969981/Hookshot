"""Exercise the renderer's real opt-in and sharpening functions in fresh processes.

These checks require neither game data nor the optional AMD runtime. Compiling
both platform branches checks that unsupported hosts cannot enable this path.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = source.index(name)
    at = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[at] == "{") - (source[at] == "}")
        at += 1
    return source[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    source = (ROOT / "gpu/vk/vk_fsr_native.cpp").read_text(encoding="utf-8")
    enabled = function(source, "bool fsr_native_enabled(").replace("#ifdef _WIN32", "#if TEST_WINDOWS")
    sharpening = function(source, "float sharpening(")
    fixture = "#include <algorithm>\n#include <cmath>\n#include <cstdlib>\n#include <cstdio>\n" + enabled + "\n" + sharpening
    fixture += '\nint main() { std::printf("%d %.3f\\n", int(fsr_native_enabled()), sharpening()); }\n'
    env = {k: v for k, v in os.environ.items() if k not in
           ("RX_VK_FSR_NATIVE_AA", "RX_VK_FSR_SHARPNESS", "RX_VK_PARALLEL")}
    cases = [
        ({}, "0 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "0"}, "0 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "1"}, "1 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "1", "RX_VK_PARALLEL": "0"}, "1 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "1", "RX_VK_PARALLEL": "1"}, "1 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "1", "RX_VK_PARALLEL": "2"}, "0 0.000"),
        ({"RX_VK_FSR_NATIVE_AA": "1", "RX_VK_PARALLEL": "8"}, "0 0.000"),
        ({"RX_VK_FSR_SHARPNESS": "0.4"}, "0 0.400"),
        ({"RX_VK_FSR_SHARPNESS": "-1"}, "0 0.000"),
        ({"RX_VK_FSR_SHARPNESS": "2"}, "0 1.000"),
        ({"RX_VK_FSR_SHARPNESS": "nan"}, "0 0.000"),
        ({"RX_VK_FSR_SHARPNESS": "inf"}, "0 0.000"),
        ({"RX_VK_FSR_SHARPNESS": "invalid"}, "0 0.000"),
    ]
    with tempfile.TemporaryDirectory(prefix="hookshot-fsr-options-") as temp:
        folder = Path(temp)
        cpp = folder / "options.cpp"
        cpp.write_text(fixture, encoding="utf-8")
        for windows in (False, True):
            binary = folder / ("windows.exe" if windows else "unsupported.exe")
            subprocess.run([args.cxx, "-std=c++17", "-O2", f"-DTEST_WINDOWS={int(windows)}",
                            str(cpp), "-o", str(binary)], check=True)
            for values, expected in cases:
                if not windows:
                    expected = "0 " + expected.split(" ", 1)[1]
                actual = subprocess.run([str(binary)], env={**env, **values}, check=True,
                                        capture_output=True, text=True).stdout.strip()
                assert actual == expected, (windows, values, actual, expected)
    print("PASS: 26 FSR opt-in/platform/parallel-replay/sharpening cases")


if __name__ == "__main__":
    main()
