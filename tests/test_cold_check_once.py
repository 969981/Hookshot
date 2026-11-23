"""The lost-cache check (vk_draw.cpp's notes_lost_by_driver, Longshot #1188) builds the notes again once per driver.

It asks the driver's own cache alone, so with that cache off (NVIDIA's shader cache disabled, __GL_SHADER_DISK_CACHE=0, AMD's off)
or too small for the notes it finds them lost at every start, and every start built them all again before the title. This
compiles gpu/vk/cold_check.h and runs the starts: the first with a lost cache asks and records the driver, the next with the same
driver does not ask, and a new driver (an update, another card) asks again.

    python -I -B tests/test_cold_check_once.py [--cxx clang++]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r"""
#include "cold_check.h"
#include <cstdio>
#include <cstdlib>
#include <string>
static int fails;
#define CHECK(name, cond) do { if (!(cond)) { std::printf("FAIL: %s (line %d)\n", name, __LINE__); fails++; } } while (0)
int main() {
    const std::string rec = std::string(std::getenv("CHECK_DIR")) + "/hookshot_pipelines.bin.rewarmed";
    std::remove(rec.c_str());
    const uint8_t uuid[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
    const std::string a = cold_check::stamp(0x10de, 0x2d04, 0x99c04800, uuid);
    const std::string b = cold_check::stamp(0x10de, 0x2d04, 0x99c05000, uuid);   // (a driver update)
    CHECK("a stamp names the vendor, the device, the driver and the cache's UUID",
          a == "000010de 00002d04 99c04800 0102030405060708090a0b0c0d0e0f10");
    CHECK("no record: the first start asks", cold_check::ask(rec, a));
    cold_check::record(rec, a);   // (it found them lost: they are built again for this driver)
    CHECK("the record reads back", cold_check::recorded(rec) == a);
    CHECK("the next start with the same driver does not ask", !cold_check::ask(rec, a));
    CHECK("nor the one after", !cold_check::ask(rec, a));
    CHECK("a new driver asks again", cold_check::ask(rec, b));
    cold_check::record(rec, b);
    CHECK("and is recorded in its place", !cold_check::ask(rec, b) && cold_check::ask(rec, a));
    std::remove(rec.c_str());
    if (!fails) std::puts("PASS: the notes are built again once per driver");
    return fails ? 1 : 0;
}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cxx", default="clang++")
    args = ap.parse_args()
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    start = draw.index("bool notes_lost_by_driver() {")
    check = draw[start:draw.index("\n}\n", start)]
    for need in ('const std::string rewarmed = pipe_notes_path() + ".rewarmed";',
                 "if (!cold_check::ask(rewarmed, driver)) {", "if (lost) cold_check::record(rewarmed, driver);"):
        if need not in check:
            raise SystemExit("vk_draw.cpp: notes_lost_by_driver() no longer has " + need)
    if check.index("if (!cold_check::ask(rewarmed, driver)) {") > check.index("pipe_notes_refs()"):
        raise SystemExit("vk_draw.cpp: notes_lost_by_driver() reads the notes before it asks whether to ask at all")
    with tempfile.TemporaryDirectory(prefix="hookshot-cold-check-") as tmp:
        folder = Path(tmp).resolve()
        src, exe = folder / "t.cpp", folder / "t.exe"
        src.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                        "-I", str(ROOT / "gpu/vk"), str(src), "-o", str(exe)], check=True)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, env={**os.environ, "CHECK_DIR": str(folder)})
        print(ran.stdout, end="")
        if ran.returncode:
            raise SystemExit(ran.stdout + ran.stderr)


if __name__ == "__main__":
    main()
