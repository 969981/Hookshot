/* (hookshot) Ocarina of Time's ultrawide loading cover (MkwVkHost.wide_cover), compiled into its runtime only (packs/oot/pack.json "host"; nx2/build.py). Cut out of
   host/hos_main.c (docs/game-packs.md a3): the code is as it was there. */
#include "nx2_pack.h"
#include "oot_host.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern volatile float hos_wide_squeeze;   /* (host/hos_main.c: the widescreen patches' picture) */
extern volatile uint32_t hos_wide_keep_mask;
/* The loading cover between two areas (the game's UILoadingFadeScreen, KEEP_VTABLES[3]): a black picture over the whole
   3840 x 2160 UI grid, and an icon. Drawn in the middle 16:9 with the menus, it leaves the sides open, and the game draws
   the new area under it for some frames before it opens: the sides showed that area, overbright at first, beside a black
   middle (issue 2220). The renderer closes the sides by as much as the cover is closed (MkwVkHost.wide_cover), which is
   read here from the screen's layout. The layout ([[screen + 0x28] + 0x80]) has its panes at [+0x100], 0xb0 bytes each
   (the layout again at +0x18, the size at +0x58, the opacity at +0x74), and their tree at [+0x110], 8 bytes a pane with
   the parent's index at +2. Pane 2 is the black picture, under pane 1 (the one the fade animates) under the root, and
   what it is drawn with is the three opacities multiplied. Anything else found there, as another game version would
   have: 0, the sides as they were. */
volatile uint64_t hos_wide_cover_screen;   /* (nx2/oot_wide.py's HUD site: the cover's screen, a guest address, noted as its projection is set up) */
static int wide_cover_read(uint64_t a, void *out, uint64_t n) {
    /* (the first MB is never mapped: a null pointer in the chain, and what would hang off it, is no layout) */
    const void *p = a >= 0x100000 ? hos_guest_range(a, n) : NULL;
    if (p) memcpy(out, p, n);
    return p != NULL;
}
float hos_wide_cover(void) {
    enum { PANE = 0xb0, COVER = 2 };
    const float squeeze = hos_wide_squeeze;
    const uint64_t screen = hos_wide_cover_screen;
    /* (a 16:9 or narrower picture has no sides; with bit 3 kept the cover is drawn over the whole picture) */
    if (!(squeeze > 0.0f && squeeze < 1.0f) || (hos_wide_keep_mask & 8) || !screen) return 0.0f;
    uint64_t vtable, holder, layout, panes, tree, owner;
    float size[2], level = 1.0f;
    uint16_t parent, grandparent;
    if (!wide_cover_read(screen, &vtable, 8) || vtable != 0x80020000ULL + 0x4be7b30 || !wide_cover_read(screen + 0x28, &holder, 8) ||
        !wide_cover_read(holder + 0x80, &layout, 8) || !wide_cover_read(layout + 0x100, &panes, 8) ||
        !wide_cover_read(layout + 0x110, &tree, 8))
        return 0.0f;
    if (!wide_cover_read(panes + COVER * PANE + 0x18, &owner, 8) || owner != layout ||
        !wide_cover_read(panes + COVER * PANE + 0x58, size, 8) || size[0] != 3840.0f || size[1] != 2160.0f ||
        !wide_cover_read(tree + COVER * 8 + 2, &parent, 2) || parent != 1 || !wide_cover_read(tree + 1 * 8 + 2, &grandparent, 2) ||
        grandparent != 0)
        return 0.0f;
    for (int k = 0; k <= COVER; k++) {
        float opacity;
        if (!wide_cover_read(panes + k * PANE + 0x74, &opacity, 4) || !(opacity >= 0.0f && opacity <= 1.0f)) return 0.0f;
        level *= opacity;
    }
    return level;
}
