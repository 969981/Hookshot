// The Insert menu. Drawn on the CPU (GDI text into a bitmap) and copied over the window's image at the last step of a present,
// so it costs nothing while closed and it never touches the game's picture, nor what frame interpolation sees. Keys: Insert
// opens and closes it, Up and Down choose, Left and Right change, Esc closes; the game gets no keyboard input while it is open.
// (hookshot) A gamepad's Home button, or Select and Start together, opens it too; the D-pad chooses and changes, the bottom
// face button changes too, the right one closes, the shoulder buttons change tabs. The game gets no gamepad input while it is
// open either. The mouse works it too. Its Keyboard and Controller tabs bind the Switch's buttons to keys and gamepad inputs,
// which the host keeps (host/nn_hid.c: host->binding and bind).
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#include "vk_menu.h"
#ifndef HOOKSHOT_VERSION   // (nx2/build.py gives it to this file: nx2/version.py)
#define HOOKSHOT_VERSION "unknown"
#endif

namespace mkwvk {
HWND app_window();

Options opts;

namespace {

struct Row {
    const char* name;
    int* value;
    int lo, hi, step;
    const char* (*text)(int v);
    const char* help;   // (shown under the list while the row is selected)
    int tab;            // (hookshot: the tab it is on, tab_names)
};
const char* tx_framegen(int v) {
    static const char* t[] = {"Off", "", "2x", "3x", "4x", "5x", "6x", "7x", "8x"};
    return t[v];
}
const char* tx_onoff(int v) { return v ? "On" : "Off"; }
const char* tx_upscale(int v) {
    static const char* t[] = {"Off", "Catmull-Rom", "Lanczos"};
    if ((unsigned)v >= sizeof t / sizeof t[0]) return t[0];   // (a setting past the row: the label, not the next bytes)
    return t[v];
}
const char* tx_percent(int v) {
    static char b[8];
    snprintf(b, sizeof b, "%d%%", v);
    return b;
}
const char* tx_resolution(int v) {
    static const char* t[] = {"Auto", "100%", "75%", "67%", "50%"};
    return t[v];
}
const char* tx_speed(int v) { return v ? "Real time" : "Per frame"; }
const char* tx_text_speed(int v) {
    static const char* t[] = {"Normal", "2x", "4x", "Instant"};
    if ((unsigned)v >= sizeof t / sizeof t[0]) return t[0];
    return t[v];
}
const char* tx_handheld(int v) { return v ? "On (restart)" : "Off (restart)"; }
const char* tx_ultrawide(int v) { return v ? "Monitor's shape (restart)" : "16:9 (restart)"; }

const char* tx_preset(int v) {
    static const char* t[] = {"Custom", "Minimum (4GB)", "Low (6GB)", "Balanced (8GB)", "High (12GB)", "Ultra (16GB+)"};
    return t[std::min(std::max(v, 0), 5)];
}
void sync_preset_budgets(int p) {
    switch (p) {
    case 1: opts.vram_mb = 2200;  opts.mirror_mb = 256;  break;
    case 2: opts.vram_mb = 3600;  opts.mirror_mb = 512;  break;
    case 3: opts.vram_mb = 4800;  opts.mirror_mb = 1024; break;
    case 4: opts.vram_mb = 7500;  opts.mirror_mb = 2048; break;
    case 5: opts.vram_mb = 11000; opts.mirror_mb = 3072; break;
    default: opts.vram_mb = 0;    opts.mirror_mb = 0;    break;
    }
}
void apply_preset(int p) {
    switch (p) {
    case 1: // Minimum (4GB VRAM / iGPU)
        opts.resolution = 4; opts.upscale = 2; opts.sharpness = 50;
        opts.framegen = 2;   opts.pace = 1;    opts.realtime = 1; opts.handheld = 1;
        break;
    case 2: // Low (6GB VRAM)
        opts.resolution = 2; opts.upscale = 2; opts.sharpness = 35;
        opts.framegen = 2;   opts.pace = 1;    opts.realtime = 1; opts.handheld = 1;
        break;
    case 3: // Balanced (8GB VRAM)
        opts.resolution = 1; opts.upscale = 1; opts.sharpness = 25;
        opts.framegen = 2;   opts.pace = 1;    opts.realtime = 1; opts.handheld = 0;
        break;
    case 4: // High (12GB VRAM)
        opts.resolution = 1; opts.upscale = 2; opts.sharpness = 30;
        opts.framegen = 2;   opts.pace = 1;    opts.realtime = 1; opts.handheld = 0;
        break;
    case 5: // Ultra (16GB+ VRAM)
        opts.resolution = 1; opts.upscale = 2; opts.sharpness = 40;
        opts.framegen = 3;   opts.pace = 1;    opts.realtime = 1; opts.handheld = 0;
        break;
    default:
        break;
    }
    sync_preset_budgets(p);
}
/// (after a row changed) the preset applied, or, when a setting it sets was changed by hand, Custom
void preset_follow(const int* value) {
    if (value == &opts.preset) apply_preset(opts.preset);
    else if (value == &opts.resolution || value == &opts.upscale || value == &opts.sharpness || value == &opts.framegen ||
             value == &opts.pace || value == &opts.realtime || value == &opts.handheld)
        opts.preset = 0, sync_preset_budgets(0);
}
const char* tx_sens(int v) {
    static char b[12];
    snprintf(b, sizeof b, "%.2f", v / 100.0);
    return b;
}
const char* tx_mousecam(int v) { return v ? "Native" : "As the stick"; }
// (the controls' rows: the text function says which kind they are, kind_of; they have no value of their own, their lo is the
// control's number, HOS_SW_* or HOS_KEY_*, and the host keeps what it is bound to)
const char* tx_keybind(int) { return ""; }
const char* tx_padbind(int) { return ""; }
const char* tx_reset(int) { return "Reset to defaults"; }

/// a key's or a mouse button's name, as the Keyboard tab shows it
const char* vk_name(int vk) {
    static char b[32];
    if (vk <= 0) return "-";
    if ((vk >= '0' && vk <= '9') || (vk >= 'A' && vk <= 'Z')) { snprintf(b, sizeof b, "%c", vk); return b; }
    if (vk >= 0x70 && vk <= 0x87) { snprintf(b, sizeof b, "F%d", vk - 0x70 + 1); return b; }   // (F1 to F24)
    if (vk >= 0x60 && vk <= 0x69) { snprintf(b, sizeof b, "Num %d", vk - 0x60); return b; }   // (the number pad)
    switch (vk) {
    case VK_LBUTTON: return "Left click";
    case VK_RBUTTON: return "Right click";
    case VK_MBUTTON: return "Middle click";
    case 0x05: return "Mouse 4";
    case 0x06: return "Mouse 5";
    case VK_SPACE: return "Space";
    case VK_SHIFT: return "Shift";
    case VK_CONTROL: return "Ctrl";
    case VK_MENU: return "Alt";
    case VK_TAB: return "Tab";
    case VK_RETURN: return "Enter";
    case VK_CAPITAL: return "Caps Lock";
    case VK_UP: return "Up";
    case VK_DOWN: return "Down";
    case VK_LEFT: return "Left";
    case VK_RIGHT: return "Right";
    case VK_HOME: return "Home";
    case VK_END: return "End";
    case VK_PRIOR: return "Page Up";
    case VK_NEXT: return "Page Down";
    case VK_DELETE: return "Delete";
    case 0xBA: return ";";
    case 0xBB: return "=";
    case 0xBC: return ",";
    case 0xBD: return "-";
    case 0xBE: return ".";
    case 0xBF: return "/";
    case 0xC0: return "`";
    case 0xDB: return "[";
    case 0xDC: return "\\";
    case 0xDD: return "]";
    case 0xDE: return "'";
    }
    snprintf(b, sizeof b, "Key %d", vk);
    return b;
}
/// a gamepad input by where it is (HOS_PAD_*, SDL's order): the same place on any pad
const char* pad_name(int p) {
    static const char* t[HOS_PAD_N] = {"Bottom face (A / Cross)", "Right face (B / Circle)", "Left face (X / Square)",
                                       "Top face (Y / Triangle)", "Back / Select / Share", "Home / Guide", "Start / Options",
                                       "Left stick press", "Right stick press", "LB / L1", "RB / R1", "D-pad up",
                                       "D-pad down", "D-pad left", "D-pad right", "LT / L2", "RT / R2"};
    return p >= 0 && p < HOS_PAD_N ? t[p] : "-";
}
// (the controls, kept by the host: host/nn_hid.c reads them back from the file, pad_<name> and key_<name>)
const char* const bind_names[HOS_KEY_N] = {"a", "b", "x", "y", "ls", "rs", "l", "r", "zl", "zr", "plus", "minus",
                                           "left", "up", "right", "down", "move_up", "move_down", "move_left",
                                           "move_right", "cam_up", "cam_down", "cam_left", "cam_right"};
bool can_bind() { return host && host->binding && host->bind && host->pad_raw; }
int key_of(int control, int slot) { return can_bind() ? host->binding(HOS_BIND_KEY, control, slot) : 0; }
int pad_of(int control) { return can_bind() ? host->binding(HOS_BIND_PAD, control, 0) : -1; }

const char* tab_names[] = {"Display", "Performance", "Camera", "Keyboard", "Controller"};
constexpr int kTabs = 5;
#define KEY_HELP "Enter, the bottom face button or a click on a key: press the new key or mouse button (Esc keeps the old one). Backspace clears it. " \
                 "Left and right: the first or second key."
#define PAD_HELP "Enter, the bottom face button or a click: press the controller button that should do this (Home is the menu's). " \
                 "Taking another button's input swaps the two. Delete clears it."

Row rows[] = {
    {"Fullscreen", &opts.fullscreen, 0, 1, 1, tx_onoff,
     "Fills the whole screen with the game, without the window's border. The same as F11.", 0},
    {"Resolution", &opts.resolution, 0, 4, 1, tx_resolution,
     "How sharp the 3D is drawn. Auto lets the game lower it when the GPU is busy. A fixed percentage stays steady: lower is "
     "faster.", 0},
    {"Upscaling", &opts.upscale, 0, 2, 1, tx_upscale,
     "The filter that scales the picture to your window. Off is the plain copy. Catmull-Rom is sharper, Lanczos sharper still.", 0},
    {"Sharpness", &opts.sharpness, 0, 100, 10, tx_percent,
     "Sharpens the picture as it goes to the window, with or without an upscaling filter. Too much shows halos around edges.", 0},
    {"Ultrawide", &opts.ultrawide, 0, 1, 1, tx_ultrawide,
     "Monitor's shape widens the camera to fill a screen wider than 16:9. The HUD is still stretched. Applies after a restart.", 0},
    {"FPS counter", &opts.show_fps, 0, 1, 1, tx_onoff,
     "Shows frames per second in the top right corner. With frame generation on, it also shows the game's own rate.", 0},
    {"Quality preset", &opts.preset, 0, 5, 1, tx_preset,
     "Sets resolution, upscaling, frame generation and the GPU memory limits for a tier of graphics card (its video memory). "
     "Changing one of those settings by hand makes it Custom.", 1},
    {"Frame generation", &opts.framegen, 0, 8, 1, tx_framegen,
     "Makes extra frames between the game's own (2x up to 8x), for smoother motion. Adds a little input delay, and fast motion or camera cuts "
     "can show artifacts.", 1},
    {"Frame pacing", &opts.pace, 0, 1, 1, tx_onoff,
     "Shows each frame at an even interval instead of as soon as it is ready. Less stutter, at up to one frame of delay.", 1},
    {"Game speed", &opts.realtime, 0, 1, 1, tx_speed,
     "Per frame: the game slows down whenever it runs below 60 fps. Real time: it keeps normal speed down to 15 fps by taking "
     "longer steps.", 1},
    {"Text speed", &opts.text_speed, 0, 3, 1, tx_text_speed,
     "How fast the letters of dialogue and other messages appear. Normal is the game's own pace, with its pauses. Instant shows "
     "each page at once, as pressing A does.", 1},
    {"Handheld profile", &opts.handheld, 0, 1, 1, tx_handheld,
     "Runs the game as if the console were undocked: lower resolutions, lighter on the GPU. Applies after a restart.", 1},
    {"Mouse camera", &opts.mouse_native, 0, 1, 1, tx_mousecam,
     "Native: the mouse turns the camera itself, one to one, like a PC game. As the stick: the mouse stands in for the right stick "
     "(the game's own stick curve and top speed). Click in the game's window to use the mouse; Esc lets it go.", 2},
    {"Mouse sensitivity", &opts.mouse_sens, 10, 500, 5, tx_sens,
     "How far the camera turns for a given mouse movement: at 1.00, a tenth of a degree a count. One setting for every camera. "
     "The - and = keys change it in the game too.", 2},
    {"Invert vertical", &opts.mouse_invert, 0, 1, 1, tx_onoff,
     "Swaps up and down for the mouse: moving it forward tilts the camera down.", 2},
    {"Button A", nullptr, HOS_SW_A, HOS_SW_A, 1, tx_keybind, KEY_HELP, 3},
    {"Button B", nullptr, HOS_SW_B, HOS_SW_B, 1, tx_keybind, KEY_HELP, 3},
    {"Button X", nullptr, HOS_SW_X, HOS_SW_X, 1, tx_keybind, KEY_HELP, 3},
    {"Button Y", nullptr, HOS_SW_Y, HOS_SW_Y, 1, tx_keybind, KEY_HELP, 3},
    {"Button L", nullptr, HOS_SW_L, HOS_SW_L, 1, tx_keybind, KEY_HELP, 3},
    {"Button R", nullptr, HOS_SW_R, HOS_SW_R, 1, tx_keybind, KEY_HELP, 3},
    {"Button ZL", nullptr, HOS_SW_ZL, HOS_SW_ZL, 1, tx_keybind, KEY_HELP, 3},
    {"Button ZR", nullptr, HOS_SW_ZR, HOS_SW_ZR, 1, tx_keybind, KEY_HELP, 3},
    {"Plus", nullptr, HOS_SW_PLUS, HOS_SW_PLUS, 1, tx_keybind, KEY_HELP, 3},
    {"Minus", nullptr, HOS_SW_MINUS, HOS_SW_MINUS, 1, tx_keybind, KEY_HELP, 3},
    {"Left stick press", nullptr, HOS_SW_LS, HOS_SW_LS, 1, tx_keybind, KEY_HELP, 3},
    {"Right stick press", nullptr, HOS_SW_RS, HOS_SW_RS, 1, tx_keybind, KEY_HELP, 3},
    {"Move forward", nullptr, HOS_KEY_MOVE_UP, HOS_KEY_MOVE_UP, 1, tx_keybind, KEY_HELP, 3},
    {"Move back", nullptr, HOS_KEY_MOVE_DOWN, HOS_KEY_MOVE_DOWN, 1, tx_keybind, KEY_HELP, 3},
    {"Move left", nullptr, HOS_KEY_MOVE_LEFT, HOS_KEY_MOVE_LEFT, 1, tx_keybind, KEY_HELP, 3},
    {"Move right", nullptr, HOS_KEY_MOVE_RIGHT, HOS_KEY_MOVE_RIGHT, 1, tx_keybind, KEY_HELP, 3},
    {"Camera up", nullptr, HOS_KEY_CAM_UP, HOS_KEY_CAM_UP, 1, tx_keybind, KEY_HELP, 3},
    {"Camera down", nullptr, HOS_KEY_CAM_DOWN, HOS_KEY_CAM_DOWN, 1, tx_keybind, KEY_HELP, 3},
    {"Camera left", nullptr, HOS_KEY_CAM_LEFT, HOS_KEY_CAM_LEFT, 1, tx_keybind, KEY_HELP, 3},
    {"Camera right", nullptr, HOS_KEY_CAM_RIGHT, HOS_KEY_CAM_RIGHT, 1, tx_keybind, KEY_HELP, 3},
    {"D-pad left", nullptr, HOS_SW_LEFT, HOS_SW_LEFT, 1, tx_keybind, KEY_HELP, 3},
    {"D-pad up", nullptr, HOS_SW_UP, HOS_SW_UP, 1, tx_keybind, KEY_HELP, 3},
    {"D-pad right", nullptr, HOS_SW_RIGHT, HOS_SW_RIGHT, 1, tx_keybind, KEY_HELP, 3},
    {"D-pad down", nullptr, HOS_SW_DOWN, HOS_SW_DOWN, 1, tx_keybind, KEY_HELP, 3},
    {"Reset keys", nullptr, 0, 0, 1, tx_reset, "Puts every key and mouse button back as they came.", 3},
    {"Controller in background", &opts.pad_background, 0, 1, 1, tx_onoff,
     "On: the controller keeps playing the game while another window has the focus, for streaming or playing beside other "
     "apps. The keyboard and mouse still need the game's window.", 4},
    {"Button A", nullptr, HOS_SW_A, HOS_SW_A, 1, tx_padbind, PAD_HELP, 4},
    {"Button B", nullptr, HOS_SW_B, HOS_SW_B, 1, tx_padbind, PAD_HELP, 4},
    {"Button X", nullptr, HOS_SW_X, HOS_SW_X, 1, tx_padbind, PAD_HELP, 4},
    {"Button Y", nullptr, HOS_SW_Y, HOS_SW_Y, 1, tx_padbind, PAD_HELP, 4},
    {"Button L", nullptr, HOS_SW_L, HOS_SW_L, 1, tx_padbind, PAD_HELP, 4},
    {"Button R", nullptr, HOS_SW_R, HOS_SW_R, 1, tx_padbind, PAD_HELP, 4},
    {"Button ZL", nullptr, HOS_SW_ZL, HOS_SW_ZL, 1, tx_padbind, PAD_HELP, 4},
    {"Button ZR", nullptr, HOS_SW_ZR, HOS_SW_ZR, 1, tx_padbind, PAD_HELP, 4},
    {"Plus", nullptr, HOS_SW_PLUS, HOS_SW_PLUS, 1, tx_padbind, PAD_HELP, 4},
    {"Minus", nullptr, HOS_SW_MINUS, HOS_SW_MINUS, 1, tx_padbind, PAD_HELP, 4},
    {"Left stick press", nullptr, HOS_SW_LS, HOS_SW_LS, 1, tx_padbind, PAD_HELP, 4},
    {"Right stick press", nullptr, HOS_SW_RS, HOS_SW_RS, 1, tx_padbind, PAD_HELP, 4},
    {"D-pad left", nullptr, HOS_SW_LEFT, HOS_SW_LEFT, 1, tx_padbind, PAD_HELP, 4},
    {"D-pad up", nullptr, HOS_SW_UP, HOS_SW_UP, 1, tx_padbind, PAD_HELP, 4},
    {"D-pad right", nullptr, HOS_SW_RIGHT, HOS_SW_RIGHT, 1, tx_padbind, PAD_HELP, 4},
    {"D-pad down", nullptr, HOS_SW_DOWN, HOS_SW_DOWN, 1, tx_padbind, PAD_HELP, 4},
    {"Reset buttons", nullptr, 0, 0, 1, tx_reset, "Puts every controller button back as it came.", 4},
};
constexpr int kRows = sizeof rows / sizeof rows[0];
constexpr int kMenuWidth = 540;   // (at a scale of 1: 720 lines)

std::atomic<bool> open_now{false};
int cur_tab = 0, selected = 0;    // (selected: the row's index in rows, on cur_tab)
std::atomic<bool> menu_refresh{false};
std::string ini_path() {
    char exe[MAX_PATH];
    const DWORD n = GetModuleFileNameA(nullptr, exe, MAX_PATH);
    std::string dir(exe, n);
    return dir.substr(0, dir.find_last_of("\\/") + 1) + "hookshot_options.ini";
}
const char* ini_key(const Row& r) {
    static char k[32];
    size_t n = 0;
    for (const char* p = r.name; *p && n + 1 < sizeof k; p++) k[n++] = *p == ' ' ? '_' : (char)tolower(*p);
    k[n] = 0;
    return k;
}
void save() {
    // (hookshot) the controls are the host's: written from it, pad_<name>=<HOS_PAD_* position> and key_<name>=<code>,<code>;
    // without it (no host table) the file's own lines are kept
    std::string kept;
    if (!can_bind())
        if (FILE* f = fopen(ini_path().c_str(), "r")) {
            char line[256];
            while (fgets(line, sizeof line, f))
                if (!strncmp(line, "pad_", 4) || !strncmp(line, "key_", 4)) kept += line;
            fclose(f);
        }
    if (FILE* f = fopen(ini_path().c_str(), "w")) {
        for (const Row& r : rows)
            if (r.value) fprintf(f, "%s=%d\n", ini_key(r), *r.value);
        fprintf(f, "options_version=2\n");   // (game_speed below 2 was only ever the old default, see menu_init)
        if (!can_bind()) fputs(kept.c_str(), f);
        for (int i = 0; i < HOS_KEY_N && can_bind(); i++) {
            if (i < HOS_SW_N) fprintf(f, "pad_%s=%d\n", bind_names[i], host->binding(HOS_BIND_PAD, i, 0));
            fprintf(f, "key_%s=%d,%d\n", bind_names[i], host->binding(HOS_BIND_KEY, i, 0), host->binding(HOS_BIND_KEY, i, 1));
        }
        fclose(f);
    }
}

// ---- what is drawn: bitmaps of the menu and the counter, in the window's byte order ----
struct Bitmap {
    uint32_t w = 0, h = 0;
    std::vector<uint8_t> px;
    uint64_t version = 0;
    bool keyed = false;                   // (black is see-through: copied as the runs of pixels in between)
    std::vector<VkBufferImageCopy> runs;
};
Bitmap menu_bmp, fps_bmp;
std::mutex bmp_mu;
int scale_of(uint32_t h) { return std::max(1u, h / 720); }

void gdi_draw(Bitmap& out, uint32_t w, uint32_t h, void (*paint)(HDC, uint32_t, uint32_t, int), int scale) {
    BITMAPINFO bi{};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = (LONG)w;
    bi.bmiHeader.biHeight = -(LONG)h;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    HDC dc = CreateCompatibleDC(nullptr);
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bmp) { DeleteDC(dc); return; }
    HGDIOBJ old = SelectObject(dc, bmp);
    paint(dc, w, h, scale);
    GdiFlush();
    const bool rgba = dev.swap_format == VK_FORMAT_R8G8B8A8_UNORM || dev.swap_format == VK_FORMAT_R8G8B8A8_SRGB;
    std::lock_guard lk(bmp_mu);
    out.w = w, out.h = h;
    out.px.assign((const uint8_t*)bits, (const uint8_t*)bits + (size_t)w * h * 4);
    if (out.keyed) {
        out.runs.clear();
        for (uint32_t y = 0; y < h; y++) {
            for (uint32_t x = 0; x < w;) {
                const uint8_t* p = out.px.data() + ((size_t)y * w + x) * 4;
                if (!(p[0] | p[1] | p[2])) { x++; continue; }
                uint32_t e = x + 1;
                for (; e < w; e++) {
                    const uint8_t* q = out.px.data() + ((size_t)y * w + e) * 4;
                    if (!(q[0] | q[1] | q[2])) break;
                }
                VkBufferImageCopy c{};
                c.bufferOffset = ((uint64_t)y * w + x) * 4;
                c.bufferRowLength = w;
                c.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
                c.imageOffset = {(int32_t)x, (int32_t)y, 0};
                c.imageExtent = {e - x, 1, 1};
                out.runs.push_back(c);
                x = e;
            }
        }
    }
    for (size_t i = 0; i < out.px.size(); i += 4) {
        if (rgba) std::swap(out.px[i], out.px[i + 2]);
        out.px[i + 3] = 255;
    }
    out.version++;
    SelectObject(dc, old);
    DeleteObject(bmp);
    DeleteDC(dc);
}
HFONT font(int px, int weight) {
    return CreateFontA(-px, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                       DEFAULT_PITCH, "Segoe UI");
}
int help_px = 0;   // (the help text's height: measure_help)
/// The help text word-wrapped to width, a TextOutA a line (the POSIX GDI stand-in has no DrawTextA), drawn at (x, y)
/// when draw: its height in pixels either way
int wrap_help(HDC dc, const char* text, int width, int x, int y, bool draw) {
    SIZE sz{};
    GetTextExtentPoint32A(dc, "Ag", 2, &sz);
    const int line_h = (int)sz.cy;
    int lines = 0;
    for (const char* p = text; *p;) {
        int n = 0;   // (the most whole words that fit; one word at least, however long)
        for (int end = 0;; end++) {
            while (p[end] && p[end] != ' ') end++;
            GetTextExtentPoint32A(dc, p, end, &sz);
            if (n && sz.cx > width) break;
            n = end;
            if (!p[end]) break;
        }
        if (draw) TextOutA(dc, x, y + lines * line_h, p, n);
        lines++;
        p += n;
        while (*p == ' ') p++;
    }
    return lines * line_h;
}
void fill(HDC dc, int x, int y, int w, int h, COLORREF c) {
    RECT r{x, y, x + w, y + h};
    HBRUSH b = CreateSolidBrush(c);
    FillRect(dc, &r, b);
    DeleteObject(b);
}
// (hookshot) The menu redrawn: tabs, a row a setting with a switch, a slider or arrows, the help under them; in the pause
// menu's colours. Only rectangles and text: the POSIX GDI stand-in (host/posix/gdi_posix.c) has nothing else. Where each
// part landed is kept for the mouse (hits), in the menu's own pixels.
enum Kind { K_CHOICE, K_TOGGLE, K_SLIDER, K_KEY, K_PAD, K_ACTION };
Kind kind_of(const Row& r) {
    if (r.text == tx_keybind) return K_KEY;
    if (r.text == tx_padbind) return K_PAD;
    if (r.text == tx_reset) return K_ACTION;
    if (r.text == tx_onoff) return K_TOGGLE;
    if ((r.hi - r.lo) / r.step >= 8 && r.text != tx_framegen) return K_SLIDER;
    return K_CHOICE;
}
constexpr int kVisible = 9;   // (rows a tab shows at once: the longer ones scroll; the panel keeps that height)
int scroll[kTabs] = {};       // (each tab's first row shown)
int sel_slot = 0;             // (the Keyboard tab: the first or the second key)
int capturing = -1, cap_slot = 0;   // (the row waiting for a key or a pad button, and which of its keys)
bool cap_released = false;           // (everything let go since the capture began: the press that began it is not taken)
int64_t cap_until = 0;
struct Hits {
    RECT tab[kTabs]{}, close{};
    RECT row[kVisible]{}, ctl[kVisible]{}, box[kVisible][2]{};
    int index[kVisible]{}, shown = 0;
} hits;
const COLORREF kBg = RGB(30, 24, 18), kHead = RGB(46, 36, 25), kTabOff = RGB(38, 30, 21), kSel = RGB(72, 56, 36),
               kGold = RGB(214, 174, 92), kText = RGB(242, 232, 212), kDim = RGB(172, 156, 128), kTrack = RGB(64, 52, 38),
               kLine = RGB(84, 68, 46);
int count_on(int tab) {
    int n = 0;
    for (const Row& r : rows) n += r.tab == tab;
    return n;
}
/// the rows on a tab, from its scroll position, kVisible at most: their indices in rows
int shown_rows(int tab, int* out) {
    int n = 0, k = 0;
    for (int i = 0; i < kRows && n < kVisible; i++)
        if (rows[i].tab == tab && k++ >= scroll[tab]) out[n++] = i;
    return n;
}
int most_rows() {
    int most = 0;
    for (int t = 0; t < kTabs; t++) most = std::max(most, count_on(t));
    return std::min(most, kVisible);
}
int text_w(HDC dc, const char* t) {
    SIZE sz{};
    GetTextExtentPoint32A(dc, t, (int)strlen(t), &sz);
    return (int)sz.cx;
}
void text_at(HDC dc, int x, int y, const char* t, COLORREF c) {
    SetTextColor(dc, c);
    TextOutA(dc, x, y, t, (int)strlen(t));
}
void frame(HDC dc, int x, int y, int w, int h, int t, COLORREF c) {
    fill(dc, x, y, w, t, c), fill(dc, x, y + h - t, w, t, c), fill(dc, x, y, t, h, c), fill(dc, x + w - t, y, t, h, c);
}
/// a box with text in it, the text cut to fit
void box(HDC dc, const RECT& r, const char* t, bool lit, bool waiting, int s) {
    fill(dc, r.left, r.top, r.right - r.left, r.bottom - r.top, waiting ? RGB(88, 66, 30) : RGB(40, 32, 23));
    frame(dc, r.left, r.top, r.right - r.left, r.bottom - r.top, std::max(1, s), lit || waiting ? kGold : kLine);
    std::string text = t;
    const int room = (r.right - r.left) - 12 * s;
    while (text.size() > 1 && text_w(dc, text.c_str()) > room) text.pop_back();
    text_at(dc, r.left + ((r.right - r.left) - text_w(dc, text.c_str())) / 2, r.top + ((r.bottom - r.top) - 18 * s) / 2, text.c_str(),
            waiting ? kGold : lit ? kText : kDim);
}
void paint_menu(HDC dc, uint32_t w, uint32_t h, int s) {
    const int W = (int)w;
    fill(dc, 0, 0, W, (int)h, kBg);
    frame(dc, 0, 0, W, (int)h, std::max(1, s), kLine);
    SetBkMode(dc, TRANSPARENT);
    HFONT title = font(19 * s, FW_SEMIBOLD), body = font(15 * s, FW_NORMAL), bold = font(15 * s, FW_SEMIBOLD),
          small = font(12 * s, FW_NORMAL), help = font(14 * s, FW_NORMAL), tabf = font(14 * s, FW_NORMAL),
          tabb = font(14 * s, FW_SEMIBOLD);
    HGDIOBJ old = SelectObject(dc, title);
    // the header: the name, and a close box
    const int head = 44 * s;
    fill(dc, 0, 0, W, head, kHead);
    fill(dc, 0, head - 2 * s, W, 2 * s, kGold);
    text_at(dc, 18 * s, 11 * s, "Hookshot", kText);
    const int tw = text_w(dc, "Hookshot");
    SelectObject(dc, body);
    text_at(dc, 18 * s + tw + 10 * s, 14 * s, "Settings", kDim);
    const int cb = 28 * s;
    hits.close = {W - cb - 10 * s, 8 * s, W - 10 * s, 8 * s + cb};
    frame(dc, hits.close.left, hits.close.top, cb, cb, std::max(1, s), kLine);
    SelectObject(dc, bold);
    text_at(dc, hits.close.left + (cb - text_w(dc, "X")) / 2, hits.close.top + 5 * s, "X", kDim);
    // the tabs
    const int ty = head, th = 36 * s, tabw = W / kTabs;
    for (int t = 0; t < kTabs; t++) {
        const int x = t * tabw, wd = t == kTabs - 1 ? W - x : tabw;
        hits.tab[t] = {x, ty, x + wd, ty + th};
        fill(dc, x, ty, wd, th, t == cur_tab ? kBg : kTabOff);
        if (t == cur_tab) fill(dc, x + 10 * s, ty + th - 3 * s, wd - 20 * s, 3 * s, kGold);
        SelectObject(dc, t == cur_tab ? tabb : tabf);
        text_at(dc, x + (wd - text_w(dc, tab_names[t])) / 2, ty + 10 * s, tab_names[t], t == cur_tab ? kText : kDim);
    }
    // the rows
    const int top = ty + th + 8 * s, rh = 40 * s, cw = 230 * s, cx = W - 26 * s - cw;
    int list[kVisible];
    hits.shown = shown_rows(cur_tab, list);
    for (int k = 0; k < hits.shown; k++) {
        const Row& r = rows[list[k]];
        const int y = top + k * rh, sel = list[k] == selected;
        hits.index[k] = list[k];
        hits.row[k] = {8 * s, y, W - 8 * s, y + rh};
        hits.ctl[k] = {cx, y, cx + cw, y + rh};
        if (sel) fill(dc, 8 * s, y + 2 * s, W - 22 * s, rh - 4 * s, kSel), fill(dc, 8 * s, y + 2 * s, 4 * s, rh - 4 * s, kGold);
        SelectObject(dc, body);
        text_at(dc, 22 * s, y + 10 * s, r.name, sel ? kText : RGB(222, 210, 188));
        const int mid = y + rh / 2;
        switch (kind_of(r)) {
        case K_KEY: {
            const int bw = (cw - 8 * s) / 2;
            for (int slot = 0; slot < 2; slot++) {
                hits.box[k][slot] = {cx + slot * (bw + 8 * s), mid - 13 * s, cx + slot * (bw + 8 * s) + bw, mid + 13 * s};
                const bool waiting = capturing == list[k] && cap_slot == slot;
                box(dc, hits.box[k][slot], waiting ? "Press a key" : vk_name(key_of(r.lo, slot)), sel && sel_slot == slot, waiting, s);
            }
            break;
        }
        case K_PAD: {
            hits.box[k][0] = {cx, mid - 13 * s, cx + cw, mid + 13 * s};
            const bool waiting = capturing == list[k];
            box(dc, hits.box[k][0], waiting ? "Press a button" : pad_name(pad_of(r.lo)), sel, waiting, s);
            break;
        }
        case K_ACTION:
            hits.box[k][0] = {cx, mid - 13 * s, cx + cw, mid + 13 * s};
            box(dc, hits.box[k][0], r.text(0), sel, false, s);
            break;
        case K_TOGGLE: {
            const int v = *r.value, sw = 48 * s, sh = 22 * s, sx = cx + cw - sw, sy = mid - sh / 2;
            fill(dc, sx, sy, sw, sh, v ? kGold : kTrack);
            fill(dc, v ? sx + sw - sh + 3 * s : sx + 3 * s, sy + 3 * s, sh - 6 * s, sh - 6 * s, v ? RGB(40, 30, 18) : kDim);
            const char* t = r.text(v);
            text_at(dc, sx - 12 * s - text_w(dc, t), y + 10 * s, t, sel ? kText : kDim);
            break;
        }
        case K_SLIDER: {
            const int v = *r.value, vw = 52 * s, tx = cx, tw2 = cw - vw - 10 * s, ky = mid - 3 * s;
            const int at = tx + (int)((long long)(v - r.lo) * tw2 / std::max(1, r.hi - r.lo));
            fill(dc, tx, ky, tw2, 6 * s, kTrack);
            fill(dc, tx, ky, at - tx, 6 * s, kGold);
            fill(dc, at - 5 * s, mid - 10 * s, 10 * s, 20 * s, sel ? kText : kDim);
            const char* t = r.text(v);
            SelectObject(dc, bold);
            text_at(dc, cx + cw - text_w(dc, t), y + 10 * s, t, sel ? kText : kDim);
            break;
        }
        default: {
            const int bw = 26 * s, by = mid - 13 * s;
            frame(dc, cx, by, bw, 26 * s, std::max(1, s), sel ? kGold : kLine);
            frame(dc, cx + cw - bw, by, bw, 26 * s, std::max(1, s), sel ? kGold : kLine);
            SelectObject(dc, bold);
            text_at(dc, cx + (bw - text_w(dc, "<")) / 2, by + 3 * s, "<", sel ? kGold : kDim);
            text_at(dc, cx + cw - bw + (bw - text_w(dc, ">")) / 2, by + 3 * s, ">", sel ? kGold : kDim);
            SelectObject(dc, body);
            const char* t = r.text(*r.value);
            text_at(dc, cx + (cw - text_w(dc, t)) / 2, y + 10 * s, t, sel ? kText : kDim);
        }
        }
    }
    {   // a scrollbar when the tab has more rows than it shows
        const int n = count_on(cur_tab);
        if (n > kVisible) {
            const int bx = W - 14 * s, bh = kVisible * rh;
            fill(dc, bx, top, 4 * s, bh, kTrack);
            fill(dc, bx, top + bh * scroll[cur_tab] / n, 4 * s, std::max(8 * s, bh * kVisible / n), kGold);
        }
    }
    // the help, then the hints
    const int help_top = top + most_rows() * rh + 8 * s;
    fill(dc, 16 * s, help_top, W - 32 * s, std::max(1, s), kLine);
    SelectObject(dc, help);
    SetTextColor(dc, RGB(200, 186, 160));
    wrap_help(dc, rows[selected].help, W - 32 * s, 16 * s, help_top + 10 * s, true);
    const int foot = help_top + 10 * s + help_px + 12 * s;
    fill(dc, 0, foot, W, (int)h - foot, kHead);
    SelectObject(dc, small);
    text_at(dc, 16 * s, foot + 8 * s, "Mouse: click, drag, wheel   Keys: arrows, Tab, Enter   Pad: D-pad, LB / RB", kDim);
    text_at(dc, 16 * s, foot + 26 * s, "Close: Esc, Insert, F1, Home, or Back + Start", kDim);
    std::string ver = HOOKSHOT_VERSION;
    if (ver.find(" (") != std::string::npos) ver.resize(ver.find(" ("));
    text_at(dc, W - 16 * s - text_w(dc, ver.c_str()), foot + 26 * s, ver.c_str(), RGB(128, 114, 92));
    SelectObject(dc, old);
    for (HFONT f : {title, body, bold, small, help, tabf, tabb}) DeleteObject(f);
}
char fps_text[48] = "";
char fps_sub[32] = "";
/// The usual overlay: a bold green number with a dark edge, nothing behind it. Hard-edged letters: see-through pixels are copied or not.
void outlined(HDC dc, int x, int y, const char* text, COLORREF colour, int edge) {
    SetTextColor(dc, RGB(6, 8, 6));
    const int n = (int)strlen(text);
    for (int dy = -edge; dy <= edge; dy++)
        for (int dx = -edge; dx <= edge; dx++)
            if (dx || dy) TextOutA(dc, x + dx, y + dy, text, n);
    SetTextColor(dc, colour);
    TextOutA(dc, x, y, text, n);
}
HFONT hard_font(int px, int weight) {
    return CreateFontA(-px, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, NONANTIALIASED_QUALITY,
                       DEFAULT_PITCH, "Segoe UI");
}
void paint_fps(HDC dc, uint32_t w, uint32_t h, int s) {
    fill(dc, 0, 0, (int)w, (int)h, RGB(0, 0, 0));
    SetBkMode(dc, TRANSPARENT);
    SetTextAlign(dc, TA_RIGHT | TA_TOP);
    const COLORREF green = RGB(72, 255, 110);
    const int right = (int)w - 2 * s;
    HFONT label = hard_font(13 * s, FW_BOLD), big = hard_font(28 * s, FW_BOLD);
    HGDIOBJ old = SelectObject(dc, label);
    outlined(dc, right, 14 * s, "FPS", green, s);
    SelectObject(dc, big);
    outlined(dc, right - 30 * s, 0, fps_text, green, s);
    if (fps_sub[0]) {
        SelectObject(dc, label);
        outlined(dc, right, 32 * s, fps_sub, RGB(226, 232, 226), s);
    }
    SelectObject(dc, old);
    DeleteObject(label);
    DeleteObject(big);
}
/// The tallest help text at scale s, in pixels: the menu keeps that height whichever row is selected
int measure_help(int s) {
    HDC dc = CreateCompatibleDC(nullptr);
    HFONT f = font(14 * s, FW_NORMAL);
    HGDIOBJ old = SelectObject(dc, f);
    int most = 0;
    for (const Row& r : rows) most = std::max(most, wrap_help(dc, r.help, (kMenuWidth - 32) * s, 0, 0, false));
    SelectObject(dc, old);
    DeleteObject(f);
    DeleteDC(dc);
    return most;
}
void redraw_menu(int s) {
    static int measured_at = 0;
    if (measured_at != s) help_px = measure_help(s), measured_at = s;
    // (header, tabs, the tallest tab's rows, the help, the hints)
    gdi_draw(menu_bmp, (uint32_t)(kMenuWidth * s), (uint32_t)((44 + 36 + 8 + most_rows() * 40 + 8 + 10 + 12 + 46) * s + help_px),
             paint_menu, s);
}

std::atomic<int64_t> game_frames{0}, shown_frames{0};
int cur_scale = 1;

struct Upload {
    VkBuffer buf = VK_NULL_HANDLE;
    VmaAllocation alloc = nullptr;
    void* map = nullptr;
    uint64_t size = 0, version = 0;
};
Upload ups[2][3];   // (the menu's, the counter's; a frame in flight's each)

void copy_over(VkCommandBuffer cmd, VkImage dst, Bitmap& b, Upload& u, int x, int y, uint32_t w, uint32_t h) {
    std::lock_guard lk(bmp_mu);
    if (!b.w || (uint32_t)x + b.w > w || (uint32_t)y + b.h > h) return;
    const uint64_t size = (uint64_t)b.w * b.h * 4;
    if (u.size < size) {
        if (!create_buffer(size, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, true, u.buf, u.alloc, &u.map)) return;
        u.size = size, u.version = 0;
    }
    if (u.version != b.version) std::memcpy(u.map, b.px.data(), size), u.version = b.version;
    if (b.keyed) {
        static thread_local std::vector<VkBufferImageCopy> at;
        at = b.runs;
        for (auto& r : at) r.imageOffset.x += x, r.imageOffset.y += y;
        if (!at.empty()) vkCmdCopyBufferToImage(cmd, u.buf, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, (uint32_t)at.size(), at.data());
        return;
    }
    VkBufferImageCopy c{};
    c.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    c.imageOffset = {x, y, 0};
    c.imageExtent = {b.w, b.h, 1};
    vkCmdCopyBufferToImage(cmd, u.buf, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &c);
}

} // namespace

void menu_init() {
    static bool done;
    if (done) return;
    done = true;
    if (FILE* f = fopen(ini_path().c_str(), "r")) {
        char line[96];
        bool has_resolution = false;
        bool v2 = false;
        int old_dynres = -1;   // (the row before Resolution: 0, the full scale, is its 100%; 1 is Auto)
        while (fgets(line, sizeof line, f)) {
            char* eq = strchr(line, '=');
            if (!eq) continue;
            *eq = 0;
            for (Row& r : rows)   // (the controls' lines are the host's: host/nn_hid.c)
                if (r.value && !strcmp(line, ini_key(r))) *r.value = std::min(std::max(atoi(eq + 1), r.lo), r.hi);
            has_resolution |= !strcmp(line, "resolution");
            v2 |= !strcmp(line, "options_version");
            if (!strcmp(line, "dynamic_resolution")) old_dynres = atoi(eq + 1);
        }
        fclose(f);
        if (!has_resolution && old_dynres >= 0) opts.resolution = old_dynres ? 0 : 1;
        // (Game speed is Real time by default now: Per frame played at half speed at 30 fps, the "slow motion" players
        // reported. An options file from before kept the old default whether chosen or not: Real time for it too)
        if (!v2) opts.realtime = 1;
    }
    if (opts.preset > 0) sync_preset_budgets(opts.preset);
    if (opts.framegen == 1) opts.framegen = 2;
    if (const char* e = getenv("RX_VK_FRAMEGEN_MULT")) opts.framegen = std::min(std::max(atoi(e), 2), 8);
    if (const char* e = getenv("RX_VK_PACE")) opts.pace = atoi(e) != 0;
    if (const char* e = getenv("RX_VK_UPSCALE"))
        for (Row& r : rows)
            if (r.value == &opts.upscale) *r.value = std::min(std::max(atoi(e), r.lo), r.hi);
    if (const char* e = getenv("HOS_REALTIME_STEP")) opts.realtime = atoi(e) != 0;
    if (const char* e = getenv("HOS_DYNRES")) opts.resolution = atoi(e) != 0 ? 0 : 1;
}

bool menu_is_open() { return open_now.load(std::memory_order_relaxed); }

void menu_note_frame(bool game_frame) {
    shown_frames.fetch_add(1, std::memory_order_relaxed);
    if (game_frame) game_frames.fetch_add(1, std::memory_order_relaxed);
}

// The Fullscreen row and F11 are one setting: the row asks the window's thread (host/display.c), F11 shows in the row
void fullscreen_tick() {
    static int applied = 0, settle = 0;
    const HWND main_window = app_window();
    if (!main_window) return;
    static const bool env = getenv("HOS_FULLSCREEN") != nullptr;   // (a test's own choice: the row is left alone)
    if (env) return;
    if (opts.fullscreen != applied) {
        applied = opts.fullscreen;
        settle = 60;
        PostMessageA(main_window, WM_APP + 7, (WPARAM)applied, 0);
        return;
    }
    if (settle > 0) { settle--; return; }
    const int now = !(GetWindowLongA(main_window, GWL_STYLE) & WS_CAPTION);
    if (now != applied) {
        opts.fullscreen = applied = now;
        menu_refresh = true;
        save();
    }
}

/// A rate over the half-second that just ended. The first call only starts that interval: the frames since launch,
/// divided by one second, were shown as the rate the moment the counter was turned on.
bool fps_take(int64_t now, int64_t shown, int64_t game, int64_t* next, int64_t* last_shown, int64_t* last_game,
              double* shown_rate, double* game_rate) {
    if (now < *next) return false;
    const bool have = *next != 0;
    if (have) {
        const double secs = (now - *next + 500000000) / 1e9;
        *shown_rate = (shown - *last_shown) / secs;
        *game_rate = (game - *last_game) / secs;
    }
    *last_shown = shown;
    *last_game = game;
    *next = now + 500000000;
    return have;
}

namespace {
/// A row's value moved one step (dir -1 or +1): a switch flips, a slider stops at its ends, a choice goes round
void change(Row& r, int dir) {
    if (!r.value || r.lo == r.hi) return;
    int v = *r.value + dir * r.step;
    if (r.value == &opts.framegen && v == 1) v = dir > 0 ? 2 : 0;   // (off, 2x ... 8x)
    if (kind_of(r) == K_SLIDER) v = std::min(std::max(v, r.lo), r.hi);
    else v = v > r.hi ? r.lo : v < r.lo ? r.hi : v;
    if (v != *r.value) *r.value = v, preset_follow(r.value), save();
}
void set_from_x(Row& r, int x, const RECT& ctl, int s) {   // (a slider: the value under the pointer)
    const int tw = (ctl.right - ctl.left) - 52 * s - 10 * s;
    const int v = r.lo + (int)((long long)(x - ctl.left) * (r.hi - r.lo) / std::max(1, tw));
    const int stepped = r.lo + (std::min(std::max(v, r.lo), r.hi) - r.lo + r.step / 2) / r.step * r.step;
    if (stepped != *r.value) *r.value = std::min(stepped, r.hi), preset_follow(r.value), save();
}
bool in(const RECT& r, int x, int y) { return x >= r.left && x < r.right && y >= r.top && y < r.bottom; }
int first_on(int tab) {
    for (int i = 0; i < kRows; i++)
        if (rows[i].tab == tab) return i;
    return 0;
}
int position_on(int tab, int row) {   // (where a row is among its tab's)
    int k = 0;
    for (int i = 0; i < row; i++) k += rows[i].tab == tab;
    return k;
}
void keep_shown() {   // (the tab scrolled so the selected row is in view)
    const int k = position_on(cur_tab, selected), n = count_on(cur_tab);
    if (k < scroll[cur_tab]) scroll[cur_tab] = k;
    if (k >= scroll[cur_tab] + kVisible) scroll[cur_tab] = k - kVisible + 1;
    scroll[cur_tab] = std::max(0, std::min(scroll[cur_tab], n - kVisible));
}
void move_sel(int dir) {   // (up or down among the rows on cur_tab)
    int list[64], n = 0;
    for (int i = 0; i < kRows && n < 64; i++)
        if (rows[i].tab == cur_tab) list[n++] = i;
    int k = 0;
    while (k < n && list[k] != selected) k++;
    selected = list[((k + dir) % n + n) % n];
    keep_shown();
}
void to_tab(int t) {
    cur_tab = (t % kTabs + kTabs) % kTabs;
    int list[kVisible];
    selected = shown_rows(cur_tab, list) ? list[0] : first_on(cur_tab);
    sel_slot = 0;
}
void wheel_scroll(int notches) {
    const int n = count_on(cur_tab);
    scroll[cur_tab] = std::max(0, std::min(scroll[cur_tab] - notches * 3, n - kVisible));
}
/// a press that repeats while held: the first at once, then after 350 ms every 60 ms
bool repeat(bool down, int64_t now, int64_t& since, int64_t& next) {
    if (!down) { since = 0; return false; }
    if (!since) { since = now, next = now + 350000000; return true; }
    if (now >= next) { next = now + 60000000; return true; }
    return false;
}
void reset_controls(int tab) {   // (the Reset rows: the controls of that tab as they came)
    if (!can_bind()) return;
    host->bind(tab == 3 ? HOS_BIND_KEY : HOS_BIND_PAD, -2, 0, 0);
    save();
}
void clear_control(const Row& r, int slot) {   // (Delete: the key, or the gamepad input, taken away)
    if (!can_bind()) return;
    if (r.text == tx_keybind) host->bind(HOS_BIND_KEY, r.lo, slot, 0);
    else host->bind(HOS_BIND_PAD, r.lo, 0, -1);
    save();
}
/// activate a row: a switch or a choice steps; a key or a pad button waits for the new one; a reset resets
void activate(int row, int slot, int64_t now) {
    Row& r = rows[row];
    switch (kind_of(r)) {
    case K_KEY: case K_PAD:
        if (!can_bind()) break;
        capturing = row, cap_slot = slot, cap_released = false, cap_until = now + 8000000000LL;
        break;
    case K_ACTION: reset_controls(r.tab); break;
    case K_SLIDER: break;
    default: change(r, +1);
    }
}
std::atomic<int> wheel_notches{0};
} // namespace

void menu_option_set(int id, int value) {
    if (id == 29) { wheel_notches += value / 120; menu_refresh = true; return; }   // (host/display.c: the wheel, while open)
    if (id == 20) opts.mouse_sens = std::min(std::max(value, 10), 500);
    else return;
    save();
    menu_refresh = true;
}

void menu_tick() {
    fullscreen_tick();
    // the keyboard (while the game's window is in front), the mouse's left button, and the controllers (host/nn_hid.c)
    enum { K_INSERT, K_UP, K_DOWN, K_LEFT, K_RIGHT, K_ESC, K_F1, K_TAB, K_ENTER, K_MOUSE, K_BACK, K_DEL, NKEYS };
    static const int keys[NKEYS] = {VK_INSERT, VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_ESCAPE, VK_F1, VK_TAB, VK_RETURN, VK_LBUTTON,
                                    VK_BACK, VK_DELETE};
    static bool held[NKEYS];
    HWND fg = GetForegroundWindow();
    DWORD pid = 0;
    if (fg) GetWindowThreadProcessId(fg, &pid);
    const bool mine = pid == GetCurrentProcessId();
    bool press[NKEYS] = {}, down[NKEYS] = {};
    for (int i = 0; i < NKEYS; i++) {
        down[i] = mine && (GetAsyncKeyState(keys[i]) & 0x8000) != 0;
        press[i] = down[i] && !held[i];
        held[i] = down[i];
    }
    // the gamepads, by position (HOS_PAD_*: host/nn_hid.c, whatever they are bound to): Home, or Back and Start together,
    // open and close it; the D-pad chooses and changes, the bottom face button is Enter, the right one Esc, the left one
    // Delete, the shoulders change tabs
    const uint32_t pad = mine && host && host->pad_raw ? host->pad_raw() : 0;
    static uint32_t pad_was;
    const uint32_t pad_press = pad & ~pad_was;
    const uint32_t chord = 1u << HOS_PAD_BACK | 1u << HOS_PAD_START;
    const bool chord_now = (pad & chord) == chord && (pad_was & chord) != chord;
    pad_was = pad;
    auto bit = [](uint32_t b, int n) { return (b >> n & 1) != 0; };
    const int scale = scale_of(dev.swap_extent.height);
    bool changed = scale != cur_scale || menu_refresh.exchange(false);
    cur_scale = scale;
    const int64_t now = prof_now();
    if (capturing >= 0) {   // waiting for a key or a pad button: nothing else works the menu meanwhile
        Row& r = rows[capturing];
        int any_key = 0;
        for (int vk = 1; vk < 255 && mine; vk++) {
            if ((vk >= 0xA0 && vk <= 0xA5) || vk == 0x5B || vk == 0x5C) continue;   // (and the Windows keys)   // (Shift, Ctrl, Alt as one key each)
            if (GetAsyncKeyState(vk) & 0x8000) { any_key = vk; break; }
        }
        const uint32_t pad_in = pad & ~(1u << HOS_PAD_GUIDE);   // (Home is the menu's)
        if (!cap_released) cap_released = !any_key && !pad_in;   // (the press that began it let go first)
        else if (now > cap_until || (any_key == VK_ESCAPE)) capturing = -1;
        else if (any_key == VK_BACK || any_key == VK_DELETE) clear_control(r, cap_slot), capturing = -1;
        else if (kind_of(r) == K_KEY && any_key && any_key != VK_INSERT && any_key != VK_F1)
            host->bind(HOS_BIND_KEY, r.lo, cap_slot, any_key), save(), capturing = -1;
        else if (kind_of(r) == K_PAD && pad_in)
            for (int b = 0; b < HOS_PAD_N; b++)
                if (bit(pad_in, b)) { host->bind(HOS_BIND_PAD, r.lo, 0, b), save(), capturing = -1; break; }
        if (capturing < 0) {   // (what was pressed is not also taken by the menu below)
            for (int i = 0; i < NKEYS; i++) press[i] = false, held[i] = true;
            pad_was = ~0u;
        }
        redraw_menu(scale);
        return;
    }
    if (press[K_INSERT] || press[K_F1] || bit(pad_press, HOS_PAD_GUIDE) || chord_now) open_now = !open_now.load(), changed = true;
    if (open_now.load()) {
        static int64_t since[2], next[2];
        const bool left = repeat(down[K_LEFT] || bit(pad, HOS_PAD_LEFT), now, since[0], next[0]);
        const bool right = repeat(down[K_RIGHT] || bit(pad, HOS_PAD_RIGHT), now, since[1], next[1]);
        if (rows[selected].tab != cur_tab) to_tab(cur_tab);
        if (const int n = wheel_notches.exchange(0)) wheel_scroll(n), changed = true;
        if (press[K_ESC] || bit(pad_press, HOS_PAD_EAST)) open_now = false, changed = true;
        if (press[K_UP] || bit(pad_press, HOS_PAD_UP)) move_sel(-1), changed = true;
        if (press[K_DOWN] || bit(pad_press, HOS_PAD_DOWN)) move_sel(+1), changed = true;
        if (press[K_TAB] || bit(pad_press, HOS_PAD_RSHOULDER)) to_tab(cur_tab + 1), changed = true;
        if (bit(pad_press, HOS_PAD_LSHOULDER)) to_tab(cur_tab - 1), changed = true;
        Row& cur = rows[selected];
        if (left || right) {
            if (kind_of(cur) == K_KEY) sel_slot = right ? 1 : 0;
            else change(cur, right ? +1 : -1);
            changed = true;
        }
        if ((press[K_BACK] || press[K_DEL] || bit(pad_press, HOS_PAD_WEST)) && (kind_of(cur) == K_KEY || kind_of(cur) == K_PAD))
            clear_control(cur, sel_slot), changed = true;
        if (press[K_ENTER] || bit(pad_press, HOS_PAD_SOUTH)) activate(selected, sel_slot, now), changed = true;
#ifdef _WIN32
        {   // the mouse: where it points in the menu's own pixels (the menu sits in the middle of the window's image)
            static POINT last{-1, -1};
            static int dragging = -1;
            POINT p{};
            const HWND win = app_window();
            RECT cr{};
            std::unique_lock lk(bmp_mu);
            const int mw = (int)menu_bmp.w, mh = (int)menu_bmp.h;
            lk.unlock();
            if (win && mine && mw && GetCursorPos(&p) && ScreenToClient(win, &p) && GetClientRect(win, &cr) && cr.right > 0 && cr.bottom > 0) {
                const int ix = (int)((long long)p.x * dev.swap_extent.width / cr.right) - ((int)dev.swap_extent.width - mw) / 2;
                const int iy = (int)((long long)p.y * dev.swap_extent.height / cr.bottom) - ((int)dev.swap_extent.height - mh) / 2;
                const bool moved = p.x != last.x || p.y != last.y;
                last = p;
                if (!down[K_MOUSE]) dragging = -1;
                if (dragging >= 0) {
                    for (int k = 0; k < hits.shown; k++)
                        if (hits.index[k] == dragging) set_from_x(rows[dragging], ix, hits.ctl[k], scale), changed = true;
                } else {
                    for (int k = 0; k < hits.shown; k++)
                        if (moved && in(hits.row[k], ix, iy) && selected != hits.index[k]) selected = hits.index[k], changed = true;
                    if (press[K_MOUSE]) {
                        if (in(hits.close, ix, iy)) open_now = false, changed = true;
                        for (int t = 0; t < kTabs; t++)
                            if (in(hits.tab[t], ix, iy) && t != cur_tab) to_tab(t), changed = true;
                        for (int k = 0; k < hits.shown; k++) {
                            if (!in(hits.ctl[k], ix, iy)) continue;
                            Row& r = rows[hits.index[k]];
                            selected = hits.index[k];
                            const Kind kd = kind_of(r);
                            if (kd == K_SLIDER) set_from_x(r, ix, hits.ctl[k], scale), dragging = hits.index[k];
                            else if (kd == K_TOGGLE) change(r, +1);
                            else if (kd == K_KEY) {
                                for (int slot = 0; slot < 2; slot++)
                                    if (in(hits.box[k][slot], ix, iy)) sel_slot = slot, activate(selected, slot, now);
                            } else if (kd == K_PAD || kd == K_ACTION) {
                                if (in(hits.box[k][0], ix, iy)) activate(selected, 0, now);
                            } else
                                change(r, ix < (hits.ctl[k].left + hits.ctl[k].right) / 2 ? -1 : +1);
                            changed = true;
                        }
                    }
                }
            }
        }
#endif
        if (changed && open_now.load()) redraw_menu(scale);
    }
    if (opts.show_fps) {
        static int64_t next = 0, last_game = 0, last_shown = 0;
        const int64_t now = prof_now();
        double shown = 0, game = 0;
        if (fps_take(now, shown_frames.load(), game_frames.load(), &next, &last_shown, &last_game, &shown, &game)) {
            snprintf(fps_text, sizeof fps_text, "%.0f", shown);
            if (opts.framegen >= 2) snprintf(fps_sub, sizeof fps_sub, "game %.0f", game);
            else fps_sub[0] = 0;
            fps_bmp.keyed = true;
            gdi_draw(fps_bmp, 120u * scale, 54u * scale, paint_fps, scale);
        }
    }
}

void menu_record(VkCommandBuffer cmd, VkImage dst, int slot, uint32_t w, uint32_t h) {
    if (opts.show_fps) {
        // The game image was just blitted (or cleared) into dst. Its write must
        // finish before the FPS pixels overwrite it; command order alone does
        // not order these transfer writes. The menu below has its own barrier.
        VkMemoryBarrier2 mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
        mb.srcStageMask = mb.dstStageMask = VK_PIPELINE_STAGE_2_ALL_TRANSFER_BIT;
        mb.srcAccessMask = mb.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
        VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
        di.memoryBarrierCount = 1, di.pMemoryBarriers = &mb;
        vkCmdPipelineBarrier2(cmd, &di);
        std::unique_lock lk(bmp_mu);
        const uint32_t fw = fps_bmp.w;
        lk.unlock();
        if (fw) copy_over(cmd, dst, fps_bmp, ups[1][slot], (int)w - (int)fw - (int)(12 * cur_scale), (int)(10 * cur_scale), w, h);
    }
    if (open_now.load(std::memory_order_relaxed)) {
        std::unique_lock lk(bmp_mu);
        const uint32_t mw = menu_bmp.w, mh = menu_bmp.h;
        lk.unlock();
        // (one copy finished before the next: on a Radeon the menu came out with its top torn away beside the FPS counter)
        VkMemoryBarrier2 mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
        mb.srcStageMask = mb.dstStageMask = VK_PIPELINE_STAGE_2_ALL_TRANSFER_BIT;
        mb.srcAccessMask = mb.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
        VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
        di.memoryBarrierCount = 1, di.pMemoryBarriers = &mb;
        vkCmdPipelineBarrier2(cmd, &di);
        if (mw) copy_over(cmd, dst, menu_bmp, ups[0][slot], (int)(w - mw) / 2, (int)(h - mh) / 2, w, h);
    }
}

} // namespace mkwvk
