/* nn::pl: the platform font library. The console's built-in fonts (nintendo_*_003.bfttf) come from the pl system
   service, which has no host counterpart and is not in the game's dump: the game's own fonts are compressed .bfotf
   files inside Font/*.bfarc.zs. GetBuiltInFontData fills BuiltInFontDataOunce { u8 flags[2]; pad[6]; void *data; u32 size; }
   (the sdk's real code, sdk module 0x854ca2b0), so a host TrueType font stands in for the built-in one. The sead font
   layer parses the returned blob itself (ttcf/OTTO/tfcs), so any plain .ttf/.ttc works.
   HOS_PL_FONT: the font file (default: segoeui.ttf, then arial.ttf from the Windows fonts folder; "none" returns
   no font, the way a console without the requested language would). */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t font_addr, font_size;
static int font_tried;

static int read_file(const char *path, char **data, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *p = n > 0 && n <= 0x01000000 ? (char *)malloc((size_t)n) : NULL;
    if (p && fread(p, 1, (size_t)n, f) == (size_t)n) {
        *data = p;
        *size = (size_t)n;
        fclose(f);
        return 1;
    }
    free(p);
    fclose(f);
    return 0;
}

static void load_font(void) {
    if (font_tried) return;
    font_tried = 1;
    const char *path = getenv("HOS_PL_FONT");
    const char *fallbacks[] = {"C:\\Windows\\Fonts\\segoeui.ttf", "C:\\Windows\\Fonts\\arial.ttf"};
    for (int i = 0; path || i < 2; i++) {
        const char *p = path ? path : fallbacks[i];
        if (!*p || !_stricmp(p, "none")) break;
        char *data = NULL;
        size_t n = 0;
        if (read_file(p, &data, &n)) {
            font_addr = hos_sys_alloc(n, 0x1000);
            memcpy(GP(font_addr), data, n);
            hos_mark_written(GP(font_addr), n);
            font_size = n;
            free(data);
            hos_log("pl: built-in font %s (%zu bytes) at %#llx", p, n, (unsigned long long)font_addr);
            return;
        }
        if (path) break;
    }
    hos_log("pl: no built-in font (set HOS_PL_FONT to a .ttf/.ttc)");
}

HLE(_ZN2nn2pl18GetBuiltInFontDataEPNS0_20BuiltInFontDataOunceENS0_20BuiltInFontTypeOunceEb) {
    uint8_t *out = (uint8_t *)GP(ARG(0));
    load_font();
    if (out) {
        memset(out, 0, 0x20);
        if (font_addr) {
            memcpy(out + 8, &font_addr, 8);
            const uint32_t size = (uint32_t)font_size;
            memcpy(out + 16, &size, 4);
        }
    }
    RET(0);
}
