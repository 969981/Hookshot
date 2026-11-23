/* (hookshot) Ocarina of Time's Skip opening, compiled into its runtime only (packs/oot/pack.json "host"; nx2/build.py). Cut out of
   host/hos_main.c (docs/game-packs.md a3): the code is as it was there. */
#include "nx2_pack.h"
#include "oot_host.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The new-game opening, build e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348. Sequence/Opening.module.ainb requests
   Opening_Introduction and Opening_LoadingKokiriForest (SeqOneShotRequestEvent, f_80d9a820; the event name is the
   pointer at the request arg + 6800, which that function reads). Opening_Introduction's flowchart writes the state
   on both paths: the clock at 6:00, Meet_DekuTree's challenge Step01, the forest terrain pin, then
   EventTransition of the timeline Dm_Kokiri_0030. Its own skip query (IsEventSkipedMostRecent, f_80de6cb0) then
   takes the short edge: Navi warped to the position that edge names, the in-game music, Navi's talked flag, the
   camera handed back, and an autosave. Opening_LoadingKokiriForest's flowchart plays the timeline Dm_Kokiri_0000
   and then waits until the forest banc has loaded. Neither timeline writes save data. Meet_DekuTree is a later
   flow and is not one of these names.

   With Skip opening on, the request is still sent, so the flowchart applies that edge. The two timelines are not
   started: f_83b5bfb0 is their only starter (EventTransition at 0x80eaba00). Leaving bit 0 of the timeline player
   + 0xf25 clear makes that caller take its own exit at 0x80eac5e0, which finishes the action node, instead of the
   exit at 0x80eac640 that leaves the timeline running. The skip query is answered yes only while one of these two
   requests is the current one, so the short edge runs and a title demo that uses the same query does not. The
   cutscene frame counter is not moved. mode 0 observes a request, 1 a timeline start (return 1: do not start it),
   2 the skip query (return 1: x0 is 1), 3 Link is updating and no event is playing (the opening has let him go). */
int hos_oot_opening(Ctx *c, int mode) {
    int rx_option(int id, int otherwise);
    static int armed;
    static char logged[64];
    char name[80];
    uint64_t p = 0;
    if (mode == 3) {
        armed = 0;
        logged[0] = 0;
        return 0;
    }
    if (!c || rx_option(HOS_OPT_SKIP_OPENING, 0) <= 0) {
        armed = 0;
        return 0;
    }
    name[0] = 0;
    if (mode == 0) {
        const uint64_t arg = c->x[1];
        p = arg ? recomp_load64(c, arg + 6800) : 0;
    } else if (mode == 1 && c->x[1]) {
        p = recomp_load64(c, c->x[1]);   /* EventTransition passes a pointer to the timeline's name */
    }
    if (p) {
        size_t i = 0;
        for (; i + 1 < sizeof name; i++) {
            name[i] = (char)recomp_load8(c, p + i);
            name[i + 1] = 0;
            if (!name[i]) break;
        }
    }
    if (mode == 0) {
        if (!strcmp(name, "Opening_Introduction") || !strcmp(name, "Opening_LoadingKokiriForest")) {
            armed = 1;
            if (strcmp(logged, name)) {
                size_t i = 0;
                for (; name[i] && i + 1 < sizeof logged; i++) logged[i] = name[i];
                logged[i] = 0;
                hos_log("opening skip: %s request sent", name);
            }
        } else if (name[0]) {
            /* a different event (Meet_DekuTree included) is not skipped and drops the arm */
            armed = 0;
            logged[0] = 0;
        }
        return 0;   /* the request is still sent */
    }
    if (!armed) return 0;
    if (mode == 2) {
        c->x[0] = 1;
        return 1;
    }
    if (mode == 1 && (!strcmp(name, "Dm_Kokiri_0000") || !strcmp(name, "Dm_Kokiri_0030"))) {
        const uint64_t player = c->x[0];
        if (player) recomp_store8(c, player + 0xf25, recomp_load8(c, player + 0xf25) & ~(uint64_t)1);
        return 1;
    }
    return 0;
}
