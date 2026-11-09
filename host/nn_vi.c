/* nn::vi for MKW (TOTKPort's nn_vi.c is bound to its renderer and TOTK's frame pacing): the default display, one
   layer, and the display's vsync event, signaled at 60 Hz by a host thread. HDR and VRR are reported unsupported,
   so the game takes its SDR path. Frames are paced at the native NVN layer's window acquire (the end of this file). */
#include "hos.h"
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void hos_system_event_init(uint64_t key, int autoclear);   /* nn_os_sync.c */
void hos_system_event_signal(uint64_t key);

static uint64_t display, layer;
static uint64_t vsync_events[8];
static volatile LONG nvsync;
static HANDLE vsync_thread;

static DWORD WINAPI vsync_main(void *p) {
    (void)p;
    const int64_t period = 1000000000LL / 60;
    int64_t next = hos_now_ns() + period;
    for (;;) {
        const int64_t now = hos_now_ns();
        if (next > now) hos_sleep_ns(next - now);
        next += period;
        if (next < hos_now_ns()) next = hos_now_ns() + period;   /* (fell behind: no burst of vsyncs) */
        for (LONG i = 0; i < nvsync; i++) hos_system_event_signal(vsync_events[i]);
    }
    return 0;
}

/* nn::vi::Initialize() */
HLE(_ZN2nn2vi10InitializeEv) {}

/* nn::vi::OpenDefaultDisplay(nn::vi::Display**) -> Result */
HLE(_ZN2nn2vi18OpenDefaultDisplayEPPNS0_7DisplayE) {
    if (!display) display = hos_sys_alloc(0x100, 16);
    G64(ARG(0)) = display;
    RET(0);
}

/* nn::vi::CreateLayer(nn::vi::Layer**, nn::vi::Display*) -> Result */
HLE(_ZN2nn2vi11CreateLayerEPPNS0_5LayerEPNS0_7DisplayE) {
    if (!layer) layer = hos_sys_alloc(0x100, 16);
    G64(ARG(0)) = layer;
    RET(0);
}

/* nn::vi::GetDisplayVsyncEvent(nn::os::SystemEventType*, nn::vi::Display*) -> Result */
HLE(_ZN2nn2vi20GetDisplayVsyncEventEPNS_2os15SystemEventTypeEPNS0_7DisplayE) {
    hos_system_event_init(ARG(0), 1);
    if (nvsync < (LONG)(sizeof vsync_events / sizeof vsync_events[0])) {
        vsync_events[nvsync] = ARG(0);
        InterlockedIncrement(&nvsync);
    }
    if (!vsync_thread) vsync_thread = CreateThread(NULL, 0, vsync_main, NULL, 0, NULL);
    RET(0);
}

/* nn::vi::QueryDisplayHdrStatus(bool* on, HdrOutputFormat*, unsigned short* max nits, Display const*) -> Result */
HLE(_ZN2nn2vi21QueryDisplayHdrStatusEPbPNS0_15HdrOutputFormatEPtPKNS0_7DisplayE) {
    *(uint8_t *)GP(ARG(0)) = 0;
    if (ARG(1)) G32(ARG(1)) = 0;
    if (ARG(2)) *(uint16_t *)GP(ARG(2)) = 0;
    RET(0);
}
/* nn::vi::QueryDisplayHdrPaperWhite(unsigned short*, Display const*) -> Result */
HLE(_ZN2nn2vi25QueryDisplayHdrPaperWhiteEPtPKNS0_7DisplayE) {
    *(uint16_t *)GP(ARG(0)) = 203;
    RET(0);
}
/* nn::vi::QueryDisplayVrrSupported(bool*, Display const*) -> Result */
HLE(_ZN2nn2vi24QueryDisplayVrrSupportedEPbPKNS0_7DisplayE) {
    *(uint8_t *)GP(ARG(0)) = 0;
    RET(0);
}
HLE(_ZN2nn2vi23SetDisplayVrrCapabilityEPNS0_7DisplayEb) { RET(0); }
HLE(_ZN2nn2vi28SetLayerExtendedSwapIntervalEPNS0_5LayerEii) { RET(0); }
HLE(_ZN2nn2vi30ClearLayerExtendedSwapIntervalEPNS0_5LayerE) { RET(0); }

/* nn::vi::GetNativeWindow(void**, nn::vi::Layer*) -> Result: a window object (NVN builds its window on it; the
   native NVN layer only keeps the pointer) */
HLE(_ZN2nn2vi15GetNativeWindowEPPvPNS0_5LayerE) {
    static uint64_t window;
    if (!window) window = hos_sys_alloc(0x100, 16);
    G64(ARG(0)) = window;
    hos_log("vi: GetNativeWindow -> %#llx", (unsigned long long)window);
    RET(0);
}

/* ---- frame pacing for the native NVN layer's window (nvn_native.c) ----
   The game acquires a window texture before each frame and presents it after; on the console the acquire blocks
   while every texture waits for the display. Here the acquire waits for the frame's display time: the present
   interval's vsyncs (1: 60 fps, 2: 30) after the last present, so the game runs at the display's rate.
   HOS_NO_PACING=1: as fast as it can. */
static volatile int present_interval = 1;
static int64_t next_frame_at, window_start, frames_in_window;
static volatile LONG64 presents;
int hos_vi_native_interval(Ctx *c, int requested) {
    (void)c;
    /* (hookshot) OoT's frame-rate controller asks for 2, 3 or 4 vsyncs a frame whenever one runs long (480 changes a minute
       in Kokiri): paced to them, frames snapped to 33, 50 and 67 ms, the stutter, and the frame rate sat far under what the
       PC managed. With game time following the clock (the Insert menu's Real time, realtime_step), a frame is shown as soon
       as it is ready, 60 at most; the game still reads back its own interval. HOS_GAME_INTERVAL=1, or Game speed Per frame:
       paced to the game's interval, as before */
    static int game_interval = -1;
    if (game_interval < 0) game_interval = getenv("HOS_GAME_INTERVAL") && atoi(getenv("HOS_GAME_INTERVAL")) != 0;
    int rx_option(int id, int otherwise);
    const int clamped = requested < 1 ? 1 : requested > 4 ? 4 : requested;
    present_interval = game_interval || !rx_option(0, 1) ? clamped : 1;
    return requested;
}
/* (hookshot) The window's textures as the console's display queue has them: each present is shown at its display
   time, the later of its present and one interval after the frame shown before it, and a texture is free again once
   the frame presented after it is shown. The acquire of a frame waits for its texture: with `active` textures, the
   texture of the frame `active` back, freed when the frame after that one is shown. With three (OoT) a frame slower
   than the display waits for nothing and a fast one is held to the display's rate, two frames queued at most;
   the acquire waited for one interval after the last present before (two textures' behaviour), which held every
   frame over 16.7 ms to the next vsync step (gameplay at 27 ms frames, 5 ms of them in this sleep). HOS_VI_DOUBLE=1:
   that behaviour */
static int64_t shown_at[16];
static volatile LONG64 shown_count;
void hos_vi_native_acquire(int active) {
    static int no_pacing = -1, double_buffered = -1;
    if (no_pacing < 0) no_pacing = getenv("HOS_NO_PACING") != NULL;
    if (double_buffered < 0) double_buffered = getenv("HOS_VI_DOUBLE") != NULL;
    if (no_pacing || !next_frame_at) return;
    int64_t until = next_frame_at;
    if (!double_buffered && active >= 2) {
        const int64_t n = shown_count, frame = n - active + 1;   /* (the frame whose showing frees this texture) */
        until = frame >= 0 && frame < n ? shown_at[frame & 15] : 0;
    }
    const int64_t now = hos_now_ns();
    if (until > now) hos_sleep_ns(until - now);
}
/* (mkw-recomp) MKW's frame pacer (0x806bcbb0, x19 when it sets the interval: nvn_native.c): its measured frame time
   (+0x128, ticks), its running excess (+0x218) and interval (+0x214), logged with the frame rate */
volatile uint64_t hos_mkw_pacer;
/* HOS_SCAN_DT=<file prefix> (diagnostic): writes every float in guest memory between ~0.015 and ~0.04, with its
   address, four times 300 presents apart: the game's frame step is either one exact 1/60 or follows the frames */
static void scan_dt(long long presents) {
    static long long first; static const char *prefix; static int scans, read_env;
    if (!read_env) read_env = 1, prefix = getenv("HOS_SCAN_DT");
    if (!prefix) return;
    if (!first) {   /* (once <prefix>go exists) */
        if (presents % 60) return;
        char go[512];
        snprintf(go, sizeof go, "%sgo", prefix);
        FILE *g = fopen(go, "r");
        if (!g) return;
        fclose(g);
        first = presents;
    }
    if (presents < first + scans * 300 || scans >= 4) return;
    char name[512];
    snprintf(name, sizeof name, "%s%d.txt", prefix, scans++);
    FILE *f = fopen(name, "w");
    if (!f) return;
    size_t hits = 0;
    for (uint64_t a = 0x80000000ULL; a < 0x2000000000ULL && hits < 4000000;) {
        MEMORY_BASIC_INFORMATION m;
        if (!VirtualQuery(GP(a), &m, sizeof m)) break;
        const uint64_t size = m.RegionSize;
        if (m.State == MEM_COMMIT && (m.Protect & PAGE_READWRITE)) {
            const uint32_t *w = (const uint32_t *)m.BaseAddress;
            for (size_t i = 0; i < size / 4; i++)
                if (w[i] >= 0x3c800000 && w[i] <= 0x3d230000) {
                    fprintf(f, "%llx %08x\n", (unsigned long long)((uint8_t *)&w[i] - (uint8_t *)GP(0)), w[i]);
                    if (++hits >= 4000000) break;
                }
        }
        a = (uint64_t)((uint8_t *)m.BaseAddress - (uint8_t *)GP(0)) + size;
    }
    fclose(f);
    hos_log("vi: scan %s: %zu floats", name, hits);
}
/* (hookshot) Game time follows the clock. The engine steps every frame by ratio = 60 / <the graphics module's nominal fps,
   an int at +0x2c8> (main+0x6e6cc4, f_83b6e300 makes seconds = ratio / 60 and the rest), so at 50 fps the game ran at 83%
   speed: walking the same 8 s at 52 and 33 fps went as far as those rates. Each present writes the rate of the last 5
   display intervals there, the rounding error carried to the next frame so that no time is lost; a step is at most
   1/15 s, as TotK Recomp's frame_rate.c (slower frames slow the game down, as on the console), at least 1/60 s (the game's
   own limit holds it at 60). HOS_REALTIME_STEP=0: the shipping whole steps. */
extern volatile uint64_t hos_gfx_module;
volatile int hos_fixed_resolution = 0;   /* (the lifted code reads these: nx2/oot_patches.py) */
volatile uint32_t hos_fixed_scale_bits = 0x3f800000;   /* (HOS_RES_SCALE: the fixed path's scale) */
volatile int hos_dynres_runs = 0;   /* (the dynamic path's frames: it fills fields the 3D reads, +8 +16 +168) */
volatile float hos_text_scale = 1.0f;   /* (the letters' clock of a text pane, f_80b1e140: nx2/oot_patches.py) */
/* The Insert menu's Text speed (0 Normal, 1 2x, 2 4x, 3 Instant), HOS_TEXT_SPEED=<0..3> over it: what the time of a letter
   animation's step is multiplied by. Instant: the page at once, as the game's own skip on A */
static float text_scale(const char *env, int menu) {
    const int v = env && *env ? atoi(env) : menu;
    return v == 1 ? 2.0f : v == 2 ? 4.0f : v >= 3 ? 1e6f : 1.0f;
}
volatile uint64_t hos_res_module = 0;   /* (f_831f3200's object, whose +0xe5c is the 3D's scale: nx2/oot_patches.py) */
volatile uint32_t hos_unl_step_bits = 0;   /* (the float bits of 60.0 times the wall time between frames, 0 off: nx2/oot_patches.py patch_unlimited_step) */
volatile uint64_t hos_unl_step_probe = 0;  /* (the object that step reads, +0x2c8: compared with the module we hold) */
volatile uint64_t hos_unl_step_probe2 = 0; /* (f_80706be0's this, whose +0xd58 accumulates the step: is the world clock keeping time?) */

/* The Frame rate menu row (option 10: 0 Off, 1 is 30, 2 is 60, 3 Unlimited) or HOS_FPS_LOCK=<fps>, which wins for test runs:
   the rate to hold the game's frames at, 0 the emulator's usual pacing, -1 no wait between presents at all. Read every frame:
   the menu changes it while the game runs */
static int fps_lock_wanted(void) {
    int rx_option(int id, int otherwise);
    static int env, env_done;
    if (!env_done) {
        env_done = 1;
        const char* s = getenv("HOS_FPS_LOCK");
        const int v = s ? (!strcmp(s, "unlimited") ? -1 : atoi(s)) : 0;
        if (v == -1 || (v >= 15 && v <= 240)) env = v, env_done = 2;   /* (the environment wins for test runs) */
    }
    if (env_done == 2) return env;
    const int row = rx_option(10, 0);
    return row == 1 ? 30 : row == 2 ? 60 : row == 3 ? -1 : 0;
}

static void realtime_step(int64_t now) {
    int rx_option(int id, int otherwise);
    /* (Resolution 100% from the start: the fixed path skipped the dynamic block from the first frame, its fields stayed
       unset and the 3D came out black. The dynamic path runs a few frames first, as when the menu turns it on in game) */
    hos_fixed_resolution = !rx_option(2, 1) && hos_dynres_runs >= 30;
    {
        static const char *env;
        static int looked;
        if (!looked) looked = 1, env = getenv("HOS_TEXT_SPEED");
        hos_text_scale = text_scale(env, rx_option(5, 0));
    }
    {   /* (the Insert menu's Resolution: the fixed path's scale in 1/10000, live; HOS_RES_SCALE
           below stays over it) */
        const int scale = rx_option(3, 0);
        if (scale > 0 && !getenv("HOS_RES_SCALE")) {
            const float v = scale / 10000.0f;
            memcpy((void *)&hos_fixed_scale_bits, &v, 4);
        }
    }
    {
        static int read_scale;
        if (!read_scale) {
            read_scale = 1;
            if (getenv("HOS_RES_SCALE")) { float v = (float)atof(getenv("HOS_RES_SCALE")); memcpy((void *)&hos_fixed_scale_bits, &v, 4); }
        }
    }
    {   /* HOS_RES_LOG=1: the 3D's scale every 300 presents. +0xe20: flags (bit 3: 0.75, bit 4: 0.5 in place of the scale),
           +0xe2c bit 3: recompute asked, +0xe50/+0xe54/+0xe58: the sizes the scale is taken against, +0xe5c: the scale */
        static int on = -1;
        static long long n;
        if (on < 0) on = getenv("HOS_RES_LOG") != NULL;
        const uint64_t m = hos_res_module;
        if (on && m && !(n++ % 300)) {
            float f[4], s;
            memcpy(f, GP(m + 0xe50), sizeof f);
            memcpy(&s, (const void *)&hos_fixed_scale_bits, 4);
            hos_log("vi: res: fixed %d (scale %.3f), +0xe20 %llx, +0xe2c %x, +0xe50 %.0f %.0f %.0f, 3D scale %.3f", hos_fixed_resolution, s,
                    (unsigned long long)*(uint64_t *)GP(m + 0xe20), *(uint32_t *)GP(m + 0xe2c), f[0], f[1], f[2], f[3]);
        }
    }
    const uint64_t mod = hos_gfx_module;
    if (!mod) return;
    const int lock = fps_lock_wanted();
    {   /* (hookshot) The frame limiter (f_8042cd20 sleeps to 1 / +0xfc). The engine's adaptive controller writes +0xfc when
           variable refresh is on (+0x231, f_804fc0f0), any rate from 40 up. HOS_FPS_TARGET=<fps>: that rate kept, 0: left alone;
           HOS_FPS_LOCK=<fps> (or the Frame rate menu row): the limiter held down at that rate instead (every frame, and
           variable refresh stays off so the controller cannot raise it back): a fixed 30 for an outside frame generator to
           build on, paced for real in hos_vi_native_presented. HOS_FPS_LOG=1: what the game asks for, every 300 presents */
        static int target = -1, log_on = -1;
        static long long n;
        if (target < 0) target = getenv("HOS_FPS_TARGET") ? atoi(getenv("HOS_FPS_TARGET")) : 0, log_on = getenv("HOS_FPS_LOG") != NULL;
        int *limit = (int *)GP(mod + 0xfc);
        if (log_on && !(n++ % 300))
            hos_log("vi: frame limiter %d, vrr %d %d, nominal %d, controller +0x58.. %d %d %d %d", *limit, *(uint8_t *)GP(mod + 0x231),
                    *(uint8_t *)GP(mod + 0x232), *(int *)GP(mod + 0x2c8), *(int *)GP(mod + 0x270 + 0x58), *(int *)GP(mod + 0x270 + 0x28),
                    *(int *)GP(mod + 0x270 + 0x2c), *(int *)GP(mod + 0x270 + 0x30));
        if (target && *limit > 0 && *limit < target) *limit = target;
        if (lock >= 15 && lock <= 240) *limit = lock, *(uint8_t *)GP(mod + 0x231) = 0;   /* (after the raise: the lock wins both ways) */
    }
    if (!rx_option(0, 1)) {   /* (the Insert menu: per frame) */
        int *slot = (int *)GP(mod + 0x2c8);
        hos_unl_step_bits = 0;   /* (the engine's own math again) */
        static long long n_pf;
        if (!(n_pf++ % 300)) hos_log("vi: game speed per frame: nominal %d, lock %d", *slot, lock);
        if (*slot >= 15 && *slot <= 60) *slot = 60;
        return;
    }
    /* (the game loop's own rate: how many of these presents go by in a second of wall time. The instantaneous
       inter-frame time was measured before and lied during a free-running loop (9000 fps counted against the
       158 fps actually shown at the title), and its average sat under 1 and pinned the step at 1/60 s: at 158 fps
       that ran the world at 2.6 times speed. The loop is paced, so a count over a second is its rate) */
    static int64_t win_at;
    static long long win_calls, steps;
    static double measured = 60.0;
    win_calls++;
    if (!win_at) win_at = now;
    if (now - win_at >= 100000000LL) {   /* (counted every tenth of a second and smoothed: a whole-second window held the
                                            stale step through the second while the rate moved (121..134 in game), which felt
                                            like speed-up and slow-down waves; the count inside the window is what makes it
                                            robust, the instantaneous inter-frame time lied in bursts) */
        const double inst = win_calls * 1e10 / (double)(now - win_at);
        measured += (inst - measured) * 0.5;
        win_at = now, win_calls = 0;
    }
    /* the engine stores this as 60/fps in integer math, so anything above 60 needs the value we choose */
    const double rate = lock >= 15 && lock <= 240 ? (double)lock
                      : measured < 15 ? 15.0 : measured;   /* no upper clamp: the step divides by the rate itself, and
                                                               clamping it (at 480 while the loop ran 580) advanced the
                                                               world 1.2x fast in light scenes -- those were the surges */
    const int fps_raw = (int)(rate + 0.5);
    /* (the rate fields the game reads of its own are integer 60/fps, so above 60 the written value means nothing unless the
       host float carries the step: above 60 only when Unlimited is chosen (the row or HOS_FPS_LOCK) and the step is on.
       The step is on by default (HOS_UNL_STEP=0 puts it away); nx2/oot_patches.py patch_unlimited_step must be in the
       build for the float to be read at all, an older lift keeps the engine's integer step. Clamped otherwise: with the
       Frame rate row Off the written rate is the old 15..60, a smoothed 61 on a paced 60 loop included) */
    static int unl_on = -1;
    if (unl_on < 0) unl_on = !getenv("HOS_UNL_STEP") || atoi(getenv("HOS_UNL_STEP"));
    const int fps = fps_raw > 60 && !(unl_on && (lock == -1 || lock > 60)) ? 60 : fps_raw;
    int *slot = (int *)GP(mod + 0x2c8);
    const int was = *slot;   /* (what the game had put there this frame: whether our last frame's write survived) */
    if (*slot >= 15 && *slot <= 9999) {
        *slot = fps;
        /* (Above 60 the game fast-forwards: every one of its rate fields stores 60/fps with an INTEGER divide
           (f_8348c5c0: +0xfc = 60 / w20, f_83b6f370 the same for the vsync controller), so nothing above 60 is
           expressible and the per-frame step floors at 1/60 s. The step itself is the divide of nx2/oot_patches.py
           (patch_unlimited_step): hos_unl_step_bits carries 60.0 times the wall time between frames to it, and it
           divides by nothing. At or under 60 it stays 0 and the engine's own 60/nominal runs. The step is on unless
           HOS_UNL_STEP=0; the probe records the object the step reads, to check it is the module whose slot we hold) */
        if (unl_on && fps > 60) {
            /* (the world advances by the wall time between presents: unlike 60 over the counted loop rate, this is
               exact whatever the pacing does -- the sum of elapsed times IS the elapsed time. The counted rate broke
               on bursty loops: the menu presented in bursts and the count swung 2300..6700, which made the menu choppy
               and its animations slow. A hitch clamps at 20 ms so a load stall skips time instead of warping the world) */
            static int64_t prev_now;
            double dt = prev_now ? (now - prev_now) / 1e9 : 1.0 / 60.0;
            prev_now = now;
            if (dt > 0.02) dt = 0.02;
            else if (dt < 0.0002) dt = 0.0002;
            const float r = (float)(60.0 * dt);
            memcpy((void *)&hos_unl_step_bits, &r, 4);
        } else
            hos_unl_step_bits = 0;
    }
    else {   /* (the game's own 0 during a load: leave it be until it comes back) */
        static int logged;
        if (!logged++) hos_log("vi: the graphics module's frame rate reads %d: no real-time step", *slot);
    }
    if (!(steps++ % 300)) {
        /* (the accumulated clock of f_80706be0 (the double at [this+0xd58]) grows by one step per call: over 300 frames
           it must gain 300 / the loop rate seconds for the world clock to keep time with the wall; fps / 60 / 60 * 300
           / fps = a 1/60 s fixed step, the fast-forward) */
        static double accum_prev; static int64_t at_prev;
        double accum = 0.0;
        if (hos_unl_step_probe2) memcpy(&accum, GP(hos_unl_step_probe2 + 0xd58), 8);
        const double per_sec = at_prev ? (accum - accum_prev) * 1e9 / (now - at_prev) : 0.0;
        accum_prev = accum, at_prev = now;
        hos_log("vi: real-time step on: wrote %d (measured %.1f fps, lock %d), nominal before was %d, "
                "engine limiter %d, tick %d, step object %llx %s us, step bits %08x, clock %.2f s, clock keeps %.2fx wall, acc at %llx",
                fps, measured, lock, was, *(int *)GP(mod + 0xfc), *(int *)GP(mod + 0x270 + 0x58),
                (unsigned long long)hos_unl_step_probe, hos_unl_step_probe == mod ? "is" : "is not",
                hos_unl_step_bits, accum, per_sec, (unsigned long long)(hos_unl_step_probe2 + 0xd58));
    }
}
/* HOS_PRESENT_TRACE=<csv>: native game present-call returns, not GPU completion or display times.
   Uses the existing frame ID/clock; a forced stop can lose the last second of buffered present records. */
static void present_trace(int64_t frame, int64_t now) {
    static FILE *file;
    static char buffer[64 * 1024];
    static int looked;
    static int64_t last, flushed;
    if (!looked) {
        const char *path = getenv("HOS_PRESENT_TRACE");
        looked = 1;
        if (path && *path) {
            file = fopen(path, "w");
            if (!file) hos_log("vi: could not open present trace %s", path);
        }
        if (file) {
            setvbuf(file, buffer, _IOFBF, sizeof buffer);
            fputs("frame,return_ns,interval_ns\n", file);
            flushed = now;
        }
    }
    if (!file) return;
    fprintf(file, "%lld,%lld,%lld\n", (long long)frame, (long long)now, (long long)(frame > 1 ? now - last : 0));
    last = now;
    if (now - flushed >= 1000000000LL) {
        fflush(file);
        flushed = now;
    }
}
void hos_vi_native_presented(void) {
    static long long scan_n;
    scan_dt(++scan_n);
    {   /* HOS_FRAME_SLEEP_MS (diagnostic): slower frames, for game speed against frame rate */
        static int ms = -1;
        if (ms < 0) ms = getenv("HOS_FRAME_SLEEP_MS") ? atoi(getenv("HOS_FRAME_SLEEP_MS")) : 0;
        if (ms) hos_sleep_ns(ms * 1000000LL);
    }
    /* (the Frame rate row / HOS_FPS_LOCK: the present-to-present period, so the acquire that paces the game loop waits a
       full 1/fps: a steady 30 for an outside frame generator. The field the game's own limiter sleeps on stays 60: TOTK
       rewrote it after every write we made there, so the pacing is done here instead) */
    const int fps_lock = fps_lock_wanted();
    const int64_t period = fps_lock > 0 ? 1000000000LL / fps_lock
                       : fps_lock < 0 ? 1000000 : 1000000000LL / 60 * present_interval, now = hos_now_ns();
                       /* (Unlimited: a 1 ms floor, not nothing. The menu free-ran at 6600 presents a second: the engine's
                          per-frame work per tick piles up at that rate. One thousand is plenty of headroom above any real
                          display, and the world step is the wall time between frames, so the floor costs no speed) */
    realtime_step(now);
    /* (behind by more than a frame: the next one is paced from now, with no burst to catch up) */
    next_frame_at = next_frame_at && now - next_frame_at < period ? next_frame_at + period : now + period;
    {   /* (the display queue: this frame is shown one interval after the last, or now when the display waits) */
        const int64_t n = shown_count, last = n ? shown_at[(n - 1) & 15] : 0;
        shown_at[n & 15] = last + period > now ? last + period : now;
        shown_count = n + 1;
    }
    const int64_t n = InterlockedIncrement64(&presents);
    present_trace(n, now);
    {   /* HOS_MKW_INTERVAL=<0|1|2>: the interval MKW's pacer is held at, through its own override (+0x210, any
           non-zero value is used as the interval): 1 (the default): a steady 60 fps; 2: 30; 0: the game's choice,
           which with frames between 16.7 and 33 ms swings between the two (25-27 fps, stutters). Its timing counts
           vsyncs, so an interval the renderer cannot keep slows the game down rather than dropping frames */
        static int forced = -1;
        if (forced < 0) forced = getenv("HOS_MKW_INTERVAL") ? atoi(getenv("HOS_MKW_INTERVAL")) : 1;
        if (hos_mkw_pacer && forced >= 0 && forced <= 2) G32(hos_mkw_pacer + 0x210) = (uint32_t)forced;
    }
    if (!window_start) window_start = now;
    frames_in_window++;
    /* (present-to-present intervals of the window, in buckets: which frames miss their vsync) */
    static int64_t last_present;
    static int hist[6];
    if (last_present) {
        const int64_t d = now - last_present;
        hist[d < 15500000 ? 0 : d < 17800000 ? 1 : d < 22000000 ? 2 : d < 30000000 ? 3 : d < 40000000 ? 4 : 5]++;
    }
    last_present = now;
    if (now - window_start >= 5000000000LL) {
        hos_log("vi: %lld frames presented (%.1f fps over the last %.1f s, interval %d)", (long long)n,
                frames_in_window * 1e9 / (double)(now - window_start), (now - window_start) / 1e9, present_interval);
        hos_log("vi: present intervals: <15.5 ms %d, 15.5-17.8 %d, 17.8-22 %d, 22-30 %d, 30-40 %d, over 40 %d", hist[0],
                hist[1], hist[2], hist[3], hist[4], hist[5]);
        memset(hist, 0, sizeof hist);
        window_start = now, frames_in_window = 0;
        const uint64_t pacer = hos_mkw_pacer, freq = pacer ? G64(0x837de940) : 0;   /* (MKW only: nvn_native.c) */
        if (pacer && freq)
            hos_log("vi: the game's pacer: frame work %.1f ms, excess %.1f ms, interval %d",
                    (double)(int64_t)G64(pacer + 0x128) * 1e3 / (double)freq,
                    (double)(int64_t)G64(pacer + 0x218) * 1e3 / (double)freq, (int)G32(pacer + 0x214)),
            hos_log("vi: pacer raw: now %llx f0 %llx 128 %llx e8 %llx 50 %x 210 %x 22c %x freq %llu", (unsigned long long)hos_ticks(),
                    (unsigned long long)G64(pacer + 0xf0), (unsigned long long)G64(pacer + 0x128),
                    (unsigned long long)G64(pacer + 0xe8), G32(pacer + 0x50), G32(pacer + 0x210), *(const uint8_t *)GP(pacer + 0x22c),
                    (unsigned long long)freq);
    }
}
