"""The process's system resource size: main.npdm's, through gen/hos_image.c, answered by svcGetInfo 16.

The SDK's nn::os::IsVirtualAddressMemoryEnabled is svcGetInfo(16, the process) being non-zero. This runtime answered 0
for every game, so a game that uses virtual address memory (Kirby Air Riders' GPU allocator) was refused it before it
asked, though AllocateAddressRegion, AllocateMemoryPages and MapPhysicalMemory are all answered here.

What this pins down:
  lift     nx2/standalone.py's write_image_c, run on a made-up main.npdm, emits hos_system_resource_size with the
           NPDM's value at 0x14 (and 0 when the NPDM has none).
  answer   host/hos_svc.c's get_info, cut out unmodified and compiled with stubs, answers 16 with that value and 17
           (used) with 0.

python -I -B tests/test_system_resource_size.py [--cc gcc]
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
sys.path.insert(0, str(ROOT / "nx2"))
import standalone  # noqa: E402


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def npdm(system_resource):
    """A main.npdm with what write_image_c reads: META, priority/core, the resource size, the stack, and an ACI0"""
    b = bytearray(0x200)
    b[0:4] = b"META"
    b[0xE], b[0xF] = 44, 0
    b[0x14:0x18] = system_resource.to_bytes(4, "little")
    b[0x1C:0x20] = (0x100000).to_bytes(4, "little")
    b[0x70:0x74] = (0x100).to_bytes(4, "little")
    b[0x74:0x78] = (0x40).to_bytes(4, "little")
    b[0x100:0x104] = b"ACI0"
    b[0x110:0x118] = (0x040077201BE50000).to_bytes(8, "little")
    return bytes(b)


def emitted(system_resource):
    with tempfile.TemporaryDirectory(prefix="sysres-") as tmp:
        exefs, gen = Path(tmp, "exefs"), Path(tmp, "gen")
        exefs.mkdir()
        gen.mkdir()
        (exefs / "main.npdm").write_bytes(npdm(system_resource))
        meta = {"exports": {}, "modules": [], "base": 0x80000000, "end": 0x80100000}
        standalone.write_image_c(str(gen), None, meta, str(exefs))
        text = (gen / "hos_image.c").read_text(encoding="utf-8")
    m = re.search(r"const uint64_t hos_system_resource_size = (0x[0-9a-f]+|0)ULL;", text)
    check(m, "gen/hos_image.c has no hos_system_resource_size")
    return int(m.group(1), 16)


FIXTURE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define HOS_HEAP_BASE 0x1000000000ULL
#define HOS_SPACE_END 0x8000000000ULL
#define HOS_SYSTEM_BASE 0x1000000ULL
#define HOS_SYSTEM_END 0x8000000ULL
static int hos_cores(void) { return 4; }
static uint64_t hos_heap_size, hos_program_size, hos_total_memory = 12ULL << 30;
static const uint64_t hos_program_id = 0x040077201BE50000ULL;
static const uint64_t hos_system_resource_size = 0x2000000ULL;
static uint64_t hos_sys_alloc(uint64_t size, uint64_t align) { (void)size; (void)align; return 0x1000000; }
@DEFINES@
@GET_INFO@
int main(void) {
    int ok = 0;
    uint64_t v = get_info(16, 0, &ok);
    if (!ok || v != 0x2000000ULL) { fprintf(stderr, "FAIL: GetInfo 16 = %#llx (ok %d)\n", (unsigned long long)v, ok); return 1; }
    v = get_info(17, 0, &ok);
    if (!ok || v != 0) { fprintf(stderr, "FAIL: GetInfo 17 = %#llx (ok %d)\n", (unsigned long long)v, ok); return 1; }
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = p.parse_args()
    check(emitted(0x2000000) == 0x2000000, "the NPDM's 32 MB did not reach gen/hos_image.c")
    check(emitted(0) == 0, "an NPDM without a system resource did not give 0")
    svc = (ROOT / "host" / "hos_svc.c").read_text(encoding="utf-8")
    start = svc.index("static uint64_t get_info(")
    end = svc.index("\n}\n", start) + 3
    defines = "\n".join(re.findall(r"^#define (?:HOS_NX2_REGION_\w+|R_\w+) .*$", svc, re.M))
    with tempfile.TemporaryDirectory(prefix="sysres-c-") as tmp:
        src, exe = Path(tmp, "f.c"), Path(tmp, "f")
        src.write_text(FIXTURE.replace("@DEFINES@", defines).replace("@GET_INFO@", svc[start:end]), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                        "-o", str(exe), str(src)], check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: main.npdm's system resource size reaches svcGetInfo 16 (virtual address memory on when it is non-zero)")


if __name__ == "__main__":
    main()
