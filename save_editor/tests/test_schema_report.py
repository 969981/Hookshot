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
from fixture import build_fixture  # noqa: E402


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
