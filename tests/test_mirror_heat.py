"""Mirrors by heat: the pool the GPU reads most takes the mirrors of colder ones, and only when that is worth it.

A pool refused for the mirrors' budget got a mirror only when one went unbound for 600 submissions, so with the budget
full of pools bound now and then, the pool bound every submission was read over PCIe for good. mirror_swap_pick
(gpu/vk/vk_memory.cpp) is extracted unmodified and run on the cases below; no GPU, game or Vulkan is involved.

python -I -B tests/test_mirror_heat.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MB = 1 << 20

FIXTURE = r"""
#include <cstddef>
#include <cstdint>
#include <cstdio>

/* ACTUAL_PICK */

static int fails;
static void check(const char* what, size_t got, size_t want) {
    if (got != want) std::printf("FAIL %s: %zu, expected %zu\n", what, got, want), fails++;
}

int main() {
    const uint64_t MB = 1ull << 20;
    uint64_t freed, cold;
    // (the A1000 case: 480 of 495 MB of mirrors, a 160 MB pool with ~3 GB of ranges a window; four 16 MB pools nearly unread
    // and larger warm ones: the cold ones first, enough of them to make room)
    {
        const uint64_t bytes[] = {16 * MB, 16 * MB, 16 * MB, 16 * MB, 64 * MB, 96 * MB, 96 * MB};
        const uint64_t heat[] = {0, 1 * MB, 5 * MB, 9 * MB, 20 * MB, 40 * MB, 60 * MB};
        const size_t k = mirror_swap_pick(bytes, heat, 7, 480 * MB, 495 * MB, 160 * MB, 3000 * MB, &freed, &cold);
        check("hot pool: the coldest that make room", k, 6);
        check("hot pool: bytes freed", freed >> 20, 224);
    }
    // (it fits already: nothing taken)
    {
        const uint64_t bytes[] = {16 * MB};
        const uint64_t heat[] = {0};
        check("fits: no swap", mirror_swap_pick(bytes, heat, 1, 300 * MB, 495 * MB, 160 * MB, 3000 * MB, &freed, &cold), 0);
    }
    // (the candidates together read more than a quarter of what the wanted pool does: kept)
    {
        const uint64_t bytes[] = {80 * MB, 80 * MB};
        const uint64_t heat[] = {150 * MB, 160 * MB};
        check("warm victims: kept", mirror_swap_pick(bytes, heat, 2, 480 * MB, 495 * MB, 96 * MB, 1000 * MB, &freed, &cold), 0);
    }
    // (a pool that reads little: never worth a mirror's copy, however cold the others; v1 traded 16 MB mirrors back and forth so)
    {
        const uint64_t bytes[] = {16 * MB};
        const uint64_t heat[] = {0};
        check("cool wanted pool: no swap", mirror_swap_pick(bytes, heat, 1, 490 * MB, 495 * MB, 16 * MB, 30 * MB, &freed, &cold), 0);
    }
    // (not enough candidates to make room: none taken, rather than some for nothing)
    {
        const uint64_t bytes[] = {16 * MB, 16 * MB};
        const uint64_t heat[] = {0, 0};
        check("too few: none", mirror_swap_pick(bytes, heat, 2, 480 * MB, 495 * MB, 160 * MB, 3000 * MB, &freed, &cold), 0);
        check("too few: nothing freed", freed, 0);
    }
    // (bigger than the whole budget: never)
    {
        const uint64_t bytes[] = {495 * MB};
        const uint64_t heat[] = {0};
        check("over the budget", mirror_swap_pick(bytes, heat, 1, 495 * MB, 495 * MB, 600 * MB, 9000 * MB, &freed, &cold), 0);
    }
    if (!fails) std::printf("mirror heat: all cases pass\n");
    return fails != 0;
}
"""


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    source = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    pick = block(source, "size_t mirror_swap_pick(")
    swap = block(source, "void mirrors_swap_for_hot()")
    # (the swap takes only mirrors the GPU is done with, old enough, and makes the wanted pool first)
    for need in ("p.last_value <= mem_completed", "kSwapHold", "mirror_swap_pick(", "std::iter_swap(mirror_wanted.begin(), hottest)"):
        assert need in swap, "mirrors_swap_for_hot: no " + need
    with tempfile.TemporaryDirectory(prefix="hookshot-mirror-heat-") as temp:
        src, exe = Path(temp) / "fixture.cpp", Path(temp) / "fixture.exe"
        src.write_text(FIXTURE.replace("/* ACTUAL_PICK */", pick), encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
