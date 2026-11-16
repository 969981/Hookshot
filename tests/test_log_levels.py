# SPDX-License-Identifier: GPL-3.0-or-later
"""A log line's level: none written for info, "[trace] " (and error, warn, debug) after the thread's tag for the rest.

The launcher's build log shows info and above unless asked for more, and reads a line's level from that tag, so an info line
must stay exactly as it was (the log files, the bug report and the scripts that read them) and the others must carry it in
the one place it looks. Compiled from the real source:

- host/hos_thread.c hos_vlog and log_level_tag, with host/hos.h's levels: the line each level writes, the tag after
  "[hos <thread>] " (where the launcher reads it), an info line as it was.
- gpu/vk/vk_main.cpp vlogf: the renderer's logf_at gives the host's log the same tag at the start of its text, and logf none.
- gpu/vk/vk_shader.cpp: a movie network left out on a card that can't run it is said once (and with RX_VK_IR each of the
  others, as trace lines), counted apart from the 30 lines a program that failed to translate gets.

python -I -B tests/test_log_levels.py --cxx clang++
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]


def block(text, marker):
    """The function or block that starts at marker, through its closing brace"""
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def statement(text, marker):
    """The declaration that starts at marker, through its semicolon"""
    start = text.index(marker)
    return text[start:text.index(";", start) + 1]


CODE = r"""
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
%(hos_enum)s
struct HosThread { const char* name; };
HosThread* cur;
volatile int hos_control_on;
void hos_control_log(const char*) {}
std::string queued;
static int log_queue(const char* line) { queued = line; return 1; }
%(tag)s
%(hos_vlog)s
void host_line(int level, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    hos_vlog(level, fmt, ap);
    va_end(ap);
}
%(enum)s
int64_t steady_ms() { return 0; }
std::atomic<int64_t> last_log_ms{0};
std::string got;
struct Host { void (*log)(const char*); };
const Host the_host{[](const char* line) { got = line; }};
const Host* host = &the_host;
%(vlogf)s
%(logf)s
%(logf_at)s
int fails = 0;
void want(const std::string& a, const char* b, const char* what) {
    if (a != b) { std::printf("FAIL: %%s: \"%%s\", not \"%%s\"\n", what, a.c_str(), b); fails++; }
}
int main() {
    want(log_level_tag(HOS_LOG_INFO), "", "host: info has no tag");
    want(log_level_tag(HOS_LOG_ERROR), "[error] ", "host: error");
    want(log_level_tag(HOS_LOG_WARN), "[warn] ", "host: warn");
    want(log_level_tag(HOS_LOG_DEBUG), "[debug] ", "host: debug");
    want(log_level_tag(HOS_LOG_TRACE), "[trace] ", "host: trace");
    host_line(HOS_LOG_INFO, "vk: device %%d", 1);
    want(queued, "[hos host] vk: device 1\n", "host: an info line as it was");
    host_line(HOS_LOG_TRACE, "vk: program %%s", "x");
    want(queued, "[hos host] [trace] vk: program x\n", "host: trace after the thread's tag");
    HosThread main_thread{"Main"};
    cur = &main_thread;
    host_line(HOS_LOG_DEBUG, "codec: y");
    want(queued, "[hos Main] [debug] codec: y\n", "host: debug, on a named thread");
    cur = nullptr;
    logf("vk: device %%d", 1);
    want(got, "vk: device 1", "renderer: logf is info, untagged");
    logf_at(log_trace, "vk: program %%016llx not translated", 0x95fca6f01e3030aull);
    want(got, "[trace] vk: program 095fca6f01e3030a not translated", "renderer: trace");
    logf_at(log_debug, "x");
    want(got, "[debug] x", "renderer: debug");
    logf_at(log_info, "y");
    want(got, "y", "renderer: logf_at info is untagged");
    logf_at((LogLevel)42, "z");
    want(got, "z", "renderer: a level out of range is info");
    if (!fails) std::printf("PASS: the log's levels\n");
    return fails != 0;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    a = ap.parse_args()
    thread = (ROOT / "host/hos_thread.c").read_text(encoding="utf-8")
    vk_main = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    internal = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    shader = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")
    hos_h = (ROOT / "host/hos.h").read_text(encoding="utf-8")
    code = CODE % {"hos_enum": statement(hos_h, "enum { HOS_LOG_ERROR"),
                   "tag": block(thread, "static const char *log_level_tag(int level) {"),
                   "hos_vlog": block(thread, "static void hos_vlog(int level, const char *fmt, va_list ap) {"),
                   "enum": statement(internal, "enum LogLevel"),
                   "vlogf": block(vk_main, "static void vlogf("),
                   "logf": block(vk_main, "void logf(const char* fmt, ...) {"),
                   "logf_at": block(vk_main, "void logf_at(LogLevel level, const char* fmt, ...) {")}
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = Path(tmp) / "levels.cpp", Path(tmp) / ("levels" + (".exe" if os.name == "nt" else ""))
        src.write_text(code, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-format-security",
                        "-D_CRT_SECURE_NO_WARNINGS", str(src), "-o", str(exe)], check=True, timeout=180)
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        sys.stdout.write(r.stdout)
        if r.returncode:
            raise SystemExit("FAIL: the log's levels")
    # (the shader's catch: a network left out (LeftOut) is told apart from a failure, said once, and with RX_VK_IR each of the
    # others at trace, never in the 30 lines a failure gets; tests/test_left_out_not_failed.py runs it)
    left = shader[shader.index("} catch (const LeftOut& e) {"):shader.index("} catch (const std::exception& e) {")]
    if "st_left_out++" not in left or "logf_at(log_trace" not in left or 'getenv("RX_VK_IR")' not in left:
        raise SystemExit("FAIL: a network left out is not a trace line counted apart")
    if 'throw LeftOut(' not in shader:
        raise SystemExit("FAIL: the network left out is not thrown as LeftOut, which its catch tells apart")
    print("PASS: a network left out is a trace line, counted apart from failures")


if __name__ == "__main__":
    main()
