"""The CPU's texel reads and writes give their thread's virtual core up while the Vulkan back end waits for the GPU.

Compiles host/rx_backend.c's own rx_nvn_read_texels, rx_nvn_write_texels and texels_keep_core with stand-ins for the back
end and the virtual cores, and checks that the back end is called with the core given up (and the core taken again after),
for the Vulkan back end and the fallback, and that HOS_TEXELS_KEEP_CORE=1 keeps it as before. Before the change the back end
ran with the core held: the stand-in sees it held and the default cases fail.

python -I -B tests/test_texels_vcore.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include "hookshot_env.h"   /* (env_on, as host/rx_backend.c reads the switch) */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
struct nvn_desc { int format; };
typedef struct { struct nvn_desc d; } nvn_tex;
typedef struct { int xoffset, yoffset, zoffset, width, height, depth; } NVNcopyRegion;
typedef struct { int kind, done; nvn_tex t; unsigned level, layer0; NVNcopyRegion r; size_t row, img; uint8_t *data; } early_call;
static int started = 1;
static int keep_early(early_call *c) { (void)c; return 0; }
static int nvn_fmt_bpb(int f, int *bw, int *bh) { (void)f; *bw = *bh = 1; return 4; }
static const nvn_tex *safe_tex(const nvn_tex *t, nvn_tex *copy, const char *what) { (void)copy; (void)what; return t; }   // (main's #102 check: a usable texture as is)
static int held = 1, during = -1, calls = 0;
int hos_vcore_away(void) { if (!held) return 0; held = 0; return 1; }
void hos_vcore_back(void) { held = 1; }
static void seen(void) { during = held; calls++; }
static void vk_read(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, uint8_t *, size_t, size_t) { seen(); }
static void vk_write(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, const uint8_t *, size_t, size_t) { seen(); }
static void wgr_nvn_read_texels(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, uint8_t *, size_t, size_t) { seen(); }
static void wgr_nvn_write_texels(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, const uint8_t *, size_t, size_t) { seen(); }
struct Api {
    void (*nvn_read_texels)(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, uint8_t *, size_t, size_t);
    void (*nvn_write_texels)(const nvn_tex *, unsigned, unsigned, const NVNcopyRegion *, const uint8_t *, size_t, size_t);
};
static struct Api api = {vk_read, vk_write};
static struct Api *vk = &api;
#include "actual_texels.inc"
int main(int argc, char **argv) {
    const char *backend = argc > 1 ? argv[1] : "vulkan";
    const int keep = argc > 2 && !strcmp(argv[2], "keep");
    if (!strcmp(backend, "fallback")) vk = NULL;
    nvn_tex t = {{1}};
    NVNcopyRegion r = {0, 0, 0, 4, 4, 1};
    uint8_t buf[64] = {0};
    int bad = 0;
    for (int write = 0; write < 2; write++) {
        during = -1, calls = 0, held = 1;
        if (write) rx_nvn_write_texels(&t, 0, 0, &r, buf, 16, 64);
        else rx_nvn_read_texels(&t, 0, 0, &r, buf, 16, 64);
        const int want = keep ? 1 : 0;
        if (calls != 1 || during != want || held != 1) {
            printf("FAIL %s %s %s: back end called %d times, core %s during the call (want %s), %s after\n", backend,
                   keep ? "keep" : "default", write ? "write" : "read", calls, during ? "held" : "given up",
                   want ? "held" : "given up", held ? "held" : "NOT held");
            bad = 1;
        }
    }
    if (!bad) printf("ok %s %s\n", backend, keep ? "keep" : "default");
    return bad;
}
"""


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
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    src = (ROOT / "host/rx_backend.c").read_text(encoding="utf-8")
    marks = ("static int texels_keep_core(", "void rx_nvn_read_texels(", "void rx_nvn_write_texels(")
    parts = [block(src, m) for m in marks if m in src]   # (without the helper: the wrappers alone, as before the change)
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="texels-vcore-") as tmp:
        folder = Path(tmp)
        (folder / "actual_texels.inc").write_text("\n".join(parts) + "\n", encoding="utf-8")
        (folder / "harness.cpp").write_text(HARNESS, encoding="utf-8")
        exe = folder / "harness.exe"
        r = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wno-unused-function", "-D_CRT_SECURE_NO_WARNINGS", "-I" + str(folder),
                            "-I" + str(ROOT / "gpu/vk/compat"), str(folder / "harness.cpp"),
                            "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout + r.stderr)
            raise SystemExit("FAIL: the harness did not compile")
        failed = 0
        for backend in ("vulkan", "fallback"):
            for keep in (False, True):
                env = os.environ.copy()
                env.pop("HOS_TEXELS_KEEP_CORE", None)
                if keep:
                    env["HOS_TEXELS_KEEP_CORE"] = "1"
                r = subprocess.run([str(exe), backend] + (["keep"] if keep else []), capture_output=True, text=True, env=env,
                                   timeout=20)
                print(r.stdout, end="")
                failed |= r.returncode != 0
        if failed:
            raise SystemExit("FAIL")
        print("ok: the texel calls give the virtual core up for the back end's wait (HOS_TEXELS_KEEP_CORE=1 keeps it)")


if __name__ == "__main__":
    main()
