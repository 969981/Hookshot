// (hookshot) Linux: the GDI the Insert menu draws with (vk_menu.cpp), over a 32-bit top-down bitmap, its text by
// FreeType (libfreetype.so.6, loaded when first needed) in the desktop's sans font (fontconfig's fc-match, else the
// usual DejaVu / Noto / Liberation files). Without FreeType the boxes are drawn and the text is not. Only what
// vk_menu.cpp calls, with GDI's meanings: COLORREF 0x00BBGGRR, pixels B G R X, a font's height negative for its em
// size, TA_TOP text placed by its cell's top.
#pragma once
#ifndef _WIN32
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#ifndef VK_GDI_NO_FREETYPE
#include <ft2build.h>
#include FT_FREETYPE_H
#endif

typedef uint32_t COLORREF;
#define RGB(r, g, b) ((COLORREF)(((uint32_t)(uint8_t)(r)) | ((uint32_t)(uint8_t)(g) << 8) | ((uint32_t)(uint8_t)(b) << 16)))
struct SIZE { LONG cx, cy; };
struct BITMAPINFOHEADER { DWORD biSize; LONG biWidth, biHeight; WORD biPlanes, biBitCount; DWORD biCompression, biSizeImage; LONG biXPelsPerMeter, biYPelsPerMeter; DWORD biClrUsed, biClrImportant; };
struct BITMAPINFO { BITMAPINFOHEADER bmiHeader; DWORD bmiColors[1]; };
#define BI_RGB 0
#define DIB_RGB_COLORS 0
#define TRANSPARENT 1
#define OPAQUE 2
#define TA_TOP 0
#define TA_LEFT 0
#define TA_RIGHT 2
#define FW_NORMAL 400
#define FW_SEMIBOLD 600
#define FW_BOLD 700
#define DEFAULT_CHARSET 1
#define OUT_DEFAULT_PRECIS 0
#define CLIP_DEFAULT_PRECIS 0
#define DEFAULT_PITCH 0
#define CLEARTYPE_QUALITY 5
#define NONANTIALIASED_QUALITY 3

namespace vk_gdi {
enum Kind { K_DC, K_BITMAP, K_FONT, K_BRUSH };
struct Obj { Kind kind; virtual ~Obj() {} };
struct Bitmap : Obj { int w = 0, h = 0; std::vector<uint32_t> px; };
struct Font : Obj { int px = 16; bool bold = false, aa = true; };
struct Brush : Obj { COLORREF c = 0; };
struct Dc : Obj { Bitmap* bmp = nullptr; Font* font = nullptr; COLORREF text = 0; unsigned align = 0; };

#ifndef VK_GDI_NO_FREETYPE
struct Ft {
    bool tried = false;
    FT_Library lib = nullptr;
    FT_Face face[2] = {};   // regular, bold
    decltype(&FT_Init_FreeType) init = nullptr;
    decltype(&FT_New_Face) new_face = nullptr;
    decltype(&FT_Set_Pixel_Sizes) set_size = nullptr;
    decltype(&FT_Load_Char) load_char = nullptr;
};
inline std::string font_file(bool bold) {
    std::string cmd = std::string("fc-match -f '%{file}' 'sans") + (bold ? ":bold'" : "'") + " 2>/dev/null";
    if (FILE* p = popen(cmd.c_str(), "r")) {
        char buf[1024] = "";
        const bool got = fgets(buf, sizeof buf, p) != nullptr;
        pclose(p);
        if (got && buf[0] == '/') return buf;
    }
    static const char* const fallback[2][4] = {
        {"/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/noto/NotoSans-Regular.ttf", "/usr/share/fonts/liberation/LiberationSans-Regular.ttf"},
        {"/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/noto/NotoSans-Bold.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"}};
    for (const char* f : fallback[bold])
        if (FILE* t = fopen(f, "rb")) { fclose(t); return f; }
    return "";
}
inline Ft& ft() {
    static Ft f;
    if (f.tried) return f;
    f.tried = true;
    void* so = dlopen("libfreetype.so.6", RTLD_NOW | RTLD_LOCAL);
    if (!so) return f;
    f.init = (decltype(f.init))dlsym(so, "FT_Init_FreeType");
    f.new_face = (decltype(f.new_face))dlsym(so, "FT_New_Face");
    f.set_size = (decltype(f.set_size))dlsym(so, "FT_Set_Pixel_Sizes");
    f.load_char = (decltype(f.load_char))dlsym(so, "FT_Load_Char");
    if (!f.init || !f.new_face || !f.set_size || !f.load_char || f.init(&f.lib)) { f.lib = nullptr; return f; }
    for (int b = 0; b < 2; b++) {
        const std::string path = font_file(b == 1);
        if (path.empty() || f.new_face(f.lib, path.c_str(), 0, &f.face[b])) f.face[b] = nullptr;
    }
    if (!f.face[1]) f.face[1] = f.face[0];
    if (!f.face[0]) f.face[0] = f.face[1];
    return f;
}
// pen: the text's start; draw: into the DC's bitmap, else only measured. Returns the advance.
inline int text_run(Dc* dc, int x, int y, const char* s, int n, bool draw) {
    Ft& f = ft();
    const Font* fo = dc->font;
    FT_Face face = f.face[fo && fo->bold];
    if (!face) return 0;
    f.set_size(face, 0, (FT_UInt)(fo ? fo->px : 16));
    const int ascender = (int)(face->size->metrics.ascender >> 6);
    int pen = 0;
    const uint32_t col = 0xFF000000u | (dc->text & 0xFF) << 16 | (dc->text & 0xFF00) | (dc->text >> 16 & 0xFF);
    for (int i = 0; i < n; i++) {
        if (f.load_char(face, (unsigned char)s[i], FT_LOAD_RENDER)) continue;
        const FT_GlyphSlot g = face->glyph;
        if (draw && dc->bmp) {
            Bitmap& b = *dc->bmp;
            const int gx = x + pen + g->bitmap_left, gy = y + ascender - g->bitmap_top;
            for (unsigned r = 0; r < g->bitmap.rows; r++)
                for (unsigned c = 0; c < g->bitmap.width; c++) {
                    const int px = gx + (int)c, py = gy + (int)r;
                    if (px < 0 || py < 0 || px >= b.w || py >= b.h) continue;
                    unsigned a = g->bitmap.buffer[r * (unsigned)g->bitmap.pitch + c];
                    if (fo && !fo->aa) a = a >= 128 ? 255 : 0;   // (hard edges: see-through pixels are copied or not)
                    if (!a) continue;
                    uint32_t& d = b.px[(size_t)py * b.w + px];
                    if (a == 255) { d = col; continue; }
                    uint32_t o = 0;
                    for (int k = 0; k < 24; k += 8)
                        o |= (((col >> k & 0xFF) * a + (d >> k & 0xFF) * (255 - a)) / 255) << k;
                    d = o | 0xFF000000u;
                }
        }
        pen += (int)(g->advance.x >> 6);
    }
    return pen;
}
#else
inline int text_run(Dc*, int, int, const char*, int, bool) { return 0; }
#endif
} // namespace vk_gdi

typedef vk_gdi::Dc* HDC;
typedef vk_gdi::Obj* HGDIOBJ;
typedef vk_gdi::Bitmap* HBITMAP;
typedef vk_gdi::Font* HFONT;
typedef vk_gdi::Brush* HBRUSH;

inline HDC CreateCompatibleDC(HDC) { auto* d = new vk_gdi::Dc; d->kind = vk_gdi::K_DC; return d; }
inline BOOL DeleteDC(HDC d) { delete d; return TRUE; }
inline HBITMAP CreateDIBSection(HDC, const BITMAPINFO* bi, UINT, void** bits, HANDLE, DWORD) {
    auto* b = new vk_gdi::Bitmap;
    b->kind = vk_gdi::K_BITMAP;
    b->w = bi->bmiHeader.biWidth;
    b->h = bi->bmiHeader.biHeight < 0 ? -bi->bmiHeader.biHeight : bi->bmiHeader.biHeight;   // (top-down asked: the only kind made)
    b->px.assign((size_t)b->w * b->h, 0);
    *bits = b->px.data();
    return b;
}
inline HGDIOBJ SelectObject(HDC d, HGDIOBJ o) {
    if (o->kind == vk_gdi::K_BITMAP) { HGDIOBJ old = d->bmp; d->bmp = (HBITMAP)o; return old; }
    if (o->kind == vk_gdi::K_FONT) { HGDIOBJ old = d->font; d->font = (HFONT)o; return old; }
    return nullptr;
}
inline BOOL DeleteObject(HGDIOBJ o) { delete o; return TRUE; }
inline BOOL GdiFlush() { return TRUE; }
inline HFONT CreateFontA(int height, int, int, int, int weight, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD quality,
                         DWORD, LPCSTR) {
    auto* f = new vk_gdi::Font;
    f->kind = vk_gdi::K_FONT;
    f->px = height < 0 ? -height : height ? height : 16;
    f->bold = weight >= FW_SEMIBOLD;
    f->aa = quality != NONANTIALIASED_QUALITY;
    return f;
}
inline HBRUSH CreateSolidBrush(COLORREF c) { auto* b = new vk_gdi::Brush; b->kind = vk_gdi::K_BRUSH; b->c = c; return b; }
inline int FillRect(HDC d, const RECT* r, HBRUSH br) {
    if (!d->bmp) return 0;
    vk_gdi::Bitmap& b = *d->bmp;
    const uint32_t c = 0xFF000000u | (br->c & 0xFF) << 16 | (br->c & 0xFF00) | (br->c >> 16 & 0xFF);
    for (int y = std::max<int>(r->top, 0); y < std::min<int>(r->bottom, b.h); y++)
        for (int x = std::max<int>(r->left, 0); x < std::min<int>(r->right, b.w); x++) b.px[(size_t)y * b.w + x] = c;
    return 1;
}
inline int SetBkMode(HDC, int) { return OPAQUE; }
inline COLORREF SetTextColor(HDC d, COLORREF c) { COLORREF old = d->text; d->text = c; return old; }
inline UINT SetTextAlign(HDC d, UINT a) { UINT old = d->align; d->align = a; return old; }
inline BOOL GetTextExtentPoint32A(HDC d, LPCSTR s, int n, SIZE* out) {
    out->cx = vk_gdi::text_run(d, 0, 0, s, n, false);
    out->cy = d->font ? d->font->px : 16;
    return TRUE;
}
inline BOOL TextOutA(HDC d, int x, int y, LPCSTR s, int n) {
    if (d->align & TA_RIGHT) x -= vk_gdi::text_run(d, 0, 0, s, n, false);
    vk_gdi::text_run(d, x, y, s, n, true);
    return TRUE;
}
#endif
