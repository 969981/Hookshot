"""Format-level tests for save_editor/savefile.py.

Run directly:

    python -I -B save_editor/tests/test_savefile.py

The fixture is synthesized here; no game save is stored in the repository.
An optional pass over real saves runs when HOOKSHOT_SAVE_DIR (or the default
I:\\Hookshot\\user\\save) exists.
"""

from __future__ import annotations

import json
import os
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

import savefile  # noqa: E402
from fixture import (  # noqa: E402
    build_account_like,
    build_empty_sections,
    build_fixture,
    build_option_like,
)


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw, self.offsets = build_fixture()
        self.save = savefile.SaveFile(self.raw)

    def test_header(self) -> None:
        self.assertEqual(self.save.magic, savefile.MAGIC)
        self.assertEqual(self.save.version, 2_982_123)
        self.assertEqual(self.save.data_offset, min(self.offsets.values()))

    def test_sections(self) -> None:
        kinds = [section.type_id for section in self.save.sections]
        self.assertEqual(
            kinds,
            [
                savefile.BOOL,
                savefile.INT,
                savefile.FLOAT,
                savefile.ENUM,
                savefile.STRING16,
                savefile.VECTOR3,
                savefile.BOOL64BIT_KEY,
                savefile.BOOL_ARRAY,
                savefile.INT_ARRAY,
            ],
        )

    def test_scalars(self) -> None:
        self.assertIs(self.save.find_one("Test.Bool").value, True)
        self.assertEqual(self.save.find_one("Test.Int").value, -7)
        self.assertAlmostEqual(self.save.find_one("Test.Float").value, 1.25, places=5)
        self.assertEqual(self.save.find_one("Test.Enum").value, 0xDEADBEEF)

    def test_references(self) -> None:
        self.assertEqual(self.save.find_one("Test.String").value, "HyruleField")
        self.assertEqual(self.save.find_one("Test.Vector").value, (1.5, 2.5, 3.5))
        self.assertEqual(
            self.save.find_one("Test.Keys").value,
            [0x1111222233334444, 0x5555666677778888],
        )
        self.assertEqual(self.save.find_one("Test.Bools").value, [True, False, True])
        self.assertEqual(self.save.find_one("Test.Ints").value, [1, -2, 3])

    def test_round_trip_is_byte_identical(self) -> None:
        self.assertEqual(self.save.to_bytes(), self.raw)

    def test_find_by_hash(self) -> None:
        wanted = savefile.murmur3_32("Test.Int")
        self.assertEqual(self.save.find_one("0x%08X" % wanted).value, -7)
        self.assertEqual(self.save.find_one(str(wanted)).value, -7)

    def test_set_scalar_patches_only_its_value(self) -> None:
        entry = self.save.find_one("Test.Int")
        before = bytes(self.save.data)
        self.save.set_scalar(entry, 12345)
        after = bytes(self.save.data)
        changed = [index for index, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(changed, list(range(entry.offset + 4, entry.offset + 8)))
        self.assertEqual(entry.value, 12345)
        self.assertEqual(savefile.SaveFile(after).find_one("Test.Int").value, 12345)

    def test_set_bool_and_float(self) -> None:
        self.save.set_value(self.save.find_one("Test.Bool"), False)
        self.assertIs(self.save.find_one("Test.Bool").value, False)
        self.save.set_value(self.save.find_one("Test.Float"), 3.5)
        self.assertAlmostEqual(self.save.find_one("Test.Float").value, 3.5, places=5)

    def test_set_fixed_string(self) -> None:
        entry = self.save.find_one("Test.String")
        before = bytes(self.save.data)
        self.save.set_string(entry, "HyruleFielD")
        after = bytes(self.save.data)
        changed = [index for index, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertTrue(changed)
        self.assertTrue(all(entry.raw <= index < entry.raw + 16 for index in changed))
        self.assertEqual(savefile.SaveFile(after).find_one("Test.String").value, "HyruleFielD")

    def test_string_too_long(self) -> None:
        entry = self.save.find_one("Test.String")
        with self.assertRaises(ValueError):
            self.save.set_string(entry, "This is longer than sixteen bytes")

    def test_unsupported_edit_is_refused(self) -> None:
        entry = self.save.find_one("Test.Ints")
        with self.assertRaises(ValueError):
            self.save.set_value(entry, [1, 2, 3])

    def test_summary(self) -> None:
        summary = self.save.summary()
        self.assertEqual(summary["magic"], "0x01020304")
        self.assertEqual(summary["version"], 2_982_123)
        self.assertEqual(len(summary["sections"]), 9)


class EmptySectionTests(unittest.TestCase):
    """account.sav and option.sav write a marker for every type, even empty."""

    def test_all_markers_parse_and_round_trip(self) -> None:
        raw, _ = build_empty_sections()
        save = savefile.SaveFile(raw)
        self.assertEqual([section.type_id for section in save.sections], list(range(0x21)))
        self.assertEqual(sum(len(section.entries) for section in save.sections), 0)
        self.assertEqual(save.data_offset, 0x20 + 0x21 * 8)
        self.assertEqual(save.to_bytes(), raw)


class AccountLikeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw, _ = build_account_like()
        self.save = savefile.SaveFile(self.raw)

    def test_account_shape(self) -> None:
        self.assertIs(self.save.find_one("Account.Slot0").value, True)
        self.assertIs(self.save.find_one("Account.Slot1").value, False)
        self.assertEqual(self.save.find_one("Account.Keys").value, [0x0123456789ABCDEF])
        self.assertEqual(self.save.to_bytes(), self.raw)
        self.assertEqual(len(self.save.sections), 2)


class OptionLikeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw, _ = build_option_like()
        self.save = savefile.SaveFile(self.raw)

    def test_option_types(self) -> None:
        self.assertIs(self.save.find_one("Option.Flag").value, True)
        self.assertEqual(self.save.find_one("Option.Value").value, 42)
        self.assertEqual(self.save.find_one("Option.Mode").value, 0xAABBCCDD)
        self.assertEqual(self.save.find_one("Option.Blob").value, b"hello")
        self.assertEqual(self.save.find_one("Option.Big").value, 0x1122334455667788)
        self.assertEqual(self.save.find_one("Option.Bigs").value, [1, 2, -3])
        self.assertEqual(
            self.save.find_one("Option.Keys").value,
            [0xDEADBEEFCAFEBABE],
        )
        self.assertEqual(self.save.to_bytes(), self.raw)


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="hookshot-schema-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_schema_json(self) -> None:
        enum_hash = savefile.murmur3_32("Test.Enum")
        path = self.root / "schema.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2_982_123,
                    "fields": {
                        "0xA3DB7114": {"name": "Game", "type": "Bool64bitKey"},
                        "0x%08X" % enum_hash: {
                            "name": "Test.Enum",
                            "type": "Enum",
                            "enum": {
                                "0xDEADBEEF": "TestValue",
                                "0x11111111": "OtherValue",
                            },
                        },
                        "0x%08X" % savefile.murmur3_32("Test.Int"): "Test.Int",
                    },
                    "enums": {"0xCAFEBABE": "GlobalValue"},
                }
            ),
            encoding="utf-8",
        )
        schema = savefile.load_schema(path)
        self.assertEqual(schema.version, 2_982_123)
        self.assertEqual(schema.name(0xA3DB7114), "Game")
        self.assertEqual(schema.type_name(0xA3DB7114), "Bool64bitKey")
        self.assertEqual(schema.name(enum_hash), "Test.Enum")
        self.assertEqual(schema.enum_name(enum_hash, 0xDEADBEEF), "TestValue")
        self.assertEqual(schema.enum_name(enum_hash, 0x11111111), "OtherValue")
        self.assertEqual(schema.enum_name(0, 0xCAFEBABE), "GlobalValue")
        table = savefile.load_name_table(path)
        self.assertEqual(table[0xA3DB7114], "Game")
        self.assertEqual(table[enum_hash], "Test.Enum")

    def test_plain_names_json_and_text(self) -> None:
        plain = self.root / "names.json"
        plain.write_text(
            json.dumps({"0x%08X" % savefile.murmur3_32("Test.Int"): "Test.Int"}),
            encoding="utf-8",
        )
        schema = savefile.load_schema(plain)
        self.assertEqual(schema.name(savefile.murmur3_32("Test.Int")), "Test.Int")

        text = self.root / "names.tsv"
        text.write_text(
            "0x%08X\tTest.Int\tInt\nGame\n" % savefile.murmur3_32("Test.Int"),
            encoding="utf-8",
        )
        schema = savefile.load_schema(text)
        self.assertEqual(schema.name(savefile.murmur3_32("Test.Int")), "Test.Int")
        self.assertEqual(schema.type_name(savefile.murmur3_32("Test.Int")), "Int")
        self.assertEqual(schema.name(0xA3DB7114), "Game")

    def test_describe_enum_uses_schema(self) -> None:
        schema = savefile.Schema()
        entry_hash = savefile.murmur3_32("Test.Enum")
        schema.fields[entry_hash] = savefile.FieldInfo(
            name="Test.Enum", enum={0xDEADBEEF: "TestValue"}
        )
        entry = savefile.Entry(entry_hash, 0, savefile.ENUM, 0xDEADBEEF, 0xDEADBEEF)
        self.assertIn("TestValue", savefile.describe_value(entry, schema=schema))


class BadFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw, _ = build_fixture()

    def test_bad_magic(self) -> None:
        broken = bytearray(self.raw)
        struct.pack_into("<I", broken, 0, 0xDEADBEEF)
        with self.assertRaises(savefile.SaveFormatError):
            savefile.SaveFile(broken)

    def test_truncated_header(self) -> None:
        with self.assertRaises(savefile.SaveFormatError):
            savefile.SaveFile(self.raw[:8])

    def test_data_offset_past_end(self) -> None:
        broken = bytearray(self.raw)
        struct.pack_into("<I", broken, 8, len(broken) + 8)
        with self.assertRaises(savefile.SaveFormatError):
            savefile.SaveFile(broken)

    def test_unknown_section_type(self) -> None:
        broken = bytearray(self.raw)
        struct.pack_into("<I", broken, 0x24, 0x7F)  # first marker's value
        with self.assertRaises(savefile.SaveFormatError):
            savefile.SaveFile(broken)

    def test_pointer_into_entry_table(self) -> None:
        broken = bytearray(self.raw)
        # The first String16 entry is the first entry after its marker; its
        # raw value is at the marker + 8 + 4.
        index = self.raw.find(struct.pack("<I", savefile.murmur3_32("Test.String")))
        self.assertGreater(index, 0)
        struct.pack_into("<I", broken, index + 4, 0x20)
        with self.assertRaises(savefile.SaveFormatError):
            savefile.SaveFile(broken)


class MurmurTests(unittest.TestCase):
    def test_known_hashes(self) -> None:
        self.assertEqual(savefile.murmur3_32("Game"), 0xA3DB7114)
        self.assertEqual(
            savefile.murmur3_32("PlayerStatus.AssignableItem.BottleInfo.BottleC.BottleItem"),
            0xB80C70A8,
        )
        self.assertEqual(savefile.murmur3_32("Player_Rot_For_Map"), 0xA4AEE8CD)

    def test_resolve_hash(self) -> None:
        self.assertEqual(savefile.resolve_hash(0xA3DB7114), 0xA3DB7114)
        self.assertEqual(savefile.resolve_hash("0xA3DB7114"), 0xA3DB7114)
        self.assertEqual(savefile.resolve_hash(str(0xA3DB7114)), 0xA3DB7114)
        self.assertEqual(savefile.resolve_hash("Game"), 0xA3DB7114)


class RealSaveTests(unittest.TestCase):
    """Best-effort checks against the user's own saves; skipped when absent."""

    @classmethod
    def setUpClass(cls) -> None:
        candidates = [
            os.environ.get("HOOKSHOT_SAVE_DIR"),
            r"I:\Hookshot\user\save",
        ]
        cls.files: list[Path] = []
        for candidate in candidates:
            if not candidate:
                continue
            root = Path(candidate)
            if not root.exists():
                continue
            cls.files = sorted(root.rglob("*.sav"))
            if cls.files:
                break

    def _find(self, name: str) -> Path | None:
        for path in self.files:
            if path.name == name:
                return path
        return None

    def test_real_saves_parse_and_round_trip(self) -> None:
        if not self.files:
            self.skipTest("no real save folder found")
        for path in self.files:
            with self.subTest(path=str(path)):
                save = savefile.SaveFile.load(path)
                self.assertEqual(save.to_bytes(), path.read_bytes())
                self.assertGreaterEqual(len(save.sections), 1)

    def test_account_and_option_layout(self) -> None:
        if not self.files:
            self.skipTest("no real save folder found")
        for name in ("account.sav", "option.sav"):
            path = self._find(name)
            if path is None:
                continue
            with self.subTest(name=name):
                save = savefile.SaveFile.load(path)
                self.assertEqual(len(save.sections), 33)
                self.assertEqual(save.to_bytes(), path.read_bytes())

        option = self._find("option.sav")
        if option is not None:
            save = savefile.SaveFile.load(option)
            self.assertTrue(
                any(section.type_id == savefile.BINARY and section.entries
                    for section in save.sections)
            )

    def test_caption_layout(self) -> None:
        if not self.files:
            self.skipTest("no real save folder found")
        caption = self._find("caption.sav")
        if caption is None:
            self.skipTest("no caption.sav in the save folder")
        save = savefile.SaveFile.load(caption)
        strings = [entry.value for entry in save.iter_entries() if isinstance(entry.value, str)]
        self.assertTrue(any("/" in value for value in strings), strings)
        self.assertTrue(any(isinstance(entry.value, tuple) for entry in save.iter_entries()))

    def test_copy_edit_round_trip(self) -> None:
        """A controlled edit against a copy, never the real file."""
        if not self.files:
            self.skipTest("no real save folder found")
        progress = next((path for path in self.files if path.name == "progress.sav"), None)
        if progress is None:
            self.skipTest("no progress.sav in the save folder")
        source_before = progress.read_bytes()
        with tempfile.TemporaryDirectory(prefix="hookshot-edit-") as tmp:
            copy = Path(tmp) / "progress.sav"
            shutil.copy2(progress, copy)
            save = savefile.SaveFile.load(copy)
            entry = next(
                (item for item in save.iter_entries() if item.section_type == savefile.BOOL),
                None,
            )
            self.assertIsNotNone(entry)
            old = entry.value
            entry_hash = entry.hash
            save.set_scalar(entry, not old)
            save.save(copy)
            self.assertNotEqual(copy.read_bytes(), source_before)
            reloaded = savefile.SaveFile.load(copy)
            self.assertEqual(reloaded.find_one(entry_hash).value, not old)
        self.assertEqual(progress.read_bytes(), source_before, "the real save was not touched")


if __name__ == "__main__":
    unittest.main(verbosity=2)
