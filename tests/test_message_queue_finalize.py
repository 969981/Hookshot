"""A message queue the game has finalized keeps its room (host/nn_os_sync.c): a send after FinalizeMessageQueue goes in
as on Horizon, where the SDK's object keeps its count and buffer, instead of sleeping for good on a queue with no room.

The object table (lookups, tombstones, an address brought back), the queue's functions and its handlers are cut out of
production unmodified and run against stubs; the one sleep a full queue would take fails the check instead:

  late send   Initialize (4 entries), Send, Receive, Finalize, then Send and TrySend: both go in, and Receive reads
              them in order. (Before: the finalize left a tombstone, the next lookup brought it back with no room, and
              Send slept: Kirby Air Riders' movie player sends a frame tick from its timer's callback after finalizing
              its video thread's queue, and the player then joins that callback's thread.)
  light       the same for a LightMessageQueue.
  again       the address initialized again is a new, empty queue with the new size.
  reused      the address used for another kind of object (a semaphore) is that object; as a queue again, new.

python -I -B tests/test_message_queue_finalize.py [--cc gcc]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]
ENV_ON_H = str(ROOT / "gpu" / "vk" / "compat" / "hookshot_env.h")

HEAD = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <stdarg.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef long LONG;
typedef unsigned long DWORD;
#define INFINITE 0xFFFFFFFFu
typedef struct { int x; } SRWLOCK;
typedef struct { int x; } CONDITION_VARIABLE;
#define SRWLOCK_INIT {0}
static void InitializeSRWLock(SRWLOCK *l) { (void)l; }
static void InitializeConditionVariable(CONDITION_VARIABLE *v) { (void)v; }
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void AcquireSRWLockShared(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockShared(SRWLOCK *l) { (void)l; }
static void WakeAllConditionVariable(CONDITION_VARIABLE *v) { (void)v; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
typedef struct HosThread HosThread;
static HosThread *hos_self(void) { return NULL; }
static const char *hos_thread_name(HosThread *t) { (void)t; return "test"; }
static void hos_log(const char *f, ...) { (void)f; }
static void hos_fatal(void *c, const char *f, ...) { (void)c; fprintf(stderr, "FAIL: hos_fatal %s\n", f); exit(1); }
static int64_t hos_now_ns(void) { static int64_t t; return t += 1000; }
static void hos_vcore_checkpoint(void) {}
/* (a full queue's send, or an empty one's receive, would sleep here with no one to wake it: the check fails) */
static void hos_cv_sleep(CONDITION_VARIABLE *v, SRWLOCK *l, DWORD ms) {
    (void)v; (void)l; (void)ms;
    fprintf(stderr, "FAIL: a queue call slept for good\n");
    exit(1);
}
static int hos_chain_on;
enum { HOS_CHAIN_SIGNAL, HOS_CHAIN_WAIT, HOS_CHAIN_WOKE };
static void hos_chain_note(int what, uint64_t key, int x) { (void)what; (void)key; (void)x; }
#define HOS_CHAIN(what, key) ((void)0)
typedef struct { uint64_t x[32]; } Ctx;
#define HLE(sym) static void sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
static uint64_t guest[64];
#define G64(a) (guest[(a) / 8])
'''

TAIL = r'''
static void poke_multiwait(Obj *o) { (void)o; }
static void spin_until_set(Obj *o, volatile int *flag) { (void)o; (void)flag; }
static int wait_until(Obj *o, int64_t deadline) { (void)o; (void)deadline; return 0; }
static int wake_lat_on(void) { return 0; }
static void wake_lat_note(int how, int64_t lat_ns) { (void)how; (void)lat_ns; }
'''

MAIN = r'''
#define Q 0x1000
static uint64_t r;
#define CALL(fn, a0, a1, a2) do { Ctx cc = {{0}}; cc.x[0] = (a0); cc.x[1] = (a1); cc.x[2] = (a2); fn(&cc); r = cc.x[0]; } while (0)
static uint64_t recv(void) { CALL(_ZN2nn2os19ReceiveMessageQueueEPmPNS0_16MessageQueueTypeE, 8, Q, 0); return G64(8); }
static uint64_t recv_light(void) { CALL(_ZN2nn2os24ReceiveLightMessageQueueEPmPNS0_21LightMessageQueueTypeE, 8, Q, 0); return G64(8); }
int main(void) {
    /* late send */
    CALL(_ZN2nn2os22InitializeMessageQueueEPNS0_16MessageQueueTypeEPmm, Q, 0x2000, 4);
    CALL(_ZN2nn2os16SendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 10, 0);
    CHECK(recv() == 10);
    CALL(_ZN2nn2os20FinalizeMessageQueueEPNS0_16MessageQueueTypeE, Q, 0, 0);
    CALL(_ZN2nn2os16SendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 9, 0);
    CALL(_ZN2nn2os19TrySendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 9, 0);
    CHECK(r == 1);
    CHECK(recv() == 9 && recv() == 9);
    /* again: a new, empty queue of the new size */
    CALL(_ZN2nn2os22InitializeMessageQueueEPNS0_16MessageQueueTypeEPmm, Q, 0x2000, 2);
    CALL(_ZN2nn2os22TryReceiveMessageQueueEPmPNS0_16MessageQueueTypeE, 8, Q, 0);
    CHECK(r == 0);
    CALL(_ZN2nn2os19TrySendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 1, 0); CHECK(r == 1);
    CALL(_ZN2nn2os19TrySendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 2, 0); CHECK(r == 1);
    CALL(_ZN2nn2os19TrySendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 3, 0); CHECK(r == 0);   /* (full at 2) */
    CHECK(recv() == 1 && recv() == 2);
    CALL(_ZN2nn2os20FinalizeMessageQueueEPNS0_16MessageQueueTypeE, Q, 0, 0);
    /* reused: the same memory as a semaphore, then a queue again */
    CALL(_ZN2nn2os19InitializeSemaphoreEPNS0_13SemaphoreTypeEii, Q, 1, 1);
    CALL(_ZN2nn2os19TryAcquireSemaphoreEPNS0_13SemaphoreTypeE, Q, 0, 0); CHECK(r == 1);
    CALL(_ZN2nn2os19TryAcquireSemaphoreEPNS0_13SemaphoreTypeE, Q, 0, 0); CHECK(r == 0);
    CALL(_ZN2nn2os17FinalizeSemaphoreEPNS0_13SemaphoreTypeE, Q, 0, 0);
    CALL(_ZN2nn2os22InitializeMessageQueueEPNS0_16MessageQueueTypeEPmm, Q, 0x2000, 3);
    CALL(_ZN2nn2os16SendMessageQueueEPNS0_16MessageQueueTypeEm, Q, 7, 0);
    CHECK(recv() == 7);
    CALL(_ZN2nn2os20FinalizeMessageQueueEPNS0_16MessageQueueTypeE, Q, 0, 0);
    /* light */
    CALL(_ZN2nn2os27InitializeLightMessageQueueEPNS0_21LightMessageQueueTypeEPmm, Q, 0x2000, 2);
    CALL(_ZN2nn2os25FinalizeLightMessageQueueEPNS0_21LightMessageQueueTypeE, Q, 0, 0);
    CALL(_ZN2nn2os21SendLightMessageQueueEPNS0_21LightMessageQueueTypeEm, Q, 5, 0);
    CALL(_ZN2nn2os24TrySendLightMessageQueueEPNS0_21LightMessageQueueTypeEm, Q, 6, 0);
    CHECK(r == 1);
    CHECK(recv_light() == 5 && recv_light() == 6);
    printf("PASS: a finalized message queue keeps its room: a late send goes in, as on Horizon\n");
    return 0;
}
'''


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def handler(text, name):
    m = re.search(r"^HLE\((_ZN2nn2os\d+%s\w*)\)" % name, text, re.M)
    if not m:
        raise SystemExit("FAIL: no handler for nn::os::%s" % name)
    return block(text[m.start():], m.group(0))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = p.parse_args()
    src = (ROOT / "host" / "nn_os_sync.c").read_text(encoding="utf-8")
    parts = [re.search(r"^enum \{ K_MUTEX = 1[^}]*\};", src, re.M).group(0),
             src[src.index("typedef struct Obj {"):src.index("} Obj;") + len("} Obj;")],
             "static int wake_lat_on(void);",
             src[src.index("static SRWLOCK table_lock"):src.index("/* Wait on o->cv until")],
             TAIL,
             block(src, "static void queue_init("), block(src, "static void queue_trace("),
             block(src, "static int queue_put("), block(src, "static int queue_get("),
             block(src, "static void sema_init(")]
    for name in ("InitializeMessageQueueEPNS0_16MessageQueueTypeEPmm", "FinalizeMessageQueue", "SendMessageQueue",
                 "TrySendMessageQueue", "ReceiveMessageQueue", "TryReceiveMessageQueue",
                 "InitializeLightMessageQueue", "FinalizeLightMessageQueue", "SendLightMessageQueue",
                 "TrySendLightMessageQueue", "ReceiveLightMessageQueue",
                 "InitializeSemaphore", "TryAcquireSemaphore", "FinalizeSemaphore"):
        parts.append(handler(src, name))
    with tempfile.TemporaryDirectory(prefix="mq-finalize-") as tmp:
        c, exe = Path(tmp, "f.c"), Path(tmp, "f")
        c.write_text(HEAD + '#include "%s"\n' % ENV_ON_H.replace("\\", "/") + "\n".join(parts) + MAIN, encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                        "-o", str(exe), str(c)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
