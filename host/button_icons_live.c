/* (hookshot) The button icons follow what is played with, without a restart (host/button_icons.c makes the font).

   The font the game was given holds every family's icons (keyboard and mouse, Xbox, PlayStation 4 and 5, Switch) and a
   cmap with a group of its own for each icon codepoint. The game's text engine (nn::fontll, FreeType 2.13.2) reads the
   font where the game loaded it, the cmap too, at each glyph it looks up. So switching families is writing, in the game's
   memory, each icon codepoint's group's glyph number: the font's "hkic" table (found by its marker, on a thread of its
   own the first time: host/camfind.c's heap search) says the families' glyphs and where each group's number is.

   When: with the Insert menu's Button icons on Auto, the last device played with (nn_hid.c: a controller's buttons or
   sticks, or a bound key or mouse button); F8 goes round the families whatever the setting (to see them). Glyphs the game
   drew before stay as they were until it draws them again (its glyph cache): how soon that is, is what this is for to
   find out. HOS_BUTTON_ICONS_LIVE=0 leaves the icons as they were at the start. */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int hos_button_icons_started(int *follow);                                       /* button_icons.c */
size_t hos_heap_find(const void *pat, size_t len, uint64_t *out, size_t max);    /* camfind.c */
int hos_sdl_pad_family(int wait_ms);                                             /* pad_sdl.c */
void hos_host_thread_register_self(const char *name);                            /* hos_main.c */

#define MARKER "HOOKSHOT-ICONS-1"
enum { FAMILIES = 5, MAX_COPIES = 4 };
static const char *const names[FAMILIES] = {"keyboard and mouse", "Xbox", "PlayStation 4", "PlayStation 5", "Switch"};

static uint64_t copies[MAX_COPIES];   /* where the hkic tables are (the font may be in memory more than once) */
static volatile LONG ncopies, searching, wanted = -1, current = -1;
static SRWLOCK lock = SRWLOCK_INIT;

static uint32_t rd32(uint64_t a) { const uint8_t *p = (const uint8_t *)GP(a); return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }
static uint16_t rd16(uint64_t a) { const uint8_t *p = (const uint8_t *)GP(a); return (uint16_t)(p[0] << 8 | p[1]); }
static void wr32(uint64_t a, uint32_t v) { uint8_t *p = (uint8_t *)GP(a); p[0] = (uint8_t)(v >> 24), p[1] = (uint8_t)(v >> 16), p[2] = (uint8_t)(v >> 8), p[3] = (uint8_t)v; }

static int valid(uint64_t a) {
    return a && !memcmp(GP(a), MARKER, 16) && rd32(a + 16) == FAMILIES && rd32(a + 24) <= 64;
}

/* every icon codepoint of the hkic at a to family f's glyph */
static void point(uint64_t a, int f) {
    const uint32_t n = rd32(a + 24);
    const uint64_t gl = a + 32, rel = gl + ((FAMILIES * n * 2 + 3) & ~3u);
    for (uint32_t i = 0; i < n; i++) {
        const int32_t r = (int32_t)rd32(rel + i * 4);
        if (r) wr32((uint64_t)((int64_t)a + r), rd16(gl + ((uint64_t)f * n + i) * 2));
    }
    wr32(a + 28, (uint32_t)f);
}

static void apply(int f) {   /* (lock held) */
    int done = 0;
    for (LONG i = 0; i < ncopies; i++)
        if (valid(copies[i])) point(copies[i], f), done++;
    if (done) {
        InterlockedExchange(&current, f);
        hos_log("button icons: now %s (%d %s)", names[f], done, done == 1 ? "copy of the font" : "copies of the font");
    }
}

static DWORD WINAPI search(void *arg) {
    (void)arg;
    hos_host_thread_register_self("button icons (search)");
    const int64_t t0 = hos_now_ns();
    uint64_t at[MAX_COPIES];
    const size_t n = hos_heap_find(MARKER, 16, at, MAX_COPIES);
    AcquireSRWLockExclusive(&lock);
    LONG k = 0;
    for (size_t i = 0; i < n; i++)
        if (valid(at[i])) copies[k++] = at[i];
    InterlockedExchange(&ncopies, k);
    hos_log("button icons: the font found in the game's memory %ld time(s) in %.0f ms", (long)k, (hos_now_ns() - t0) / 1e6);
    if (k && wanted >= 0) apply((int)wanted);
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
    for (LONG i = 0; i < ncopies && !ok; i++) ok = valid(copies[i]);
    if (ok) apply(f);
    ReleaseSRWLockExclusive(&lock);
    if (!ok && !InterlockedExchange(&searching, 1))   /* (not found yet, or the game moved it: look again) */
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
