/* Experimental host frame-rate policy shared with its CPU-only regression. */
#ifndef HOS_RATE_POLICY_H
#define HOS_RATE_POLICY_H

#include <stdint.h>
#include <stdlib.h>

#define HOS_RATE_DEFAULT_HZ 60
#define HOS_RATE_MAX_HZ 120
#define HOS_RATE_VIDEO_GRACE_NS 250000000LL

/* Preserve HOS_REALTIME_WINDOW's upstream atoi/default/bounds semantics. */
static inline unsigned hos_rate_parse_window(const char *text) {
    const int window = text ? atoi(text) : 5;
    return window < 1 ? 1u : window > 5 ? 5u : (unsigned)window;
}

enum {
    HOS_RATE_INVALID = -1,
    HOS_RATE_UNSET = 0,
    HOS_RATE_VALID = 1,
};

/* HOS_FPS_TARGET values through 60 retain their existing limiter-floor meaning.
   Rates above 60 opt in to the experimental path, with a conservative 120 Hz ceiling. */
static inline int hos_rate_parse_target(const char *text, int *target) {
    if (!text || !*text) {
        *target = 0;
        return HOS_RATE_UNSET;
    }
    char *end = NULL;
    const long value = strtol(text, &end, 10);
    if (end == text || *end || value < 0 || value > HOS_RATE_MAX_HZ) {
        *target = 0;
        return HOS_RATE_INVALID;
    }
    *target = (int)value;
    return value ? HOS_RATE_VALID : HOS_RATE_UNSET;
}

/* A concurrent picture publication can be slightly newer than VI's snapshot of now.
   Accept that bounded skew, but not a timestamp from a different clock epoch. */
static inline int hos_rate_recent_video_picture(int64_t now, int64_t last_picture) {
    if (now < 0 || last_picture <= 0) return 0;
    return now >= last_picture ? now - last_picture <= HOS_RATE_VIDEO_GRACE_NS :
                                 last_picture - now <= HOS_RATE_VIDEO_GRACE_NS;
}

static inline int hos_rate_effective_hz(int target, int realtime, int no_pacing, int video_active) {
    return realtime && !no_pacing && !video_active && target > HOS_RATE_DEFAULT_HZ && target <= HOS_RATE_MAX_HZ ?
           target : HOS_RATE_DEFAULT_HZ;
}

static inline int64_t hos_rate_period_ns(int hz, int interval) {
    if (hz < HOS_RATE_DEFAULT_HZ || hz > HOS_RATE_MAX_HZ) hz = HOS_RATE_DEFAULT_HZ;
    if (interval < 1) interval = 1;
    if (interval > 4) interval = 4;
    /* Keep the default expression and integer rounding identical to the old VI path. */
    return (1000000000LL / hz) * interval;
}

/* Restore a value only while it still matches the value this policy last wrote. */
static inline int hos_rate_restore_owned_value(int current, int applied, int previous) {
    return current == applied ? previous : current;
}

/* Per-frame mode is fixed at 60. Only reclaim a high nominal value we wrote ourselves. */
static inline int hos_rate_restore_nominal(int current, int last_written) {
    return last_written > HOS_RATE_DEFAULT_HZ && current == last_written ? HOS_RATE_DEFAULT_HZ : current;
}

/* One atomic word identifies both the active high-rate policy and its
   successfully committed timestep nominal. */
static inline uint32_t hos_rate_committed_nominal_state(int rate, int fps) {
    if (rate <= HOS_RATE_DEFAULT_HZ || rate > HOS_RATE_MAX_HZ || fps < 15 || fps > rate) return 0;
    return ((uint32_t)rate << 8) | (uint32_t)fps;
}

static inline uint32_t hos_rate_clock_input(uint32_t original, uint32_t state) {
    if (original < 15 || original > HOS_RATE_MAX_HZ) return original;
    const int rate = (int)(state >> 8), fps = (int)(state & 255u);
    return state && hos_rate_committed_nominal_state(rate, fps) == state ? (uint32_t)fps : original;
}

typedef struct HosRateLimiter {
    int owned, previous, applied;
} HosRateLimiter;

/* Follow the effective rate, including temporary movie/mode caps. The shipping
   <=60 floor remains separate, so it cannot re-enable a capped high target. */
static inline int hos_rate_limiter_apply(HosRateLimiter *s, int current, int target, int effective_hz) {
    const int high_rate_active = effective_hz > HOS_RATE_DEFAULT_HZ;
    if (s->owned && (!high_rate_active || s->applied != effective_hz)) {
        current = hos_rate_restore_owned_value(current, s->applied, s->previous);
        s->owned = 0;
    }
    if (high_rate_active && current > 0 && current < effective_hz) {
        if (!s->owned || current != s->applied) s->previous = current;
        current = s->applied = effective_hz;
        s->owned = 1;
    }
    if (!high_rate_active && target > 0 && target <= HOS_RATE_DEFAULT_HZ && current > 0 && current < target)
        current = target;
    return current;
}

typedef struct HosRateStepper {
    int64_t last_ns;
    double samples[5];
    double carry, desired_ratio;
    unsigned count, next, window;
    int realtime, max_hz, initialized;
} HosRateStepper;

static inline void hos_rate_stepper_reset(HosRateStepper *s, int64_t now, int realtime, int max_hz) {
    s->last_ns = now;
    for (unsigned i = 0; i < 5; i++) s->samples[i] = 0.0;
    s->carry = s->desired_ratio = 0.0;
    s->count = s->next = 0;
    s->window = 5;
    s->realtime = realtime;
    s->max_hz = max_hz;
    s->initialized = 1;
}

/* Return 1 when a guest nominal frame rate is available. Mode/rate transitions and
   long stalls establish a fresh timing window instead of carrying stale elapsed time. */
static inline int hos_rate_stepper_update_window(HosRateStepper *s, int64_t now, int realtime, int max_hz,
                                                unsigned window, int *fps_out, double *ratio_out) {
    if (max_hz < HOS_RATE_DEFAULT_HZ || max_hz > HOS_RATE_MAX_HZ) max_hz = HOS_RATE_DEFAULT_HZ;
    window = window < 1 ? 1 : window > 5 ? 5 : window;
    if (!s->initialized || s->realtime != realtime || s->max_hz != max_hz || s->window != window || !realtime) {
        hos_rate_stepper_reset(s, now, realtime, max_hz);
        s->window = window;
        return 0;
    }

    const double dt = (double)(now - s->last_ns) / 1e9;
    s->last_ns = now;
    if (dt <= 0.0 || dt > 0.25) {
        s->count = s->next = 0;
        s->carry = 0.0;
        return 0;
    }
    s->samples[s->next++ % window] = dt;
    if (s->count < window) s->count++;
    double sum = 0.0;
    for (unsigned i = 0; i < s->count; i++) sum += s->samples[i];
    const double desired_ratio = sum / s->count * 60.0 + s->carry;
    s->desired_ratio = desired_ratio;
    double ratio = desired_ratio;
    const double min_ratio = 60.0 / max_hz;
    if (ratio < min_ratio) ratio = min_ratio;
    if (ratio > 4.0) ratio = 4.0;
    int fps = (int)(60.0 / ratio + 0.5);
    if (fps < 15) fps = 15;
    if (fps > max_hz) fps = max_hz;
    *fps_out = fps;
    *ratio_out = ratio;
    return 1;
}

static inline int hos_rate_stepper_update(HosRateStepper *s, int64_t now, int realtime, int max_hz,
                                          int *fps_out, double *ratio_out) {
    return hos_rate_stepper_update_window(s, now, realtime, max_hz, 5, fps_out, ratio_out);
}

/* Commit rounding carry only after the caller confirms the guest slot is writable. */
static inline void hos_rate_stepper_commit(HosRateStepper *s, int fps, double ratio) {
    if (s->max_hz > HOS_RATE_DEFAULT_HZ) {
        /* Keep negative debt when output is capped at max_hz, but discard time
           beyond the existing 15-fps slow-step ceiling instead of fast-forwarding later. */
        const double committed_ratio = s->desired_ratio > 4.0 ? 4.0 : s->desired_ratio;
        s->carry = committed_ratio - 60.0 / fps;
    } else {
        /* Preserve the shipping 60-Hz clamp and rounding behavior exactly. */
        s->carry = ratio - 60.0 / fps;
        if (s->carry > 0.5) s->carry = 0.5;
        if (s->carry < -0.5) s->carry = -0.5;
    }
}

#endif
