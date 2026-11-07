/* (hookshot) time.h for the game module: CLOCK_MONOTONIC only (a64_rt.c's profile timer and counter). */
#ifndef HOOKSHOT_TIME_H
#define HOOKSHOT_TIME_H
#include "hookshot_crt.h"
struct timespec { long long tv_sec; long tv_nsec; };
#define CLOCK_MONOTONIC 1
static inline int hookshot_clock_gettime(int clock, struct timespec *ts) {
    (void)clock;
    uint64_t ns = hookshot_crt_monotonic_ns();
    ts->tv_sec = (long long)(ns / 1000000000u);
    ts->tv_nsec = (long)(ns % 1000000000u);
    return 0;
}
#define clock_gettime hookshot_clock_gettime
#endif
