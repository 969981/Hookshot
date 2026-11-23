"""Compile and run the VRAM mirrors' budget rule (gpu/vk/mirror_budget.h; no game/GPU required), and check that vk_memory.cpp's
mirror_budget still uses it alone: one rule by the card, with no Quality preset or menu row over it (an 8 GB card's Balanced
preset held 1024 MB, where the pools went over PCIe).

python -I -B tests/test_mirror_budget.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "g++"]

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    if marker not in text:
        raise SystemExit("gpu/vk/vk_memory.cpp no longer has: " + marker)
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def check_source():
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    if '#include "mirror_budget.h"' not in mem:
        raise SystemExit("vk_memory.cpp no longer includes mirror_budget.h")
    budget = block(mem, "uint64_t mirror_budget() {")
    # (#637: a 6 GB card, over 4.5 GB and up to 6.5, a third of the card, at most 1700 MB; a small card keeps mirror_budget.h's
    # 256 MB, and the rest its rule)
    for needle in ['getenv("RX_VK_MIRROR_MB")', "mirror_rule::auto_bytes(vram)",
                   "if (vram && !mirror_rule::small_card(vram) && vram <= (6ull << 30) + (512ull << 20))",
                   "return std::min<uint64_t>(1700ull << 20, vram / 3);"]:
        if needle not in budget:
            raise SystemExit("vk_memory.cpp: mirror_budget() no longer has: " + needle)
    if "vram / 8" in budget or "vram / 4" in budget:
        raise SystemExit("vk_memory.cpp: mirror_budget() works the size out itself again, apart from mirror_budget.h")
    # (no preset or row: the options do not reach it)
    if "opts." in budget or "preset_caps_memory" in mem:
        raise SystemExit("vk_memory.cpp: mirror_budget() reads the options again (a preset's or a row's mirrors)")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="mirror-budget-") as tmp:
        exe = Path(tmp) / "mirror-budget"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/test_mirror_budget.cpp"), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
