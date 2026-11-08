"""scripts/pipenotes.py merges pipeline notes by key and refuses files from another build.

python -I -B tests/test_pipenotes.py
"""
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import pipenotes  # noqa: E402

SIZE = 32
STAMP = 0x1122334455667788


def record(key: int, fill: int) -> bytes:
    return struct.pack("<Q", key) + bytes([fill]) * (SIZE - 8)


def make(path: str, records, stamp: int = STAMP, size: int = SIZE, tail: bytes = b"") -> str:
    with open(path, "wb") as f:
        f.write(struct.pack("<QQ", stamp, size))
        for r in records:
            f.write(r)
        f.write(tail)
    return path


class PipeNotesTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name

    def p(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def test_merge_keeps_the_first_record_of_each_key_in_order(self):
        a = make(self.p("a.bin"), [record(1, 0xA1), record(2, 0xA2)])
        b = make(self.p("b.bin"), [record(2, 0xB2), record(3, 0xB3)])
        out = self.p("out.bin")
        self.assertEqual(pipenotes.main(["merge", out, a, b]), 0)
        n = pipenotes.read_notes(out)
        self.assertEqual(list(n.records), [1, 2, 3])
        self.assertEqual(n.records[2], record(2, 0xA2))
        self.assertEqual((n.stamp, n.size), (STAMP, SIZE))

    def test_a_duplicate_inside_one_file_counts_once(self):
        a = make(self.p("a.bin"), [record(1, 1), record(1, 2)])
        self.assertEqual(len(pipenotes.read_notes(a).records), 1)

    def test_other_stamp_or_record_size_is_refused_and_nothing_is_written(self):
        a = make(self.p("a.bin"), [record(1, 1)])
        other_stamp = make(self.p("s.bin"), [record(2, 2)], stamp=STAMP + 1)
        other_size = make(self.p("z.bin"), [struct.pack("<Q", 3) + b"\0" * 8], size=16)
        for bad in (other_stamp, other_size):
            out = self.p("out.bin")
            self.assertEqual(pipenotes.main(["merge", out, a, bad]), 1)
            self.assertFalse(os.path.exists(out))

    def test_a_cut_last_record_is_left_out(self):
        a = make(self.p("a.bin"), [record(1, 1)], tail=record(2, 2)[:10])
        n = pipenotes.read_notes(a)
        self.assertEqual(list(n.records), [1])
        self.assertEqual(n.cut, 10)

    def test_output_may_not_be_an_input(self):
        a = make(self.p("a.bin"), [record(1, 1)])
        before = open(a, "rb").read()
        self.assertEqual(pipenotes.main(["merge", a, a]), 1)
        self.assertEqual(open(a, "rb").read(), before)

    def test_files_that_are_not_notes_are_refused(self):
        short = self.p("short.bin")
        open(short, "wb").write(b"abc")
        zero = make(self.p("zero.bin"), [], size=0)
        for bad in (short, zero):
            with self.assertRaises(pipenotes.NotesError):
                pipenotes.read_notes(bad)
        self.assertEqual(pipenotes.main(["info", short]), 1)

    def test_no_temporary_file_is_left_beside_the_output(self):
        a = make(self.p("a.bin"), [record(1, 1)])
        pipenotes.main(["merge", self.p("out.bin"), a])
        self.assertEqual(sorted(os.listdir(self.dir)), ["a.bin", "out.bin"])


if __name__ == "__main__":
    unittest.main()
