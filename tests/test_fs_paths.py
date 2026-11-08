"""The game's file paths stay inside their mounts: no "..", no Windows separators or drive colons, no symbolic links.

A save from someone else is untrusted, and the game builds paths from what it reads (and a save folder unpacked from an
archive can hold symbolic links). host/nn_fs.c refuses a guest path whose part inside its mount is not plain names, and
one that goes through a symbolic link below the mount's folder (the folder itself may be one: the player's choice);
DeleteDirectoryRecursively removes a link it meets, never what the link names.

The fixture is the real host/nn_fs.c and host/posix/win32_posix.c (the Win32 calls on Linux), with a stand-in hos.h
(guest memory is a plain buffer) and stubs for the rest of the OS layer. It mounts a save folder in a temporary
directory and calls the HLE functions as the game does: normal saves work as before, and traversal, absolute parts,
backslashes, colons and links (to a file, to a folder, in the middle of a path, as the file itself) are refused, with
nothing outside the save folder read, written or deleted.

python -I -B tests/test_fs_paths.py [--cc gcc]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOS_H = r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef struct { uint64_t x[32]; } Ctx;
extern uint8_t test_mem[1 << 20];
#define HLE(sym) void imp_##sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
#define GP(a) ((void *)(test_mem + ((uint64_t)(a) & ((1u << 20) - 1))))
#define G64(a) (*(uint64_t *)GP(a))
#define G32(a) (*(uint32_t *)GP(a))
#define HOS_WRITTEN(p, n) ((void)0)
extern int hos_trace;
void hos_log(const char *fmt, ...);
void hos_fatal(Ctx *c, const char *fmt, ...);
int hos_vcore_away(void);
void hos_vcore_back(void);
int64_t hos_now_ns(void);
void hos_sleep_ns(int64_t ns);
'''

MAIN = r'''
#include "hos.h"
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>
#define CHECK(x) ((x) ? (void)0 : (fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x), exit(1)))
uint8_t test_mem[1 << 20];
int hos_trace;
static int refusals;
void hos_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    char b[1024];
    vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    if (strstr(b, "fs: refused")) refusals++;
    fprintf(stderr, "[log] %s\n", b);
}
void hos_fatal(Ctx *c, const char *fmt, ...) { (void)c; fprintf(stderr, "FATAL %s\n", fmt); exit(3); }
int hos_vcore_away(void) { return 0; }
void hos_vcore_back(void) {}
int64_t hos_now_ns(void) { return 0; }
void hos_sleep_ns(int64_t ns) { (void)ns; }
int hos_mods_romfs(const char *rel, char *out, size_t cap) { (void)rel, (void)out, (void)cap; return 0; }
int hos_mods_dir_extras(const char *rel, const char *base, void (*fn)(void *, const char *, int, uint64_t), void *arg) {
    (void)rel, (void)base, (void)fn, (void)arg; return 0; }
int hos_blank_cached_icon(const char *guest, const char *host) { (void)guest, (void)host; return 0; }
int hos_ultrawide_redirect(const char *guest, char *out, size_t cap) { (void)guest, (void)out, (void)cap; return 0; }
extern const char *hos_romfs_dir, *hos_user_dir;

#define F(sym) void imp_##sym(Ctx *c);
F(_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE)
F(_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci)
F(_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm)
F(_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE)
F(_ZN2nn2fs9CloseFileENS0_10FileHandleE)
F(_ZN2nn2fs10CreateFileEPKcl)
F(_ZN2nn2fs10DeleteFileEPKc)
F(_ZN2nn2fs15CreateDirectoryEPKc)
F(_ZN2nn2fs26DeleteDirectoryRecursivelyEPKc)
F(_ZN2nn2fs10RenameFileEPKcS2_)
F(_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc)
F(_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci)

/* guest memory: strings at 0x1000.., the handle at 0x100, buffers at 0x8000 */
static uint64_t str(int slot, const char *s) {
    const uint64_t a = 0x1000 + 0x200 * (uint64_t)slot;
    snprintf((char *)GP(a), 0x200, "%s", s);
    return a;
}
static uint64_t call1(void (*fn)(Ctx *), uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4) {
    Ctx c = {{0}};
    c.x[0] = a0, c.x[1] = a1, c.x[2] = a2, c.x[3] = a3, c.x[4] = a4;
    fn(&c);
    return c.x[0];
}
#define OPEN(path, mode) call1(imp__ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci, 0x100, str(0, path), mode, 0, 0)
#define CREATE(path, n) call1(imp__ZN2nn2fs10CreateFileEPKcl, str(1, path), n, 0, 0, 0)
#define DELETE(path) call1(imp__ZN2nn2fs10DeleteFileEPKc, str(1, path), 0, 0, 0, 0)
#define MKDIR(path) call1(imp__ZN2nn2fs15CreateDirectoryEPKc, str(1, path), 0, 0, 0, 0)
#define RMTREE(path) call1(imp__ZN2nn2fs26DeleteDirectoryRecursivelyEPKc, str(1, path), 0, 0, 0, 0)
#define RENAME(a, b) call1(imp__ZN2nn2fs10RenameFileEPKcS2_, str(1, a), str(2, b), 0, 0, 0)
#define TYPE(path) call1(imp__ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc, 0x180, str(1, path), 0, 0, 0)
#define OPENDIR(path) call1(imp__ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci, 0x188, str(1, path), 3, 0, 0)

static void put(const char *path, const char *text) {
    FILE *f = fopen(path, "w");
    CHECK(f);
    fputs(text, f);
    fclose(f);
}
static void same(const char *path, const char *text) {
    char b[256] = "";
    FILE *f = fopen(path, "r");
    CHECK(f);
    size_t n = fread(b, 1, sizeof b - 1, f);
    fclose(f);
    b[n] = 0;
    if (strcmp(b, text)) { fprintf(stderr, "FAIL %s holds \"%s\", not \"%s\"\n", path, b, text); exit(1); }
}
static int exists(const char *path) { struct stat st; return lstat(path, &st) == 0; }

int main(int argc, char **argv) {
    CHECK(argc == 2);
    const char *t = argv[1];   /* t/user (the user folder), t/outside (what must stay untouched) */
    char user[512], save[600], outside[512], p[900], q[900];
    snprintf(user, sizeof user, "%s/user", t);
    snprintf(outside, sizeof outside, "%s/outside", t);
    mkdir(user, 0755), mkdir(outside, 0755);
    snprintf(p, sizeof p, "%s/secret.txt", outside), put(p, "secret");
    snprintf(p, sizeof p, "%s/keep", outside), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/keep/file", outside), put(p, "keep");
    hos_user_dir = user;
    G64(0x40) = 0x1111, G64(0x48) = 0x2222;   /* the Uid */
    CHECK(call1(imp__ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE, str(3, "save"), 0x40, 0, 0, 0) == 0);
    snprintf(save, sizeof save, "%s/save/%016llx%016llx", user, 0x2222ull, 0x1111ull);

    /* ---- the normal paths work as before */
    CHECK(MKDIR("save:/slot_00") == 0);
    CHECK(CREATE("save:/slot_00/data.sav", 0) == 0);
    CHECK(OPEN("save:/slot_00/data.sav", 3) == 0);
    const uint64_t h = G64(0x100);
    memcpy(GP(0x8000), "hello", 5);
    G64(0x7000) = 0;
    CHECK(call1(imp__ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE, h, 0, 0x8000, 5, 0x7000) == 0);
    call1(imp__ZN2nn2fs9CloseFileENS0_10FileHandleE, h, 0, 0, 0, 0);
    snprintf(p, sizeof p, "%s/slot_00/data.sav", save), same(p, "hello");
    CHECK(OPEN("save://slot_00//data.sav", 1) == 0);   /* (repeated slashes: as before) */
    const uint64_t h2 = G64(0x100);
    CHECK(call1(imp__ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm, 0x7008, h2, 0, 0x9000, 5) == 0 && G64(0x7008) == 5);
    CHECK(!memcmp(GP(0x9000), "hello", 5));
    call1(imp__ZN2nn2fs9CloseFileENS0_10FileHandleE, h2, 0, 0, 0, 0);
    CHECK(OPEN("save:/./slot_00/data.sav", 1) == 0);   /* ("." is the same folder) */
    call1(imp__ZN2nn2fs9CloseFileENS0_10FileHandleE, G64(0x100), 0, 0, 0, 0);
    CHECK(CREATE("save:/slot_00/data..sav", 0) == 0 && OPEN("save:/slot_00/data..sav", 1) == 0);   /* (dots inside a name) */
    call1(imp__ZN2nn2fs9CloseFileENS0_10FileHandleE, G64(0x100), 0, 0, 0, 0);
    CHECK(TYPE("save:/slot_00") == 0 && G32(0x180) == 0);
    CHECK(RENAME("save:/slot_00/data.sav", "save:/slot_00/data2.sav") == 0);
    CHECK(DELETE("save:/slot_00/data2.sav") == 0);
    CHECK(refusals == 0);

    /* ---- traversal, absolute parts, Windows separators and colons: refused, nothing outside touched */
    const char *bad[] = {"save:/../../../outside/secret.txt", "save:/slot_00/../../../../outside/secret.txt", "save:/..",
                         "save:/slot_00/..", "save:/.../x", "save:/. ./x", "save:/slot_00\\..\\..\\x", "save:/C:/x",
                         "save:/x:stream", "save:/../save/slot_00"};
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        CHECK(OPEN(bad[i], 1) != 0);
        CHECK(OPEN(bad[i], 3) != 0);
        CHECK(CREATE(bad[i], 0) != 0);
        CHECK(MKDIR(bad[i]) != 0);
        CHECK(DELETE(bad[i]) != 0);
        CHECK(TYPE(bad[i]) != 0);
        CHECK(RMTREE(bad[i]) != 0);
        CHECK(RENAME("save:/slot_00", bad[i]) != 0);
    }
    CHECK(refusals > 0);
    snprintf(p, sizeof p, "%s/secret.txt", outside), same(p, "secret");
    snprintf(p, sizeof p, "%s/keep/file", outside), same(p, "keep");

    /* ---- symbolic links below the save folder: refused */
    snprintf(p, sizeof p, "%s/secret.txt", outside), snprintf(q, sizeof q, "%s/slot_00/link.sav", save);
    CHECK(symlink(p, q) == 0);   /* a file link */
    CHECK(OPEN("save:/slot_00/link.sav", 1) != 0);
    CHECK(OPEN("save:/slot_00/link.sav", 3) != 0);
    CHECK(TYPE("save:/slot_00/link.sav") != 0);
    snprintf(q, sizeof q, "%s/slot_01", save);
    CHECK(symlink(outside, q) == 0);   /* a folder link, then a file through it */
    CHECK(OPEN("save:/slot_01/secret.txt", 1) != 0);
    CHECK(OPEN("save:/slot_01/secret.txt", 3) != 0);
    CHECK(CREATE("save:/slot_01/new.sav", 0) != 0);
    CHECK(MKDIR("save:/slot_01/newdir") != 0);
    CHECK(DELETE("save:/slot_01/secret.txt") != 0);
    CHECK(OPENDIR("save:/slot_01") != 0);
    CHECK(RENAME("save:/slot_01/secret.txt", "save:/slot_00/stolen.sav") != 0);
    CHECK(RMTREE("save:/slot_01") != 0);
    snprintf(p, sizeof p, "%s/new.sav", outside), CHECK(!exists(p));
    snprintf(p, sizeof p, "%s/newdir", outside), CHECK(!exists(p));
    /* a link deep inside a folder the game deletes: the link goes, what it names stays */
    CHECK(MKDIR("save:/slot_02") == 0);
    CHECK(CREATE("save:/slot_02/a.sav", 0) == 0);
    snprintf(p, sizeof p, "%s/keep", outside), snprintf(q, sizeof q, "%s/slot_02/keep", save);
    CHECK(symlink(p, q) == 0);
    snprintf(p, sizeof p, "%s/secret.txt", outside), snprintf(q, sizeof q, "%s/slot_02/secret", save);
    CHECK(symlink(p, q) == 0);
    CHECK(RMTREE("save:/slot_02") == 0);
    snprintf(p, sizeof p, "%s/slot_02", save), CHECK(!exists(p));
    snprintf(p, sizeof p, "%s/keep/file", outside), same(p, "keep");
    snprintf(p, sizeof p, "%s/secret.txt", outside), same(p, "secret");

    /* ---- the mount's own folder may be a link (a player who keeps saves on another drive) */
    char real[600], linked[600];
    snprintf(real, sizeof real, "%s/elsewhere", t), mkdir(real, 0755);
    snprintf(p, sizeof p, "%s/slot_00", real), mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/slot_00/x.sav", real), put(p, "x");
    G64(0x40) = 0x3333;
    snprintf(linked, sizeof linked, "%s/save/%016llx%016llx", user, 0x2222ull, 0x3333ull);
    CHECK(symlink(real, linked) == 0);
    CHECK(call1(imp__ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE, str(3, "save2"), 0x40, 0, 0, 0) == 0);
    CHECK(OPEN("save2:/slot_00/x.sav", 1) == 0);
    fprintf(stderr, "ok\n");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default=None, help="C compiler (gcc or clang)")
    a = p.parse_args()
    if os.name == "nt":
        print("skipped: the fixture uses the POSIX layer (host/posix/win32_posix.c)")
        return 0
    cc = a.cc or shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
    if not cc:
        print("skipped: no C compiler")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "hos.h").write_text(HOS_H, encoding="utf-8")
        (tmp / "main.c").write_text(MAIN, encoding="utf-8")
        # (nn_fs.c is copied beside the stand-in hos.h, so its #include "hos.h" finds that one)
        shutil.copy(ROOT / "host" / "nn_fs.c", tmp / "nn_fs.c")
        exe = tmp / "fs_paths"
        cmd = [cc, "-O1", "-g", "-w", "-I", str(tmp), "-I", str(ROOT / "host" / "posix"), str(tmp / "main.c"),
               str(tmp / "nn_fs.c"), str(ROOT / "host" / "posix" / "win32_posix.c"), "-o", str(exe), "-lpthread", "-ldl",
               "-lm"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit("the fixture did not build")
        work = tmp / "t"
        work.mkdir()
        r = subprocess.run([str(exe), str(work)], capture_output=True, text=True, timeout=60)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit("a path check failed")
    print("ok: traversal, absolute parts, separators, colons and symbolic links refused; normal saves unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
