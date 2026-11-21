"""The depth draws of a skinned mesh the game no longer shows are left out (#1370): the camera inside Link.

When the camera is pushed into Link (the crawl tunnels, the top of a vine wall) the game stops drawing his colour but goes
on drawing his 30 meshes into the scene's depth, in draws that write depth alone (its z-prepass). The temporal upscalers
and frame generation take that depth and the actor motion drawn over it, so they follow an invisible body in front of the
room: copies of the background and a drag over the picture. The rule (gpu/vk/hidden_mesh.h) leaves those draws out once a
mesh has had no colour for kHiddenAfter frames in a row after it had colour; vk_draw.cpp draw() asks it right after
skin_check, before the draw is given to actor motion and before its descriptors are built.

A compiled check of the rule's whole decision (Tracker::draw) over frames as the game draws them (each mesh's depth draws
first, then its colour): Link visible, then the camera in him, then out again (the frame his colour comes back is counted);
a mesh that never had colour; meshes the game asks about with occlusion queries (its culling); meshes that blink (colour
every other frame, every other two frames), which the game's EQUAL colour test would otherwise lose, and one with three
frames off, whose first frame back is lost and counted; colour into another target, and from a draw that is not skinned;
depth into another target than the scene's; a draw with no vertex from stream 0; a frame skipped; a mesh missing for a
frame; a full table; the stencil count; the rule off (RX_VK_DLSS_FADED=0: counted, nothing left out); and the frame
summaries the log is written from. Then source checks of what draw() gives the rule, where it asks it, and its switches.
No game, no GPU, no threads.

python -I -B tests/test_hidden_mesh_depth.py [--cxx g++]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"

FIXTURE = r'''
#include "hidden_mesh.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <vector>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
using namespace mkwvk::hidden_mesh;

constexpr uint64_t kScene = 0x5ce7e, kOther = 0x07e4;   // (the scene's depth target, and another of its size)
static int scene_asks = 0;
static bool is_scene(uint64_t target) { return scene_asks++, target == kScene; }

struct Draws {
    uint64_t key;
    int depth, colour;
    bool query, colour_elsewhere, stencil;
    Draws(uint64_t k, int d, int c, bool q = false, bool e = false, bool st = false)
        : key(k), depth(d), colour(c), query(q), colour_elsewhere(e), stencil(st) {}
};

static Draw depth_draw(uint64_t key, bool query = false, bool stencil = false, uint64_t target = kScene) {
    Draw d;
    d.key = key, d.program = 0x1000 + key, d.target = target, d.skinned = true, d.depth_write = true;
    d.stencil = stencil, d.query = query;
    return d;
}
static Draw colour_draw(uint64_t key, uint64_t target = kScene, bool skinned = true) {
    Draw d;
    d.key = key, d.program = 0x2000 + key, d.target = target, d.skinned = skinned, d.colour = true;
    return d;
}

// (one frame as the game draws it: every mesh's depth draws first, its z-prepass, then the colour. The depth draws left out)
static int frame(Tracker& t, uint64_t f, const std::vector<Draws>& meshes, bool rule = true, Summary* s = nullptr) {
    const Summary sum = t.begin(f);
    if (s) *s = sum;
    int out = 0;
    for (const Draws& m : meshes)
        for (int i = 0; i < m.depth; i++) out += t.draw(depth_draw(m.key, m.query, m.stencil), rule, is_scene);
    for (const Draws& m : meshes)
        for (int i = 0; i < m.colour; i++) t.draw(colour_draw(m.key, m.colour_elsewhere ? kOther : kScene), rule, is_scene);
    return out;
}

// (Link: 30 meshes, one depth draw and one colour draw each)
static std::vector<Draws> link(bool colour, bool query = false, bool stencil = false) {
    std::vector<Draws> v;
    for (uint64_t i = 0; i < 30; i++) v.push_back(Draws(0x7fb0000000ull + i * 0x1100, 1, colour ? 1 : 0, query, false, stencil));
    return v;
}

int main() {
    CHECK(kHiddenAfter == 3);
    {   // (Link visible: nothing left out; the camera in him: drawn for kHiddenAfter frames, then all 30 left out a frame;
        //  out again: the frame his colour comes back still has them left out, and is counted; the next ones none)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 100;
        for (int i = 0; i < 20; i++) CHECK(frame(*t, f++, link(true)) == 0);
        for (uint32_t i = 0; i < kHiddenAfter; i++) CHECK(frame(*t, f++, link(false)) == 0);
        for (int i = 0; i < 10; i++) CHECK(frame(*t, f++, link(false)) == 30);
        Summary s;
        CHECK(frame(*t, f++, link(true), true, &s) == 30);   // (decided before the colour came)
        CHECK(s.frame == f - 2 && s.meshes == 30 && s.bare == 30 && s.coloured == 0 && s.hidden == 30 && s.inherited == 30);
        CHECK(s.f && s.f->left_out == 30 && s.f->depth_draws == 30 && s.f->back == 0 && s.f->stencil == 0);
        CHECK(s.one && s.one->key >= 0x7fb0000000ull && s.f->scene_colour == 0);
        CHECK(frame(*t, f++, link(true), true, &s) == 0);
        CHECK(s.coloured == 30 && s.hidden == 0 && s.inherited == 30 && s.f->back == 30 && s.f->scene_colour == 30);
        for (int i = 0; i < 10; i++) CHECK(frame(*t, f++, link(true), true, &s) == 0);
        CHECK(s.f->back == 0 && s.f->left_out == 0);
        // (and in again: the same; the stencil test of the draws left out is counted)
        for (uint32_t i = 0; i < kHiddenAfter; i++) CHECK(frame(*t, f++, link(false, false, true)) == 0);
        CHECK(frame(*t, f++, link(false, false, true)) == 30);
        CHECK(frame(*t, f++, link(false, false, true), true, &s) == 30);
        CHECK(s.f->stencil == 30);
    }
    {   // (the scene's depth target is asked about once a frame, at its first skinned depth draw)
        auto t = std::make_unique<Tracker>();
        scene_asks = 0;
        for (uint64_t f = 1; f <= 5; f++) frame(*t, f, link(true));
        CHECK(scene_asks == 5);
    }
    {   // (a mesh that never had colour: never left out)
        auto t = std::make_unique<Tracker>();
        for (uint64_t f = 1; f < 40; f++) CHECK(frame(*t, f, {Draws(0x5000, 2, 0)}) == 0);
    }
    {   // (the game's culling: an occluded mesh's depth draws carry its occlusion query and are drawn, however long it is
        //  hidden; the summary counts them)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 1;
        for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
        Summary s;
        for (int i = 0; i < 20; i++) CHECK(frame(*t, f++, link(false, true), true, &s) == 0);
        CHECK(s.f && s.f->kept_query == 30 && s.f->left_out == 0 && s.hidden == 30);
    }
    {   // (meshes that blink, their depth drawn every frame: never left out, so the EQUAL colour test still finds it)
        for (int period : {1, 2}) {
            auto t = std::make_unique<Tracker>();
            uint64_t f = 1;
            for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
            for (int i = 0; i < 60; i++) CHECK(frame(*t, f++, link((i / period) % 2 != 0)) == 0);
        }
        // (a colour lost for one frame or two: nothing left out either)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 1;
        for (int gap = 1; gap < (int)kHiddenAfter; gap++) {
            for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
            for (int i = 0; i < gap; i++) CHECK(frame(*t, f++, link(false)) == 0);
            CHECK(frame(*t, f++, link(true)) == 0);
        }
    }
    {   // (the limit: a mesh that blinks with kHiddenAfter frames off loses its first frame back each time; the log counts it)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 1;
        for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
        int backs = 0;
        for (int cycle = 0; cycle < 5; cycle++) {
            for (uint32_t i = 0; i < kHiddenAfter; i++) frame(*t, f++, link(false));
            CHECK(frame(*t, f++, link(true)) == 30);
            Summary s;
            frame(*t, f, link(false), true, &s);   // (the next frame closes the one with the colour back)
            backs += s.f->back;
            f++;
            frame(*t, f++, link(true));   // (and one frame on, so each cycle starts from colour)
        }
        CHECK(backs == 5 * 30);
    }
    {   // (colour into another target is colour, and so is colour from a draw that is no skinned one (another program,
        //  another target size): a mesh shown there is not hidden)
        for (int how = 0; how < 2; how++) {
            auto t = std::make_unique<Tracker>();
            for (uint64_t f = 1; f < 30; f++) {
                t->begin(f);
                int out = t->draw(depth_draw(0x6000), true, is_scene);
                if (how == 0) t->draw(colour_draw(0x6000, kOther), true, is_scene);
                else t->draw(colour_draw(0x6000, 0, false), true, is_scene);
                CHECK(out == 0);
            }
        }
        // (but a draw that is no skinned one, from a buffer nobody follows, counts nothing)
        auto t = std::make_unique<Tracker>();
        t->begin(1);
        t->draw(colour_draw(0x6100, kScene, false), true, is_scene);
        CHECK(!t->active() && !t->followed(0x6100));
    }
    {   // (depth into another target than the scene's (another view of the scene's size, a shadow): never counted, never
        //  left out, even from a hidden mesh)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 1;
        for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
        for (int i = 0; i < 10; i++) {
            const int out = frame(*t, f, link(false));
            CHECK(!t->draw(depth_draw(0x7fb0000000ull, false, false, kOther), true, is_scene));
            CHECK(!t->draw(depth_draw(0x7fb0000000ull, false, false, 0), true, is_scene));
            CHECK(out == (i < (int)kHiddenAfter ? 0 : 30));
            f++;
        }
        // (a draw with no vertex from stream 0 (key 0): nothing)
        Draw none = depth_draw(0);
        CHECK(!t->draw(none, true, is_scene));
        none = colour_draw(0);
        t->draw(none, true, is_scene);
        CHECK(!Tracker::find(t->now, 0));
    }
    {   // (a frame skipped: nothing is known of the frame before, so a hidden mesh is drawn, and stays drawn until it
        //  has colour again; the same for a mesh with no draw at all in a frame)
        for (int missing = 0; missing < 2; missing++) {
            auto t = std::make_unique<Tracker>();
            uint64_t f = 1;
            for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true)) == 0);
            for (uint32_t i = 0; i < kHiddenAfter; i++) CHECK(frame(*t, f++, link(false)) == 0);
            if (missing) CHECK(frame(*t, f++, {Draws(0x9000, 1, 1)}) == 0);   // (another mesh's frame: Link's not followed)
            else f++;                                                          // (no draw at all in that frame)
            for (int i = 0; i < 10; i++) CHECK(frame(*t, f++, link(false)) == 0);
            for (int i = 0; i < 2; i++) CHECK(frame(*t, f++, link(true)) == 0);
            for (uint32_t i = 0; i < kHiddenAfter; i++) CHECK(frame(*t, f++, link(false)) == 0);
            CHECK(frame(*t, f++, link(false)) == 30);
        }
    }
    {   // (a full table: kSlots * 3 / 4 meshes followed, the next ones not, and never left out; the first ones still are)
        auto t = std::make_unique<Tracker>();
        std::vector<Draws> all;
        for (uint64_t i = 0; i < kSlots * 3 / 4; i++) all.push_back(Draws(0x100000 + i * 0x40, 1, 1));
        all.push_back(Draws(0xdead000, 1, 1));
        uint64_t f = 1;
        Summary s;
        for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, all) == 0);
        all.back().colour = 0;
        all.front().colour = 0;
        int out = 0;
        for (int i = 0; i < 10; i++) out += frame(*t, f++, all, true, &s);
        CHECK(out == 10 - (int)kHiddenAfter);   // (the first mesh, from its kHiddenAfter-th frame without colour)
        CHECK(s.f && s.f->over == 1 && s.f->meshes == kSlots * 3 / 4);
        CHECK(!Tracker::find(t->now, 0xdead000) && Tracker::find(t->now, 0x100000));
    }
    {   // (the rule off: everything counted, nothing left out)
        auto t = std::make_unique<Tracker>();
        uint64_t f = 1;
        Summary s;
        for (int i = 0; i < 5; i++) CHECK(frame(*t, f++, link(true), false) == 0);
        for (int i = 0; i < 10; i++) CHECK(frame(*t, f++, link(false), false, &s) == 0);
        CHECK(s.hidden == 30 && s.f && s.f->left_out == 0 && s.f->depth_draws == 30);
    }
    {   // (begin: the same frame again is no new frame; what is followed and when)
        auto t = std::make_unique<Tracker>();
        CHECK(!t->active() && t->begin(7).frame == ~0ull && t->begin(7).frame == ~0ull);
        t->draw(colour_draw(0x4000), true, is_scene);
        CHECK(t->active() && t->followed(0x4000) && !t->followed(0x4040) && !t->followed(0));
        CHECK(t->begin(8).frame == 7 && t->followed(0x4000));   // (in the frame before)
        CHECK(t->begin(9).frame == 8 && !t->followed(0x4000) && !t->active());
    }
    std::printf("PASS: the hidden-mesh rule over the game's frames\n");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    if not (VK / "hidden_mesh.h").exists():
        raise SystemExit("FAIL: no hidden-mesh rule (gpu/vk/hidden_mesh.h): every depth draw of a skinned mesh the game no "
                         "longer shows is drawn, so with the camera inside Link a temporal upscaler follows his invisible body "
                         "(#1370)")
    draw_cpp = (VK / "vk_draw.cpp").read_text(encoding="utf-8")
    temporal = (VK / "vk_temporal.cpp").read_text(encoding="utf-8")

    # (draw() asks the rule right after skin_check, leaves the draw out at once, and only then gives a skinned draw to actor
    # motion and builds its descriptors: a draw left out is neither drawn again for its motion nor given a descriptor set)
    draw = block(draw_cpp, "void draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d) {")
    order = ["skin_check(s, p, d, at, skinned);", "if (hidden_depth_draw(p, s, d, at, skinned.depth != nullptr)) SKIP_HERE();",
             "if (skinned.depth) temporal_note_skinned_draw(skinned);", "build_descriptors(s, p);"]
    at = [draw.find(x) for x in order]
    if -1 in at or at != sorted(at):
        raise SystemExit("vk_draw.cpp draw(): not in this order: " + " / ".join(order))
    if "temporal_note_skinned_draw(" in block(draw_cpp, "static void skin_check("):
        raise SystemExit("vk_draw.cpp skin_check(): it gives the draw to actor motion itself, before the rule could leave it out")
    # (what the rule is told of a draw: the mesh only where the draw reads stream 0 and is no mesh-shader draw, its depth
    # target, the query; and one table for every drawing thread, not one each)
    rule = block(draw_cpp, "static bool hidden_depth_draw(")
    for need in ("x.key = !d.mesh && reads_stream0 ? s.vbuf[0].address : 0;", "x.target = (uint64_t)(uintptr_t)at.depth;",
                 "x.skinned = skinned;", "x.colour = target_write_mask(p, s, i) != 0;", "x.depth_write = at.depth && s.ds.dw;",
                 "x.query = d.occ_pool != VK_NULL_HANDLE;", "return h.t.draw(x, (mode & 1) != 0,",
                 "temporal_scene_depth_is(", "temporal_hidden_mesh_mode()"):
        if need not in rule:
            raise SystemExit("vk_draw.cpp hidden_depth_draw(): lost " + need)
    if "TLS_OBJECT(hidden_meshes" in draw_cpp or "thread_local" in rule:
        raise SystemExit("vk_draw.cpp: the hidden-mesh table is one a thread: the game submits from several threads, and a "
                         "mesh's depth and colour from two of them would make it look hidden")

    # (its switches: the rule on unless RX_VK_DLSS_FADED=0, the detailed log only with RX_VK_DLSS_FADED_LOG=1, and nothing
    # at all with the parallel replay, whose workers draw at once)
    mode = block(temporal, "int temporal_hidden_mesh_mode() {")
    for need in ('env_switch("RX_VK_DLSS_FADED") != 0', 'env_switch("RX_VK_DLSS_FADED_LOG") == 1',
                 "const bool parallel = replay_par_threads() >= 2;", "return parallel ? 0 :"):
        if need not in mode:
            raise SystemExit("vk_temporal.cpp temporal_hidden_mesh_mode(): lost " + need)
    # (the parallel replay as the renderer runs it, the player's switch or the game profile's (Mario Kart World's 4): not the
    # switch alone, with which a profile's workers would draw from the one unlocked table at once)
    if 'env_switch("RX_VK_PARALLEL")' in mode or 'getenv("RX_VK_PARALLEL")' in mode:
        raise SystemExit("vk_temporal.cpp temporal_hidden_mesh_mode(): asks RX_VK_PARALLEL itself, not par_threads(), and so "
                         "misses the game profile's parallel replay")
    replay_src = (VK / "vk_replay.cpp").read_text(encoding="utf-8")
    if "int replay_par_threads() { return par_threads(); }" not in replay_src:
        raise SystemExit("vk_replay.cpp: no replay_par_threads(), par_threads' name outside its anonymous namespace")
    par = block(replay_src, "int par_threads() {")
    for need in ('getenv("RX_VK_PARALLEL")', 'getenv("HOOKSHOT_PROFILE_VK_PARALLEL")'):
        if need not in par:
            raise SystemExit("vk_replay.cpp par_threads(): lost " + need + ", which temporal_hidden_mesh_mode() rests on")

    with tempfile.TemporaryDirectory(prefix="hidden-mesh-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(FIXTURE, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(VK), str(cpp), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: hidden meshes: the rule over the game's frames, what draw() tells it, where it asks it, and its switches")


if __name__ == "__main__":
    main()
