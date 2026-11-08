"""Button icons: the builder's library (nx2/button_icons.py) and the font made from it at the start (host/button_icons.c).

Game-free: a stand-in External font (a box for each of U+E040 to U+E06A) and a stand-in lettering font are made here
with fontTools (the wheel in third_party/fonttools). The builder writes hookshot_icons.bin from them; host/button_icons.c
is compiled against stand-ins for the controls (an options file) and the controller family, and makes the font for
keyboard and mouse and for a PlayStation 5 controller. The test reads it back as the game would (zstd, the obfuscation,
the font) and checks:
- the Top DICT's offsets became five-byte numbers and every glyph still draws as it did (fixed_offsets);
- A bound to E shows a key cap (wider than the box), the movement keys a cluster (taller than a cap), the right stick
  the mouse; on the PS5 pad A bound to the right face button shows the circle (round, not the box); what nothing is
  bound to, and glyphs that are not buttons, stay as they were;
- the frame decodes with no dictionary, and the width table has every glyph;
- the resource size table the game gets has the font's buffer raised (the game's 14 856 bytes were too few for the font
  with other icons: the game crashed at its start), and with a mod's own table the font is left out when it does not fit.
python -I -B tests/test_button_icons.py   (Linux: host/posix's windows.h; needs a C compiler)
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))

try:
    import zstandard  # noqa: F401
    import button_icons as bi
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont
    HAVE = True
except ImportError:   # (no zstandard here: the builder's Python has it)
    HAVE = False

KEY = 0xA6018502
RSTB_PATH = "System/Resource/ResourceSizeTable.Product.100.Nin_Ounce_NVN2.rsizetable.zs"


def rstb(font_size=14856):
    """a resource size table: the External font's entry and another"""
    import zlib
    entries = sorted([(zlib.crc32(bi.FONT_KEY.encode()), font_size), (zlib.crc32(b"Other/file.bin"), 100)])
    return b"RESTBL" + struct.pack("<IIII", 1, 160, len(entries), 0) + b"".join(struct.pack("<II", h, v) for h, v in entries)


def rstb_entry(data):
    import zlib
    h = zlib.crc32(bi.FONT_KEY.encode())
    n = struct.unpack_from("<I", data, 14)[0]
    for i in range(n):
        k, v = struct.unpack_from("<II", data, 22 + i * 8)
        if k == h:
            return v
    return None


def box_font(name, glyphs):
    """a CFF font: each (name, codepoint, (x0, y0, x1, y1), width) a box"""
    fb = FontBuilder(1000, isTTF=False)
    order = [".notdef"] + [g[0] for g in glyphs]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({g[1]: g[0] for g in glyphs})
    charstrings, metrics = {}, {}
    for gname, _, box, width in [(".notdef", 0, (50, 0, 450, 700), 500)] + list(glyphs):
        pen = T2CharStringPen(width, None)
        x0, y0, x1, y1 = box
        pen.moveTo((x0, y0))
        pen.lineTo((x1, y0))
        pen.lineTo((x1, y1))
        pen.lineTo((x0, y1))
        pen.closePath()
        charstrings[gname] = pen.getCharString()
        metrics[gname] = (width, x0)
    fb.setupCFF(name, {"FullName": name}, charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=700, descent=-300)
    fb.setupNameTable({"familyName": name, "styleName": "Regular"})
    fb.setupOS2(sCapHeight=700)
    fb.setupPost()
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


STUBS = r"""
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <windows.h>
#include "vk_api.h"
void hos_log(const char *f, ...) { va_list a; va_start(a, f); vprintf(f, a); va_end(a); putchar(10); }
DWORD GetModuleFileNameA(HMODULE m, LPSTR b, DWORD n) { (void)m; return (DWORD)snprintf(b, n, "%s/hookshot", getenv("T_DIR")); }
int hos_sdl_pad_family(int w) { (void)w; return atoi(getenv("T_PAD")); }
/* A: E and the right face button (1); the movement keys WASD; nothing on B */
int hos_binding(int set, int b, int slot) {
    if (set == HOS_BIND_PAD) return b == HOS_SW_A ? 1 : -1;
    if (slot) return 0;
    switch (b) {
    case HOS_SW_A: return 'E';
    case HOS_KEY_MOVE_UP: return 'W';
    case HOS_KEY_MOVE_LEFT: return 'A';
    case HOS_KEY_MOVE_DOWN: return 'S';
    case HOS_KEY_MOVE_RIGHT: return 'D';
    default: return 0;
    }
}
/* T_MOD_RSTB=1: a mod has its own size table */
int hos_mods_romfs(const char *rel, char *out, size_t cap) {
    if (!getenv("T_MOD_RSTB") || !strstr(rel, "rsizetable")) return 0;
    snprintf(out, cap, "mod");
    return 1;
}
int hos_button_icons_romfs(const char *rel, char *out, size_t cap);
int main(void) {
    char out[1024];
    if (hos_button_icons_romfs("Font2/Scalable/Symbol.Nin_Ounce_NVN2.bfotf.zs", out, sizeof out)) return 3;   /* (another font) */
    if (!getenv("T_MOD_RSTB")) {   /* (the game reads its size table first) */
        if (!hos_button_icons_romfs("System/Resource/ResourceSizeTable.Product.100.Nin_Ounce_NVN2.rsizetable.zs", out, sizeof out))
            return 4;
        puts(out);
    }
    if (!hos_button_icons_romfs("Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs", out, sizeof out)) return 2;
    puts(out);
    return 0;
}
"""


LIVE_HARNESS = r"""
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <windows.h>
static uint8_t *mem;
static size_t mem_n;
static uint32_t key;
void hos_log(const char *f, ...) { va_list a; va_start(a, f); vfprintf(stderr, f, a); va_end(a); fputc('\n', stderr); }
int64_t hos_now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (int64_t)t.tv_sec * 1000000000 + t.tv_nsec; }
size_t hos_heap_find(const void *pat, size_t len, uint64_t *out, size_t max) {
    size_t n = 0;
    for (size_t o = 0; o + len <= mem_n && n < max; o += 4)
        if (!memcmp(mem + o, pat, len)) out[n++] = (uint64_t)(uintptr_t)(mem + o);
    return n;
}
int hos_button_icons_started(int *follow) { *follow = 0; return 0; }
uint32_t hos_button_icons_key(void) { return key; }
int hos_sdl_pad_family(int wait_ms) { (void)wait_ms; return 0; }
void hos_host_thread_register_self(const char *name) { (void)name; }
void hos_button_icons_input(int pad, int keys, int f8);
typedef struct { LPTHREAD_START_ROUTINE fn; void *arg; } Start;
static void *trampoline(void *p) { Start s = *(Start *)p; free(p); s.fn(s.arg); return NULL; }
HANDLE CreateThread(void *attr, SIZE_T stack, LPTHREAD_START_ROUTINE fn, void *arg, DWORD flags, DWORD *id) {
    (void)attr, (void)stack, (void)flags, (void)id;
    Start *s = malloc(sizeof *s);
    s->fn = fn, s->arg = arg;
    pthread_t t;
    if (pthread_create(&t, NULL, trampoline, s)) return NULL;
    pthread_detach(t);
    return (HANDLE)1;
}
int main(int argc, char **argv) {   /* in out key presses: the file as the game has it in memory, F8 pressed that often */
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END), mem_n = (size_t)ftell(f), fseek(f, 0, SEEK_SET);
    mem = aligned_alloc(4096, (mem_n + 4095) & ~(size_t)4095);
    if (fread(mem, 1, mem_n, f) != mem_n) return 2;
    fclose(f);
    key = (uint32_t)strtoul(argv[3], NULL, 0);
    for (int i = 0; i < atoi(argv[4]); i++) {
        hos_button_icons_input(0, 0, 1), hos_button_icons_input(0, 0, 0);
        usleep(300000);   /* (the first press looks for the font on a thread of its own) */
    }
    f = fopen(argv[2], "wb");
    fwrite(mem, 1, mem_n, f), fclose(f);
    return 0;
}
"""

def bounds(font, code):
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    bp = BoundsPen(gs)
    gs[cmap[code]].draw(bp)
    return bp.bounds


def drawing(font, code):
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    rec = RecordingPen()
    gs[cmap[code]].draw(rec)
    return rec.value


@unittest.skipUnless(HAVE, "needs zstandard and fontTools (the builder's Python and third_party/fonttools)")
class ButtonIconsTests(unittest.TestCase):
    def setUp(self):
        boxes = [("uni%04X" % c, c, (10, 20, 600, 580), 640) for c in bi.CODEPOINTS]   # (lsb 10: not the icons' 40)
        self.external = box_font("External", boxes + [("space", 0x20, (0, 0, 1, 1), 250)])
        letters = [(ch, ord(ch), (30, 0, 470, 700), 520) for ch in "?ABDESW"]
        self.lettering = TTFont(io.BytesIO(box_font("Lettering", letters)))
        self.data, self.glyphs = bi.build_from(self.external, KEY, self.lettering, [(RSTB_PATH, rstb())])

    def test_fixed_offsets_keep_every_glyph(self):
        fixed = bi.fixed_offsets(self.external)
        bi.check_charstrings_offset(fixed)
        a, b = TTFont(io.BytesIO(self.external)), TTFont(io.BytesIO(fixed))
        for code in bi.CODEPOINTS:
            self.assertEqual(drawing(a, code), drawing(b, code))

    def test_library(self):
        version, key, font_size, count, first, n, tables_at, tables = struct.unpack_from("<IIIIIIII", self.data, 4)
        self.assertEqual((version, key, first, n, tables), (3, KEY, 0xE040, len(bi.CODEPOINTS), 1))
        for want in (0x01000045, 0x02000057, 0x03000000, 0x10000300 | bi.EAST, 0x10000100 | bi.SOUTH, 0x20000000 | 0xE040):
            self.assertIn(want, self.glyphs)

    def run_c(self, env_extra):
        if sys.platform == "win32":
            self.skipTest("host/posix's windows.h stands in for Windows here")
        cc = shutil.which("gcc") or shutil.which("clang")
        if not cc:
            self.skipTest("no C compiler")
        with tempfile.TemporaryDirectory(prefix="hookshot-icons-") as tmp:
            tmp = Path(tmp)
            (tmp / "hookshot_icons.bin").write_bytes(self.data)
            (tmp / "stubs.c").write_text(STUBS, encoding="utf-8")
            exe = tmp / "t"
            build = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-I", str(ROOT / "host" / "posix"), "-I", str(ROOT / "host"),
                                    "-I", str(ROOT / "third_party" / "a64-kit" / "rt"), "-I", str(ROOT / "gpu" / "vk"),
                                    str(ROOT / "host" / "button_icons.c"), str(tmp / "stubs.c"), "-o", str(exe)],
                                   text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            env = dict(os.environ, T_DIR=str(tmp), **env_extra)
            run = subprocess.run([str(exe)], text=True, capture_output=True, env=env, timeout=60)
            files = [Path(line).read_bytes() for line in run.stdout.splitlines() if line.startswith(str(tmp))]
        return run, files

    def font_for(self, pad, family_env):
        run, files = self.run_c({"T_PAD": str(pad), "HOS_BUTTON_ICONS": family_env})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        table = zstandard.ZstdDecompressor().decompress(files[0], max_output_size=1 << 26)
        self.assertEqual(rstb_entry(table), bi.RSTB_FONT)                  # the font's buffer raised
        self.assertEqual(len(table), len(rstb()))
        frame = zstandard.ZstdDecompressor().decompress(files[-1], max_output_size=1 << 26)
        self.assertLessEqual(len(frame) + 288, bi.RSTB_FONT)               # (and the font fits it)
        otf, key = bi.bfotf_decode(frame)
        self.assertEqual(key, KEY)
        self.last_otf = otf
        font = TTFont(io.BytesIO(otf))
        self.assertEqual(font["hhea"].numberOfHMetrics, len(font.getGlyphOrder()))
        return font

    def test_keyboard(self):
        font = self.font_for(0, "keyboard")
        base = TTFont(io.BytesIO(self.external))
        cap = bounds(font, 0xE040)                    # A: the E key's cap
        self.assertGreater(cap[2] - cap[0], 0)
        self.assertNotEqual(drawing(font, 0xE040), drawing(base, 0xE040))
        self.assertEqual(font["hmtx"][font.getBestCmap()[0xE040]][1], 40)
        cluster = bounds(font, 0xE05E)                # the left stick: W above A S D, the line's whole height
        self.assertAlmostEqual(cluster[1], bi.CLUSTER_LO, delta=2)
        self.assertAlmostEqual(cluster[3], bi.CLUSTER_HI, delta=2)
        self.assertAlmostEqual(cluster[0], 40, delta=2)
        pair = bounds(font, 0xE054)                   # left and right: A and D side by side, a cap's height
        self.assertAlmostEqual(pair[1], bi.BOTTOM, delta=2)
        self.assertAlmostEqual(pair[3], bi.TOP, delta=2)
        mouse = bounds(font, 0xE05F)                  # the right stick: the mouse (360 wide)
        self.assertAlmostEqual(mouse[2] - mouse[0], 360, delta=2)
        self.assertEqual(drawing(font, 0xE041), drawing(base, 0xE041))   # B: nothing bound, the game's own
        self.assertEqual(drawing(font, 0xE048), drawing(base, 0xE048))   # power: not a button of the game's

    def test_mod_size_table(self):
        """a mod's own size table: the game keeps its 14 856 bytes for the font, and the larger font is left out"""
        # (the stand-in fonts are small: a game entry of 1 000 bytes, which the font does not fit)
        self.data, _ = bi.build_from(self.external, KEY, self.lettering, [(RSTB_PATH, rstb(1000))])
        run, files = self.run_c({"T_PAD": "0", "HOS_BUTTON_ICONS": "keyboard", "T_MOD_RSTB": "1"})
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("a mod's", run.stdout)
        # and a font that fits the game's own entry is still given with a mod's table
        self.data, _ = bi.build_from(self.external, KEY, self.lettering, [(RSTB_PATH, rstb(14856))])
        run, files = self.run_c({"T_PAD": "0", "HOS_BUTTON_ICONS": "keyboard", "T_MOD_RSTB": "1"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_patch_rstb(self):
        table, before = bi.patch_rstb(rstb(14856))
        self.assertEqual((before, rstb_entry(table)), (14856, bi.RSTB_FONT))
        table, before = bi.patch_rstb(rstb(600000))                       # (never lowered)
        self.assertEqual(rstb_entry(table), 600000)

    def test_live_switch(self):
        """the font holds every family's icons; writing hkic's glyph numbers into the cmap, as host/button_icons_live.c
        does in the game's memory, makes A the PS5 circle (it was the E key)"""
        font = self.font_for(0, "keyboard")
        otf = bytearray(self.last_otf)
        at = otf.find(b"HOOKSHOT-ICONS-1")
        self.assertGreater(at, 0)
        fams, first, n, cur = struct.unpack_from(">IIII", otf, at + 16)
        self.assertEqual((fams, first, n, cur), (5, 0xE040, len(bi.CODEPOINTS), 0))
        gl = at + 32
        rel = gl + ((fams * n * 2 + 3) & ~3)
        glyph = lambda f, i: struct.unpack_from(">H", otf, gl + (f * n + i) * 2)[0]
        order = font.getGlyphOrder()
        self.assertEqual(order.index(font.getBestCmap()[0xE040]), glyph(0, 0))    # A: the keyboard's glyph now
        self.assertNotEqual(glyph(0, 0), glyph(3, 0))
        for i in range(n):                                                      # (host/button_icons_live.c's point)
            r = struct.unpack_from(">i", otf, rel + i * 4)[0]
            if r:
                struct.pack_into(">I", otf, at + r, glyph(3, i))
        switched = TTFont(io.BytesIO(bytes(otf)))
        circle = bounds(switched, 0xE040)
        self.assertAlmostEqual(circle[2] - circle[0], 560, delta=2)
        base = TTFont(io.BytesIO(self.external))
        self.assertEqual(drawing(switched, 0xE05E), drawing(base, 0xE05E))       # (the sticks on a pad: the game's)
        self.assertEqual(drawing(switched, 0x20), drawing(base, 0x20))           # (other characters untouched)

    def test_live_copies(self):
        """host/button_icons_live.c itself, on the font as the game may hold it: plain, and as the file has it (XORed with
        its key, after the 8-byte header). Three F8 presses go keyboard -> Xbox -> PS4 -> PS5 (the first finds the font on
        its thread, the others write at once); read back, A is the PS5 circle (it was the E key)"""
        if sys.platform == "win32":
            self.skipTest("host/posix's windows.h stands in for Windows here")
        cc = shutil.which("gcc") or shutil.which("clang")
        if not cc:
            self.skipTest("no C compiler")
        self.font_for(0, "keyboard")
        otf = bytes(self.last_otf) + bytes(-len(self.last_otf) % 4)
        k = struct.pack(">I", KEY)
        base = TTFont(io.BytesIO(self.external))
        with tempfile.TemporaryDirectory(prefix="hookshot-live-") as tmp:
            tmp = Path(tmp)
            (tmp / "hos.h").write_text("#pragma once\n#include <stddef.h>\n#include <stdint.h>\n"
                                       "#define GP(a) ((void *)(uintptr_t)(a))\n"
                                       "void hos_log(const char *fmt, ...);\nint64_t hos_now_ns(void);\n")
            shutil.copy(ROOT / "host" / "button_icons_live.c", tmp / "live.c")   # (beside the stand-in hos.h)
            (tmp / "harness.c").write_text(LIVE_HARNESS, encoding="utf-8")
            exe = tmp / "live"
            build = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-pthread", "-I", str(tmp),
                                    "-I", str(ROOT / "host" / "posix"), str(tmp / "live.c"), str(tmp / "harness.c"),
                                    "-o", str(exe)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            for xored in (False, True):
                with self.subTest(xored=xored):
                    body = bytes(b ^ k[i & 3] for i, b in enumerate(otf)) if xored else otf
                    (tmp / "in").write_bytes(b"\0" * 8 + body)
                    run = subprocess.run([str(exe), str(tmp / "in"), str(tmp / "out"), hex(KEY), "3"], text=True,
                                         capture_output=True, timeout=60)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn("1 time(s) as the file has it" if xored else "1 time(s) plain", run.stderr)
                    self.assertIn("now PlayStation 5", run.stderr)
                    out = (tmp / "out").read_bytes()[8:]
                    if xored:
                        out = bytes(b ^ k[i & 3] for i, b in enumerate(out))
                    switched = TTFont(io.BytesIO(out))
                    circle = bounds(switched, 0xE040)
                    self.assertAlmostEqual(circle[2] - circle[0], 560, delta=2)
                    self.assertEqual(drawing(switched, 0x20), drawing(base, 0x20))   # (other characters untouched)

    def test_playstation(self):
        font = self.font_for(3, "ps5")
        base = TTFont(io.BytesIO(self.external))
        circle = bounds(font, 0xE040)                 # A on the right face button: the PS5 circle, 560 across
        self.assertAlmostEqual(circle[2] - circle[0], 560, delta=2)
        self.assertNotEqual(drawing(font, 0xE040), drawing(base, 0xE040))
        self.assertEqual(drawing(font, 0xE05E), drawing(base, 0xE05E))   # the sticks: the game's own on a pad


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=1))
