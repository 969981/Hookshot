// (hookshot) RX_VK_TEST_DRIVER: a GPU driver that stops answering, faults or is slow, at one pipeline build, on any GPU. A
// test, off unless set: the preparation's watchdog (vk_main.cpp prepare_watchdog), the driver-fault guard (vk_draw.cpp
// guarded_graphics) and the Builder's handling of both can be watched without a driver that does it.
//   hang@<n>        the n-th graphics pipeline build of the run never returns (a driver that stopped answering)
//   fault@<n>       the n-th faults inside the guarded driver call, as a driver's compiler that crashes
//   slow:<ms>@<n>   the n-th is held <ms> first (at most 600000), as one pipeline the driver takes minutes over
// The builds are counted from 1 across every thread, in the order they reach the driver. Anything else is ignored, with a
// warning (bad).
#pragma once

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace test_driver {

enum class Kind { kNone, kHang, kFault, kSlow };
struct Plan {
    Kind kind = Kind::kNone;
    int64_t at = 0;   // (the build it acts at, from 1)
    int ms = 0;       // (kSlow's)
};

inline Plan parse(const char* e, bool* bad) {
    Plan p;
    if (bad) *bad = false;
    if (!e || !*e) return p;
    const char* at = strchr(e, '@');
    char* end = nullptr;
    const long long n = at ? strtoll(at + 1, &end, 10) : 0;
    const bool n_ok = at && end && !*end && n > 0;
    if (n_ok && !strncmp(e, "hang@", 5)) p.kind = Kind::kHang;
    else if (n_ok && !strncmp(e, "fault@", 6)) p.kind = Kind::kFault;
    else if (n_ok && !strncmp(e, "slow:", 5)) {
        const long ms = strtol(e + 5, &end, 10);
        if (end == at && ms > 0) p.kind = Kind::kSlow, p.ms = ms > 600000 ? 600000 : (int)ms;
    }
    if (p.kind == Kind::kNone) {
        if (bad) *bad = true;
        return p;
    }
    p.at = n;
    return p;
}

/// The action for the next build, counted: kNone for every build but the plan's one
inline Kind next(const Plan& p, std::atomic<int64_t>& count) {
    if (p.kind == Kind::kNone) return Kind::kNone;
    return count.fetch_add(1, std::memory_order_relaxed) + 1 == p.at ? p.kind : Kind::kNone;
}

}  // namespace test_driver
