/* (hookshot) The game pack's C interface (docs/game-packs.md 2.4, 4): what the shared runtime asks of the running game's
   pack, and what a pack's C (packs/<key>/host/, compiled into that game's runtime only: nx2/build.py) may call.

   A pack's C includes this header and the headers it names here, nothing else of the engine's:
     - hos.h: guest memory (recomp_load* / recomp_store*, hos_guest_range), hos_log, the clock, the Win32 calls host/posix
       has (SRWLOCK and the like), the guest's Ctx;
     - ../gpu/vk/vk_api.h: the option ids (HOS_OPT_*), rx_option (below);
     - ../gpu/vk/compat/hookshot_env.h: env_on, the switches' one rule;
     - control.h: the control bridge's game table (HosControlGame), whose game members a pack fills (control);
   and the functions and globals the lifted patches of the game call (hos_text_scale, hos_camera_scale,
   hos_fixed_resolution, hos_mouse_camera, the prompt font server, hos_zstd_stream, the hos_wide_* picture), which are the
   runtime's own and stay so.

   Each game's runtime links one NX2PackHost: its pack's, when pack.json has a "host" list, else host/game_none.c's, which
   answers nothing. A member left NULL is a hook the game does not take: the runtime does what it does without a game. */
#pragma once

#include <stdint.h>

#if defined(_WIN32)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#endif
#include <windows.h>   /* (Windows', or on Linux host/posix's: the SRWLOCK and window calls a pack may make) */
#include "hos.h"
#include "control.h"
#include "../gpu/vk/vk_api.h"
#include "../gpu/vk/compat/hookshot_env.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The interface's version: nx2/packs.py NX2PC_PACK_API, the same two numbers (tests/test_game_packs.py holds them so). A pack
   built for another major is not this engine's; during 0.x any minor may break (0.2: lift/, tools/, assets/ and build_steps,
   docs/game-packs.md a2; 0.3: this header and a pack's host/ C, a3; 0.4: the menu table, NX2GameMenu (vk_api.h), and its
   option ids from HOS_OPT_GAME_BASE, a4) */
#define NX2_PACK_API_MAJOR 0
#define NX2_PACK_API_MINOR 4

/* The runtime's functions and globals a pack's C may use besides hos.h's: */
int rx_option(int id, int otherwise);        /* (host/rx_backend.c) an Insert menu row's value, or otherwise */
void rx_option_set(int id, int value);       /* (host/rx_backend.c) a value the game side reports to the menu */
int hos_move_stick_held(void);               /* (host/nn_hid.c) the player holds the move stick */
uint64_t hos_flight_input(void);             /* (host/nn_hid.c) the debug flight's buttons this frame */
uint64_t hos_free_camera_input(int *height); /* (host/nn_hid.c) the free camera's axes, and its height */
extern volatile uint32_t hos_frame_seconds_bits;   /* (host/nn_vi.c) the frame's seconds as a float's bits, 0 at a cap of 60 */

typedef struct NX2PackHost {
    uint32_t version;   /* NX2_PACK_API_MAJOR << 16 | NX2_PACK_API_MINOR, as the pack was built */
    uint32_t size;      /* sizeof (NX2PackHost) as the pack was built */
    const char *key;    /* the pack's key (packs/<key>), for the log; "none" for game_none.c */
    /* host/hos_main.c, before the game starts (its switches read, the log open) */
    void (*start)(void);
    /* host/control.c, before the bridge serves: the game's members of the table (map, destination, teleport, age, ...) */
    void (*control)(HosControlGame *game);
    /* host/nn_hid.c, a test run's live input file: "!cheat <action>", the action as the Insert menu's Cheats page sends it */
    void (*cheat_action)(int action);
    /* host/nn_hid.c: the player's character is the player's to move (the Insert menu's Auto run), 0 when the game cannot
       tell; the debug flight's shortcut may toggle it now */
    int (*player_free)(void);
    int (*flight_shortcut_ready)(void);
    /* host/rx_backend.c (MkwVkHost.wide_cover): how closed the game's full-picture loading cover is, 0..1; 0: none */
    float (*wide_cover)(void);
    /* host/nvn_native.c, nvnWindowSetPresentInterval: the game setting its present interval (c: the call's registers) */
    void (*present_interval_set)(Ctx *c);
    /* host/nn_vi.c: at each present; and with the frame rate's log line, every 5 s */
    void (*present)(void);
    void (*present_log)(void);
    /* host/hos_thread.c, a thread dump: 1 when c is in the game's own idle loop, whose registers are not worth printing */
    int (*thread_idle)(const Ctx *c);
    /* (0.4) the game's menu content for the shared renderer (gpu/vk/vk_api.h NX2GameMenu: its pack's menu/ C), handed over as
       MkwVkHost.game_menu (host/rx_backend.c), asked once; NULL, or none: the menu's game rows hidden, no tools */
    const NX2GameMenu *(*menu)(void);
    /* (0.4) host/nn_hid.c, the controller as the game reads it: the option ids of the game's debug flight and free camera, whose
       inputs it routes (hos_flight_input, hos_free_camera_input, the flight's double-tap shortcut); 0: the game has none */
    int flight_option, free_camera_option;
} NX2PackHost;

/* The running game's pack: never NULL (host/game_none.c's for a game whose pack has no C) */
const NX2PackHost *nx2_pack_host(void);

/* The shared runtime's calls, each what it was without a game when the pack takes no such hook */
static inline void hos_game_start(void) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->start) p->start();
}
static inline void hos_game_control(HosControlGame *game) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->control) p->control(game);
}
static inline void hos_game_cheat_action(int action) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->cheat_action) p->cheat_action(action);
}
static inline int hos_game_player_free(void) {
    const NX2PackHost *p = nx2_pack_host();
    return p->player_free ? p->player_free() : 0;
}
static inline int hos_game_flight_shortcut_ready(void) {
    const NX2PackHost *p = nx2_pack_host();
    return p->flight_shortcut_ready ? p->flight_shortcut_ready() : 0;
}
static inline float hos_game_wide_cover(void) {
    const NX2PackHost *p = nx2_pack_host();
    return p->wide_cover ? p->wide_cover() : 0.0f;
}
static inline void hos_game_present_interval_set(Ctx *c) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->present_interval_set) p->present_interval_set(c);
}
static inline void hos_game_present(void) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->present) p->present();
}
static inline void hos_game_present_log(void) {
    const NX2PackHost *p = nx2_pack_host();
    if (p->present_log) p->present_log();
}
static inline int hos_game_thread_idle(const Ctx *c) {
    const NX2PackHost *p = nx2_pack_host();
    return p->thread_idle ? p->thread_idle(c) : 0;
}
static inline const NX2GameMenu *hos_game_menu(void) {
    const NX2PackHost *p = nx2_pack_host();
    return p->menu ? p->menu() : NULL;
}
static inline int hos_game_flight_option(void) { return nx2_pack_host()->flight_option; }
static inline int hos_game_free_camera_option(void) { return nx2_pack_host()->free_camera_option; }

#ifdef __cplusplus
}
#endif
