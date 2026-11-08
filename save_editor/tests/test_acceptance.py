"""Tests for save_editor/acceptance.py (read-only save-tree report)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SAVE_EDITOR = HERE.parents[1]
sys.path.insert(0, str(SAVE_EDITOR))
sys.path.insert(0, str(HERE.parent))

import acceptance  # noqa: E402
import savefile  # noqa: E402
from fixture import build_save  # noqa: E402


def run(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = acceptance.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


def patch_hash(save: savefile.SaveFile, name: str, wanted: int) -> None:
    entry = save.find_one(name)
    struct.pack_into("<I", save.data, entry.offset, wanted)
    entry.hash = wanted


def paired_progress() -> bytes:
    save = savefile.SaveFile(build_save([
        (savefile.STRING64, [("Spawn", "TestScene_Child")]),
        (savefile.VECTOR3, [("Position", (1.0, 2.0, 3.0))]),
    ])[0])
    patch_hash(save, "Spawn", 0x1D6189DA)
    patch_hash(save, "Position", 0xC884818D)
    return save.to_bytes()


def paired_caption(scene: str = "TestScene") -> bytes:
    save = savefile.SaveFile(build_save([
        (savefile.STRING64, [("Scene", scene)]),
        (savefile.STRING64, [("Spawn", "TestScene/TestScene_Child")]),
        (savefile.VECTOR3, [("Position", (1.0, 2.0, 3.0))]),
    ])[0])
    patch_hash(save, "Scene", 0xF74E0E8E)
    patch_hash(save, "Spawn", 0x0CCCC6CD)
    patch_hash(save, "Position", 0x8B26618E)
    return save.to_bytes()


class AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-acceptance-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.account = self.tmp / "user" / "save" / "account"
        self.slot = self.account / "slot_00"
        self.slot.mkdir(parents=True)
        self.progress = self.slot / "progress.sav"
        self.caption = self.slot / "caption.sav"
        self.progress.write_bytes(paired_progress())
        self.caption.write_bytes(paired_caption())
        self.root = self.tmp / "user" / "save"

    def test_all_pass(self) -> None:
        code, out, _ = run("--save-dir", self.root)
        self.assertEqual(code, 0)
        self.assertIn("slot_00", out)
        self.assertIn("scene=TestScene", out)
        self.assertIn("spawn=TestScene_Child", out)
        self.assertIn("newest slot", out)

    def test_pair_pass(self) -> None:
        code, _, _ = run("--save-dir", self.root, "--pair")
        self.assertEqual(code, 0)

    def test_pair_mismatch(self) -> None:
        caption = savefile.SaveFile.load(self.caption)
        caption.set_value(caption.find_one(0xF74E0E8E), "OtherScene")
        caption.save(self.caption)
        code, out, _ = run("--save-dir", self.root, "--pair")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)
        self.assertIn("scene", out)

    def test_missing_caption_pair(self) -> None:
        self.caption.unlink()
        code, out, _ = run("--save-dir", self.root, "--pair")
        self.assertEqual(code, 1)
        self.assertIn("progress.sav and caption.sav", out)

    def test_corrupt_progress(self) -> None:
        self.progress.write_bytes(b"not a save")
        code, out, _ = run("--save-dir", self.root)
        self.assertEqual(code, 1)
        self.assertIn("BAD", out)

    def test_json(self) -> None:
        code, out, _ = run("--save-dir", self.root, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["failures"], 0)
        self.assertEqual(len(data["slots"]), 1)

    def test_read_only(self) -> None:
        before = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        run("--save-dir", self.root, "--pair")
        after = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_no_tree(self) -> None:
        code, _, _ = run("--save-dir", self.tmp / "missing")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
