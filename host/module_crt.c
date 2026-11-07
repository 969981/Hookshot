/* (hookshot) The C library of the game module (build.py --part game: nx2/rt/crt/hookshot_crt.h, nx2/rt/module_crt.c),
   which is built on the player's PC with no Windows SDK or libc headers: the runtime's own C library, by the names
   the module calls. In the one-executable build nothing calls these. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <windows.h>
#else
#include <time.h>
#endif

FILE *hookshot_crt_stream(int fd) { return fd == 1 ? stdout : stderr; }
FILE *hookshot_crt_fopen(const char *path, const char *mode) { return fopen(path, mode); }
int hookshot_crt_fclose(FILE *f) { return fclose(f); }
int hookshot_crt_fflush(FILE *f) { return fflush(f); }
int hookshot_crt_vfprintf(FILE *f, const char *fmt, va_list ap) { return vfprintf(f, fmt, ap); }
int hookshot_crt_vsnprintf(char *dst, size_t cap, const char *fmt, va_list ap) { return vsnprintf(dst, cap, fmt, ap); }
size_t hookshot_crt_fwrite(const void *p, size_t size, size_t n, FILE *f) { return fwrite(p, size, n, f); }
int hookshot_crt_fputs(const char *s, FILE *f) { return fputs(s, f); }
char *hookshot_crt_getenv(const char *name) { return getenv(name); }
void *hookshot_crt_malloc(size_t n) { return malloc(n); }
void *hookshot_crt_calloc(size_t n, size_t size) { return calloc(n, size); }
void *hookshot_crt_realloc(void *p, size_t n) { return realloc(p, n); }
void hookshot_crt_free(void *p) { free(p); }
_Noreturn void hookshot_crt_abort(void) { abort(); }
_Noreturn void hookshot_crt_exit(int code) { exit(code); }
void *hookshot_crt_memcpy(void *dst, const void *src, size_t n) { return memcpy(dst, src, n); }
void *hookshot_crt_memmove(void *dst, const void *src, size_t n) { return memmove(dst, src, n); }
void *hookshot_crt_memset(void *dst, int v, size_t n) { return memset(dst, v, n); }
int hookshot_crt_memcmp(const void *a, const void *b, size_t n) { return memcmp(a, b, n); }
size_t hookshot_crt_strlen(const char *s) { return strlen(s); }
int hookshot_crt_strcmp(const char *a, const char *b) { return strcmp(a, b); }
int hookshot_crt_strncmp(const char *a, const char *b, size_t n) { return strncmp(a, b, n); }

uint64_t hookshot_crt_monotonic_ns(void) {
#if defined(_WIN32)
    static uint64_t freq;
    LARGE_INTEGER t;
    if (!freq) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        freq = (uint64_t)f.QuadPart;
    }
    QueryPerformanceCounter(&t);
    const uint64_t n = (uint64_t)t.QuadPart;   /* (no 128-bit division: __udivti3 is not in Microsoft's libraries) */
    return n / freq * 1000000000u + n % freq * 1000000000u / freq;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
#endif
}
