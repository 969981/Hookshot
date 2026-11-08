"""Tests for save_editor/label_diff.py (game-free)."""

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

import label_diff  # noqa: E402
import savefile  # noqa: E402
from fixture import build_fixture  # noqa: E402

LABEL = "collected heart piece"


def run(*args: object) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = label_diff.main([str(arg) for arg in args])
    return code, out.getvalue(), err.getvalue()


class LabelDiffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-labeldiff-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.raw, _ = build_fixture()
        self.before = self.tmp / "before.sav"
        self.after = self.tmp / "after.sav"
        self.int_hash = savefile.murmur3_32("Test.Int")
        self.string_hash = savefile.murmur3_32("Test.String")
        self._make_pair()

    def _make_pair(self) -> None:
        self.before.write_bytes(self.raw)
        self.after.write_bytes(self.raw)
        save = savefile.SaveFile.load(self.after)
        save.set_value(save.find_one("Test.Int"), 12345)
        save.set_value(save.find_one("Test.String"), "HyruleFielD")
        save.save(self.after)
        self.before_bytes = self.before.read_bytes()
        self.after_bytes = self.after.read_bytes()

    def _assert_saves_unchanged(self) -> None:
        self.assertEqual(self.before.read_bytes(), self.before_bytes)
        self.assertEqual(self.after.read_bytes(), self.after_bytes)

    # ---- labels -----------------------------------------------------------

    def test_semantic_labels_are_written(self) -> None:
        out = self.tmp / "names.json"
        code, stdout, stderr = run(
            self.before, self.after, "--label", LABEL, "--out", out
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("wrote", stdout)

        schema = savefile.load_schema(out)
        integer = schema.field(self.int_hash)
        self.assertIsNotNone(integer)
        self.assertEqual(integer.type_name, "Int")
        self.assertEqual(integer.name, "%s.0x%08X" % (LABEL, self.int_hash))
        self.assertEqual(integer.provenance, "semantic")
        self.assertEqual(integer.note, LABEL)

        string = schema.field(self.string_hash)
        self.assertEqual(string.type_name, "String16")
        self.assertEqual(string.name, "%s.0x%08X" % (LABEL, self.string_hash))
        self.assertEqual(string.provenance, "semantic")
        self.assertEqual(string.note, LABEL)

        self._assert_saves_unchanged()

    def test_exact_name_is_curated(self) -> None:
        out = self.tmp / "curated.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--hash", "0x%08X" % self.int_hash,
            "--name", "Test.Int",
            "--out", out,
        )
        self.assertEqual(code, 0, stderr)

        schema = savefile.load_schema(out)
        integer = schema.field(self.int_hash)
        self.assertEqual(integer.name, "Test.Int")
        self.assertEqual(integer.provenance, "curated")
        self.assertEqual(integer.note, LABEL)
        # The other change was not selected.
        self.assertIsNone(schema.name(self.string_hash))
        self._assert_saves_unchanged()

    def test_note_overrides_label(self) -> None:
        out = self.tmp / "noted.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--note", "heart container pickup",
            "--out", out,
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(out)
        self.assertEqual(schema.field(self.int_hash).note, "heart container pickup")
        self.assertEqual(
            schema.field(self.int_hash).name,
            "%s.0x%08X" % (LABEL, self.int_hash),
        )

    def test_name_is_ignored_when_several_changes_match(self) -> None:
        out = self.tmp / "many.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--name", "Test.Int",
            "--out", out,
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(out)
        self.assertEqual(
            schema.field(self.int_hash).name,
            "%s.0x%08X" % (LABEL, self.int_hash),
        )
        self.assertEqual(schema.field(self.int_hash).provenance, "semantic")

    # ---- selection --------------------------------------------------------

    def test_hash_filter_accepts_hex_decimal_and_name(self) -> None:
        for index, value in enumerate(
            ("0x%08X" % self.int_hash, str(self.int_hash), "Test.Int")
        ):
            with self.subTest(hash=value):
                out = self.tmp / ("filter-%d.json" % index)
                code, _, stderr = run(
                    self.before, self.after,
                    "--label", LABEL,
                    "--hash", value,
                    "--out", out,
                )
                self.assertEqual(code, 0, stderr)
                schema = savefile.load_schema(out)
                self.assertEqual(
                    schema.name(self.int_hash),
                    "%s.0x%08X" % (LABEL, self.int_hash),
                )
                self.assertIsNone(schema.name(self.string_hash))

    def test_multiple_hash_filters(self) -> None:
        out = self.tmp / "both.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--hash", "0x%08X" % self.int_hash,
            "--hash", "0x%08X" % self.string_hash,
            "--out", out,
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(out)
        self.assertEqual(
            schema.name(self.int_hash),
            "%s.0x%08X" % (LABEL, self.int_hash),
        )
        self.assertEqual(
            schema.name(self.string_hash),
            "%s.0x%08X" % (LABEL, self.string_hash),
        )

    def test_no_selected_changes_writes_nothing(self) -> None:
        out = self.tmp / "none.json"
        code, stdout, _ = run(
            self.before, self.before, "--label", LABEL, "--out", out
        )
        self.assertEqual(code, 0)
        self.assertFalse(out.exists())
        self.assertIn("nothing written", stdout)

    # ---- dry run / json ---------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        out = self.tmp / "dry.json"
        code, stdout, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--out", out,
            "--dry-run",
        )
        self.assertEqual(code, 0, stderr)
        self.assertFalse(out.exists())
        self.assertIn("%s.0x%08X" % (LABEL, self.int_hash), stdout)
        self.assertIn("%s.0x%08X" % (LABEL, self.string_hash), stdout)
        self.assertIn("dry run", stdout)
        self._assert_saves_unchanged()

    def test_dry_run_json(self) -> None:
        out = self.tmp / "dry-json.json"
        code, stdout, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--out", out,
            "--dry-run",
            "--json",
        )
        self.assertEqual(code, 0, stderr)
        self.assertFalse(out.exists())
        report = json.loads(stdout)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["written"])
        self.assertEqual(len(report["proposals"]), 2)

    def test_json_report_and_output(self) -> None:
        out = self.tmp / "json-names.json"
        code, stdout, stderr = run(
            self.before, self.after, "--label", LABEL, "--out", out, "--json"
        )
        self.assertEqual(code, 0, stderr)
        # --json must leave stdout parseable even though write_schema prints.
        report = json.loads(stdout)
        self.assertEqual(report["out"], str(out))
        self.assertFalse(report["dry_run"])
        self.assertTrue(report["written"])
        self.assertEqual(len(report["changes"]), 2)

        proposals = {item["hash"]: item for item in report["proposals"]}
        integer = proposals["0x%08X" % self.int_hash]
        self.assertEqual(integer["name"], "%s.0x%08X" % (LABEL, self.int_hash))
        self.assertEqual(integer["provenance"], "semantic")
        self.assertTrue(integer["applied"])
        self.assertTrue(out.is_file())
        self._assert_saves_unchanged()

    # ---- existing names ---------------------------------------------------

    def test_existing_names_need_force(self) -> None:
        existing = self.tmp / "existing.json"
        existing.write_text(
            json.dumps({
                "0x%08X" % self.int_hash: "Existing.Int",
                "0x%08X" % self.string_hash: "Existing.String",
            }),
            encoding="utf-8",
        )
        original_text = existing.read_text(encoding="utf-8")

        kept = self.tmp / "kept.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--names", existing,
            "--out", kept,
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(kept)
        self.assertEqual(schema.name(self.int_hash), "Existing.Int")
        self.assertEqual(schema.name(self.string_hash), "Existing.String")
        self.assertIsNone(schema.field(self.int_hash).provenance)

        forced = self.tmp / "forced.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--names", existing,
            "--out", forced,
            "--force",
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(forced)
        self.assertEqual(
            schema.name(self.int_hash),
            "%s.0x%08X" % (LABEL, self.int_hash),
        )
        self.assertEqual(schema.field(self.int_hash).provenance, "semantic")
        self.assertEqual(
            schema.name(self.string_hash),
            "%s.0x%08X" % (LABEL, self.string_hash),
        )

        # The input names table is user data; it must not be rewritten.
        self.assertEqual(existing.read_text(encoding="utf-8"), original_text)

    def test_force_with_curated_name(self) -> None:
        existing = self.tmp / "existing.json"
        existing.write_text(
            json.dumps({"0x%08X" % self.int_hash: "Old.Int"}),
            encoding="utf-8",
        )
        out = self.tmp / "forced-curated.json"
        code, _, stderr = run(
            self.before, self.after,
            "--label", LABEL,
            "--names", existing,
            "--hash", "0x%08X" % self.int_hash,
            "--name", "Test.Int",
            "--force",
            "--out", out,
        )
        self.assertEqual(code, 0, stderr)
        schema = savefile.load_schema(out)
        self.assertEqual(schema.name(self.int_hash), "Test.Int")
        self.assertEqual(schema.field(self.int_hash).provenance, "curated")

    # ---- default output ---------------------------------------------------

    def test_default_out_is_the_account_folder_for_a_slot(self) -> None:
        account = self.tmp / "account"
        slot = account / "slot_00"
        slot.mkdir(parents=True)
        before = slot / "before.sav"
        after = slot / "after.sav"
        before.write_bytes(self.raw)
        after.write_bytes(self.raw)
        save = savefile.SaveFile.load(after)
        save.set_value(save.find_one("Test.Int"), 99)
        save.save(after)

        code, _, stderr = run(before, after, "--label", LABEL)
        self.assertEqual(code, 0, stderr)
        self.assertTrue((account / "names.json").is_file())
        self.assertFalse((slot / "names.json").exists())

    # ---- bad input --------------------------------------------------------

    def test_bad_input_returns_2(self) -> None:
        missing = self.tmp / "missing.sav"
        code, _, stderr = run(missing, self.after, "--label", LABEL)
        self.assertEqual(code, 2)
        self.assertIn("error", stderr)

        code, _, stderr = run(
            self.before, self.after, "--label", LABEL, "--hash", "0xZZ"
        )
        self.assertEqual(code, 2)
        self.assertIn("error", stderr)

        out = self.tmp / "bad.json"
        code, _, _ = run(
            self.before, self.after, "--label", "   ", "--out", out
        )
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
