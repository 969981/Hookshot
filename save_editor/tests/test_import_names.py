"""Tests for save_editor/import_names.py (read-only name import).

Everything here is synthetic: a temp schema, a temp save built by fixture.py,
a temp CSV/WordList, a temp printable binary and a temp curated table.  No
game file is read and no real save is touched.
"""

from __future__ import annotations

import contextlib
import csv
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

import fixture  # noqa: E402
import import_names  # noqa: E402
import savefile  # noqa: E402


def run(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = import_names.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class ImportNamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-import-names-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.h_csv = savefile.murmur3_32("TotK.CsvField")
        self.h_wordlist = savefile.murmur3_32("TotK.WordListField")
        self.h_scan = savefile.murmur3_32("OoT.ScanField")
        self.h_semantic = savefile.murmur3_32("OoT.Gamma")
        self.semantic_label = "PlayerStatus.SemanticTest"
        # The curated label really is semantic: it does not hash to the field.
        self.assertNotEqual(savefile.murmur3_32(self.semantic_label), self.h_semantic)

        self.schema = self.tmp / "schema.json"
        self.schema.write_text(
            json.dumps(
                {
                    "version": 2_982_123,
                    "source": "fixture",
                    "save_directory": ["slot_00/progress.sav"],
                    "fields": {
                        "0x%08X" % self.h_csv: {
                            "type": "Int",
                            "default": 5,
                            "reset": 1,
                            "save_file": "slot_00/progress.sav",
                            "save_file_index": 0,
                        },
                        "0x%08X" % self.h_wordlist: {
                            "type": "Float",
                            "default": 1.5,
                        },
                        "0x%08X" % self.h_scan: {"type": "Bool", "default": False},
                        "0x%08X" % self.h_semantic: {
                            "type": "Enum",
                            "default": 0,
                            "reset": 2,
                            "enum": {"0x00000000": "Off", "0x00000001": "On"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        self.csv = self.tmp / "totk.csv"
        with self.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["index", "hash", "decimal", "type", "name"])
            # Valid: the name hashes to the field.
            writer.writerow(["0", "0x%08X" % self.h_csv, str(self.h_csv), "Int", "TotK.CsvField"])
            # Valid for a field the OoT scan also finds; TotK is lower priority.
            writer.writerow(["1", "0x%08X" % self.h_scan, str(self.h_scan), "Bool", "OoT.ScanField"])
            # Invalid: the hash belongs to another name; the wordlist must win.
            writer.writerow(["2", "0x%08X" % self.h_wordlist, str(self.h_wordlist), "Float", "TotK.WrongName"])
            # Valid name, but its hash is not a field in the schema.
            unknown = savefile.murmur3_32("TotK.Unknown")
            writer.writerow(["3", "0x%08X" % unknown, str(unknown), "Int", "TotK.Unknown"])

        self.wordlist = self.tmp / "wordlist.txt"
        self.wordlist.write_text("TotK.WordListField\n", encoding="utf-8")

        self.binary = self.tmp / "oot.bin"
        self.binary.write_bytes(
            b"\x00\x01" + b"OoT.ScanField" + b"\x00\xff" + b"OoT.Gamma" + b"\x00"
        )

        self.curated = self.tmp / "curated.json"
        self.curated.write_text(
            json.dumps(
                {
                    "fields": {
                        # Exact curated name: hashes to the field.
                        "0x%08X" % self.h_csv: {
                            "name": "TotK.CsvField",
                            "note": "curated exact",
                        },
                        # Exact curated name beats the OoT scan.
                        "0x%08X" % self.h_scan: {
                            "name": "OoT.ScanField",
                            "note": "curated beats scan",
                        },
                        # Semantic label: does not hash, but curated is highest.
                        "0x%08X" % self.h_semantic: {
                            "name": self.semantic_label,
                            "note": "semantic label",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        # A synthetic save that must stay byte-identical.
        self.save = self.tmp / "progress.sav"
        self.save.write_bytes(
            fixture.build_save(
                [
                    (savefile.INT, [("Save.Int", -3)]),
                    (savefile.BOOL, [("Save.Flag", True)]),
                ]
            )[0]
        )

        self.out = self.tmp / "out.names.json"

    def test_full_import_and_priority(self) -> None:
        schema_before = self.schema.read_bytes()
        save_before = self.save.read_bytes()
        binary_before = self.binary.read_bytes()

        code, stdout, stderr = run(
            "--schema", self.schema,
            "--save", self.save,
            "--totk-csv", self.csv,
            "--totk-wordlist", self.wordlist,
            "--oot-scan", self.binary,
            "--curated", self.curated,
            "--out", self.out,
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("fields 4, named 4, exact 3, semantic 1", stdout)
        self.assertIn("invalid names skipped 1", stdout)
        self.assertIn("names not in schema 1", stdout)
        self.assertIn(str(self.out), stdout)

        # Read-only: the base schema, the save and the scanned binary are intact.
        self.assertEqual(self.schema.read_bytes(), schema_before)
        self.assertEqual(self.save.read_bytes(), save_before)
        self.assertEqual(self.binary.read_bytes(), binary_before)

        loaded = savefile.load_schema(self.out)
        self.assertEqual(loaded.version, 2_982_123)

        csv_field = loaded.field(self.h_csv)
        self.assertEqual(csv_field.name, "TotK.CsvField")
        self.assertEqual(csv_field.provenance, "curated")  # curated > totk
        self.assertEqual(csv_field.note, "curated exact")
        self.assertEqual(csv_field.type_name, "Int")
        self.assertEqual(csv_field.default, 5)
        self.assertEqual(csv_field.reset, 1)
        self.assertEqual(csv_field.save_file, "slot_00/progress.sav")
        self.assertEqual(csv_field.save_file_index, 0)

        # The invalid CSV row was rejected; the WordList name supplied this one.
        wordlist_field = loaded.field(self.h_wordlist)
        self.assertEqual(wordlist_field.name, "TotK.WordListField")
        self.assertEqual(wordlist_field.provenance, "totk")
        self.assertEqual(wordlist_field.type_name, "Float")
        self.assertEqual(wordlist_field.default, 1.5)

        # OoT scan found it, but curated is higher priority.
        scan_field = loaded.field(self.h_scan)
        self.assertEqual(scan_field.name, "OoT.ScanField")
        self.assertEqual(scan_field.provenance, "curated")
        self.assertEqual(scan_field.note, "curated beats scan")
        self.assertEqual(scan_field.type_name, "Bool")

        # A curated semantic label overrides the OoT exact name.
        semantic_field = loaded.field(self.h_semantic)
        self.assertEqual(semantic_field.name, self.semantic_label)
        self.assertEqual(semantic_field.provenance, "semantic")
        self.assertEqual(semantic_field.note, "semantic label")
        self.assertEqual(semantic_field.enum, {0: "Off", 1: "On"})
        self.assertEqual(semantic_field.reset, 2)

        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2_982_123)
        self.assertEqual(data["source"], "fixture")
        self.assertEqual(data["save_directory"], ["slot_00/progress.sav"])
        item = data["fields"]["0x%08X" % self.h_csv]
        self.assertEqual(item["name"], "TotK.CsvField")
        self.assertEqual(item["provenance"], "curated")
        self.assertEqual(item["note"], "curated exact")

    def test_save_only_builds_schema_from_entry_types(self) -> None:
        before = self.save.read_bytes()
        out = self.tmp / "save-only.names.json"
        code, stdout, stderr = run("--save", self.save, "--out", out)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("fields 2, named 0", stdout)
        self.assertEqual(self.save.read_bytes(), before)

        loaded = savefile.load_schema(out)
        self.assertEqual(loaded.version, 2_982_123)
        self.assertEqual(
            loaded.field(savefile.murmur3_32("Save.Int")).type_name, "Int"
        )
        self.assertEqual(
            loaded.field(savefile.murmur3_32("Save.Flag")).type_name, "Bool"
        )
        self.assertEqual(loaded.name(savefile.murmur3_32("Save.Int")), None)

    def test_no_base_is_an_error(self) -> None:
        code, stdout, stderr = run("--out", self.out)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--schema or --save", stderr)
        self.assertFalse(self.out.exists())

    def test_missing_base_schema_is_an_error(self) -> None:
        code, _, stderr = run(
            "--schema", self.tmp / "missing.json", "--out", self.out
        )
        self.assertEqual(code, 2)
        self.assertIn("missing.json", stderr)
        self.assertFalse(self.out.exists())

    def test_refuses_to_write_inside_slot_folder(self) -> None:
        slot = self.tmp / "slot_00"
        slot.mkdir()
        target = slot / "names.json"
        code, _, stderr = run("--schema", self.schema, "--out", target)
        self.assertEqual(code, 2)
        self.assertIn("slot_", stderr)
        self.assertFalse(target.exists())

    def test_default_output_path(self) -> None:
        self.assertEqual(
            import_names.default_output_path(self.schema),
            self.tmp / "schema.names.json",
        )
        self.assertEqual(
            import_names.default_output_path(Path(tempfile.gettempdir()) / "names.json"),
            Path(tempfile.gettempdir()) / "names.names.json",
        )
        slot_schema = self.tmp / "slot_00" / "schema.json"
        self.assertEqual(
            import_names.default_output_path(slot_schema),
            Path(tempfile.gettempdir()) / "names.json",
        )

    def test_default_output_is_used_without_out(self) -> None:
        code, stdout, _ = run("--schema", self.schema)
        self.assertEqual(code, 0)
        default = self.tmp / "schema.names.json"
        self.assertTrue(default.exists())
        self.assertIn(str(default), stdout)
        self.assertEqual(savefile.load_schema(default).version, 2_982_123)

    def test_bad_scan_bounds_are_an_error(self) -> None:
        code, _, stderr = run(
            "--schema", self.schema, "--oot-scan", self.binary,
            "--min-len", "9", "--max-len", "2", "--out", self.out,
        )
        self.assertEqual(code, 2)
        self.assertIn("--min-len", stderr)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
