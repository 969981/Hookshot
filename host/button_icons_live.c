/* (hookshot) The button icons follow what is played with, without a restart (host/button_icons.c makes the font).

   The font the game was given holds every family's icons (keyboard and mouse, Xbox, PlayStation 4 and 5, Switch) and a
   cmap with a group of its own for each icon codepoint. The game's text engine (nn::fontll, FreeType 2.13.2) reads the
   font where the game loaded it, the cmap too, at each glyph it looks up. So switching families is writing, in the game's
   memory, each icon codepoint's group's glyph number: the font's "hkic" table (found by its marker, on a thread of its
   own the first time: host/camfind.c's heap search) says the families' glyphs and where each group's number is.

   When: with the Insert menu's Button icons on Auto, the last device played with (nn_hid.c: a controller's buttons or
   sticks, or a bound key or mouse button); F8 goes round the families whatever the setting (to see them). Glyphs the game
   drew before stay as they were until it draws them again (its glyph cache): how soon that is, is what this is for to
   find out. HOS_BUTTON_ICONS_LIVE=0 leaves the icons as they were at the start.

   The font may be in memory as the file has it, XORed with its key (bfotf: the game may read it through that), as well
   as plain: both are looked for, and a copy found XORed is read and written XORed. The first test found neither (0
   copies); the log says now which, if any, was found. A search that finds nothing is not run again within 10 s. */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int hos_button_icons_started(int *follow);                                       /* button_icons.c */
uint32_t hos_button_icons_key(void);                                             /* button_icons.c */
size_t hos_heap_find(const void *pat, size_t len, uint64_t *out, size_t max);    /* camfind.c */
int hos_sdl_pad_family(int wait_ms);                                             /* pad_sdl.c */
void hos_host_thread_register_self(const char *name);                            /* hos_main.c */

#define MARKER "HOOKSHOT-ICONS-1"
enum { FAMILIES = 5, MAX_COPIES = 4 };
static const char *const names[FAMILIES] = {"keyboard and mouse", "Xbox", "PlayStation 4", "PlayStation 5", "Switch"};

typedef struct { uint64_t at; uint32_t key; } Copy;   /* key: 0 for a plain copy, the font's key for one as the file has it */
static Copy copies[MAX_COPIES];   /* where the hkic tables are (the font may be in memory more than once) */
static volatile LONG ncopies, searching, wanted = -1, current = -1;
static volatile LONG64 searched_at;   /* when a search that found nothing ended (hos_now_ns), 0: none */
static SRWLOCK lock = SRWLOCK_INIT;

/* the byte at x of the hkic table at c (sfnt tables are 4-aligned in the font, so x - c.at's low bits are the key's byte) */
static uint8_t kb(const Copy *c, uint64_t x) { return (uint8_t)(c->key >> (8 * (3 - ((x - c->at) & 3)))); }
static uint32_t rd32(const Copy *c, uint64_t x) {
    const uint8_t *p = (const uint8_t *)GP(x);
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v = v << 8 | (uint8_t)(p[i] ^ kb(c, x + i));
    return v;
}
static uint16_t rd16(const Copy *c, uint64_t x) {
    const uint8_t *p = (const uint8_t *)GP(x);
    return (uint16_t)((p[0] ^ kb(c, x)) << 8 | (p[1] ^ kb(c, x + 1)));
}
static void wr32(const Copy *c, uint64_t x, uint32_t v) {
    uint8_t *p = (uint8_t *)GP(x);
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)((v >> (8 * (3 - i))) ^ kb(c, x + i));
}

static void marker_as(uint32_t key, uint8_t out[16]) {   /* the marker as a copy XORed with key holds it */
    for (int i = 0; i < 16; i++) out[i] = (uint8_t)(MARKER[i] ^ (key >> (8 * (3 - (i & 3)))));
}

static int valid(const Copy *c) {
    uint8_t m[16];
    marker_as(c->key, m);
    return c->at && !memcmp(GP(c->at), m, 16) && rd32(c, c->at + 16) == FAMILIES && rd32(c, c->at + 24) <= 64;
}

/* every icon codepoint of the hkic at a to family f's glyph */
static void point(const Copy *c, int f) {
    const uint64_t a = c->at;
    const uint32_t n = rd32(c, a + 24);
    const uint64_t gl = a + 32, rel = gl + ((FAMILIES * n * 2 + 3) & ~3u);
    for (uint32_t i = 0; i < n; i++) {
        const int32_t r = (int32_t)rd32(c, rel + i * 4);
        if (r) wr32(c, (uint64_t)((int64_t)a + r), rd16(c, gl + ((uint64_t)f * n + i) * 2));
    }
    wr32(c, a + 28, (uint32_t)f);
}

static void apply(int f) {   /* (lock held) */
    int done = 0;
    for (LONG i = 0; i < ncopies; i++)
        if (valid(&copies[i])) point(&copies[i], f), done++;
    if (done) {
        InterlockedExchange(&current, f);
        hos_log("button icons: now %s (%d %s)", names[f], done, done == 1 ? "copy of the font" : "copies of the font");
    }
}

static DWORD WINAPI search(void *arg) {
    (void)arg;
    hos_host_thread_register_self("button icons (search)");
    const int64_t t0 = hos_now_ns();
    const uint32_t key = hos_button_icons_key();
    uint64_t at[2][MAX_COPIES];
    size_t n[2] = {hos_heap_find(MARKER, 16, at[0], MAX_COPIES), 0};
    if (key) {
        uint8_t m[16];
        marker_as(key, m);
        n[1] = hos_heap_find(m, 16, at[1], MAX_COPIES);
    }
    AcquireSRWLockExclusive(&lock);
    LONG k = 0, kind[2] = {0, 0};
    for (int x = 0; x < 2; x++)
        for (size_t i = 0; i < n[x] && k < MAX_COPIES; i++) {
            const Copy c = {at[x][i], x ? key : 0};
            if (valid(&c)) copies[k++] = c, kind[x]++;
        }
    InterlockedExchange(&ncopies, k);
    hos_log("button icons: the font found in the game's memory %ld time(s) plain, %ld time(s) as the file has it (XORed), "
            "in %.0f ms", (long)kind[0], (long)kind[1], (hos_now_ns() - t0) / 1e6);
    if (k && wanted >= 0) apply((int)wanted);
    InterlockedExchange64(&searched_at, k ? 0 : hos_now_ns());
    ReleaseSRWLockExclusive(&lock);
    InterlockedExchange(&searching, 0);
    return 0;
}

static void want(int f) {
    static int off = -1;
    if (off < 0) {
        const char *e = getenv("HOS_BUTTON_ICONS_LIVE");
        off = e && !strcmp(e, "0");
    }
    int follow;
    const int start = hos_button_icons_started(&follow);
    if (off || start < 0 || f < 0 || f >= FAMILIES) return;
    if (current < 0) InterlockedCompareExchange(&current, start, -1);
    InterlockedExchange(&wanted, f);
    if (f == current) return;
    AcquireSRWLockExclusive(&lock);
    int ok = 0;
    for (LONG i = 0; i < ncopies && !ok; i++) ok = valid(&copies[i]);
    if (ok) apply(f);
    ReleaseSRWLockExclusive(&lock);
    const int64_t last = searched_at;   /* (a search found nothing: not again for 10 s, each is a second of a core) */
    if (!ok && (!last || hos_now_ns() - last >= 10000000000ll) && !InterlockedExchange(&searching, 1))
        if (!CreateThread(NULL, 0, search, NULL, 0, NULL)) InterlockedExchange(&searching, 0);
}

/* nn_hid.c, with each read: whether a controller and whether the keyboard or mouse was played with, and F8 */
void hos_button_icons_input(int pad, int keys, int f8) {
    static int last = -1, f8_was;
    if (f8 && !f8_was) {
        const int c = wanted >= 0 ? (int)wanted : current;
        want(((c < 0 ? 0 : c) + 1) % FAMILIES);
    }
    f8_was = f8;
    int follow;
    if (hos_button_icons_started(&follow) < 0 || !follow) return;
    if (pad && last != 1) {
        last = 1;
        const int f = hos_sdl_pad_family(0);
        want(f ? f : 1);   /* (an XInput pad SDL has not opened: Xbox's) */
    } else if (keys && !pad && last != 0) {
        last = 0;
        want(0);
    }
}
