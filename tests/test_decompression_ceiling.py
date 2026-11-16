"""Decompression of files a dump or a download supplies stops at a ceiling, instead of holding whatever a frame unpacks to:

  - nx2/nup.py copy_to (a .nup member) and nx2/bounded.py zstd (the prompts' game files, nx2/prompt_assets.py) feed the
    decompressor nx2/bounded.STEP bytes at a time, so a crafted frame (here 8 GB in 256 kB of RLE blocks) is stopped at the
    size the package declares, in a process allowed 2 GB of address space; one call unpacked all it was given before;
  - nx2/nup.py extracts only from a package that passes its strict checks (the TOC's SHA-256, its bounds, no cycle);
  - nx2/pipeline_states.py load() unpacks no more than SENT_MOST unless asked, and nx2/pipeline_share.py read_list the same.

    python -I -B tests/test_decompression_ceiling.py
"""
import lzma
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_MODULES = ["zstandard"]
RUN_ALL_SKIP = {"windows": "the address-space limit is POSIX (resource.setrlimit)"}


def rle_bomb(blocks=65536):
    """A zstd frame of `blocks` RLE blocks of 128 KB (8 GB at 65536), no content size stated: 4 bytes a block"""
    out = bytearray(b"\x28\xb5\x2f\xfd" + bytes([0x00, 0x38]))   # (no size, no checksum; a 128 KB window)
    for i in range(blocks):
        header = (1 if i == blocks - 1 else 0) | (1 << 1) | ((128 << 10) << 3)
        out += struct.pack("<I", header)[:3] + b"\0"
    return bytes(out)


def package(name, logical_size, stored, codec):
    """One title, one root directory, one file (nx2/test_nup_codec.py's layout)"""
    name = name.encode()
    file_at = 0x80 + 0x30 + 0x40 + 24
    strings_at = file_at + 80 + 4
    data_at = (strings_at + len(name) + 15) & ~15
    blob = bytearray(data_at + len(stored) + 64)
    blob[:4] = b"NUP\x01"
    struct.pack_into("<Q", blob, 0x40, len(blob))
    blob[0x80:0x84] = b"TOC\x01"
    struct.pack_into("<IQQQQ", blob, 0x84, 1, 1, 1, strings_at - 0x80, len(name))
    struct.pack_into("<QIIII", blob, 0xb0, 1, 0, 0, 0, 1)
    struct.pack_into("<4I", blob, file_at - 24, 0, 0, 0, 1)
    struct.pack_into("<IIQQQ", blob, file_at, 0, len(name), logical_size, data_at, len(stored))
    blob[file_at + 64] = codec
    struct.pack_into("<I", blob, file_at + 80, 1)
    blob[strings_at:strings_at + len(name)] = name
    blob[data_at:data_at + len(stored)] = stored
    return blob


CHILD = r"""
import importlib.util, io, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
sys.path.insert(0, sys.argv[1] + "/nx2")
spec = importlib.util.spec_from_file_location("nup_t", sys.argv[1] + "/nx2/nup.py")
nup = importlib.util.module_from_spec(spec); spec.loader.exec_module(nup)
import bounded
if sys.argv[3] == "nup":
    r = nup.Nup(sys.argv[2]); rel, rec = next(r.walk(0))
    try:
        r.copy_to(rec, io.BytesIO(), rel)
    except SystemExit as e:
        print("stopped:", e)
else:
    try:
        bounded.zstd(open(sys.argv[2], "rb").read(), 1 << 20)
    except bounded.TooLarge as e:
        print("stopped:", e)
"""


class Zstd(unittest.TestCase):
    def run_child(self, path, kind):
        return subprocess.run([sys.executable, "-I", "-c", CHILD, str(ROOT), str(path), kind], capture_output=True, text=True,
                              timeout=300)

    def test_a_nup_member_that_unpacks_past_its_size_is_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "bomb.nup")
            path.write_bytes(package("bomb.bin", 1 << 20, rle_bomb(), 1))
            r = self.run_child(path, "nup")
            self.assertIn("stopped: a compressed file expanded past its declared size", r.stdout, r.stderr[-2000:])

    def test_a_prompt_file_that_unpacks_past_the_ceiling_is_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "bomb.zs")
            path.write_bytes(rle_bomb())
            r = self.run_child(path, "zs")
            self.assertIn("stopped: unpacks to more than", r.stdout, r.stderr[-2000:])

    def test_a_small_frame_still_unpacks_whole(self):
        sys.path.insert(0, str(ROOT / "nx2"))
        import bounded
        import zstandard
        data = bytes(range(256)) * 4096
        self.assertEqual(bounded.zstd(zstandard.ZstdCompressor().compress(data), len(data)), data)
        with self.assertRaises(bounded.TooLarge):
            bounded.zstd(zstandard.ZstdCompressor().compress(data), len(data) - 1)


class StrictExtraction(unittest.TestCase):
    def test_extraction_checks_the_package_first(self):
        # (package() writes no TOC hash and no TOC offset: what a crafted or damaged package looks like to the strict checks)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "damaged.nup")
            path.write_bytes(package("a.bin", 3, b"abc", 0))
            out = Path(d, "out")
            r = subprocess.run([sys.executable, "-I", str(ROOT / "nx2" / "nup.py"), str(path), "--extract", str(out)],
                               capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("a damaged .nup", r.stderr + r.stdout)
            self.assertFalse(any(out.rglob("a.bin")) if out.exists() else False)


class Xz(unittest.TestCase):
    def test_pipeline_lists_unpack_no_further_than_the_limit(self):
        sys.path.insert(0, str(ROOT / "nx2"))
        import pipeline_states
        import pipeline_share
        src = (ROOT / "nx2" / "pipeline_states.py").read_text(encoding="utf-8")
        self.assertIn("def load(path, most=SENT_MOST):", src)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, pipeline_share.LIST)
            path.write_bytes(lzma.compress(bytes(4 << 20), format=lzma.FORMAT_XZ))
            old = pipeline_states.SENT_MOST
            try:
                pipeline_states.SENT_MOST = 1 << 20
                with self.assertRaises(SystemExit):
                    pipeline_states.load(str(path), pipeline_states.SENT_MOST)
                with self.assertRaises(pipeline_share.ShareError) as e:
                    pipeline_share.read_list(str(path))
                self.assertIn("unpacks to more than", str(e.exception))
            finally:
                pipeline_states.SENT_MOST = old


if __name__ == "__main__":
    unittest.main()
