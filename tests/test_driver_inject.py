"""RX_VK_TEST_DRIVER (gpu/vk/test_driver.h): a driver that stops answering, faults or is slow at one pipeline build, so the
preparation's watchdog and the driver-fault guard can be run on any GPU. This compiles test_driver.h's parse and next
against a driver of the test's own and checks: each form is read (hang@<n>, fault@<n>, slow:<ms>@<n>, the hold at most
600 s); anything else is ignored and said to be bad; the plan acts at its one build, counted from 1 across threads, and at
no other. It also checks both platforms' guarded_graphics raise the fault inside the guard (where a driver's own fault is
caught) and that a hang never returns. No game or GPU.

python -I -B tests/test_driver_inject.py [--cxx g++]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block  # noqa: E402

FIXTURE = r"""
#include "test_driver.h"
#include <cstdio>
#include <thread>
#include <vector>
using namespace test_driver;
static int bad_count;
#define CHECK(x) do { if (!(x)) { std::printf("FAIL: %s\n", #x); bad_count++; } } while (0)
int main() {
    bool bad = false;
    Plan p = parse("hang@3", &bad);
    CHECK(!bad && p.kind == Kind::kHang && p.at == 3);
    p = parse("fault@120", &bad);
    CHECK(!bad && p.kind == Kind::kFault && p.at == 120);
    p = parse("slow:2500@7", &bad);
    CHECK(!bad && p.kind == Kind::kSlow && p.at == 7 && p.ms == 2500);
    p = parse("slow:9999999@1", &bad);
    CHECK(!bad && p.ms == 600000);
    for (const char* e : {"hang", "hang@", "hang@0", "hang@-2", "hang@3x", "fault@", "slow:@4", "slow:0@4", "slow:5", "wobble@2",
                          "slow:x@3"}) {
        p = parse(e, &bad);
        CHECK(bad && p.kind == Kind::kNone);
    }
    p = parse(nullptr, &bad);
    CHECK(!bad && p.kind == Kind::kNone);
    p = parse("", &bad);
    CHECK(!bad && p.kind == Kind::kNone);
    // (one build of many acts, whatever thread reaches it)
    std::atomic<int64_t> count{0};
    const Plan plan = parse("fault@500", &bad);
    std::atomic<int> acted{0};
    std::vector<std::thread> ts;
    for (int t = 0; t < 8; t++)
        ts.emplace_back([&] { for (int i = 0; i < 250; i++) if (next(plan, count) == Kind::kFault) acted++; });
    for (auto& t : ts) t.join();
    CHECK(acted == 1 && count == 2000);
    // (none: never acts, and counts nothing)
    std::atomic<int64_t> none{0};
    for (int i = 0; i < 10; i++) CHECK(next(Plan{}, none) == Kind::kNone);
    CHECK(none == 0);
    if (!bad_count) std::puts("ok");
    return bad_count;
}
"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++"))
    a = p.parse_args()
    src = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    # (the wiring: each platform's guard asks before the driver call and raises the fault inside the guarded part)
    win = src[src.index("#ifdef _WIN32\n__declspec(noinline) VkResult guarded_graphics("):]
    win = block(win, "__declspec(noinline) VkResult guarded_graphics(")
    if "test_driver_now() == test_driver::Kind::kFault" not in win or win.index("RaiseException(EXCEPTION_ACCESS_VIOLATION") < win.index("__try {"):
        raise SystemExit("FAIL: Windows' guarded_graphics does not raise RX_VK_TEST_DRIVER's fault inside its __try")
    lin = block(src, "VkResult guarded_graphics(VkPipelineCache cache, const VkGraphicsPipelineCreateInfo* ci, VkPipeline* pl) {\n    install_driver_fault_guard();")
    if "test_driver_now()" not in lin or not (lin.index("fault_armed = 1;") < lin.index("raise(SIGSEGV)") < lin.index("vkCreateGraphicsPipelines")):
        raise SystemExit("FAIL: Linux's guarded_graphics does not raise RX_VK_TEST_DRIVER's fault in its armed window")
    now = block(src, "test_driver::Kind test_driver_now() {")
    if "for (;;) Sleep(1000);" not in now:
        raise SystemExit("FAIL: RX_VK_TEST_DRIVER's hang returns")
    with tempfile.TemporaryDirectory(prefix="driver-inject-") as tmp:
        cpp, exe = Path(tmp) / "t.cpp", Path(tmp) / "t.exe"
        cpp.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread", "-I", str(ROOT / "gpu" / "vk"), str(cpp),
                        "-o", str(exe)], check=True)
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print(r.stdout, end="")
        if r.returncode:
            sys.exit(1)
    print("PASS: RX_VK_TEST_DRIVER is read as written, acts at its one build, and is wired inside both platforms' guards")


if __name__ == "__main__":
    main()
