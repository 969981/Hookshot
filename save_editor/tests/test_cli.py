"""End-to-end tests for the command-line editor.

Run directly:

    python -I -B save_editor/tests/test_cli.py

Everything is written under a temporary directory; the user's real saves are
never touched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SAVE_EDITOR = HERE.parents[1]
ROOT = HERE.parents[2]
CLI = SAVE_EDITOR / "save_editor.py"

sys.path.insert(0, str(SAVE_EDITOR))
sys.path.insert(0, str(HERE.parent))

import save_editor as cli  # noqa: E402
import savefile  # noqa: E402
from fixture import build_fixture  # noqa: E402


def run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(CLI), *[str(arg) for arg in args]],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hookshot-save-editor-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.progress = self.tmp / "progress.sav"
        self.progress.write_bytes(build_fixture()[0])
        self.names = self.tmp / "names.json"
        known = [
            "Test.Bool", "Test.Int", "Test.Float", "Test.Enum", "Test.String",
            "Test.Vector", "Test.Keys", "Test.Bools", "Test.Ints",
        ]
        self.names.write_text(
            json.dumps({"0x%08X" % savefile.murmur3_32(name): name for name in known}, indent=2),
            encoding="utf-8",
        )

    def test_hash(self) -> None:
        result = run_cli("hash", "Game")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("0xA3DB7114", result.stdout)

    def test_check(self) -> None:
        result = run_cli("check", self.progress)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("OK", result.stdout)
        self.assertIn("version", result.stdout)

    def test_get_by_name_and_hash(self) -> None:
        name = run_cli("get", self.progress, "Test.Int")
        self.assertEqual(name.returncode, 0, name.stdout)
        self.assertIn("-7", name.stdout)
        hashed = run_cli("get", self.progress, "0x%08X" % savefile.murmur3_32("Test.Int"))
        self.assertEqual(hashed.returncode, 0, hashed.stdout)
        self.assertIn("-7", hashed.stdout)

    def test_set_scalar_and_backup(self) -> None:
        result = run_cli("set", self.progress, "Test.Int", "12345")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("-7 -> 12345", result.stdout)
        self.assertIn("backup", result.stdout)
        self.assertEqual(savefile.SaveFile.load(self.progress).find_one("Test.Int").value, 12345)
        backups = list(self.tmp.rglob("progress.sav"))
        self.assertTrue(any("save-backups" in str(path) for path in backups))

    def test_set_bool_and_string(self) -> None:
        result = run_cli("set", self.progress, "Test.Bool", "false")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIs(savefile.SaveFile.load(self.progress).find_one("Test.Bool").value, False)

        result = run_cli("set", self.progress, "Test.String", "HyruleFielD")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            savefile.SaveFile.load(self.progress).find_one("Test.String").value,
            "HyruleFielD",
        )

    def test_set_dry_run_does_not_write(self) -> None:
        before = self.progress.read_bytes()
        result = run_cli("set", self.progress, "Test.Int", "1", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("would set", result.stdout)
        self.assertEqual(self.progress.read_bytes(), before)

    def test_get_and_set_section(self) -> None:
        result = run_cli("get", self.progress, "Test.Int", "--section", "Int")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("-7", result.stdout)

        result = run_cli("set", self.progress, "Test.Int", "5", "--section", "Int", "--no-backup")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(savefile.SaveFile.load(self.progress).find_one("Test.Int").value, 5)

        result = run_cli("get", self.progress, "Test.Int", "--section", "Bool")
        self.assertEqual(result.returncode, 1)

    def test_backup_paths_do_not_collide(self) -> None:
        first = cli.backup_file(self.progress)
        second = cli.backup_file(self.progress)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_unknown_field_fails(self) -> None:
        result = run_cli("get", self.progress, "No.Such.Field")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no entry", result.stdout)

    def test_dump_json_and_section_filter(self) -> None:
        result = run_cli("dump", self.progress, "--json", "--names", self.names)
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["summary"]["magic"], "0x01020304")
        self.assertTrue(data["entries"])
        self.assertTrue(any(entry["name"] == "Test.Int" for entry in data["entries"]))

        result = run_cli("dump", self.progress, "--section", "Int", "--names", self.names)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Test.Int", result.stdout)
        self.assertNotIn("Test.Float", result.stdout)

    def test_dump_nonzero(self) -> None:
        result = run_cli("dump", self.progress, "--nonzero", "--all", "--names", self.names)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Test.Int", result.stdout)
        # The fixture has no zero/empty values, so this mostly checks the flag.
        self.assertNotIn("more; use --all", result.stdout)

    def test_scan_finds_a_name(self) -> None:
        game = self.tmp / "game.bin"
        game.write_bytes(b"\x00\x00Test.Int\x00\x00Game\x00No.Such.Field\x00")
        names = self.tmp / "names.json"
        result = run_cli("scan", "--game", game, "--save", self.progress, "--out", names)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Test.Int", result.stdout)
        self.assertTrue(names.exists())
        data = json.loads(names.read_text(encoding="utf-8"))
        self.assertIn("0x%08X" % savefile.murmur3_32("Test.Int"), data)

    def test_names_template_merge_and_auto_load(self) -> None:
        template = self.tmp / "template.json"
        result = run_cli("names", self.progress, "--out", template)
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(template.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2_982_123)
        self.assertEqual(len(data["fields"]), 9)
        self.assertEqual(
            data["fields"]["0x%08X" % savefile.murmur3_32("Test.Int")]["type"],
            "Int",
        )

        merged = self.tmp / "merged.json"
        result = run_cli("names", self.progress, "--out", merged, "--merge", self.names)
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(merged.read_text(encoding="utf-8"))
        self.assertEqual(
            data["fields"]["0x%08X" % savefile.murmur3_32("Test.Int")]["name"],
            "Test.Int",
        )

        # Auto-discovery: a names.json beside the save is picked up.
        auto = self.tmp / "names.json"
        auto.write_text(self.names.read_text(encoding="utf-8"), encoding="utf-8")
        result = run_cli("dump", self.progress, "--section", "Int", "--limit", "1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Test.Int", result.stdout)
        self.assertIn("names", result.stdout)

    def test_names_scans_game_binary(self) -> None:
        game = self.tmp / "game.bin"
        game.write_bytes(b"\x00Test.Int\x00Game\x00")
        out = self.tmp / "scanned.json"
        result = run_cli("names", self.progress, "--out", out, "--game", game)
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            data["fields"]["0x%08X" % savefile.murmur3_32("Test.Int")]["name"],
            "Test.Int",
        )

    def test_schema_enum_label(self) -> None:
        schema = self.tmp / "schema.json"
        enum_hash = savefile.murmur3_32("Test.Enum")
        schema.write_text(
            json.dumps(
                {
                    "fields": {
                        "0x%08X" % enum_hash: {
                            "name": "Test.Enum",
                            "type": "Enum",
                            "enum": {"0x11111111": "OtherValue"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        result = run_cli("set", self.progress, "Test.Enum", "OtherValue", "--schema", schema)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("enum name", result.stdout)
        self.assertEqual(
            savefile.SaveFile.load(self.progress).find_one("Test.Enum").value,
            0x11111111,
        )
        result = run_cli("get", self.progress, "Test.Enum", "--schema", schema)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("OtherValue", result.stdout)

    def test_list_finds_slots_and_caption(self) -> None:
        account = self.tmp / "user" / "save" / "account"
        slot = account / "slot_00"
        slot.mkdir(parents=True)
        (slot / "progress.sav").write_bytes(self.progress.read_bytes())
        (slot / "caption.sav").write_bytes(self.progress.read_bytes())
        (account / "option.sav").write_bytes(b"")
        result = run_cli("list", "--save-dir", self.tmp / "user" / "save")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("slot_00", result.stdout)
        self.assertIn("scene=HyruleField", result.stdout)

    def test_backup(self) -> None:
        account = self.tmp / "user" / "save" / "account"
        slot = account / "slot_00"
        slot.mkdir(parents=True)
        (slot / "progress.sav").write_bytes(self.progress.read_bytes())
        (account / "option.sav").write_bytes(b"")
        result = run_cli("backup", "--save-dir", self.tmp / "user" / "save")
        self.assertEqual(result.returncode, 0, result.stdout)
        copies = list((self.tmp / "user" / "save-backups").rglob("progress.sav"))
        self.assertEqual(len(copies), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
