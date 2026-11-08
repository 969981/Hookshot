"""The shader census finds the game's compute programs: Effect/*.vsetb and the agl shader archives (.sharcb), and the agl
archives' graphics and mesh programs from their stage records.

Without them the seed had no compute program at all, and each was translated and built the first time the game met it,
2-18 s with the picture frozen on an RX 7700S. Synthetic data only, no game files. python -I -B tests/test_shader_census.py
"""
import collections
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import shader_census as sc  # noqa: E402

CONTROL_SIZE = 0xD8


def control(stage, length=0x40, c1o=0, end=0x100):
    c = bytearray(CONTROL_SIZE)
    c[0:4] = sc.CONTROL_MAGIC
    struct.pack_into("<I", c, 0x38, length)
    struct.pack_into("<II", c, 0x40, c1o, end)
    struct.pack_into("<H", c, 0x56, stage)
    return c


def sharc(entries, code_base=0x1000, size=0x4000):
    """A BAHS blob: each entry (control offset, stage, code offset from the code region, code end)"""
    data = bytearray(size)
    data[0:4] = b"BAHS"
    struct.pack_into("<I", data, 0x1C, code_base)
    for at, stage, code_off, end in entries:
        data[at:at + CONTROL_SIZE] = control(stage, end=end)
        struct.pack_into("<I", data, at - 0x68, code_off)
        code = code_base + code_off
        if code + end <= size:
            data[code:code + end] = bytes((at + i) & 0xFF for i in range(end))
    return bytes(data)


def check_sharc():
    data = sharc([(0x200, 5, 0x100, 0x100),     # a compute program
                  (0x400, 1, 0x300, 0x100),     # a fragment stage: not a program of its own here
                  (0x600, 5, 0x3000, 0x100)])   # its code past the end of the archive
    stats = collections.Counter()
    found = list(sc.sharc_computes(data, stats))
    if len(found) != 1 or len(found[0]) != 1:
        raise SystemExit("expected one compute program, got %r" % [[s[0] for s in p] for p in found])
    stage, ctl, code = found[0][0]
    if stage != 5 or ctl != data[0x200:0x200 + CONTROL_SIZE] or code != data[0x1100:0x1200]:
        raise SystemExit("the compute program's control section or code is not the archive's")
    if stats["sharc, stage 5"] != 1 or stats["sharc, code out of range"] != 1:
        raise SystemExit("unexpected counts: %r" % dict(stats))


def sharc_records(stages, code_base=0x8000, size=0x10000):
    """A BAHS blob with a stage record for each (record offset, control offset, stage, code offset): u32 code offset, u32 0,
    u32 0xe0, s32 control offset from the record. A control section may be shared by several records"""
    data = bytearray(size)
    data[0:4] = b"BAHS"
    struct.pack_into("<I", data, 0x1C, code_base)
    for rec, ctl, stage, code_off in stages:
        if data[ctl:ctl + 4] != sc.CONTROL_MAGIC:
            data[ctl:ctl + CONTROL_SIZE] = control(stage)
        struct.pack_into("<IIIi", data, rec, code_off, 0, 0xE0, ctl - rec)
        code = code_base + code_off
        data[code:code + 0x100] = bytes((rec + i) & 0xFF for i in range(0x100))
    return bytes(data)


def check_sharc_programs():
    data = sharc_records([(0x200, 0x268, 0, 0x000), (0x400, 0x468, 1, 0x200),     # a vertex and fragment program
                          (0x600, 0x268, 0, 0x400), (0x700, 0x768, 1, 0x600),     # another, its vertex control shared
                          (0x900, 0x968, 0, 0x800),                               # a vertex stage alone
                          (0xB00, 0xB68, 6, 0xA00), (0xC00, 0xC68, 7, 0xC00), (0xD00, 0xD68, 1, 0xE00),   # task, mesh, fragment
                          (0xF00, 0xF68, 5, 0x1000)])                             # a compute stage
    stats = collections.Counter()
    found = [[s[0] for s in p] for p in sc.sharc_programs(data, stats)]
    if found != [[0, 1], [0, 1], [0], [6, 7, 1], [5]]:
        raise SystemExit("sharc programs: %r" % found)
    second = list(sc.sharc_programs(data, collections.Counter()))[1]
    if second[0][1] != data[0x268:0x268 + CONTROL_SIZE] or second[0][2] != data[0x8400:0x8500]:
        raise SystemExit("a record pointing back at a shared control section did not get that section and its own code")


def check_archives():
    with tempfile.TemporaryDirectory() as romfs:
        for folder, name in (("Shader", "Model.Product.bfsha.zs"), ("Shader", "ApplicationPackage.release.sarc.zs"),
                             ("Effect", "Animal_Cow.Nin_Ounce_NVN2.vsetb.byml.zs"), ("Effect", "notes.txt"),
                             ("Layout2Shader", "MergeShader.bfsha.zs"), (os.path.join("Lib", "agl"), "agl_resource.release.sarc.zs"),
                             (os.path.join("Lib", "agl"), "readme.txt")):
            os.makedirs(os.path.join(romfs, folder), exist_ok=True)
            open(os.path.join(romfs, folder, name), "wb").close()
        names = sorted(os.path.basename(p) for p in sc.archives(romfs))
    want = sorted(["Model.Product.bfsha.zs", "ApplicationPackage.release.sarc.zs", "Animal_Cow.Nin_Ounce_NVN2.vsetb.byml.zs",
                   "MergeShader.bfsha.zs", "agl_resource.release.sarc.zs"])
    if names != want:
        raise SystemExit("archives: %r, expected %r" % (names, want))


def check_main_reads_sharc():
    source = (ROOT / "nx2" / "shader_census.py").read_text(encoding="utf-8")
    if 'data[:4] == b"BAHS"' not in source or "sharc_computes(data, stats)" not in source or "sharc_programs(data, stats)" not in source:
        raise SystemExit("main() does not hand BAHS archives to sharc_computes")


def main():
    check_sharc()
    check_sharc_programs()
    check_archives()
    check_main_reads_sharc()
    print("shader census: compute programs from .vsetb and .sharcb, and the .sharcb graphics programs ok")


if __name__ == "__main__":
    main()
