"""TimerEvent's Stop, Clear and Signal (host/nn_os_sync.c), in the file's model: a due time (0: not started) and a period.

The timer handlers are cut out of production unmodified and run against a stand-in object and clock:

  stop      a periodic timer stopped is not signalled, and stays so as time passes (a wait waits for the next start).
  clear     a periodic timer that fired is not signalled after Clear, and fires again a period later; a one-shot that
            fired stops.
  signal    a timer not started, or due later, is signalled at once by Signal; a periodic one goes on from there.

Left to the lifted SDK, StopTimerEvent read the SDK's own layout of the event, which this file never fills: Kirby Air
Riders' movie player faulted at guest 0x10.

python -I -B tests/test_timer_event_stop.py [--cc gcc]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]

FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { uint64_t x[32]; } Ctx;
#define HLE(sym) static void sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
typedef int SRWLOCK, CONDITION_VARIABLE;
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void WakeAllConditionVariable(CONDITION_VARIABLE *v) { (void)v; }
enum { K_TIMER = 3 };
typedef struct { SRWLOCK lk; CONDITION_VARIABLE cv; int64_t due_ns, period_ns; int signaled, autoclear; } Obj;
static Obj the;
static Obj *obj(uint64_t key, int kind) { (void)key; (void)kind; return &the; }
static int64_t now = 1000000;
static int64_t hos_now_ns(void) { return now; }
static void poke_multiwait(Obj *o) { (void)o; }
@HANDLERS@
#define CALL(fn, a0, a1, a2) do { Ctx c = {{0}}; c.x[0] = (a0); c.x[1] = (a1); c.x[2] = (a2); fn(&c); r = c.x[0]; } while (0)
static uint64_t r;
static int fired(void) {
    CALL(_ZN2nn2os17TryWaitTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    return (int)r;
}
int main(void) {
    /* stop */
    CALL(_ZN2nn2os23StartPeriodicTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanES3_, 64, 100, 100);
    now += 150;
    CHECK(fired());
    CALL(_ZN2nn2os14StopTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    now += 1000;
    CHECK(!fired() && the.due_ns == 0);
    /* clear: periodic */
    CALL(_ZN2nn2os23StartPeriodicTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanES3_, 64, 100, 100);
    now += 120;
    CALL(_ZN2nn2os15ClearTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    CHECK(!fired());
    now += 100;
    CHECK(fired());
    /* clear: one-shot */
    CALL(_ZN2nn2os22StartOneShotTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanE, 64, 50, 0);
    now += 60;
    CALL(_ZN2nn2os15ClearTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    CHECK(!fired() && the.due_ns == 0);
    /* signal */
    CALL(_ZN2nn2os14StopTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    CALL(_ZN2nn2os16SignalTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    CHECK(fired());
    CALL(_ZN2nn2os22StartOneShotTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanE, 64, 10000, 0);
    CHECK(!fired());
    CALL(_ZN2nn2os16SignalTimerEventEPNS0_14TimerEventTypeE, 64, 0, 0);
    CHECK(fired());
    printf("PASS: TimerEvent Stop, Clear and Signal in the file's model\n");
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


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = p.parse_args()
    src_text = (ROOT / "host" / "nn_os_sync.c").read_text(encoding="utf-8")
    handlers = "\n".join(block(src_text, "HLE(_ZN2nn2os" + n) for n in (
        "23StartPeriodicTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanES3_)",
        "22StartOneShotTimerEventEPNS0_14TimerEventTypeENS_8TimeSpanE)",
        "17TryWaitTimerEventEPNS0_14TimerEventTypeE)",
        "14StopTimerEventEPNS0_14TimerEventTypeE)",
        "15ClearTimerEventEPNS0_14TimerEventTypeE)",
        "16SignalTimerEventEPNS0_14TimerEventTypeE)"))
    with tempfile.TemporaryDirectory(prefix="timer-event-") as tmp:
        src, exe = Path(tmp, "f.c"), Path(tmp, "f")
        src.write_text(FIXTURE.replace("@HANDLERS@", handlers), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(src)],
                       check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
