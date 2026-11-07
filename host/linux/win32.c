/* (hookshot) The Win32 calls of host/linux/windows.h that keep state, on Linux: handles (threads, events,
   semaphores, waitable timers, files, directory listings), the futex locks' slow paths, fibers on ucontext,
   virtual memory on mmap, vectored exception handlers on signals, modules on dlopen and waveOut on SDL's audio.
   Built into the Linux runtime only (nx2/build.py); the Vulkan back end binds these from it. */
#define _GNU_SOURCE
#include <windows.h>

#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <fnmatch.h>
#include <linux/futex.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <ucontext.h>
#include <unistd.h>

#include <SDL3/SDL.h>

/* ---- errors ---- */
static _Thread_local DWORD last_error;
DWORD GetLastError(void) { return last_error; }
void SetLastError(DWORD e) { last_error = e; }
static DWORD from_errno(int e) {
    switch (e) {
    case 0: return ERROR_SUCCESS;
    case ENOENT: return ERROR_FILE_NOT_FOUND;
    case ENOTDIR: return ERROR_PATH_NOT_FOUND;
    case EACCES: case EPERM: case EROFS: return ERROR_ACCESS_DENIED;
    case EEXIST: return ERROR_ALREADY_EXISTS;
    case ENOMEM: return ERROR_NOT_ENOUGH_MEMORY;
    case EBADF: return ERROR_INVALID_HANDLE;
    case ETIMEDOUT: return ERROR_TIMEOUT;
    default: return ERROR_INVALID_PARAMETER;
    }
}
static BOOL fail(void) { last_error = from_errno(errno); return FALSE; }

/* ---- futexes ---- */
static int futex_wait(uint32_t *p, uint32_t seen, const struct timespec *rel) {
    return (int)syscall(SYS_futex, p, FUTEX_WAIT_PRIVATE, seen, rel, NULL, 0);
}
static void futex_wake(uint32_t *p, int n) { syscall(SYS_futex, p, FUTEX_WAKE_PRIVATE, n, NULL, NULL, 0); }
static struct timespec rel_ms(DWORD ms) { struct timespec t = {(time_t)(ms / 1000), (long)(ms % 1000) * 1000000L}; return t; }

/* SRW locks: state is WIN32_SRW_WRITER or the reader count; waiters, how many sleep on it. A release wakes one
   sleeper when anyone sleeps, which tries again (a reader let in while others still sleep only costs them time). */
void win32_srw_wait(SRWLOCK *l, uint32_t seen) { futex_wait(&l->state, seen, NULL); }
void win32_srw_wake(SRWLOCK *l) { futex_wake(&l->state, 1); }
void win32_srw_lock_slow(PSRWLOCK l, int shared) {
    for (int spin = 0; spin < 100; spin++) {
        if (shared ? TryAcquireSRWLockShared(l) : TryAcquireSRWLockExclusive(l)) return;
        _mm_pause();
    }
    for (;;) {
        __atomic_add_fetch(&l->waiters, 1, __ATOMIC_SEQ_CST);
        const uint32_t s = __atomic_load_n(&l->state, __ATOMIC_SEQ_CST);
        if (shared ? (s & WIN32_SRW_WRITER) != 0 : s != 0) futex_wait(&l->state, s, NULL);
        __atomic_sub_fetch(&l->waiters, 1, __ATOMIC_SEQ_CST);
        if (shared ? TryAcquireSRWLockShared(l) : TryAcquireSRWLockExclusive(l)) {
            /* (a wake that let this one in may have been the only one: pass it on while others sleep and the lock
               still admits them, readers behind a reader) */
            if (shared && __atomic_load_n(&l->waiters, __ATOMIC_SEQ_CST)) futex_wake(&l->state, 1);
            return;
        }
    }
}

void WakeConditionVariable(PCONDITION_VARIABLE cv) {
    __atomic_add_fetch(&cv->seq, 1, __ATOMIC_SEQ_CST);
    futex_wake(&cv->seq, 1);
}
void WakeAllConditionVariable(PCONDITION_VARIABLE cv) {
    __atomic_add_fetch(&cv->seq, 1, __ATOMIC_SEQ_CST);
    futex_wake(&cv->seq, INT_MAX);
}
BOOL SleepConditionVariableSRW(PCONDITION_VARIABLE cv, PSRWLOCK l, DWORD ms, ULONG flags) {
    const int shared = (flags & CONDITION_VARIABLE_LOCKMODE_SHARED) != 0;
    const uint32_t seq = __atomic_load_n(&cv->seq, __ATOMIC_SEQ_CST);
    if (shared) ReleaseSRWLockShared(l);
    else ReleaseSRWLockExclusive(l);
    struct timespec t = rel_ms(ms);
    const int r = futex_wait(&cv->seq, seq, ms == INFINITE ? NULL : &t);
    const int timed_out = r != 0 && errno == ETIMEDOUT;
    if (shared) AcquireSRWLockShared(l);
    else AcquireSRWLockExclusive(l);
    if (timed_out) { last_error = ERROR_TIMEOUT; return FALSE; }
    return TRUE;
}

static long tid(void) {
    static _Thread_local long t;
    if (!t) t = syscall(SYS_gettid);
    return t;
}
void InitializeCriticalSection(LPCRITICAL_SECTION cs) { memset(cs, 0, sizeof *cs); }
void EnterCriticalSection(LPCRITICAL_SECTION cs) {
    if (__atomic_load_n(&cs->owner, __ATOMIC_RELAXED) == tid()) { cs->depth++; return; }
    AcquireSRWLockExclusive(&cs->lock);
    __atomic_store_n(&cs->owner, tid(), __ATOMIC_RELAXED);
    cs->depth = 1;
}
BOOL TryEnterCriticalSection(LPCRITICAL_SECTION cs) {
    if (__atomic_load_n(&cs->owner, __ATOMIC_RELAXED) == tid()) { cs->depth++; return TRUE; }
    if (!TryAcquireSRWLockExclusive(&cs->lock)) return FALSE;
    __atomic_store_n(&cs->owner, tid(), __ATOMIC_RELAXED);
    cs->depth = 1;
    return TRUE;
}
void LeaveCriticalSection(LPCRITICAL_SECTION cs) {
    if (--cs->depth) return;
    __atomic_store_n(&cs->owner, 0, __ATOMIC_RELAXED);
    ReleaseSRWLockExclusive(&cs->lock);
}

/* ---- handles ---- */
enum { K_THREAD = 0x54485244, K_EVENT, K_SEMAPHORE, K_TIMER, K_FILE, K_FIND };
typedef struct {
    int kind;
    int refs;   /* (a thread: its handle and the thread itself) */
    pthread_mutex_t m;
    pthread_cond_t cv;
} Obj;
typedef struct {
    Obj o;
    pthread_t thread;
    long tid;
    int done, own;
    DWORD code;
    LPTHREAD_START_ROUTINE fn;
    void *arg;
} ThreadObj;
typedef struct { Obj o; int manual, signaled; } EventObj;
typedef struct { Obj o; LONG count, max; } SemObj;
typedef struct { Obj o; struct timespec due; int armed; } TimerObj;
typedef struct { Obj o; int fd; } FileObj;
typedef struct { Obj o; DIR *dir; char dirpath[PATH_MAX], pat[256]; } FindObj;

#define PSEUDO_PROCESS ((HANDLE)(intptr_t)-1)
#define PSEUDO_THREAD ((HANDLE)(intptr_t)-2)

static void *obj_new(int kind, size_t size) {
    Obj *o = (Obj *)calloc(1, size);
    o->kind = kind;
    o->refs = 1;
    pthread_mutex_init(&o->m, NULL);
    pthread_condattr_t a;
    pthread_condattr_init(&a);
    pthread_condattr_setclock(&a, CLOCK_MONOTONIC);
    pthread_cond_init(&o->cv, &a);
    pthread_condattr_destroy(&a);
    return o;
}
static void obj_unref(Obj *o) {
    if (__atomic_sub_fetch(&o->refs, 1, __ATOMIC_ACQ_REL)) return;
    pthread_mutex_destroy(&o->m);
    pthread_cond_destroy(&o->cv);
    free(o);
}
static int is_obj(HANDLE h, int kind) {
    return h && h != PSEUDO_PROCESS && h != PSEUDO_THREAD && (uintptr_t)h > 0x10000 && ((Obj *)h)->kind == kind;
}

/* this thread's object: made on first use for threads CreateThread did not start (the main thread) */
static _Thread_local ThreadObj *self_obj;
static ThreadObj *self_thread(void) {
    if (!self_obj) {
        ThreadObj *t = (ThreadObj *)obj_new(K_THREAD, sizeof *t);
        t->thread = pthread_self();
        t->tid = tid();
        t->o.refs = 2;   /* (never freed: the thread's own reference outlives every handle) */
        self_obj = t;
    }
    return self_obj;
}
static ThreadObj *thread_of(HANDLE h) {
    if (h == PSEUDO_THREAD) return self_thread();
    return is_obj(h, K_THREAD) ? (ThreadObj *)h : NULL;
}

static void thread_finished(ThreadObj *t, DWORD code) {
    pthread_mutex_lock(&t->o.m);
    t->done = 1;
    t->code = code;
    pthread_cond_broadcast(&t->o.cv);
    pthread_mutex_unlock(&t->o.m);
    obj_unref(&t->o);
}
static void *thread_start(void *p) {
    ThreadObj *t = (ThreadObj *)p;
    self_obj = t;
    t->tid = tid();
    /* (a stack of its own for the signal handlers: a fault from a stack overflow is reported, not a silent death) */
    stack_t ss = {0};
    ss.ss_size = 64 << 10;
    ss.ss_sp = malloc(ss.ss_size);
    if (ss.ss_sp) sigaltstack(&ss, NULL);
    thread_finished(t, t->fn(t->arg));
    return NULL;
}
HANDLE CreateThread(SECURITY_ATTRIBUTES *sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags, LPDWORD id) {
    (void)sa;
    ThreadObj *t = (ThreadObj *)obj_new(K_THREAD, sizeof *t);
    t->o.refs = 2;
    t->fn = fn;
    t->arg = arg;
    pthread_attr_t a;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    if (stack) pthread_attr_setstacksize(&a, stack < (1 << 20) ? (1 << 20) : stack);
    (void)flags;   /* (CREATE_SUSPENDED: not used here) */
    const int r = pthread_create(&t->thread, &a, thread_start, t);
    pthread_attr_destroy(&a);
    if (r) {
        free(t);
        last_error = from_errno(r);
        return NULL;
    }
    if (id) *id = 0;
    return t;
}
void ExitThread(DWORD code) {
    ThreadObj *t = self_thread();
    thread_finished(t, code);
    pthread_exit(NULL);
}
HANDLE GetCurrentThread(void) { return PSEUDO_THREAD; }
HANDLE GetCurrentProcess(void) { return PSEUDO_PROCESS; }
DWORD GetCurrentThreadId(void) { return (DWORD)tid(); }
DWORD GetCurrentProcessId(void) { return (DWORD)getpid(); }
DWORD GetThreadId(HANDLE h) {
    ThreadObj *t = thread_of(h);
    return t ? (DWORD)t->tid : 0;
}
BOOL DuplicateHandle(HANDLE sp, HANDLE h, HANDLE tp, HANDLE *out, DWORD access, BOOL inherit, DWORD options) {
    (void)sp, (void)tp, (void)access, (void)inherit, (void)options;
    Obj *o = h == PSEUDO_THREAD ? &self_thread()->o : (Obj *)h;
    if (!o || h == PSEUDO_PROCESS) { last_error = ERROR_INVALID_HANDLE; return FALSE; }
    __atomic_add_fetch(&o->refs, 1, __ATOMIC_ACQ_REL);
    *out = o;
    return TRUE;
}
BOOL CloseHandle(HANDLE h) {
    if (!h || h == PSEUDO_PROCESS || h == PSEUDO_THREAD) return TRUE;
    Obj *o = (Obj *)h;
    if (o->kind == K_FILE) {
        const int fd = ((FileObj *)o)->fd;
        if (fd > 2) close(fd);
        if (fd <= 2) return TRUE;   /* (GetStdHandle's: never freed) */
    }
    if (o->kind == K_FIND && ((FindObj *)o)->dir) closedir(((FindObj *)o)->dir);
    obj_unref(o);
    return TRUE;
}

static int wait_until(Obj *o, const struct timespec *deadline) {   /* (with o->m held) 0: woken, else timed out */
    return deadline ? pthread_cond_timedwait(&o->cv, &o->m, deadline) == ETIMEDOUT : (pthread_cond_wait(&o->cv, &o->m), 0);
}
static struct timespec deadline_ms(DWORD ms) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    t.tv_sec += ms / 1000;
    t.tv_nsec += (long)(ms % 1000) * 1000000L;
    if (t.tv_nsec >= 1000000000L) t.tv_sec++, t.tv_nsec -= 1000000000L;
    return t;
}
DWORD WaitForSingleObject(HANDLE h, DWORD ms) {
    Obj *o = h == PSEUDO_THREAD ? &self_thread()->o : (Obj *)h;
    if (!o || h == PSEUDO_PROCESS) return WAIT_FAILED;
    if (o->kind == K_TIMER) {   /* (absolute, on the monotonic clock: no drift for the short sleeps it is used for) */
        TimerObj *t = (TimerObj *)o;
        if (t->armed) while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t->due, NULL) == EINTR) {}
        t->armed = 0;
        return WAIT_OBJECT_0;
    }
    struct timespec dl = deadline_ms(ms);
    const struct timespec *d = ms == INFINITE ? NULL : &dl;
    DWORD r = WAIT_OBJECT_0;
    pthread_mutex_lock(&o->m);
    switch (o->kind) {
    case K_THREAD:
        while (!((ThreadObj *)o)->done)
            if (wait_until(o, d)) { r = WAIT_TIMEOUT; break; }
        break;
    case K_EVENT: {
        EventObj *e = (EventObj *)o;
        while (!e->signaled)
            if (wait_until(o, d)) break;
        if (!e->signaled) r = WAIT_TIMEOUT;
        else if (!e->manual) e->signaled = 0;
        break;
    }
    case K_SEMAPHORE: {
        SemObj *s = (SemObj *)o;
        while (!s->count)
            if (wait_until(o, d)) break;
        if (!s->count) r = WAIT_TIMEOUT;
        else s->count--;
        break;
    }
    default:
        r = WAIT_FAILED;
    }
    pthread_mutex_unlock(&o->m);
    return r;
}
HANDLE CreateEventA(SECURITY_ATTRIBUTES *sa, BOOL manual, BOOL initial, LPCSTR name) {
    (void)sa, (void)name;
    EventObj *e = (EventObj *)obj_new(K_EVENT, sizeof *e);
    e->manual = manual;
    e->signaled = initial;
    return e;
}
BOOL SetEvent(HANDLE h) {
    if (!is_obj(h, K_EVENT)) return FALSE;
    EventObj *e = (EventObj *)h;
    pthread_mutex_lock(&e->o.m);
    e->signaled = 1;
    if (e->manual) pthread_cond_broadcast(&e->o.cv);
    else pthread_cond_signal(&e->o.cv);
    pthread_mutex_unlock(&e->o.m);
    return TRUE;
}
BOOL ResetEvent(HANDLE h) {
    if (!is_obj(h, K_EVENT)) return FALSE;
    EventObj *e = (EventObj *)h;
    pthread_mutex_lock(&e->o.m);
    e->signaled = 0;
    pthread_mutex_unlock(&e->o.m);
    return TRUE;
}
HANDLE CreateSemaphoreA(SECURITY_ATTRIBUTES *sa, LONG initial, LONG max, LPCSTR name) {
    (void)sa, (void)name;
    SemObj *s = (SemObj *)obj_new(K_SEMAPHORE, sizeof *s);
    s->count = initial;
    s->max = max;
    return s;
}
BOOL ReleaseSemaphore(HANDLE h, LONG n, LONG *prev) {
    if (!is_obj(h, K_SEMAPHORE)) return FALSE;
    SemObj *s = (SemObj *)h;
    pthread_mutex_lock(&s->o.m);
    if (prev) *prev = s->count;
    const int ok = s->count + n <= s->max;
    if (ok) s->count += n;
    pthread_cond_broadcast(&s->o.cv);
    pthread_mutex_unlock(&s->o.m);
    return ok;
}
HANDLE CreateWaitableTimerExW(SECURITY_ATTRIBUTES *sa, LPCWSTR name, DWORD flags, DWORD access) {
    (void)sa, (void)name, (void)flags, (void)access;
    return obj_new(K_TIMER, sizeof(TimerObj));
}
BOOL SetWaitableTimerEx(HANDLE h, const LARGE_INTEGER *due, LONG period, void *fn, void *arg, void *wake, ULONG delay) {
    (void)period, (void)fn, (void)arg, (void)wake, (void)delay;
    if (!is_obj(h, K_TIMER) || due->QuadPart >= 0) return FALSE;   /* (relative times only: what is used) */
    TimerObj *t = (TimerObj *)h;
    const int64_t ns = -due->QuadPart * 100;
    clock_gettime(CLOCK_MONOTONIC, &t->due);
    t->due.tv_sec += ns / 1000000000;
    t->due.tv_nsec += ns % 1000000000;
    if (t->due.tv_nsec >= 1000000000L) t->due.tv_sec++, t->due.tv_nsec -= 1000000000L;
    t->armed = 1;
    return TRUE;
}

/* Priorities: lowered as asked (a thread's nice value); raised only where the process may (RLIMIT_NICE), else left
   at the default without a word. */
BOOL SetThreadPriority(HANDLE h, int prio) {
    ThreadObj *t = thread_of(h);
    if (!t || !t->tid) return FALSE;
    const int nice = prio <= -2 ? 4 : prio == -1 ? 2 : prio == 0 ? 0 : prio == 1 ? -2 : -4;
    if (setpriority(PRIO_PROCESS, (id_t)t->tid, nice) && nice < 0) setpriority(PRIO_PROCESS, (id_t)t->tid, 0);
    return TRUE;
}
DWORD_PTR SetThreadAffinityMask(HANDLE h, DWORD_PTR mask) {
    ThreadObj *t = thread_of(h);
    if (!t) return 0;
    cpu_set_t was, want;
    CPU_ZERO(&was);
    CPU_ZERO(&want);
    pthread_getaffinity_np(t->thread, sizeof was, &was);
    for (int i = 0; i < 64; i++)
        if (mask >> i & 1) CPU_SET(i, &want);
    if (pthread_setaffinity_np(t->thread, sizeof want, &want)) return 0;
    DWORD_PTR old = 0;
    for (int i = 0; i < 64; i++)
        if (CPU_ISSET(i, &was)) old |= (DWORD_PTR)1 << i;
    return old ? old : 1;
}
BOOL GetProcessAffinityMask(HANDLE h, DWORD_PTR *proc, DWORD_PTR *sys) {
    (void)h;
    cpu_set_t s;
    CPU_ZERO(&s);
    sched_getaffinity(0, sizeof s, &s);
    DWORD_PTR m = 0;
    for (int i = 0; i < 64; i++)
        if (CPU_ISSET(i, &s)) m |= (DWORD_PTR)1 << i;
    *proc = m;
    *sys = m;
    return TRUE;
}
HRESULT SetThreadDescription(HANDLE h, PCWSTR name) {
    ThreadObj *t = thread_of(h);
    if (!t) return -1;
    char n[16];
    WideCharToMultiByte(CP_UTF8, 0, name, -1, n, (int)sizeof n, NULL, NULL);
    n[15] = 0;
    pthread_setname_np(t->thread, n);
    return S_OK;
}
BOOL GetThreadTimes(HANDLE h, FILETIME *created, FILETIME *exited, FILETIME *kernel, FILETIME *user) {
    ThreadObj *t = thread_of(h);
    clockid_t clk;
    struct timespec ts;
    if (!t || pthread_getcpuclockid(t->thread, &clk) || clock_gettime(clk, &ts)) return FALSE;
    const uint64_t v = ((uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec) / 100;   /* (100 ns units) */
    memset(created, 0, sizeof *created);
    memset(exited, 0, sizeof *exited);
    memset(kernel, 0, sizeof *kernel);
    user->dwLowDateTime = (DWORD)v;
    user->dwHighDateTime = (DWORD)(v >> 32);
    return TRUE;
}
void ExitProcess(UINT code) {
    fflush(NULL);
    _exit((int)code);
}

/* the cores: each one's logical processors (their thread siblings) */
BOOL GetLogicalProcessorInformation(SYSTEM_LOGICAL_PROCESSOR_INFORMATION *out, DWORD *len) {
    const DWORD cap = *len / (DWORD)sizeof *out;
    DWORD n = 0;
    uint64_t seen = 0;
    for (int cpu = 0; cpu < 64; cpu++) {
        char path[96], line[256];
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list", cpu);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        const int got = fgets(line, sizeof line, f) != NULL;
        fclose(f);
        if (!got) continue;
        uint64_t mask = 0;
        for (char *p = line; *p && *p != '\n';) {   /* "0,8" or "0-1" */
            char *e;
            const long a = strtol(p, &e, 10);
            long b = a;
            if (*e == '-') b = strtol(e + 1, &e, 10);
            for (long i = a; i <= b && i < 64; i++) mask |= 1ull << i;
            p = *e == ',' ? e + 1 : e;
            if (e == p && *p != ',') break;
        }
        if (!mask || (mask & seen)) continue;
        seen |= mask;
        if (n < cap) {
            memset(&out[n], 0, sizeof out[n]);
            out[n].ProcessorMask = (ULONG_PTR)mask;
            out[n].Relationship = RelationProcessorCore;
        }
        n++;
    }
    *len = n * (DWORD)sizeof *out;
    if (n > cap) { last_error = ERROR_INSUFFICIENT_BUFFER; return FALSE; }
    return n > 0;
}

/* ---- fibers ---- */
typedef struct {
    ucontext_t uc;
    void *stack;
    size_t size;
    LPFIBER_START_ROUTINE fn;
    void *arg;
} Fiber;
static _Thread_local Fiber *cur_fiber;
static void fiber_entry(unsigned lo, unsigned hi) {
    Fiber *f = (Fiber *)(uintptr_t)((uint64_t)hi << 32 | lo);
    f->fn(f->arg);
    fprintf(stderr, "[win32] a fiber's function returned: the thread ends\n");   /* (as on Windows) */
    pthread_exit(NULL);
}
LPVOID CreateFiberEx(SIZE_T commit, SIZE_T reserve, DWORD flags, LPFIBER_START_ROUTINE fn, LPVOID arg) {
    (void)commit, (void)flags;
    Fiber *f = (Fiber *)calloc(1, sizeof *f);
    f->size = reserve ? reserve : (1 << 20);
    /* (reserved, not committed: the pages are given as they are touched; the lowest one a guard) */
    f->stack = mmap(NULL, f->size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (f->stack == MAP_FAILED) { free(f); last_error = ERROR_NOT_ENOUGH_MEMORY; return NULL; }
    mprotect(f->stack, 4096, PROT_NONE);
    f->fn = fn;
    f->arg = arg;
    getcontext(&f->uc);
    f->uc.uc_stack.ss_sp = f->stack;
    f->uc.uc_stack.ss_size = f->size;
    f->uc.uc_link = NULL;
    const uint64_t p = (uint64_t)(uintptr_t)f;
    makecontext(&f->uc, (void (*)(void))fiber_entry, 2, (unsigned)p, (unsigned)(p >> 32));
    return f;
}
LPVOID ConvertThreadToFiberEx(LPVOID arg, DWORD flags) {
    (void)flags;
    if (cur_fiber) return cur_fiber;
    Fiber *f = (Fiber *)calloc(1, sizeof *f);
    f->arg = arg;
    cur_fiber = f;
    return f;
}
void SwitchToFiber(LPVOID to) {
    Fiber *from = cur_fiber, *f = (Fiber *)to;
    if (!from || from == f) return;
    cur_fiber = f;
    swapcontext(&from->uc, &f->uc);
    /* (back here, maybe on another thread: that thread's fiber is this one) */
    cur_fiber = from;
}
void DeleteFiber(LPVOID fiber) {
    Fiber *f = (Fiber *)fiber;
    if (!f) return;
    if (f->stack) munmap(f->stack, f->size);
    free(f);
}

/* ---- virtual memory ---- */
static int prot_of(DWORD p) {
    if (p & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE)) return PROT_READ | PROT_WRITE;
    if (p & (PAGE_READONLY | PAGE_EXECUTE_READ)) return PROT_READ;
    return PROT_NONE;
}
LPVOID VirtualAlloc(LPVOID at, SIZE_T size, DWORD type, DWORD protect) {
    if (at && (type & MEM_RESET)) {   /* (the contents may go; the pages stay usable) */
        madvise(at, size, MADV_FREE);
        return at;
    }
    if (at && !(type & MEM_RESERVE)) {   /* MEM_COMMIT inside a reservation */
        if (mprotect(at, size, prot_of(protect))) { fail(); return NULL; }
        return at;
    }
    const int prot = type & MEM_COMMIT ? prot_of(protect) : PROT_NONE;
    void *p = mmap(at, size, prot, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE | (at ? MAP_FIXED_NOREPLACE : 0), -1, 0);
    if (p == MAP_FAILED) { fail(); return NULL; }
    if (at && p != at) { munmap(p, size); last_error = ERROR_INVALID_PARAMETER; return NULL; }
    return p;
}
BOOL VirtualFree(LPVOID at, SIZE_T size, DWORD type) {
    if (type & MEM_DECOMMIT)   /* (reads as zero afterwards, as a page committed again on Windows does) */
        return madvise(at, size, MADV_DONTNEED) == 0 ? TRUE : fail();
    if ((type & MEM_RELEASE) && size) return munmap(at, size) == 0 ? TRUE : fail();
    last_error = ERROR_INVALID_PARAMETER;   /* (MEM_RELEASE of a whole reservation by its base: not used here) */
    return FALSE;
}
BOOL VirtualProtect(LPVOID at, SIZE_T size, DWORD protect, DWORD *old) {
    if (old) *old = PAGE_READWRITE;   /* (Linux does not say: what every caller here restores) */
    const uintptr_t lo = (uintptr_t)at & ~(uintptr_t)4095;
    return mprotect((void *)lo, size + ((uintptr_t)at - lo), prot_of(protect)) == 0 ? TRUE : fail();
}
/* from /proc/self/maps: a mapping is committed (the kernel gives its pages on touch), a gap free */
SIZE_T VirtualQuery(LPCVOID at, MEMORY_BASIC_INFORMATION *out, SIZE_T size) {
    if (size < sizeof *out) return 0;
    const uintptr_t a = (uintptr_t)at & ~(uintptr_t)4095;
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f) return 0;
    char line[512];
    uintptr_t prev_end = 0;
    int found = 0;
    memset(out, 0, sizeof *out);
    while (fgets(line, sizeof line, f)) {
        unsigned long lo, hi;
        char perms[8] = "";
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perms) != 3) continue;
        if (a < lo) {   /* in the gap before this mapping */
            out->BaseAddress = (PVOID)a;
            out->RegionSize = lo - a;
            out->State = MEM_FREE;
            out->Protect = PAGE_NOACCESS;
            found = 1;
            break;
        }
        if (a < hi) {
            const int r = perms[0] == 'r', w = perms[1] == 'w', x = perms[2] == 'x';
            out->BaseAddress = (PVOID)a;
            out->AllocationBase = (PVOID)lo;
            out->RegionSize = hi - a;
            out->State = r || w || x ? MEM_COMMIT : MEM_RESERVE;
            out->Protect = w ? (x ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE) : r ? (x ? PAGE_EXECUTE_READ : PAGE_READONLY)
                             : x ? PAGE_EXECUTE : PAGE_NOACCESS;
            out->AllocationProtect = out->Protect;
            out->Type = strchr(line, '/') ? MEM_IMAGE : MEM_PRIVATE;
            found = 1;
            break;
        }
        prev_end = hi;
    }
    fclose(f);
    (void)prev_end;
    if (!found) {
        if (a >= 0x800000000000ull) return 0;
        out->BaseAddress = (PVOID)a;
        out->RegionSize = 0x800000000000ull - a;
        out->State = MEM_FREE;
        out->Protect = PAGE_NOACCESS;
    }
    return sizeof *out;
}

/* ---- exceptions: SIGSEGV, SIGBUS, SIGILL, SIGFPE and SIGTRAP through the vectored handlers, then the unhandled
   filter, then the signal's default (a core dump) ---- */
#define MAX_VEH 16
static struct { PVECTORED_EXCEPTION_HANDLER fn; } veh[MAX_VEH];
static int nveh;
static SRWLOCK veh_lock = SRWLOCK_INIT;
static LPTOP_LEVEL_EXCEPTION_FILTER unhandled;
static _Thread_local sigjmp_buf *guard;

static void ctx_from(CONTEXT *c, const ucontext_t *u) {
    const greg_t *g = u->uc_mcontext.gregs;
    c->Rax = (DWORD64)g[REG_RAX], c->Rcx = (DWORD64)g[REG_RCX], c->Rdx = (DWORD64)g[REG_RDX], c->Rbx = (DWORD64)g[REG_RBX];
    c->Rsp = (DWORD64)g[REG_RSP], c->Rbp = (DWORD64)g[REG_RBP], c->Rsi = (DWORD64)g[REG_RSI], c->Rdi = (DWORD64)g[REG_RDI];
    c->R8 = (DWORD64)g[REG_R8], c->R9 = (DWORD64)g[REG_R9], c->R10 = (DWORD64)g[REG_R10], c->R11 = (DWORD64)g[REG_R11];
    c->R12 = (DWORD64)g[REG_R12], c->R13 = (DWORD64)g[REG_R13], c->R14 = (DWORD64)g[REG_R14], c->R15 = (DWORD64)g[REG_R15];
    c->Rip = (DWORD64)g[REG_RIP];
    c->EFlags = (DWORD)g[REG_EFL];
}
static void ctx_to(ucontext_t *u, const CONTEXT *c) {
    greg_t *g = u->uc_mcontext.gregs;
    g[REG_RAX] = (greg_t)c->Rax, g[REG_RCX] = (greg_t)c->Rcx, g[REG_RDX] = (greg_t)c->Rdx, g[REG_RBX] = (greg_t)c->Rbx;
    g[REG_RSP] = (greg_t)c->Rsp, g[REG_RBP] = (greg_t)c->Rbp, g[REG_RSI] = (greg_t)c->Rsi, g[REG_RDI] = (greg_t)c->Rdi;
    g[REG_R8] = (greg_t)c->R8, g[REG_R9] = (greg_t)c->R9, g[REG_R10] = (greg_t)c->R10, g[REG_R11] = (greg_t)c->R11;
    g[REG_R12] = (greg_t)c->R12, g[REG_R13] = (greg_t)c->R13, g[REG_R14] = (greg_t)c->R14, g[REG_R15] = (greg_t)c->R15;
    g[REG_RIP] = (greg_t)c->Rip;
    g[REG_EFL] = (greg_t)c->EFlags;
}
static void on_signal(int sig, siginfo_t *si, void *uctx) {
    ucontext_t *u = (ucontext_t *)uctx;
    if (guard && (sig == SIGSEGV || sig == SIGBUS)) siglongjmp(*guard, 1);
    EXCEPTION_RECORD rec;
    CONTEXT ctx;
    memset(&rec, 0, sizeof rec);
    ctx_from(&ctx, u);
    rec.ExceptionAddress = (PVOID)ctx.Rip;
    switch (sig) {
    case SIGSEGV:
    case SIGBUS:
        rec.ExceptionCode = EXCEPTION_ACCESS_VIOLATION;
        rec.NumberParameters = 2;
        rec.ExceptionInformation[0] = (u->uc_mcontext.gregs[REG_ERR] & 2) ? 1 : 0;   /* (the page fault's W bit) */
        rec.ExceptionInformation[1] = (ULONG_PTR)si->si_addr;
        break;
    case SIGILL: rec.ExceptionCode = EXCEPTION_ILLEGAL_INSTRUCTION; break;
    case SIGFPE: rec.ExceptionCode = EXCEPTION_INT_DIVIDE_BY_ZERO; break;
    default: rec.ExceptionCode = si->si_code == TRAP_BRKPT ? EXCEPTION_BREAKPOINT : EXCEPTION_SINGLE_STEP;
    }
    EXCEPTION_POINTERS ep = {&rec, &ctx};
    for (int i = 0; i < nveh; i++)
        if (veh[i].fn && veh[i].fn(&ep) == EXCEPTION_CONTINUE_EXECUTION) { ctx_to(u, &ctx); return; }
    if (unhandled && unhandled(&ep) == EXCEPTION_CONTINUE_EXECUTION) { ctx_to(u, &ctx); return; }
    signal(sig, SIG_DFL);   /* (returning faults again, now to the default: the process ends as it would have) */
}
static void install_signals(void) {
    static int done;
    if (done++) return;
    stack_t ss = {0};   /* (the main thread's alternate stack; CreateThread's threads make their own) */
    ss.ss_size = 64 << 10;
    ss.ss_sp = malloc(ss.ss_size);
    if (ss.ss_sp) sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_signal;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigemptyset(&sa.sa_mask);
    static const int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGTRAP};
    for (unsigned i = 0; i < sizeof sigs / sizeof *sigs; i++) sigaction(sigs[i], &sa, NULL);
}
PVOID AddVectoredExceptionHandler(ULONG first, PVECTORED_EXCEPTION_HANDLER fn) {
    AcquireSRWLockExclusive(&veh_lock);
    install_signals();
    if (nveh >= MAX_VEH) { ReleaseSRWLockExclusive(&veh_lock); return NULL; }
    if (first) {
        memmove(&veh[1], &veh[0], sizeof veh[0] * (size_t)nveh);
        veh[0].fn = fn;
    } else {
        veh[nveh].fn = fn;
    }
    __atomic_add_fetch(&nveh, 1, __ATOMIC_RELEASE);
    ReleaseSRWLockExclusive(&veh_lock);
    return (PVOID)fn;
}
ULONG RemoveVectoredExceptionHandler(PVOID h) {
    AcquireSRWLockExclusive(&veh_lock);
    for (int i = 0; i < nveh; i++)
        if ((PVOID)veh[i].fn == h) {
            memmove(&veh[i], &veh[i + 1], sizeof veh[0] * (size_t)(nveh - i - 1));
            nveh--;
            ReleaseSRWLockExclusive(&veh_lock);
            return 1;
        }
    ReleaseSRWLockExclusive(&veh_lock);
    return 0;
}
LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER fn) {
    install_signals();
    LPTOP_LEVEL_EXCEPTION_FILTER old = unhandled;
    unhandled = fn;
    return old;
}
void *(*volatile win32_guarded_memcpy)(void *, const void *, size_t) = memcpy;
int win32_guarded_copy(void *dst, const void *src, size_t n) {
    install_signals();
    sigjmp_buf jb;
    sigjmp_buf *outer = guard;
    if (sigsetjmp(jb, 1)) {
        guard = outer;
        return 0;
    }
    guard = &jb;
    win32_guarded_memcpy(dst, src, n);
    guard = outer;
    return 1;
}

/* ---- files ---- */
/* the path with '\' read as '/' (host/nn_fs.c builds its paths with '\') */
static const char *native(const char *in, char *out) {
    size_t i = 0;
    for (; in[i] && i < PATH_MAX - 1; i++) out[i] = in[i] == '\\' ? '/' : in[i];
    out[i] = 0;
    return out;
}
static FileObj std_files[3] = {{{K_FILE, 1 << 30}, 0}, {{K_FILE, 1 << 30}, 1}, {{K_FILE, 1 << 30}, 2}};
HANDLE GetStdHandle(DWORD which) {
    return which == STD_INPUT_HANDLE ? &std_files[0] : which == STD_OUTPUT_HANDLE ? &std_files[1] : &std_files[2];
}
DWORD GetFileType(HANDLE h) {
    if (!is_obj(h, K_FILE)) return FILE_TYPE_UNKNOWN;
    struct stat st;
    if (fstat(((FileObj *)h)->fd, &st)) return FILE_TYPE_UNKNOWN;
    return S_ISCHR(st.st_mode) ? FILE_TYPE_CHAR : S_ISFIFO(st.st_mode) || S_ISSOCK(st.st_mode) ? FILE_TYPE_PIPE : FILE_TYPE_DISK;
}
BOOL GetConsoleMode(HANDLE h, DWORD *mode) {
    *mode = 0;
    return is_obj(h, K_FILE) && isatty(((FileObj *)h)->fd);
}
HANDLE CreateFileA(LPCSTR path, DWORD access, DWORD share, SECURITY_ATTRIBUTES *sa, DWORD disposition, DWORD flags,
                   HANDLE tmpl) {
    (void)share, (void)sa, (void)flags, (void)tmpl;
    char p[PATH_MAX];
    int o = (access & GENERIC_WRITE) ? ((access & GENERIC_READ) ? O_RDWR : O_WRONLY) : O_RDONLY;
    switch (disposition) {
    case CREATE_NEW: o |= O_CREAT | O_EXCL; break;
    case CREATE_ALWAYS: o |= O_CREAT | O_TRUNC; break;
    case OPEN_ALWAYS: o |= O_CREAT; break;
    case TRUNCATE_EXISTING: o |= O_TRUNC; break;
    }
    const int fd = open(native(path, p), o | O_CLOEXEC, 0644);
    if (fd < 0) {
        last_error = errno == EEXIST ? ERROR_FILE_EXISTS : from_errno(errno);
        return INVALID_HANDLE_VALUE;
    }
    struct stat st;
    if (!fstat(fd, &st) && S_ISDIR(st.st_mode)) {   /* (Windows opens no directory this way) */
        close(fd);
        last_error = ERROR_ACCESS_DENIED;
        return INVALID_HANDLE_VALUE;
    }
    FileObj *f = (FileObj *)obj_new(K_FILE, sizeof *f);
    f->fd = fd;
    return f;
}
static int fd_of(HANDLE h) { return is_obj(h, K_FILE) ? ((FileObj *)h)->fd : -1; }
BOOL ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, OVERLAPPED *ov) {
    const ssize_t r = ov ? pread(fd_of(h), buf, n, (off_t)((uint64_t)ov->OffsetHigh << 32 | ov->Offset)) : read(fd_of(h), buf, n);
    if (got) *got = r > 0 ? (DWORD)r : 0;
    return r >= 0 ? TRUE : fail();
}
BOOL WriteFile(HANDLE h, LPCVOID buf, DWORD n, LPDWORD put, OVERLAPPED *ov) {
    const ssize_t r = ov ? pwrite(fd_of(h), buf, n, (off_t)((uint64_t)ov->OffsetHigh << 32 | ov->Offset)) : write(fd_of(h), buf, n);
    if (put) *put = r > 0 ? (DWORD)r : 0;
    return r >= 0 ? TRUE : fail();
}
BOOL SetFilePointerEx(HANDLE h, LARGE_INTEGER to, LARGE_INTEGER *now, DWORD how) {
    const off_t r = lseek(fd_of(h), (off_t)to.QuadPart, how == FILE_BEGIN ? SEEK_SET : how == FILE_CURRENT ? SEEK_CUR : SEEK_END);
    if (r < 0) return fail();
    if (now) now->QuadPart = r;
    return TRUE;
}
BOOL SetEndOfFile(HANDLE h) {
    const int fd = fd_of(h);
    const off_t at = lseek(fd, 0, SEEK_CUR);
    return at >= 0 && ftruncate(fd, at) == 0 ? TRUE : fail();
}
BOOL GetFileSizeEx(HANDLE h, LARGE_INTEGER *size) {
    struct stat st;
    if (fstat(fd_of(h), &st)) return fail();
    size->QuadPart = st.st_size;
    return TRUE;
}
BOOL FlushFileBuffers(HANDLE h) { return fsync(fd_of(h)) == 0 || errno == EINVAL ? TRUE : fail(); }

static DWORD attributes(const struct stat *st, const char *name) {
    DWORD a = S_ISDIR(st->st_mode) ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    if (!(st->st_mode & S_IWUSR)) a |= FILE_ATTRIBUTE_READONLY;
    if (name[0] == '.' && strcmp(name, ".") && strcmp(name, "..")) a |= FILE_ATTRIBUTE_HIDDEN;
    return a;
}
DWORD GetFileAttributesA(LPCSTR path) {
    char p[PATH_MAX];
    struct stat st;
    if (stat(native(path, p), &st)) { fail(); return INVALID_FILE_ATTRIBUTES; }
    const char *base = strrchr(p, '/');
    return attributes(&st, base ? base + 1 : p);
}
BOOL CreateDirectoryA(LPCSTR path, SECURITY_ATTRIBUTES *sa) {
    (void)sa;
    char p[PATH_MAX];
    return mkdir(native(path, p), 0755) == 0 ? TRUE : fail();
}
int _mkdir(const char *path) { return CreateDirectoryA(path, NULL) ? 0 : -1; }
BOOL RemoveDirectoryA(LPCSTR path) {
    char p[PATH_MAX];
    return rmdir(native(path, p)) == 0 ? TRUE : fail();
}
BOOL DeleteFileA(LPCSTR path) {
    char p[PATH_MAX];
    return unlink(native(path, p)) == 0 ? TRUE : fail();
}
BOOL MoveFileExA(LPCSTR from, LPCSTR to, DWORD flags) {
    char a[PATH_MAX], b[PATH_MAX];
    native(from, a), native(to, b);
    if (!(flags & MOVEFILE_REPLACE_EXISTING) && access(b, F_OK) == 0) { last_error = ERROR_ALREADY_EXISTS; return FALSE; }
    return rename(a, b) == 0 ? TRUE : fail();
}
BOOL SetCurrentDirectoryA(LPCSTR path) {
    char p[PATH_MAX];
    return chdir(native(path, p)) == 0 ? TRUE : fail();
}

/* a listing: "<dir>\*" or "<dir>\<pattern>"; a pattern without a wildcard finds that one name */
static BOOL find_next(FindObj *f, WIN32_FIND_DATAA *out) {
    for (struct dirent *e; (e = readdir(f->dir)) != NULL;) {
        if (fnmatch(f->pat, e->d_name, FNM_CASEFOLD)) continue;
        char full[PATH_MAX];
        struct stat st;
        snprintf(full, sizeof full, "%s/%s", f->dirpath, e->d_name);
        if (stat(full, &st)) continue;
        memset(out, 0, sizeof *out);
        out->dwFileAttributes = attributes(&st, e->d_name);
        out->nFileSizeLow = (DWORD)st.st_size;
        out->nFileSizeHigh = (DWORD)((uint64_t)st.st_size >> 32);
        const uint64_t ft = ((uint64_t)st.st_mtime + 11644473600ull) * 10000000ull;   /* (since 1601, 100 ns) */
        out->ftLastWriteTime.dwLowDateTime = out->ftCreationTime.dwLowDateTime = (DWORD)ft;
        out->ftLastWriteTime.dwHighDateTime = out->ftCreationTime.dwHighDateTime = (DWORD)(ft >> 32);
        snprintf(out->cFileName, sizeof out->cFileName, "%s", e->d_name);
        return TRUE;
    }
    last_error = ERROR_NO_MORE_FILES;
    return FALSE;
}
HANDLE FindFirstFileA(LPCSTR pattern, WIN32_FIND_DATAA *out) {
    char p[PATH_MAX];
    native(pattern, p);
    char *slash = strrchr(p, '/');
    FindObj *f = (FindObj *)obj_new(K_FIND, sizeof *f);
    snprintf(f->dirpath, sizeof f->dirpath, "%s", slash ? (slash == p ? "/" : (*slash = 0, p)) : ".");
    snprintf(f->pat, sizeof f->pat, "%s", slash ? slash + 1 : p);
    if (!(f->dir = opendir(f->dirpath))) {
        fail();
        if (last_error == ERROR_FILE_NOT_FOUND) last_error = ERROR_PATH_NOT_FOUND;
        obj_unref(&f->o);
        return INVALID_HANDLE_VALUE;
    }
    if (!find_next(f, out)) {
        CloseHandle(f);
        last_error = ERROR_FILE_NOT_FOUND;
        return INVALID_HANDLE_VALUE;
    }
    return f;
}
BOOL FindNextFileA(HANDLE h, WIN32_FIND_DATAA *out) { return is_obj(h, K_FIND) && find_next((FindObj *)h, out); }
BOOL FindClose(HANDLE h) { return CloseHandle(h); }

/* ---- modules: a Windows name read as its Linux one (SDL3.dll, vulkan-1.dll, <name>.dll -> lib<name>.so) ---- */
DWORD GetModuleFileNameA(HMODULE m, LPSTR out, DWORD cap) {
    if (!cap) return 0;
    char p[PATH_MAX];
    ssize_t n = -1;
    Dl_info info;
    if (m && dladdr(m, &info) && info.dli_fname) {
        snprintf(p, sizeof p, "%s", info.dli_fname);
        n = (ssize_t)strlen(p);
    } else {
        n = readlink("/proc/self/exe", p, sizeof p - 1);
    }
    if (n < 0) { fail(); out[0] = 0; return 0; }
    if ((DWORD)n >= cap) n = (ssize_t)cap - 1;
    memcpy(out, p, (size_t)n);
    out[n] = 0;
    return (DWORD)n;
}
HMODULE GetModuleHandleA(LPCSTR name) {
    if (!name) return dlopen(NULL, RTLD_NOW);
    return NULL;   /* (kernel32.dll, user32.dll ...: none here; GetProcAddress(NULL) answers NULL) */
}
BOOL GetModuleHandleExA(DWORD flags, LPCSTR name, HMODULE *out) {
    Dl_info info;
    if ((flags & GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS) && dladdr(name, &info)) {
        *out = info.dli_fbase;
        return TRUE;
    }
    *out = NULL;
    return FALSE;
}
static const char *linux_name(const char *in, char *out) {
    char p[PATH_MAX];
    native(in, p);
    const char *base = strrchr(p, '/');
    const size_t dir = base ? (size_t)(base + 1 - p) : 0;
    base = base ? base + 1 : p;
    static const struct { const char *win, *lin; } known[] = {
        {"sdl3.dll", "libSDL3.so.0"}, {"vulkan-1.dll", "libvulkan.so.1"}};
    const size_t n = strlen(base);
    if (n < 4 || strcasecmp(base + n - 4, ".dll")) return strcpy(out, p);
    for (unsigned i = 0; i < sizeof known / sizeof *known; i++)
        if (!strcasecmp(base, known[i].win)) {   /* (a system library: found on the loader's path, not beside us) */
            snprintf(out, PATH_MAX, "%s", known[i].lin);
            return out;
        }
    snprintf(out, PATH_MAX, "%.*slib%.*s.so", (int)dir, p, (int)(n - 4), base);
    return out;
}
HMODULE LoadLibraryA(LPCSTR name) {
    char p[PATH_MAX];
    void *h = dlopen(linux_name(name, p), RTLD_NOW | RTLD_LOCAL);
    if (!h) last_error = ERROR_FILE_NOT_FOUND;
    return h;
}
FARPROC GetProcAddress(HMODULE m, LPCSTR name) { return m ? (FARPROC)dlsym(m, name) : NULL; }
BOOL FreeLibrary(HMODULE m) { return m && dlclose(m) == 0; }

/* ---- text: UTF-8 and wchar_t (UTF-32 here) ---- */
int MultiByteToWideChar(UINT cp, DWORD flags, LPCSTR in, int n, LPWSTR out, int cap) {
    (void)cp, (void)flags;
    const unsigned char *s = (const unsigned char *)in;
    const size_t len = n < 0 ? strlen(in) + 1 : (size_t)n;
    int k = 0;
    for (size_t i = 0; i < len;) {
        uint32_t c = s[i++];
        int more = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
        if (more) c &= 0x3F >> more;
        while (more-- && i < len) c = c << 6 | (s[i++] & 0x3F);
        if (cap) {
            if (k >= cap) { last_error = ERROR_INSUFFICIENT_BUFFER; return 0; }
            out[k] = (wchar_t)c;
        }
        k++;
    }
    return k;
}
int WideCharToMultiByte(UINT cp, DWORD flags, LPCWSTR in, int n, LPSTR out, int cap, LPCSTR def, BOOL *used) {
    (void)cp, (void)flags, (void)def;
    if (used) *used = FALSE;
    const size_t len = n < 0 ? wcslen(in) + 1 : (size_t)n;
    int k = 0;
    for (size_t i = 0; i < len; i++) {
        const uint32_t c = (uint32_t)in[i];
        unsigned char b[4];
        int m = 0;
        if (c < 0x80) b[m++] = (unsigned char)c;
        else if (c < 0x800) b[m++] = (unsigned char)(0xC0 | c >> 6), b[m++] = (unsigned char)(0x80 | (c & 0x3F));
        else if (c < 0x10000) b[m++] = (unsigned char)(0xE0 | c >> 12), b[m++] = (unsigned char)(0x80 | (c >> 6 & 0x3F)),
                              b[m++] = (unsigned char)(0x80 | (c & 0x3F));
        else b[m++] = (unsigned char)(0xF0 | c >> 18), b[m++] = (unsigned char)(0x80 | (c >> 12 & 0x3F)),
             b[m++] = (unsigned char)(0x80 | (c >> 6 & 0x3F)), b[m++] = (unsigned char)(0x80 | (c & 0x3F));
        if (cap) {
            if (k + m > cap) {   /* (what fits, ended: a thread name cut short is fine) */
                if (k < cap) out[k] = 0;
                last_error = ERROR_INSUFFICIENT_BUFFER;
                return 0;
            }
            memcpy(out + k, b, (size_t)m);
        }
        k += m;
    }
    return k;
}

/* ---- waveOut on SDL's audio: the blocks written are played in order by SDL's callback, each marked done when it
   has been played out, as waveOut does ---- */
typedef struct {
    SDL_AudioStream *stream;
    SDL_Mutex *m;
    WAVEHDR *head, *tail;   /* queued, the playing one first */
    DWORD pos;              /* bytes of head played */
    Uint8 silence;
} WaveOut;
/* (SDL asks for `want` more bytes when its device runs low: put in from the blocks, a block's end as it comes) */
static void wave_cb(void *user, SDL_AudioStream *stream, int want, int total) {
    (void)total;
    WaveOut *w = (WaveOut *)user;
    SDL_LockMutex(w->m);
    while (want > 0) {
        WAVEHDR *h = w->head;
        if (!h) {   /* (behind: silence) */
            Uint8 quiet[1024];
            memset(quiet, w->silence, sizeof quiet);
            for (; want > 0; want -= (int)sizeof quiet)
                SDL_PutAudioStreamData(stream, quiet, want < (int)sizeof quiet ? want : (int)sizeof quiet);
            break;
        }
        const DWORD n = h->dwBufferLength - w->pos < (DWORD)want ? h->dwBufferLength - w->pos : (DWORD)want;
        SDL_PutAudioStreamData(stream, h->lpData + w->pos, (int)n);
        want -= (int)n, w->pos += n;
        if (w->pos == h->dwBufferLength) {
            w->head = h->lpNext;
            if (!w->head) w->tail = NULL;
            w->pos = 0;
            __atomic_store_n(&h->dwFlags, (h->dwFlags & ~(DWORD)WHDR_INQUEUE) | WHDR_DONE, __ATOMIC_RELEASE);
        }
    }
    SDL_UnlockMutex(w->m);
}
MMRESULT waveOutOpen(HWAVEOUT *out, UINT device, const WAVEFORMATEX *f, DWORD_PTR cb, DWORD_PTR inst, DWORD flags) {
    (void)device, (void)cb, (void)inst, (void)flags;
    *out = NULL;
    void hos_sdl_identity(void);   /* (display_sdl.c: the name the mixer shows) */
    hos_sdl_identity();
    SDL_SetHint(SDL_HINT_AUDIO_DEVICE_SAMPLE_FRAMES, "256");   /* (the small device buffer SDL2's spec asked for) */
    if (!SDL_WasInit(SDL_INIT_AUDIO) && !SDL_InitSubSystem(SDL_INIT_AUDIO)) return MMSYSERR_ERROR;
    WaveOut *w = (WaveOut *)calloc(1, sizeof *w);
    w->m = SDL_CreateMutex();
    const SDL_AudioSpec spec = {f->wBitsPerSample == 16 ? SDL_AUDIO_S16 : SDL_AUDIO_U8, (int)f->nChannels,
                                (int)f->nSamplesPerSec};
    w->silence = (Uint8)SDL_GetSilenceValueForFormat(spec.format);
    w->stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, wave_cb, w);
    if (!w->stream) {
        SDL_DestroyMutex(w->m);
        free(w);
        return MMSYSERR_ERROR;
    }
    SDL_ResumeAudioStreamDevice(w->stream);
    *out = w;
    return MMSYSERR_NOERROR;
}
MMRESULT waveOutPrepareHeader(HWAVEOUT w, WAVEHDR *h, UINT size) {
    (void)w, (void)size;
    h->dwFlags |= WHDR_PREPARED;
    return MMSYSERR_NOERROR;
}
MMRESULT waveOutUnprepareHeader(HWAVEOUT w, WAVEHDR *h, UINT size) {
    (void)w, (void)size;
    h->dwFlags &= ~(DWORD)WHDR_PREPARED;
    return MMSYSERR_NOERROR;
}
MMRESULT waveOutWrite(HWAVEOUT hw, WAVEHDR *h, UINT size) {
    (void)size;
    WaveOut *w = (WaveOut *)hw;
    if (!w) return MMSYSERR_ERROR;
    SDL_LockMutex(w->m);
    h->dwFlags = (h->dwFlags & ~(DWORD)WHDR_DONE) | WHDR_INQUEUE;
    h->lpNext = NULL;
    if (w->tail) w->tail->lpNext = h;
    else w->head = h;
    w->tail = h;
    SDL_UnlockMutex(w->m);
    return MMSYSERR_NOERROR;
}
MMRESULT waveOutReset(HWAVEOUT hw) {
    WaveOut *w = (WaveOut *)hw;
    if (!w) return MMSYSERR_ERROR;
    SDL_LockMutex(w->m);
    for (WAVEHDR *h = w->head; h; h = h->lpNext) h->dwFlags = (h->dwFlags & ~(DWORD)WHDR_INQUEUE) | WHDR_DONE;
    w->head = w->tail = NULL;
    w->pos = 0;
    SDL_UnlockMutex(w->m);
    return MMSYSERR_NOERROR;
}
MMRESULT waveOutClose(HWAVEOUT hw) {
    WaveOut *w = (WaveOut *)hw;
    if (!w) return MMSYSERR_ERROR;
    SDL_DestroyAudioStream(w->stream);   /* (its device closed with it) */
    SDL_DestroyMutex(w->m);
    free(w);
    return MMSYSERR_NOERROR;
}

/* ---- psapi.h ---- */
#include <psapi.h>
BOOL K32GetProcessMemoryInfo(HANDLE process, PROCESS_MEMORY_COUNTERS *out, DWORD size) {
    (void)process;
    if (size < sizeof *out) return FALSE;
    memset(out, 0, size);
    out->cb = size;
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return fail();
    char line[128];
    unsigned long long kb;
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "VmRSS: %llu", &kb) == 1) out->WorkingSetSize = (SIZE_T)(kb << 10);
        else if (sscanf(line, "VmHWM: %llu", &kb) == 1) out->PeakWorkingSetSize = (SIZE_T)(kb << 10);
        else if (sscanf(line, "RssAnon: %llu", &kb) == 1 && size >= sizeof(PROCESS_MEMORY_COUNTERS_EX))
            ((PROCESS_MEMORY_COUNTERS_EX *)out)->PrivateUsage = (SIZE_T)(kb << 10);
    }
    fclose(f);
    return TRUE;
}
