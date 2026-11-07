/* (hookshot) setjmp.h for the game module: clang's intrin.h (MSVC targets) needs jmp_buf for a declaration. The
   lifted code's setjmp is the runtime's (host/hos_setjmp.c). Microsoft's layout: 16 16-byte slots. */
#ifndef HOOKSHOT_SETJMP_H
#define HOOKSHOT_SETJMP_H
typedef struct __attribute__((aligned(16))) { unsigned long long Part[2]; } SETJMP_FLOAT128;
typedef SETJMP_FLOAT128 jmp_buf[16];
#endif
