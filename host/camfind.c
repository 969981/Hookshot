/* (hookshot) The camera finder: HOS_CAMFIND=1, then F6 in the game. Native mouse look needs the camera's own angles (its
   yaw and pitch, "lng" and "lat" in the engine's camera parameters: PlayerCameraBasicParam's StickSensitivityLat2LngRatio),
   and nothing names where they are. This finds them by watching the game turn its camera.

   F6 (the game's window in front) starts a run of about 25 s; the player's controls are off until it ends. For each axis,
   yaw (the right stick's x) and then pitch (its y):
     1. idle 1.5 s, then a snapshot of the heap's used pages (resident in the process and not all zero)
     2. idle 0.4 s more: the words that changed with nothing pressed (animation, particles, timers) are noise
     3. the stick one way for a moment; the words that changed, noise left out, are the candidates
     4. idle 2 s, then 0.5 s: candidates that still moved are dropped (the camera is at rest)
     5. the stick the other way as long: kept if they moved back, by a similar amount
     6. idle again: dropped if still moving
     7. the stick the first way again: kept if they moved that way, by a similar amount
   The candidates left are logged ("camfind:" lines in hookshot_log.txt), best match first, each with its values at every
   step and the floats around it (the object it is in). An angle that wraps (radians past pi, degrees past 180) counts
   as moving the short way round. */
#include "hos.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

extern uint64_t hos_heap_size;
enum { PAGE = 4096, WORDS = PAGE / 4, NV = 9, MAX_CANDS = 4 << 20, REPORT = 48 };

typedef struct { uint64_t addr; float v[NV]; } Cand;

typedef enum { A_NONE, A_SNAP, A_NOISE, A_DIFF, A_REC, A_STABLE, A_OPP, A_SAME, A_REPORT, A_NEXT } Action;
typedef struct { int dir; double seconds; Action then; int slot; const char *what; } Step;

/* one axis: the stick direction (+1/-1 times the axis), how long, and what is done when the step ends */
static const Step steps[] = {
    {0, 1.5, A_SNAP, 0, "snapshot"},
    {0, 0.4, A_NOISE, 0, "noise"},
    {+1, 0.0, A_DIFF, 1, "first turn"},      /* (the turn's length: per axis, below) */
    {0, 2.0, A_REC, 2, "settle"},
    {0, 0.5, A_STABLE, 3, "at rest"},
    {-1, 0.0, A_OPP, 4, "turn back"},
    {0, 2.0, A_REC, 5, "settle"},
    {0, 0.5, A_STABLE, 6, "at rest"},
    {+1, 0.0, A_SAME, 7, "turn again"},
    {0, 1.5, A_REC, 8, "settle"},
    {0, 0.0, A_REPORT, 0, "report"},
    {0, 0.0, A_NEXT, 0, "next axis"},
};
static const char *axis_name[2] = {"yaw (right stick x)", "pitch (right stick y)"};
static const double turn_seconds[2] = {0.6, 0.35};

static int enabled = -1, running, axis, step, key_was;
static int64_t step_start;
static uint8_t *snap;            /* the snapshot's pages, packed */
static uint64_t *snap_addr;      /* each page's guest address */
static uint8_t *noise;           /* a bit a word: changed while idle */
static size_t snap_pages;
static Cand *cands;
static size_t ncands;

static double secs(int64_t ns) { return ns / 1e9; }

static void drop_snapshot(void) {
    free(snap), free(snap_addr), free(noise);
    snap = NULL, snap_addr = NULL, noise = NULL, snap_pages = 0;
}

static int page_zero(const uint8_t *p) {
    const uint64_t *q = (const uint64_t *)p;
    for (int i = 0; i < PAGE / 8; i++)
        if (q[i]) return 0;
    return 1;
}

/* Which heap pages the process has touched: Windows says which are in its working set; elsewhere every page is read
   (an untouched page of an anonymous mapping reads as the shared zero page there, and zero pages are left out) */
#ifdef _WIN32
typedef struct { void *va; ULONG_PTR attr; } WsInfo;
typedef BOOL(WINAPI *QwsFn)(HANDLE, void *, DWORD);
#endif

static void take_snapshot(void) {
    const int64_t t0 = hos_now_ns();
    drop_snapshot();
    const uint64_t pages = hos_heap_size / PAGE;
    uint8_t *use = (uint8_t *)calloc((size_t)pages, 1);
    if (!use) { hos_log("camfind: no memory for the page map"); return; }
#ifdef _WIN32
    QwsFn qws = (QwsFn)GetProcAddress(GetModuleHandleA("kernel32.dll"), "K32QueryWorkingSetEx");
    if (qws) {
        enum { BATCH = 65536 };
        WsInfo *info = (WsInfo *)malloc(sizeof(WsInfo) * BATCH);
        for (uint64_t i = 0; info && i < pages; i += BATCH) {
            const uint64_t n = pages - i < BATCH ? pages - i : BATCH;
            for (uint64_t k = 0; k < n; k++) info[k].va = GP(HOS_HEAP_BASE + (i + k) * PAGE), info[k].attr = 0;
            if (qws(GetCurrentProcess(), info, (DWORD)(n * sizeof(WsInfo))))
                for (uint64_t k = 0; k < n; k++) use[i + k] = (uint8_t)(info[k].attr & 1);
        }
        free(info);
    } else
#endif
        memset(use, 1, (size_t)pages);
    size_t n = 0;
    for (uint64_t i = 0; i < pages; i++)
        if (use[i] && !page_zero((const uint8_t *)GP(HOS_HEAP_BASE + i * PAGE))) use[i] = 2, n++;
    snap = (uint8_t *)malloc(n * PAGE);
    snap_addr = (uint64_t *)malloc(n * sizeof(uint64_t));
    noise = (uint8_t *)calloc(n * WORDS / 8 + 1, 1);
    if (!snap || !snap_addr || !noise) {
        hos_log("camfind: no memory for a %zu MB snapshot", (n * PAGE) >> 20);
        drop_snapshot();
        free(use);
        return;
    }
    size_t j = 0;
    for (uint64_t i = 0; i < pages; i++)
        if (use[i] == 2) {
            snap_addr[j] = HOS_HEAP_BASE + i * PAGE;
            memcpy(snap + j * PAGE, GP(snap_addr[j]), PAGE);
            j++;
        }
    snap_pages = n;
    free(use);
    hos_log("camfind: snapshot of %zu MB (%zu pages of the %llu MB heap) in %.0f ms", (n * PAGE) >> 20, n,
            (unsigned long long)(hos_heap_size >> 20), (hos_now_ns() - t0) / 1e6);
}

static void mark_noise(void) {
    size_t marked = 0;
    for (size_t p = 0; p < snap_pages; p++) {
        const uint32_t *old = (const uint32_t *)(snap + p * PAGE), *now = (const uint32_t *)GP(snap_addr[p]);
        for (int w = 0; w < WORDS; w++)
            if (old[w] != now[w]) {
                const size_t bit = p * WORDS + w;
                noise[bit >> 3] |= (uint8_t)(1 << (bit & 7));
                marked++;
            }
    }
    hos_log("camfind: %zu words changed with nothing pressed (left out)", marked);
}

static int usable(float f) { return isfinite(f) && fabsf(f) < 1e6f; }

static void diff(void) {
    free(cands);
    cands = (Cand *)malloc(sizeof(Cand) * MAX_CANDS);
    ncands = 0;
    if (!cands || !snap) { hos_log("camfind: no snapshot to compare with"); return; }
    size_t over = 0;
    for (size_t p = 0; p < snap_pages; p++) {
        const uint32_t *old = (const uint32_t *)(snap + p * PAGE), *now = (const uint32_t *)GP(snap_addr[p]);
        for (int w = 0; w < WORDS; w++) {
            if (old[w] == now[w]) continue;
            const size_t bit = p * WORDS + w;
            if (noise[bit >> 3] & (1 << (bit & 7))) continue;
            float a, b;
            memcpy(&a, &old[w], 4), memcpy(&b, &now[w], 4);
            if (!usable(a) || !usable(b) || fabsf(b - a) < 1e-5f) continue;
            if (ncands == MAX_CANDS) { over++; continue; }
            Cand *c = &cands[ncands++];
            memset(c->v, 0, sizeof c->v);
            c->addr = snap_addr[p] + (uint64_t)w * 4;
            c->v[0] = a, c->v[1] = b;
        }
    }
    drop_snapshot();
    hos_log("camfind: %zu candidates moved with the turn%s", ncands, over ? " (more than kept: the rest dropped)" : "");
}

static float now_of(const Cand *c) { float f; memcpy(&f, GP(c->addr), 4); return f; }

/* d, or d the short way round for an angle that wrapped (radians or degrees) */
static int matches(float d, float d0, int opposite) {
    const float want = opposite ? -d0 : d0;
    const float tries[3] = {d, d - copysignf(6.2831853f, d), d - copysignf(360.0f, d)};
    for (int i = 0; i < 3; i++) {
        const float r = tries[i] / want;
        if (r >= 0.3f && r <= 3.3f) return 1;
    }
    return 0;
}

static void keep(Action a, int slot) {
    size_t kept = 0;
    for (size_t i = 0; i < ncands; i++) {
        Cand *c = &cands[i];
        const float f = now_of(c);
        int ok = usable(f);
        if (ok && a == A_STABLE) ok = fabsf(f - c->v[slot - 1]) <= 1e-6f * fmaxf(1.0f, fabsf(f));
        if (ok && a == A_OPP) ok = matches(f - c->v[slot - 1], c->v[1] - c->v[0], 1);
        if (ok && a == A_SAME) ok = matches(f - c->v[slot - 1], c->v[1] - c->v[0], 0);
        if (!ok) continue;
        c->v[slot] = f;
        cands[kept++] = *c;
    }
    ncands = kept;
}

static float score(const Cand *c) {   /* (how alike the three turns were: 0 = the same) */
    const float d0 = c->v[1] - c->v[0], d1 = -(c->v[4] - c->v[3]), d2 = c->v[7] - c->v[6];
    return fabsf(logf(fabsf(d1 / d0))) + fabsf(logf(fabsf(d2 / d0)));
}

static int by_score(const void *a, const void *b) {
    const float x = score((const Cand *)a), y = score((const Cand *)b);
    return x < y ? -1 : x > y;
}

static void report(void) {
    qsort(cands, ncands, sizeof(Cand), by_score);
    hos_log("camfind: %s: %zu candidates", axis_name[axis], ncands);
    for (size_t i = 0; i < ncands && i < REPORT; i++) {
        const Cand *c = &cands[i];
        char around[400];
        int k = 0;
        for (int o = -8; o <= 8; o++) {
            float f;
            memcpy(&f, GP(c->addr + o * 4), 4);
            k += snprintf(around + k, sizeof around - k, o ? " %.4g" : " [%.4g]", f);
        }
        hos_log("camfind:  %#llx: %.5f -> %.5f, rest %.5f, back %.5f, rest %.5f, again %.5f, now %.5f (score %.2f); around:%s",
                (unsigned long long)c->addr, c->v[0], c->v[1], c->v[3], c->v[4], c->v[6], c->v[7], c->v[8], score(c), around);
    }
}

/* Called with each read of the controller (the game's thread): 1 while a run has the controls */
int hos_camfind(uint64_t *buttons, int32_t stick[4]) {
    if (enabled < 0) {
        enabled = getenv("HOS_CAMFIND") && atoi(getenv("HOS_CAMFIND")) != 0;
        if (enabled) hos_log("camfind: on: stand still somewhere open, then press F6 (about 25 s, hands off the controls)");
    }
    if (!enabled) return 0;
    const int key = (GetAsyncKeyState(VK_F6) & 0x8000) != 0;
    if (key && !key_was && !running) {
        running = 1, axis = 0, step = 0, step_start = hos_now_ns();
        hos_log("camfind: started");
    }
    key_was = key;
    if (!running) return 0;
    *buttons = 0;
    memset(stick, 0, 4 * sizeof *stick);
    const Step *s = &steps[step];
    const double length = s->dir ? turn_seconds[axis] : s->seconds;
    if (s->dir) stick[2 + axis] = s->dir * 30000;
    if (secs(hos_now_ns() - step_start) < length) return 1;
    switch (s->then) {
    case A_SNAP: take_snapshot(); break;
    case A_NOISE: mark_noise(); break;
    case A_DIFF: diff(); break;
    case A_REC:
        for (size_t i = 0; i < ncands; i++) cands[i].v[s->slot] = now_of(&cands[i]);
        break;
    case A_STABLE: case A_OPP: case A_SAME:
        keep(s->then, s->slot);
        hos_log("camfind: %s: %zu left", s->what, ncands);
        break;
    case A_REPORT: report(); break;
    case A_NEXT:
        if (++axis == 2) {
            running = 0;
            free(cands), cands = NULL, ncands = 0;
            hos_log("camfind: done: the controls are back");
            return 0;
        }
        step = -1;
        break;
    default: break;
    }
    step++;
    step_start = hos_now_ns();   /* (after the work: a snapshot takes the game's thread a moment) */
    return 1;
}
