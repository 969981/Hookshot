"""Tests for save_editor/compare.py (read-only diagnostics)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SAVE_EDITOR = HERE.parents[1]
sys.path.insert(0, str(SAVE_EDITOR))
sys.path.insert(0, str(HERE.parent))

import compare  # noqa: E402
import savefile  # noqa: E402
from fixture import build_fixture, build_save  # noqa: E402


def run(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = compare.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-compare-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.raw, _ = build_fixture()
        self.a = self.tmp / "a.sav"
        self.b = self.tmp / "b.sav"
        self.a.write_bytes(self.raw)
        self.b.write_bytes(self.raw)
        self.names = self.tmp / "names.json"
        known = [
            "Test.Bool", "Test.Int", "Test.Float", "Test.Enum", "Test.String",
            "Test.Vector", "Test.Keys", "Test.Bools", "Test.Ints", "Only.Here",
        ]
        self.names.write_text(
            json.dumps({"0x%08X" % savefile.murmur3_32(name): name for name in known}),
            encoding="utf-8",
        )

    def test_identical(self) -> None:
        code, out, _ = run(self.a, self.b)
        self.assertEqual(code, 0)
        self.assertIn("no differences", out)

    def test_scalar_change(self) -> None:
        save = savefile.SaveFile.load(self.b)
        save.set_value(save.find_one("Test.Int"), 12345)
        save.save(self.b)
        code, out, _ = run(self.a, self.b, "--names", self.names)
        self.assertEqual(code, 1)
        self.assertIn("Test.Int", out)
        self.assertIn("-7 -> 12345", out)

    def test_string_change(self) -> None:
        save = savefile.SaveFile.load(self.b)
        save.set_value(save.find_one("Test.String"), "HyruleFielD")
        save.save(self.b)
        code, out, _ = run(self.a, self.b)
        self.assertEqual(code, 1)
        self.assertIn("HyruleFielD", out)

    def test_added_removed(self) -> None:
        other, _ = build_save([
            (savefile.BOOL, [("Test.Bool", True)]),
            (savefile.INT, [("Test.Int", -7)]),
            (savefile.INT, [("Only.Here", 9)]),
        ])
        self.b.write_bytes(other)
        code, out, _ = run(self.a, self.b)
        self.assertEqual(code, 1)
        self.assertIn("Only.Here", out)
        self.assertIn("+", out)

    def test_directory_mode(self) -> None:
        left = self.tmp / "left"
        right = self.tmp / "right"
        for root in (left, right):
            root.mkdir()
            (root / "progress.sav").write_bytes(self.raw)
            (root / "caption.sav").write_bytes(self.raw)
        code, _, _ = run(left, right)
        self.assertEqual(code, 0)

        changed = savefile.SaveFile.load(right / "caption.sav")
        changed.set_value(changed.find_one("Test.String"), "Changed")
        changed.save(right / "caption.sav")
        code, out, _ = run(left, right)
        self.assertEqual(code, 1)
        self.assertIn("caption.sav", out)

    def test_duplicate_hash_across_sections(self) -> None:
        raw, _ = build_save([
            (savefile.BOOL, [("Dup", True)]),
            (savefile.INT, [("Dup", 1)]),
        ])
        self.a.write_bytes(raw)
        self.b.write_bytes(raw)
        code, _, _ = run(self.a, self.b)
        self.assertEqual(code, 0)

        save = savefile.SaveFile.load(self.b)
        entry = next(item for item in save.find("Dup") if item.section_type == savefile.INT)
        save.set_value(entry, 2)
        save.save(self.b)
        code, out, _ = run(self.a, self.b)
        self.assertEqual(code, 1)
        self.assertIn("Int", out)

    def test_json(self) -> None:
        code, out, _ = run(self.a, self.b, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["equal"])
        self.assertEqual(data["files"][0]["file"], "a.sav")

    def test_read_only(self) -> None:
        before = self.a.read_bytes(), self.b.read_bytes()
        run(self.a, self.b)
        self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), before)

    def test_bad_path(self) -> None:
        code, _, err = run(self.tmp / "missing.sav", self.b)
        self.assertEqual(code, 2)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
