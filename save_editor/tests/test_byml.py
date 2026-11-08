"""Tests for the stdlib BYML v7 reader."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SAVE_EDITOR = HERE.parents[1]
sys.path.insert(0, str(SAVE_EDITOR))

import byml  # noqa: E402


def minimal_map(big_endian: bool) -> bytes:
    """A v7 map ``{"Answer": 42}`` in either byte order."""
    endian = ">" if big_endian else "<"
    order = "big" if big_endian else "little"
    magic = b"BY" if big_endian else b"YB"
    table = bytearray([0xC2])
    table += (1).to_bytes(3, order)
    table += (12).to_bytes(4, order)
    table += (19).to_bytes(4, order)
    table += b"Answer\x00"
    root_offset = 0x10 + len(table)
    root = bytearray([0xC1])
    root += (1).to_bytes(3, order)
    root += (0).to_bytes(3, order) + bytes([0xD1]) + (42).to_bytes(4, order)
    header = (
        magic
        + (7).to_bytes(2, order)
        + (0x10).to_bytes(4, order)
        + (0).to_bytes(4, order)
        + root_offset.to_bytes(4, order)
    )
    return bytes(header + table + root)


class MinimalTests(unittest.TestCase):
    def test_little_endian_map(self) -> None:
        document = byml.Byml(minimal_map(False))
        self.assertEqual(document.version, 7)
        self.assertEqual(document.endian, "<")
        self.assertEqual(document.root, {"Answer": 42})

    def test_big_endian_map(self) -> None:
        document = byml.Byml(minimal_map(True))
        self.assertEqual(document.version, 7)
        self.assertEqual(document.endian, ">")
        self.assertEqual(document.root, {"Answer": 42})

    def test_bad_magic(self) -> None:
        with self.assertRaises(byml.BymlError):
            byml.Byml(b"XXXX" + b"\x00" * 12)

    def test_short(self) -> None:
        with self.assertRaises(byml.BymlError):
            byml.Byml(b"YB")

    def test_bad_version(self) -> None:
        data = bytearray(minimal_map(False))
        data[2:4] = (9).to_bytes(2, "little")
        with self.assertRaises(byml.BymlError):
            byml.Byml(data)


def _romfs() -> Path | None:
    candidates = [os.environ.get("HOOKSHOT_ROMFS"), r"I:\Hookshot\romfs"]
    for candidate in candidates:
        if candidate:
            root = Path(candidate)
            if root.is_dir():
                return root
    return None


class RealBymlTests(unittest.TestCase):
    def test_a_real_bgyml(self) -> None:
        root = _romfs()
        if root is None:
            self.skipTest("no game romfs")
        sample = next(
            (
                path
                for path in root.rglob("*.bgyml")
                if path.stat().st_size > 32
            ),
            None,
        )
        if sample is None:
            self.skipTest("no non-empty .bgyml in the romfs")
        document = byml.Byml(sample.read_bytes())
        self.assertIn(document.version, (2, 3, 4, 7))
        self.assertIsInstance(document.root, (dict, list))

    def test_decompressed_gamedata_optional(self) -> None:
        candidates = [
            os.environ.get("HOOKSHOT_GAMEDATA"),
            str(Path(os.environ.get("TEMP", ".")) / "GameDataList.byml"),
        ]
        path = next((Path(item) for item in candidates if item and Path(item).is_file()), None)
        if path is None:
            self.skipTest("no decompressed GameDataList")
        document = byml.Byml(path.read_bytes())
        self.assertEqual(document.version, 7)
        self.assertIsInstance(document.root, dict)
        self.assertIn("Data", document.root)
        self.assertIn("MetaData", document.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
