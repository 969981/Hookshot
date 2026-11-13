/* DualSense haptics: the game's own HD Rumble values, played by the DualSense's voice coils (host/haptic.h).
   RenHDrum (third_party/renhdrum, MIT OR Apache-2.0) turns amp_low / freq_low / amp_high / freq_high into four channels
   of 48 kHz float audio, and the DualSense's own USB sound card plays channels 3 and 4 into its voice coils. The pad is
   the endpoint: no speaker is touched, and no other pad changes.

   Threads: the game thread pushes values (host/nn_hid.c), the pad thread sets the mode and the strength and reports the
   pads (host/pad_sdl.c), SDL's audio thread pulls the DSP's samples, and one worker here runs the silence watchdog, the
   endpoint's health and the test buzz. Locks, in this order and never two inner ones at once:
     g_life  the pad, the backend and the attach/detach (no other thread takes it)
     g_host  the pad's fields, held while SDL is called on it
     g_eng   the DSP and the last value each hand sent
     g_audio the stream and its device
     g_note  the detail line
   The audio thread only ever takes g_eng, and never for longer than one fill. */
#include "hos.h"
#include "haptic.h"
#include "hos_sdl.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdbool.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#ifdef _WIN32
#include "../third_party/renhdrum/renhdrum.h"
#endif

#define HAPTIC_FREQ_LOW 160.0f    /* the game's own when it sends none (its neutral value) */
#define HAPTIC_FREQ_HIGH 320.0f
#define HAPTIC_STALE_NS 250000000LL    /* a hand that sent nothing this long goes silent */
#define HAPTIC_ENUM_NS 2000000000LL    /* how often to look for the endpoint again */
#define HAPTIC_TEST_NS 400000000LL     /* the test's length: short, and it always ends */
#define HAPTIC_TEST_AMP 0.5f
#define HAPTIC_TEST_FREQ_LOW 180.0f
#define HAPTIC_TEST_FREQ_HIGH 320.0f
#define HAPTIC_DLL "renhdrum.dll"
#define HAPTIC_ABI 0x000300u           /* RenHDrum's, whose ABI 0x000301 keeps */

static volatile LONG g_mode = HOS_HAPTIC_AUTO;
static volatile LONG g_strength = 100;
static volatile LONG g_status = HOS_HAPTIC_NO_PAD;
static volatile LONG g_running;
static volatile LONG g_stop;
static volatile LONG g_testing;
static SRWLOCK g_life = SRWLOCK_INIT;
static SRWLOCK g_host = SRWLOCK_INIT;
static SRWLOCK g_eng = SRWLOCK_INIT;
static SRWLOCK g_audio = SRWLOCK_INIT;
static SRWLOCK g_note = SRWLOCK_INIT;
static struct {
    void *pad;
    int vendor;
    int wireless;   /* 1 Bluetooth, 0 cable, -1 SDL cannot say */
    int switch_hd;  /* the pad's own HD Rumble (a Switch Pro or Joy-Con): host/pad_sdl.c sends it */
    char name[64];
} g_pad;
static char g_detail[192];
static void *g_stream;
static uint32_t g_device;
#ifdef _WIN32
static RenHdcDualSenseDsp *g_dsp;
#endif
static int64_t g_at[2];      /* when each hand last sent a value (0 none) */
static int g_muted[2] = {1, 1};
static int64_t g_test_until;
static HANDLE g_thread;
static volatile LONG g_pushes, g_packets, g_fills, g_nonzero;

static void note(const char *fmt, ...) {
    char text[192];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    AcquireSRWLockExclusive(&g_note);
    snprintf(g_detail, sizeof g_detail, "%s", text);
    ReleaseSRWLockExclusive(&g_note);
}
static void detail_copy(char *out, int size) {
    AcquireSRWLockShared(&g_note);
    snprintf(out, (size_t)size, "%s", g_detail);
    ReleaseSRWLockShared(&g_note);
}
static float strength_scale(void) {   /* the menu's Rumble strength, once: 100 % is the game's own value */
    const int percent = (int)g_strength;
    const float scale = percent > 0 ? (float)percent / 100.0f : 0.0f;
    return scale > 2.0f ? 2.0f : scale;
}
static int pad_is_dualsense(void) {
    if (g_pad.vendor == 0x054C /* Sony */) return 1;
    return strstr(g_pad.name, "DualSense") != NULL || strstr(g_pad.name, "DualSense Edge") != NULL;
}

#ifdef _WIN32
/* ---- RenHDrum, loaded by its full path from beside the game: no folder of ours or PATH is searched for it ---- */
static struct {
    int tried, ok;
    HMODULE module;
    uint32_t (*version)(void);
    RenHdcDualSenseDsp *(*dsp_create)(int32_t volume_percent, bool crisp_notes);
    void (*dsp_destroy)(RenHdcDualSenseDsp *);
    int32_t (*dsp_set_half)(RenHdcDualSenseDsp *, bool is_right, float al, float fl, float ah, float fh);
    int32_t (*dsp_fill)(RenHdcDualSenseDsp *, float *out, size_t frames);
} rh;
#define RH_BIND(field, name) \
    if (!(*(FARPROC *)&rh.field = GetProcAddress(m, name))) { missing = name; goto failed; }
static int load_renhdrum(void) {
    if (rh.tried) return rh.ok;
    rh.tried = 1;
    wchar_t path[1024];
    DWORD n = GetModuleFileNameW(NULL, path, (DWORD)(sizeof path / sizeof path[0]));
    if (!n || n >= sizeof path / sizeof path[0]) {
        note("the game's own path could not be read: the standard rumble only");
        hos_log("haptic: GetModuleFileNameW failed: the standard rumble only");
        return 0;
    }
    while (n && path[n - 1] != L'\\' && path[n - 1] != L'/') n--;
    if (n + 32 >= sizeof path / sizeof path[0]) {
        note("the game's own path is too long: the standard rumble only");
        return 0;
    }
    const wchar_t *dll = L"renhdrum.dll";
    for (int i = 0; dll[i]; i++) path[n + i] = dll[i], path[n + i + 1] = 0;
    if (GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES) {
        note(HAPTIC_DLL " is not next to the game: the standard rumble only");
        hos_log("haptic: " HAPTIC_DLL " not next to the executable: the standard rumble only");
        return 0;
    }
    /* LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR: the DLL's own folder only, for its dependencies too. No PATH, no current folder. */
    HMODULE m = LoadLibraryExW(path, NULL, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!m) {
        note(HAPTIC_DLL " would not load (error %lu): the standard rumble only", (unsigned long)GetLastError());
        hos_log("haptic: " HAPTIC_DLL " would not load (error %lu): the standard rumble only", (unsigned long)GetLastError());
        return 0;
    }
    const char *missing = NULL;
    RH_BIND(version, "renhdrum_version");
    RH_BIND(dsp_create, "renhdrum_dualsense_dsp_create");
    RH_BIND(dsp_destroy, "renhdrum_dualsense_dsp_destroy");
    RH_BIND(dsp_set_half, "renhdrum_dualsense_dsp_set_half_target");
    RH_BIND(dsp_fill, "renhdrum_dualsense_dsp_fill_f32");
    {
        const uint32_t v = rh.version();
        if (v != HAPTIC_ABI && v != 0x000301u) {
            note(HAPTIC_DLL " is version %u.%u.%u, this build wants %u.%u.%u: the standard rumble only",
                 (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF, (HAPTIC_ABI >> 16) & 0xFF, (HAPTIC_ABI >> 8) & 0xFF, HAPTIC_ABI & 0xFF);
            hos_log("haptic: " HAPTIC_DLL " is version 0x%06x, expected 0x%06x: the standard rumble only", v, HAPTIC_ABI);
            FreeLibrary(m);
            return 0;
        }
        hos_log("haptic: " HAPTIC_DLL " %u.%u.%u loaded", (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF);
    }
    rh.module = m, rh.ok = 1;
    return 1;
failed:
    note(HAPTIC_DLL " has no %s: the standard rumble only", missing);
    hos_log("haptic: " HAPTIC_DLL " has no %s: the standard rumble only", missing);
    FreeLibrary(m);
    return 0;
}
#endif

/* ---- the endpoint ---- */
static void endpoint_pull(void *user, float *out, int frames) {
    (void)user;
    int filled = 0;
    AcquireSRWLockExclusive(&g_eng);
#ifdef _WIN32
    if (g_dsp && rh.dsp_fill(g_dsp, out, (size_t)frames) >= 0) filled = 1;
#endif
    if (!filled) {
        memset(out, 0, (size_t)frames * 4 * sizeof(float));   /* (nothing to play: silence, never a held waveform) */
    } else {
        float peak = 0.0f;
        unsigned loud = 0;
        for (int i = 0; i < frames; i++) {
            for (int ch = 2; ch <= 3; ch++) {   /* (the voice coils: channels 3 and 4) */
                const float v = out[i * 4 + ch];
                const float a = v < 0 ? -v : v;
                if (a > peak) peak = a;
                if (a > 0.0001f) loud++;
            }
        }
        InterlockedIncrement(&g_fills);
        if (loud) InterlockedExchangeAdd(&g_nonzero, (LONG)loud);
        (void)peak;
    }
    ReleaseSRWLockExclusive(&g_eng);
}
static int find_endpoint(uint32_t *device) {
    uint32_t ids[32];
    char names[32][128];
    int channels[32];
    const int n = hos_sdl_audio_endpoints(ids, names, channels, 32);
    int found = 0;
    for (int i = 0; i < n; i++) {
        const int named = strstr(names[i], "DualSense") || strstr(names[i], "Wireless Controller");
        if (!named) continue;
        hos_log("haptic: audio endpoint \"%s\": %d channels", names[i], channels[i]);
        if (channels[i] >= 4 && !found) *device = ids[i], found = 1;
    }
    if (!found) hos_log("haptic: %d playback devices, none of them a DualSense with four channels yet", n);
    return found;
}
/* call with g_life held and no other lock */
static int open_endpoint(void) {
#ifdef _WIN32
    uint32_t device = 0;
    if (!find_endpoint(&device)) {
        note("waiting for the DualSense's audio endpoint: plug it in by cable");
        return 0;
    }
    void *stream = hos_sdl_audio_endpoint_open(device, 48000, 4, endpoint_pull, NULL);
    if (!stream) {
        note("the DualSense's audio endpoint would not open: the standard rumble only");
        hos_log("haptic: the DualSense audio endpoint would not open");
        return 0;
    }
    /* The DSP runs at its full gain, always: the player's Rumble strength is applied once, in push(). Scaling it here as
       well is what once made 40 % come out at about 16 %. */
    RenHdcDualSenseDsp *dsp = rh.dsp_create(100, false);
    if (!dsp) {
        hos_sdl_audio_endpoint_close(stream);
        note("RenHDrum's haptics engine would not start: the standard rumble only");
        return 0;
    }
    AcquireSRWLockExclusive(&g_eng);
    g_dsp = dsp;
    g_at[0] = g_at[1] = 0;
    g_muted[0] = g_muted[1] = 1;
    ReleaseSRWLockExclusive(&g_eng);
    AcquireSRWLockExclusive(&g_audio);
    g_stream = stream, g_device = device;
    ReleaseSRWLockExclusive(&g_audio);
    /* the pad's motors stay quiet while the voice coils play: the two would fight */
    AcquireSRWLockShared(&g_host);
    if (g_pad.pad) hos_sdl_pad_rumble(g_pad.pad, 0, 0, 0);
    ReleaseSRWLockShared(&g_host);
    InterlockedExchange(&g_running, 1);
    note("4-channel 48 kHz audio on the wire");
    hos_log("haptic: 4-channel 48 kHz audio on the DualSense's endpoint");
    return 1;
#else
    note("haptics need Windows (" HAPTIC_DLL "): the standard rumble only");
    return 0;
#endif
}
static void close_endpoint(void) {
    void *stream;
    AcquireSRWLockExclusive(&g_audio);
    stream = g_stream, g_stream = NULL, g_device = 0;
    ReleaseSRWLockExclusive(&g_audio);
    InterlockedExchange(&g_running, 0);
    /* The stream first, and with no g_eng held: destroying it waits for the callback to return, and the callback takes
       g_eng. The DSP goes after it, so no fill can reach a freed DSP. */
    if (stream) hos_sdl_audio_endpoint_close(stream);
#ifdef _WIN32
    AcquireSRWLockExclusive(&g_eng);
    RenHdcDualSenseDsp *dsp = g_dsp;
    g_dsp = NULL;
    g_at[0] = g_at[1] = 0;
    g_muted[0] = g_muted[1] = 1;
    ReleaseSRWLockExclusive(&g_eng);
    if (dsp) rh.dsp_destroy(dsp);
#endif
}
static int endpoint_alive(void) {
    int alive;
    AcquireSRWLockShared(&g_audio);
    alive = hos_sdl_audio_endpoint_alive(g_stream);
    ReleaseSRWLockShared(&g_audio);
    return alive;
}

/* ---- what drives the pad now ---- */
static int want_status(void) {   /* (g_life held; g_host shared) */
    if (!g_pad.pad) return HOS_HAPTIC_NO_PAD;
    const int mode = (int)g_mode;
    if (mode == HOS_HAPTIC_OFF) return HOS_HAPTIC_DISABLED;
    if ((int)g_strength <= 0) return HOS_HAPTIC_DISABLED;   /* Rumble strength Off: no backend at all */
    if (g_pad.switch_hd && mode != HOS_HAPTIC_STANDARD) return HOS_HAPTIC_SWITCH_HD;
    if (!pad_is_dualsense()) return HOS_HAPTIC_STANDARD_RUMBLE;
    if (mode == HOS_HAPTIC_STANDARD) return HOS_HAPTIC_STANDARD_RUMBLE;
    if (g_pad.wireless == 1) return HOS_HAPTIC_DS_BT;   /* no four-channel endpoint over Bluetooth */
    return HOS_HAPTIC_DS_USB;
}
static void stop_haptics(void) {
    InterlockedExchange(&g_testing, 0);
    AcquireSRWLockExclusive(&g_eng);
    g_test_until = 0;
    ReleaseSRWLockExclusive(&g_eng);
    close_endpoint();
}
/* call with g_life held */
static void apply(void) {
    AcquireSRWLockShared(&g_host);
    const int want = want_status();
    ReleaseSRWLockShared(&g_host);
    const int was = (int)g_status;
    if (want != HOS_HAPTIC_DS_USB) {
        if (was == HOS_HAPTIC_DS_USB || g_stream) stop_haptics();
        InterlockedExchange(&g_status, want);
        switch (want) {
        case HOS_HAPTIC_NO_PAD: note("no controller"); break;
        case HOS_HAPTIC_DISABLED:
            note((int)g_mode == HOS_HAPTIC_OFF ? "Haptic feedback is Off" : "Rumble strength is Off");
            break;
        case HOS_HAPTIC_SWITCH_HD: note("the pad's own HD Rumble"); break;
        case HOS_HAPTIC_DS_BT: note("Bluetooth: haptics need a cable, so the standard rumble is in use"); break;
        case HOS_HAPTIC_STANDARD_RUMBLE:
            note((int)g_mode == HOS_HAPTIC_DUALSENSE && pad_is_dualsense() == 0
                     ? "this pad has no haptics, so the standard rumble is in use"
                     : "the standard rumble");
            break;
        default: break;
        }
        return;
    }
#ifdef _WIN32
    if (was == HOS_HAPTIC_DS_USB && g_stream) return;   /* (nothing changed: the stream stays) */
    stop_haptics();
    if (!load_renhdrum()) {
        InterlockedExchange(&g_status, HOS_HAPTIC_UNAVAILABLE);
        return;
    }
    InterlockedExchange(&g_status, open_endpoint() ? HOS_HAPTIC_DS_USB : HOS_HAPTIC_WAITING);
#else
    InterlockedExchange(&g_status, HOS_HAPTIC_UNAVAILABLE);
    note("haptics need Windows (" HAPTIC_DLL "): the standard rumble only");
#endif
}

/* ---- the worker: the watchdog, the endpoint's health and the test ---- */
static DWORD WINAPI haptic_main(LPVOID unused) {
    (void)unused;
    hos_host_thread_register_self("haptics (DualSense)");
    int64_t next_enum = 0;
    uint64_t next_report = 0;
    while (!g_stop) {
        const int64_t now = hos_now_ns();
        const int status = (int)g_status;
        /* a hand that sent nothing goes silent: the DSP would otherwise hold its last waveform for ever */
        if (status == HOS_HAPTIC_DS_USB && (int)g_running) {
            AcquireSRWLockExclusive(&g_eng);
#ifdef _WIN32
            if (g_dsp)
                for (int s = 0; s < 2; s++) {
                    if (g_at[s] && !g_muted[s] && now - g_at[s] > HAPTIC_STALE_NS) {
                        rh.dsp_set_half(g_dsp, s == 1, 0.0f, HAPTIC_FREQ_LOW, 0.0f, HAPTIC_FREQ_HIGH);
                        g_muted[s] = 1;
                    }
                }
#endif
            ReleaseSRWLockExclusive(&g_eng);
        }
        /* the test: a short, gentle pattern through whatever backend is playing, then silence */
        int64_t until;
        AcquireSRWLockExclusive(&g_eng);
        until = g_test_until;
        if (until && now >= until) {
            g_test_until = 0;
            InterlockedExchange(&g_testing, 0);
            until = 0;
#ifdef _WIN32
            if (g_dsp) rh.dsp_set_half(g_dsp, 0, 0.0f, HAPTIC_FREQ_LOW, 0.0f, HAPTIC_FREQ_HIGH),
                        rh.dsp_set_half(g_dsp, 1, 0.0f, HAPTIC_FREQ_LOW, 0.0f, HAPTIC_FREQ_HIGH);
#endif
            g_at[0] = g_at[1] = 0;
            g_muted[0] = g_muted[1] = 1;
        } else if (until) {
#ifdef _WIN32
            if (g_dsp) rh.dsp_set_half(g_dsp, 0, HAPTIC_TEST_AMP, HAPTIC_TEST_FREQ_LOW, HAPTIC_TEST_AMP, HAPTIC_TEST_FREQ_HIGH),
                        rh.dsp_set_half(g_dsp, 1, HAPTIC_TEST_AMP, HAPTIC_TEST_FREQ_LOW, HAPTIC_TEST_AMP, HAPTIC_TEST_FREQ_HIGH);
#endif
            g_at[0] = g_at[1] = now;
            g_muted[0] = g_muted[1] = 0;
        }
        ReleaseSRWLockExclusive(&g_eng);
        if (until && status != HOS_HAPTIC_DS_USB) {   /* (the motors buzz for a test too) */
            AcquireSRWLockShared(&g_host);
            if (g_pad.pad) hos_sdl_pad_rumble(g_pad.pad, 24000, 12000, 120);
            ReleaseSRWLockShared(&g_host);
        }
        /* the endpoint: gone, or not there yet */
        if (status == HOS_HAPTIC_DS_USB) {
            if (!endpoint_alive()) {
                AcquireSRWLockExclusive(&g_life);
                if ((int)g_status == HOS_HAPTIC_DS_USB && !endpoint_alive()) {
                    stop_haptics();
                    InterlockedExchange(&g_status, HOS_HAPTIC_WAITING);
                    note("the DualSense's audio endpoint went away: looking for it again");
                    hos_log("haptic: the DualSense audio endpoint went away: looking for it again");
                    next_enum = 0;
                }
                ReleaseSRWLockExclusive(&g_life);
            }
        } else if (status == HOS_HAPTIC_WAITING && now >= next_enum) {
            next_enum = now + HAPTIC_ENUM_NS;
            AcquireSRWLockExclusive(&g_life);
            if ((int)g_status == HOS_HAPTIC_WAITING && open_endpoint()) InterlockedExchange(&g_status, HOS_HAPTIC_DS_USB);
            ReleaseSRWLockExclusive(&g_life);
        }
        if (now >= (int64_t)next_report) {   /* a line every 5 s, when it has something to say */
            next_report = (uint64_t)(now + 5000000000LL);
            if (status == HOS_HAPTIC_DS_USB || g_pushes)
                hos_log("haptic: %s, %ld values, %ld audio fills, %ld loud samples", status == HOS_HAPTIC_DS_USB ? "DualSense haptics (USB)" : "no haptics",
                        (long)g_pushes, (long)g_fills, (long)g_nonzero);
        }
        Sleep(1);
    }
    return 0;
}
static void start_worker(void) {
    if (g_thread) return;
    InterlockedExchange(&g_stop, 0);
    g_thread = CreateThread(NULL, 0, haptic_main, NULL, 0, NULL);
    if (g_thread) hos_log("haptic: worker started");
}
static void stop_worker(void) {
    HANDLE thread;
    AcquireSRWLockExclusive(&g_host);
    thread = g_thread, g_thread = NULL;
    ReleaseSRWLockExclusive(&g_host);
    if (!thread) return;
    InterlockedExchange(&g_stop, 1);
    /* the worker may be inside hos_sdl_pad_rumble or the DSP fill: no lock of ours is held here, so this cannot deadlock */
    WaitForSingleObject(thread, 2000);
    CloseHandle(thread);
}

/* ---- what the rest of the host calls ---- */
void hos_haptic_mode(int mode) {
    if (mode < HOS_HAPTIC_AUTO || mode > HOS_HAPTIC_OFF) mode = HOS_HAPTIC_AUTO;
    if ((int)g_mode == mode) return;
    InterlockedExchange(&g_mode, mode);
    AcquireSRWLockExclusive(&g_life);
    apply();
    if ((int)g_status == HOS_HAPTIC_DS_USB || (int)g_status == HOS_HAPTIC_WAITING) start_worker();
    ReleaseSRWLockExclusive(&g_life);
}
void hos_haptic_strength(int percent) {
    if (percent < 0) percent = 0;
    if (percent > 150) percent = 150;
    const int was = (int)g_strength;
    if (was == percent) return;
    InterlockedExchange(&g_strength, percent);
    /* 0 and back changes what plays at all; any other step is read by the next value: the stream is not rebuilt */
    if ((was <= 0) != (percent <= 0)) {
        AcquireSRWLockExclusive(&g_life);
        apply();
        ReleaseSRWLockExclusive(&g_life);
    }
}
void hos_haptic_pad_added(void *pad, int vendor, const char *name, int wireless, int switch_hd) {
    if (!pad) return;
    AcquireSRWLockExclusive(&g_life);
    AcquireSRWLockExclusive(&g_host);
    g_pad.pad = pad, g_pad.vendor = vendor, g_pad.wireless = wireless, g_pad.switch_hd = switch_hd;
    snprintf(g_pad.name, sizeof g_pad.name, "%s", name ? name : "?");
    ReleaseSRWLockExclusive(&g_host);
    apply();
    if ((int)g_status == HOS_HAPTIC_DS_USB || (int)g_status == HOS_HAPTIC_WAITING) start_worker();
    ReleaseSRWLockExclusive(&g_life);
    hos_log("haptic: pad \"%s\": %s", name ? name : "?", g_detail);
}
void hos_haptic_pad_removed(void *pad) {
    AcquireSRWLockExclusive(&g_life);
    AcquireSRWLockExclusive(&g_host);
    const int mine = pad && g_pad.pad == pad;
    if (mine) memset(&g_pad, 0, sizeof g_pad), g_pad.wireless = -1;
    ReleaseSRWLockExclusive(&g_host);
    if (mine) apply();
    if ((int)g_status != HOS_HAPTIC_DS_USB && (int)g_status != HOS_HAPTIC_WAITING) stop_worker();
    ReleaseSRWLockExclusive(&g_life);
}
void hos_haptic_push(int hand, const float value[4]) {
    if (hand < 0 || hand > 1 || !value) return;
    const int mode = (int)g_mode;
    if (mode == HOS_HAPTIC_OFF) return;
    const int status = (int)g_status;
    if (status != HOS_HAPTIC_DS_USB) return;
    const float scale = strength_scale();
    if (scale <= 0.0f) return;   /* Rumble strength Off: nothing at all */
    float al = value[0] * scale, ah = value[2] * scale;
    al = al < 0.0f ? 0.0f : al > 1.0f ? 1.0f : al;
    ah = ah < 0.0f ? 0.0f : ah > 1.0f ? 1.0f : ah;
    const float fl = value[1] > 0.0f ? value[1] : HAPTIC_FREQ_LOW;
    const float fh = value[3] > 0.0f ? value[3] : HAPTIC_FREQ_HIGH;
    const int64_t now = hos_now_ns();
    int played = 0;
    AcquireSRWLockExclusive(&g_eng);
#ifdef _WIN32
    if (g_dsp) played = rh.dsp_set_half(g_dsp, hand == 1, al, fl, ah, fh) >= 0;
#endif
    if (played) g_at[hand] = now, g_muted[hand] = 0;
    ReleaseSRWLockExclusive(&g_eng);
    if (played) {
        InterlockedIncrement(&g_pushes);
        if (al > 0.0f || ah > 0.0f) InterlockedIncrement(&g_nonzero);
    }
}
int hos_haptic_owns(void *pad) {
    if (!pad) return 0;
    if ((int)g_status != HOS_HAPTIC_DS_USB || !g_running) return 0;
    AcquireSRWLockShared(&g_host);
    const int mine = g_pad.pad == pad;
    ReleaseSRWLockShared(&g_host);
    return mine;
}
int hos_haptic_test(void) {
    if ((int)g_mode == HOS_HAPTIC_OFF || (int)g_strength <= 0) return 0;
    const int status = (int)g_status;
    if (status == HOS_HAPTIC_NO_PAD || status == HOS_HAPTIC_DISABLED || status == HOS_HAPTIC_UNAVAILABLE) return 0;
    if (status == HOS_HAPTIC_DS_USB && !g_running) return 0;
    if (!g_thread) start_worker();
    if (!g_thread) return 0;
    AcquireSRWLockExclusive(&g_eng);
    g_test_until = hos_now_ns() + HAPTIC_TEST_NS;
    ReleaseSRWLockExclusive(&g_eng);
    InterlockedExchange(&g_testing, 1);
    hos_log("haptic: test, %d ms", (int)(HAPTIC_TEST_NS / 1000000));
    return 1;
}
int hos_haptic_testing(void) { return (int)g_testing; }
int hos_haptic_status(void) { return (int)g_status; }
void hos_haptic_shutdown(void) {
    AcquireSRWLockExclusive(&g_life);
    stop_haptics();
    stop_worker();
    AcquireSRWLockExclusive(&g_host);
    memset(&g_pad, 0, sizeof g_pad), g_pad.wireless = -1;
    ReleaseSRWLockExclusive(&g_host);
    InterlockedExchange(&g_status, HOS_HAPTIC_NO_PAD);
    ReleaseSRWLockExclusive(&g_life);
}
