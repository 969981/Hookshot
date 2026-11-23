/* (hookshot) What Ocarina of Time's runtime files (packs/oot/host/) share among themselves: the debug tools (oot_cheats.c) take
   the teleport, the age switch and the game's data from oot_teleport.c. None of it is the engine's (host/nx2_pack.h is). */
#pragma once

#include "nx2_pack.h"
#include "../menu/oot_menu.h"   /* (the menu's option ids and the tools' actions: OOT_AGE_ACTION, OOT_TELEPORT_ACTION, ...) */

#define OOT_BRIDGE_AGE 0x10000     /* (oot_bridge_tp.asked: the age switch to the child, | 1 to the adult; not a destination) */

/* oot_teleport.c: the game's data and events, the teleport and the age switch, the map, the bridge's asks */
int oot_gamedata_bool(Ctx *c, uint32_t hash, int *value);
int oot_gamedata_set_bool(Ctx *c, uint32_t hash, int value);
int oot_gamedata_string(Ctx *c, uint32_t hash, char *buf, size_t size);
int oot_event_playing(Ctx *c);
int oot_teleport(Ctx *c, uint64_t actor, unsigned index);
int oot_toggle_age(Ctx *c, uint64_t actor);
void oot_teleport_followup(Ctx *c);
void oot_map_refresh(Ctx *c, int64_t now);
int oot_bridge_teleport_take(int *ticket);
void oot_bridge_teleport_done(int ticket, int sent);
int hos_oot_state(int *event, int *loading, int *child);
int hos_oot_current_map(char *out, size_t cap);
int hos_oot_destination(int index, const char **child, const char **adult);
int hos_oot_teleport_ask(int index);
int hos_oot_age_ask(int adult);
int hos_oot_teleport_result(int ticket, char *why, size_t why_cap, char *map, size_t map_cap, int *accepted);
void hos_oot_teleport_cancel(int ticket);

/* oot_cheats.c: the hearts' protection, the player's state for Auto run and the flight's shortcut, the pack's start */
extern int hos_oot_protect_hearts;
int hos_oot_hearts_enabled(void);
int hos_oot_is_player(Ctx *c, uint64_t actor);
int hos_oot_player_free(void);
int hos_oot_flight_shortcut_ready(void);
void oot_cheats_start(void);

/* oot_opening.c: Skip opening (mode 3: Link is updating with no event playing) */
int hos_oot_opening(Ctx *c, int mode);

/* oot_wide_cover.c: the ultrawide loading cover */
float hos_wide_cover(void);

/* ../menu/menu.c: the menu's content for the shared renderer (NX2PackHost.menu) */
const NX2GameMenu *oot_menu(void);
