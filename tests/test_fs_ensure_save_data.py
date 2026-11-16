"""nn::fs::EnsureSaveData(Uid const&) is answered: the user's save folder made, success (host/nn_fs.c).

Left to the lifted SDK it asked for the fs service proxy, which the runtime does not have, and read through the null it
got: Donkey Kong Bananza calls it at boot, and with null guest reads faulting it crashed there. The HLE and
MountSaveData are cut from the source and compiled with stand-ins for the folders. Checks: the result is 0; the folder
made is the one MountSaveData mounts for the same user (the uid's two halves, high first); nothing else is made.

python -I -B tests/test_fs_ensure_save_data.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint64_t x[32]; } Ctx;
#define HLE(sym) void imp_##sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
#define G64(a) (*(uint64_t *)(uintptr_t)(a))
#define HOS_FS_PATH 1024
#define R_NOT_MOUNTED 0x7d402
static const char *hos_user_dir = "U";
static const char *gstr(uint64_t a) { return (const char *)(uintptr_t)a; }
static char made[4][HOS_FS_PATH];
static int nmade;
static void make_dirs(const char *path) { if (nmade < 4) snprintf(made[nmade++], HOS_FS_PATH, "%s", path); }
static char mounted[HOS_FS_PATH];
static int user_mount(const char *name, const char *sub) { (void)name; snprintf(mounted, sizeof mounted, "%s", sub); return 1; }

@ENSURE@
@MOUNT@

static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
int main(void) {
    static uint64_t uid[2] = {0x0000000000000001ull, 0x5a3c9e1f0b7d2468ull};   /* (the runtime's user 1: low, high) */
    Ctx c;
    memset(&c, 0, sizeof c);
    c.x[0] = (uint64_t)(uintptr_t)uid;
    imp__ZN2nn2fs14EnsureSaveDataERKNS_7account3UidE(&c);
    check(c.x[0] == 0, "EnsureSaveData answers success");
    check(nmade == 1, "one folder made");
    memset(&c, 0, sizeof c);
    c.x[0] = (uint64_t)(uintptr_t)"save", c.x[1] = (uint64_t)(uintptr_t)uid;
    imp__ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE(&c);
    char want[HOS_FS_PATH];
    snprintf(want, sizeof want, "U\\%s\\", mounted);
    check(!strcmp(made[0], want), "the folder made is the one MountSaveData mounts for that user");
    check(strstr(made[0], "5a3c9e1f0b7d24680000000000000001") != NULL, "the uid's high half first");
    if (!bad) puts("ok");
    else printf("made %s, mounted %s\n", made[0], mounted);
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++"))
    parser.add_argument("--source", type=Path, default=ROOT / "host/nn_fs.c")
    args = parser.parse_args()
    src = args.source.read_text(encoding="utf-8")
    fixture = (HARNESS.replace("@ENSURE@", block(src, "HLE(_ZN2nn2fs14EnsureSaveDataERKNS_7account3UidE) {"))
               .replace("@MOUNT@", block(src, "HLE(_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE) {")))
    with tempfile.TemporaryDirectory(prefix="fs-ensure-") as tmp:
        folder = Path(tmp)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / "fixture"
        subprocess.run([args.cxx, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        print(out.stdout, end="")
        if out.returncode or out.stdout.strip() != "ok":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
