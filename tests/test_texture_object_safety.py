"""A texture object from the game reaches the renderer only as a checked copy (host/nvn_layout.c nvn_tex_sanitize).

The object is the game's bytes. Its layout L was copied into recordings as it stood, and the renderer sized its staging,
uploads and page walks by it: a layer size that wrapped became a heap write past a staging arena, an allocation of
terabytes or a loop of 2^43 (the 2026-11-14 audit's V1, V4, V5). Now the layout is computed again from the description,
and a description past any texture is refused. Checks:

- nvn_tex_sanitize (compiled from nvn_layout.c): an ordinary texture keeps its description and gets the layout
  nvn_tex_layout gives, whatever the object's own L said; a side past 65,536, more than 17 levels, a sample count NVN
  has not, an unknown target and storage past 16 GiB are refused; at every bound the layout does not wrap (the size is
  checked against a 128-bit sum of the same levels);
- every route of a texture object into the renderer takes such a copy: tex_copy (recordings), and in host/rx_backend.c
  registration, creation, release, ReadTexels, WriteTexels and present;
- texels() reads the texture, view and region once, into copies (the audit's V2: another thread of the game could change
  them between the checks and the renderer's use).

python -I -B tests/test_texture_object_safety.py [--cc gcc]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "rx_nvn.h"
static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
static nvn_tex tex(int target, int format, uint32_t w, uint32_t h, uint32_t d, uint32_t levels, int samples) {
    nvn_tex t;
    memset(&t, 0, sizeof t);
    t.d.target = target, t.d.format = format, t.d.width = w, t.d.height = h, t.d.depth = d, t.d.levels = levels;
    t.d.samples = samples;
    t.serial = 7, t.gpu = 0x10000000;
    return t;
}
/* the level sizes nvn_tex_layout adds, summed again in 128 bits: what the 64-bit size must equal when it did not wrap */
static unsigned __int128 wide_size(const nvn_tex *t) {
    nvn_texlayout L;
    nvn_tex_layout(&t->d, &L);
    unsigned __int128 s = 0;
    const unsigned levels = t->d.levels ? t->d.levels : 1;
    for (unsigned l = 0; l < levels; l++) {
        const uint64_t a = nvn_tex_level_offset(&t->d, l), b = nvn_tex_level_offset(&t->d, l + 1);
        s += (unsigned __int128)(b - a);
        if (b < a) return ~(unsigned __int128)0;   /* (a level's offset that went down: wrapped) */
    }
    return s;
}
int main(void) {
    const int RGBA8 = 0x25, RGBA32F = 0x2e, R8 = 0x01;
    {   /* an ordinary texture: its description kept, its layout made again whatever the object said */
        nvn_tex in = tex(1, RGBA8, 1920, 1080, 1, 11, 0), out;
        nvn_texlayout want;
        nvn_tex_layout(&in.d, &want);
        in.L.size = 0xffffffffffffff00ull, in.L.layer_size = 0x10, in.L.layers = 9999;   /* (the game's bytes: anything) */
        check(nvn_tex_sanitize(&in, &out) == 1, "a 1920x1080 RGBA8 texture is taken");
        check(!memcmp(&out.d, &in.d, sizeof in.d) && out.gpu == in.gpu && out.serial == in.serial, "its description kept");
        check(out.L.size == want.size && out.L.layer_size == want.layer_size && out.L.layers == want.layers,
              "its layout is nvn_tex_layout's, not the object's");
    }
    {
        nvn_tex out, a = tex(1, RGBA8, 65537, 4, 1, 1, 0), b = tex(1, RGBA8, 4, 4, 1, 18, 0), c = tex(1, RGBA8, 4, 4, 1, 1, 3),
                     d = tex(11, RGBA8, 4, 4, 1, 1, 0), e = tex(2, RGBA32F, 4096, 4096, 512, 1, 0),
                     f = tex(1, RGBA8, 0x80000000u, 0x80000000u, 1, 1, 0), g = tex(4, RGBA8, 64, 64, 65537, 1, 0);
        check(!nvn_tex_sanitize(&a, &out), "a side of 65,537 is refused");
        check(!nvn_tex_sanitize(&b, &out), "18 levels are refused");
        check(!nvn_tex_sanitize(&c, &out), "3 samples are refused");
        check(!nvn_tex_sanitize(&d, &out), "target 11 is refused");
        check(!nvn_tex_sanitize(&e, &out), "a 4096x4096x512 RGBA32F volume (32 GiB) is refused");
        check(!nvn_tex_sanitize(&f, &out), "a 2^31 x 2^31 texture (its size wrapped before) is refused");
        check(!nvn_tex_sanitize(&g, &out), "65,537 layers are refused");
    }
    {   /* at the bounds, the 64-bit layout equals its 128-bit sum: nothing taken wrapped */
        const nvn_tex edge[] = {
            tex(2, RGBA32F, 65536, 65536, 65536, 17, 0),   /* 3D: refused by size, the sum checked all the same */
            tex(4, RGBA32F, 65536, 65536, 65536, 17, 0),   /* 2D array */
            tex(1, RGBA32F, 65536, 65536, 1, 17, 8),
            tex(1, R8, 65536, 65536, 1, 1, 0),
        };
        for (unsigned i = 0; i < sizeof edge / sizeof *edge; i++) {
            nvn_texlayout L;
            nvn_tex_layout(&edge[i].d, &L);
            const unsigned __int128 w = wide_size(&edge[i]);
            const unsigned __int128 per = edge[i].d.target == 4 ? (unsigned __int128)L.layer_size : w;
            check(w != ~(unsigned __int128)0 && (unsigned __int128)L.layer_size >= w && per == L.layer_size,
                  "at the bounds a layer's size is its levels' sum, not wrapped");
            nvn_tex out;
            const int taken = nvn_tex_sanitize(&edge[i], &out);
            check(!taken || out.L.size <= NVN_TEX_MAX_BYTES, "what is taken fits 16 GiB");
        }
        nvn_tex big = tex(1, R8, 65536, 65536, 1, 1, 0), out;
        check(nvn_tex_sanitize(&big, &out) == 1, "a 65536x65536 R8 texture (4 GiB) is taken");
    }
    if (!bad) puts("ok");
    return bad;
}
"""


def routes(src_native, src_backend):
    """what the sources must do on each route of a texture object into the renderer, and the problems found"""
    bad = []
    tc = block(src_native, "static void tex_copy(const nvn_tex **dst, uint64_t t) {")
    if "nvn_tex_sanitize(" not in tc or "*dst = NULL" not in tc.split("nvn_tex_sanitize(")[1]:
        bad.append("tex_copy keeps the game's object as it stands (no nvn_tex_sanitize, or a refusal not left out)")
    if "pins[s] = (nvn_tex *)malloc(sizeof(nvn_tex));\n        *pins[s] = *o;" not in tc or "const nvn_tex *o = &safe;" not in tc:
        bad.append("tex_copy's pinned copy is not the checked one")
    tx = block(src_native, "static void texels(Ctx *c, int write, int strided) {")
    for want in ("nvn_tex tc = *tg;", "const NVNcopyRegion rc = *rg;", "(vc = *vg, &vc)"):
        if want not in tx:
            bad.append("texels() reads the game's %s more than once (no %r)" % (want.split()[1], want))
    if "safe_tex(" not in src_backend or "nvn_tex_sanitize(in, copy)" not in block(src_backend, "static const nvn_tex *safe_tex(const nvn_tex *in, nvn_tex *copy, const char *what) {"):
        bad.append("host/rx_backend.c has no safe_tex over nvn_tex_sanitize")
    for fn in ("void rx_nvn_register_texture(", "void rx_nvn_texture_released(", "void rx_nvn_texture_created(",
               "void rx_nvn_read_texels(", "void rx_nvn_write_texels(", "void rx_nvn_present("):
        body = block(src_backend, fn)
        if "safe_tex(" not in body:
            bad.append("%s passes the game's texture object on as it stands" % fn.split()[1].rstrip("("))
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or shutil.which("clang"))
    args = parser.parse_args()
    bad = routes((ROOT / "host/nvn_native.c").read_text(encoding="utf-8"), (ROOT / "host/rx_backend.c").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="tex-safety-") as tmp:
        folder = Path(tmp)
        (folder / "t.c").write_text(HARNESS, encoding="utf-8")
        exe = folder / "t"
        built = subprocess.run([args.cc, "-std=c11", "-O1", "-w", "-I" + str(ROOT / "gpu" / "render"), str(folder / "t.c"),
                                str(ROOT / "host" / "nvn_layout.c"), "-o", str(exe)], capture_output=True, text=True)
        if built.returncode:
            bad.append("the harness did not build (no nvn_tex_sanitize?): " + built.stderr.strip()[-400:])
        else:
            out = subprocess.run([str(exe)], capture_output=True, text=True)
            print(out.stdout, end="")
            if out.returncode or out.stdout.strip() != "ok":
                bad.append("nvn_tex_sanitize")
    for b in bad:
        print("FAIL:", b)
    if bad:
        raise SystemExit(1)
    print("PASS: texture objects reach the renderer as checked copies, their layout made from their description")


if __name__ == "__main__":
    main()
