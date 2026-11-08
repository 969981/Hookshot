"""Tests for save_editor/schema_report.py (read-only coverage report)."""

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

import savefile  # noqa: E402
import schema_report  # noqa: E402
from fixture import build_fixture, build_save  # noqa: E402


def run(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = schema_report.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class SchemaReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-schema-report-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.save = self.tmp / "progress.sav"
        self.save.write_bytes(build_fixture()[0])
        self.schema = self.tmp / "schema.json"
        self.schema.write_text(
            json.dumps(
                {
                    "version": 2_982_123,
                    "fields": {
                        "0x%08X" % savefile.murmur3_32("Test.Int"): {
                            "name": "Test.Int",
                            "type": "Int",
                        },
                        "0x%08X" % savefile.murmur3_32("Test.Float"): {
                            "name": "Test.Float",
                            "type": "Int",  # deliberate mismatch: actually Float
                        },
                        "0x%08X" % savefile.murmur3_32("Extra.Field"): {
                            "name": "Extra.Field",
                            "type": "Bool",  # schema-only
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_coverage_and_strict(self) -> None:
        code, out, _ = run(self.save, "--names", self.schema)
        self.assertEqual(code, 0)
        self.assertIn("entries 9 (2 named, 7 unnamed)", out)
        self.assertIn("type mismatch 1", out)
        self.assertIn("schema-only    1", out)
        self.assertIn("Test.Float", out)
        self.assertIn("Extra.Field", out)
        self.assertIn("provenance    2 exact, 0 semantic, 7 unknown", out)

        code, _, _ = run(self.save, "--names", self.schema, "--strict")
        self.assertEqual(code, 1)

    def test_version_mismatch(self) -> None:
        data = json.loads(self.schema.read_text(encoding="utf-8"))
        data["version"] = 1
        self.schema.write_text(json.dumps(data), encoding="utf-8")
        code, out, _ = run(self.save, "--names", self.schema)
        self.assertEqual(code, 0)
        self.assertIn("version mismatch 1", out)
        code, _, _ = run(self.save, "--names", self.schema, "--strict")
        self.assertEqual(code, 1)

    def test_directory(self) -> None:
        second = self.tmp / "slot_01"
        second.mkdir()
        (second / "progress.sav").write_bytes(self.save.read_bytes())
        code, out, _ = run(self.tmp, "--names", self.schema)
        self.assertEqual(code, 0)
        self.assertIn("files         2", out)
        self.assertIn("entries 18", out)

    def test_json(self) -> None:
        code, out, _ = run(self.save, "--names", self.schema, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["totals"]["entries"], 9)
        self.assertEqual(data["totals"]["type_mismatches"], 1)
        self.assertEqual(len(data["schema_only"]), 1)

    def test_read_only(self) -> None:
        before = self.save.read_bytes()
        run(self.save, "--names", self.schema)
        self.assertEqual(self.save.read_bytes(), before)

    def _write_critical_fixture(self) -> tuple[Path, Path]:
        life_hash = 0x31AB5580
        exact_hash = savefile.murmur3_32("Test.Exact")
        semantic_hash = savefile.murmur3_32("Test.Semantic")
        data, _ = build_save(
            [
                (
                    savefile.INT,
                    [
                        ("PlayerStatus.Life", 10),
                        ("Test.Exact", 11),
                        ("Test.Semantic", 12),
                        ("Test.Unnamed", 13),
                    ],
                )
            ]
        )
        save = self.tmp / "critical.sav"
        save.write_bytes(data)
        schema = self.tmp / "critical_schema.json"
        schema.write_text(
            json.dumps(
                {
                    "version": 2_982_123,
                    "fields": {
                        "0x%08X" % life_hash: {
                            "name": "PlayerStatus.Life",
                            "type": "Int",
                            "provenance": "curated",
                            "save_file": "progress.sav",
                        },
                        "0x%08X" % exact_hash: {
                            "name": "Test.Exact",
                            "type": "Int",
                        },
                        # The key is Test.Semantic's hash, but the name is not:
                        # with no provenance this is a semantic label.
                        "0x%08X" % semantic_hash: {
                            "name": "Test.NotTheHash",
                            "type": "Int",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        return save, schema

    def test_provenance_by_type_by_save_file_and_critical(self) -> None:
        save, schema = self._write_critical_fixture()
        code, out, _ = run(save, "--names", schema, "--by-type", "--by-save-file")
        self.assertEqual(code, 0)
        self.assertIn("provenance    2 exact, 1 semantic, 1 unknown", out)
        self.assertIn("by type", out)
        self.assertIn("by save file", out)
        self.assertIn("3 named, 1 unnamed", out)
        self.assertIn("progress.sav", out)
        self.assertIn("(none)", out)
        life_line = next(line for line in out.splitlines() if "0x31AB5580" in line)
        self.assertIn("OK", life_line)
        self.assertIn("PlayerStatus.Life", life_line)
        self.assertIn("MISSING 0xFBE01DA1  PlayerStatus.MaxLife", out)

        code, out, _ = run(save, "--names", schema, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["totals"]["exact"], 2)
        self.assertEqual(data["totals"]["semantic"], 1)
        self.assertEqual(data["totals"]["unknown"], 1)
        self.assertEqual(
            data["by_type"]["Int"], {"named": 3, "unnamed": 1, "total": 4}
        )
        self.assertEqual(
            data["by_save_file"]["progress.sav"],
            {"named": 1, "unnamed": 0, "total": 1},
        )
        self.assertEqual(
            data["by_save_file"]["(none)"], {"named": 2, "unnamed": 1, "total": 3}
        )
        self.assertEqual(len(data["named_fields"]), 3)
        self.assertEqual(
            {item["name"] for item in data["named_fields"]},
            {"PlayerStatus.Life", "Test.Exact", "Test.NotTheHash"},
        )
        by_hash = {item["hash"]: item for item in data["critical"]}
        self.assertEqual(len(by_hash), len(schema_report.CRITICAL_FIELDS))
        life = by_hash["0x31AB5580"]
        self.assertTrue(life["present_in_save"])
        self.assertTrue(life["ok"])
        self.assertEqual(life["schema_name"], "PlayerStatus.Life")
        self.assertEqual(life["status"], "OK")
        max_life = by_hash["0xFBE01DA1"]
        self.assertFalse(max_life["present_in_save"])
        self.assertFalse(max_life["ok"])
        self.assertEqual(max_life["status"], "MISSING")

    def test_named_only_hides_unnamed_and_schema_only(self) -> None:
        code, out, _ = run(self.save, "--names", self.schema, "--named-only")
        self.assertEqual(code, 0)
        self.assertNotIn("unnamed", out)
        self.assertNotIn("schema-only", out)
        self.assertNotIn("Extra.Field", out)
        self.assertIn("entries 9", out)
        self.assertIn("Test.Int", out)
        self.assertIn("Test.Float", out)

    def test_named_fields_respects_limit(self) -> None:
        save, schema = self._write_critical_fixture()
        code, out, _ = run(save, "--names", schema, "--json", "--limit", "2")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["named_fields"]), 2)

        code, out, _ = run(save, "--names", schema, "--json", "--named-only")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["named_fields"]), 3)

    def test_no_schema_is_an_error_path(self) -> None:
        bare = self.tmp / "bare"
        bare.mkdir()
        save = bare / "progress.sav"
        save.write_bytes(self.save.read_bytes())
        code, out, _ = run(save)
        self.assertEqual(code, 0)
        self.assertIn("(none: pass --names)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
