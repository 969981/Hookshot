"""The console's users (host/nn_applet.c's nn::account block), against fake guest memory.

The block is cut out of production unmodified and compiled; the guest memory, the log and the clock are fakes.

What this pins down:
  one       with one user (Ocarina's profile, HOS_ACCOUNT_USERS unset) user 1 is the uid there always was, so its
            save folder is the same one; ListAllUsers says one; the selector, with that user open, answers a failure.
  four      HOS_ACCOUNT_USERS=4: four users with four uids and four nicknames; ShowUserSelector hands out the first
            user not open, in turn, and a failure (never a user, never success) once all four are; a closed user
            can be chosen again; GetUserId answers the handle's own uid, not user 1's.
  push      PushOpenUsers keeps the open users and PopOpenUsers gives them back once.
  Ocarina's answers, as this runtime always gave them (both cases): TryOpenPreselectedUser's handle has 1 in its
            third word and OpenUser's 0; OpenUser of a uid that is not a user here succeeds with that uid; GetUserId of
            a handle that names no user answers user 1; the network service token cache has no handler here (the
            generic stub's answer, as before).
  refusals  LoadProfileImage answers a failure and leaves no size behind; ShowUserSelector with no Uid to fill fails;
            PopOpenUsers and ShowUserSelector take null pointers.

python -I -B tests/test_account_users.py [--cc gcc]
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]
CASES = ("one", "four")

FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef int32_t LONG;
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static LONG InterlockedDecrement(volatile LONG *p) { return --*p; }
static LONG InterlockedExchange(volatile LONG *p, LONG v) { LONG o = *p; *p = v; return o; }   /* (for any neighbour) */
typedef struct { uint64_t x[32]; } Ctx;
#define HLE(sym) static void sym(Ctx *c)
static unsigned char memory[4096];
static void *guest_at(uint64_t a) { CHECK(a + 8 <= sizeof memory); return memory + a; }
#define GP(a) guest_at(a)
#define G32(a) (*(uint32_t *)guest_at(a))
#define G64(a) (*(uint64_t *)guest_at(a))
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
static char last_log[512];
static void hos_log(const char *f, ...) { va_list a; va_start(a, f); vsnprintf(last_log, sizeof last_log, f, a); va_end(a); }
@PROFILE@
@BLOCK@
#define CALL(fn, ...) do { Ctx c = {{0}}; uint64_t args[] = {__VA_ARGS__}; \
    for (unsigned i = 0; i < sizeof args / sizeof *args; i++) { c.x[i] = args[i]; } c.x[30] = 0x1234; fn(&c); r = c.x[0]; } while (0)
enum { UID = 64, HANDLE = 128, OUT = 256, LIST = 512, NICK = 1024, SETTINGS = 2048 };
static uint64_t r;
static uint64_t selected(void) {   /* ShowUserSelector's answer: the uid's first word, or 0 when it failed */
    G64(UID) = G64(UID + 8) = 0;
    CALL(_ZN2nn7account16ShowUserSelectorEPNS0_3UidERKNS0_21UserSelectionSettingsE, UID, SETTINGS);
    return r ? 0 : G64(UID);
}
static void open_uid(uint64_t lo, uint64_t hi, uint64_t handle) {
    G64(UID) = lo, G64(UID + 8) = hi;
    CALL(_ZN2nn7account8OpenUserEPNS0_10UserHandleERKNS0_3UidE, handle, UID);
}
int main(int argc, char **argv) {
    const char *name = argc > 1 ? argv[1] : "";
    const uint64_t hi = 0x5a3c9e1f0b7d2468ULL;
    CALL(_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE, HANDLE);
    CHECK(r == 1 && G64(HANDLE) == 1 && G64(HANDLE + 8) == hi && G64(HANDLE + 16) == 1);   /* (user 1: as always) */
    CALL(_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE, OUT, HANDLE);
    CHECK(r == 0 && G64(OUT) == 1 && G64(OUT + 8) == hi);
    /* Ocarina's OpenUser and GetUserId, as this runtime always answered them */
    open_uid(1, hi, HANDLE + 1024);
    CHECK(r == 0 && G64(HANDLE + 1024) == 1 && G64(HANDLE + 1032) == hi && G64(HANDLE + 1040) == 0);
    CALL(_ZN2nn7account9CloseUserERKNS0_10UserHandleE, HANDLE + 1024);
    open_uid(9, hi ^ 1, HANDLE + 1024);   /* (not a user here: opened all the same) */
    CHECK(r == 0 && G64(HANDLE + 1024) == 9 && G64(HANDLE + 1032) == (hi ^ 1) && G64(HANDLE + 1040) == 0);
    CALL(_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE, OUT, HANDLE + 1024);
    CHECK(r == 0 && G64(OUT) == 1 && G64(OUT + 8) == hi);   /* (a handle of no user here: user 1) */
    CALL(_ZN2nn7account16ShowUserSelectorEPNS0_3UidERKNS0_21UserSelectionSettingsE, 0, SETTINGS);
    CHECK(r != 0);   /* (no Uid to fill) */
    CALL(_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi, 0, 0, 8);   /* (nothing to write to: no crash) */
    CALL(_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi, 32, LIST, 8);
    const int users = (int)G32(32);
    if (!strcmp(name, "one")) {
        CHECK(users == 1 && G64(LIST) == 1 && G64(LIST + 8) == hi);
        CHECK(selected() == 0 && r != 0);   /* (the one user is open: a failure, not a user and not success) */
        G64(UID) = 1, G64(UID + 8) = hi;
        CALL(_ZN2nn7account11GetNicknameEPNS0_8NicknameERKNS0_3UidE, NICK, UID);
        CHECK(!strcmp((const char *)GP(NICK), "Player"));
        printf("PASS: account one\n");
        return 0;
    }
    CHECK(users == 4);
    for (int i = 0; i < 4; i++) CHECK(G64(LIST + 16 * i) == 1 + (uint64_t)i && G64(LIST + 16 * i + 8) == hi);
    CALL(_ZN2nn7account12GetUserCountEPi, 32);
    CHECK(r == 0 && G32(32) == 4);
    /* players 2, 3 and 4 each pick: the first user not open, in turn */
    for (int want = 2; want <= 4; want++) {
        const uint64_t got = selected();
        CHECK(got == (uint64_t)want);
        open_uid(got, hi, HANDLE + 24 * (uint64_t)want);
        CHECK(r == 0);
        CALL(_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE, OUT, HANDLE + 24 * (uint64_t)want);
        CHECK(G64(OUT) == (uint64_t)want);   /* (the handle's own uid, not user 1's) */
    }
    G64(UID) = 3, G64(UID + 8) = hi;
    CALL(_ZN2nn7account11GetNicknameEPNS0_8NicknameERKNS0_3UidE, NICK, UID);
    CHECK(!strcmp((const char *)GP(NICK), "Player 3"));
    CALL(_ZN2nn7account16GetUserExistenceEPbRKNS0_3UidE, OUT, UID);
    CHECK(*(uint8_t *)GP(OUT) == 1);
    CHECK(selected() == 0 && r != 0);   /* (all four open) */
    CALL(_ZN2nn7account9CloseUserERKNS0_10UserHandleE, HANDLE + 24 * 3);
    CHECK(selected() == 3);              /* (closed, so it can be chosen again) */
    /* push and pop: the open users (1, 2 and 4 now), once */
    CALL(_ZN2nn7account13PushOpenUsersEv, 0);
    CALL(_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi, 32, LIST, 8);
    CHECK(r == 0 && G32(32) == 3 && G64(LIST) == 1 && G64(LIST + 24) == 2 && G64(LIST + 48) == 4);
    CALL(_ZN2nn7account12PopOpenUsersEPiPNS0_10UserHandleEi, 32, LIST, 8);
    CHECK(r == 0 && G32(32) == 0);
    /* refusals */
    G64(OUT) = 77;
    CALL(_ZN2nn7account16LoadProfileImageEPmPvmRKNS0_3UidE, OUT, NICK, 64, UID);
    CHECK(r != 0 && G64(OUT) == 0);
    G64(UID) = G64(UID + 8) = 0;
    CALL(_ZN2nn7account16ShowUserSelectorEPNS0_3UidERKNS0_21UserSelectionSettingsE, UID, 0);   /* (no settings) */
    CHECK(r == 0 && G64(UID) == 3);
    printf("PASS: account four\n");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    applet = (ROOT / "host" / "nn_applet.c").read_text(encoding="utf-8")
    start = applet.index("/* ---- nn::account: the users")
    end = applet.index("\n/* ---- ", start + 1)   # (to the next section, whichever it is: not past it into others' code)
    block = applet[start:end]
    if "ShowUserSelector" not in block or "GAME_ACCOUNT_USERS" not in block:
        raise SystemExit("host/nn_applet.c: the account block no longer has the user selector or the profile's count")
    if "IdTokenCacheEPmPcmRKNS0_10UserHandleE)" in applet:
        raise SystemExit("host/nn_applet.c: LoadNetworkServiceAccountIdTokenCache has a handler: Ocarina's answer was the "
                         "generic stub's")
    header = (ROOT / "game" / "game_profile.h").read_text(encoding="utf-8")
    profile = re.search(r"^#define GAME_ACCOUNT_USERS [^\n]+", header, re.M).group(0)
    env = {k: v for k, v in os.environ.items() if k != "HOS_ACCOUNT_USERS"}
    with tempfile.TemporaryDirectory(prefix="account-users-") as tmp:
        src, exe = Path(tmp) / "fixture.c", Path(tmp) / "fixture.exe"
        src.write_text(FIXTURE.replace("@PROFILE@", profile).replace("@BLOCK@", block), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-Wno-unused-parameter", str(src), "-o", str(exe)], check=True)
        for case in CASES:
            case_env = dict(env, **({"HOS_ACCOUNT_USERS": "4"} if case == "four" else {}))
            subprocess.run([str(exe), case], env=case_env, check=True)


if __name__ == "__main__":
    main()
