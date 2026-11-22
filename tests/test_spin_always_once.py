"""HOS_SPIN_ALWAYS, the switch that turns the adaptive spin off, is read once, by one thread, game-free.

host/nn_os_sync.c's spin_begin (x86-64: before an event, queue or mutex wait sleeps) skips the spin on an object whose
spins keep missing (4 misses: one wait in 32 spins) unless HOS_SPIN_ALWAYS is on. It reads the switch at the first spin.
The fixture runs the real spin_begin cut from the source (tests/srccut.py), with hookshot_env.h's env_on, over a stub
spin length (hos_spin_ns: 20 us); getenv is the fixture's, which counts the reads of the switch and holds each read until
every racing thread has reached the cold lock or getenv. A missed rendezvous fails the fixture. One child process
per case, with HOS_SPIN_ALWAYS set in the child's environment:

  - unset, 0, off: adaptive: an object with 4 misses spins at 2 of 64 waits; 1, yes: every one of the 64; the switch read
    once;
  - repeat: the variable changed after the first spin (unset to 1, 1 to 0) changes nothing, still one read;
  - race: 8 threads spinning for the first time at once, with the switch unset and with it 1: the switch read once, and
    each thread spins as it says (on main each of the 8 read it and stored the cached flag, a plain int, at once).

Skipped (exit 0) on a host that is not x86-64 (spin_begin is x86-64 code).

For a Linux x86-64 validation job with a compiler and ThreadSanitizer runtime installed, pass --tsan to run the race
under ThreadSanitizer and require that a plain outer load is detected as a data race. A missing or unusable sanitizer
fails that invocation; the default suite does not require it.

python -I -B tests/test_spin_always_once.py [--cc <C compiler>] [--tsan]   (default compiler: $CC, else on Windows
    HOOKSHOT_MINGW's or the clone's llvm-mingw clang, else clang, gcc or cc on PATH)
"""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import between  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SWITCH = "HOS_SPIN_ALWAYS"


def compiler(given):
    """--cc, else $CC, else the configured llvm-mingw's clang (HOOKSHOT_MINGW, Windows: run_all.py sets it to the
    clone's folder), else clang, gcc or cc on PATH"""
    for c in (given, os.environ.get("CC")):
        if c:
            return shutil.which(c) or c
    if os.name == "nt":
        for mingw in (os.environ.get("HOOKSHOT_MINGW", ""), str(ROOT / "llvm-mingw")):
            if mingw and os.path.isfile(os.path.join(mingw, "bin", "clang.exe")):
                return os.path.join(mingw, "bin", "clang.exe")
    for n in ("clang", "gcc", "cc"):
        if shutil.which(n):
            return shutil.which(n)
    raise SystemExit("test_spin_always_once: no C compiler (pass --cc or set CC)")


FIXTURE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>   /* (Linux: host/posix's) */
#ifndef _WIN32
#include <pthread.h>
#include <time.h>
#endif
static uint64_t now_ms(void) {
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
#endif
}
/* Count each worker once, at the cold lock or (on the old implementation) getenv. Relaxed atomics do not
   establish a happens-before edge between the outer cache load and publication in the sanitizer run. */
static volatile long switch_reads;
static long cold_arrived, rendezvous_failed;
static _Thread_local int cold_seen;
static int threads_n = 1;
static void cold_arrival(void) {
    if (!cold_seen) {
        cold_seen = 1;
        __atomic_add_fetch(&cold_arrived, 1, __ATOMIC_RELAXED);
    }
}
static void fixture_lock(SRWLOCK *lock) {
    cold_arrival();
    AcquireSRWLockExclusive(lock);
}
static char *fixture_getenv(const char *name) {
    if (!strcmp(name, "HOS_SPIN_ALWAYS")) {
        cold_arrival();
        __atomic_add_fetch(&switch_reads, 1, __ATOMIC_RELAXED);
        const uint64_t t0 = now_ms();
        while (__atomic_load_n(&cold_arrived, __ATOMIC_RELAXED) < threads_n) {
            if (now_ms() - t0 >= 10000) {
                __atomic_store_n(&rendezvous_failed, 1, __ATOMIC_RELAXED);
                break;
            }
        }
    }
    return getenv(name);
}
#define getenv fixture_getenv
#include "hookshot_env.h"
typedef struct Obj { int spin_misses, spin_waits; } Obj;
static int64_t hos_spin_ns(void) { return 20000; }
#define AcquireSRWLockExclusive fixture_lock
@SPIN@
#undef AcquireSRWLockExclusive
#undef getenv

/* 64 waits on an object whose spins keep missing: how many spin */
static int spins(void) {
    Obj o = {4, 0};
    int n = 0, misses;
    for (int i = 0; i < 64; i++) n += spin_begin(&o, &misses);
    return n;
}
static int set_switch(const char *v) {
#ifdef _WIN32
    char b[64];
    snprintf(b, sizeof b, "HOS_SPIN_ALWAYS=%s", v);
    return _putenv(b);
#else
    return setenv("HOS_SPIN_ALWAYS", v, 1);
#endif
}
static volatile long arrived;
static int setup_failed;
static int results[8];
#ifdef _WIN32
static DWORD WINAPI racer(void *arg)
#else
static void *racer(void *arg)
#endif
{
    const int i = (int)(intptr_t)arg;
    __atomic_fetch_add(&arrived, 1, __ATOMIC_SEQ_CST);
    while (__atomic_load_n(&arrived, __ATOMIC_SEQ_CST) < threads_n) {
        if (__atomic_load_n(&setup_failed, __ATOMIC_SEQ_CST)) return 0;
    }
    results[i] = spins();
    return 0;
}
int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "";
    const int want = argc > 2 ? atoi(argv[2]) : -1;   /* (spins of 64 expected: 2 adaptive, 64 always) */
    int got = -1, failed = 0;
    if (!strcmp(mode, "spins")) {
        got = spins();
        failed = got != want;
    } else if (!strcmp(mode, "repeat")) {
        got = spins();
        if (set_switch(argc > 3 ? argv[3] : "1")) {
            fprintf(stderr, "FAIL: could not update HOS_SPIN_ALWAYS\n");
            return 2;
        }
        const int again = spins();
        failed = got != want || again != want;
        if (again != want) fprintf(stderr, "  after the variable changed: %d of 64 spun\n", again);
    } else if (!strcmp(mode, "race")) {
        enum { N = 8 };
        threads_n = N;
#ifdef _WIN32
        HANDLE t[N];
#else
        pthread_t t[N];
#endif
        int created = 0;
        for (int i = 0; i < N; i++) {
#ifdef _WIN32
            t[i] = CreateThread(NULL, 0, racer, (void *)(intptr_t)i, 0, NULL);
            if (!t[i]) {
                fprintf(stderr, "FAIL: CreateThread %d failed (error %lu)\n", i, (unsigned long)GetLastError());
#else
            int err = pthread_create(&t[i], NULL, racer, (void *)(intptr_t)i);
            if (err) {
                fprintf(stderr, "FAIL: pthread_create %d: %s\n", i, strerror(err));
#endif
                __atomic_store_n(&setup_failed, 1, __ATOMIC_SEQ_CST);
                break;
            }
            created++;
        }
        for (int i = 0; i < created; i++) {
#ifdef _WIN32
            if (WaitForSingleObject(t[i], INFINITE) != WAIT_OBJECT_0) {
                fprintf(stderr, "FAIL: thread wait %d failed (error %lu)\n", i, (unsigned long)GetLastError());
                return 2;
            }
            if (!CloseHandle(t[i])) {
                fprintf(stderr, "FAIL: CloseHandle %d failed (error %lu)\n", i, (unsigned long)GetLastError());
                return 2;
            }
#else
            int err = pthread_join(t[i], NULL);
            if (err) {
                fprintf(stderr, "FAIL: pthread_join %d: %s\n", i, strerror(err));
                return 2;
            }
#endif
        }
        if (created != N) return 2;
        if (__atomic_load_n(&rendezvous_failed, __ATOMIC_RELAXED)) {
            fprintf(stderr, "FAIL: cold-path rendezvous missed (%ld of %d workers)\n",
                    __atomic_load_n(&cold_arrived, __ATOMIC_RELAXED), N);
            return 2;
        }
        got = results[0];
        for (int i = 0; i < N; i++) failed |= results[i] != want;
    } else {
        fprintf(stderr, "unknown mode %s\n", mode);
        return 2;
    }
    if (switch_reads != 1) {
        fprintf(stderr, "  HOS_SPIN_ALWAYS was read %ld times\n", switch_reads);
        failed = 1;
    }
    if (failed) {
        fprintf(stderr, "FAIL: %s: %d of 64 waits spun (expected %d), the switch read %ld times (expected once)\n", mode,
                got, want, switch_reads);
        return 1;
    }
    printf("PASS: %s: %d of 64 waits spun, the switch read once\n", mode, got);
    return 0;
}
'''

ADAPTIVE, ALWAYS = "2", "64"
# (name, HOS_SPIN_ALWAYS in the child's environment: None unset, the fixture's arguments)
CASES = (
    ("unset", None, ("spins", ADAPTIVE)),
    ("0", "0", ("spins", ADAPTIVE)),
    ("off", "off", ("spins", ADAPTIVE)),
    ("1", "1", ("spins", ALWAYS)),
    ("yes", "yes", ("spins", ALWAYS)),
    ("repeat: unset, then 1", None, ("repeat", ADAPTIVE, "1")),
    ("repeat: 1, then 0", "1", ("repeat", ALWAYS, "0")),
    ("race, unset", None, ("race", ADAPTIVE)),
    ("race, 1", "1", ("race", ALWAYS)),
)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cc", help="C compiler (default: $CC, else llvm-mingw on Windows, else clang, gcc or cc)")
    p.add_argument("--tsan", action="store_true", help="require ThreadSanitizer and its plain-load control (Linux x86-64)")
    a = p.parse_args()
    if a.tsan and (not sys.platform.startswith("linux") or platform.machine().lower() not in ("amd64", "x86_64")):
        p.error("--tsan requires Linux x86-64")
    if platform.machine().lower() not in ("amd64", "x86_64"):
        print("test_spin_always_once: skipped (spin_begin is x86-64 code; this host is %s)" % platform.machine())
        return
    cc = compiler(a.cc)
    src = (ROOT / "host" / "nn_os_sync.c").read_text(encoding="utf-8")
    spin = between(src, "#include <emmintrin.h>\n", "static void spin_end(")   # (spin_begin and what it reads)
    failed = []
    with tempfile.TemporaryDirectory(prefix="spin-always-once-") as tmp:
        c = Path(tmp) / "fixture.c"
        exe = Path(tmp) / "fixture.exe"
        c.write_text(FIXTURE.replace("@SPIN@", spin), encoding="utf-8")
        cmd = [cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Wno-unused-function", "-I", str(ROOT / "gpu" / "vk" / "compat")]
        if os.name != "nt":
            cmd += ["-I", str(ROOT / "host" / "posix"), "-pthread"]
        subprocess.run(cmd + [str(c), "-o", str(exe)], check=True)
        for name, value, args in CASES:
            env = {k: v for k, v in os.environ.items() if k != SWITCH}
            if value is not None:
                env[SWITCH] = value
            r = subprocess.run([str(exe)] + list(args), env=env, capture_output=True, text=True, timeout=60)
            out = (r.stdout + r.stderr).strip()
            print("%s: %s" % (name, out))
            if r.returncode:
                failed.append(name)
        if a.tsan:
            outer = "int adaptive = __atomic_load_n(&spin_adaptive, __ATOMIC_RELAXED);"
            if spin.count(outer) != 1:
                raise SystemExit("test_spin_always_once: cannot make the plain-load sanitizer control")
            for label, source in (("atomic", spin), ("plain-load control", spin.replace(outer, "int adaptive = spin_adaptive;"))):
                c.write_text(FIXTURE.replace("@SPIN@", source), encoding="utf-8")
                tsan_cmd = [cc, "-std=gnu11", "-O1", "-g", "-fsanitize=thread", "-fno-omit-frame-pointer",
                            "-I", str(ROOT / "gpu" / "vk" / "compat"), "-I", str(ROOT / "host" / "posix"), "-pthread"]
                subprocess.run(tsan_cmd + [str(c), "-o", str(exe)], check=True)
                env = {k: v for k, v in os.environ.items() if k != SWITCH}
                env["TSAN_OPTIONS"] = "halt_on_error=1:exitcode=66"
                r = subprocess.run([str(exe), "race", ADAPTIVE], env=env, capture_output=True, text=True, timeout=60)
                out = r.stdout + r.stderr
                detected = r.returncode == 66 and "ThreadSanitizer: data race" in out and "spin_adaptive" in out
                ok = r.returncode == 0 if label == "atomic" else detected
                print("ThreadSanitizer %s: %s\n%s" % (label, "PASS" if ok else "FAIL", out.strip()))
                if not ok:
                    failed.append("ThreadSanitizer " + label)
    if failed:
        raise SystemExit("test_spin_always_once: FAILED: %s" % ", ".join(failed))
    print("PASS: every case (%s)" % ", ".join(n for n, _, _ in CASES))


if __name__ == "__main__":
    main()
