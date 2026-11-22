// gpu/vk/bounded_wait.h: the start's wait for its pipeline builds ends when they are done, when the window is closed, or when
// none has finished for the stall's seconds, never later. tests/test_bounded_wait.py builds and runs it. The clock is the test's
// own: each pause is 100 ms of it, and a script says which round finishes an item.
#include "../gpu/vk/bounded_wait.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <functional>
#include <vector>

static int failed = 0;
static void check(bool ok, const char* what) {
    if (!ok) {
        failed++;
        printf("FAIL: %s\n", what);
    }
}

using Clock = std::chrono::steady_clock;

struct Run {
    bounded_wait::End end{};
    double secs = 0;              // (the test clock's time when the wait ended)
    size_t rounds = 0;            // (ticks)
    size_t last_tick = 0;         // (the done count the last tick saw)
    bool tick_saw_total = false;
};

// finish(round): how many items finish in that round (after its pause). stop_at: the round from which stopped() says so
// (0: never). A cap on rounds stops a wait that would never end, which is the bug: reported as a failure, not a hang
static Run run(size_t total, int stall_s, const std::function<size_t(size_t)>& finish, size_t stop_at = 0) {
    std::atomic<size_t> done{0};
    const Clock::time_point base{};
    Clock::duration t{};
    Run r;
    constexpr size_t kCap = 100000;   // (10,000 s of the test clock)
    bool capped = false;
    r.end = bounded_wait::wait(
        done, total, stall_s, [&] { return base + t; },
        [&](size_t d) {
            r.rounds++, r.last_tick = d;
            if (d >= total) r.tick_saw_total = true;
        },
        [&] {
            if (r.rounds >= kCap) capped = true;
            return capped || (stop_at && r.rounds >= stop_at);
        },
        [&] {
            t += std::chrono::milliseconds(100);
            done += finish(r.rounds);
        });
    r.secs = std::chrono::duration<double>(t).count();
    if (capped) r.end = bounded_wait::End::kStopped, r.secs = -1;
    return r;
}

int main() {
    using bounded_wait::End;

    {   // (nothing to wait for)
        const Run r = run(0, 30, [](size_t) { return size_t(0); });
        check(r.end == End::kDone && r.rounds == 1 && r.tick_saw_total, "nothing to build: done at once, the screen told 0 of 0");
    }
    {   // (every build finishes, one every round)
        const Run r = run(50, 30, [](size_t) { return size_t(1); });
        check(r.end == End::kDone, "builds that finish: done");
        check(r.tick_saw_total && r.last_tick == 50, "the last progress the screen gets is the whole");
    }
    {   // (AMD's seconds-long first compiles: one finishing every 20 s keeps it waiting, under a 30 s stall)
        const Run r = run(10, 30, [](size_t round) { return size_t(round % 200 == 0 ? 1 : 0); });
        check(r.end == End::kDone, "slow builds that still finish within the stall: waited for, not left");
        check(r.secs > 150, "slow builds: the wait took their time");
    }
    {   // (#1373, #1405: some builds finish, then none ever again: the start goes on 30 s after the last one)
        const Run r = run(2096, 30, [](size_t round) { return size_t(round <= 40 ? 10 : 0); });
        check(r.end == End::kStalled, "builds the driver never finishes: the start goes on (it waited for good before)");
        check(r.secs >= 4.0 + 30 - 0.11 && r.secs <= 4.0 + 30 + 0.21, "it goes on the stall's seconds after the last build ended");
        check(r.last_tick == 400, "the screen showed how far the builds got");
    }
    {   // (none ever finishes: the first build hung: the start goes on after the stall from the wait's start)
        const Run r = run(100, 30, [](size_t) { return size_t(0); });
        check(r.end == End::kStalled && r.secs >= 29.9 && r.secs <= 30.2, "a first build that never ends: on after 30 s");
    }
    {   // (RX_VK_PRECOMPILE_STALL_S=0: wait for all, however long)
        const Run r = run(3, 0, [](size_t round) { return size_t(round == 9000 ? 3 : 0); });
        check(r.end == End::kDone && r.secs >= 899, "stall 0: waits for every build, 15 minutes of none finishing too");
    }
    {   // (the window closed: the wait ends at once, the close keeps what was built)
        const Run r = run(100, 30, [](size_t) { return size_t(0); }, 5);
        check(r.end == End::kStopped && r.rounds == 5 && r.secs < 1, "the window closed: the wait ends then");
    }

    if (!failed) printf("PASS: the start's wait for its pipeline builds is bounded by their progress\n");
    return failed ? 1 : 0;
}
