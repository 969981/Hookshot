/* CPU-only checks of the production frame-rate policy helper. */
#include "../hos_rate_policy.h"

#include <stdio.h>
#include <string.h>

static int fail(const char *name, int line) {
    fprintf(stderr, "FAIL %s at line %d\n", name, line);
    return 1;
}

#define CHECK(name, cond) do { if (!(cond)) return fail((name), __LINE__); } while (0)

static int step_at_window(HosRateStepper *s, int64_t t, int realtime, int hz, unsigned window, int expected) {
    int fps = -1;
    double ratio = 0.0;
    const int ready = hos_rate_stepper_update_window(s, t, realtime, hz, window, &fps, &ratio);
    if (ready) hos_rate_stepper_commit(s, fps, ratio);
    return ready && fps == expected;
}

static int step_at(HosRateStepper *s, int64_t t, int realtime, int hz, int expected) {
    return step_at_window(s, t, realtime, hz, 5, expected);
}

static int epoch_at_window(HosRateStepper *s, int64_t t, int realtime, int hz, unsigned window) {
    int fps = -1;
    double ratio = 0.0;
    return !hos_rate_stepper_update_window(s, t, realtime, hz, window, &fps, &ratio);
}

static int epoch_at(HosRateStepper *s, int64_t t, int realtime, int hz) {
    return epoch_at_window(s, t, realtime, hz, 5);
}

static int advance(HosRateStepper *s, int64_t *now, int64_t interval_ns, int max_hz,
                   double *game_seconds, int *fps_out) {
    int fps = -1;
    double ratio = 0.0;
    *now += interval_ns;
    if (!hos_rate_stepper_update(s, *now, 1, max_hz, &fps, &ratio)) return 0;
    *game_seconds += 1.0 / fps;
    hos_rate_stepper_commit(s, fps, ratio);
    if (fps_out) *fps_out = fps;
    return 1;
}

/* Test-only pre-debt policy reference for default parity and the high-rate negative control. */
typedef struct LegacyRateStepper {
    double samples[5], carry;
    unsigned count, next;
    int64_t last_ns;
} LegacyRateStepper;

static int legacy_rate_update_window(LegacyRateStepper *s, int64_t now, int max_hz, unsigned window, int *fps_out) {
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
    double ratio = sum / s->count * 60.0 + s->carry;
    const double min_ratio = 60.0 / max_hz;
    if (ratio < min_ratio) ratio = min_ratio;
    if (ratio > 4.0) ratio = 4.0;
    int fps = (int)(60.0 / ratio + 0.5);
    if (fps < 15) fps = 15;
    if (fps > max_hz) fps = max_hz;
    s->carry = ratio - 60.0 / fps;
    if (s->carry > 0.5) s->carry = 0.5;
    if (s->carry < -0.5) s->carry = -0.5;
    *fps_out = fps;
    return 1;
}

static int legacy_rate_update(LegacyRateStepper *s, int64_t now, int max_hz, int *fps_out) {
    return legacy_rate_update_window(s, now, max_hz, 5, fps_out);
}

/* The upstream 60-Hz averaging/rounding trace, including stalls, for every allowed window. */
static int upstream_window_parity(unsigned window) {
    static const int64_t intervals[] = {16666667LL, 10000000LL, 24000000LL, 50000000LL,
                                        12000000LL, 33333333LL, 300000000LL};
    HosRateStepper current = {0};
    LegacyRateStepper upstream = {0};
    int64_t now = 0;
    if (!epoch_at_window(&current, now, 1, 60, window)) return 0;
    for (int i = 0; i < 700; i++) {
        int fps = -1, expected = -1;
        double ratio = 0.0;
        now += intervals[i % (sizeof intervals / sizeof intervals[0])];
        const int ready = hos_rate_stepper_update_window(&current, now, 1, 60, window, &fps, &ratio);
        const int upstream_ready = legacy_rate_update_window(&upstream, now, 60, window, &expected);
        if (ready != upstream_ready) return 0;
        if (ready) {
            if (fps != expected) return 0;
            hos_rate_stepper_commit(&current, fps, ratio);
        }
        if (current.carry != upstream.carry || current.count != upstream.count) return 0;
    }
    return 1;
}

/* Negative control for the first debt fix: it retained positive debt above ratio 4. */
static int unbounded_positive_update(LegacyRateStepper *s, int64_t now, int max_hz, int *fps_out) {
    const double dt = (double)(now - s->last_ns) / 1e9;
    s->last_ns = now;
    if (dt <= 0.0 || dt > 0.25) {
        s->count = s->next = 0;
        s->carry = 0.0;
        return 0;
    }
    s->samples[s->next++ % 5] = dt;
    if (s->count < 5) s->count++;
    double sum = 0.0;
    for (unsigned i = 0; i < s->count; i++) sum += s->samples[i];
    const double desired_ratio = sum / s->count * 60.0 + s->carry;
    double ratio = desired_ratio;
    const double min_ratio = 60.0 / max_hz;
    if (ratio < min_ratio) ratio = min_ratio;
    if (ratio > 4.0) ratio = 4.0;
    int fps = (int)(60.0 / ratio + 0.5);
    if (fps < 15) fps = 15;
    if (fps > max_hz) fps = max_hz;
    s->carry = desired_ratio - 60.0 / fps;
    *fps_out = fps;
    return 1;
}

static int slow_step_recovery_case(void) {
    HosRateStepper current = {0};
    LegacyRateStepper flawed = {0};
    int64_t now = 0;
    double slow_game = 0.0, recovery_game = 0.0, flawed_recovery_game = 0.0;
    if (!epoch_at(&current, 0, 1, 90)) return 0;
    for (int i = 0; i < 100; i++) {
        int fps = -1;
        if (!advance(&current, &now, 100000000LL, 90, &slow_game, &fps) ||
            !unbounded_positive_update(&flawed, now, 90, &fps)) return 0;
    }
    if (current.carry > 0.000001 || current.carry < -0.000001) return 0;

    const int64_t recovery_start = now;
    int current_fps = -1, flawed_fps = -1;
    for (int i = 0; i < 60; i++) {
        if (!advance(&current, &now, 11111111LL, 90, &recovery_game, &current_fps) ||
            !unbounded_positive_update(&flawed, now, 90, &flawed_fps)) return 0;
        flawed_recovery_game += 1.0 / flawed_fps;
        if (i >= 5 && current_fps != 90) return 0;
    }
    const double recovery_wall = (double)(now - recovery_start) / 1e9;
    const double current_excess = recovery_game - recovery_wall;
    const double flawed_excess = flawed_recovery_game - recovery_wall;
    printf("90 Hz slow-step recovery: bounded-debt excess %+.6f s; unbounded-positive-debt excess %+.6f s; final buggy fps %d\n",
           current_excess, flawed_excess, flawed_fps);
    return current_fps == 90 && flawed_fps == 15 && current_excess < 0.2 && flawed_excess > 1.0;
}

static int cap_jitter_case(int max_hz, int64_t short_ns, int64_t long_ns, int high_rate_negative_control) {
    HosRateStepper current = {0};
    LegacyRateStepper legacy = {0};
    int64_t now = 0;
    double current_game = 0.0, legacy_game = 0.0;
    const int frames = 20000;
    if (!epoch_at(&current, 0, 1, max_hz)) return 0;
    for (int i = 1; i <= frames; i++) {
        int current_fps = -1, legacy_fps = -1;
        const int64_t interval = (i & 1) ? short_ns : long_ns;
        now += interval;
        double ratio = 0.0;
        if (!hos_rate_stepper_update(&current, now, 1, max_hz, &current_fps, &ratio) ||
            !legacy_rate_update(&legacy, now, max_hz, &legacy_fps)) return 0;
        if (max_hz == HOS_RATE_DEFAULT_HZ && current_fps != legacy_fps) return 0;
        current_game += 1.0 / current_fps;
        legacy_game += 1.0 / legacy_fps;
        hos_rate_stepper_commit(&current, current_fps, ratio);
    }
    const double wall = (double)now / 1e9;
    const double current_error = current_game - wall;
    const double legacy_error = legacy_game - wall;
    printf("%d Hz cap jitter: updated error %+.6f s; pre-fix error %+.6f s\n",
           max_hz, current_error, legacy_error);
    if (max_hz == HOS_RATE_DEFAULT_HZ)
        return current_game == legacy_game;  /* default 60 keeps the established behavior */
    if (high_rate_negative_control)
        return legacy_error > 1.0 && current_error < 0.02 && current_error > -0.02;
    return current_error < 0.02 && current_error > -0.02;
}

static int steady_rate(int hz) {
    HosRateStepper s = {0};
    int64_t now = 0;
    double game_seconds = 0.0;
    const int64_t interval_ns = 1000000000LL / hz;
    const int frames = 120000;
    if (!epoch_at(&s, now, 1, hz)) return 0;
    for (int i = 0; i < frames; i++)
        if (!advance(&s, &now, interval_ns, hz, &game_seconds, NULL)) return 0;
    const double wall_seconds = (double)now / 1e9;
    return game_seconds >= wall_seconds - 0.001 && game_seconds <= wall_seconds + 0.001;
}

static int mixed_and_jitter(void) {
    static const int rates[] = {50, 60, 90, 120};
    static const int64_t jitter_ns[] = {14000000LL, 19333333LL};
    HosRateStepper s = {0};
    int64_t now = 0;
    double game_seconds = 0.0;
    if (!epoch_at(&s, now, 1, 120)) return 0;

    for (unsigned block = 0; block < sizeof rates / sizeof rates[0]; block++) {
        const int hz = rates[block];
        const int64_t interval_ns = 1000000000LL / hz;
        double settled_game = 0.0;
        int64_t settled_wall_ns = 0;
        const int frames = 6000;
        for (int i = 1; i <= frames; i++) {
            int fps = -1;
            const int64_t old_now = now;
            if (!advance(&s, &now, interval_ns, 120, &game_seconds, &fps)) return 0;
            if (i > 5) {
                settled_game += 1.0 / fps;
                settled_wall_ns += now - old_now;
            }
        }
        const double settled_wall = (double)settled_wall_ns / 1e9;
        if (settled_game < settled_wall - 0.001 || settled_game > settled_wall + 0.001) return 0;
    }

    double jitter_game = 0.0;
    int64_t jitter_wall_ns = 0;
    const int jitter_frames = 24000;
    for (int i = 1; i <= jitter_frames; i++) {
        int fps = -1;
        const int64_t old_now = now;
        if (!advance(&s, &now, jitter_ns[(i - 1) & 1], 120, &game_seconds, &fps)) return 0;
        if (i > 5) {
            jitter_game += 1.0 / fps;
            jitter_wall_ns += now - old_now;
        }
    }
    const double jitter_wall = (double)jitter_wall_ns / 1e9;
    if (jitter_game < jitter_wall - 0.01 || jitter_game > jitter_wall + 0.01) return 0;

    const double total_wall = (double)now / 1e9;
    return game_seconds >= total_wall - 0.15 && game_seconds <= total_wall + 0.15;
}

/* A VI/limiter/timestep simulation using the production policies: start at the
   requested rate, acquire 30-fps pictures, then receive only empty/EOS output. */
static int movie_rate_case_window(int target, unsigned window) {
    HosRateLimiter limiter = {0};
    HosRateStepper stepper = {0};
    int64_t now = 1000000000LL, last_picture = 0;
    const int high_hz = target > 60 ? target : 60;
    int rate = hos_rate_effective_hz(target, 1, 0, hos_rate_recent_video_picture(now, last_picture));
    int limit = hos_rate_limiter_apply(&limiter, 60, target, rate);
    CHECK("idle native rate", rate == high_hz && limit == high_hz);
    CHECK("idle epoch", epoch_at_window(&stepper, now, 1, rate, window));
    now += hos_rate_period_ns(rate, 1);
    CHECK("idle timestep", step_at_window(&stepper, now, 1, rate, window, high_hz));

    last_picture = now;   /* a queued picture was acquired, including one whose PTS is zero */
    rate = hos_rate_effective_hz(target, 1, 0, hos_rate_recent_video_picture(now, last_picture));
    limit = hos_rate_limiter_apply(&limiter, limit, target, rate);
    CHECK("movie starts at 60 including owned limiter", rate == 60 && limit == 60);
    CHECK("movie nominal restored", hos_rate_restore_nominal(high_hz, high_hz) == 60);
    if (high_hz > 60) CHECK("movie resets step window", epoch_at_window(&stepper, now, 1, rate, window));
    for (int frame = 1; frame <= 60; frame++) {
        now += hos_rate_period_ns(60, 1);
        if (!(frame % 2)) last_picture = now;   /* successful picture every other VI frame */
        rate = hos_rate_effective_hz(target, 1, 0, hos_rate_recent_video_picture(now, last_picture));
        limit = hos_rate_limiter_apply(&limiter, limit, target, rate);
        CHECK("30 fps movie keeps VI and limiter at 60", rate == 60 && limit == 60);
        CHECK("movie timestep is 60", step_at_window(&stepper, now, 1, rate, window, 60));
    }
    for (int frame = 0; frame < 15; frame++) {   /* empty/EOS returns do not publish new activity */
        now += hos_rate_period_ns(60, 1);
        rate = hos_rate_effective_hz(target, 1, 0, hos_rate_recent_video_picture(now, last_picture));
        limit = hos_rate_limiter_apply(&limiter, limit, target, rate);
        CHECK("short decode gaps keep cap", rate == 60 && limit == 60);
        CHECK("empty output keeps normal timestep sampling", step_at_window(&stepper, now, 1, rate, window, 60));
    }
    now = last_picture + HOS_RATE_VIDEO_GRACE_NS;
    CHECK("last grace nanosecond active", hos_rate_recent_video_picture(now, last_picture));
    now++;
    rate = hos_rate_effective_hz(target, 1, 0, hos_rate_recent_video_picture(now, last_picture));
    limit = hos_rate_limiter_apply(&limiter, limit, target, rate);
    CHECK("empty/EOS output eventually releases cap", rate == high_hz && limit == high_hz);
    if (high_hz > 60) CHECK("movie exit resets step window", epoch_at_window(&stepper, now, 1, rate, window));
    now += hos_rate_period_ns(rate, 1);
    CHECK("resumed timestep", step_at_window(&stepper, now, 1, rate, window, high_hz));

    rate = hos_rate_effective_hz(target, 0, 0, 0);
    limit = hos_rate_limiter_apply(&limiter, limit, target, rate);
    CHECK("per-frame restores limiter", rate == 60 && limit == 60);
    CHECK("no pacing stays 60", hos_rate_effective_hz(target, 1, 1, 0) == 60);
    return 0;
}

static int movie_rate_case(int target) {
    return movie_rate_case_window(target, 5);
}

int main(void) {
    CHECK("upstream default five samples", hos_rate_parse_window(NULL) == 5);
    CHECK("upstream one-sample override", hos_rate_parse_window("1") == 1);
    CHECK("upstream intermediate override", hos_rate_parse_window("3") == 3);
    CHECK("upstream five-sample override", hos_rate_parse_window("5") == 5);
    CHECK("upstream lower bound", hos_rate_parse_window("0") == 1 && hos_rate_parse_window("-3") == 1);
    CHECK("upstream upper bound", hos_rate_parse_window("9") == 5);
    CHECK("upstream atoi behavior", hos_rate_parse_window("") == 1 && hos_rate_parse_window("other") == 1 &&
                                         hos_rate_parse_window("3tail") == 3);
    for (unsigned window = 1; window <= 5; window++) {
        CHECK("60-Hz trace matches upstream averaging", upstream_window_parity(window));
        CHECK("configured-window movie simulation60", !movie_rate_case_window(60, window));
        CHECK("configured-window movie simulation70", !movie_rate_case_window(70, window));
        CHECK("configured-window movie simulation75", !movie_rate_case_window(75, window));
    }
    HosRateStepper configured = {0};
    CHECK("configured window epoch", epoch_at_window(&configured, 0, 1, 75, 1));
    CHECK("configured window sample", step_at_window(&configured, 13333333LL, 1, 75, 1, 75));
    CHECK("window change resets sample epoch", epoch_at_window(&configured, 26666666LL, 1, 75, 3) &&
                                               configured.count == 0 && configured.carry == 0.0 && configured.window == 3);
    CHECK("changed-window first sample", step_at_window(&configured, 39999999LL, 1, 75, 3, 75));
    int target = -1;
    CHECK("unset default", hos_rate_parse_target(NULL, &target) == HOS_RATE_UNSET && target == 0);
    CHECK("zero keeps opt-out", hos_rate_parse_target("0", &target) == HOS_RATE_UNSET && target == 0);
    CHECK("legacy target accepted", hos_rate_parse_target("60", &target) == HOS_RATE_VALID && target == 60);
    CHECK("90 target accepted", hos_rate_parse_target("90", &target) == HOS_RATE_VALID && target == 90);
    CHECK("max target accepted", hos_rate_parse_target("120", &target) == HOS_RATE_VALID && target == 120);
    CHECK("unsupported rate rejected", hos_rate_parse_target("121", &target) == HOS_RATE_INVALID && target == 0);
    CHECK("negative rejected", hos_rate_parse_target("-1", &target) == HOS_RATE_INVALID && target == 0);
    CHECK("malformed rejected", hos_rate_parse_target("90hz", &target) == HOS_RATE_INVALID && target == 0);

    CHECK("default rate", hos_rate_effective_hz(0, 1, 0, 0) == 60);
    CHECK("per-frame stays 60", hos_rate_effective_hz(120, 0, 0, 0) == 60);
    CHECK("real-time opts in", hos_rate_effective_hz(120, 1, 0, 0) == 120);
    CHECK("no picture timestamp is inactive", !hos_rate_recent_video_picture(1000000000LL, 0));
    CHECK("negative clock is inactive", !hos_rate_recent_video_picture(-1, 1000000000LL));
    CHECK("concurrent newer publication stays active", hos_rate_recent_video_picture(1000000000LL, 1000000001LL));
    CHECK("backward clock outside grace expires", !hos_rate_recent_video_picture(1000000000LL, 1250000001LL));
    CHECK("large clock values do not overflow", !hos_rate_recent_video_picture(INT64_MAX, 1));
    CHECK("large backward clock values do not overflow", !hos_rate_recent_video_picture(1, INT64_MAX));
    CHECK("movie simulation default 60", !movie_rate_case(60));
    CHECK("movie simulation 75", !movie_rate_case(75));
    CHECK("movie simulation 90", !movie_rate_case(90));

    HosRateLimiter limiter = {0};
    CHECK("own high limiter", hos_rate_limiter_apply(&limiter, 60, 75, 75) == 75 && limiter.owned);
    CHECK("movie cap preserves external limiter write", hos_rate_limiter_apply(&limiter, 45, 75, 60) == 45 && !limiter.owned);
    CHECK("movie cap cannot reapply high target", hos_rate_limiter_apply(&limiter, 45, 75, 60) == 45);
    CHECK("movie exit owns new previous limiter", hos_rate_limiter_apply(&limiter, 45, 75, 75) == 75);
    CHECK("mode exit restores new previous limiter", hos_rate_limiter_apply(&limiter, 75, 75, 60) == 45);
    CHECK("unset target keeps limiter", hos_rate_limiter_apply(&limiter, 30, 0, 60) == 30);
    CHECK("legacy sub-60 floor unchanged", hos_rate_limiter_apply(&limiter, 30, 45, 60) == 45);
    CHECK("legacy 60 floor unchanged during video", hos_rate_limiter_apply(&limiter, 30, 60, 60) == 60);
    const int64_t old_default_periods[] = {16666666LL, 33333332LL, 49999998LL, 66666664LL};
    for (int interval = 1; interval <= 4; interval++)
        CHECK("default period matches old VI behavior",
              hos_rate_period_ns(60, interval) == old_default_periods[interval - 1]);
    CHECK("120 Hz interval 1", hos_rate_period_ns(120, 1) == 8333333LL);
    CHECK("120 Hz interval 2", hos_rate_period_ns(120, 2) == 16666666LL);
    CHECK("invalid period falls back to 60", hos_rate_period_ns(121, 1) == 16666666LL);
    CHECK("restore our high nominal to 60", hos_rate_restore_nominal(120, 120) == 60);
    CHECK("preserve foreign nominal value", hos_rate_restore_nominal(105, 120) == 105);
    CHECK("do not restore legacy nominal", hos_rate_restore_nominal(60, 60) == 60);
    CHECK("restore our limiter override", hos_rate_restore_owned_value(120, 120, 60) == 60);
    CHECK("preserve changed limiter", hos_rate_restore_owned_value(90, 120, 60) == 90);

    HosRateStepper s = {0};
    int fps = -1;
    double ratio = 0.0;
    CHECK("first default sample establishes epoch", epoch_at(&s, 0, 1, 60));
    CHECK("default 60 sample", step_at(&s, 16666667LL, 1, 60, 60));
    CHECK("default fast sample remains at 60", step_at(&s, 25000000LL, 1, 60, 60));

    memset(&s, 0, sizeof s);
    CHECK("90 epoch", epoch_at(&s, 0, 1, 90));
    for (int n = 1; n <= 12; n++)
        CHECK("90 Hz game step", step_at(&s, n * 11111111LL, 1, 90, 90));

    memset(&s, 0, sizeof s);
    CHECK("120 epoch", epoch_at(&s, 0, 1, 120));
    for (int n = 1; n <= 12; n++)
        CHECK("120 Hz game step", step_at(&s, n * 8333333LL, 1, 120, 120));

    CHECK("rate change resets carry/window",
          epoch_at(&s, 108333329LL, 1, 90));
    CHECK("first changed-rate interval is sampled cleanly", step_at(&s, 119444440LL, 1, 90, 90));
    CHECK("mode change resets window", epoch_at(&s, 130555551LL, 0, 60));
    CHECK("resume real-time starts fresh", epoch_at(&s, 141666662LL, 1, 90));
    CHECK("long stall resets window", epoch_at(&s, 500000000LL, 1, 90));
    CHECK("post-stall first interval works", step_at(&s, 511111111LL, 1, 90, 90));
    CHECK("zero interval resets window", !hos_rate_stepper_update(&s, 511111111LL, 1, 90, &fps, &ratio));
    CHECK("post-zero interval recovers", step_at(&s, 522222222LL, 1, 90, 90));

    memset(&s, 0, sizeof s);
    CHECK("invalid slot epoch", epoch_at(&s, 0, 1, 120));
    CHECK("candidate rate before slot check", hos_rate_stepper_update(&s, 8333333LL, 1, 120, &fps, &ratio));
    CHECK("uncommitted frame does not consume carry", s.carry == 0.0);

    CHECK("long steady 60 Hz elapsed time", steady_rate(60));
    CHECK("long steady 90 Hz elapsed time", steady_rate(90));
    CHECK("long steady 120 Hz elapsed time", steady_rate(120));
    CHECK("mixed stable blocks and jitter conserve elapsed time", mixed_and_jitter());
    CHECK("60 Hz cap jitter matches legacy output", cap_jitter_case(60, 2000000LL, 31333333LL, 0));
    CHECK("90 Hz cap jitter repairs legacy debt loss", cap_jitter_case(90, 2000000LL, 20222222LL, 1));
    CHECK("120 Hz cap jitter conserves elapsed time", cap_jitter_case(120, 1000000LL, 15666666LL, 0));
    CHECK("100 ms slow frames do not create later fast-forward", slow_step_recovery_case());

    puts("rate policy: all checks passed");
    return 0;
}
