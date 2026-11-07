/* (hookshot) What clang's MSVC target expects from Microsoft's C library, for the game module (build.py --part game,
   Windows), which links with -nostdlib: string.h's calls the compiler did not inline, forwarded to the runtime
   (host/module_crt.c); _fltused, which every object using floating point references; and __chkstk, the stack probe
   of frames over a page (the giant lifted functions), as compiler-rt's: it touches each page down to rsp - rax and
   leaves rsp alone. On Linux the module binds these to the process's libc and none of this is built. */
#include <stddef.h>

void *hookshot_crt_memcpy(void *dst, const void *src, size_t n);
void *hookshot_crt_memmove(void *dst, const void *src, size_t n);
void *hookshot_crt_memset(void *dst, int v, size_t n);
int hookshot_crt_memcmp(const void *a, const void *b, size_t n);
size_t hookshot_crt_strlen(const char *s);
int hookshot_crt_strcmp(const char *a, const char *b);
int hookshot_crt_strncmp(const char *a, const char *b, size_t n);

void *memcpy(void *dst, const void *src, size_t n) { return hookshot_crt_memcpy(dst, src, n); }
void *memmove(void *dst, const void *src, size_t n) { return hookshot_crt_memmove(dst, src, n); }
void *memset(void *dst, int v, size_t n) { return hookshot_crt_memset(dst, v, n); }
int memcmp(const void *a, const void *b, size_t n) { return hookshot_crt_memcmp(a, b, n); }
size_t strlen(const char *s) { return hookshot_crt_strlen(s); }
int strcmp(const char *a, const char *b) { return hookshot_crt_strcmp(a, b); }
int strncmp(const char *a, const char *b, size_t n) { return hookshot_crt_strncmp(a, b, n); }

int _fltused = 0x9875;

__asm__(
    ".text\n"
    ".globl __chkstk\n"
    ".p2align 4\n"
    "__chkstk:\n"
    "    pushq %rcx\n"
    "    pushq %rax\n"
    "    cmpq $0x1000, %rax\n"
    "    leaq 24(%rsp), %rcx\n"
    "    jb 1f\n"
    "2:\n"
    "    subq $0x1000, %rcx\n"
    "    testq %rcx, (%rcx)\n"
    "    subq $0x1000, %rax\n"
    "    cmpq $0x1000, %rax\n"
    "    ja 2b\n"
    "1:\n"
    "    subq %rax, %rcx\n"
    "    testq %rcx, (%rcx)\n"
    "    popq %rax\n"
    "    popq %rcx\n"
    "    retq\n");
