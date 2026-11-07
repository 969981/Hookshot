/* CPU-only committed-state/consumer tests; no game, renderer or guest memory. */
#include "../hos_rate_policy.h"
#include <stdio.h>

#define CHECK(name, value) do { if (!(value)) { fprintf(stderr, "FAIL %s at line %d\n", name, __LINE__); return 1; } } while (0)

/* A focused caller model using the real rate selector, stepper, commit and input
   helpers. Publication follows only a ready, writable, successfully committed step. */
typedef struct Model { HosRateStepper stepper; uint32_t state; } Model;
static void frame(Model *m, int64_t now, int module, int realtime, int target, int movie, int slot) {
    const int rate = hos_rate_effective_hz(target, realtime, 0, movie);
    if (!module) { m->state = 0; return; }
    if (!realtime || rate <= 60 || (int)(m->state >> 8) != rate) m->state = 0;
    int fps = 0;
    double ratio = 0.0;
    if (!hos_rate_stepper_update(&m->stepper, now, realtime, rate, &fps, &ratio) || slot < 15 || slot > rate) {
        m->state = 0;
        return;
    }
    hos_rate_stepper_commit(&m->stepper, fps, ratio);
    m->state = hos_rate_committed_nominal_state(rate, fps);
}

int main(void) {
    const uint32_t p75 = hos_rate_committed_nominal_state(75, 75);
    CHECK("75 committed substitutes original load", hos_rate_clock_input(60, p75) == 75);
    CHECK("missing publication preserves original", hos_rate_clock_input(60, 0) == 60);
    CHECK("unsupported original initialization preserved", hos_rate_clock_input(0, p75) == 0 &&
          hos_rate_clock_input(14, p75) == 14 && hos_rate_clock_input(121, p75) == 121 &&
          hos_rate_clock_input(UINT32_MAX, p75) == UINT32_MAX);
    CHECK("default cannot publish", !hos_rate_committed_nominal_state(60, 60));
    CHECK("invalid policy cannot publish", !hos_rate_committed_nominal_state(121, 60));
    CHECK("invalid committed fps cannot publish", !hos_rate_committed_nominal_state(75, 14) &&
          !hos_rate_committed_nominal_state(75, 76));
    const uint32_t invalid[] = {60u << 8 | 60u, 121u << 8 | 60u, 75u << 8 | 14u,
                               75u << 8 | 76u, 75u << 8, UINT32_MAX};
    for (unsigned i = 0; i < sizeof invalid / sizeof invalid[0]; i++)
        CHECK("malformed publication preserves original", hos_rate_clock_input(60, invalid[i]) == 60);
    CHECK("high policy accepts committed fps55", hos_rate_clock_input(60, hos_rate_committed_nominal_state(75, 55)) == 55);
    CHECK("90 policy accepts committed fps55", hos_rate_clock_input(60, hos_rate_committed_nominal_state(90, 55)) == 55);

    Model m = {0};
    int64_t now = 1000000000LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("first sample has no committed publication", m.state == 0);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("75 ready step publishes actual nominal", m.state == p75);
    frame(&m, now + 1000000LL, 0, 1, 75, 0, 60);
    CHECK("missing module clears publication", !m.state);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("subsequent successful commit restores publication", m.state == p75);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 99);
    CHECK("unwritable guest slot clears publication", !m.state);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("valid slot restores publication", m.state == p75);
    now += 300000000LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("stall clears publication", !m.state);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("post-stall success recovers", m.state == p75);
    now += 16666667LL;
    frame(&m, now, 1, 1, 75, 1, 60);
    CHECK("movie transition clears publication before default step", !m.state);
    now += 16666667LL;
    frame(&m, now, 1, 1, 75, 1, 60);
    CHECK("movie commit stays inactive", !m.state && hos_rate_clock_input(60, m.state) == 60);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("movie exit waits for new committed epoch", !m.state);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("movie exit resumes75", m.state == p75);
    now += 13333333LL;
    frame(&m, now, 1, 0, 75, 0, 60);
    CHECK("per-frame mode clears publication", !m.state);
    now += 13333333LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("mode resume waits for fresh commit", !m.state);
    now += 18181818LL;
    frame(&m, now, 1, 1, 75, 0, 60);
    CHECK("slower55 actual committed step retained under75 policy", m.state == hos_rate_committed_nominal_state(75, 55));
    now += 11111111LL;
    frame(&m, now, 1, 1, 90, 0, 60);
    CHECK("75-to90 rate transition clears publication", !m.state);
    now += 11111111LL;
    frame(&m, now, 1, 1, 90, 0, 60);
    CHECK("90 successful commit publishes90", hos_rate_clock_input(60, m.state) == 90);
    now += 16666667LL;
    frame(&m, now, 1, 1, 0, 0, 60);
    CHECK("default target clears publication", !m.state);
    puts("native clock input: focused policy checks passed");
    return 0;
}
