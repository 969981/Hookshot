/* (hookshot) Two places in the game that need whole 60 fps frames, under a Frame rate cap other than 60, where the frame step is the
   frame's own time (host/nn_vi.c, hos_frame_seconds_bits: 0.5 at 120 fps). nx2/oot_patches.py hooks them; host/oot_high_fps.c
   holds the state. At a cap of 60 (no frame time given: timed 0) both return what the game computed, so nothing changes.

   The sound manager's job (f_80435300, its phase in w2): phase 0 hands the game step to the sound engine, whose fade and delay
   timers and parameter ramps count updates of at least one whole step. Fed less, some sounds are not released when an area
   unloads, the voices in use climb until the game's voice budget is full, and new sounds (sword, voices), then the area music,
   stop until a restart. Above 60 fps the job runs at 60 Hz: phase 0 adds the step to a remainder and runs once a whole step has
   built up, with a step of 1 (sound_step), keeping the rest; the job's other phases follow that frame's choice. Sound keeps the
   clock on average. A step of 1 or more runs as the game has it and keeps the remainder: around 60 fps (a cap above 60 with
   the game near 60, or VRR) steps of 0.98 and 1.02 alternate, and the 0.98 that did not run comes back as a whole step later
   (dropped, the sound clock ran at half the real one). A step of 0 or less (or NaN), and every phase at a cap of 60, run as the
   game has them, and clear the remainder.
   (After zortos293/Lichii PR #1289 by auz466, GPL-3.0: the same gate as a code patch for the emulator.)

   The physics world's solver steps: the game sets them each update to (int)(frame step * the steps of a 60 fps frame + 0.001)
   (f_838ceb80 and f_838cfc10, into the solver's +712; 8 a 60 fps frame in the places seen). A step under 1 cuts that short,
   and as the measured step wanders around its mean the count changes from frame to frame (8 x 0.49 is 3, 8 x 0.5 is 4 at 120
   fps); 4 steps a 60 fps frame would be 0 above 240. A moving platform (the stone one under the spikes in the Deku Tree)
   then slides out from under Link (issue 1798). While the step is under 1 the count is rounded up instead, so a solver step
   never covers more time than at 60 fps, give or take 10% (the frame time's jitter, so a steady rate keeps a steady count),
   and it is at least 1 and never under the game's. A step of 1 or more (a frame slower than 60 fps) keeps the game's count.

   Floors that carry a character: the character controller's three follow-surface updates (f_8015d490, f_80159dc0, f_838baa40)
   add the velocity of what Link stands on to his move, but first compare the physics' inverse step (one over the step's seconds
   times the time scale) with 100.0 and, above it, keep a tenth of that velocity. At the console's 60 and 30 fps the inverse
   step is 60 or 30, so that only happens in a slow motion under 0.6 of the game's speed; with the frames' own time as the step
   it is the frame rate, and above 100 fps every update cut the floor's velocity to a tenth: Link was left behind on moving
   platforms (the Deku Tree's floating one) and the floor slid out from under him. The gate now compares with 100 over the
   frame step (frame seconds x 60) while that is under 1, so the cut comes at the time scales where it came at 60 fps and
   nowhere else; at a step of 1 or more (60 fps and below, or no frame time: a cap of 60) it stays the game's 100. After
   NX2navi (chrg6) commit c84724d, which found and measured it, used with its author's permission. */
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    float part;   /* (the steps added up since the job last ran, under 1) */
    int run;      /* (phase 0's choice, for the other phases of the same frame) */
} OotSoundGate;
#define OOT_SOUND_GATE_INIT {0.0f, 1}   /* (every phase runs until a phase 0 has chosen) */

static inline float oot_bits_float(uint32_t bits) {
    float f;
    memcpy(&f, &bits, 4);
    return f;
}
static inline uint32_t oot_float_bits(float f) {
    uint32_t bits;
    memcpy(&bits, &f, 4);
    return bits;
}

/* (whether the step is a part of a frame: the only case the gate and the feed change) */
static inline int oot_sound_partial(float step, int timed) {
    return timed && step > 0.0f && step < 1.0f;
}

/* 1: run the job now, 0: return from it at once */
static inline int oot_sound_gate(OotSoundGate *g, uint32_t phase, float step, int timed) {
    if (!timed) {   /* (a cap of 60: every phase of every frame, whatever came before) */
        g->part = 0.0f;
        g->run = 1;
        return 1;
    }
    if (phase != 0) return g->run;
    if (!(step > 0.0f)) {   /* (0, below 0, NaN: as the game has it) */
        g->part = 0.0f;
        g->run = 1;
        return 1;
    }
    if (step >= 1.0f) {   /* (a whole step or more: runs with it, the remainder kept for the next partial ones) */
        g->run = 1;
        return 1;
    }
    g->part += step;
    g->run = g->part >= 1.0f;
    if (g->run) g->part -= 1.0f;
    return g->run;
}

/* The step phase 0 hands to the sound engine: a whole one while the gate holds the job to whole steps */
static inline uint32_t oot_sound_step(uint32_t step_bits, int timed) {
    return oot_sound_partial(oot_bits_float(step_bits), timed) ? 0x3f800000u : step_bits;
}

/* The solver's steps: game (the game's own count), scaled_bits (frame step * steps a 60 fps frame + 0.001, the float it cut),
   per_frame_bits (the steps of a 60 fps frame, a float) */
static inline int32_t oot_solver_steps(int32_t game, uint32_t scaled_bits, uint32_t per_frame_bits, int timed) {
    const float scaled = oot_bits_float(scaled_bits) - 0.001f, per_frame = oot_bits_float(per_frame_bits);
    if (!timed || !(scaled > 0.0f) || !(per_frame > 0.0f) || !(scaled < per_frame) || per_frame > 1000.0f) return game;
    int32_t steps = (int32_t)ceilf(scaled - 0.1f);
    if (steps < 1) steps = 1;
    return steps > game ? steps : game;
}

/* The number f_8015d490, f_80159dc0 and f_838baa40 compare the inverse step with: seconds_bits, the frame's seconds as the host
   gives them (0: no frame time, a cap of 60) */
static inline float oot_floor_cut(uint32_t seconds_bits) {
    const float step = oot_bits_float(seconds_bits) * 60.0f;
    return seconds_bits && step > 0.0f && step < 1.0f ? 100.0f / step : 100.0f;
}
