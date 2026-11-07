/* (hookshot) The Win32 that host/ and gpu/vk use, on Linux: on the include path of the Linux runtime and Vulkan back
   end only (nx2/build.py --target linux), so the shared files keep their one Windows-API source. Small calls are
   here, inline (locks, atomics, the clock); the ones with state live in host/linux/win32.c (threads, events, timers,
   files, directory listings, fibers on ucontext, vectored exception handlers on signals, waveOut on SDL's audio),
   and the window's (keyboard, foreground) in host/linux/display_sdl.c. The Vulkan back end, a shared object loaded
   by the runtime, binds the same win32.c functions from the runtime (linked -rdynamic).

   Paths: GetModuleFileNameA answers the native path ('/'); every path these calls take may use '\' as well (nn_fs.c
   builds them so), which they read as '/'. Only what this project calls is here, with Windows' meanings where they
   matter to it. */
#ifndef HOOKSHOT_LINUX_WINDOWS_H
#define HOOKSHOT_LINUX_WINDOWS_H

#include <errno.h>
#include <limits.h>
#include <sched.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>
#include <wchar.h>
#include <emmintrin.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- types ---- */
#define WINAPI
#define CALLBACK
#define APIENTRY
#define WINAPIV
typedef int BOOL;
typedef uint8_t BYTE, BOOLEAN;
typedef uint16_t WORD;
typedef uint32_t DWORD, ULONG, UINT32;
typedef int32_t LONG, HRESULT, INT32;
typedef unsigned int UINT;
typedef int64_t LONG64, LONGLONG, INT64;
typedef uint64_t ULONG64, ULONGLONG, DWORD64, UINT64;
typedef size_t SIZE_T;
typedef intptr_t LONG_PTR, INT_PTR, SSIZE_T;
typedef uintptr_t ULONG_PTR, DWORD_PTR, UINT_PTR;
typedef char CHAR;
typedef wchar_t WCHAR;
typedef void *PVOID, *LPVOID, *HANDLE, *HMODULE, *HINSTANCE, *HWND;
typedef const void *LPCVOID;
typedef char *LPSTR;
typedef const char *LPCSTR;
typedef wchar_t *LPWSTR;
typedef const wchar_t *LPCWSTR, *PCWSTR;
typedef DWORD *LPDWORD;
typedef void *(*FARPROC)(void);
typedef DWORD(WINAPI *LPTHREAD_START_ROUTINE)(LPVOID);
typedef void(WINAPI *LPFIBER_START_ROUTINE)(LPVOID);
typedef struct { DWORD dwLowDateTime, dwHighDateTime; } FILETIME;
typedef union {
    struct { DWORD LowPart; LONG HighPart; };
    struct { DWORD LowPart; LONG HighPart; } u;
    LONGLONG QuadPart;
} LARGE_INTEGER;
typedef struct { DWORD nLength; void *lpSecurityDescriptor; BOOL bInheritHandle; } SECURITY_ATTRIBUTES;
typedef struct { LONG left, top, right, bottom; } RECT;
#define __int64 long long

#ifndef TRUE
#define TRUE 1
#define FALSE 0
#endif
#define MAX_PATH 260
#define INFINITE 0xFFFFFFFFu
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define MAKEINTRESOURCEA(i) ((LPSTR)(uintptr_t)(WORD)(i))
#define LOWORD(l) ((WORD)((uintptr_t)(l) & 0xffff))
#define HIWORD(l) ((WORD)(((uintptr_t)(l) >> 16) & 0xffff))
#define SUCCEEDED(hr) ((HRESULT)(hr) >= 0)
#define FAILED(hr) ((HRESULT)(hr) < 0)
#define S_OK 0

/* ---- errors ---- */
#define ERROR_SUCCESS 0
#define ERROR_FILE_NOT_FOUND 2
#define ERROR_PATH_NOT_FOUND 3
#define ERROR_ACCESS_DENIED 5
#define ERROR_INVALID_HANDLE 6
#define ERROR_NOT_ENOUGH_MEMORY 8
#define ERROR_FILE_EXISTS 80
#define ERROR_INVALID_PARAMETER 87
#define ERROR_INSUFFICIENT_BUFFER 122
#define ERROR_ALREADY_EXISTS 183
#define ERROR_NO_MORE_FILES 18
#define ERROR_TIMEOUT 1460
DWORD GetLastError(void);
void SetLastError(DWORD e);

/* ---- atomics (Windows' Interlocked*: full barriers, the new value returned unless named Exchange) ---- */
static inline LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedExchange(volatile LONG *p, LONG v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedExchangeAdd(volatile LONG *p, LONG v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedAdd(volatile LONG *p, LONG v) { return __atomic_add_fetch(p, v, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedOr(volatile LONG *p, LONG v) { return __atomic_fetch_or(p, v, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedAnd(volatile LONG *p, LONG v) { return __atomic_fetch_and(p, v, __ATOMIC_SEQ_CST); }
static inline LONG InterlockedCompareExchange(volatile LONG *p, LONG v, LONG cmp) {
    __atomic_compare_exchange_n(p, &cmp, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp;
}
static inline LONG64 InterlockedIncrement64(volatile LONG64 *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static inline LONG64 InterlockedDecrement64(volatile LONG64 *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
static inline LONG64 InterlockedExchange64(volatile LONG64 *p, LONG64 v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
static inline LONG64 InterlockedExchangeAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
static inline LONG64 InterlockedAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_add_fetch(p, v, __ATOMIC_SEQ_CST); }
static inline LONG64 InterlockedCompareExchange64(volatile LONG64 *p, LONG64 v, LONG64 cmp) {
    __atomic_compare_exchange_n(p, &cmp, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp;
}
static inline void *InterlockedExchangePointer(void *volatile *p, void *v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
static inline void *InterlockedCompareExchangePointer(void *volatile *p, void *v, void *cmp) {
    __atomic_compare_exchange_n(p, &cmp, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cmp;
}
#define YieldProcessor() _mm_pause()
#define MemoryBarrier() __atomic_thread_fence(__ATOMIC_SEQ_CST)

/* ---- locks: slim reader/writer locks and condition variables on futexes (one word each, zero-initialized as
   Windows' are), critical sections recursive ---- */
typedef struct { uint32_t state, waiters; } SRWLOCK, *PSRWLOCK;
typedef struct { uint32_t seq, pad; } CONDITION_VARIABLE, *PCONDITION_VARIABLE;
#define SRWLOCK_INIT {0, 0}
#define CONDITION_VARIABLE_INIT {0, 0}
#define CONDITION_VARIABLE_LOCKMODE_SHARED 0x1
void win32_srw_wait(SRWLOCK *l, uint32_t seen);
void win32_srw_wake(SRWLOCK *l);
#define WIN32_SRW_WRITER 0x80000000u
static inline void InitializeSRWLock(PSRWLOCK l) { l->state = l->waiters = 0; }
static inline BOOL TryAcquireSRWLockExclusive(PSRWLOCK l) {
    uint32_t z = 0;
    return __atomic_compare_exchange_n(&l->state, &z, WIN32_SRW_WRITER, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}
static inline BOOL TryAcquireSRWLockShared(PSRWLOCK l) {
    uint32_t s = __atomic_load_n(&l->state, __ATOMIC_RELAXED);
    while (!(s & WIN32_SRW_WRITER))
        if (__atomic_compare_exchange_n(&l->state, &s, s + 1, 1, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return TRUE;
    return FALSE;
}
void win32_srw_lock_slow(PSRWLOCK l, int shared);
static inline void AcquireSRWLockExclusive(PSRWLOCK l) { if (!TryAcquireSRWLockExclusive(l)) win32_srw_lock_slow(l, 0); }
static inline void AcquireSRWLockShared(PSRWLOCK l) { if (!TryAcquireSRWLockShared(l)) win32_srw_lock_slow(l, 1); }
static inline void ReleaseSRWLockExclusive(PSRWLOCK l) {
    __atomic_store_n(&l->state, 0, __ATOMIC_SEQ_CST);
    if (__atomic_load_n(&l->waiters, __ATOMIC_SEQ_CST)) win32_srw_wake(l);
}
static inline void ReleaseSRWLockShared(PSRWLOCK l) {
    if (__atomic_sub_fetch(&l->state, 1, __ATOMIC_SEQ_CST) == 0 && __atomic_load_n(&l->waiters, __ATOMIC_SEQ_CST))
        win32_srw_wake(l);
}
static inline void InitializeConditionVariable(PCONDITION_VARIABLE cv) { cv->seq = cv->pad = 0; }
void WakeConditionVariable(PCONDITION_VARIABLE cv);
void WakeAllConditionVariable(PCONDITION_VARIABLE cv);
BOOL SleepConditionVariableSRW(PCONDITION_VARIABLE cv, PSRWLOCK l, DWORD ms, ULONG flags);

typedef struct { SRWLOCK lock; long owner; int depth; } CRITICAL_SECTION, *LPCRITICAL_SECTION;
void InitializeCriticalSection(LPCRITICAL_SECTION cs);
static inline BOOL InitializeCriticalSectionAndSpinCount(LPCRITICAL_SECTION cs, DWORD spin) {
    (void)spin;
    InitializeCriticalSection(cs);
    return TRUE;
}
void EnterCriticalSection(LPCRITICAL_SECTION cs);
BOOL TryEnterCriticalSection(LPCRITICAL_SECTION cs);
void LeaveCriticalSection(LPCRITICAL_SECTION cs);
static inline void DeleteCriticalSection(LPCRITICAL_SECTION cs) { (void)cs; }

/* ---- the clock ---- */
static inline uint64_t win32_now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
}
static inline BOOL QueryPerformanceCounter(LARGE_INTEGER *c) { c->QuadPart = (LONGLONG)win32_now_ns(); return TRUE; }
static inline BOOL QueryPerformanceFrequency(LARGE_INTEGER *f) { f->QuadPart = 1000000000; return TRUE; }
static inline ULONGLONG GetTickCount64(void) { return win32_now_ns() / 1000000u; }
static inline DWORD GetTickCount(void) { return (DWORD)(win32_now_ns() / 1000000u); }
static inline void Sleep(DWORD ms) {
    if (!ms) { sched_yield(); return; }
    struct timespec t = {(time_t)(ms / 1000), (long)(ms % 1000) * 1000000L};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}
static inline BOOL SwitchToThread(void) { return sched_yield() == 0; }
static inline UINT timeBeginPeriod(UINT ms) { (void)ms; return 0; }
static inline UINT timeEndPeriod(UINT ms) { (void)ms; return 0; }

/* ---- handles, threads, events, waitable timers (win32.c) ---- */
#define WAIT_OBJECT_0 0u
#define WAIT_TIMEOUT 258u
#define WAIT_FAILED 0xFFFFFFFFu
#define STACK_SIZE_PARAM_IS_A_RESERVATION 0x00010000
#define CREATE_SUSPENDED 0x4
#define THREAD_PRIORITY_LOWEST -2
#define THREAD_PRIORITY_BELOW_NORMAL -1
#define THREAD_PRIORITY_NORMAL 0
#define THREAD_PRIORITY_ABOVE_NORMAL 1
#define THREAD_PRIORITY_HIGHEST 2
#define THREAD_PRIORITY_TIME_CRITICAL 15
#define DUPLICATE_SAME_ACCESS 0x2
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x2
#define TIMER_ALL_ACCESS 0x1F0003
#define EVENT_ALL_ACCESS 0x1F0003
HANDLE CreateThread(SECURITY_ATTRIBUTES *sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags, LPDWORD id);
void ExitThread(DWORD code);
HANDLE GetCurrentThread(void);
HANDLE GetCurrentProcess(void);
DWORD GetCurrentThreadId(void);
DWORD GetCurrentProcessId(void);
DWORD GetThreadId(HANDLE h);
BOOL DuplicateHandle(HANDLE sp, HANDLE h, HANDLE tp, HANDLE *out, DWORD access, BOOL inherit, DWORD options);
BOOL CloseHandle(HANDLE h);
DWORD WaitForSingleObject(HANDLE h, DWORD ms);
HANDLE CreateEventA(SECURITY_ATTRIBUTES *sa, BOOL manual, BOOL initial, LPCSTR name);
BOOL SetEvent(HANDLE h);
BOOL ResetEvent(HANDLE h);
HANDLE CreateSemaphoreA(SECURITY_ATTRIBUTES *sa, LONG initial, LONG max, LPCSTR name);
BOOL ReleaseSemaphore(HANDLE h, LONG n, LONG *prev);
HANDLE CreateWaitableTimerExW(SECURITY_ATTRIBUTES *sa, LPCWSTR name, DWORD flags, DWORD access);
BOOL SetWaitableTimerEx(HANDLE h, const LARGE_INTEGER *due, LONG period, void *fn, void *arg, void *wake, ULONG delay);
BOOL SetThreadPriority(HANDLE h, int prio);
DWORD_PTR SetThreadAffinityMask(HANDLE h, DWORD_PTR mask);
BOOL GetProcessAffinityMask(HANDLE h, DWORD_PTR *proc, DWORD_PTR *sys);
HRESULT SetThreadDescription(HANDLE h, PCWSTR name);
BOOL GetThreadTimes(HANDLE h, FILETIME *created, FILETIME *exited, FILETIME *kernel, FILETIME *user);
__attribute__((noreturn)) void ExitProcess(UINT code);

typedef enum { RelationProcessorCore, RelationNumaNode, RelationCache, RelationProcessorPackage } LOGICAL_PROCESSOR_RELATIONSHIP;
typedef struct {
    ULONG_PTR ProcessorMask;
    LOGICAL_PROCESSOR_RELATIONSHIP Relationship;
    union { BYTE Flags; ULONGLONG Reserved[2]; };
} SYSTEM_LOGICAL_PROCESSOR_INFORMATION;
BOOL GetLogicalProcessorInformation(SYSTEM_LOGICAL_PROCESSOR_INFORMATION *out, DWORD *len);

/* ---- fibers (ucontext) ---- */
#define FIBER_FLAG_FLOAT_SWITCH 0x1
LPVOID CreateFiberEx(SIZE_T commit, SIZE_T reserve, DWORD flags, LPFIBER_START_ROUTINE fn, LPVOID arg);
LPVOID ConvertThreadToFiberEx(LPVOID arg, DWORD flags);
void SwitchToFiber(LPVOID fiber);
void DeleteFiber(LPVOID fiber);

/* ---- virtual memory ---- */
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_DECOMMIT 0x4000
#define MEM_RELEASE 0x8000
#define MEM_FREE 0x10000
#define MEM_PRIVATE 0x20000
#define MEM_MAPPED 0x40000
#define MEM_RESET 0x80000
#define MEM_IMAGE 0x1000000
#define MEM_WRITE_WATCH 0x200000
#define MEM_LARGE_PAGES 0x20000000
#define PAGE_NOACCESS 0x01
#define PAGE_READONLY 0x02
#define PAGE_READWRITE 0x04
#define PAGE_WRITECOPY 0x08
#define PAGE_EXECUTE 0x10
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_GUARD 0x100
typedef struct {
    PVOID BaseAddress, AllocationBase;
    DWORD AllocationProtect;
    WORD PartitionId;
    SIZE_T RegionSize;
    DWORD State, Protect, Type;
} MEMORY_BASIC_INFORMATION;
LPVOID VirtualAlloc(LPVOID at, SIZE_T size, DWORD type, DWORD protect);
BOOL VirtualFree(LPVOID at, SIZE_T size, DWORD type);
BOOL VirtualProtect(LPVOID at, SIZE_T size, DWORD protect, DWORD *old);
SIZE_T VirtualQuery(LPCVOID at, MEMORY_BASIC_INFORMATION *out, SIZE_T size);

/* ---- exceptions: signals seen as Windows' structured exceptions ---- */
#define EXCEPTION_ACCESS_VIOLATION 0xC0000005u
#define EXCEPTION_ILLEGAL_INSTRUCTION 0xC000001Du
#define EXCEPTION_INT_DIVIDE_BY_ZERO 0xC0000094u
#define EXCEPTION_BREAKPOINT 0x80000003u
#define EXCEPTION_SINGLE_STEP 0x80000004u
#define EXCEPTION_STACK_OVERFLOW 0xC00000FDu
#define EXCEPTION_CONTINUE_EXECUTION (-1)
#define EXCEPTION_CONTINUE_SEARCH 0
#define EXCEPTION_EXECUTE_HANDLER 1
#define EXCEPTION_MAXIMUM_PARAMETERS 15
typedef struct _EXCEPTION_RECORD {
    DWORD ExceptionCode, ExceptionFlags;
    struct _EXCEPTION_RECORD *ExceptionRecord;
    PVOID ExceptionAddress;
    DWORD NumberParameters;
    ULONG_PTR ExceptionInformation[EXCEPTION_MAXIMUM_PARAMETERS];
} EXCEPTION_RECORD;
typedef struct {   /* (the integer registers and the instruction pointer: what a handler here reads or changes) */
    DWORD ContextFlags, EFlags;
    DWORD64 Rax, Rcx, Rdx, Rbx, Rsp, Rbp, Rsi, Rdi, R8, R9, R10, R11, R12, R13, R14, R15, Rip;
} CONTEXT;
typedef struct { EXCEPTION_RECORD *ExceptionRecord; CONTEXT *ContextRecord; } EXCEPTION_POINTERS;
typedef LONG(CALLBACK *PVECTORED_EXCEPTION_HANDLER)(EXCEPTION_POINTERS *);
typedef LONG(WINAPI *LPTOP_LEVEL_EXCEPTION_FILTER)(EXCEPTION_POINTERS *);
PVOID AddVectoredExceptionHandler(ULONG first, PVECTORED_EXCEPTION_HANDLER fn);
ULONG RemoveVectoredExceptionHandler(PVOID h);
LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER fn);
/* (Linux) a copy that answers 0 instead of faulting, for what __try / __except guard on Windows */
int win32_guarded_copy(void *dst, const void *src, size_t n);

/* ---- files ---- */
#define GENERIC_READ 0x80000000u
#define GENERIC_WRITE 0x40000000u
#define FILE_SHARE_READ 0x1
#define FILE_SHARE_WRITE 0x2
#define FILE_SHARE_DELETE 0x4
#define CREATE_NEW 1
#define CREATE_ALWAYS 2
#define OPEN_EXISTING 3
#define OPEN_ALWAYS 4
#define TRUNCATE_EXISTING 5
#define FILE_ATTRIBUTE_READONLY 0x1
#define FILE_ATTRIBUTE_HIDDEN 0x2
#define FILE_ATTRIBUTE_DIRECTORY 0x10
#define FILE_ATTRIBUTE_ARCHIVE 0x20
#define FILE_ATTRIBUTE_NORMAL 0x80
#define FILE_FLAG_SEQUENTIAL_SCAN 0x08000000
#define FILE_FLAG_RANDOM_ACCESS 0x10000000
#define INVALID_FILE_ATTRIBUTES 0xFFFFFFFFu
#define INVALID_FILE_SIZE 0xFFFFFFFFu
#define FILE_BEGIN 0
#define FILE_CURRENT 1
#define FILE_END 2
#define MOVEFILE_REPLACE_EXISTING 0x1
#define STD_INPUT_HANDLE ((DWORD)-10)
#define STD_OUTPUT_HANDLE ((DWORD)-11)
#define STD_ERROR_HANDLE ((DWORD)-12)
#define FILE_TYPE_UNKNOWN 0
#define FILE_TYPE_DISK 1
#define FILE_TYPE_CHAR 2
#define FILE_TYPE_PIPE 3
typedef struct { ULONG_PTR Internal, InternalHigh; union { struct { DWORD Offset, OffsetHigh; }; PVOID Pointer; }; HANDLE hEvent; } OVERLAPPED;
typedef struct {
    DWORD dwFileAttributes;
    FILETIME ftCreationTime, ftLastAccessTime, ftLastWriteTime;
    DWORD nFileSizeHigh, nFileSizeLow, dwReserved0, dwReserved1;
    CHAR cFileName[MAX_PATH], cAlternateFileName[14];
} WIN32_FIND_DATAA;
HANDLE CreateFileA(LPCSTR path, DWORD access, DWORD share, SECURITY_ATTRIBUTES *sa, DWORD disposition, DWORD flags,
                   HANDLE tmpl);
BOOL ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, OVERLAPPED *ov);
BOOL WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD put, OVERLAPPED *ov);
BOOL SetFilePointerEx(HANDLE h, LARGE_INTEGER to, LARGE_INTEGER *now, DWORD how);
BOOL SetEndOfFile(HANDLE h);
BOOL GetFileSizeEx(HANDLE h, LARGE_INTEGER *size);
BOOL FlushFileBuffers(HANDLE h);
HANDLE GetStdHandle(DWORD which);
DWORD GetFileType(HANDLE h);
BOOL GetConsoleMode(HANDLE h, DWORD *mode);
static inline BOOL AllocConsole(void) { return FALSE; }
static inline BOOL FreeConsole(void) { return TRUE; }
static inline BOOL SetConsoleTitleA(LPCSTR t) { (void)t; return TRUE; }
DWORD GetFileAttributesA(LPCSTR path);
BOOL CreateDirectoryA(LPCSTR path, SECURITY_ATTRIBUTES *sa);
BOOL RemoveDirectoryA(LPCSTR path);
BOOL DeleteFileA(LPCSTR path);
BOOL MoveFileExA(LPCSTR from, LPCSTR to, DWORD flags);
BOOL SetCurrentDirectoryA(LPCSTR path);
HANDLE FindFirstFileA(LPCSTR pattern, WIN32_FIND_DATAA *out);
BOOL FindNextFileA(HANDLE h, WIN32_FIND_DATAA *out);
BOOL FindClose(HANDLE h);

/* ---- modules ---- */
#define GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS 0x4
#define GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT 0x2
DWORD GetModuleFileNameA(HMODULE m, LPSTR out, DWORD cap);
HMODULE GetModuleHandleA(LPCSTR name);
BOOL GetModuleHandleExA(DWORD flags, LPCSTR name, HMODULE *out);
HMODULE LoadLibraryA(LPCSTR name);
FARPROC GetProcAddress(HMODULE m, LPCSTR name);
BOOL FreeLibrary(HMODULE m);

/* ---- text ---- */
#define CP_ACP 0
#define CP_UTF8 65001
int MultiByteToWideChar(UINT cp, DWORD flags, LPCSTR in, int n, LPWSTR out, int cap);
int WideCharToMultiByte(UINT cp, DWORD flags, LPCWSTR in, int n, LPSTR out, int cap, LPCSTR def, BOOL *used);
static inline void OutputDebugStringA(LPCSTR s) { (void)s; }
#define OutputDebugString OutputDebugStringA

/* ---- the C library's Microsoft names ---- */
#define _strdup strdup
#define _stricmp strcasecmp
#define _strnicmp strncasecmp
#define _atoi64 atoll
#define _ftelli64 ftello
#define _fseeki64 fseeko
#define _fileno fileno
#define _mkgmtime timegm
#define _chsize_s(fd, size) ftruncate((fd), (off_t)(size))
#define localtime_s(tm, t) (localtime_r((t), (tm)) ? 0 : EINVAL)
#define gmtime_s(tm, t) (gmtime_r((t), (tm)) ? 0 : EINVAL)
#define sprintf_s snprintf
#define _snprintf snprintf
#define _aligned_malloc(n, a) aligned_alloc((a), ((n) + (a) - 1) / (a) * (a))
#define _aligned_free free
int _mkdir(const char *path);
extern char **environ;
#define _environ environ

/* ---- the window's (host/linux/display_sdl.c): the game's window stands for every window of this process ---- */
#define GWL_STYLE (-16)
#define GWL_EXSTYLE (-20)
#define WS_EX_TOOLWINDOW 0x80
#define WS_EX_NOACTIVATE 0x08000000
#define SM_CXSCREEN 0
#define SM_CYSCREEN 1
HWND GetForegroundWindow(void);
DWORD GetWindowThreadProcessId(HWND w, DWORD *pid);
LONG_PTR GetWindowLongPtrA(HWND w, int index);
int GetClassNameA(HWND w, LPSTR out, int cap);
BOOL GetClientRect(HWND w, RECT *r);
int GetSystemMetrics(int index);
short GetAsyncKeyState(int vk);
#define VK_LBUTTON 0x01
#define VK_RBUTTON 0x02
#define VK_MBUTTON 0x04
#define VK_BACK 0x08
#define VK_TAB 0x09
#define VK_RETURN 0x0D
#define VK_SHIFT 0x10
#define VK_CONTROL 0x11
#define VK_MENU 0x12
#define VK_PAUSE 0x13
#define VK_CAPITAL 0x14
#define VK_ESCAPE 0x1B
#define VK_SPACE 0x20
#define VK_PRIOR 0x21
#define VK_NEXT 0x22
#define VK_END 0x23
#define VK_HOME 0x24
#define VK_LEFT 0x25
#define VK_UP 0x26
#define VK_RIGHT 0x27
#define VK_DOWN 0x28
#define VK_INSERT 0x2D
#define VK_DELETE 0x2E
#define VK_NUMPAD0 0x60
#define VK_MULTIPLY 0x6A
#define VK_ADD 0x6B
#define VK_SUBTRACT 0x6D
#define VK_DECIMAL 0x6E
#define VK_DIVIDE 0x6F
#define VK_F1 0x70
#define VK_LSHIFT 0xA0
#define VK_RSHIFT 0xA1
#define VK_LCONTROL 0xA2
#define VK_RCONTROL 0xA3
#define VK_LMENU 0xA4
#define VK_RMENU 0xA5
#define VK_OEM_1 0xBA
#define VK_OEM_PLUS 0xBB
#define VK_OEM_COMMA 0xBC
#define VK_OEM_MINUS 0xBD
#define VK_OEM_PERIOD 0xBE
#define VK_OEM_2 0xBF
#define VK_OEM_3 0xC0
#define VK_OEM_4 0xDB
#define VK_OEM_5 0xDC
#define VK_OEM_6 0xDD
#define VK_OEM_7 0xDE
#define VK_F2 0x71
#define VK_F3 0x72
#define VK_F4 0x73
#define VK_F5 0x74
#define VK_F6 0x75
#define VK_F7 0x76
#define VK_F8 0x77
#define VK_F9 0x78
#define VK_F10 0x79
#define VK_F11 0x7A
#define VK_F12 0x7B

/* ---- waveOut (mmsystem.h), on SDL's audio ---- */
typedef void *HWAVEOUT;
typedef UINT MMRESULT;
#define MMSYSERR_NOERROR 0
#define MMSYSERR_ERROR 1
#define WAVE_MAPPER ((UINT)-1)
#define WAVE_FORMAT_PCM 1
#define CALLBACK_NULL 0
#define WHDR_DONE 0x1
#define WHDR_PREPARED 0x2
#define WHDR_INQUEUE 0x10
typedef struct { WORD wFormatTag, nChannels; DWORD nSamplesPerSec, nAvgBytesPerSec; WORD nBlockAlign, wBitsPerSample, cbSize; } WAVEFORMATEX;
typedef struct wavehdr_tag {
    LPSTR lpData;
    DWORD dwBufferLength, dwBytesRecorded;
    DWORD_PTR dwUser;
    DWORD dwFlags, dwLoops;
    struct wavehdr_tag *lpNext;
    DWORD_PTR reserved;
} WAVEHDR;
MMRESULT waveOutOpen(HWAVEOUT *out, UINT device, const WAVEFORMATEX *f, DWORD_PTR cb, DWORD_PTR inst, DWORD flags);
MMRESULT waveOutPrepareHeader(HWAVEOUT w, WAVEHDR *h, UINT size);
MMRESULT waveOutUnprepareHeader(HWAVEOUT w, WAVEHDR *h, UINT size);
MMRESULT waveOutWrite(HWAVEOUT w, WAVEHDR *h, UINT size);
MMRESULT waveOutReset(HWAVEOUT w);
MMRESULT waveOutClose(HWAVEOUT w);

#ifdef __cplusplus
}
#endif
#endif
