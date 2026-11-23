"""A game module copied in text mode is named as that, before the translator fails on it (nx2/make_game.py nso_short,
check_modules).

A text-mode copy (an FTP client's ASCII transfer type) drops the CR of every CR LF pair, so each NSO comes out a few
bytes shorter than its header says: Pokémon Pokopia's dump on the developers' PC lacked 980 bytes of main, 301 of sdk and
31 of subsdk0, and lift.py stopped with "segment 2 is outside the file". The modules here are made-up headers with
made-up bytes, no game's.

    python -I -B tests/test_nso_short.py
"""
import contextlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party/a64-kit/tools"))
spec = importlib.util.spec_from_file_location("make_game_nso_short", ROOT / "nx2/make_game.py")
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


def nso(sizes, compressed=(True, True, True), name=b"made-up\0"):
    """An NSO0 header and its segments' bytes as the header lists them: sizes is (in the file, in memory) per segment"""
    h = bytearray(0x100)
    h[:4] = b"NSO0"
    flags = sum(1 << i for i, c in enumerate(compressed) if c) | 0x38
    struct.pack_into("<I", h, 0xC, flags)
    at, mem = 0x100 + len(name), 0
    for i, (file_size, mem_size) in enumerate(sizes):
        struct.pack_into("<III", h, 0x10 + 0x10 * i, at, mem, mem_size)
        struct.pack_into("<I", h, 0x60 + 4 * i, file_size)
        at += file_size if compressed[i] else mem_size
        mem += (mem_size + 0xfff) & ~0xfff
    return bytes(h) + name + b"\x5a" * (at - 0x100 - len(name))


class NsoShortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hookshot-nso-short-")
        self.addCleanup(tmp.cleanup)
        self.exefs = Path(tmp.name)

    def write(self, name, data):
        (self.exefs / name).write_bytes(data)
        return str(self.exefs / name)

    def test_whole_modules_are_not_short(self):
        self.assertEqual(make_game.nso_short(self.write("main", nso([(300, 900), (200, 500), (50, 120)]))), 0)
        self.assertEqual(make_game.nso_short(self.write("rtld", nso([(300, 300), (40, 40), (60, 60)], (False,) * 3))), 0)
        self.assertEqual(make_game.nso_short(self.write("main.npdm", b"META" + bytes(60))), 0)
        self.assertEqual(make_game.nso_short(self.write("tiny", b"NSO0")), 0)

    def test_short_by_what_a_text_copy_dropped(self):
        whole = nso([(300, 900), (200, 500), (50, 120)])
        self.assertEqual(make_game.nso_short(self.write("main", whole[:-7])), 7)
        # (an uncompressed segment ends at its size in memory, not at the compressed size the header also holds)
        plain = nso([(1, 300), (1, 40), (1, 60)], (False, False, False))
        self.assertEqual(make_game.nso_short(self.write("rtld", plain[:-3])), 3)
        self.assertEqual(make_game.nso_short(self.write("rtld", plain)), 0)

    def check_modules(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                make_game.check_modules(str(self.exefs))
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def test_check_modules_names_each_short_module(self):
        self.write("main", nso([(300, 900), (200, 500), (50, 120)])[:-9])
        self.write("sdk", nso([(100, 400), (60, 200), (20, 80)])[:-2])
        self.write("rtld", nso([(30, 90), (20, 50), (5, 12)]))
        self.write("main.npdm", b"META" + bytes(60))
        code, text = self.check_modules()
        self.assertEqual(code, 1, text)
        self.assertIn("@@error", text)
        self.assertIn("main by 9 bytes, sdk by 2 bytes", text)
        self.assertIn("copied in text mode", text)
        self.assertIn("binary mode", text)
        self.assertNotIn("rtld", text)

    def test_check_modules_passes_whole_modules(self):
        self.write("main", nso([(300, 900), (200, 500), (50, 120)]))
        self.write("main.npdm", b"META" + bytes(60))
        self.assertEqual(self.check_modules(), (0, ""))


if __name__ == "__main__":
    unittest.main()
