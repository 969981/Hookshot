"""The shader preparation's driver step is left out only after two starts in a row ended inside it.

prepare_run builds every compute pipeline once for the driver's cache, and a driver that crashed in there would crash at
every start, so a file beside the game says the step began. One start ending inside it used to leave the step out, and the
stamp written at the end kept it out for that version: closing the window while it looked stuck at 99% (AMD, after an
update) cost every later start of that version a full compile of the compute pipelines. The marker now counts starts, as
the precompile's does: the next start tries once more, the second in a row leaves it out. An empty file, written by an
older build, counts as one.

Compiles warm_marker from gpu/vk/vk_draw.cpp on its own and runs it on files in a temporary folder.
python -I -B tests/test_warm_marker.py [--cxx clang++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r"""
#include <cstdio>
#include <string>
%s
static int read_count(const std::string& path) {
    int n = -1;
    if (FILE* f = std::fopen(path.c_str(), "rb")) {
        if (std::fscanf(f, "%%d", &n) != 1) n = -2;
        std::fclose(f);
    }
    return n;
}
static int fail(const char* what) { std::fprintf(stderr, "FAIL: %%s\n", what); return 1; }
int main(int argc, char** argv) {
    const std::string dir = argv[1];
    const std::string fresh = dir + "/fresh.warming";
    if (warm_marker(fresh) != 0) return fail("no file: the step goes ahead");
    if (read_count(fresh) != 1) return fail("no file: the file must count this start");
    if (warm_marker(fresh) != 1) return fail("one start ended inside: tried once more, not left out");
    if (read_count(fresh) != 2) return fail("the retry must count itself");
    if (warm_marker(fresh) != 2) return fail("two starts in a row ended inside: left out");
    const std::string old = dir + "/old.warming";
    if (FILE* f = std::fopen(old.c_str(), "wb")) std::fclose(f);
    if (warm_marker(old) != 1) return fail("an older build's empty file counts as one start");
    if (read_count(old) != 2) return fail("after an older build's file, the retry must count itself");
    std::puts("PASS: the driver step is left out after two starts in a row, not one");
    return 0;
}
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    source = (a.source / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    marker = block(source, "int warm_marker(const std::string& path)")
    prepare = block(source, "void prepare_run(void (*progress)(size_t done, size_t total))")
    if "warm_marker(warming)" not in prepare:
        raise SystemExit("prepare_run does not count its starts with warm_marker")
    if "if (ended_inside >= 2) {" not in prepare or "warm = warm_cs = false;" not in block(prepare, "if (ended_inside >= 2) {"):
        raise SystemExit("prepare_run must leave the driver step out only after two starts ended inside it")
    if "remove(warming.c_str());" not in prepare:
        raise SystemExit("prepare_run must remove the marker once the step has ended")
    with tempfile.TemporaryDirectory(prefix="warm-marker-") as tmp:
        src, exe = Path(tmp) / "fixture.cpp", Path(tmp) / "fixture.exe"
        src.write_text(FIXTURE % marker, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe), tmp], check=True)


if __name__ == "__main__":
    main()
