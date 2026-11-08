"""Every shader program in the game's archives, for the precompile before the first start.

    python -I nx2/shader_census.py <romfs> [--out <file>]

Walks romfs/Shader/*.bfsha.zs (and the shader packages inside other archives), and each BNSH inside them. A BNSH's variations
are programs: which stages go together, so what comes out are real pairs, not a heap of stages. The compute programs of the AGL
shader archives (BAHS, .sharcb) and of Lib/ and Effect/ too: built where a dispatch first meets them, they stopped the game. --out writes them for the back
end to translate (gpu/vk/vk_shader.cpp); nothing here goes into the repository: the file is made from the player's own game.

After tools/shader_census.py of TotK Recomp (github.com/valentderah/totk-pc-recomp, MIT), itself after MacNativeGames'
prepare_shader_seed.py (MIT): the BNSH layout and the walk of its variations.

    <out>:  "HKSEED01", u32 programs, u32 stages; then the stages, each once:
            u64 key, u32 stage, u32 control bytes, u32 code bytes, control, code
            then the programs: u32 count, u32 stage indices
"""
import argparse
import collections
import hashlib
import os
import re
import struct
import sys

CONTROL_MAGIC = b"\x34\x12\x76\x98"
ZSTD = b"\x28\xb5\x2f\xfd"
# the stages a variation's program points at (a 0 offset: none), as far as the structure reaches
SLOTS = (8, 16, 24, 32, 40, 48, 56, 64, 72)


def dictionaries(romfs, zs):
    """The game's zstd dictionaries: the files of a SARC, Pack/ZsDic.pack.zs"""
    raw = zs.ZstdDecompressor().decompress(open(os.path.join(romfs, "Pack", "ZsDic.pack.zs"), "rb").read(), max_output_size=1 << 28)
    start = struct.unpack_from("<I", raw, 0x0c)[0]
    out = []
    for k in range(struct.unpack_from("<H", raw, 0x1a)[0]):
        _, _, st, end = struct.unpack_from("<IIII", raw, 0x20 + k * 16)
        out.append(raw[start + st:start + end])
    return out


def unpack(data, dicts, zs):
    if data[:4] != ZSTD:
        return data
    try:
        return zs.ZstdDecompressor().decompress(data, max_output_size=1 << 31)
    except zs.ZstdError:
        pass
    for d in dicts:
        try:
            return zs.ZstdDecompressor(dict_data=zs.ZstdCompressionDict(d, dict_type=zs.DICT_TYPE_FULLDICT)).decompress(
                data, max_output_size=1 << 31)
        except zs.ZstdError:
            continue
    raise ValueError("no dictionary fits")


def bnsh_programs(data, stats):
    """Each variation of a BNSH (its offsets are its own) as [(stage, control, code)...]"""
    if data[:4] != b"BNSH" or data[12:14] != b"\xff\xfe" or data[96:100] != b"grsc":
        raise ValueError("unsupported BNSH header")
    size = struct.unpack_from("<I", data, 28)[0]
    data = data[:size]
    count, variations = struct.unpack_from("<IQ", data, 124)
    for i in range(count):
        program = struct.unpack_from("<Q", data, variations + i * 64 + 16)[0]
        if not program or program + 80 > size:
            stats["no program"] += 1
            continue
        stages = []
        for rel in SLOTS:
            offset = struct.unpack_from("<Q", data, program + rel)[0]
            if not offset or offset + 32 > size:
                continue
            control_off, code_off, code_size, control_size = struct.unpack_from("<QQII", data, offset + 8)
            if not 0x60 <= control_size <= 0x1000 or control_off + control_size > size or code_off + code_size > size:
                continue
            control = data[control_off:control_off + control_size]
            if control[:4] != CONTROL_MAGIC:
                continue
            stage = struct.unpack_from("<H", control, 0x56)[0]
            stats["slot %d, stage %d" % (rel, stage)] += 1
            stages.append((stage, control, data[code_off:code_off + code_size]))
        if stages:
            yield stages


def bahs_compute(data, stats):
    """The compute programs of an AGL shader archive (BAHS, .sharcb): the metadata (header +0x1c bytes, from the start) holds
    the control sections, the code follows it (+0x18 bytes). 0x68 bytes before each control section: u64 its code's offset in
    the code part, u32 the control section's size, u32 0x68"""
    code_size, meta = struct.unpack_from("<II", data, 0x18)
    if meta + code_size != len(data):
        raise ValueError("unsupported BAHS header")
    for m in re.finditer(re.escape(CONTROL_MAGIC), data[:meta]):
        control_off = m.start()
        if control_off < 0x68 or control_off + 0xd8 > meta:
            continue
        code_off, control_size, back = struct.unpack_from("<QII", data, control_off - 0x68)
        end = struct.unpack_from("<I", data, control_off + 0x44)[0]
        if back != 0x68 or not 0xd8 <= control_size <= 0x1000 or code_off + end > code_size:
            stats["BAHS, no record"] += 1
            continue
        stage = struct.unpack_from("<H", data, control_off + 0x56)[0]
        stats["BAHS, stage %d" % stage] += 1
        if stage == 5:
            yield [(5, data[control_off:control_off + control_size], data[meta + code_off:meta + code_off + end])]


def archives(romfs):
    """The files that hold shaders, each with whether only its compute programs are wanted: Shader/* (programs, and
    ApplicationPackage's compute in its .sharcb), Layout2Shader/*; the compute of Lib/* (AGL, gsys, terrain) and Effect/* (the
    particles'). Compute is built on the drawing thread when first met (seconds each, minutes for some), graphics on workers"""
    for folder, compute_only in (("Shader", False), ("Layout2Shader", False), ("Lib", True), ("Effect", True)):
        top = os.path.join(romfs, folder)
        for root, dirs, files in os.walk(top):
            dirs.sort()
            for name in sorted(files):
                if ".bfsha" in name or ".sarc" in name or ".vsetb" in name:
                    yield os.path.join(root, name), compute_only


def sarc_files(data):
    start = struct.unpack_from("<I", data, 0x0c)[0]
    count = struct.unpack_from("<H", data, 0x1a)[0]
    for k in range(count):
        _, _, st, end = struct.unpack_from("<IIII", data, 0x20 + k * 16)
        yield data[start + st:start + end]


def blobs(data, dicts, zs):
    """The data itself, or each file when it is an archive"""
    if data[:4] == b"SARC":
        for inner in sarc_files(data):
            yield from blobs(unpack(inner, dicts, zs), dicts, zs)
    else:
        yield data


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("romfs")
    p.add_argument("--out")
    a = p.parse_args()
    import zstandard as zs
    dicts = dictionaries(a.romfs, zs)
    stats = collections.Counter()
    stage_index, stage_list, programs, seen = {}, [], [], set()
    def found(data):
        """each program of a blob: its BAHS compute, or its BNSHs' programs"""
        if data[:4] == b"BAHS":
            yield from bahs_compute(data, stats)
            return
        view = memoryview(data)
        for m in re.finditer(b"BNSH", data):
            if data[m.start() + 96:m.start() + 100] == b"grsc":
                yield from bnsh_programs(bytes(view[m.start():m.start() + struct.unpack_from("<I", data, m.start() + 28)[0]]), stats)

    for path, compute_only in archives(a.romfs):
        made = 0
        for data in blobs(unpack(open(path, "rb").read(), dicts, zs), dicts, zs):
            for stages in found(data):
                if compute_only:
                    stages = [s for s in stages if s[0] == 5]
                    if not stages:
                        continue
                ids = []
                for stage, control, code in stages:
                    key = int.from_bytes(hashlib.sha1(control + code).digest()[:8], "little")
                    if key not in stage_index:
                        stage_index[key] = len(stage_list)
                        stage_list.append((key, stage, control, code))
                    ids.append(stage_index[key])
                ids = tuple(ids)
                if ids not in seen:
                    seen.add(ids)
                    programs.append(ids)
                    made += 1
        if made or not compute_only:   # (most of Effect/'s files have no compute)
            print("%-64s %6d programs" % (os.path.basename(path)[:64], made), flush=True)
    kinds = collections.Counter(tuple(sorted(stage_list[i][1] for i in ids)) for ids in programs)
    print("%d programs from %d stages" % (len(programs), len(stage_list)))
    print("by stages:", kinds.most_common(12))
    print("slots:", sorted(stats.items()))
    if a.out:
        with open(a.out, "wb") as f:
            f.write(b"HKSEED01" + struct.pack("<II", len(programs), len(stage_list)))
            for key, stage, control, code in stage_list:
                f.write(struct.pack("<QIII", key, stage, len(control), len(code)) + control + code)
            for ids in programs:
                f.write(struct.pack("<I%dI" % len(ids), len(ids), *ids))
        print("wrote", a.out, os.path.getsize(a.out) >> 20, "MB")


if __name__ == "__main__":
    sys.exit(main())
