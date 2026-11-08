/* (hookshot) Button icons for what the player plays with: keyboard and mouse, an Xbox, PlayStation 4 or 5 or Switch
   controller. The game draws every button prompt, text and HUD alike, with the private-use glyphs of one font,
   romfs:/Font2/Scalable/External.Nin_Ounce_NVN2.bfotf.zs (U+E040 A to U+E06A). The builder (nx2/button_icons.py)
   left in the game folder hookshot_icons.bin: that font, and a library of glyphs (a key cap for every key, the mouse,
   each gamepad input by position for each family). When the game first opens the font, this makes it again with the
   glyphs of the family in use, for the controls as bound (host/nn_hid.c: A bound to Space shows Space, A bound to the
   bottom face button shows a cross on a PlayStation pad), and nn_fs.c gives the game that file instead.

   Which family: the Insert menu's "Button icons" (button_icons= in hookshot_options.ini: 0 Auto, 1 Keyboard, 2 Xbox,
   3 PlayStation, 4 Switch); Auto is the controller connected when the game starts (host/pad_sdl.c), keyboard and mouse
   when there is none. The game reads the font once, at its start: another controller later shows its icons from the
   next start.

   How the font is made again: its CFF gets a new CharStrings INDEX at its end (the builder wrote the Top DICT's
   CharStrings offset as five bytes, which change here alone), the hmtx table is written whole (numberOfHMetrics every
   glyph) for the new widths, and the file is obfuscated as the original (MAGIC ^ key, size ^ key, the font XORed with
   the key) and put in a zstd frame of raw blocks (no compression, so no dictionary matters). A glyph made of more than
   one library glyph (two keys, the movement keys' cluster) is their charstrings one after another, each first rmoveto
   moved to where the one before ended.
   HOS_BUTTON_ICONS=0 leaves the game's own; HOS_BUTTON_ICONS=keyboard, xbox, playstation, ps4, ps5 or switch sets it. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "../gpu/vk/vk_api.h"
#include "hos_paths.h"   /* (the game folder's files: the game is bin\hookshot.exe beside them since the launcher came) */

void hos_log(const char *fmt, ...);               /* hos_log.c (hos.h is not needed here: tests compile this alone) */
int hos_binding(int set, int button, int slot);   /* nn_hid.c */
int hos_sdl_pad_family(int wait_ms);              /* pad_sdl.c: 0 none, 1 Xbox, 2 PS4, 3 PS5, 4 Switch */

enum { FAM_KEYS, FAM_XBOX, FAM_PS4, FAM_PS5, FAM_SWITCH };
static const char *const fam_name[] = {"keyboard and mouse", "Xbox", "PlayStation 4", "PlayStation 5", "Switch"};
#define FONT_PATH "font2/scalable/external.nin_ounce_nvn2.bfotf.zs"
#define MAGIC 0x7F9A0218u
#define SIDE 40

typedef struct { uint32_t id; uint16_t width; int16_t sx, sy, ex, ey; uint16_t pad; uint32_t off, len; } LibGlyph;

static const uint8_t *lib_cs;       /* the library's charstrings */
static const LibGlyph *lib;
static uint32_t lib_n;

static uint16_t be16(const uint8_t *p) { return (uint16_t)(p[0] << 8 | p[1]); }
static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }
static void put16(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 8), p[1] = (uint8_t)v; }
static void put32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 24), p[1] = (uint8_t)(v >> 16), p[2] = (uint8_t)(v >> 8), p[3] = (uint8_t)v; }

static const LibGlyph *find(uint32_t id) {
    uint32_t lo = 0, hi = lib_n;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) / 2;
        if (lib[mid].id < id) lo = mid + 1;
        else hi = mid;
    }
    return lo < lib_n && lib[lo].id == id ? &lib[lo] : NULL;
}

/* ---- a growing buffer ---- */
typedef struct { uint8_t *p; size_t n, cap; } Buf;
static void put(Buf *b, const void *d, size_t n) {
    if (b->n + n > b->cap) {
        b->cap = (b->n + n) * 2 + 256;
        b->p = (uint8_t *)realloc(b->p, b->cap);
    }
    memcpy(b->p + b->n, d, n);
    b->n += n;
}
static void put_byte(Buf *b, uint8_t v) { put(b, &v, 1); }
static void put_num(Buf *b, int v) {   /* a Type 2 charstring number */
    if (v >= -107 && v <= 107) put_byte(b, (uint8_t)(v + 139));
    else if (v >= 108 && v <= 1131) v -= 108, put_byte(b, (uint8_t)((v >> 8) + 247)), put_byte(b, (uint8_t)v);
    else if (v >= -1131 && v <= -108) v = -v - 108, put_byte(b, (uint8_t)((v >> 8) + 251)), put_byte(b, (uint8_t)v);
    else put_byte(b, 28), put_byte(b, (uint8_t)((v >> 8) & 0xff)), put_byte(b, (uint8_t)(v & 0xff));
}
static size_t num_len(const uint8_t *p) { return *p == 28 ? 3 : *p == 255 ? 5 : *p >= 247 ? 2 : 1; }

/* ---- a glyph made of library glyphs ---- */
typedef struct { uint32_t id; int dx, dy; } Part;
typedef struct { Buf cs; int width; } Made;

/* parts[] one after another (each at its dx, dy); 0 if one is not in the library */
static int compose(const Part *parts, int n, Made *m) {
    int cx = 0, cy = 0, right = 0;
    m->cs.n = 0;
    for (int i = 0; i < n; i++) {
        const LibGlyph *g = find(parts[i].id);
        if (!g || !g->len) return 0;
        const uint8_t *cs = lib_cs + g->off, *end = cs + g->len;
        const uint8_t *body = cs + num_len(cs);   /* (its first op: dx dy rmoveto, from 0, 0) */
        body += num_len(body);
        if (body >= end || *body != 21) return 0;
        body++;
        put_num(&m->cs, g->sx + parts[i].dx - cx);
        put_num(&m->cs, g->sy + parts[i].dy - cy);
        put_byte(&m->cs, 21);
        put(&m->cs, body, (size_t)(end - body));
        cx = g->ex + parts[i].dx, cy = g->ey + parts[i].dy;
        if (parts[i].dx + g->width > right) right = parts[i].dx + g->width;
    }
    put_byte(&m->cs, 14);   /* endchar */
    m->width = right + SIDE;
    return 1;
}

static int lib_width(uint32_t id) {
    const LibGlyph *g = find(id);
    return g ? g->width : 0;
}

/* ---- what each codepoint becomes ---- */
static int key_of(int control) {   /* the control's first bound key, 0 none */
    const int a = hos_binding(HOS_BIND_KEY, control, 0);
    return a ? a : hos_binding(HOS_BIND_KEY, control, 1);
}

static int one(uint32_t id, Made *m) {
    const Part p = {id, SIDE, 0};
    return compose(&p, 1, m);
}

static int key_glyph(int control, Made *m) {
    const int vk = key_of(control);
    return vk && one(0x01000000u | (uint32_t)vk, m);
}

static int key_pair(int c1, int c2, Made *m) {
    const int a = key_of(c1), b = key_of(c2);
    if (!a || !b) return 0;
    const Part p[2] = {{0x01000000u | (uint32_t)a, SIDE, 0},
                       {0x01000000u | (uint32_t)b, SIDE + lib_width(0x01000000u | (uint32_t)a) + 50, 0}};
    return compose(p, 2, m);
}

/* the keys of four directions as on a keyboard: up above, left down right below (the builder's small caps sit at the
   bottom row: the up one moves up by the row and the gap) */
static int cluster(int up, int left, int down, int right, Made *m) {
    const int k[4] = {key_of(up), key_of(left), key_of(down), key_of(right)};
    if (!k[0] || !k[1] || !k[2] || !k[3]) return 0;
    const uint32_t id[4] = {0x02000000u | (uint32_t)k[0], 0x02000000u | (uint32_t)k[1], 0x02000000u | (uint32_t)k[2],
                            0x02000000u | (uint32_t)k[3]};
    const int wl = lib_width(id[1]), wd = lib_width(id[2]), wu = lib_width(id[0]);
    const int row_h = (680 - (-60) - 30) / 2;   /* (nx2/button_icons.py: CLUSTER_LO, CLUSTER_HI, SMALL) */
    const Part p[4] = {{id[1], SIDE, 0}, {id[2], SIDE + wl + 25, 0}, {id[3], SIDE + wl + 25 + wd + 25, 0},
                       {id[0], SIDE + wl + 25 + (wd - wu) / 2, row_h + 30}};
    return compose(p, 4, m);
}

/* a Switch button (HOS_SW_*) on a gamepad of family fam: the input it is bound to */
static int pad_glyph(int fam, int sw, Made *m) {
    const int p = hos_binding(HOS_BIND_PAD, sw, 0);
    return p >= 0 && p < HOS_PAD_N && one(0x10000000u | (uint32_t)fam << 8 | (uint32_t)p, m);
}

/* codepoint -> the Switch button it shows (HOS_SW_*), or -1 */
static int cp_button(uint32_t cp) {
    switch (cp) {
    case 0xE040: return HOS_SW_A;
    case 0xE041: return HOS_SW_B;
    case 0xE042: return HOS_SW_X;
    case 0xE043: return HOS_SW_Y;
    case 0xE044: return HOS_SW_L;
    case 0xE045: return HOS_SW_R;
    case 0xE046: return HOS_SW_ZL;
    case 0xE047: return HOS_SW_ZR;
    case 0xE04B: return HOS_SW_PLUS;
    case 0xE04C: return HOS_SW_MINUS;
    case 0xE056: return HOS_SW_LS;
    case 0xE057: return HOS_SW_RS;
    case 0xE060: return HOS_SW_UP;
    case 0xE061: return HOS_SW_DOWN;
    case 0xE062: return HOS_SW_LEFT;
    case 0xE063: return HOS_SW_RIGHT;
    default: return -1;
    }
}

/* what codepoint cp becomes for family fam: 1 and m filled, or 0 to keep the game's */
static int glyph_for(int fam, uint32_t cp, Made *m) {
    const int sw = cp_button(cp);
    if (fam != FAM_KEYS) return sw >= 0 && pad_glyph(fam, sw, m);   /* (the sticks, and the rest, are the game's) */
    if (sw >= 0) return key_glyph(sw, m);
    switch (cp) {
    case 0xE050: return key_glyph(HOS_KEY_MOVE_DOWN, m);
    case 0xE052: return key_glyph(HOS_KEY_MOVE_UP, m);
    case 0xE058: return key_glyph(HOS_KEY_MOVE_RIGHT, m);
    case 0xE05A: return key_glyph(HOS_KEY_MOVE_LEFT, m);
    case 0xE054: return key_pair(HOS_KEY_MOVE_LEFT, HOS_KEY_MOVE_RIGHT, m);
    case 0xE05C: return key_pair(HOS_KEY_MOVE_UP, HOS_KEY_MOVE_DOWN, m);
    case 0xE05E: return cluster(HOS_KEY_MOVE_UP, HOS_KEY_MOVE_LEFT, HOS_KEY_MOVE_DOWN, HOS_KEY_MOVE_RIGHT, m);
    case 0xE064: return key_pair(HOS_SW_UP, HOS_SW_DOWN, m);
    case 0xE065: return key_pair(HOS_SW_LEFT, HOS_SW_RIGHT, m);
    case 0xE049: return cluster(HOS_SW_UP, HOS_SW_LEFT, HOS_SW_DOWN, HOS_SW_RIGHT, m);
    /* the right stick is the camera: the mouse, with its motion */
    case 0xE05F: case 0xE068: return one(0x03000000u, m);
    case 0xE053: return one(0x03000001u, m);
    case 0xE051: return one(0x03000002u, m);
    case 0xE05B: return one(0x03000003u, m);
    case 0xE059: return one(0x03000004u, m);
    case 0xE05D: return one(0x03000005u, m);
    case 0xE055: return one(0x03000006u, m);
    default: return 0;
    }
}

/* ---- the font ---- */
typedef struct { uint32_t tag; const uint8_t *data; uint32_t len; } Table;

static int tables_of(const uint8_t *otf, size_t size, Table *t, int max) {
    if (size < 12) return 0;
    const int n = be16(otf + 4);
    if (n > max || 12 + (size_t)n * 16 > size) return 0;
    for (int i = 0; i < n; i++) {
        const uint8_t *e = otf + 12 + i * 16;
        t[i].tag = be32(e), t[i].len = be32(e + 12);
        const uint32_t off = be32(e + 8);
        if ((size_t)off + t[i].len > size) return 0;
        t[i].data = otf + off;
    }
    return n;
}

static Table *table(Table *t, int n, const char *tag) {
    const uint32_t want = (uint32_t)tag[0] << 24 | (uint32_t)tag[1] << 16 | (uint32_t)tag[2] << 8 | (uint32_t)tag[3];
    for (int i = 0; i < n; i++)
        if (t[i].tag == want) return &t[i];
    return NULL;
}

/* a CFF INDEX at at: its count, offset size, the data's start minus 1 and its end; 0 if it does not fit */
static int cff_index(const uint8_t *c, size_t len, size_t at, uint32_t *count, int *osz, size_t *base, size_t *end) {
    if (at + 2 > len) return 0;
    *count = be16(c + at);
    if (!*count) { *osz = 0, *base = at + 2, *end = at + 2; return 1; }
    if (at + 3 > len) return 0;
    *osz = c[at + 2];
    if (*osz < 1 || *osz > 4 || at + 3 + (size_t)(*count + 1) * (size_t)*osz > len) return 0;
    *base = at + 3 + (size_t)(*count + 1) * (size_t)*osz - 1;
    uint32_t last = 0;
    for (int k = 0; k < *osz; k++) last = last << 8 | c[at + 3 + (size_t)*count * (size_t)*osz + k];
    *end = *base + last;
    return *end <= len;
}

static uint32_t index_off(const uint8_t *c, size_t at, int osz, uint32_t k) {
    uint32_t v = 0;
    for (int i = 0; i < osz; i++) v = v << 8 | c[at + 3 + (size_t)k * (size_t)osz + i];
    return v;
}

/* the Top DICT's CharStrings operand (29 + int32, nx2/button_icons.py fixed_offsets): where its four bytes are */
static size_t charstrings_operand(const uint8_t *d, size_t n, size_t data_at) {
    size_t i = 0, last5 = 0;
    int last_was5 = 0;
    while (i < n) {
        const uint8_t b = d[i];
        if (b == 29) last5 = i + 1, last_was5 = 1, i += 5;
        else if (b == 28) last_was5 = 0, i += 3;
        else if (b == 30) {
            last_was5 = 0, i++;
            while (i < n && (d[i] >> 4) != 0xF && (d[i] & 0xF) != 0xF) i++;
            i++;
        } else if (b >= 32 && b <= 246) last_was5 = 0, i++;
        else if (b >= 247 && b <= 254) last_was5 = 0, i += 2;
        else if (b == 12) i += 2;
        else {
            if (b == 17) return last_was5 ? data_at + last5 : 0;
            i++;
        }
    }
    return 0;
}

static void write_sfnt(Buf *out, const uint8_t *orig_header, Table *t, int n) {
    uint8_t head[12];
    memcpy(head, orig_header, 12);
    put(out, head, 12);
    size_t at = 12 + (size_t)n * 16;
    const size_t dir = out->n;
    uint8_t zero[16] = {0};
    for (int i = 0; i < n; i++) put(out, zero, 16);
    for (int i = 0; i < n; i++) {
        uint32_t sum = 0;
        for (uint32_t k = 0; k < t[i].len; k += 4) {
            uint8_t w[4] = {0};
            memcpy(w, t[i].data + k, t[i].len - k < 4 ? t[i].len - k : 4);
            sum += be32(w);
        }
        uint8_t *e = out->p + dir + (size_t)i * 16;
        put32(e, t[i].tag), put32(e + 4, sum), put32(e + 8, (uint32_t)at), put32(e + 12, t[i].len);
        put(out, t[i].data, t[i].len);
        const uint32_t pad = (4 - t[i].len % 4) % 4;
        put(out, zero, pad);
        at += t[i].len + pad;
    }
}

/* the font with the glyphs of family fam: Buf filled, how many glyphs were changed; -1 on a font it cannot read */
static int remake(const uint8_t *otf, size_t size, const uint16_t *gids, uint32_t cp_first, uint32_t cp_count, int fam,
                  Buf *out) {
    Table t[32];
    const int n = tables_of(otf, size, t, 32);
    Table *cff = table(t, n, "CFF "), *hmtx = table(t, n, "hmtx"), *hhea = table(t, n, "hhea"), *maxp = table(t, n, "maxp");
    if (!cff || !hmtx || !hhea || !maxp || hhea->len < 36 || maxp->len < 6) return -1;
    const uint8_t *c = cff->data;
    const size_t clen = cff->len;
    uint32_t cnt, ccount;
    int osz, cosz;
    size_t base, top_at, top_base, top_end, cs_at, cs_base, cs_end;
    if (!cff_index(c, clen, c[2], &cnt, &osz, &base, &top_at)) return -1;          /* (the Name INDEX) */
    if (!cff_index(c, clen, top_at, &cnt, &osz, &top_base, &top_end) || cnt != 1) return -1;
    const size_t dict_at = top_base + index_off(c, top_at, osz, 0);
    const size_t dict_end = top_base + index_off(c, top_at, osz, 1);
    const size_t operand = charstrings_operand(c + dict_at, dict_end - dict_at, dict_at);
    if (!operand) return -1;
    cs_at = be32(c + operand);
    if (!cff_index(c, clen, cs_at, &ccount, &cosz, &cs_base, &cs_end)) return -1;
    const uint32_t nglyphs = be16(maxp->data + 4);
    if (ccount != nglyphs) return -1;

    /* the new glyphs */
    Made *made = (Made *)calloc(nglyphs, sizeof *made);
    int changed = 0;
    for (uint32_t i = 0; i < cp_count; i++) {
        const uint16_t g = gids[i];
        if (!g || g >= nglyphs || made[g].cs.n) continue;
        if (glyph_for(fam, cp_first + i, &made[g])) changed++;
        else made[g].cs.n = 0;
    }

    /* the CharStrings INDEX, at the end of the CFF */
    Buf idx = {0};
    uint8_t h[3] = {(uint8_t)(nglyphs >> 8), (uint8_t)nglyphs, 4};
    put(&idx, h, 3);
    uint32_t off = 1;
    uint8_t w[4];
    put32(w, off), put(&idx, w, 4);
    for (uint32_t g = 0; g < nglyphs; g++) {
        off += made[g].cs.n ? (uint32_t)made[g].cs.n
                            : index_off(c, cs_at, cosz, g + 1) - index_off(c, cs_at, cosz, g);
        put32(w, off), put(&idx, w, 4);
    }
    for (uint32_t g = 0; g < nglyphs; g++) {
        if (made[g].cs.n) put(&idx, made[g].cs.p, made[g].cs.n);
        else put(&idx, c + cs_base + index_off(c, cs_at, cosz, g), index_off(c, cs_at, cosz, g + 1) - index_off(c, cs_at, cosz, g));
    }
    Buf newcff = {0};
    put(&newcff, c, clen);
    while (newcff.n % 4) put_byte(&newcff, 0);
    put32(newcff.p + operand, (uint32_t)newcff.n);
    put(&newcff, idx.p, idx.n);

    /* hmtx: every glyph its own width */
    const uint32_t nhm = be16(hhea->data + 34);
    Buf newhmtx = {0};
    for (uint32_t g = 0; g < nglyphs; g++) {
        uint8_t e[4];
        const uint32_t src = g < nhm ? g : nhm - 1;
        const uint16_t adv = be16(hmtx->data + src * 4);
        const uint16_t lsb = g < nhm ? be16(hmtx->data + g * 4 + 2) : be16(hmtx->data + nhm * 4 + (g - nhm) * 2);
        put16(e, made[g].cs.n ? (uint32_t)made[g].width : adv);
        put16(e + 2, made[g].cs.n ? SIDE : lsb);
        put(&newhmtx, e, 4);
    }
    uint8_t newhhea[64];
    memcpy(newhhea, hhea->data, hhea->len < sizeof newhhea ? hhea->len : sizeof newhhea);
    put16(newhhea + 34, nglyphs);
    cff->data = newcff.p, cff->len = (uint32_t)newcff.n;
    hmtx->data = newhmtx.p, hmtx->len = (uint32_t)newhmtx.n;
    hhea->data = newhhea;
    write_sfnt(out, otf, t, n);
    for (uint32_t g = 0; g < nglyphs; g++) free(made[g].cs.p);
    free(made), free(idx.p), free(newcff.p), free(newhmtx.p);
    return changed;
}

/* ---- the family ---- */
static int option_from_file(void) {   /* button_icons= in hookshot_options.ini in the game folder, -1 none */
    char path[MAX_PATH];
    DWORD n = hos_game_path_a(path, sizeof path);
    while (n && path[n - 1] != '\\' && path[n - 1] != '/') n--;
    snprintf(path + n, sizeof path - n, "hookshot_options.ini");
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char line[128];
    int v = -1;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "button_icons=", 13)) v = atoi(line + 13);
    fclose(f);
    return v;
}

static int choose(const char **why) {
    const char *e = getenv("HOS_BUTTON_ICONS");
    if (e && *e) {
        *why = "HOS_BUTTON_ICONS";
        if (!_stricmp(e, "keyboard")) return FAM_KEYS;
        if (!_stricmp(e, "xbox")) return FAM_XBOX;
        if (!_stricmp(e, "ps4")) return FAM_PS4;
        if (!_stricmp(e, "ps5") || !_stricmp(e, "playstation")) return FAM_PS5;
        if (!_stricmp(e, "switch")) return FAM_SWITCH;
    }
    const int opt = option_from_file();
    if (opt >= 1 && opt <= 4) {
        *why = "the menu's Button icons";
        if (opt == 3) {   /* PlayStation: a PS4 pad's names if one is connected, else the PS5's */
            const int f = hos_sdl_pad_family(1500);
            return f == FAM_PS4 ? FAM_PS4 : FAM_PS5;
        }
        return opt == 1 ? FAM_KEYS : opt == 2 ? FAM_XBOX : FAM_SWITCH;
    }
    const int f = hos_sdl_pad_family(1500);
    *why = f ? "the controller connected at the start" : "no controller connected at the start";
    return f;
}

/* ---- the zstd frame: raw blocks ---- */
static void zstd_raw(Buf *out, const uint8_t *d, size_t n) {
    const uint8_t hdr[5] = {0x28, 0xB5, 0x2F, 0xFD, 0xA0};   /* single segment, a four-byte content size, no checksum */
    put(out, hdr, 5);
    uint8_t sz[4] = {(uint8_t)n, (uint8_t)(n >> 8), (uint8_t)(n >> 16), (uint8_t)(n >> 24)};
    put(out, sz, 4);
    size_t at = 0;
    do {
        const size_t len = n - at > 131072 ? 131072 : n - at;
        const uint32_t bh = (uint32_t)(len << 3) | (at + len == n ? 1u : 0u);   /* raw, the last one marked */
        uint8_t b[3] = {(uint8_t)bh, (uint8_t)(bh >> 8), (uint8_t)(bh >> 16)};
        put(out, b, 3);
        put(out, d + at, len);
        at += len;
    } while (at < n);
}

/* ---- the library file, read once ---- */
typedef struct { char path[MAX_PATH]; uint32_t before; const uint8_t *data; uint32_t len; char served[MAX_PATH]; } SizeTable;
static uint8_t *bin;
static size_t bin_size;
static char game_dir[MAX_PATH];
static uint32_t font_key, font_size, cp_first, cp_count;
static size_t font_at, gids_at;
static SizeTable tables[4];
static int ntables;

int hos_mods_romfs(const char *rel, char *out, size_t cap);   /* mods.c */

/* hookshot_icons.bin read and checked; 0 (and said why) when it cannot be used */
static int load_bin(void) {
    const char *e = getenv("HOS_BUTTON_ICONS");
    if (e && !strcmp(e, "0")) return 0;
    DWORD n = hos_game_path_a(game_dir, sizeof game_dir);
    while (n && game_dir[n - 1] != '\\' && game_dir[n - 1] != '/') n--;
    game_dir[n] = 0;
    char path[MAX_PATH + 32];
    snprintf(path, sizeof path, "%shookshot_icons.bin", game_dir);
    FILE *f = fopen(path, "rb");
    if (!f) {
        hos_log("button icons: no hookshot_icons.bin in the game folder (the builder makes it): the game's own");
        return 0;
    }
    fseek(f, 0, SEEK_END);
    const long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    bin = (uint8_t *)malloc(size > 0 ? (size_t)size : 1);
    const size_t got = size > 0 ? fread(bin, 1, (size_t)size, f) : 0;
    fclose(f);
    bin_size = got;
    uint32_t h[8];
    if (got != (size_t)size || got < 36 || memcmp(bin, "HKIC", 4)) {
        hos_log("button icons: %s is not one (the game's own)", path);
        return 0;
    }
    memcpy(h, bin + 4, sizeof h);
    if (h[0] != 2) {
        hos_log("button icons: %s is of another version (%u; this one reads 2): the game's own", path, h[0]);
        return 0;
    }
    font_key = h[1], font_size = h[2], lib_n = h[3], cp_first = h[4], cp_count = h[5];
    font_at = 36, gids_at = font_at + ((font_size + 3) & ~(size_t)3);
    const size_t lib_at = gids_at + ((cp_count * 2 + 3) & ~(size_t)3);
    const size_t cs_at = lib_at + (size_t)lib_n * sizeof(LibGlyph);
    size_t at = h[6];
    if (cs_at > got || at > got) {
        hos_log("button icons: %s is cut short: the game's own", path);
        return 0;
    }
    lib = (const LibGlyph *)(bin + lib_at), lib_cs = bin + cs_at;
    for (uint32_t i = 0; i < lib_n; i++)
        if (cs_at + lib[i].off + lib[i].len > at) {
            hos_log("button icons: %s is cut short: the game's own", path);
            return 0;
        }
    for (uint32_t i = 0; i < h[7] && ntables < 4; i++) {   /* the resource size tables, the font's entry raised */
        if (at + 4 > got) return 0;
        uint32_t plen;
        memcpy(&plen, bin + at, 4);
        at += 4;
        if (plen >= MAX_PATH || at + plen > got) return 0;
        SizeTable *t = &tables[ntables];
        memcpy(t->path, bin + at, plen);
        t->path[plen] = 0;
        at += (plen + 3) & ~3u;
        if (at + 8 > got) return 0;
        memcpy(&t->before, bin + at, 4);
        memcpy(&t->len, bin + at + 4, 4);
        at += 8;
        if (at + t->len > got) return 0;
        t->data = bin + at;
        at += (t->len + 3) & ~3u;
        ntables++;
    }
    if (!ntables) {
        hos_log("button icons: %s has no resource size table (an older builder): the game's own", path);
        return 0;
    }
    return 1;
}

/* ---- nn_fs.c ---- */
static SRWLOCK made_lock = SRWLOCK_INIT;
static volatile LONG ready;      /* 0 not looked, 1 icons on, -1 off */
static int made_state;           /* the font: 0 not made, 1 made, -1 none */
static char made_path[MAX_PATH + 32];

/* a file the game gets in place of one of its own: the data in a zstd frame of raw blocks, written in the game folder */
static int write_zs(const char *name, const uint8_t *d, size_t n, char *path, size_t cap) {
    Buf zs = {0};
    zstd_raw(&zs, d, n);
    snprintf(path, cap, "%s%s", game_dir, name);
    FILE *f = fopen(path, "wb");
    const int ok = f && fwrite(zs.p, 1, zs.n, f) == zs.n;
    if (f) fclose(f);
    free(zs.p);
    if (!ok) hos_log("button icons: could not write %s", path);
    return ok;
}

static int make_font(void) {
    const char *why = "";
    const int fam = choose(&why);
    Buf otf = {0};
    const int changed = remake(bin + font_at, font_size, (const uint16_t *)(bin + gids_at), cp_first, cp_count, fam, &otf);
    if (changed < 0) {
        hos_log("button icons: the font in hookshot_icons.bin could not be read: the game's own");
        free(otf.p);
        return 0;
    }
    while (otf.n % 4) put_byte(&otf, 0);   /* (obfuscated as the game's, the font padded to four bytes) */
    /* a mod's size table is not ours: then the font only if it fits the game's own entry (288: the table's margin) */
    for (int i = 0; i < ntables; i++) {
        char mod[MAX_PATH];
        if (hos_mods_romfs(tables[i].path, mod, sizeof mod) && otf.n + 8 + 288 > tables[i].before) {
            hos_log("button icons: a mod's %s keeps the font at %u bytes, and this one is %zu: the game's own", tables[i].path,
                    tables[i].before, otf.n + 8);
            free(otf.p);
            return 0;
        }
    }
    Buf bf = {0};
    uint8_t h[8];
    put32(h, MAGIC ^ font_key), put32(h + 4, (uint32_t)otf.n ^ font_key);
    put(&bf, h, 8);
    uint8_t k[4];
    put32(k, font_key);
    for (size_t i = 0; i < otf.n; i++) otf.p[i] ^= k[i & 3];
    put(&bf, otf.p, otf.n);
    const int ok = write_zs("hookshot_icons_font.zs", bf.p, bf.n, made_path, sizeof made_path);
    if (ok) hos_log("button icons: %s (%s): %d glyphs, %zu bytes", fam_name[fam], why, changed, bf.n);
    free(otf.p), free(bf.p);
    return ok;
}

/* the file the game gets for a RomFS path ("Font2/...", no mount; after mods): 1 and out filled for the button icons' font
   and the resource size table that lets the game take it */
int hos_button_icons_romfs(const char *rel, char *out, size_t cap) {
    if (!ready) {
        AcquireSRWLockExclusive(&made_lock);
        if (!ready) InterlockedExchange(&ready, load_bin() ? 1 : -1);
        ReleaseSRWLockExclusive(&made_lock);
    }
    if (ready < 0) return 0;
    int i = 0;
    while (i < ntables && _stricmp(rel, tables[i].path)) i++;
    const int font = !_stricmp(rel, FONT_PATH);
    if (i == ntables && !font) return 0;
    AcquireSRWLockExclusive(&made_lock);
    int ok = 0;
    if (font) {
        if (!made_state) made_state = make_font() ? 1 : -1;
        if ((ok = made_state > 0)) snprintf(out, cap, "%s", made_path);
    } else {
        SizeTable *t = &tables[i];
        if (!t->served[0]) {
            char name[64];
            snprintf(name, sizeof name, "hookshot_icons_rstb%d.zs", i);
            if (write_zs(name, t->data, t->len, t->served, sizeof t->served))
                hos_log("button icons: %s with the font's buffer raised from %u bytes", t->path, t->before);
            else
                t->served[0] = 1;   /* (could not write: the game's own) */
        }
        if ((ok = t->served[0] > 1)) snprintf(out, cap, "%s", t->served);
    }
    ReleaseSRWLockExclusive(&made_lock);
    return ok;
}
