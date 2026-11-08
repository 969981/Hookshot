"""Include the real game-path header first and run its actual POSIX forwarding implementation.

The released v2.0.1 header used DWORD/GetModuleFileNameA before including the
POSIX windows.h. --header accepts that exact file as a compile-negative. No
Windows types/prototypes are mocked and no game, GUI or device is involved.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--header", type=Path, default=ROOT / "host/hos_paths.h")
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("test_linux_game_path requires a Linux compiler and runtime")
    source = (ROOT / "host/posix/win32_posix.c").read_text(encoding="utf-8")
    actual = block(source, "static void exe_path(") + "\n" + block(source, "DWORD GetModuleFileNameA(")
    fixture = r'''#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
/* Must precede every other declaration: including windows.h ourselves would hide the released bug. */
#include "hos_paths.h"
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
''' + actual + r'''
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char **argv) {
    CHECK(argc == 2);
    const char *expected = argv[1];
    const size_t size = strlen(expected);
    CHECK(size > 8 && size < PATH_MAX);
    char path[PATH_MAX];
    const DWORD count = hos_game_path_a(path, sizeof path);
    CHECK(count == size && strcmp(path, expected) == 0);
    char direct[PATH_MAX];
    CHECK(GetModuleFileNameA(NULL, direct, sizeof direct) == count);
    CHECK(strcmp(path, direct) == 0);
    char short_path[10];
    memset(short_path, '?', sizeof short_path);
    CHECK(hos_game_path_a(short_path + 1, 8) == 7);
    CHECK(memcmp(short_path + 1, expected, 7) == 0 && short_path[8] == 0);
    CHECK(short_path[0] == '?' && short_path[9] == '?');
    memset(short_path, '?', sizeof short_path);
    CHECK(hos_game_path_a(short_path + 1, 1) == 0 && short_path[1] == 0);
    CHECK(short_path[0] == '?' && short_path[2] == '?');
    CHECK(hos_game_path_a(short_path, 0) == 0 && short_path[0] == '?');
    puts("PASS: standalone header, actual POSIX executable path and bounded forwarding");
    return 0;
}
'''
    def run(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                preexec_fn=lambda: os.nice(10))
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            raise SystemExit(result.returncode)
    with tempfile.TemporaryDirectory(prefix="hookshot-game-path-") as temporary:
        folder = Path(temporary)
        shutil.copyfile(args.header, folder / "hos_paths.h")
        authored = folder / "fixture.c"
        authored.write_text(fixture, encoding="utf-8")
        executable = folder / "fixture"
        run([args.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "host/posix"),
             str(authored), "-ldl", "-o", str(executable)])
        flat = folder / "flat game" / "hookshot"
        nested = folder / "game with bin" / "bin" / "hookshot"
        for path in (flat, nested):
            path.parent.mkdir(parents=True)
            shutil.copy2(executable, path)
            run([str(path), str(path.resolve())])
        # Unlike Windows rebasing, Linux keeps the executable's path even when bin/ has a sibling image marker.
        (nested.parent.parent / "image.bin").write_text("authored marker only", encoding="utf-8")
        run([str(nested), str(nested.resolve())])


if __name__ == "__main__":
    main()
