/* (hookshot) What Ocarina of Time's menu table (menu.c) and its game side (packs/oot/host/) say to each other through the shared
   renderer (docs/game-packs.md 2.4): the option ids the game reads its debug tools at and reports to them, in the pack's own
   range (HOS_OPT_GAME_BASE and up, gpu/vk/vk_api.h), and the actions the tools send. Before game packs a4 the ids were the
   engine's (HOS_OPT_CHEAT_* 32 to 36, 4090, 4096 and up); the actions are the ones they always were, which the control
   bridge's and a test run's "!cheat <action>" send too. */
#pragma once

#include "nx2_pack.h"

enum {
    OOT_OPT_HEARTS = HOS_OPT_GAME_BASE,   /* Heart protection: 1 on, 0 off, -1 not set (HOS_OOT_PROTECT_HEARTS decides) */
    OOT_OPT_FLIGHT,                       /* No-clip flight: -1 off, 0 on (on the ground: the double-tap takes off), 1 flying */
    OOT_OPT_ACTION,                       /* the tools' actions, below, each read once: 0 none waiting */
    OOT_OPT_CAMERA,                       /* Free camera: 1 on */
    OOT_OPT_SPEED,                        /* Movement speed: Link's, in percent of his own (100 with the tools off) */
    OOT_OPT_AGE,                          /* (reported by the game) Link's age, for Switch age: 0 child, 1 adult, -1 no save */
    OOT_OPT_ITEMS = HOS_OPT_GAME_BASE + 0x100   /* (reported) + item: the Inventory page's count of it, -1 not known */
};

/* The actions (OOT_OPT_ACTION) */
#define OOT_MARK_ACTION 2          /* Mark position */
#define OOT_RETURN_ACTION 3        /* Return to mark */
#define OOT_TIME_ACTION 100        /* + the hour: Set time */
#define OOT_POUCH_ACTION 200       /* + item * 32 + amount + 10: a pouch's content changed ("!cheat" only: no row sends it now) */
#define OOT_AGE_ACTION 999         /* Switch age */
#define OOT_TELEPORT_ACTION 1000   /* + the destination (oot_destinations.h): Teleport */
#define OOT_ITEM_ACTION 3000       /* + item * 256 + op: op 0..127 adds op - 64; op 200 unlocks (1) or relocks (all) */
