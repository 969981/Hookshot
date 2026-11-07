/* (hookshot) The game's window on Linux, on SDL3 (Wayland first, else X11), in place of host/display.c: the renderer's
   surface (gpu/vk, through HosLinuxWindow: gpu/vk/vk_api.h). As display.c: HOS_HEADLESS=1, no window;
   HOS_WINDOW_SIZE=<w>x<h>, HOS_FULLSCREEN=1, HOS_WINDOW_NOACTIVATE=1; F11 fullscreen; a click captures the mouse
   (relative motion, read by nn_hid.c), Esc or leaving the window releases it; closing the window ends the run.

   It also answers the window calls of host/linux/windows.h: GetAsyncKeyState (the keyboard as SDL last saw it),
   GetForegroundWindow (the game's window while it has the keyboard), GetClientRect, GetSystemMetrics. */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <windows.h>
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#include "../../gpu/render/rx.h"
#include "../../gpu/vk/vk_api.h"

volatile LONG64 hos_present_count;   /* (nvn_native.c: the game's presents) */

/* The app's identity, before SDL starts anything (the boot's screen query, the audio, the window, whichever is first):
   Wayland's app_id and X11's WM_CLASS, the desktop file's name, what the compositor and the mixer show */
void hos_sdl_identity(void) {
    static volatile LONG done;
    if (InterlockedExchange(&done, 1)) return;
    SDL_SetAppMetadata("Hookshot", NULL, "io.github.gab9281.Hookshot");
}
static SDL_Window *window;
static int renderer;

static volatile LONG mouse_dx, mouse_dy, mouse_captured;
int hos_mouse_take(int32_t *dx, int32_t *dy) {
    *dx = InterlockedExchange(&mouse_dx, 0);
    *dy = InterlockedExchange(&mouse_dy, 0);
    return mouse_captured;
}
static void mouse_capture(int on) {
    if (on == (int)mouse_captured) return;
    mouse_captured = on;
    InterlockedExchange(&mouse_dx, 0);
    InterlockedExchange(&mouse_dy, 0);
    if (window) SDL_SetWindowRelativeMouseMode(window, on != 0);
}

static int fullscreen;
static void set_fullscreen(int on) {
    if (on == fullscreen || !window) return;
    fullscreen = on;
    SDL_SetWindowFullscreen(window, on != 0);   /* (no mode set: the desktop's, borderless) */
    hos_log("display: %s", on ? "fullscreen (F11 for a window)" : "windowed");
}

/* what the Vulkan back end asks of the window */
static int vk_surface(void *native, void *instance, uint64_t *surface) {
    VkSurfaceKHR s = 0;
    if (!SDL_Vulkan_CreateSurface((SDL_Window *)native, (VkInstance)instance, NULL, &s)) {
        hos_log("display: no Vulkan surface: %s", SDL_GetError());
        return 0;
    }
    *surface = (uint64_t)s;
    return 1;
}
static void drawable_size(void *native, int *w, int *h) { SDL_GetWindowSizeInPixels((SDL_Window *)native, w, h); }
/* (asked from the render thread: SDL's window calls belong to the window's own, which pump() is) */
static Uint32 fullscreen_event;
static void ask_fullscreen(void *native, int on) {
    (void)native;
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = fullscreen_event;
    e.user.code = on;
    if (fullscreen_event) SDL_PushEvent(&e);
}
static int is_fullscreen(void *native) { (void)native; return fullscreen; }
static HosLinuxWindow surface_desc = {0, vk_surface, drawable_size, ask_fullscreen, is_fullscreen};
static HANDLE renderer_up;

/* (hookshot) as display.c: the game's first work for the GPU waits for the renderer (what it submitted while the shaders
   were prepared was lost: no ambient light, black foliage, dark characters) */
void hos_renderer_wait(void) {
    if (renderer_up) WaitForSingleObject(renderer_up, INFINITE);
}

static void pump(void) {
    void hos_sdl_pads_pump(void);   /* host/pad_sdl.c: the controllers, on this thread as SDL's queue wants */
    SDL_Event e;
    for (;;) {
        hos_sdl_pads_pump();
        if (!SDL_WaitEventTimeout(&e, 4)) return;   /* (4 ms: the controllers' poll, as their own thread had on Windows) */
        switch (e.type) {
        default:
            if (fullscreen_event && e.type == fullscreen_event) set_fullscreen(e.user.code);
            break;
        case SDL_EVENT_QUIT:
            hos_log("display: window closed, exiting");
            hos_log_flush();
            ExitProcess(0);
        case SDL_EVENT_MOUSE_BUTTON_DOWN:
            if (e.button.button == SDL_BUTTON_LEFT && renderer && !mouse_captured) mouse_capture(1);
            break;
        case SDL_EVENT_MOUSE_MOTION:
            if (mouse_captured) {   /* (SDL3's motion is in fractions of a pixel: the remainders kept for the next) */
                static float rx, ry;
                rx += e.motion.xrel, ry += e.motion.yrel;
                const LONG dx = (LONG)rx, dy = (LONG)ry;
                rx -= (float)dx, ry -= (float)dy;
                InterlockedExchangeAdd(&mouse_dx, dx);
                InterlockedExchangeAdd(&mouse_dy, dy);
            }
            break;
        case SDL_EVENT_KEY_DOWN:
            if (e.key.scancode == SDL_SCANCODE_ESCAPE) mouse_capture(0);
            if (e.key.scancode == SDL_SCANCODE_F11 && !e.key.repeat) set_fullscreen(!fullscreen);
            break;
        case SDL_EVENT_WINDOW_FOCUS_LOST:
        case SDL_EVENT_WINDOW_MOUSE_LEAVE:
            mouse_capture(0);
            break;
        case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
            if (renderer) {
                int w = 0, h = 0;
                SDL_GetWindowSizeInPixels(window, &w, &h);
                rx_resize(w, h);
            }
            break;
        }
    }
}

static DWORD WINAPI display_main(void *p) {
    (void)p;
    SDL_SetHint(SDL_HINT_VIDEO_ALLOW_SCREENSAVER, "0");
    if (getenv("HOS_WINDOW_NOACTIVATE")) SDL_SetHint(SDL_HINT_WINDOW_ACTIVATE_WHEN_SHOWN, "0");
    hos_sdl_identity();
    if (!SDL_InitSubSystem(SDL_INIT_VIDEO)) {
        hos_log("display: no window (%s): the renderer draws offscreen", SDL_GetError());
        rx_start(NULL);
        SetEvent(renderer_up);
        return 0;
    }
    extern float hos_aspect_ratio;
    int win_w = hos_aspect_ratio > 1.8f ? (int)(720 * hos_aspect_ratio + 0.5f) : 1280, win_h = 720;
    if (getenv("HOS_WINDOW_SIZE")) {
        int w = 0, h = 0;
        if (sscanf(getenv("HOS_WINDOW_SIZE"), "%dx%d", &w, &h) == 2 && w >= 64 && h >= 64) win_w = w, win_h = h;
    }
    window = SDL_CreateWindow(HOS_GAME_TITLE, win_w, win_h, SDL_WINDOW_VULKAN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!window) {
        hos_log("display: no window (%s): the renderer draws offscreen", SDL_GetError());
        rx_start(NULL);
        SetEvent(renderer_up);
        return 0;
    }
    if (getenv("HOS_FULLSCREEN") && atoi(getenv("HOS_FULLSCREEN"))) set_fullscreen(1);
    hos_log("display: SDL %d.%d.%d on %s", SDL_VERSIONNUM_MAJOR(SDL_GetVersion()), SDL_VERSIONNUM_MINOR(SDL_GetVersion()),
            SDL_VERSIONNUM_MICRO(SDL_GetVersion()), SDL_GetCurrentVideoDriver());
    fullscreen_event = SDL_RegisterEvents(1);   /* (0 when none is left) */
    surface_desc.native = window;
    renderer = rx_start(&surface_desc);
    if (!renderer) hos_log("display: no Vulkan renderer: the window stays black");
    SetEvent(renderer_up);
    for (;;) pump();
    return 0;
}

void hos_display_start(void) {
    if (getenv("HOS_HEADLESS")) {   /* no window: the renderer still draws, offscreen (screenshots, frame dumps) */
        rx_start(NULL);
        return;
    }
    renderer_up = CreateEventA(NULL, TRUE, FALSE, NULL);
    CreateThread(NULL, 0, display_main, NULL, 0, NULL);
}

/* ---- host/linux/windows.h's window calls ---- */
HWND GetForegroundWindow(void) { return window && SDL_GetKeyboardFocus() == window ? (HWND)window : NULL; }
DWORD GetWindowThreadProcessId(HWND w, DWORD *pid) {
    if (pid) *pid = w ? (DWORD)getpid() : 0;
    return w ? 1 : 0;
}
LONG_PTR GetWindowLongPtrA(HWND w, int index) { (void)w, (void)index; return 0; }
int GetClassNameA(HWND w, LPSTR out, int cap) {
    if (cap <= 0) return 0;
    snprintf(out, (size_t)cap, "%s", w ? "Hookshot" : "");
    return (int)strlen(out);
}
BOOL GetClientRect(HWND w, RECT *r) {
    int x = 0, y = 0;
    if (w) SDL_GetWindowSizeInPixels((SDL_Window *)w, &x, &y);
    r->left = r->top = 0;
    r->right = x, r->bottom = y;
    return w != NULL;
}
/* (the screen's size once the window's thread started SDL's video: before that, as for hos_main.c's ultrawide
   check, 0, read as "not known") */
/* (the boot's ultrawide check asks before the display thread has started SDL's video: started here then, once, and
   kept; SDL counts the display thread's start as a second user) */
int GetSystemMetrics(int index) {
    static volatile LONG tried;
    if (!SDL_WasInit(SDL_INIT_VIDEO) && !InterlockedExchange(&tried, 1) && (getenv("DISPLAY") || getenv("WAYLAND_DISPLAY"))) {
        hos_sdl_identity();
        SDL_InitSubSystem(SDL_INIT_VIDEO);
    }
    const SDL_DisplayMode *m = SDL_WasInit(SDL_INIT_VIDEO) ? SDL_GetDesktopDisplayMode(SDL_GetPrimaryDisplay()) : NULL;
    if (!m) return 0;
    return index == SM_CXSCREEN ? m->w : index == SM_CYSCREEN ? m->h : 0;
}

static SDL_Scancode scancode_of(int vk) {
    if (vk >= 'A' && vk <= 'Z') return (SDL_Scancode)(SDL_SCANCODE_A + (vk - 'A'));
    if (vk >= '1' && vk <= '9') return (SDL_Scancode)(SDL_SCANCODE_1 + (vk - '1'));
    if (vk == '0') return SDL_SCANCODE_0;
    if (vk >= VK_F1 && vk <= VK_F12) return (SDL_Scancode)(SDL_SCANCODE_F1 + (vk - VK_F1));
    if (vk >= VK_NUMPAD0 + 1 && vk <= VK_NUMPAD0 + 9) return (SDL_Scancode)(SDL_SCANCODE_KP_1 + (vk - VK_NUMPAD0 - 1));
    switch (vk) {
    case VK_NUMPAD0: return SDL_SCANCODE_KP_0;
    case VK_BACK: return SDL_SCANCODE_BACKSPACE;
    case VK_TAB: return SDL_SCANCODE_TAB;
    case VK_RETURN: return SDL_SCANCODE_RETURN;
    case VK_PAUSE: return SDL_SCANCODE_PAUSE;
    case VK_CAPITAL: return SDL_SCANCODE_CAPSLOCK;
    case VK_ESCAPE: return SDL_SCANCODE_ESCAPE;
    case VK_SPACE: return SDL_SCANCODE_SPACE;
    case VK_PRIOR: return SDL_SCANCODE_PAGEUP;
    case VK_NEXT: return SDL_SCANCODE_PAGEDOWN;
    case VK_END: return SDL_SCANCODE_END;
    case VK_HOME: return SDL_SCANCODE_HOME;
    case VK_LEFT: return SDL_SCANCODE_LEFT;
    case VK_UP: return SDL_SCANCODE_UP;
    case VK_RIGHT: return SDL_SCANCODE_RIGHT;
    case VK_DOWN: return SDL_SCANCODE_DOWN;
    case VK_INSERT: return SDL_SCANCODE_INSERT;
    case VK_DELETE: return SDL_SCANCODE_DELETE;
    case VK_MULTIPLY: return SDL_SCANCODE_KP_MULTIPLY;
    case VK_ADD: return SDL_SCANCODE_KP_PLUS;
    case VK_SUBTRACT: return SDL_SCANCODE_KP_MINUS;
    case VK_DECIMAL: return SDL_SCANCODE_KP_PERIOD;
    case VK_DIVIDE: return SDL_SCANCODE_KP_DIVIDE;
    case VK_LSHIFT: return SDL_SCANCODE_LSHIFT;
    case VK_RSHIFT: return SDL_SCANCODE_RSHIFT;
    case VK_LCONTROL: return SDL_SCANCODE_LCTRL;
    case VK_RCONTROL: return SDL_SCANCODE_RCTRL;
    case VK_LMENU: return SDL_SCANCODE_LALT;
    case VK_RMENU: return SDL_SCANCODE_RALT;
    case VK_OEM_1: return SDL_SCANCODE_SEMICOLON;
    case VK_OEM_PLUS: return SDL_SCANCODE_EQUALS;
    case VK_OEM_COMMA: return SDL_SCANCODE_COMMA;
    case VK_OEM_MINUS: return SDL_SCANCODE_MINUS;
    case VK_OEM_PERIOD: return SDL_SCANCODE_PERIOD;
    case VK_OEM_2: return SDL_SCANCODE_SLASH;
    case VK_OEM_3: return SDL_SCANCODE_GRAVE;
    case VK_OEM_4: return SDL_SCANCODE_LEFTBRACKET;
    case VK_OEM_5: return SDL_SCANCODE_BACKSLASH;
    case VK_OEM_6: return SDL_SCANCODE_RIGHTBRACKET;
    case VK_OEM_7: return SDL_SCANCODE_APOSTROPHE;
    }
    return SDL_SCANCODE_UNKNOWN;
}
short GetAsyncKeyState(int vk) {
    if (!window) return 0;
    if (vk == VK_LBUTTON || vk == VK_RBUTTON || vk == VK_MBUTTON) {
        const SDL_MouseButtonFlags b = SDL_GetMouseState(NULL, NULL);
        const Uint32 want = vk == VK_LBUTTON ? SDL_BUTTON_LMASK : vk == VK_RBUTTON ? SDL_BUTTON_RMASK : SDL_BUTTON_MMASK;
        return (b & want) ? (short)0x8000 : 0;
    }
    const bool *k = SDL_GetKeyboardState(NULL);
    int down;
    switch (vk) {   /* (either side, as Windows' VK_SHIFT / VK_CONTROL / VK_MENU) */
    case VK_SHIFT: down = k[SDL_SCANCODE_LSHIFT] || k[SDL_SCANCODE_RSHIFT]; break;
    case VK_CONTROL: down = k[SDL_SCANCODE_LCTRL] || k[SDL_SCANCODE_RCTRL]; break;
    case VK_MENU: down = k[SDL_SCANCODE_LALT] || k[SDL_SCANCODE_RALT]; break;
    default: {
        const SDL_Scancode s = scancode_of(vk);
        down = s != SDL_SCANCODE_UNKNOWN && k[s];
    }
    }
    return down ? (short)0x8000 : 0;
}
