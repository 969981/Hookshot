"""nn::fs::GetSaveDataSize (host/nn_fs.c): success with both sizes, and only into the game's memory; and
MountCacheStorage(char const*) answered, as cache storage 0.

The handler is cut out of production unmodified and run against fake guest memory: both sizes written (a generous
1 GiB each: a host folder has no size of its own), the answer success; a null or out-of-range pointer is not written.
Left to the lifted SDK, the call read through a null fs service proxy (Kirby Air Riders' crash after its first save
mount).

python -I -B tests/test_fs_save_data_size.py [--cc gcc]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]

FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct { uint64_t x[32]; } Ctx;
#define HLE(sym) static void sym(Ctx *c)
#define ARG(n) (c->x[n])
#define RET(v) (c->x[0] = (uint64_t)(v))
enum { MEMORY = 1024 };
static unsigned char memory[MEMORY];
#define GP(a) ((void *)(memory + (a)))
#define G64(a) (*(uint64_t *)GP(a))
static void *hos_guest_range(uint64_t a, uint64_t n) { return a < MEMORY && n <= MEMORY - a ? GP(a) : NULL; }
static void hos_log(const char *f, ...) { (void)f; }
@HANDLER@
int main(void) {
    Ctx c = {{0}};
    memset(memory, 0xa5, sizeof memory);
    c.x[0] = 64; c.x[1] = 72; c.x[2] = 128;
    _ZN2nn2fs15GetSaveDataSizeEPlS1_RKNS_7account3UidE(&c);
    CHECK(c.x[0] == 0 && G64(64) == 1ULL << 30 && G64(72) == 1ULL << 30 && memory[80] == 0xa5);
    memset(memory, 0xa5, sizeof memory);
    Ctx d = {{0}};
    d.x[0] = 0; d.x[1] = MEMORY - 4; d.x[2] = 128;
    _ZN2nn2fs15GetSaveDataSizeEPlS1_RKNS_7account3UidE(&d);
    CHECK(d.x[0] == 0 && memory[0] == 0xa5 && memory[MEMORY - 4] == 0xa5);
    Ctx e = {{0}};
    e.x[0] = 256; e.x[1] = 264; e.x[2] = 0;
    _ZN2nn2fs19GetCacheStorageSizeEPlS1_i(&e);
    CHECK(e.x[0] == 0 && G64(256) == 1ULL << 30 && G64(264) == 1ULL << 30);
    Ctx m = {{0}};
    m.x[0] = 512; m.x[1] = 520;
    _ZN2nn2fs18GetSaveDataSizeMaxEPlS1_(&m);
    CHECK(m.x[0] == 0 && G64(512) >= 1ULL << 30 && G64(520) >= 1ULL << 30);   /* (at least what GetSaveDataSize says) */
    Ctx f = {{0}};
    f.x[0] = 600;
    _ZN2nn2fs16GetFreeSpaceSizeEPlPKc(&f);
    CHECK(f.x[0] == 0 && G64(600) >= 1ULL << 30);
    printf("PASS: the save and cache storage sizes, the most a save grows to and the free space, only into the game's "
           "memory\n");
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
    fs = (ROOT / "host" / "nn_fs.c").read_text(encoding="utf-8")
    handler = block(fs, "HLE(_ZN2nn2fs15GetSaveDataSizeEPlS1_RKNS_7account3UidE)") + "\n" + \
        block(fs, "HLE(_ZN2nn2fs19GetCacheStorageSizeEPlS1_i)") + "\n" + \
        block(fs, "HLE(_ZN2nn2fs18GetSaveDataSizeMaxEPlS1_)") + "\n" + block(fs, "HLE(_ZN2nn2fs16GetFreeSpaceSizeEPlPKc)")
    for sym in ("_ZN2nn2fs14ExtendSaveDataERKNS_7account3UidEll", "_ZN2nn2fs9IsMountedEPKc"):
        if not re.search(r"^HLE\(" + sym + r"\)", fs, re.M):
            raise SystemExit("FAIL: %s is not answered" % sym)
    plain = re.search(r'^HLE\(_ZN2nn2fs17MountCacheStorageEPKc\) \{ RET\(user_mount\(gstr\(ARG\(0\)\), "cache\\\\0"\)', fs, re.M)
    if not plain:
        raise SystemExit("FAIL: MountCacheStorage(char const*) is not answered as cache storage 0")
    with tempfile.TemporaryDirectory(prefix="fs-save-size-") as tmp:
        src, exe = Path(tmp, "f.c"), Path(tmp, "f")
        src.write_text(FIXTURE.replace("@HANDLER@", handler), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-o", str(exe), str(src)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
