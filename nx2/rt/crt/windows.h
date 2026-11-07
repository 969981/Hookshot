/* (hookshot) windows.h for the game module: the two clocks a64-kit's rt/a64_rt.c reads on Windows. */
#ifndef HOOKSHOT_WINDOWS_H
#define HOOKSHOT_WINDOWS_H
#include "hookshot_crt.h"
typedef union { struct { uint32_t LowPart; int32_t HighPart; } u; long long QuadPart; } LARGE_INTEGER;
static inline uint64_t GetTickCount64(void) { return hookshot_crt_monotonic_ns() / 1000000u; }
static inline int QueryPerformanceFrequency(LARGE_INTEGER *f) { f->QuadPart = 1000000000LL; return 1; }
static inline int QueryPerformanceCounter(LARGE_INTEGER *t) { t->QuadPart = (long long)hookshot_crt_monotonic_ns(); return 1; }
#endif
