/* (hookshot) Ocarina of Time's debug teleport, age switch and map (the control bridge's map, teleport and age), compiled into its runtime only (packs/oot/pack.json "host"; nx2/build.py). Cut out of
   host/hos_main.c (docs/game-packs.md a3): the code is as it was there. */
#include "nx2_pack.h"
#include "oot_host.h"
#include "../menu/oot_destinations.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32) || defined(__linux__)   /* (the debug tools: host/posix has what they call) */
/* Cross-map teleport (build e0f3ccfa). Map changes in this game only run through an event: an entrance actor
   requests the Dm_SY_BancChangeGo event with NextBanc / NextStartPos / BootEventName arguments, the event calls
   GameSystemActor's ReserveBancChange, and the sequence's ChangeBancOnEvent loads the map when the event ends.
   This builds the same event request (see work/oot-teleport-research.md). The menu's labels are the same list's
   (packs/oot/menu/oot_destinations.h, menu.c). */
typedef struct {
    const char *child_banc, *child_start, *child_boot;
    const char *adult_banc, *adult_start, *adult_boot;
} OotDestination;
/* (packs/oot/menu/oot_destinations.h: the one list, the menu's Teleport page's cards and these maps) */
#define OOT_DEST_NO_SECTION(title)
#define OOT_DEST_MAPS(name, note, child_banc, child_start, child_boot, adult_banc, adult_start, adult_boot) \
    {child_banc, child_start, child_boot, adult_banc, adult_start, adult_boot},
static const OotDestination oot_destinations[] = {OOT_DESTINATIONS(OOT_DEST_NO_SECTION, OOT_DEST_MAPS)};
#define OOT_GAMEDATA_SLOT 0x84c9e180ULL   /* GameDataMgr, as ReserveBancChange's readers load it */
#define OOT_EVENTMGR_SLOT 0x84c9e090ULL   /* EventMgr, as SeqOneShotRequestEvent loads it */
#define OOT_EVENT_ARG_SIZE 0x1b20ULL      /* EventRequestArg: SeqOneShotRequestEvent reserves this much stack */

static uint64_t oot_singleton(Ctx *c, uint64_t slot) {
    const uint64_t holder = recomp_load64(c, slot);
    return holder ? recomp_load64(c, holder) : 0;
}

/* GameDataMgr getters by MurMur3 name hash: f_8022d9c0 (bool) and f_80cd0ea0 (String64). 1 when found. */
int oot_gamedata_bool(Ctx *c, uint32_t hash, int *value) {
    const uint64_t mgr = oot_singleton(c, OOT_GAMEDATA_SLOT);
    A64Fn get = a64_lookup(0x8022d9c0ULL);
    if (!mgr || !get) return 0;
    const Ctx saved = *c;
    const uint64_t out = (c->x[31] - 0x40) & ~0xfULL;
    recomp_store64_sp(c, out, 0);
    c->x[31] = out;
    c->x[0] = mgr, c->x[1] = out, c->x[2] = hash;
    get(c);
    const int found = (int)(c->x[0] & 1);
    *value = recomp_load8(c, out) & 1;
    *c = saved;
    return found;
}

int oot_gamedata_string(Ctx *c, uint32_t hash, char *buf, size_t size) {
    const uint64_t mgr = oot_singleton(c, OOT_GAMEDATA_SLOT);
    A64Fn get = a64_lookup(0x80cd0ea0ULL);
    buf[0] = 0;
    if (!mgr || !get) return 0;
    const Ctx saved = *c;
    const uint64_t out = (c->x[31] - 0x40) & ~0xfULL;
    recomp_store64_sp(c, out, 0);
    c->x[31] = out;
    c->x[0] = mgr, c->x[1] = out, c->x[2] = hash;
    get(c);
    const int found = (int)(c->x[0] & 1);
    const uint64_t text = recomp_load64(c, out);
    *c = saved;
    if (!found || !text) return 0;
    for (size_t i = 0; i + 1 < size; i++) {
        buf[i] = (char)recomp_load8(c, text + i);
        buf[i + 1] = 0;
        if (!buf[i]) break;
    }
    return 1;
}

/* One argument of an EventRequestArg: the array of 200-byte entries at arg+8, capacity arg+0, count arg+16.
   Entry: +0 type (1 string pointer, 4 bool), +8 name buffer, +16 its capacity, +152 the value. As
   QueryBancChangeEventRequestArg (f_829df380) fills them. A string value is a pointer the request does not copy. */
static int oot_event_arg(Ctx *c, uint64_t arg, const char *name, uint32_t type, uint64_t value) {
    const uint32_t capacity = recomp_load32(c, arg), count = recomp_load32(c, arg + 16);
    if (count >= capacity) return 0;
    const uint64_t entry = recomp_load64(c, arg + 8) + (uint64_t)count * 200;
    const uint64_t name_buf = recomp_load64(c, entry + 8);
    const uint32_t name_cap = recomp_load32(c, entry + 16);
    const size_t length = strlen(name);
    if (!name_buf || length + 1 > name_cap) return 0;
    memcpy(A64_PTR(name_buf), name, length + 1);
    if (type == 4) recomp_store8(c, entry + 152, value & 1);
    else recomp_store64(c, entry + 152, value);
    recomp_store32(c, entry, type);
    recomp_store32(c, arg + 16, count + 1);
    return 1;
}

/* (the Main sequence's "Event" test: an event is playing) */
int oot_event_playing(Ctx *c) {
    const uint64_t events = oot_singleton(c, OOT_EVENTMGR_SLOT);
    return events && recomp_load64(c, events + 104);
}

static struct {
    int64_t requested_at, check_at;
    unsigned destination;
    const char *why, *banc;   /* (the last call's: why it was not sent, the map it was sent to; the control bridge's reply) */
    uint64_t result;          /* (and the event manager's answer to the request: 0 seen when the map did not change) */
} teleport;

int oot_teleport(Ctx *c, uint64_t actor, unsigned index) {
    teleport.why = "no such destination", teleport.banc = NULL;
    if (index >= sizeof oot_destinations / sizeof oot_destinations[0]) return 0;
    const int64_t now = hos_now_ns();
    if (teleport.requested_at && now - teleport.requested_at < 8000000000LL) {
        hos_log("cheats: teleport ignored, a request is still in progress");
        teleport.why = "another teleport is still in progress (one every 8 s)";
        return 0;
    }
    const uint64_t events = oot_singleton(c, OOT_EVENTMGR_SLOT);
    A64Fn make_arg = a64_lookup(0x80d99f10ULL), request = a64_lookup(0x80d9a820ULL);
    A64Fn set_link = a64_lookup(0x802dc440ULL), release_link = a64_lookup(0x8022b6f0ULL);
    if (!events || !make_arg || !request || !set_link || !release_link) {
        hos_log("cheats: teleport unavailable (event manager %#llx)", (unsigned long long)events);
        teleport.why = "the game's event manager is not there yet";
        return 0;
    }
    if (recomp_load64(c, events + 104)) {   /* (the Main sequence's "Event" test: an event is playing) */
        hos_log("cheats: teleport waits until the current event ends");
        teleport.why = "an event is playing (a cutscene, a map change's)";
        return 0;
    }
    int loading = 0, child = 1;
    if (oot_gamedata_bool(c, 0xef2b60c2u, &loading) && loading) {   /* Sequence_IsLoadingBanc */
        hos_log("cheats: teleport ignored while a map loads");
        teleport.why = "a map is loading";
        return 0;
    }
    if (!oot_gamedata_bool(c, 0xcea220d3u, &child)) hos_log("cheats: Sequence_IsChildhood not found, using child maps");
    const OotDestination *d = &oot_destinations[index];
    const char *banc = child ? d->child_banc : d->adult_banc;
    const char *start = child ? d->child_start : d->adult_start;
    const char *boot = child ? d->child_boot : d->adult_boot;
    if (!banc) {
        hos_log("cheats: teleport %u has no %s map", index, child ? "child" : "adult");
        teleport.why = child ? "this destination has no map for the child" : "this destination has no map for the adult";
        return 0;
    }

    /* The event reads its string arguments after this request returns, so they live in memory that stays. */
    static uint64_t strings;
    if (!strings) strings = hos_sys_alloc(512, 16);
    if (!strings) return teleport.why = "out of memory", 0;
    memset(A64_PTR(strings), 0, 512);
    memcpy(A64_PTR(strings), banc, strlen(banc) + 1);
    memcpy(A64_PTR(strings + 128), start, strlen(start) + 1);
    memcpy(A64_PTR(strings + 256), boot, strlen(boot) + 1);

    const char *event_env = getenv("HOS_OOT_TELEPORT_EVENT");
    const char *event = event_env && *event_env ? event_env : "Dm_SY_BancChangeGo";
    const char *starter_env = getenv("HOS_OOT_TELEPORT_STARTER");
    const int with_starter = !(starter_env && !strcmp(starter_env, "0"));

    const Ctx saved = *c;
    const uint64_t arg = (c->x[31] - OOT_EVENT_ARG_SIZE - 0x100) & ~0xfULL;
    c->x[31] = arg;
    c->x[0] = arg;
    make_arg(c);
    int ok = 1;
    {   /* the event's name: arg+6800 points at its buffer, arg+6808 the capacity */
        const uint64_t buf = recomp_load64(c, arg + 6800);
        const uint32_t cap = recomp_load32(c, arg + 6808);
        if (!buf || strlen(event) + 1 > cap) ok = 0;
        else memcpy(A64_PTR(buf), event, strlen(event) + 1);
    }
    ok = ok && oot_event_arg(c, arg, "NextBanc", 1, strings);
    ok = ok && oot_event_arg(c, arg, "NextStartPos", 1, strings + 128);
    ok = ok && oot_event_arg(c, arg, "BootEventName", 1, strings + 256);
    ok = ok && oot_event_arg(c, arg, "IsCheckAir", 4, 0);
    if (ok && with_starter) {   /* the starter actor (arg+0x1950), as OneShotEventMemberRequestEventByEventName sets it */
        c->x[31] = arg;
        c->x[0] = arg + 0x1950, c->x[1] = actor, c->x[2] = 0;
        set_link(c);
    }
    uint64_t result = 0;
    if (ok) {
        c->x[31] = arg;
        c->x[0] = events, c->x[1] = arg;
        request(c);
        result = c->x[0];
    }
    /* SeqOneShotRequestEvent's cleanup of the arg's two actor links */
    for (unsigned i = 0; i < 2; i++) {
        const uint64_t link = arg + (i ? 0x1950 : 0x1968);
        recomp_store64(c, link, 0x84994b20ULL);
        c->x[31] = arg;
        c->x[0] = link;
        release_link(c);
    }
    *c = saved;
    /* (issues 2217, 2588) A map-hiding area can leave this saved flag set when a debug teleport skips its exit.
       Only clear it once the event manager takes the request; the destination may hide its own minimap.
       Use the game's Bool setter (mgr, value, hash): it queues the change for the live value AND the save's copy.
       Writing only the live byte restores the HUD until the next load, but saves keep the old hidden value. */
    int hidden = 0;
    if (ok && result && oot_gamedata_bool(c, 0x9a4dd779u, &hidden) && hidden) {
        A64Fn set_bool = a64_lookup(0x806a2b20ULL);
        const uint64_t mgr = oot_singleton(c, OOT_GAMEDATA_SLOT);
        if (set_bool && mgr) {
            c->x[0] = mgr, c->x[1] = 0, c->x[2] = 0x9a4dd779u;   /* MapIconDeactivateByAreaActor */
            set_bool(c);
            *c = saved;
            hos_log("cheats: teleport: the area left had hidden the minimap (MapIconDeactivateByAreaActor): restore queued");
        } else {   /* (another game build: say so, a report's log then shows why the minimap stayed away) */
            hos_log("cheats: teleport: the area left had hidden the minimap (MapIconDeactivateByAreaActor): %s not found, left hidden",
                    set_bool ? "the game data manager" : "the game's Bool setter");
        }
    }
    teleport.requested_at = now;
    teleport.check_at = now + 1500000000LL;
    teleport.destination = index;
    hos_log("cheats: teleport %s to %s at %s (boot %s, event %s, starter %d): request %s, result %#llx",
            child ? "child" : "adult", banc, start, boot, event, with_starter, ok ? "sent" : "not built",
            (unsigned long long)result);
    teleport.result = result;
    if (ok) teleport.banc = banc;
    else teleport.why = "the game's event request could not be built";
    return ok;   /* (sent; the request's own result is only logged) */
}

/* Writes a GameData bool directly: GameDataMgr+32 is the (hash, index) table (+44 its size), the bools are 24-byte
   entries at GameDataMgr+24 (+16 their count) with the value at +22, as f_8022d9c0 reads them. 1 when found. */
int oot_gamedata_set_bool(Ctx *c, uint32_t hash, int value) {
    const uint64_t mgr = oot_singleton(c, OOT_GAMEDATA_SLOT);
    if (!mgr) return 0;
    const uint64_t table = recomp_load64(c, mgr + 32);
    const uint32_t size = recomp_load32(c, mgr + 44);
    if (!table || !size) return 0;
    for (uint32_t probe = 0, slot = hash % size; probe < size; probe++, slot = (slot + 1) % size) {
        const uint32_t key = recomp_load32(c, table + slot * 8ULL);
        if (!key) return 0;
        if (key != hash) continue;
        const uint32_t index = recomp_load32(c, table + slot * 8ULL + 4), count = recomp_load32(c, mgr + 16);
        if (index >= count) return 0;
        recomp_store8(c, recomp_load64(c, mgr + 24) + index * 24ULL + 22, value ? 1 : 0);
        return 1;
    }
    return 0;
}

/* Age toggle: flips Sequence_IsChildhood (what the Temple of Time event sets) and reloads the current map, or the
   Market when this map is not in oot_destinations, through the teleport so the other age's map and Link load. 1: the map
   change was sent (teleport.banc its map); 0: not, teleport.why says why (the control bridge's reply) */
int oot_toggle_age(Ctx *c, uint64_t actor) {
    int child = 1;
    if (!oot_gamedata_bool(c, 0xcea220d3u, &child)) {
        hos_log("cheats: age toggle: Sequence_IsChildhood not found");
        teleport.why = "Link's age is not in the game's data (Sequence_IsChildhood not found)", teleport.banc = NULL;
        return 0;
    }
    char current[80];
    oot_gamedata_string(c, 0x1d6189dau, current, sizeof current);   /* Sequence_CurrentBanc */
    unsigned target = 3;   /* Market (the Temple of Time is in it) */
    for (unsigned i = 0; i < sizeof oot_destinations / sizeof oot_destinations[0]; i++) {
        const OotDestination *d = &oot_destinations[i];
        const char *other = child ? d->adult_banc : d->child_banc;
        if (other && ((d->child_banc && !strcmp(d->child_banc, current)) || (d->adult_banc && !strcmp(d->adult_banc, current)))) {
            target = i;
            break;
        }
    }
    if (!oot_gamedata_set_bool(c, 0xcea220d3u, !child)) {
        hos_log("cheats: age toggle: could not write Sequence_IsChildhood");
        teleport.why = "Link's age could not be written (Sequence_IsChildhood)", teleport.banc = NULL;
        return 0;
    }
    hos_log("cheats: age toggle: %s -> %s, reloading via destination %u (current map \"%s\")",
            child ? "child" : "adult", child ? "adult" : "child", target, current);
    if (!oot_teleport(c, actor, target)) {
        oot_gamedata_set_bool(c, 0xcea220d3u, child);
        hos_log("cheats: age toggle undone, the map change was not requested");
        return 0;
    }
    return 1;
}

void oot_teleport_followup(Ctx *c) {
    if (!teleport.check_at || hos_now_ns() < teleport.check_at) return;
    teleport.check_at = 0;
    char next[80], current[80];
    oot_gamedata_string(c, 0xee27bf71u, next, sizeof next);     /* Sequence_ReservedNextBanc */
    oot_gamedata_string(c, 0x1d6189dau, current, sizeof current);   /* Sequence_CurrentBanc */
    const uint64_t events = oot_singleton(c, OOT_EVENTMGR_SLOT);
    hos_log("cheats: teleport check: reserved \"%s\", current \"%s\", event playing %d", next, current,
            events && recomp_load64(c, events + 104) ? 1 : 0);
}

/* (hookshot) the control bridge (host/control.c): the map the game is in (Sequence_CurrentBanc), read on the game's thread
   with the player's update every 250 ms and kept for the bridge's thread; the last one known (it stays through loads and
   menus, where Link is not updated). And the debug teleport by destination, as the Cheats page asks for it: taken at the
   next update of the player, in play, and what came of it (sent, or refused and why) kept for the bridge's reply */
static SRWLOCK oot_map_lock = SRWLOCK_INIT;
static char oot_map[80];
static int oot_flags = -1;   /* (1 an event playing, 2 a map loading, 4 the child; -1 not read yet) */
void oot_map_refresh(Ctx *c, int64_t now) {
    static int64_t next;
    if (now < next) return;
    next = now + 250000000LL;
    char map[80];
    if (!oot_gamedata_string(c, 0x1d6189dau, map, sizeof map) || !map[0]) return;   /* Sequence_CurrentBanc */
    int loading = 0, child = 1;
    oot_gamedata_bool(c, 0xef2b60c2u, &loading);   /* Sequence_IsLoadingBanc */
    oot_gamedata_bool(c, 0xcea220d3u, &child);     /* Sequence_IsChildhood */
    const uint64_t events = oot_singleton(c, OOT_EVENTMGR_SLOT);
    const int flags = (events && recomp_load64(c, events + 104) ? 1 : 0) | (loading ? 2 : 0) | (child ? 4 : 0);
    AcquireSRWLockExclusive(&oot_map_lock);
    memcpy(oot_map, map, sizeof oot_map);
    oot_flags = flags;
    ReleaseSRWLockExclusive(&oot_map_lock);
}
/* (with the map) whether an event (a cutscene, a map change's) is playing, a map is loading, Link is the child: 0 before any */
int hos_oot_state(int *event, int *loading, int *child) {
    AcquireSRWLockShared(&oot_map_lock);
    const int f = oot_flags;
    ReleaseSRWLockShared(&oot_map_lock);
    if (f < 0) return 0;
    *event = f & 1, *loading = (f >> 1) & 1, *child = (f >> 2) & 1;
    return 1;
}
int hos_oot_current_map(char *out, size_t cap) {
    AcquireSRWLockShared(&oot_map_lock);
    const int have = oot_map[0] != 0;
    if (have) snprintf(out, cap, "%s", oot_map);
    ReleaseSRWLockShared(&oot_map_lock);
    return have;
}
int hos_oot_destination(int index, const char **child, const char **adult) {
    if (index < 0 || index >= (int)(sizeof oot_destinations / sizeof oot_destinations[0])) return 0;
    *child = oot_destinations[index].child_banc, *adult = oot_destinations[index].adult_banc;
    return 1;
}
/* The bridge's teleport: asked on its thread, taken at the player's next update (oot_bridge_teleport_take), and what came
   of it kept for the bridge, by ticket. A later ask replaces one not taken yet. (oot_map_lock) */
static struct {
    int asked;    /* the destination + 1 waiting to be taken, OOT_BRIDGE_AGE | adult the age switch; 0 none */
    int ticket;   /* the last ask's */
    int taking;   /* the ticket taken by the player's update and not done yet (it is not replaced meanwhile) */
    int done_ticket, result, accepted;   /* the last one taken: 1 sent, -1 refused, 2 (the age switch) Link was that age
                                            already; accepted: the event manager's answer */
    char why[96], map[80];
} oot_bridge_tp;
int hos_oot_teleport_ask(int index) {
    if (index < 0 || index >= (int)(sizeof oot_destinations / sizeof oot_destinations[0])) return 0;
    AcquireSRWLockExclusive(&oot_map_lock);
    const int ticket = ++oot_bridge_tp.ticket;
    __atomic_store_n(&oot_bridge_tp.asked, index + 1, __ATOMIC_RELAXED);   /* (read without the lock first: take) */
    ReleaseSRWLockExclusive(&oot_map_lock);
    return ticket;
}
/* The bridge's age switch to the adult (1) or the child (0): the Debug tab's Switch age (oot_toggle_age), asked and taken as
   a teleport is, with the same tickets, result and cancel. The age wanted goes with it and is checked when it is taken (the
   bridge's sample of Link's age can be old): already that age, nothing is done (result 2) */
int hos_oot_age_ask(int adult) {
    AcquireSRWLockExclusive(&oot_map_lock);
    const int ticket = ++oot_bridge_tp.ticket;
    __atomic_store_n(&oot_bridge_tp.asked, OOT_BRIDGE_AGE | (adult ? 1 : 0), __ATOMIC_RELAXED);
    ReleaseSRWLockExclusive(&oot_map_lock);
    return ticket;
}
/* 1 sent (map: the map it goes to; accepted: whether the game's event manager took the request), -1 refused (why: the
   reason), 2 (the age switch) Link was that age already, nothing done, 0 not taken yet (or being taken), -2 replaced by a
   later ask or cancelled */
int hos_oot_teleport_result(int ticket, char *why, size_t why_cap, char *map, size_t map_cap, int *accepted) {
    AcquireSRWLockShared(&oot_map_lock);
    int r = -2;
    if (oot_bridge_tp.done_ticket == ticket) {
        r = oot_bridge_tp.result, *accepted = oot_bridge_tp.accepted;
        snprintf(why, why_cap, "%s", oot_bridge_tp.why);
        snprintf(map, map_cap, "%s", oot_bridge_tp.map);
    } else if (oot_bridge_tp.taking == ticket || (oot_bridge_tp.ticket == ticket && oot_bridge_tp.asked)) r = 0;
    ReleaseSRWLockShared(&oot_map_lock);
    return r;
}
void hos_oot_teleport_cancel(int ticket) {   /* (the bridge gave up waiting: not taken later behind its back) */
    AcquireSRWLockExclusive(&oot_map_lock);
    if (oot_bridge_tp.ticket == ticket) __atomic_store_n(&oot_bridge_tp.asked, 0, __ATOMIC_RELAXED);
    ReleaseSRWLockExclusive(&oot_map_lock);
}
int oot_bridge_teleport_take(int *ticket) {   /* (the player's update) the destination + 1 asked for, or 0 */
    if (!__atomic_load_n(&oot_bridge_tp.asked, __ATOMIC_RELAXED)) return 0;
    AcquireSRWLockExclusive(&oot_map_lock);
    const int asked = oot_bridge_tp.asked;
    *ticket = oot_bridge_tp.ticket;
    if (asked) oot_bridge_tp.taking = *ticket;   /* (still "not taken yet" to the bridge until done) */
    __atomic_store_n(&oot_bridge_tp.asked, 0, __ATOMIC_RELAXED);
    ReleaseSRWLockExclusive(&oot_map_lock);
    return asked;
}
void oot_bridge_teleport_done(int ticket, int sent) {   /* sent: 1, 0 refused (teleport.why), 2 already that age */
    AcquireSRWLockExclusive(&oot_map_lock);
    if (sent == 2) {
        oot_bridge_tp.done_ticket = ticket, oot_bridge_tp.result = 2, oot_bridge_tp.taking = 0, oot_bridge_tp.accepted = 0;
        oot_bridge_tp.why[0] = oot_bridge_tp.map[0] = 0;
        ReleaseSRWLockExclusive(&oot_map_lock);
        return;
    }
    oot_bridge_tp.done_ticket = ticket, oot_bridge_tp.result = sent ? 1 : -1, oot_bridge_tp.taking = 0;
    oot_bridge_tp.accepted = sent && teleport.result != 0;
    snprintf(oot_bridge_tp.why, sizeof oot_bridge_tp.why, "%s", sent || !teleport.why ? "" : teleport.why);
    snprintf(oot_bridge_tp.map, sizeof oot_bridge_tp.map, "%s", sent && teleport.banc ? teleport.banc : "");
    ReleaseSRWLockExclusive(&oot_map_lock);
}

#endif
