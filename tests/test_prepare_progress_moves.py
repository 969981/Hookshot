"""A preparation's progress never sits still while work goes on (gpu/vk/vk_draw.cpp precompile_warm, the --prepare-shaders
run's last part and the first start after a preparation). The Builder's bar and the preparation's watchdog (vk_main.cpp
prepare_watchdog: a part whose count has not moved for 300 s is stopped as a driver that stopped responding) both read this
count. Its compute step used to report the notes' count, (total, total), on every tick: the bar sat at 100% and the watchdog
stopped healthy runs on GPUs whose compute step is long, leaving the preparation skipped and no pipeline cache (worklist
row 3.2, #73).

Game-free: precompile_warm cut from the source by its text and compiled with stubs (no Vulkan, no notes file): 120 noted
pipelines, one a program, and 200 compute programs, each build 3 ms on 2 threads, through the real gpu/vk/warm_bounded.h. At
every progress report:
  - the total is the same, the notes and the computes together (the bar's share does not jump, and its end is the end);
  - the count never goes back and never passes the total;
  - the builds finished so far are never more than the threads ahead of the count: whatever is built, the count says;
and the reports reach the total, with several of them inside the compute step (the check ran where it used to sit still).

    python -I -B tests/test_prepare_progress_moves.py [--cxx clang++] [--source gpu/vk/vk_draw.cpp] [--expect-still]

--expect-still: the source is one whose progress sits still (main before #73): the check must fail on it.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block  # noqa: E402

HEAD = r"""
#include "warm_bounded.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
using VkPipeline = uintptr_t;
using VkResult = int;
constexpr VkPipeline VK_NULL_HANDLE = 0;
constexpr VkResult VK_SUCCESS = 0;
enum VkPrimitiveTopology { VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST };
struct VkComputePipelineCreateInfo { uint64_t key = 0; };
struct { uintptr_t device = 1; } dev;
struct NoteRef { uint64_t key = 0, prog = 0, variant = 0; int64_t at = 0; };
struct PipeNote { int s = 0; int topo = 0; };
struct Stage { int module = 1; };
struct Program { uint64_t key = 0, variant = 0; bool compute = false, failed = false; std::vector<Stage> stages{Stage{}}; };

constexpr size_t kNotes = 120, kComputes = 200;
constexpr unsigned kThreads = 2;
static std::string folder;
static std::atomic<size_t> builds{0};   // (builds finished: graphics and compute)
static void build_time() { std::this_thread::sleep_for(std::chrono::milliseconds(3)); }

std::string precompile_built_path() { return folder + "/hookshot_pipelines.bin.built"; }
std::string pipe_notes_path() { return folder + "/hookshot_pipelines.bin"; }
void precompile_built_write(size_t) {}
void pcache_init() {}
void pipeline_cache_save(bool) {}
bool env_on(const char*) { return false; }
bool prepare_stopped() { return false; }
unsigned usable_cpus() { return kThreads; }
void ub_thread_off() {}
void ensure_layout(Program&) {}
bool taau_key(uint64_t) { return false; }
void logf(const char*, ...) {}
void prepare_progress_log(const char*, size_t, size_t, std::chrono::steady_clock::time_point,
                          std::chrono::steady_clock::time_point&) {}
std::vector<NoteRef> pipe_notes_refs() {
    std::vector<NoteRef> refs;
    for (size_t i = 0; i < kNotes; i++) refs.push_back({1000 + i, 1 + i, 0, (int64_t)i});
    return refs;
}
std::vector<uint64_t> program_cache_compute_keys() {
    std::vector<uint64_t> keys;
    for (size_t i = 0; i < kComputes; i++) keys.push_back(100000 + i);
    return keys;
}
template <class Fn> void program_cache_borrow(uint64_t key, Fn&& fn) {
    Program p;
    p.key = key;
    p.compute = key >= 100000;
    fn(p);
}
bool pipe_note_at(FILE*, int64_t, PipeNote*) { return true; }
int note_targets(const PipeNote&) { return 0; }
VkPipeline build_graphics(Program&, int, int, VkPrimitiveTopology) { build_time(); builds++; return 1; }
VkComputePipelineCreateInfo compute_pipeline_info(Program& p, bool) { return {p.key}; }
VkResult cached_compute(const VkComputePipelineCreateInfo*, VkPipeline* out) { build_time(); builds++; *out = 1; return VK_SUCCESS; }
void vkDestroyPipeline(uintptr_t, VkPipeline, const void*) {}

struct Report { size_t built, done, total; };
static std::mutex reports_mu;
static std::vector<Report> reports;
static void progress(size_t done, size_t total) {
    std::lock_guard lk(reports_mu);
    reports.push_back({builds.load(), done, total});
}
"""

MAIN = r"""
int main(int argc, char** argv) {
    folder = argc > 1 ? argv[1] : ".";
    precompile_warm(progress);
    int bad = 0;
    size_t last = 0, in_computes = 0;
    for (const Report& r : reports) {
        if (r.total != kNotes + kComputes) {
            if (bad++ < 5) std::printf("FAIL: a report's total is %zu, not the notes and the computes, %zu\n", r.total, kNotes + kComputes);
        }
        if (r.done < last || r.done > r.total) {
            if (bad++ < 5) std::printf("FAIL: the count went from %zu to %zu (total %zu)\n", last, r.done, r.total);
        }
        if (r.built > r.done + kThreads) {
            if (bad++ < 5) std::printf("FAIL: %zu builds finished, the count says %zu: it sat still while work went on\n", r.built, r.done);
        }
        if (r.built > kNotes && r.built < kNotes + kComputes) in_computes++;
        last = r.done;
    }
    if (reports.empty() || reports.back().done != reports.back().total) {
        std::printf("FAIL: the reports did not reach the total\n");
        bad++;
    }
    if (in_computes < 2) {
        std::printf("FAIL: only %zu reports inside the compute step: the check did not run there\n", in_computes);
        bad++;
    }
    std::printf("%zu reports, %zu inside the compute step, %zu builds\n", reports.size(), in_computes, builds.load());
    return bad ? 1 : 0;
}
"""


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cxx", default=shutil.which("clang++") or shutil.which("g++") or "c++")
    p.add_argument("--source", type=Path, default=ROOT / "gpu" / "vk" / "vk_draw.cpp")
    p.add_argument("--expect-still", action="store_true", help="the source's progress sits still: the check must fail")
    a = p.parse_args()
    src = a.source.read_text(encoding="utf-8")
    warm = block(src, "void precompile_warm(void (*progress)(size_t done, size_t total)) {")
    with tempfile.TemporaryDirectory(prefix="prepare-progress-") as tmp:
        cpp, exe = Path(tmp) / "warm.cpp", Path(tmp) / "warm"
        cpp.write_text(HEAD + warm + MAIN, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-pthread", "-I", str(ROOT / "gpu" / "vk"), str(cpp), "-o",
                        str(exe)], check=True)
        r = subprocess.run([str(exe), tmp], capture_output=True, text=True)
        print(r.stdout, end="")
    if a.expect_still:
        if not r.returncode:
            raise SystemExit("FAIL: --expect-still, but the progress moved")
        print("PASS: the check catches a progress that sits still")
        return
    if r.returncode:
        raise SystemExit(1)
    print("PASS: the preparation's count moves with every build, the compute step's too, to one total")


if __name__ == "__main__":
    main()
