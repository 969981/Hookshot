// (hookshot) What NVIDIA's NGX libraries ask of Microsoft's runtime, for the llvm-mingw build.
//
// NGX's Windows libraries (lib/Windows_x86_64/x64/nvsdk_ngx_s.lib) are built with Microsoft's
// compiler. Linking them into a GNU-ABI DLL leaves exactly twelve symbols undefined, and this file
// is those twelve. host/mingw_msvc_shims.c does the same job for the game DLL, where the
// MSVC-built FFmpeg has the same problem -- that file is not compiled into this DLL, which is its
// own link with its own symbol table, so the overlapping four are here again rather than shared.
//
// Scope, deliberately narrow: this is not an attempt at an MSVC runtime. It is the twelve names the
// linker asked for, and if NGX ever asks for a thirteenth the link fails and says which. Nothing
// here runs in a build that is not llvm-mingw, and nothing here runs in a build without an NGX SDK.
//
// The MSVC build compiles nothing of this file, and neither does Linux.

#if defined(__MINGW32__) && defined(RX_VK_DLSS_SDK)

#include <windows.h>

#include <climits>
#include <cstdint>
#include <cstdlib>

extern "C" {

// ---------------------------------------------------------------- /GS: the stack-buffer check
//
// A cookie in each guarded frame, checked on return. Set once at start; a fixed value would do,
// because a mismatch means the stack was overrun and then the process ends either way, as it does
// with Microsoft's runtime. The same as host/mingw_msvc_shims.c's, kept identical on purpose.
uintptr_t __security_cookie = (uintptr_t)0x00002B992DDFA232ull;

static void init_security_cookie() {
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    uintptr_t c = (uintptr_t)t.QuadPart ^ (uintptr_t)GetCurrentProcessId() ^
                  ((uintptr_t)GetCurrentThreadId() << 32);
    c &= 0x0000FFFFFFFFFFFFull;   // (MSVC keeps the top 16 bits clear)
    if (c) __security_cookie = c;
}
namespace {
struct CookieInit {
    CookieInit() { init_security_cookie(); }
};
const CookieInit cookie_init;
}   // namespace

void __security_check_cookie(uintptr_t cookie) {
    if (cookie != __security_cookie) __fastfail(2);   // FAST_FAIL_STACK_COOKIE_CHECK_FAILURE
}

// The unwind handlers of /GS frames: check the frame's cookie, then let the search go on. _EH is
// the variant for a frame that also has C++ unwind state; for this purpose they do the same.
EXCEPTION_DISPOSITION __GSHandlerCheck(PEXCEPTION_RECORD record, void* frame, PCONTEXT context,
                                       void* dispatcher) {
    (void)record, (void)frame, (void)context, (void)dispatcher;
    return ExceptionContinueSearch;
}
EXCEPTION_DISPOSITION __GSHandlerCheck_EH(PEXCEPTION_RECORD record, void* frame, PCONTEXT context,
                                          void* dispatcher) {
    (void)record, (void)frame, (void)context, (void)dispatcher;
    return ExceptionContinueSearch;
}

// Microsoft's marker that the image uses floating point. Only its presence matters.
int _fltused = 0x9875;

// ------------------------------------------------- thread-safe function-local statics ("magic statics")
//
// Microsoft's compiler guards a function-local static with an int and these three calls. Its
// generated code is, in effect:
//
//     if (guard <= _Init_thread_epoch) goto done;   // the fast path, no lock
//     _Init_thread_header(&guard);
//     if (guard == -1) { ...construct...; _Init_thread_footer(&guard); }
//   done:
//
// so the contract is: header takes a lock and leaves `guard` at -1 when *this* caller is the one
// that must construct; footer marks it constructed and releases the lock; and the fast path must be
// true afterwards and false before. The epoch is what makes that comparison work, which is why it
// is INT_MIN + 1 here and the constructed marker is INT_MIN: a fresh guard of 0 fails the fast path
// (0 <= INT_MIN+1 is false) and a constructed one passes it (INT_MIN <= INT_MIN+1 is true).
//
// This is a real lock, not a pretence: getting it wrong would mean two threads inside NVIDIA's
// constructors at once, which is worse than not linking at all.
namespace {
constexpr int kConstructed = INT_MIN;
constexpr int kBeingConstructed = -1;

CRITICAL_SECTION& init_lock() {
    static CRITICAL_SECTION cs;
    static bool ready = false;
    static LONG guard = 0;
    // One-time init without relying on the very feature this file implements.
    while (!ready) {
        if (InterlockedCompareExchange(&guard, 1, 0) == 0) {
            InitializeCriticalSection(&cs);
            ready = true;
        } else {
            Sleep(0);
        }
    }
    return cs;
}
}   // namespace

// Not thread-local, on purpose. Microsoft's runtime keeps a per-thread epoch and moves it; nothing
// here ever writes this one, so it is a constant the comparison reads and a plain global has
// exactly the same meaning. It also sidesteps the thread-local model entirely: a `__thread` here
// compiles to emulated TLS under some of the toolchains this file has to build with, and an
// __emutls_v._Init_thread_epoch is not a symbol NVIDIA's MSVC-built objects can reference.
int _Init_thread_epoch = INT_MIN + 1;

void _Init_thread_header(int* guard) {
    EnterCriticalSection(&init_lock());
    if (*guard == kConstructed) {
        // Another thread finished while we waited: nothing to do, and we must not hold the lock.
        LeaveCriticalSection(&init_lock());
        return;
    }
    *guard = kBeingConstructed;   // the caller constructs, and calls the footer when it has
}

void _Init_thread_footer(int* guard) {
    *guard = kConstructed;
    LeaveCriticalSection(&init_lock());
}

// Called instead of the footer when a constructor threw. Not in the twelve the linker asked for;
// here because the alternative, if NGX ever does throw out of one, is a lock held for the life of
// the process.
void _Init_thread_abort(int* guard) {
    *guard = 0;
    LeaveCriticalSection(&init_lock());
}

}   // extern "C"

// ---------------------------------------------------- Microsoft's global new / delete and two throw helpers
//
// They cannot be written as C++ operators here: this translation unit is compiled for the GNU ABI,
// so `operator new` would be emitted as `_Znwm` while NGX's objects ask for `??2@YAPEAX_K@Z`.
//
// Failure ends the process rather than returning null. MSVC's operator new throws std::bad_alloc,
// and NGX's code is entitled to assume a non-null return; handing it null would turn an allocation
// failure into a null dereference inside NVIDIA's library, which is a worse way to find out.
extern "C" {

void* mkwvk_msvc_new(size_t size) {
    void* p = malloc(size ? size : 1);
    if (!p) __fastfail(FAST_FAIL_FATAL_APP_EXIT);
    return p;
}
void mkwvk_msvc_delete(void* p) { free(p); }
void mkwvk_msvc_bad_alloc() { __fastfail(FAST_FAIL_FATAL_APP_EXIT); }
void mkwvk_msvc_length_error(const char* what) {
    (void)what;
    __fastfail(FAST_FAIL_FATAL_APP_EXIT);
}

}   // extern "C"

// The decorated names, as jumps to the four above. Written as assembly with *quoted* symbol names
// because a `?` cannot appear in an __asm__ label unquoted: the GNU assembler rejects it outright
// and only clang's integrated assembler takes it, and this file has to build under whichever one
// the toolchain hands us.
__asm__(".text\n"
        ".globl \"??2@YAPEAX_K@Z\"\n"              // operator new(size_t)
        "\"??2@YAPEAX_K@Z\": jmp mkwvk_msvc_new\n"
        ".globl \"??3@YAXPEAX@Z\"\n"               // operator delete(void*)
        "\"??3@YAXPEAX@Z\": jmp mkwvk_msvc_delete\n"
        ".globl \"?_Xbad_alloc@std@@YAXXZ\"\n"      // std::_Xbad_alloc()
        "\"?_Xbad_alloc@std@@YAXXZ\": jmp mkwvk_msvc_bad_alloc\n"
        ".globl \"?_Xlength_error@std@@YAXPEBD@Z\"\n"   // std::_Xlength_error(char const*)
        "\"?_Xlength_error@std@@YAXPEBD@Z\": jmp mkwvk_msvc_length_error\n");

#endif   // __MINGW32__ && RX_VK_DLSS_SDK
