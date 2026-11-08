"""A draw that has to wait for its graphics pipeline builds it itself instead of waiting in line.

A draw into targets made this frame cannot be left out, so it waits for its pipeline. It used to wait for the background
job, queued behind the others, with the driver's optimizer: a new area's first frame stood still for as long as all its
pipelines' compiles added up (31 s and 62 s on an RX 6650 XT after v2.0.2). Now:

- first the pipeline cache is asked, compiling nothing: a pipeline met in an earlier session is there, whole, and the
  worker's build is then not needed;
- on AMD (or RX_VK_QUICK_PIPELINES=1) the draw builds one without the optimizer at once, never over a full build that came
  first, and the worker's full build still replaces it;
- otherwise, or if that fails, the draw takes its own full build from the queue when no worker has begun it, and the
  worker then leaves it alone;
- a pipeline the optimizer fails on is built again without it rather than left out.

Compiles pipeline_build_waited from gpu/vk/vk_draw.cpp with stand-ins for the driver and runs the cases.
python -I -B tests/test_pipeline_wait.py [--cxx clang++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path


def block(text, marker, semicolon=False):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    if semicolon:
        end = text.index(";", end) + 1
    return text[start:end]


FIXTURE = r"""
#include <atomic>
#include <cstdio>
#include <vector>
struct VkPipeline_T {};
using VkPipeline = VkPipeline_T*;
#define VK_NULL_HANDLE nullptr
enum VkPrimitiveTopology { VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST };
struct Program {};
struct NvnState {};
struct Targets {};
struct { void* device = nullptr; struct { bool pipelineCreationCacheControl = true; } vk13; } dev;
VkPipeline_T quick_one, full_one, cached_one;
bool quick_on = false, quick_fails = false, in_cache = false;
VkPipeline full_lands_first = nullptr;   // (a worker's full build stored while the quick one is being made)
int quick_builds = 0, full_builds = 0, lookups = 0, destroyed = 0, notified = 0;
%s
%s
bool quick_pipelines() { return quick_on; }
VkPipeline build_graphics(const Program&, const NvnState&, const Targets&, VkPrimitiveTopology, int part = -1, BuildHow how = BuildHow::Full);
PipeEntry* current = nullptr;
VkPipeline build_graphics(const Program&, const NvnState&, const Targets&, VkPrimitiveTopology, int part, BuildHow how) {
    if (part != -1) return nullptr;
    if (how == BuildHow::Cached) {
        lookups++;
        return in_cache ? &cached_one : nullptr;
    }
    if (how == BuildHow::Quick) {
        quick_builds++;
        if (full_lands_first) current->pipeline = full_lands_first;
        return quick_fails ? nullptr : &quick_one;
    }
    full_builds++;
    return &full_one;
}
void vkDestroyPipeline(void*, VkPipeline p, const void*) { if (p == &quick_one) destroyed++; }
void jobs_notify() { notified++; }
%s
static int fail(const char* what) { std::fprintf(stderr, "FAIL: %%s\n", what); return 1; }
static void reset(PipeEntry& e) {
    current = &e;
    quick_builds = full_builds = lookups = destroyed = notified = 0;
    full_lands_first = nullptr;
    quick_fails = in_cache = false;
    dev.vk13.pipelineCreationCacheControl = true;
}
int main() {
    Program p; NvnState s; Targets at;
    {   // in the pipeline cache: taken from there, nothing compiled, the worker's build not needed
        PipeEntry e; reset(e); quick_on = true; in_cache = true;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (lookups != 1 || quick_builds || full_builds || e.pipeline != &cached_one || !e.ready || !notified)
            return fail("a pipeline the cache has must be taken from it before anything is compiled");
        if (!e.started) return fail("a pipeline taken from the cache must spare the worker its build");
    }
    {   // no cache control on the device: no lookup
        PipeEntry e; reset(e); quick_on = true; in_cache = true; dev.vk13.pipelineCreationCacheControl = false;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (lookups || quick_builds != 1) return fail("without pipeline cache control the cache must not be asked");
    }
    {   // no quick builds, no worker yet: the draw takes its full build and the worker will leave it
        PipeEntry e; reset(e); quick_on = false;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (full_builds != 1 || e.pipeline != &full_one || !e.ready || !e.started || !notified)
            return fail("without quick builds the waiting draw must build the full pipeline itself, and say so");
        if (!e.started.exchange(true)) return fail("the queued job must see that its build was taken");
    }
    {   // a worker has begun it: waited for, nothing built twice
        PipeEntry e; reset(e); quick_on = false; e.started = true;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (full_builds || quick_builds || e.ready) return fail("a build a worker began must be waited for, not made again");
    }
    {   // not in the cache, quick builds: one without the optimizer now, the worker's full build still to come
        PipeEntry e; reset(e); quick_on = true;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (lookups != 1 || quick_builds != 1 || full_builds || e.pipeline != &quick_one || !e.ready || !notified)
            return fail("with quick builds the waiting draw must draw with a quick pipeline at once");
        if (e.started) return fail("a quick build must leave the full build to the worker");
    }
    {   // the worker's full build landed while the quick one was made: the full one stays
        PipeEntry e; reset(e); quick_on = true; full_lands_first = &full_one;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (e.pipeline != &full_one || destroyed != 1 || !e.ready)
            return fail("a quick pipeline must never replace a full one, and is let go when it lost");
    }
    {   // the quick build failed: the full one, here
        PipeEntry e; reset(e); quick_on = true; quick_fails = true;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (full_builds != 1 || e.pipeline != &full_one || !e.ready) return fail("a failed quick build must fall back to the full one");
    }
    {   // ready already: nothing to do
        PipeEntry e; reset(e); quick_on = true; e.ready = true;
        pipeline_build_waited(e, p, s, at, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST);
        if (quick_builds || full_builds) return fail("a ready pipeline must not be built again");
    }
    std::puts("PASS: a waiting draw builds its own pipeline, quick on AMD, and never over a full one");
    return 0;
}
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    source = (a.source / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    entry = block(source, "struct PipeEntry {", semicolon=True)
    how = source[source.index("enum class BuildHow {"):source.index("};", source.index("enum class BuildHow {")) + 2]
    waited = block(source, "void pipeline_build_waited(PipeEntry& e,")
    quick = block(source, "bool quick_pipelines() {")
    if 'getenv("RX_VK_QUICK_PIPELINES")' not in quick or "dev.props.vendorID == 0x1002" not in quick:
        raise SystemExit("quick builds must be RX_VK_QUICK_PIPELINES, on by default on AMD only")
    graphics = block(source, "PipeEntry* graphics_pipeline(const Program& p,")
    job = block(graphics, "jobs_push([job] {")
    if job.index("job->e->started.exchange(true)") > job.index("build_graphics("):
        raise SystemExit("the queued job must leave a pipeline whose waiting draw took its build")
    draw_wait = source.index("pipeline_build_waited(*pe, p, s, at, topo);")
    if not source.rindex("if (may_skip) return void(st_pending_skips++);", 0, draw_wait) < draw_wait < source.index("jobs_wait(pe->ready);", draw_wait):
        raise SystemExit("a draw must build its pipeline itself only when it cannot be left out, before it waits")
    build = block(source, "VkPipeline build_graphics(const Program& p, const NvnState& s, const Targets& at, VkPrimitiveTopology topo, int part, BuildHow how)")
    if "if (how == BuildHow::Cached) ci.flags |= VK_PIPELINE_CREATE_FAIL_ON_PIPELINE_COMPILE_REQUIRED_BIT;" not in build:
        raise SystemExit("a cache lookup must not compile")
    cached = block(build, "if (how == BuildHow::Cached) {   //")
    if "return r == VK_SUCCESS ? pl : VK_NULL_HANDLE;" not in cached or build.index(cached) > build.index("if (r != VK_SUCCESS && !(ci.flags"):
        raise SystemExit("a cache miss must return nothing, before the retry without the optimizer")
    if "if (noopt || quick) ci.flags |= VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT;" not in build:
        raise SystemExit("a quick build must be made without the driver's optimizer")
    if "guarded_graphics(part >= 0 || quick ? VK_NULL_HANDLE : pcache, &ci, &pl)" not in build:
        raise SystemExit("a quick build must stay out of the pipeline cache")
    retry = "if (r != VK_SUCCESS && !(ci.flags & VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT)) {"
    if retry not in build or "ci.flags |= VK_PIPELINE_CREATE_DISABLE_OPTIMIZATION_BIT;" not in block(build, retry):
        raise SystemExit("a pipeline the optimizer fails on must be built again without it")
    if "SLOW compile" not in build:
        raise SystemExit("slow graphics compiles must be logged")
    with tempfile.TemporaryDirectory(prefix="pipeline-wait-") as tmp:
        src, exe = Path(tmp) / "fixture.cpp", Path(tmp) / "fixture.exe"
        src.write_text(FIXTURE % (entry, how, waited), encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
