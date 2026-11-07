/* (hookshot) stdlib for the game module: nx2/rt/crt/README.md */
#ifndef HOOKSHOT_STDLIB_H
#define HOOKSHOT_STDLIB_H
#include "hookshot_crt.h"
#define getenv hookshot_crt_getenv
#define malloc hookshot_crt_malloc
#define calloc hookshot_crt_calloc
#define realloc hookshot_crt_realloc
#define free hookshot_crt_free
#define abort hookshot_crt_abort
#define exit hookshot_crt_exit
#endif
