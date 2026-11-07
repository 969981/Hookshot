"""Game-free extraction boundary regressions: python -I nx2/test_nup_paths.py."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nup


class Package:
    def __init__(self, names, data=b"authored fixture"):
        self.names, self.data = names, data

    def walk(self, directory):
        for name in self.names:
            yield name, (0, 0, len(self.data), 0, len(self.data))

    def copy_to(self, record, output, name):
        output.write(self.data)


class ExtractionPaths(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / "out"
        self.out.mkdir()
        self.sentinel = self.root / "outside.bin"
        self.sentinel.write_bytes(b"keep outside data")

    def extract(self, names):
        return nup.extract(Package(names), {"romfs": 0}, str(self.out), only="romfs", quiet=True)

    def reject(self, names):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.assertRaises(SystemExit):
            self.extract(names)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def directory_link(self, link, target):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as error:
            if os.name != "nt":
                raise
            # Junctions need no Developer Mode or symbolic-link privilege on Windows.
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True)
            if result.returncode:
                self.fail("Cannot create the link fixture: %s; %s" % (error, result.stderr))

    def test_nested_names_and_resume(self):
        names = ["Map/Forest/data.bin", "Text/Français.bin", "com10.txt", ".hidden", "conifer.txt"]
        self.assertEqual(self.extract(names), (len(names), len(names) * len(b"authored fixture")))
        for name in names:
            self.assertEqual((self.out / "romfs" / name).read_bytes(), b"authored fixture")
        self.assertEqual(self.extract(names), (0, 0))

    def test_paths_cannot_leave_the_output(self):
        names = ["", ".", "..", "../../outside.bin", "nested/../../outside.bin", "nested/../file.bin",
                 "/rooted.bin", "//server/share/file.bin", r"\rooted.bin", r"\\server\share\file.bin",
                 r"C:\outside.bin", "C:/outside.bin", "C:outside.bin", r"nested\..\outside.bin",
                 "nested//file.bin", "nested/.. /outside.bin", "file:stream", "trailing.", "trailing ",
                 "control\x00.bin", "CON", "aux.txt", "NUL.bin", "COM1", "LPT9.txt", "COM¹.txt"]
        for name in names:
            with self.subTest(name=name):
                # Even a broken validator must not let the fixture touch an absolute path or UNC share.
                with patch.object(nup.os.path, "exists", return_value=False), \
                        patch.object(nup.os, "lstat", side_effect=FileNotFoundError), \
                        patch.object(nup.os, "makedirs", side_effect=AssertionError("directory write attempted")) as mkdir, \
                        patch("builtins.open", side_effect=AssertionError("file write attempted")) as opened, \
                        patch.object(nup.os, "replace", side_effect=AssertionError("replacement attempted")) as replace:
                    with self.assertRaises(SystemExit):
                        self.extract([name])
                    mkdir.assert_not_called()
                    opened.assert_not_called()
                    replace.assert_not_called()

    def test_parent_path_cannot_overwrite_a_sibling(self):
        # This also safely demonstrates the original failure: both files belong to this fixture.
        self.reject(["../../outside.bin"])

    def test_ordinary_partial_file_is_replaced(self):
        (self.out / "romfs").mkdir()
        partial = self.out / "romfs" / "file.bin.part"
        partial.write_bytes(b"an interrupted extraction")
        self.extract(["file.bin"])
        self.assertEqual((self.out / "romfs" / "file.bin").read_bytes(), b"authored fixture")
        self.assertFalse(partial.exists())

    def test_part_directory_link_cannot_redirect_extraction(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.directory_link(self.out / "romfs", outside)
        self.reject(["new.bin"])

    def test_nested_directory_link_cannot_redirect_extraction(self):
        (self.out / "romfs").mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.directory_link(self.out / "romfs" / "nested", outside)
        self.reject(["nested/new.bin"])

    def test_directory_reparse_point_cannot_redirect_extraction(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.out / "romfs"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            os.symlink(outside, link, target_is_directory=True)
        self.reject(["new.bin"])

    def test_destination_link_is_rejected_before_resume(self):
        (self.out / "romfs").mkdir()
        try:
            os.symlink(self.sentinel, self.out / "romfs" / "file.bin")
        except OSError as error:
            self.fail("Cannot create the file-link fixture: %s" % error)
        self.reject(["file.bin"])

    def test_temporary_link_cannot_truncate_an_outside_file(self):
        (self.out / "romfs").mkdir()
        try:
            os.symlink(self.sentinel, self.out / "romfs" / "file.bin.part")
        except OSError as error:
            self.fail("Cannot create the file-link fixture: %s" % error)
        self.reject(["file.bin"])

    def test_temporary_hard_link_cannot_truncate_an_outside_file(self):
        (self.out / "romfs").mkdir()
        os.link(self.sentinel, self.out / "romfs" / "file.bin.part")
        self.reject(["file.bin"])


if __name__ == "__main__":
    unittest.main()
