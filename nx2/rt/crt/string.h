/* (hookshot) string.h for the game module: the real names, so clang keeps treating them as builtins (inlined when the
   size is known). nx2/rt/crt/README.md */
#ifndef HOOKSHOT_STRING_H
#define HOOKSHOT_STRING_H
#include <stddef.h>
void *memcpy(void *restrict dst, const void *restrict src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
void *memset(void *dst, int v, size_t n);
int memcmp(const void *a, const void *b, size_t n);
size_t strlen(const char *s);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t n);
#endif
