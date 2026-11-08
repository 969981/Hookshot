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
- the frame decodes with no dictionary, and the width table has every glyph.
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
int hos_button_icons_romfs(const char *rel, char *out, size_t cap);
int main(void) {
    char out[1024];
    if (hos_button_icons_romfs("Font2/Scalable/Symbol.Nin_Ounce_NVN2.bfotf.zs", out, sizeof out)) return 3;   /* (another font) */
    if (!hos_button_icons_romfs("Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs", out, sizeof out)) return 2;
    puts(out);
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
        self.data, self.glyphs = bi.build_from(self.external, KEY, self.lettering)

    def test_fixed_offsets_keep_every_glyph(self):
        fixed = bi.fixed_offsets(self.external)
        bi.check_charstrings_offset(fixed)
        a, b = TTFont(io.BytesIO(self.external)), TTFont(io.BytesIO(fixed))
        for code in bi.CODEPOINTS:
            self.assertEqual(drawing(a, code), drawing(b, code))

    def test_library(self):
        version, key, font_size, count, first, n = struct.unpack_from("<IIIIII", self.data, 4)
        self.assertEqual((version, key, first, n), (1, KEY, 0xE040, len(bi.CODEPOINTS)))
        for want in (0x01000045, 0x02000057, 0x03000000, 0x10000300 | bi.EAST, 0x10000100 | bi.SOUTH, 0x20000000 | 0xE040):
            self.assertIn(want, self.glyphs)

    def font_for(self, pad, family_env):
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
            env = dict(os.environ, T_DIR=str(tmp), T_PAD=str(pad), HOS_BUTTON_ICONS=family_env)
            run = subprocess.run([str(exe)], text=True, capture_output=True, env=env, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            raw = Path(run.stdout.strip().splitlines()[-1]).read_bytes()
        frame = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 26)
        otf, key = bi.bfotf_decode(frame)
        self.assertEqual(key, KEY)
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

    def test_playstation(self):
        font = self.font_for(3, "ps5")
        base = TTFont(io.BytesIO(self.external))
        circle = bounds(font, 0xE040)                 # A on the right face button: the PS5 circle, 560 across
        self.assertAlmostEqual(circle[2] - circle[0], 560, delta=2)
        self.assertNotEqual(drawing(font, 0xE040), drawing(base, 0xE040))
        self.assertEqual(drawing(font, 0xE05E), drawing(base, 0xE05E))   # the sticks: the game's own on a pad


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=1))
