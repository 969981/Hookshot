"""Every program the shader preparation counts as failed is counted on a line by its reason, the ones past the 30 lines too.

prepare_run's "vk: prepared N shader programs (... M failed ...)" counts st_failed, which vk_shader.cpp adds to in three
places, each with its own "not translated" line that names 30 programs, so that a log is not flooded: translate_into's catch
(not for an answer seed_compute only tries), translate_mesh_into's catch, and a seed stage that does not parse
(seed_stage_failed's two callers). Past 30 the rest went uncounted: a Mac start's "70 failed" named 31 programs (Longshot
#423). shader_fail_lines, right after the preparation's line, now gives each reason's whole count. The networks a card leaves
out on purpose are not failures in this tree (LeftOut): the preparation's line counts them apart.

Checked here, with the counters and shader_fail_lines cut from the source and run with a stand-in logf: 40 failures, 2 mesh
failures and 1 stage that does not parse give 3 count lines, 40 ("the first 30 named above"), 2 and 1; none gives no line.
The source: each of the three places counts on its own counter, and prepare_run logs the count lines right after its own.

No Vulkan, GPU or game. python -I -B tests/test_not_translated_counts.py [--cxx clang++]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    at = text.index(marker)
    end = text.index("{", at) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[at:end]


def check(cond, why):
    if not cond:
        raise SystemExit("vk_shader.cpp: " + why)


FIXTURE = r"""
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
std::vector<std::string> lines;
void logf(const char* fmt, ...) {
    char b[512]; va_list a; va_start(a, fmt); vsnprintf(b, sizeof b, fmt, a); va_end(a); lines.push_back(b);
}
@COUNTERS@
@LINES@
static int fails;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d\n", __LINE__); fails++; } } while (0)
int main() {
    shader_fail_lines();
    CHECK(lines.empty());   // (no failure: no count line)
    st_not_translated = 40, st_mesh_not_translated = 2, st_stage_failed = 1;
    shader_fail_lines();
    CHECK(lines.size() == 3);
    CHECK(lines.size() == 3 && lines[0] == "vk: not translated: 40 programs (the first 30 named above)");
    CHECK(lines.size() == 3 && lines[1] == "vk: not translated, mesh: 2 programs (named above)");
    CHECK(lines.size() == 3 && lines[2] == "vk: not translated, a seed stage that does not parse: 1 program (named above)");
    for (auto& l : lines) std::printf("  %s\n", l.c_str());
    if (!fails) std::puts("PASS: every failed program is counted by its reason");
    return fails ? 1 : 0;
}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cxx", default="clang++")
    args = ap.parse_args()
    shader = (ROOT / "gpu" / "vk" / "vk_shader.cpp").read_text(encoding="utf-8")
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")

    counters = shader[shader.index("constexpr int64_t kFailNamed = 30;"):]
    counters = counters[:counters.index(";", counters.index("std::atomic<int64_t> st_not_translated")) + 1]
    check("st_not_translated, st_mesh_not_translated, st_stage_failed" in counters, "the three failure counters are gone")
    catch = shader[shader.index("} catch (const std::exception& e) {"):]
    catch = catch[:catch.index("st_translate_ns")]
    check("!trial && st_not_translated++ < kFailNamed" in catch, "translate_into's catch no longer counts every failure")
    mesh = block(shader, "void translate_mesh_into(")
    check("st_mesh_not_translated++ < kFailNamed" in mesh, "translate_mesh_into's catch no longer counts every failure")
    seed = block(shader, "void seed_stage_failed(const Code& c, uint64_t program) {")
    check("st_stage_failed++ < kFailNamed" in seed, "seed_stage_failed no longer counts every stage that does not parse")
    check("logged++ < 30" not in catch + mesh + seed, "a 'not translated' line stops counting at 30 again")
    prepared = draw[draw.index('logf("vk: prepared %zu shader programs'):]
    check("shader_fail_lines();" in prepared[:prepared.index("if (warm) {")],
          "vk_draw.cpp: prepare_run no longer logs the count lines right after its own")

    src = FIXTURE.replace("@COUNTERS@", counters).replace("@LINES@", block(shader, "void shader_fail_lines() {"))
    with tempfile.TemporaryDirectory(prefix="hookshot-not-translated-") as tmp:
        c, exe = Path(tmp) / "t.cpp", Path(tmp) / "t.exe"
        c.write_text(src, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", str(c), "-o", str(exe)],
                       check=True)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(ran.stdout, end="")
        if ran.returncode:
            raise SystemExit(ran.stdout + ran.stderr)


if __name__ == "__main__":
    main()
