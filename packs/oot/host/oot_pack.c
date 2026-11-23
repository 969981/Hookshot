/* (hookshot) Ocarina of Time's pack, as the runtime sees it (host/nx2_pack.h): the hooks its code takes. Compiled into Ocarina's
   runtime only (packs/oot/pack.json "host"; nx2/build.py), in place of host/game_none.c. */
#include "nx2_pack.h"
#include "oot_host.h"
#include <stdlib.h>

/* (nx2/oot_occlusion.py) read by the game's code where it decides to make occlusion queries: 1, none (HOOKSHOT_OCCLUSION_OFF=1) */
volatile int hos_occlusion_off;
static void occlusion_switch(void) {
    const char *e = getenv("HOOKSHOT_OCCLUSION_OFF");
    hos_occlusion_off = e && e[0] == '1';
    if (hos_occlusion_off) hos_log("occlusion off: the game makes no occlusion queries (HOOKSHOT_OCCLUSION_OFF=1)");
}

static void oot_start(void) {
    occlusion_switch();
    oot_cheats_start();
}

/* (test runs) the live input file's "!cheat <action>" (host/nn_hid.c): taken at Link's next update, as the Cheats page's */
extern volatile int hos_oot_live_action;
static void oot_cheat_action(int action) { __atomic_store_n(&hos_oot_live_action, action, __ATOMIC_RELAXED); }

/* The control bridge's Ocarina verbs (docs/control.md): map, teleport and age, and the state beside the map */
static void oot_control(HosControlGame *game) {
    game->map = hos_oot_current_map;
    game->destination = hos_oot_destination;
    game->teleport = hos_oot_teleport_ask;
    game->teleport_result = hos_oot_teleport_result;
    game->teleport_cancel = hos_oot_teleport_cancel;
    game->age = hos_oot_age_ask;
    game->oot_state = hos_oot_state;
}

static const NX2PackHost oot = {
    .version = NX2_PACK_API_MAJOR << 16 | NX2_PACK_API_MINOR,
    .size = sizeof(NX2PackHost),
    .key = "oot",
    .start = oot_start,
    .control = oot_control,
    .cheat_action = oot_cheat_action,
    .player_free = hos_oot_player_free,
    .flight_shortcut_ready = hos_oot_flight_shortcut_ready,
    .wide_cover = hos_wide_cover,
};

const NX2PackHost *nx2_pack_host(void) { return &oot; }
