"""The RX_VK_SHOT_FILE one-shot request protocol, compiled from the production function.

The fixture extracts read_shot_request() from gpu/vk/vk_replay.cpp and exercises the rules the MCP server relies on:
no environment, a missing request, an empty request, a path with spaces and CRLF, a request read only once, and a
path longer than the caller's buffer. No game, GPU or driver is involved.

    python -I -B tests/test_shot_request.py [--cxx g++]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "gpu" / "vk" / "vk_replay.cpp"

FIXTURE = r'''
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)

__FUNCTION__

#ifdef _WIN32
static void set_env(const char* key, const char* value) { _putenv_s(key, value); }
static void unset_env(const char* key) { _putenv_s(key, ""); }
#else
static void set_env(const char* key, const char* value) { setenv(key, value, 1); }
static void unset_env(const char* key) { unsetenv(key); }
#endif

static bool write_file(const std::string& path, const std::string& data) {
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    const bool ok = std::fwrite(data.data(), 1, data.size(), f) == data.size();
    std::fclose(f);
    return ok;
}

static bool file_exists(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::fclose(f);
    return true;
}

int main(int argc, char** argv) {
    CHECK(argc == 2);
    const std::string dir = argv[1];
    char out[64];

    unset_env("RX_VK_SHOT_FILE");
    CHECK(!read_shot_request(out, sizeof out));

    set_env("RX_VK_SHOT_FILE", (dir + "/missing.request").c_str());
    CHECK(!read_shot_request(out, sizeof out));

    const std::string empty = dir + "/empty.request";
    CHECK(write_file(empty, "\n"));
    set_env("RX_VK_SHOT_FILE", empty.c_str());
    CHECK(!read_shot_request(out, sizeof out));
    CHECK(!file_exists(empty));

    const std::string request = dir + "/request.txt";
    CHECK(write_file(request, "C:\\tmp\\shot 1.png\r\n"));
    set_env("RX_VK_SHOT_FILE", request.c_str());
    char path[128];
    CHECK(read_shot_request(path, sizeof path));
    CHECK(std::string(path) == "C:\\tmp\\shot 1.png");
    CHECK(!file_exists(request));
    CHECK(!read_shot_request(path, sizeof path));   /* one request, one shot */

    const std::string long_request = dir + "/long.request";
    CHECK(write_file(long_request, std::string(300, 'a') + "\n"));
    set_env("RX_VK_SHOT_FILE", long_request.c_str());
    char small[32];
    CHECK(read_shot_request(small, sizeof small));
    CHECK(std::strlen(small) == sizeof small - 1);
    CHECK(!file_exists(long_request));
    return 0;
}
'''


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default="g++", help="C++ compiler (default: g++)")
    args = parser.parse_args()
    source = REPLAY.read_text(encoding="utf-8")
    function = block(source, "bool read_shot_request(")
    fixture = FIXTURE.replace("__FUNCTION__", function)

    with tempfile.TemporaryDirectory(prefix="shot-request-") as tmp:
        folder = Path(tmp)
        cpp = folder / "fixture.cpp"
        exe = folder / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8", newline="\n")
        build = subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(build.stdout, end="")
        print(build.stderr, end="", file=sys.stderr)
        build.check_returncode()
        run = subprocess.run([str(exe), str(folder)], capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        print(run.stdout, end="")
        print(run.stderr, end="", file=sys.stderr)
        run.check_returncode()
    print("PASS: RX_VK_SHOT_FILE requests are trimmed, one-shot and safe at the buffer edge")


if __name__ == "__main__":
    main()
