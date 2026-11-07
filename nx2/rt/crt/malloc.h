/* (hookshot) malloc.h for the game module: clang's mm_malloc.h includes it for _mm_malloc, which nothing calls. */
#ifndef HOOKSHOT_MALLOC_H
#define HOOKSHOT_MALLOC_H
#include <stdlib.h>
void *_aligned_malloc(size_t size, size_t align);
void _aligned_free(void *p);
#endif
