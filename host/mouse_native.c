/* (hookshot) Native mouse look (HOS_MOUSE_NATIVE=1): the captured mouse turns the camera itself, its yaw and pitch in
   degrees, instead of standing in for the right stick (no decay, no response curve, no top speed: nn_hid.c's
   mouse_stick).

   Where the angles are (the camera finder, camfind.c, in Kokiri Forest, 2026-11-07): the player camera's controller
   holds its pitch limits as {-85, 89, -85, 89} (four floats, degrees), its distance after them, its pitch at +0x14
   from them and its yaw at +0x2c; the stick turns those, about 90 degrees a second, yaw falling as the stick goes
   right and pitch falling as it goes up. Its output (pitch and yaw side by side, after a ring of recent camera
   positions) follows. The limits are looked for in the heap when the mouse first moves and whenever they are no
   longer where they were (a new scene), at most every 3 s.

   Which of those the game reads is not known yet: F7 cycles what the mouse writes, logged as "mouse native: target":
   1 the controller, 2 the output copies, 3 both. The sensitivity is the mouse's (- and =): 0.1 degrees a count at 1.
   HOS_MOUSE_INVERT_Y=1 flips up and down. */
#include "hos.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int hos_mouse_take(int32_t *dx, int32_t *dy);                                   /* display.c */
size_t hos_heap_find(const void *pat, size_t len, uint64_t *out, size_t max);   /* camfind.c */
extern double hos_mouse_sens;

enum { MAX_T = 16, PITCH_AT = 0x14, YAW_AT = 0x2c };
static const float limits_sig[4] = {-85.0f, 89.0f, -85.0f, 89.0f};
static uint64_t ctl[MAX_T], outs[MAX_T];   /* the controllers (where their limits are); the output copies (their pitch) */
static size_t nctl, nout;
static int target;                          /* 0 the controller, 1 the output copies, 2 both */
static int64_t next_scan;
static volatile LONG busy, pend_x, pend_y;   /* (motion taken by a thread while another applied: applied next) */
static long long applied, moves;

static float getf(uint64_t a) { float f; memcpy(&f, GP(a), 4); return f; }
static void putf(uint64_t a, float f) { memcpy(GP(a), &f, 4); }

static int still_there(void) { return nctl && !memcmp(GP(ctl[0]), limits_sig, sizeof limits_sig); }

static void find(void) {
    const int64_t t0 = hos_now_ns();
    uint64_t found[MAX_T];
    size_t n = hos_heap_find(limits_sig, sizeof limits_sig, found, MAX_T);
    nctl = 0;
    for (size_t i = 0; i < n; i++) {
        const float p = getf(found[i] + PITCH_AT), y = getf(found[i] + YAW_AT);
        if (isfinite(p) && isfinite(y) && fabsf(p) <= 90.0f && fabsf(y) <= 360.0f) ctl[nctl++] = found[i];
        hos_log("mouse native: limits at %#llx: pitch %.3f, yaw %.3f%s", (unsigned long long)found[i], p, y,
                nctl && ctl[nctl - 1] == found[i] ? "" : " (not angles: left out)");
    }
    nout = 0;
    if (nctl) {   /* the output copies: the controller's pitch and yaw side by side */
        const float pair[2] = {getf(ctl[0] + PITCH_AT), getf(ctl[0] + YAW_AT)};
        const size_t n2 = hos_heap_find(pair, sizeof pair, outs, MAX_T);
        for (size_t i = 0; i < n2; i++) {   /* (not inside a controller: its yaw after a 0 reads as the pair at pitch 0) */
            int inside = 0;
            for (size_t c = 0; c < nctl; c++) inside |= outs[i] >= ctl[c] && outs[i] < ctl[c] + 0x40;
            if (!inside) outs[nout++] = outs[i];
        }
    }
    hos_log("mouse native: %zu controller(s), %zu output copies, found in %.0f ms; target %d (%s)", nctl, nout,
            (hos_now_ns() - t0) / 1e6, target + 1, target == 0 ? "the controller" : target == 1 ? "the output copies" : "both");
}

static void turn(uint64_t pitch_at, uint64_t yaw_at, float dpitch, float dyaw, float lo, float hi) {
    float y = getf(yaw_at) + dyaw, p = getf(pitch_at) + dpitch;
    if (y > 180.0f) y -= 360.0f;
    if (y < -180.0f) y += 360.0f;
    if (p < lo) p = lo;
    if (p > hi) p = hi;
    putf(yaw_at, y), putf(pitch_at, p);
}

/* With each read of the controller (nn_hid.c, the game's threads): the mouse's motion since the last into the camera.
   1 while the mouse is captured (its buttons count then) */
int hos_mouse_native(void) {
    static int invert = -1, f7_was;
    if (invert < 0) {
        invert = getenv("HOS_MOUSE_INVERT_Y") != NULL;
        hos_log("mouse native: on: the mouse turns the camera itself (F7: what it writes)");
    }
    const int f7 = (GetAsyncKeyState(VK_F7) & 0x8000) != 0;
    if (f7 && !f7_was) {
        target = (target + 1) % 3;
        hos_log("mouse native: target %d (%s)", target + 1,
                target == 0 ? "the controller" : target == 1 ? "the output copies" : "both");
    }
    f7_was = f7;
    int32_t mx, my;
    const int captured = hos_mouse_take(&mx, &my);
    if (!captured) return 0;
    if (mx || my) InterlockedExchangeAdd(&pend_x, mx), InterlockedExchangeAdd(&pend_y, my);
    if (InterlockedExchange(&busy, 1)) return captured;   /* (another thread is at it: it takes this motion too) */
    const LONG dx = InterlockedExchange(&pend_x, 0), dy = InterlockedExchange(&pend_y, 0);
    if (!dx && !dy) { busy = 0; return captured; }
    const int64_t now = hos_now_ns();
    if (!still_there() && now >= next_scan) {
        next_scan = now + 3000000000LL;
        find();
    }
    if (nctl) {
        const double k = 0.1 * hos_mouse_sens;
        const float dyaw = (float)(-dx * k), dpitch = (float)((invert ? dy : -dy) * k);
        const float lo = getf(ctl[0]), hi = getf(ctl[0] + 4);
        if (target != 1)
            for (size_t i = 0; i < nctl; i++) turn(ctl[i] + PITCH_AT, ctl[i] + YAW_AT, dpitch, dyaw, lo, hi);
        if (target != 0)
            for (size_t i = 0; i < nout; i++) turn(outs[i], outs[i] + 4, dpitch, dyaw, lo, hi);
        applied++;
    }
    moves++;
    if ((moves & 1023) == 1)
        hos_log("mouse native: %lld moves, %lld applied; controller pitch %.2f yaw %.2f", moves, applied,
                nctl ? getf(ctl[0] + PITCH_AT) : 0.0f, nctl ? getf(ctl[0] + YAW_AT) : 0.0f);
    busy = 0;
    return captured;
}
