/* (hookshot) Ocarina of Time's debug tools (hearts, speed, pouch and inventory, clock, flight, free camera), compiled into its runtime only (packs/oot/pack.json "host"; nx2/build.py). Cut out of
   host/hos_main.c (docs/game-packs.md a3): the code is as it was there. */
#include "nx2_pack.h"
#include "oot_host.h"
#include "../menu/oot_cheat_items.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int hos_oot_protect_hearts;
volatile int hos_oot_live_action;   /* (test runs) the live input file's "!cheat <action>": host/nn_hid.c */

int hos_oot_hearts_enabled(void) {
    int rx_option(int id, int otherwise);
    const int menu = rx_option(OOT_OPT_HEARTS, -1);
    return menu < 0 ? hos_oot_protect_hearts : menu;
}

int hos_oot_is_player(Ctx *c, uint64_t actor) {
    if (recomp_load64(c, actor) != 0x84b6c460ULL) return 0;
    // Other actors share this vtable. The actor's resource record identifies Link.
    const uint64_t resource = recomp_load64(c, actor + 16);
    return resource && recomp_load32(c, resource + 88) == 0x79616c50 &&
           recomp_load16(c, resource + 92) == 0x7265 &&
           recomp_load8(c, resource + 94) == 0;
}

/* Movement speed (the Debug tab's row): Link's character controller and the row's percent, as oot_player_speed saw them on
   his last update (0: none, or 1x) */
static uint64_t oot_speed_controller;
static int oot_speed_percent = 100;

/* A character controller's forward speed being set (nx2/oot_patches.py patch_cct_speed: s0, in metres a second, or a frame when
   per_second is 0): Link's scaled by the row while it is above 1x and the player steers him (a scene's walk, as the one into a
   map, keeps its pace), the physics step moving him by it with its collisions. Only speeds up to 15 m/s (his run is 4.5): a ledge
   climb sets some 55 for a frame, and scaled it threw him past the ledge. Every other controller as it is. */
void hos_oot_cct_speed(Ctx *c, uint64_t controller, int per_second) {
    if (!controller || controller != __atomic_load_n(&oot_speed_controller, __ATOMIC_RELAXED)) return;
    const int percent = __atomic_load_n(&oot_speed_percent, __ATOMIC_RELAXED);
    if (percent <= 100 || percent > 400 || !hos_move_stick_held()) return;
    float speed;
    memcpy(&speed, &c->vreg[0][0], sizeof speed);
    if (!(fabsf(speed) * (per_second ? 1.0f : 60.0f) <= 15.0f)) return;   /* (and NaN) */
    speed *= percent / 100.0f;
    memcpy(&c->vreg[0][0], &speed, sizeof speed);
}

#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
/* OneShotAddOrRemovePouchContent (f_828d4a70) for one pouch content by actor name: delta > 0 adds, < 0 removes.
   Returns the event status (top bits of the result word), or -1 when the pouch is not ready. */
static int oot_pouch_change(Ctx *c, const char *name, int delta) {
    const uint64_t pouch_singleton = recomp_load64(c, 0x84c9e0d0ULL);
    const uint64_t state_singleton = recomp_load64(c, 0x84c9e2a8ULL);
    const uint64_t actor_db = recomp_load64(c, 0x84eb6888ULL);
    const uint64_t actor_info = recomp_load64(c, 0x84e8c060ULL);
    A64Fn edit = a64_lookup(0x828d4a70ULL);
    if (!edit || !pouch_singleton || !recomp_load64(c, pouch_singleton) ||
        !state_singleton || !recomp_load64(c, state_singleton) || !actor_db || !actor_info || !delta ||
        strlen(name) >= 0x60) return -1;

    // OneShotAddOrRemovePouchContent reads the event parameters through pointers.
    const Ctx saved = *c;
    const uint64_t event = (c->x[31] - 0xe0) & ~0xfULL;
    memset(A64_PTR(event), 0, 0xe0);
    memcpy(A64_PTR(event + 0x80), name, strlen(name) + 1);
    recomp_store64_sp(c, event + 0x20, event + 0x60);
    recomp_store64_sp(c, event + 0x30, event + 0x70);
    recomp_store64_sp(c, event + 0x38, event + 0x68);
    recomp_store64_sp(c, event + 0x48, event + 0x74);
    recomp_store64_sp(c, event + 0x50, event + 0x74);
    recomp_store64_sp(c, event + 0x60, event + 0x80);
    recomp_store64_sp(c, event + 0x68, 0x83f0a6d8ULL); // Script
    recomp_store32_sp(c, event + 0x70, (uint32_t)delta);
    c->x[31] = event;
    c->x[0] = event;
    edit(c);
    const int result = (int)(recomp_load32(c, event + 0x18) >> 30);
    *c = saved;
    return result;
}

/* QueryGetPouchContentNumByActorName (f_82a3c260): f_805b63e0(pouch manager, &name, 0, 0) counts one content. */
static int oot_pouch_count(Ctx *c, const char *name) {
    const uint64_t manager = recomp_load64(c, 0x84e98e28ULL);
    A64Fn count = a64_lookup(0x805b63e0ULL);
    if (!manager || !count || strlen(name) >= 0x60) return -1;
    const Ctx saved = *c;
    const uint64_t frame = (c->x[31] - 0x80) & ~0xfULL;
    memcpy(A64_PTR(frame + 0x10), name, strlen(name) + 1);
    recomp_store64_sp(c, frame, frame + 0x10);
    c->x[31] = frame;
    c->x[0] = manager, c->x[1] = frame, c->x[2] = 0, c->x[3] = 0;
    count(c);
    const int n = (int)(int32_t)c->x[0];
    *c = saved;
    return n;
}

static void oot_edit_pouch(Ctx *c, int action) {
    static const char *const items[] = {
        "Item_DekuSeed", "Item_DekuNut", "Item_DekuStick", "Item_Bomb", "Item_Arrow_Normal"
    };
    const unsigned item = (unsigned)(action - OOT_POUCH_ACTION) / 32;
    const int delta = (action - OOT_POUCH_ACTION) % 32 - 10;
    if (item >= sizeof items / sizeof items[0] || !delta || delta < -10 || delta > 10) return;
    const int result = oot_pouch_change(c, items[item], delta);
    if (result >= 0) hos_log("cheats: pouch %s %+d (event status %d)", items[item], delta, result);
}

/* The Inventory page (packs/oot/menu/oot_cheat_items.h): counts go to the menu twice a second, edits come back as actions. */
#define OOT_ITEM_NAME(label, name, kind) name,
#define OOT_ITEM_KIND(label, name, kind) kind,
#define OOT_NO_SECTION(name)
static const char *const oot_item_names[] = {OOT_CHEAT_ITEMS(OOT_NO_SECTION, OOT_ITEM_NAME)};
static const unsigned char oot_item_kinds[] = {OOT_CHEAT_ITEMS(OOT_NO_SECTION, OOT_ITEM_KIND)};
#define OOT_ITEM_COUNT (sizeof oot_item_names / sizeof oot_item_names[0])

static int oot_item_count(Ctx *c, const char *name);

static void oot_inventory_refresh(Ctx *c, int64_t now) {
    static int64_t next;
    static int probed;
    if (now < next) return;
    next = now + 500000000LL;
    int loaded = 0;   /* (a save's counts read: the first item's is not -1) */
    for (unsigned i = 0; i < OOT_ITEM_COUNT; i++) {
        const int count = oot_item_count(c, oot_item_names[i]);
        if (!i) loaded = count >= 0;
        rx_option_set(OOT_OPT_ITEMS + (int)i, count);
    }
    {   /* Link's age for the Debug tab's Switch age row: 0 child, 1 adult, -1 no save loaded (Sequence_IsChildhood) */
        int child = 1;
        rx_option_set(OOT_OPT_AGE, loaded && oot_gamedata_bool(c, 0xcea220d3u, &child) ? (child ? 0 : 1) : -1);
    }
    if (loaded) {   /* the map Link is in and his age, logged when either changes: where a log's problem happened, the "Where in
                       the game" of the Insert menu's Bug report (gpu/vk/vk_bug_report.cpp reads the last of these lines) */
        static char logged[100];
        char map[80], line[100];
        int child = 1;
        if (oot_gamedata_string(c, 0x1d6189dau, map, sizeof map) && map[0]) {   /* Sequence_CurrentBanc */
            snprintf(line, sizeof line, "%s (%s)", map,
                     oot_gamedata_bool(c, 0xcea220d3u, &child) ? (child ? "child" : "adult") : "age not known");
            if (strcmp(line, logged)) hos_log("map: %s", line), snprintf(logged, sizeof logged, "%s", line);
        }
    }
    if (!probed && oot_pouch_count(c, "Item_DekuSeed") >= 0) {
        probed = 1;   /* (one line naming every count, both spellings: which names the pouch knows) */
        static const char *const probes[] = {OOT_CHEAT_ITEM_PROBES};
        const unsigned total = (unsigned)(OOT_ITEM_COUNT + sizeof probes / sizeof probes[0]);
        char line[512];
        size_t used = 0;
        for (unsigned i = 0; i < total; i++) {   /* (several short lines: the log cuts long ones) */
            const char *name = i < OOT_ITEM_COUNT ? oot_item_names[i] : probes[i - OOT_ITEM_COUNT];
            used += (size_t)snprintf(line + used, sizeof line - used, " %s=%d", name, oot_pouch_count(c, name));
            if (used > 300 || i + 1 == total) hos_log("cheats: inventory probe:%s", line), used = 0;
        }
    }
}

/* Masks share one slot (the Happy Mask Shop lends one at a time). The game takes "Item_Mask_X" when adding one and
   may keep it as "Armor_Child_Mask_X", so both spellings count and a new mask first returns the one held. */
static int oot_mask_alias(const char *name, char *alias, size_t size) {
    if (!strncmp(name, "Armor_Child_Mask_", 17)) return snprintf(alias, size, "Item_Mask_%s", name + 17), 1;
    return 0;
}

static int oot_item_count(Ctx *c, const char *name) {
    char alias[64];
    const int n = oot_pouch_count(c, name);
    if (!oot_mask_alias(name, alias, sizeof alias)) return n;
    const int m = oot_pouch_count(c, alias);
    return n > m ? n : m;
}

static void oot_inventory_edit(Ctx *c, int action) {
    const unsigned item = (unsigned)(action - OOT_ITEM_ACTION) / 256, op = (unsigned)(action - OOT_ITEM_ACTION) % 256;
    if (item >= OOT_ITEM_COUNT) return;
    const char *name = oot_item_names[item];
    char alias[64];
    const int is_mask = oot_mask_alias(name, alias, sizeof alias);
    const int have = oot_item_count(c, name);
    int delta = 0;
    if (op == 200) delta = have > 0 ? -have : 1;   /* unlock / relock */
    else if (op < 128) delta = (int)op - 64;
    if (!delta || have < 0) return;
    if (is_mask && delta > 0) {   /* return whichever mask is in the slot first */
        for (unsigned i = 0; i < OOT_ITEM_COUNT; i++) {
            char other_alias[64];
            if (i == item || !oot_mask_alias(oot_item_names[i], other_alias, sizeof other_alias)) continue;
            const char *spellings[2] = {oot_item_names[i], other_alias};
            for (unsigned k = 0; k < 2; k++) {
                const int held = oot_pouch_count(c, spellings[k]);
                if (held > 0) hos_log("cheats: inventory returned %s (status %d)", spellings[k], oot_pouch_change(c, spellings[k], -held));
            }
        }
        delta = 1;
    }
    int result = -1;
    if (is_mask && delta < 0) {   /* remove under whichever spelling holds it */
        const char *spellings[2] = {name, alias};
        for (unsigned k = 0; k < 2; k++) {
            const int held = oot_pouch_count(c, spellings[k]);
            if (held > 0) result = oot_pouch_change(c, spellings[k], -held);
        }
    } else {
        result = oot_pouch_change(c, is_mask ? alias : name, delta);   /* (masks are added as Item_Mask_X, as the shop does) */
    }
    hos_log("cheats: inventory %s %+d (had %d, now %d, event status %d)", name, delta, have, oot_item_count(c, name), result);
}

static void oot_set_time(Ctx *c, unsigned hour) {
    // ExecuteWmSetTime passes seconds since midnight to the world manager.
    const uint64_t singleton = recomp_load64(c, 0x84c9e898ULL);
    const uint64_t world = singleton ? recomp_load64(c, singleton) : 0;
    A64Fn set_time = a64_lookup(0x834722d0ULL);
    if (!set_time || !world || !recomp_load64(c, world + 0x510)) {
        hos_log("cheats: world clock unavailable");
        return;
    }
    const Ctx saved = *c;
    c->x[0] = world;
    c->x[1] = hour * 3600;
    set_time(c);
    *c = saved;
    hos_log("cheats: requested %02u:00", hour);
}

static uint64_t oot_gameplay_view(Ctx *c) {
    const uint64_t singleton = recomp_load64(c, 0x84ca6bc0ULL);
    const uint64_t manager = singleton ? recomp_load64(c, singleton) : 0;
    const uint64_t cameras = manager && recomp_load32(c, manager + 32) ? recomp_load64(c, manager + 40) : 0;
    const uint64_t camera = cameras ? recomp_load64(c, cameras) : 0;
    return camera ? recomp_load64(c, camera + 120) : 0;
}

static float oot_load_float(Ctx *c, uint64_t addr) {
    const uint32_t bits = recomp_load32(c, addr);
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static _Thread_local uint64_t flight_transform_controller;
static _Thread_local uint64_t flight_pose_actor;
static _Thread_local float flight_transform_yaw;
static int64_t flight_player_seen;
static int64_t player_free_seen;   /* (Link updated with no event playing: the Auto run row, hos_oot_player_free) */

/* Link's controller (vtable 0x84c54f10, the one OneShotPhysicsCctChangePosition moves) from his components, 0 when the chain
   is not there */
static uint64_t oot_player_controller(Ctx *c, uint64_t components) {
    const unsigned offsets[] = {80, 32, 16, 8};
    uint64_t controller = components;
    for (unsigned i = 0; i < sizeof offsets / sizeof offsets[0]; i++) {
        controller = recomp_load64(c, controller + offsets[i]);
        if (!controller) return 0;
    }
    return recomp_load64(c, controller) == 0x84c54f10ULL ? controller : 0;
}

/* Movement speed (the Debug tab's row, OOT_OPT_SPEED in percent): Link's controller and the percent for
   hos_oot_cct_speed, which scales the forward speed the game sets on it. (The controller's speed rate beside it, +476, is no
   use: the game sets it again before each step.) */
static void oot_player_speed(Ctx *c, uint64_t components) {
    int rx_option(int id, int otherwise);
    static int logged = 100;
    int percent = rx_option(OOT_OPT_SPEED, 100);
    if (percent < 100 || percent > 400) percent = 100;
    __atomic_store_n(&oot_speed_controller, percent > 100 ? oot_player_controller(c, components) : 0, __ATOMIC_RELAXED);
    __atomic_store_n(&oot_speed_percent, percent, __ATOMIC_RELAXED);
    if (percent != logged) hos_log("cheats: movement speed %d%%", percent);
    logged = percent;
}

/* Whether the game moved Link since the flight last placed him: he is more than 2 units (the flight moves him 4 a second)
   from where it holds him. A respawn after a fall or a warp keeps his actor, so the flight took him straight back to
   where it had him, over the same drop: he fell, respawned and fell again until the flight was switched off. It starts
   again from where the game put him instead. */
static int oot_flight_moved_away(Ctx *c, uint64_t actor, const float held[3]) {
    float apart = 0;
    for (unsigned i = 0; i < 3; i++) {
        const float d = oot_load_float(c, actor + 796 + i * 4) - held[i];
        apart += d * d;
    }
    return apart > 2.0f * 2.0f;
}

static void oot_update_player_cheats(Ctx *c, uint64_t actor, uint64_t components) {
    int rx_option(int id, int otherwise);
    static uint64_t flying_actor, marked_actor, marked_controller;
    static int64_t last_tick, last_player_tick;
    static float target[3], velocity[3], mark[3], yaw;
    int flying = rx_option(OOT_OPT_FLIGHT, 0) > 0;
    /* An event that takes Link (a map change he flew into, the age toggle's, a cutscene) waits for him to stand, and the
       flight holds him in the air: the screen the event had faded stayed black until the flight was switched off, and a
       second request met "waits until the current event ends". While an event plays he is let go and lands; the flight
       takes him again, from where he then stands, when it has ended. */
    if (flying && oot_event_playing(c)) flying = 0;
    if (!flying) flying_actor = 0;
    const int64_t now = hos_now_ns();
    __atomic_store_n(&flight_player_seen, now, __ATOMIC_RELAXED);
    if (marked_actor != actor || now - last_player_tick > 2000000000LL) marked_controller = 0;
    last_player_tick = now;
    int action = rx_option(OOT_OPT_ACTION, 0);
    if (!action) action = __atomic_exchange_n(&hos_oot_live_action, 0, __ATOMIC_RELAXED);
    int bridge_ticket = 0;   /* (the control bridge's teleport or age switch, host/control.c: what came of it is kept for its reply) */
    int bridge_adult = -1;   /* (the bridge's age switch: the age wanted, 1 the adult) */
    if (!action) {
        const int asked = oot_bridge_teleport_take(&bridge_ticket);
        if (asked & OOT_BRIDGE_AGE) action = OOT_AGE_ACTION, bridge_adult = asked & 1;
        else if (asked) action = OOT_TELEPORT_ACTION + asked - 1;
    }
    oot_teleport_followup(c);
    oot_inventory_refresh(c, now);
    oot_player_speed(c, components);
    oot_map_refresh(c, now);
    if (action >= OOT_ITEM_ACTION && action < OOT_ITEM_ACTION + 256 * (int)OOT_ITEM_COUNT) {
        oot_inventory_edit(c, action);
    } else if (action == OOT_AGE_ACTION) {
        int child = 1;   /* (the bridge's: Link's age now, not its sample's; already the age wanted, nothing to flip) */
        if (bridge_adult >= 0 && oot_gamedata_bool(c, 0xcea220d3u, &child) && !child == bridge_adult) {
            hos_log("cheats: age toggle: Link is the %s already", child ? "child" : "adult");
            oot_bridge_teleport_done(bridge_ticket, 2);
        } else {
            const int sent = oot_toggle_age(c, actor);
            if (bridge_ticket) oot_bridge_teleport_done(bridge_ticket, sent);
        }
    } else if (action >= OOT_TELEPORT_ACTION) {
        const int sent = oot_teleport(c, actor, (unsigned)(action - OOT_TELEPORT_ACTION));
        if (bridge_ticket) oot_bridge_teleport_done(bridge_ticket, sent);
    } else if (action >= OOT_POUCH_ACTION) {
        oot_edit_pouch(c, action);
    } else if (action >= OOT_TIME_ACTION && action < OOT_TIME_ACTION + 24) {
        oot_set_time(c, action - OOT_TIME_ACTION);
    }
    if (!flying && action != OOT_MARK_ACTION && action != OOT_RETURN_ACTION) return;

    // Follow OneShotPhysicsCctChangePosition to Link's controller.
    const uint64_t controller = oot_player_controller(c, components);
    if (!controller || recomp_load64(c, 0x84c54f10ULL + 0xe8) != 0x806d2530ULL) return;
    A64Fn change_position = a64_lookup(0x806d2530ULL);
    if (!change_position) return;

    if (action == OOT_MARK_ACTION) {
        for (unsigned i = 0; i < 3; i++) {
            const uint32_t bits = recomp_load32(c, actor + 796 + i * 4);
            memcpy(&mark[i], &bits, sizeof bits);
        }
        marked_actor = actor;
        marked_controller = controller;
        hos_log("cheats: marked position %.2f %.2f %.2f", mark[0], mark[1], mark[2]);
    }
    const int returning = action == OOT_RETURN_ACTION && marked_controller == controller;
    if (action == OOT_RETURN_ACTION && !returning) hos_log("cheats: no mark for this player controller");
    if (!flying && !returning) return;
    const int64_t elapsed = now - last_tick;
    if (flying_actor != actor)
        yaw = atan2f(oot_load_float(c, actor + 816), oot_load_float(c, actor + 840));
    if (returning) {
        memcpy(target, mark, sizeof target);
        memset(velocity, 0, sizeof velocity);
        flying_actor = flying ? actor : 0;
    } else if (flying_actor != actor || elapsed < 0 || elapsed > 250000000 || oot_flight_moved_away(c, actor, target)) {
        for (unsigned i = 0; i < 3; i++) target[i] = oot_load_float(c, actor + 796 + i * 4);
        memset(velocity, 0, sizeof velocity);
        flying_actor = actor;
        last_tick = now;
        return;
    } else if (elapsed == 0) {
        return;
    }
    last_tick = now;

    DWORD foreground = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &foreground);
    if (!returning) {
        const float dt = fminf((float)elapsed * 1e-9f, 0.05f);
        float desired[3] = {0};
        if (foreground == GetCurrentProcessId() && !rx_option(1, 0) && !rx_option(OOT_OPT_CAMERA, 0)) {
            const uint64_t axes = hos_flight_input();
            const float right = (int16_t)axes / 32767.0f, forward = (int16_t)(axes >> 16) / 32767.0f;
            const int vertical = (GetAsyncKeyState(VK_PRIOR) < 0) - (GetAsyncKeyState(VK_NEXT) < 0);
            const uint64_t view = oot_gameplay_view(c);
            float rx = 1, rz = 0;
            if (view && recomp_load64(c, view) == 0x84c18a88ULL) {
                const float x = oot_load_float(c, view + 8), z = oot_load_float(c, view + 16);
                const float length = hypotf(x, z);
                if (length > 0.01f) rx = x / length, rz = z / length;
            }
            desired[0] = rx * right + rz * forward;
            desired[1] = vertical ? vertical : (int16_t)(axes >> 32);
            desired[2] = rz * right - rx * forward;
        }
        const float length = sqrtf(desired[0] * desired[0] + desired[1] * desired[1] + desired[2] * desired[2]);
        const float speed = 4.0f / fmaxf(1.0f, length);
        const float blend = 1.0f - expf(-12.0f * dt);
        for (unsigned i = 0; i < 3; i++) {
            velocity[i] += (desired[i] * speed - velocity[i]) * blend;
            target[i] += velocity[i] * dt;
        }
        if (hypotf(desired[0], desired[2]) > 0.08f) {
            const float delta = remainderf(atan2f(desired[0], desired[2]) - yaw, 6.28318531f);
            yaw += fmaxf(-8.0f * dt, fminf(delta, 8.0f * dt));
        }
    }

    const Ctx saved = *c;
    const uint64_t arg = c->x[31] - 16;
    for (unsigned i = 0; i < 3; i++) {
        uint32_t bits;
        memcpy(&bits, &target[i], sizeof bits);
        recomp_store32_sp(c, arg + i * 4, bits);
    }
    recomp_store32_sp(c, arg + 12, 0);
    c->x[31] = arg;
    c->x[0] = controller;
    c->x[1] = arg;
    flight_transform_yaw = yaw;
    flight_pose_actor = flying && !returning ? actor : 0;
    flight_transform_controller = flying && !returning ? controller : 0;
    change_position(c);
    flight_transform_controller = 0;
    *c = saved;
    if (returning) hos_log("cheats: returned to %.2f %.2f %.2f", mark[0], mark[1], mark[2]);
}
#endif

/* (the Insert menu's Auto run, host/auto_run.h) Link is the player's to move: updated within the last 250 ms with no
   event playing (a cutscene, a talk). A paused game stops his updates */
int hos_oot_player_free(void) {
#if defined(_WIN32) || defined(__linux__)
    const int64_t seen = __atomic_load_n(&player_free_seen, __ATOMIC_RELAXED);
    return seen && hos_now_ns() - seen < 250000000;
#else
    return 0;
#endif
}

int hos_oot_flight_shortcut_ready(void) {
#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
    const int64_t seen = __atomic_load_n(&flight_player_seen, __ATOMIC_RELAXED);
    return seen && hos_now_ns() - seen < 250000000;
#else
    return 0;
#endif
}

#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
static void oot_write_flight_rotation(Ctx *c, uint64_t matrix) {
    const float s = sinf(flight_transform_yaw), co = cosf(flight_transform_yaw);
    const float rotation[3][3] = {{co, 0, s}, {0, 1, 0}, {-s, 0, co}};
    for (unsigned row = 0; row < 3; row++) {
        for (unsigned col = 0; col < 3; col++) {
            uint32_t bits;
            memcpy(&bits, &rotation[row][col], sizeof bits);
            recomp_store32(c, matrix + row * 16 + col * 4, bits);
        }
    }
}
#endif

void hos_oot_flight_rotation(Ctx *c, uint64_t controller, uint64_t matrix) {
#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
    if (flight_transform_controller == controller) oot_write_flight_rotation(c, matrix);
#else
    (void)c;
    (void)controller;
    (void)matrix;
#endif
}

void hos_oot_flight_pose(Ctx *c, uint64_t actor, uint64_t matrix) {
#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
    if (flight_pose_actor == actor) {
        flight_pose_actor = 0;
        oot_write_flight_rotation(c, matrix);
    }
#else
    (void)c;
    (void)actor;
    (void)matrix;
#endif
}

void hos_oot_lift_player(Ctx *c, uint64_t actor) {
#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
    flight_pose_actor = 0;
    if (recomp_load64(c, actor) != 0x84b45e18ULL) return;
    const uint64_t components = recomp_load64(c, actor + 624);
    const uint64_t movement = components ? recomp_load64(c, components + 800) : 0;
    if (movement < HOS_HEAP_BASE + 0x1d50 || recomp_load64(c, movement + 24) != actor ||
        !hos_oot_is_player(c, movement - 0x1d50)) return;
    if (!oot_event_playing(c)) hos_oot_opening(c, 3);   /* the opening has let Link go: later events keep their own skip */
    __atomic_store_n(&player_free_seen, oot_event_playing(c) ? 0 : hos_now_ns(), __ATOMIC_RELAXED);

    // Link can be updated by different guest workers on successive frames.
    static SRWLOCK cheats_lock = SRWLOCK_INIT;
    if (!TryAcquireSRWLockExclusive(&cheats_lock)) return;
    oot_update_player_cheats(c, actor, components);
    ReleaseSRWLockExclusive(&cheats_lock);
#else
    (void)c;
    (void)actor;
#endif
}

#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
static void oot_build_free_view(Ctx *c, uint64_t view, uint64_t out) {
    int rx_option(int id, int otherwise);
    static uint64_t active_view;
    static int64_t last_tick;
    static double eye[3], yaw, pitch;
    const int64_t now = hos_now_ns();
    const int64_t elapsed = now - last_tick;

    if (!view) {
        active_view = 0;
        return;
    }
    if (active_view != view || elapsed > 500000000) {
        double target[3];
        memcpy(eye, A64_PTR(view + 96), sizeof eye);
        memcpy(target, A64_PTR(view + 120), sizeof target);
        const double x = eye[0] - target[0], y = eye[1] - target[1], z = eye[2] - target[2];
        yaw = atan2(x, z);
        pitch = atan2(y, hypot(x, z));
        active_view = view;
        hos_log("cheats: free camera at %.2f %.2f %.2f", eye[0], eye[1], eye[2]);
    }
    last_tick = now;
    DWORD foreground = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &foreground);
    const double dt = elapsed > 0 && elapsed < 100000000 ? (double)elapsed * 1e-9 : 0;
    const int control = foreground == GetCurrentProcessId() && !rx_option(1, 0);
    int height = 0;
    const uint64_t input = control ? hos_free_camera_input(&height) : 0;
    if (control) {
        yaw -= 1.6 * dt * (int16_t)(input >> 32) / 32767.0;
        pitch -= 1.2 * dt * (int16_t)(input >> 48) / 32767.0;
        pitch = fmax(-1.55, fmin(1.55, pitch));
    }
    const double sy = sin(yaw), cy = cos(yaw), sp = sin(pitch), cp = cos(pitch);
    const double axes[3][3] = {{cy, 0, -sy}, {-sp * sy, cp, -sp * cy}, {cp * sy, sp, cp * cy}};
    if (control) {
        const double step = dt * (GetAsyncKeyState(VK_CONTROL) < 0 ? 30.0 : 8.0);
        const double right = (int16_t)input / 32767.0;
        const double forward = (int16_t)(input >> 16) / 32767.0;
        const int key_height = (GetAsyncKeyState(VK_PRIOR) < 0) - (GetAsyncKeyState(VK_NEXT) < 0);
        eye[1] += step * (key_height ? key_height : height);
        for (int i = 0; i < 3; i++)
            eye[i] += step * (right * axes[0][i] - forward * axes[2][i]);
    }
    for (int row = 0; row < 3; row++) {
        const double *axis = axes[row];
        const float values[4] = {(float)axis[0], (float)axis[1], (float)axis[2],
            (float)-(axis[0] * eye[0] + axis[1] * eye[1] + axis[2] * eye[2])};
        for (int col = 0; col < 4; col++) {
            uint32_t bits;
            memcpy(&bits, &values[col], sizeof bits);
            recomp_store32(c, out + (row * 4 + col) * 4, bits);
        }
    }
}
#endif

void hos_oot_free_view(Ctx *c, uint64_t view) {
#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
    int rx_option(int id, int otherwise);
    if (!view || recomp_load64(c, view) != 0x84c18a88ULL) return;

    // Camera zero supplies the gameplay view. The same matrix routine also serves other cameras.
    if (oot_gameplay_view(c) != view) return;

    static SRWLOCK lock = SRWLOCK_INIT;
    AcquireSRWLockExclusive(&lock);
    const int enabled = rx_option(OOT_OPT_CAMERA, 0);
    oot_build_free_view(c, enabled ? view : 0, view + 8);
    ReleaseSRWLockExclusive(&lock);
#else
    (void)c;
    (void)view;
#endif
}

/* (the pack's start, nx2_pack_host start) HOS_OOT_PROTECT_HEARTS=1: hearts kept while the menu's row is untouched */
void oot_cheats_start(void) {
    const char *protect_hearts = getenv("HOS_OOT_PROTECT_HEARTS");
    hos_oot_protect_hearts = protect_hearts && !strcmp(protect_hearts, "1");
}
