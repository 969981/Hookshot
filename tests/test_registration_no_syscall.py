"""A texture or sampler registration reaches the renderer as the host's own copy, with no system call (#102's regression).

#102 made host/rx_backend.c hand the renderer copies of the game's texture objects (their layouts made again:
nvn_tex_sanitize). The copies are on the host's stack, outside guest memory, so the renderer's nvn_register_texture checked
each with descriptor_ok's VirtualQuery (gpu/vk/vk_bank.cpp), which on Linux opens and reads /proc/self/maps
(host/posix/win32_posix.c) and on Windows is a system call: 38% of Ocarina's main thread in play, half its frame rate.

Now the host checks the game's object once (whole, in guest memory) and hands its copy through the entries that take a
copy (nvn_register_texture_copy, nvn_register_sampler_copy: MKWVK_API_VERSION 24). Checks:

- the host (rx_backend.c's in_guest, safe_tex, rx_nvn_register_texture and _sampler and forward, cut from the source and
  compiled with nvn_layout.c): a registration from guest memory reaches the copy entry with a copy (not the game's
  pointer), never the pointer entries; one from outside guest memory is left out (a view: only the view); one kept from
  before the renderer started (issue 1117's case) is forwarded through the copy entries too;
- the renderer (vk_replay.cpp's register_texture_copy_queued and register_sampler_copy_queued, compiled with stubs whose
  descriptor_ok and VirtualQuery count their calls and refuse): with the replay thread and without it, each copy is
  registered or queued, and neither is called;
- the renderer's table fills both copy entries, and rx_backend.c calls no pointer entry for a registration.

python -I -B tests/test_registration_no_syscall.py [--cc gcc] [--cxx g++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import CutError, block, between   # noqa: E402

RUN_ALL_ARGS = ["--cc", "gcc", "--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HOST = r"""
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vk_api.h"
typedef volatile long LONG;
static uint8_t guest[1 << 16];
uint8_t *hos_guest_base(void) { return guest; }
static void *hos_guest_range(uint64_t a, uint64_t n) { return a < sizeof guest && n <= sizeof guest - a ? guest + a : NULL; }
static void hos_log(const char *f, ...) { (void)f; }
@EARLY@
static early_call kept[8];
static int n_kept, started = 1;
static int keep_early(const early_call *c) { if (started) return 0; kept[n_kept++] = *c; return 1; }
static int pointer_calls, tex_copies, samp_copies, copy_in_guest, with_view;
static uint32_t width_seen;
static int inside(const void *p) { return (const uint8_t *)p >= guest && (const uint8_t *)p < guest + sizeof guest; }
static void ptr_tex(int id, const nvn_tex *t, const nvn_view *v) { (void)id, (void)t, (void)v; pointer_calls++; }
static void ptr_samp(int id, const nvn_samp_par *p) { (void)id, (void)p; pointer_calls++; }
static void copy_tex(int id, const nvn_tex *t, const nvn_view *v) {
    (void)id; tex_copies++; copy_in_guest += inside(t) || (v && inside(v)); with_view += v != NULL; width_seen = t->d.width;
}
static void copy_samp(int id, const nvn_samp_par *p) { (void)id; samp_copies++; copy_in_guest += inside(p); }
static MkwVkRenderer table;
static const MkwVkRenderer *vk = &table;
static void wgr_nvn_register_texture(int id, const nvn_tex *t, const nvn_view *v) { (void)id, (void)t, (void)v; abort(); }
static void wgr_nvn_register_sampler(int id, const nvn_samp_par *p) { (void)id, (void)p; abort(); }
static void wgr_nvn_set_texture_pool_size(int n) { (void)n; abort(); }
static void wgr_nvn_texture_released(const nvn_tex *t) { (void)t; abort(); }
static void wgr_nvn_write_texels(const nvn_tex *t, unsigned l, unsigned l0, const NVNcopyRegion *r, const uint8_t *s,
                                 uint64_t a, uint64_t b) { (void)t, (void)l, (void)l0, (void)r, (void)s, (void)a, (void)b; abort(); }
@FORWARD@
@IN_GUEST@
@SAFE_TEX@
@REG_TEX@
@REG_SAMP@
static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: host: %s\n", what); bad = 1; } }
int main(void) {
    table.nvn_register_texture = ptr_tex, table.nvn_register_sampler = ptr_samp;
    table.nvn_register_texture_copy = copy_tex, table.nvn_register_sampler_copy = copy_samp;
    nvn_tex *t = (nvn_tex *)(guest + 0x100);
    nvn_view *v = (nvn_view *)(guest + 0x800);
    nvn_samp_par *p = (nvn_samp_par *)(guest + 0xc00);
    t->d.target = 1, t->d.format = 0x25, t->d.width = 256, t->d.height = 128, t->d.depth = 1, t->d.levels = 1;
    t->L.size = ~0ull;   /* (the game's layout: anything) */
    rx_nvn_register_texture(3, t, v);
    rx_nvn_register_sampler(4, p);
    check(tex_copies == 1 && samp_copies == 1 && width_seen == 256 && with_view == 1, "a registration from guest memory reaches the copy entries");
    check(copy_in_guest == 0, "what the renderer gets is the host's copy, not the game's object");
    nvn_tex outside = *t;
    nvn_view outside_view = *v;
    rx_nvn_register_texture(5, &outside, v);
    rx_nvn_register_sampler(6, (const nvn_samp_par *)&outside_view);
    check(tex_copies == 1 && samp_copies == 1, "a texture or a sampler outside guest memory is left out");
    rx_nvn_register_texture(7, t, &outside_view);
    check(tex_copies == 2 && with_view == 1, "a view outside guest memory: the texture registered without it");
    started = 0;
    rx_nvn_register_texture(8, t, v);
    rx_nvn_register_sampler(9, p);
    check(n_kept == 2 && tex_copies == 2 && samp_copies == 1, "before the renderer starts, both are kept");
    started = 1;
    for (int i = 0; i < n_kept; i++) forward(&kept[i]);
    check(tex_copies == 3 && samp_copies == 2 && with_view == 2 && copy_in_guest == 0, "the kept ones go through the copy entries (issue 1117)");
    check(pointer_calls == 0, "no registration goes through nvn_register_texture or nvn_register_sampler");
    if (!bad) printf("host: ok\n");
    return bad;
}
"""

RENDERER = r"""
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <utility>
#define _Static_assert static_assert   // (rx_nvn.h is C)
#include "rx_nvn.h"
enum { JOB_OTHER, JOB_REGISTER_TEXTURE, JOB_REGISTER_SAMPLER };
struct Job { int kind; int id; nvn_tex tex; nvn_view view; bool has_view; nvn_samp_par samp; };
static bool async, at_once;
static int direct, queued, checked, queried;
static bool async_on() { return async; }
static bool register_at_once() { return at_once; }
static void register_texture_copy(int, const nvn_tex&, const nvn_view*) { direct++; }
static void register_sampler_copy(int, const nvn_samp_par&) { direct++; }
static void enqueue(Job&& j) { queued += j.kind == JOB_REGISTER_TEXTURE || j.kind == JOB_REGISTER_SAMPLER; }
struct MEMORY_BASIC_INFORMATION {};
[[maybe_unused]] static size_t VirtualQuery(const void*, MEMORY_BASIC_INFORMATION*, size_t) { queried++; return 0; }
[[maybe_unused]] static const void* descriptor_ok(const void*, size_t) { checked++; MEMORY_BASIC_INFORMATION m; VirtualQuery(nullptr, &m, 0); return nullptr; }
@COPY_TEX@
@COPY_SAMP@
int main() {
    int bad = 0;
    for (int mode = 0; mode < 3; mode++) {
        async = mode != 0, at_once = mode == 2, direct = queued = 0;
        nvn_tex t{};
        nvn_view v{};
        nvn_samp_par p{};
        register_texture_copy_queued(1, &t, &v);
        register_sampler_copy_queued(2, &p);
        register_texture_copy_queued(-1, &t, &v);   // (out of range: nothing)
        const int want_direct = async && !at_once ? 0 : 2, want_queued = 2 - want_direct;
        if (direct != want_direct || queued != want_queued) {
            std::printf("FAIL: renderer: mode %d: %d registered at once, %d queued (want %d, %d)\n", mode, direct, queued, want_direct, want_queued);
            bad = 1;
        }
    }
    if (checked || queried) {
        std::printf("FAIL: renderer: a host copy was checked %d times, %d VirtualQuery calls\n", checked, queried);
        bad = 1;
    }
    if (!bad) std::printf("renderer: ok\n");
    return bad;
}
"""


def build_and_run(cmd, exe, what, bad):
    built = subprocess.run(cmd, capture_output=True, text=True)
    if built.returncode:
        bad.append("%s did not build: %s" % (what, built.stderr.strip()[-600:]))
        return
    out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    print(out.stdout, end="")
    if out.returncode:
        bad.append(what + " (above)")


def compiled(backend, replay, args, bad):
    host = (HOST.replace("@EARLY@", "typedef struct {\n    int kind;" + between(backend, "typedef struct {\n    int kind;", "} early_call;")[len("typedef struct {\n    int kind;"):] + "} early_call;")
            .replace("@FORWARD@", block(backend, "static void forward(const early_call *c) {"))
            .replace("@IN_GUEST@", block(backend, "static int in_guest(const void *p, size_t n) {"))
            .replace("@SAFE_TEX@", block(backend, "static const nvn_tex *safe_tex(const nvn_tex *in, nvn_tex *copy, const char *what) {"))
            .replace("@REG_TEX@", block(backend, "void rx_nvn_register_texture(int id, const nvn_tex *t_in, const nvn_view *v_in) {"))
            .replace("@REG_SAMP@", block(backend, "void rx_nvn_register_sampler(int id, const nvn_samp_par *p_in) {")))
    renderer = (RENDERER.replace("@COPY_TEX@", block(replay, "void register_texture_copy_queued(int id, const nvn_tex* t, const nvn_view* v) {"))
                .replace("@COPY_SAMP@", block(replay, "void register_sampler_copy_queued(int id, const nvn_samp_par* p) {")))
    with tempfile.TemporaryDirectory(prefix="registration-no-syscall-") as tmp:
        folder = Path(tmp)
        (folder / "host.c").write_text(host, encoding="utf-8")
        (folder / "renderer.cpp").write_text(renderer, encoding="utf-8")
        build_and_run([args.cc, "-std=c11", "-O1", "-Wall", "-Wno-unused-function", "-I" + str(ROOT / "gpu" / "vk"),
                       "-I" + str(ROOT / "gpu" / "render"), str(folder / "host.c"), str(ROOT / "host" / "nvn_layout.c"),
                       "-o", str(folder / "host")], folder / "host", "the host's registrations", bad)
        build_and_run([args.cxx, "-std=c++17", "-O1", "-Wall", "-I" + str(ROOT / "gpu" / "render"), str(folder / "renderer.cpp"),
                       "-o", str(folder / "renderer")], folder / "renderer", "the renderer's copy entries", bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or shutil.which("clang"))
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++"))
    args = parser.parse_args()
    backend = (ROOT / "host/rx_backend.c").read_text(encoding="utf-8")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    bad = []
    for entry in ("t.nvn_register_texture_copy = register_texture_copy_queued;",
                  "t.nvn_register_sampler_copy = register_sampler_copy_queued;"):
        if entry not in replay:
            bad.append("the renderer's table does not fill " + entry.split(" =")[0][2:])
    for pointer in ("vk->nvn_register_texture(", "vk->nvn_register_sampler("):
        if pointer in backend:
            bad.append("rx_backend.c still registers through %s...): a VirtualQuery each" % pointer[4:-1])
    try:
        compiled(backend, replay, args, bad)
    except CutError as e:   # (main before the fix: no in_guest, no copy entries)
        bad.append(str(e))
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: registrations reach the renderer as the host's copies, with no VirtualQuery")


if __name__ == "__main__":
    main()
