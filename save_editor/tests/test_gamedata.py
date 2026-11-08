"""Tests for the GameDataList -> save schema generator."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SAVE_EDITOR = HERE.parents[1]
sys.path.insert(0, str(SAVE_EDITOR))

import extract_gamedata  # noqa: E402
import gamedata  # noqa: E402
import savefile  # noqa: E402


def document() -> dict:
    return {
        "RootNode": {
            "Data": {
                "Bool": [
                    {
                        "Hash": 0xA3DB7114,
                        "DefaultValue": False,
                        "ResetTypeValue": 8,
                        "SaveFileIndex": 0,
                    }
                ],
                "Enum": [
                    {
                        "Hash": 0x11223344,
                        "DefaultValue": 0x11111111,
                        "ResetTypeValue": 1,
                        "SaveFileIndex": 1,
                        "Values": [0x11111111, 0x22222222],
                        "RawValues": ["Off", "On"],
                    }
                ],
                "Struct": [
                    {"Hash": 0x55667788, "ResetTypeValue": 0, "SaveFileIndex": -1}
                ],
                "Bool64bitKey": [
                    {"Hash": 0x123456789ABCDEF0, "SaveFileIndex": 0}
                ],
            },
            "MetaData": {
                "FormatVersion": 1,
                "SaveDirectory": ["slot_%02d/progress.sav", "option.sav"],
            },
        }
    }


class BuildSchemaTests(unittest.TestCase):
    def test_saved_only(self) -> None:
        schema, stats = gamedata.build_schema(
            document(), revision=2_982_123, saved_only=True, defaults=True, merge=None
        )
        self.assertEqual(stats["fields"], 2)
        self.assertEqual(stats["saved"], 2)
        self.assertEqual(stats["runtime"], 1)
        self.assertEqual(stats["skipped_64bit"], 1)
        self.assertEqual(schema.version, 2_982_123)
        self.assertEqual(set(schema.fields), {0xA3DB7114, 0x11223344})

        enum = schema.field(0x11223344)
        self.assertEqual(enum.type_name, "Enum")
        self.assertEqual(enum.enum, {0x11111111: "Off", 0x22222222: "On"})
        self.assertEqual(enum.save_file, "option.sav")
        self.assertEqual(enum.save_file_index, 1)
        self.assertEqual(enum.default, 0x11111111)
        self.assertEqual(enum.reset, 1)

    def test_all_fields(self) -> None:
        schema, stats = gamedata.build_schema(
            document(), revision=None, saved_only=False, defaults=True, merge=None
        )
        self.assertEqual(stats["fields"], 3)
        self.assertIn(0x55667788, schema.fields)
        self.assertEqual(schema.field(0x55667788).type_name, "Struct")
        self.assertEqual(schema.field(0x55667788).save_file_index, -1)

    def test_merge_keeps_names(self) -> None:
        base = savefile.Schema()
        base.fields[0xA3DB7114] = savefile.FieldInfo(name="Game")
        schema, _ = gamedata.build_schema(
            document(), revision=None, saved_only=True, defaults=True, merge=base
        )
        self.assertEqual(schema.name(0xA3DB7114), "Game")

    def test_write_and_reload(self) -> None:
        schema, _ = gamedata.build_schema(
            document(), revision=2_982_123, saved_only=True, defaults=True, merge=None
        )
        with tempfile.TemporaryDirectory(prefix="hookshot-gamedata-") as tmp:
            out = Path(tmp) / "schema.json"
            gamedata.write_schema(schema, "test", ["slot_%02d/progress.sav", "option.sav"], out)
            reloaded = savefile.load_schema(out)
            self.assertEqual(reloaded.version, 2_982_123)
            self.assertEqual(reloaded.field(0x11223344).enum[0x22222222], "On")
            self.assertEqual(reloaded.field(0x11223344).save_file, "option.sav")
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["fields"]["0x11223344"]["default"], 0x11111111)


class SarcTests(unittest.TestCase):
    def test_read_sarc(self) -> None:
        # Header + two 16-byte entries; data starts after the table.
        data_start = 0x40
        header = bytearray(data_start)
        header[:4] = b"SARC"
        header[0x0C:0x10] = (data_start).to_bytes(4, "little")
        header[0x1A:0x1C] = (2).to_bytes(2, "little")
        header[0x20:0x40] = (
            (0).to_bytes(4, "little") * 2
            + (0).to_bytes(4, "little")
            + (4).to_bytes(4, "little")
            + (0).to_bytes(4, "little") * 2
            + (4).to_bytes(4, "little")
            + (8).to_bytes(4, "little")
        )
        blob = bytes(header) + b"AAAABBBB"
        self.assertEqual(extract_gamedata.read_sarc(blob), [b"AAAA", b"BBBB"])

    def test_bad_sarc(self) -> None:
        with self.assertRaises(ValueError):
            extract_gamedata.read_sarc(b"nope")


class RealGameDataTests(unittest.TestCase):
    def test_optional_extract_from_romfs(self) -> None:
        candidates = [os.environ.get("HOOKSHOT_ROMFS"), r"I:\Hookshot\romfs"]
        root = next((Path(item) for item in candidates if item and Path(item).is_dir()), None)
        if root is None:
            self.skipTest("no game romfs")
        if extract_gamedata._zstd is None:
            self.skipTest("no zstd module")
        data, revision, target = extract_gamedata.extract(root)
        self.assertEqual(len(data), 1_462_888)
        self.assertEqual(data[:2], b"BY")
        self.assertEqual(revision, 2_982_123)
        self.assertIn("GameDataList", target.name)

    def test_optional_real_gamedata(self) -> None:
        candidates = [
            os.environ.get("HOOKSHOT_GAMEDATA"),
            str(Path(os.environ.get("TEMP", ".")) / "GameDataList.byml"),
        ]
        path = next((Path(item) for item in candidates if item and Path(item).is_file()), None)
        if path is None:
            self.skipTest("no decompressed GameDataList")
        with tempfile.TemporaryDirectory(prefix="hookshot-gamedata-real-") as tmp:
            out = Path(tmp) / "schema.json"
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = gamedata.main([str(path), "--revision", "2982123", "--out", str(out)])
            self.assertEqual(code, 0)
            schema = savefile.load_schema(out)
            self.assertGreater(len(schema.fields), 1000)
            self.assertEqual(schema.version, 2_982_123)


if __name__ == "__main__":
    unittest.main(verbosity=2)
