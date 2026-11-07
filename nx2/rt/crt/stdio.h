/* (hookshot) stdio for the game module: nx2/rt/crt/README.md */
#ifndef HOOKSHOT_STDIO_H
#define HOOKSHOT_STDIO_H
#include "hookshot_crt.h"
#define stdout hookshot_crt_stream(1)
#define stderr hookshot_crt_stream(2)
#define fopen hookshot_crt_fopen
#define fclose hookshot_crt_fclose
#define fflush hookshot_crt_fflush
#define fwrite hookshot_crt_fwrite
#define fputs hookshot_crt_fputs
#define vfprintf hookshot_crt_vfprintf
#define vsnprintf hookshot_crt_vsnprintf
static inline int hookshot_fprintf(FILE *f, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = hookshot_crt_vfprintf(f, fmt, ap);
    va_end(ap);
    return n;
}
static inline int hookshot_snprintf(char *dst, size_t cap, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = hookshot_crt_vsnprintf(dst, cap, fmt, ap);
    va_end(ap);
    return n;
}
#define fprintf hookshot_fprintf
#define printf(...) hookshot_fprintf(stdout, __VA_ARGS__)
#define snprintf hookshot_snprintf
#endif
