"""Button icons for keyboard and mouse, Xbox, PlayStation and Switch controllers (host/button_icons.c at the game's start).

The game draws every button prompt, its text and its HUD alike, with one font's private-use glyphs:
romfs/Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs, U+E040 to U+E06A (A B X Y L R ZL ZR + -, the sticks and
their directions and presses, the d-pad). This makes, from the player's own romfs, what the game's start needs to put
other icons in those glyphs' place:

- the External font itself, as the game has it (de-obfuscated), re-saved so that its CFF's CharStrings offset is a
  five-byte number: host/button_icons.c appends a new CharStrings INDEX and changes only that number;
- a library of glyphs, each a Type 2 charstring of its own (rmoveto first, no width, no subroutines, no endchar), its
  left edge at x 0: a key cap for every key at two sizes (the full height of a button glyph, and a third of the
  movement-key cluster), the mouse with each button filled, the mouse with the camera's motion, and a glyph for each
  gamepad input by position (HOS_PAD_*) for Xbox, PlayStation 4 and 5 and Switch controllers.

A cap's lettering is ClarimoUDPE-DemiBold, from the same romfs; the cut-outs are contours wound the other way (CFF fills
by the non-zero rule). Nothing of the game's goes into the source tree: all of it is made from the player's files here.

The game gives each file a buffer of the size its resource size table says (System/Resource/ResourceSizeTable.Product.
<version>.rsizetable.zs, RESTBL: the CRC-32 of the path without .zs, and the size): the External font has 14 856 bytes
(its 14 568 and 288), and the font with other icons in it is larger (a first one of 25 332 bytes: the game's loader gave
the font code nothing, and the game crashed at its start). The table goes in too, its entry for the External font raised
to RSTB_FONT, and host/button_icons.c gives it to the game in the table's place.

hookshot_icons.bin (little-endian): "HKIC", u32 version 2, u32 the font's obfuscation key, u32 font size, u32 glyph
count, u32 first codepoint (U+E040), u32 codepoint count, u32 where the tables are, u32 how many, the font (padded to 4
bytes), the font's glyph index for each
codepoint (u16, 0: none; padded to 4 bytes), then per library glyph {u32 id, u16 width, i16 start x, i16 start y,
i16 end x, i16 end y, u16 pad, u32 offset, u32 length}, sorted by id, the charstrings (offsets from their start), and
per size table {u32 path length, the RomFS path (padded to 4), u32 the font's size in it before, u32 size, the table as
the game has it decompressed, the font's entry raised (padded to 4)}.
Glyph ids:
  0x01000000 | vk           a key at button height (vk 1, 2, 4: the mouse with that button filled; 0: none)
  0x02000000 | vk           the same at a third of the cluster (a small cap; the mouse's buttons as LMB RMB MMB)
  0x03000000 | n            the mouse with the camera's motion: 0 none, 1 up, 2 down, 3 left, 4 right, 5 up and down,
                            6 left and right
  0x10000000 | f << 8 | p   gamepad family f (1 Xbox, 2 PlayStation 4, 3 PlayStation 5, 4 Switch) input p (HOS_PAD_*)
  0x20000000 | codepoint    the game's own glyph for an External codepoint (what is left when nothing is bound)

python -I nx2/button_icons.py <romfs> --out <game folder>/hookshot_icons.bin
"""
import argparse
import io
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WHEEL = os.path.join(ROOT, "third_party", "fonttools", "fonttools-4.60.1-py3-none-any.whl")
if os.path.exists(WHEEL):   # (pure Python, imported from the wheel itself: the builder's Python has no fontTools)
    sys.path.insert(0, WHEEL)

import zstandard as zs  # noqa: E402
from fontTools.misc.transform import Transform  # noqa: E402
from fontTools.pens.basePen import BasePen  # noqa: E402
from fontTools.pens.recordingPen import RecordingPen  # noqa: E402
from fontTools.pens.reverseContourPen import ReverseContourPen  # noqa: E402
from fontTools.pens.transformPen import TransformPen  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

EXTERNAL = ("Font2", "Scalable", "External.Nin_Ounce_NVN2.bfotf.zs")
LETTERING = ("Font2", "Scalable", "ClarimoUDPE-DemiBold.Nin_Ounce_NVN2.bfotf.zs")
MAGIC = 0x7F9A0218
VERSION = 2
RSTB_FONT = 131072          # the External font's buffer in the size table (its own: 14 856)
FONT_KEY = "Font2/Scalable/External.Nin_Ounce_NVN2.bfotf"   # (the size table's name for it: the path without .zs)

BOTTOM, TOP = 20, 580                       # a button glyph's height (the game's A: 19 to 583)
CLUSTER_LO, CLUSTER_HI = -60, 680           # the movement keys' cluster: the line's whole height
SMALL = (CLUSTER_HI - CLUSTER_LO - 30) / 2  # a small cap's height
VK_LBUTTON, VK_RBUTTON, VK_MBUTTON = 1, 2, 4

# HOS_PAD_* (gpu/vk/vk_api.h): SDL's order
SOUTH, EAST, WEST, NORTH, BACK, GUIDE, START, LSTICK, RSTICK, LSHOULDER, RSHOULDER, UP, DOWN, LEFT, RIGHT, LTRIGGER, RTRIGGER = range(17)
XBOX, PS4, PS5, SWITCH = 1, 2, 3, 4

KEY_NAMES = {0x08: "Bksp", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x13: "Pause",
             0x14: "Caps", 0x1B: "Esc", 0x20: "Space", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home",
             0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2D: "Ins", 0x2E: "Del", 0x05: "M4",
             0x06: "M5", 0x5B: "Win", 0x5C: "Win", 0x5D: "Menu", 0x90: "NumLk", 0x91: "ScrLk", 0xA0: "Shift",
             0xA1: "Shift", 0xA2: "Ctrl", 0xA3: "Ctrl", 0xA4: "Alt", 0xA5: "Alt", 0xBA: ";", 0xBB: "=", 0xBC: ",",
             0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0xE2: "\\",
             0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/"}
for _c in range(0x30, 0x3A):
    KEY_NAMES[_c] = chr(_c)
for _c in range(0x41, 0x5B):
    KEY_NAMES[_c] = chr(_c)
for _c in range(0x60, 0x6A):
    KEY_NAMES[_c] = "Num" + str(_c - 0x60)
for _c in range(0x70, 0x88):
    KEY_NAMES[_c] = "F" + str(_c - 0x6F)
SMALL_MOUSE = {VK_LBUTTON: "LMB", VK_RBUTTON: "RMB", VK_MBUTTON: "MMB"}

# the Switch's own glyph for each input position (a Switch Pro Controller: B at the bottom, A on the right)
SWITCH_GLYPH = {SOUTH: 0xE041, EAST: 0xE040, WEST: 0xE043, NORTH: 0xE042, BACK: 0xE04C, GUIDE: 0xE04A, START: 0xE04B,
                LSTICK: 0xE056, RSTICK: 0xE057, LSHOULDER: 0xE044, RSHOULDER: 0xE045, UP: 0xE060, DOWN: 0xE061,
                LEFT: 0xE062, RIGHT: 0xE063, LTRIGGER: 0xE046, RTRIGGER: 0xE047}
# the External glyphs host/button_icons.c may replace or reuse
CODEPOINTS = list(range(0xE040, 0xE06B))


# ---- the files ----------------------------------------------------------------------------------------------------

def dictionaries(romfs):
    raw = zs.ZstdDecompressor().decompress(open(os.path.join(romfs, "Pack", "ZsDic.pack.zs"), "rb").read(),
                                           max_output_size=1 << 28)
    start = struct.unpack_from("<I", raw, 0x0C)[0]
    out = []
    for k in range(struct.unpack_from("<H", raw, 0x1A)[0]):
        _, _, st, end = struct.unpack_from("<IIII", raw, 0x20 + k * 16)
        out.append(raw[start + st:start + end])
    return out


def read_zs(path, dicts):
    data = open(path, "rb").read()
    fhd = data[4]
    size = [0, 1, 2, 4][fhd & 3]
    at = 5 + (0 if (fhd >> 5) & 1 else 1)
    want = int.from_bytes(data[at:at + size], "little") if size else 0
    for d in dicts:
        if want and d[:4] == b"\x37\xa4\x30\xec" and struct.unpack_from("<I", d, 4)[0] == want:
            fd = zs.ZstdCompressionDict(d, dict_type=zs.DICT_TYPE_FULLDICT)
            return zs.ZstdDecompressor(dict_data=fd).decompress(data, max_output_size=1 << 28)
    return zs.ZstdDecompressor().decompress(data, max_output_size=1 << 28)


def bfotf_decode(data):
    """(the font, the key): the header is MAGIC ^ key and size ^ key (big-endian), the font XORed with the key"""
    key = struct.unpack(">I", data[:4])[0] ^ MAGIC
    size = struct.unpack(">I", data[4:8])[0] ^ key
    k = struct.pack(">I", key)
    return bytes(b ^ k[i & 3] for i, b in enumerate(data[8:8 + size])), key


# ---- outlines -----------------------------------------------------------------------------------------------------

class Outline:
    """contours as lists of segments, in font units: ("M", p), ("L", p), ("C", p1, p2, p3), each closed"""

    def __init__(self):
        self.contours = []

    def pen(self):
        return _OutlinePen(self)

    def add(self, other, dx=0, dy=0, reverse=False):
        rec = RecordingPen()
        other.draw(rec)
        target = TransformPen(self.pen(), Transform(1, 0, 0, 1, dx, dy))
        rec.replay(ReverseContourPen(target) if reverse else target)

    def draw(self, pen):
        for c in self.contours:
            for seg in c:
                if seg[0] == "M":
                    pen.moveTo(seg[1])
                elif seg[0] == "L":
                    pen.lineTo(seg[1])
                else:
                    pen.curveTo(seg[1], seg[2], seg[3])
            pen.closePath()

    def bounds(self):
        xs = [p[0] for c in self.contours for s in c for p in s[1:]]
        ys = [p[1] for c in self.contours for s in c for p in s[1:]]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 0, 0)


class _OutlinePen(BasePen):
    def __init__(self, outline):
        super().__init__(None)
        self.o = outline
        self.cur = None

    def _moveTo(self, p):
        self.cur = [("M", p)]

    def _lineTo(self, p):
        self.cur.append(("L", p))

    def _curveToOne(self, p1, p2, p3):
        self.cur.append(("C", p1, p2, p3))

    def _closePath(self):
        if self.cur and len(self.cur) > 1:
            self.o.contours.append(self.cur)
        self.cur = None

    _endPath = _closePath


def signed_area(contour):
    pts = [p for s in contour for p in s[1:]]
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def polygon(points, filled=True):
    """a closed polygon, counter-clockwise when filled (CFF's outer contours), clockwise when cut out"""
    o = Outline()
    c = [("M", points[0])] + [("L", p) for p in points[1:]]
    if (signed_area(c) > 0) != filled:
        pts = list(reversed(points))
        c = [("M", pts[0])] + [("L", p) for p in pts[1:]]
    o.contours.append(c)
    return o


def rounded(x0, y0, x1, y1, r, filled=True):
    k = 0.5523 * r
    o = Outline()
    pen = o.pen()
    pen.moveTo((x0 + r, y0))
    pen.lineTo((x1 - r, y0))
    pen.curveTo((x1 - r + k, y0), (x1, y0 + r - k), (x1, y0 + r))
    pen.lineTo((x1, y1 - r))
    pen.curveTo((x1, y1 - r + k), (x1 - r + k, y1), (x1 - r, y1))
    pen.lineTo((x0 + r, y1))
    pen.curveTo((x0 + r - k, y1), (x0, y1 - r + k), (x0, y1 - r))
    pen.lineTo((x0, y0 + r))
    pen.curveTo((x0, y0 + r - k), (x0 + r - k, y0), (x0 + r, y0))
    pen.closePath()
    if not filled:
        r2 = Outline()
        r2.add(o, reverse=True)
        return r2
    return o


def circle(cx, cy, r, filled=True):
    return rounded(cx - r, cy - r, cx + r, cy + r, r, filled)


def merged(*outlines):
    o = Outline()
    for x in outlines:
        o.contours.extend(x.contours)
    return o


class Lettering:
    def __init__(self, font):
        self.gs = font.getGlyphSet()
        self.cmap = font.getBestCmap()
        self.cap_height = font["OS/2"].sCapHeight or 700

    def text(self, s, cap, cut=True):
        """the text (caps cap units high, its left at 0, its baseline at 0), wound to cut out of a filled shape"""
        scale = cap / self.cap_height
        o, x = Outline(), 0
        for ch in s:
            g = self.cmap.get(ord(ch)) or self.cmap.get(ord("?"))
            rec = RecordingPen()
            self.gs[g].draw(TransformPen(rec, Transform(scale, 0, 0, scale, x, 0)))
            target = ReverseContourPen(o.pen()) if cut else o.pen()
            rec.replay(target)
            x += self.gs[g].width * scale
        return o, x


# ---- the glyphs (each with its left edge at 0) -------------------------------------------------------------------

def keycap(L, label, bottom=BOTTOM, top=TOP):
    h = top - bottom
    cap = h * (0.50 if len(label) <= 1 else 0.40 if len(label) <= 3 else 0.32)
    text, tw = L.text(label, cap)
    w = max(h, tw + h * 0.45)
    body = rounded(0, bottom, w, top, min(110, h * 0.22))
    body.add(text, (w - tw) / 2, bottom + (h - cap) / 2)
    return body


def mouse(button=None):
    w, h = 360, TOP - BOTTOM
    o = merged(rounded(0, BOTTOM, w, TOP, w / 2), rounded(48, BOTTOM + 48, w - 48, TOP - 48, w / 2 - 48, filled=False))
    split = BOTTOM + h * 0.58
    o = merged(o, rounded(30, split - 20, w - 30, split + 20, 10), rounded(w / 2 - 20, split, w / 2 + 20, TOP - 30, 10))
    if button in (VK_LBUTTON, VK_RBUTTON):   # the button's quarter of the body: its curve and the split's edges
        k, r = 0.5523, w / 2
        cx, cy = w / 2, TOP - r
        fill = Outline()
        pen = fill.pen()
        if button == VK_LBUTTON:
            pen.moveTo((cx, split))
            pen.lineTo((cx, TOP))
            pen.curveTo((cx - k * r, TOP), (0, cy + k * r), (0, cy))
            pen.lineTo((0, split))
        else:
            pen.moveTo((cx, split))
            pen.lineTo((w, split))
            pen.lineTo((w, cy))
            pen.curveTo((w, cy + k * r), (cx + k * r, TOP), (cx, TOP))
        pen.closePath()
        if signed_area(fill.contours[0]) < 0:
            f2 = Outline()
            f2.add(fill, reverse=True)
            fill = f2
        o = merged(o, fill)
    elif button == VK_MBUTTON:
        o = merged(o, rounded(w / 2 - 45, split + 40, w / 2 + 45, TOP - 70, 45))
    return o


def arrows(kind):
    """the camera's motion beside a mouse: 1 up, 2 down, 3 left, 4 right, 5 up and down, 6 left and right"""
    s = 250 if kind <= 4 else 230
    heads = {1: [(0, 1)], 2: [(0, -1)], 3: [(-1, 0)], 4: [(1, 0)], 5: [(0, 1), (0, -1)], 6: [(-1, 0), (1, 0)]}[kind]
    cy = (BOTTOM + TOP) / 2
    o = Outline()
    for dx, dy in heads:
        px, py = -dy, dx
        o = merged(o, polygon([(dx * s * 1.1, cy + dy * s * 1.1),
                               (dx * s * 0.25 + px * s * 0.6, cy + dy * s * 0.25 + py * s * 0.6),
                               (dx * s * 0.25 - px * s * 0.6, cy + dy * s * 0.25 - py * s * 0.6)]),
                   polygon([(px * 42, cy + py * 42), (dx * s * 0.3 + px * 42, cy + dy * s * 0.3 + py * 42),
                            (dx * s * 0.3 - px * 42, cy + dy * s * 0.3 - py * 42), (-px * 42, cy - py * 42)]))
    return o


def mouse_moving(kind):
    o = mouse()
    if kind:
        a = arrows(kind)
        x0 = a.bounds()[0]
        o.add(a, 360 + 60 - x0)
    return o


def face(L, label=None, symbol=None):
    """a filled round button with its letter or symbol cut out (Xbox, PlayStation)"""
    r = (TOP - BOTTOM) / 2
    cx, cy = r, BOTTOM + r
    o = circle(cx, cy, r)
    if label:
        cap = r * (0.95 if len(label) == 1 else 0.70)
        t, tw = L.text(label, cap)
        o.add(t, cx - tw / 2, cy - cap / 2)
    elif symbol == "cross":
        a, b = r * 0.48, r * 0.12   # the arms' half length and half width, one polygon (overlapping cut-outs would fill)
        d = math.sqrt(0.5)
        arm = [(b, b), (b, a), (-b, a), (-b, b), (-a, b), (-a, -b), (-b, -b), (-b, -a), (b, -a), (b, -b), (a, -b), (a, b)]
        rot = [((x - y) * d + cx, (x + y) * d + cy) for x, y in arm]
        o = merged(o, polygon(rot, filled=False))
    elif symbol == "circle":
        o = merged(o, circle(cx, cy, r * 0.52, filled=False), circle(cx, cy, r * 0.36))
    elif symbol == "square":
        s1, s2 = r * 0.46, r * 0.30
        o = merged(o, polygon([(cx - s1, cy - s1), (cx + s1, cy - s1), (cx + s1, cy + s1), (cx - s1, cy + s1)], filled=False),
                   polygon([(cx - s2, cy - s2), (cx + s2, cy - s2), (cx + s2, cy + s2), (cx - s2, cy + s2)]))
    elif symbol == "triangle":
        def tri(t):
            return [(cx + t * math.cos(math.radians(a)), cy - r * 0.06 + t * math.sin(math.radians(a))) for a in (90, 210, 330)]
        o = merged(o, polygon(tri(r * 0.60), filled=False), polygon(tri(r * 0.32)))
    elif symbol == "view":   # two windows, one up and to the right of the other (Xbox's View); the front one whole,
        q = r * 0.30         # the back one only where the front does not cover it (overlapping cut-outs would fill)
        fx0, fy0 = cx - q * 1.25, cy - q * 1.25
        bx0, by0 = cx - q * 0.25, cy - q * 0.25
        t = q * 0.30
        o = merged(o, polygon([(fx0, fy0), (fx0 + 1.5 * q, fy0), (fx0 + 1.5 * q, fy0 + 1.5 * q), (fx0, fy0 + 1.5 * q)], filled=False),
                   polygon([(fx0 + t, fy0 + t), (fx0 + 1.5 * q - t, fy0 + t), (fx0 + 1.5 * q - t, fy0 + 1.5 * q - t),
                            (fx0 + t, fy0 + 1.5 * q - t)]),
                   polygon([(fx0 + 1.5 * q + t * 0.8, by0), (bx0 + 1.5 * q, by0), (bx0 + 1.5 * q, by0 + 1.5 * q), (bx0, by0 + 1.5 * q),
                            (bx0, fy0 + 1.5 * q + t * 0.8), (bx0 + t, fy0 + 1.5 * q + t * 0.8), (bx0 + t, by0 + 1.5 * q - t),
                            (bx0 + 1.5 * q - t, by0 + 1.5 * q - t), (bx0 + 1.5 * q - t, by0 + t), (fx0 + 1.5 * q + t * 0.8, by0 + t)],
                           filled=False))
    elif symbol == "menu":   # three lines (Xbox's Menu)
        w, t = r * 0.55, r * 0.09
        for k in (-1, 0, 1):
            y = cy + k * r * 0.32
            o = merged(o, polygon([(cx - w, y - t), (cx + w, y - t), (cx + w, y + t), (cx - w, y + t)], filled=False))
    return o


def pill(L, label, kind):
    """a shoulder button, a trigger or a small labelled button with its text cut out"""
    h = TOP - BOTTOM
    cap = h * (0.40 if len(label) <= 2 else 0.24)
    t, tw = L.text(label, cap)
    if kind == "trigger":   # rounded on top, square below
        w = max(h * 0.95, tw + h * 0.4)
        r, k = min(w, h) * 0.45, 0.5523 * min(w, h) * 0.45
        o = Outline()   # (one contour: two overlapping ones would be filled twice, and the cut-out would not show)
        pen = o.pen()
        pen.moveTo((0, BOTTOM))
        pen.lineTo((w, BOTTOM))
        pen.lineTo((w, TOP - r))
        pen.curveTo((w, TOP - r + k), (w - r + k, TOP), (w - r, TOP))
        pen.lineTo((r, TOP))
        pen.curveTo((r - k, TOP), (0, TOP - r + k), (0, TOP - r))
        pen.closePath()
        o.add(t, (w - tw) / 2, BOTTOM + (h - cap) * 0.42)
    elif kind == "bumper":
        w = max(h * 1.25, tw + h * 0.5)
        hb = h * 0.62
        y0 = BOTTOM + (h - hb) / 2
        o = rounded(0, y0, w, y0 + hb, hb * 0.45)
        o.add(t, (w - tw) / 2, y0 + (hb - cap) / 2)
    else:   # "stick" (L3, LS: round) or "small" (Share, Options: a pill)
        if kind == "stick":
            return face(L, label)
        w = max(h * 1.3, tw + h * 0.5)
        hb = h * 0.56
        y0 = BOTTOM + (h - hb) / 2
        o = rounded(0, y0, w, y0 + hb, hb / 2)
        o.add(t, (w - tw) / 2, y0 + (hb - cap) / 2)
    return o


def pad_glyph(L, family, p, original):
    """a gamepad input p (HOS_PAD_*) of a family, or None for the game's own (the d-pad, Home)"""
    if family == SWITCH:
        return original(SWITCH_GLYPH[p])
    if p in (UP, DOWN, LEFT, RIGHT):
        return original(SWITCH_GLYPH[p])   # (a d-pad is a d-pad)
    if p == GUIDE:
        return original(0xE04A)            # (Home: the game's own house)
    if family == XBOX:
        return {SOUTH: lambda: face(L, "A"), EAST: lambda: face(L, "B"), WEST: lambda: face(L, "X"),
                NORTH: lambda: face(L, "Y"), BACK: lambda: face(L, symbol="view"), START: lambda: face(L, symbol="menu"),
                LSTICK: lambda: pill(L, "LS", "stick"), RSTICK: lambda: pill(L, "RS", "stick"),
                LSHOULDER: lambda: pill(L, "LB", "bumper"), RSHOULDER: lambda: pill(L, "RB", "bumper"),
                LTRIGGER: lambda: pill(L, "LT", "trigger"), RTRIGGER: lambda: pill(L, "RT", "trigger")}[p]()
    return {SOUTH: lambda: face(L, symbol="cross"), EAST: lambda: face(L, symbol="circle"),
            WEST: lambda: face(L, symbol="square"), NORTH: lambda: face(L, symbol="triangle"),
            BACK: lambda: pill(L, "SHARE" if family == PS4 else "CREATE", "small"),
            START: lambda: pill(L, "OPTIONS", "small"),
            LSTICK: lambda: pill(L, "L3", "stick"), RSTICK: lambda: pill(L, "R3", "stick"),
            LSHOULDER: lambda: pill(L, "L1", "bumper"), RSHOULDER: lambda: pill(L, "R1", "bumper"),
            LTRIGGER: lambda: pill(L, "L2", "trigger"), RTRIGGER: lambda: pill(L, "R2", "trigger")}[p]()


# ---- Type 2 charstrings -------------------------------------------------------------------------------------------

def t2_number(v):
    v = int(round(v))
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    return b"\x1c" + struct.pack(">h", v)


def charstring(o):
    """(bytes, start, end): rmoveto first (from 0, 0), rlineto and rrcurveto, closed contours; no width, no endchar"""
    out = bytearray()
    cur = (0, 0)
    start = None

    def rel(p):
        nonlocal cur
        q = (int(round(p[0])), int(round(p[1])))
        d = (q[0] - cur[0], q[1] - cur[1])
        cur = q
        return d

    for c in o.contours:
        for seg in c:
            if seg[0] == "M":
                d = rel(seg[1])
                if start is None:
                    start = cur
                out += t2_number(d[0]) + t2_number(d[1]) + b"\x15"
            elif seg[0] == "L":
                d = rel(seg[1])
                if d != (0, 0):
                    out += t2_number(d[0]) + t2_number(d[1]) + b"\x05"
            else:
                d1, d2, d3 = rel(seg[1]), rel(seg[2]), rel(seg[3])
                out += b"".join(t2_number(v) for v in d1 + d2 + d3) + b"\x08"
    return bytes(out), start or (0, 0), cur


# ---- the library --------------------------------------------------------------------------------------------------

def patch_rstb(data, key=FONT_KEY, size=RSTB_FONT):
    """(the table with key's entry at least size, key's size before; 0 when the table has none for it)"""
    import zlib
    if data[:6] != b"RESTBL":
        raise ValueError("not a resource size table")
    _, strsz, ncrc, nname = struct.unpack_from("<IIII", data, 6)
    out = bytearray(data)
    h, before = zlib.crc32(key.encode()), 0
    for i in range(ncrc):
        at = 22 + i * 8
        if struct.unpack_from("<I", out, at)[0] == h:
            before = struct.unpack_from("<I", out, at + 4)[0]
            struct.pack_into("<I", out, at + 4, max(before, size))
    at = 22 + ncrc * 8
    for i in range(nname):   # (names whose CRC-32 collide with another's are kept by name)
        if out[at:at + strsz].split(b"\0")[0].decode("utf-8", "replace") == key:
            before = struct.unpack_from("<I", out, at + strsz)[0]
            struct.pack_into("<I", out, at + strsz, max(before, size))
        at += strsz + 4
    return bytes(out), before


def size_tables(romfs, dicts):
    """[(RomFS path, the table decompressed)] for System/Resource/ResourceSizeTable.Product.*.rsizetable.zs"""
    folder = os.path.join(romfs, "System", "Resource")
    out = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.startswith("ResourceSizeTable.") and name.endswith(".rsizetable.zs"):
            out.append(("System/Resource/" + name, read_zs(os.path.join(folder, name), dicts)))
    return out


def build(romfs):
    dicts = dictionaries(romfs)
    ext, key = bfotf_decode(read_zs(os.path.join(romfs, *EXTERNAL), dicts))
    lettering = TTFont(io.BytesIO(bfotf_decode(read_zs(os.path.join(romfs, *LETTERING), dicts))[0]))
    tables = size_tables(romfs, dicts)
    if not tables:
        raise SystemExit("no resource size table in %s/System/Resource: the game would not take a larger font" % romfs)
    return build_from(ext, key, lettering, tables)


def build_from(ext, key, lettering, tables=()):
    """hookshot_icons.bin from the External font (an OpenType font's bytes), its key, the lettering's font and the
    resource size tables [(RomFS path, decompressed)]"""
    font = TTFont(io.BytesIO(ext))
    L = Lettering(lettering)
    gs, cmap = font.getGlyphSet(), font.getBestCmap()

    def original(code):
        o = Outline()
        gs[cmap[code]].draw(o.pen())
        x0 = o.bounds()[0]
        moved = Outline()
        moved.add(o, -x0)
        return moved

    glyphs = {}
    for vk in list(KEY_NAMES) + [0, VK_LBUTTON, VK_RBUTTON, VK_MBUTTON]:
        glyphs[0x01000000 | vk] = mouse(vk) if vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON) else keycap(L, KEY_NAMES.get(vk, "-"))
        glyphs[0x02000000 | vk] = keycap(L, SMALL_MOUSE.get(vk) or KEY_NAMES.get(vk, "-"), CLUSTER_LO, CLUSTER_LO + SMALL)
    for n in range(7):
        glyphs[0x03000000 | n] = mouse_moving(n)
    for family in (XBOX, PS4, PS5, SWITCH):
        for p in range(17):
            glyphs[0x10000000 | family << 8 | p] = pad_glyph(L, family, p, original)
    for code in CODEPOINTS:
        if code in cmap:
            glyphs[0x20000000 | code] = original(code)

    # the font with its CFF's Top DICT offsets as five-byte numbers (fixed_offsets), for host/button_icons.c
    otf = fixed_offsets(ext)
    check_charstrings_offset(otf)
    entries, blob = [], bytearray()
    for gid, o in sorted(glyphs.items()):
        cs, start, end = charstring(o)
        b = o.bounds()
        width = int(math.ceil(b[2])) if o.contours else 0
        entries.append(struct.pack("<IHhhhhHII", gid, width, start[0], start[1], end[0], end[1], 0, len(blob), len(cs)))
        blob += cs
    otf_padded = otf + b"\0" * (-len(otf) % 4)
    order = {name: i for i, name in enumerate(font.getGlyphOrder())}
    gids = b"".join(struct.pack("<H", order[cmap[c]] if c in cmap else 0) for c in CODEPOINTS)
    gids += b"\0" * (-len(gids) % 4)
    blob += b"\0" * (-len(blob) % 4)
    rst = bytearray()
    for path, table in tables:
        patched, before = patch_rstb(table)
        if not before:
            raise SystemExit("%s has no entry for %s" % (path, FONT_KEY))
        p = path.encode()
        rst += struct.pack("<I", len(p)) + p + b"\0" * (-len(p) % 4)
        rst += struct.pack("<II", before, len(patched)) + patched + b"\0" * (-len(patched) % 4)
    head_len = 4 + 8 * 4
    body = otf_padded + gids + b"".join(entries) + bytes(blob)
    return (b"HKIC" + struct.pack("<IIIIIIII", VERSION, key, len(otf), len(entries), CODEPOINTS[0], len(CODEPOINTS),
                                  head_len + len(body), len(tables)) + body + bytes(rst)), glyphs


def otf_table(otf, tag):
    n = struct.unpack_from(">H", otf, 4)[0]
    for i in range(n):
        t, _, off, length = struct.unpack_from(">4sIII", otf, 12 + i * 16)
        if t == tag:
            return off, length
    raise ValueError("no %s table" % tag)


def cff_index(cff, at):
    """(count, offsize, the data's start minus 1, the offsets, the end) of the INDEX at at"""
    count = struct.unpack_from(">H", cff, at)[0]
    if not count:
        return 0, 0, at + 2, [], at + 2
    osz = cff[at + 2]
    offs = [int.from_bytes(cff[at + 3 + k * osz:at + 3 + (k + 1) * osz], "big") for k in range(count + 1)]
    base = at + 3 + (count + 1) * osz - 1
    return count, osz, base, offs, base + offs[-1]


def dict_ops(d):
    """a DICT's (operator, [(kind, value)]) in order: kind "int" or "raw" (a real's bytes)"""
    out, args, i = [], [], 0
    while i < len(d):
        b = d[i]
        if b == 28:
            args.append(("int", struct.unpack_from(">h", d, i + 1)[0]))
            i += 3
        elif b == 29:
            args.append(("int", struct.unpack_from(">i", d, i + 1)[0]))
            i += 5
        elif b == 30:
            j = i + 1
            while (d[j] >> 4) != 0x0F and (d[j] & 0x0F) != 0x0F:
                j += 1
            args.append(("raw", d[i:j + 1]))
            i = j + 1
        elif 32 <= b <= 246:
            args.append(("int", b - 139))
            i += 1
        elif 247 <= b <= 250:
            args.append(("int", (b - 247) * 256 + d[i + 1] + 108))
            i += 2
        elif 251 <= b <= 254:
            args.append(("int", -(b - 251) * 256 - d[i + 1] - 108))
            i += 2
        elif b == 12:
            out.append((1200 + d[i + 1], args))
            args = []
            i += 2
        else:
            out.append((b, args))
            args = []
            i += 1
    return out


def dict_number(v):
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    if -32768 <= v <= 32767:
        return b"\x1c" + struct.pack(">h", v)
    return b"\x1d" + struct.pack(">i", v)


OFFSET_OPS = {15, 16, 17, 18, 1236, 1237}   # charset, Encoding, CharStrings, Private (size, offset), FDArray, FDSelect


def fixed_offsets(otf):
    """the font with its CFF's Top DICT rewritten: every offset (and the Private DICT's size) a five-byte number, and
    what follows the Top DICT moved by however much it grew. host/button_icons.c can then put a new CharStrings INDEX at
    the end of the table and change only those five bytes."""
    off, length = otf_table(otf, b"CFF ")
    cff = otf[off:off + length]
    hdr = cff[2]
    _, _, _, _, top_at = cff_index(cff, hdr)
    count, osz, base, offs, after = cff_index(cff, top_at)
    if count != 1:
        raise ValueError("a CFF with %d fonts" % count)
    ops = dict_ops(cff[base + offs[0]:base + offs[1]])

    def encode(shift):
        d = bytearray()
        for op, args in ops:
            for k, (kind, v) in enumerate(args):
                if kind == "raw":
                    d += v
                elif op in OFFSET_OPS and not (op == 18 and k == 0):
                    d += b"\x1d" + struct.pack(">i", v + shift)
                elif op == 18 and k == 0:   # (the Private DICT's size: unchanged, five bytes all the same)
                    d += b"\x1d" + struct.pack(">i", v)
                else:
                    d += dict_number(v)
            d += bytes([12, op - 1200]) if op >= 1200 else bytes([op])
        return bytes(d)

    def index(data):
        return struct.pack(">HB", 1, 4) + struct.pack(">II", 1, len(data) + 1) + data

    old_len = after - top_at
    new = index(encode(0))
    shift = len(new) - old_len
    new = index(encode(shift))   # (the numbers are five bytes whatever they hold: the length is the same)
    assert len(new) - old_len == shift
    cff2 = cff[:top_at] + new + cff[after:]
    return replace_table(otf, b"CFF ", cff2)


def replace_table(otf, tag, data):
    """the font with one table's bytes replaced (the tables laid out again, in the same order)"""
    n = struct.unpack_from(">H", otf, 4)[0]
    tables = []
    for i in range(n):
        t, _, o, ln = struct.unpack_from(">4sIII", otf, 12 + i * 16)
        tables.append((t, data if t == tag else otf[o:o + ln]))
    head = bytearray(otf[:12])
    body = bytearray()
    at = 12 + 16 * n
    entries = []
    for t, d in sorted(tables, key=lambda x: x[0]):
        pad = d + b"\0" * (-len(d) % 4)
        csum = sum(struct.unpack(">%dI" % (len(pad) // 4), pad)) & 0xFFFFFFFF
        entries.append(struct.pack(">4sIII", t, csum, at + len(body), len(d)))
        body += pad
    return bytes(head) + b"".join(entries) + bytes(body)


def check_charstrings_offset(otf):
    """the Top DICT's CharStrings operand (op 17) must be 29 + int32, which host/button_icons.c overwrites in place"""
    off, _ = otf_table(otf, b"CFF ")
    cff = otf[off:]
    hdr = cff[2]

    def index_end(at):
        count = struct.unpack_from(">H", cff, at)[0]
        if not count:
            return at + 2
        osz = cff[at + 2]
        last = int.from_bytes(cff[at + 3 + count * osz:at + 3 + (count + 1) * osz], "big")
        return at + 3 + (count + 1) * osz + last - 1

    top = index_end(hdr)   # (after the Name INDEX: the Top DICT INDEX)
    count, osz = struct.unpack_from(">H", cff, top)[0], cff[top + 2]
    o0 = int.from_bytes(cff[top + 3:top + 3 + osz], "big")
    o1 = int.from_bytes(cff[top + 3 + osz:top + 3 + 2 * osz], "big")
    data = top + 3 + (count + 1) * osz - 1
    d = cff[data + o0:data + o1]
    i, last_num = 0, None
    while i < len(d):
        b = d[i]
        if b == 29:
            last_num = ("i32", i)
            i += 5
        elif b == 28:
            last_num = ("i16", i)
            i += 3
        elif 32 <= b <= 246:
            last_num = ("i8", i)
            i += 1
        elif 247 <= b <= 254:
            last_num = ("i8", i)
            i += 2
        elif b == 30:
            i += 1
            while i < len(d) and (d[i] & 0x0F) != 0x0F and (d[i] >> 4) != 0x0F:
                i += 1
            i += 1
            last_num = ("real", i)
        elif b == 12:
            i += 2
        else:
            if b == 17:
                if not last_num or last_num[0] != "i32":
                    raise ValueError("the CharStrings offset is not a five-byte number (%s)" % (last_num,))
                return
            i += 1
    raise ValueError("no CharStrings offset in the Top DICT")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("romfs")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    data, glyphs = build(a.romfs)
    tmp = a.out + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, a.out)
    print("button icons: %d glyphs, %d KB" % (len(glyphs), len(data) >> 10), flush=True)


if __name__ == "__main__":
    main()
