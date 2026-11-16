"""NRO modules: the code a game loads at run time with nn::ro (romfs .nro files), read as a64-kit's Nso reads an NSO.

An NRO is the module image itself, uncompressed: its header (at 0x10, "NRO0") gives the three segments at the same
offsets in the file and in memory, the bss size and the build id, and its first words give the MOD0 offset, as an
NSO's image does. So the rest (MOD0, the dynamic table, symbols, relocations, the init array) is a64-kit's Nso._mod0.

A static recompilation cannot load code at run time, so the lift takes the dump's NROs as more modules, after sdk
(nx2/image.py), links them against the rest and lifts them with the game; host/nn_ro.c answers nn::ro::LoadModule by
finding the lifted module by its build id. find() is where they come from: every *.nro in the romfs beside the ExeFS.
"""
import os
import struct

import kit  # noqa: F401  (sys.path)
import nso2  # noqa: F401  (DT_RELR)
import nso

PAGE = 0x1000


class Nro(nso.Nso):
    def __init__(self, path, name=None):   # (not Nso.__init__: no NSO header, nothing compressed)
        raw = open(path, "rb").read()
        if raw[0x10:0x14] != b"NRO0":
            raise ValueError("%s: not an NRO" % path)
        self.path = path
        self.name = name or os.path.basename(path)
        segs = [struct.unpack_from("<II", raw, 0x20 + 8 * i) for i in range(3)]
        self.header_bss_size = struct.unpack_from("<I", raw, 0x38)[0]
        self.bss_size = self.header_bss_size
        self.build_id = raw[0x40:0x60].rstrip(b"\0").hex()
        self.build_id_raw = raw[0x40:0x60]
        self.segments = [(off, size, n) for (off, size), n in zip(segs, (".text", ".rodata", ".data"))]
        end = max(off + size for off, size, _ in self.segments)
        self.image = bytearray(raw[:end])
        if len(self.image) < end:
            raise ValueError("%s: shorter than its segments (%#x < %#x)" % (path, len(raw), end))
        self.nro = True
        self._mod0()


def find(exefs):
    """The NROs of the dump the ExeFS is in: every *.nro under the romfs beside it, by path (sorted: the same layout
    every lift). None when there is no romfs there."""
    romfs = os.path.join(os.path.dirname(os.path.abspath(exefs.rstrip("/\\"))), "romfs")
    if not os.path.isdir(romfs):
        return []
    found = []
    for root, dirs, files in os.walk(romfs):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(".nro"):
                found.append(os.path.join(root, f))
    return found


def load(paths):
    """The NROs as modules, named by their romfs file name ("EffectScript.nro")"""
    return [Nro(p) for p in paths]
