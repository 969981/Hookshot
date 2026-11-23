/* (mkw-recomp) Mario Kart World 1.8.0's frame pacer and idle loop, at that build's fixed guest addresses. Kept for a later
   1.8.0 profile (docs/game-packs.md a3, docs/one-engine.md rule 3): the mkw pack's profile is 1.0.0 (main 9e244438), so none of
   this runs today, and every hook checks main's build id first (mkw_180), as host/hos_main.c's hos_game_is_mkw did. Compiled
   into Mario Kart World's runtime only (packs/mkw/pack.json "host"; nx2/build.py). Moved from host/nn_vi.c, host/nvn_native.c
   and host/hos_thread.c, the code as it was there. */
#include "nx2_pack.h"
#include <stdlib.h>
#include <string.h>

/* main's build id is 1.8.0's (d00e4f39...): any other build has other code at these addresses */
static int mkw_180(void) { return !strcmp(hos_main_build_id, "d00e4f399e411d0613aa8d26b1c02fef"); }

/* (mkw-recomp) MKW's frame pacer (0x806bcbb0, x19 when it sets the interval: nvnWindowSetPresentInterval): its measured frame
   time (+0x128, ticks), its running excess (+0x218) and interval (+0x214), logged with the frame rate */
static volatile uint64_t hos_mkw_pacer;

/* nvnWindowSetPresentInterval: its frame pacer, which calls it from 0x806bce24 */
static void mkw_present_interval_set(Ctx *c) {
    if (mkw_180() && c->x[30] == 0x806bce28) hos_mkw_pacer = c->x[19];
}

/* HOS_MKW_INTERVAL=<0|1|2>: the interval MKW's pacer is held at, through its own override (+0x210, any non-zero value is used
   as the interval): 1 (the default): a steady 60 fps; 2: 30; 0: the game's choice, which with frames between 16.7 and 33 ms
   swings between the two (25-27 fps, stutters). Its timing counts vsyncs, so an interval the renderer cannot keep slows the
   game down rather than dropping frames */
static void mkw_present(void) {
    static int forced = -1;
    if (forced < 0) forced = getenv("HOS_MKW_INTERVAL") ? atoi(getenv("HOS_MKW_INTERVAL")) : 1;
    if (hos_mkw_pacer && forced >= 0 && forced <= 2) G32(hos_mkw_pacer + 0x210) = (uint32_t)forced;
}

static void mkw_present_log(void) {
    const uint64_t pacer = hos_mkw_pacer, freq = pacer ? G64(0x837de940) : 0;
    if (pacer && freq)
        hos_log("vi: the game's pacer: frame work %.1f ms, excess %.1f ms, interval %d",
                (double)(int64_t)G64(pacer + 0x128) * 1e3 / (double)freq,
                (double)(int64_t)G64(pacer + 0x218) * 1e3 / (double)freq, (int)G32(pacer + 0x214)),
        hos_log("vi: pacer raw: now %llx f0 %llx 128 %llx e8 %llx 50 %x 210 %x 22c %x freq %llu", (unsigned long long)hos_ticks(),
                (unsigned long long)G64(pacer + 0xf0), (unsigned long long)G64(pacer + 0x128),
                (unsigned long long)G64(pacer + 0xe8), G32(pacer + 0x50), G32(pacer + 0x210), *(const uint8_t *)GP(pacer + 0x22c),
                (unsigned long long)freq);
}

/* a thread dump: MKW's idle thread loop */
static int mkw_thread_idle(const Ctx *c) { return mkw_180() && c->x[30] == 0x825c6844ULL; }

static const NX2PackHost mkw = {
    .version = NX2_PACK_API_MAJOR << 16 | NX2_PACK_API_MINOR,
    .size = sizeof(NX2PackHost),
    .key = "mkw",
    .present_interval_set = mkw_present_interval_set,
    .present = mkw_present,
    .present_log = mkw_present_log,
    .thread_idle = mkw_thread_idle,
};

const NX2PackHost *nx2_pack_host(void) { return &mkw; }
