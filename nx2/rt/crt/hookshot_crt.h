/* (hookshot) The game module's C library: the prebuilt runtime's (host/module_crt.c), by these names. */
#ifndef HOOKSHOT_CRT_H
#define HOOKSHOT_CRT_H
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>

typedef struct hookshot_FILE FILE;
FILE *hookshot_crt_stream(int fd);   /* 1 stdout, 2 stderr */
FILE *hookshot_crt_fopen(const char *path, const char *mode);
int hookshot_crt_fclose(FILE *f);
int hookshot_crt_fflush(FILE *f);
int hookshot_crt_vfprintf(FILE *f, const char *fmt, va_list ap);
int hookshot_crt_vsnprintf(char *dst, size_t cap, const char *fmt, va_list ap);
size_t hookshot_crt_fwrite(const void *p, size_t size, size_t n, FILE *f);
int hookshot_crt_fputs(const char *s, FILE *f);
char *hookshot_crt_getenv(const char *name);
void *hookshot_crt_malloc(size_t n);
void *hookshot_crt_calloc(size_t n, size_t size);
void *hookshot_crt_realloc(void *p, size_t n);
void hookshot_crt_free(void *p);
_Noreturn void hookshot_crt_abort(void);
_Noreturn void hookshot_crt_exit(int code);
uint64_t hookshot_crt_monotonic_ns(void);
#endif
