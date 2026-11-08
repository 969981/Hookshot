"""Game-free ZIP byte compatibility and bounded reads: python -I -B tests/test_setup_parts_stream.py."""
import hashlib
import importlib.util
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_parts_under_test", ROOT / "scripts" / "make_setup_parts.py")
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)


class BoundedReader:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def fileno(self):
        return self.stream.fileno()

    def read(self, size=-1):
        if not 0 < size <= 1 << 20:
            raise AssertionError("package files must be read in bounded blocks")
        return self.stream.read(size)


def original_zip(path, root, names):
    """The released writer, used as a byte-compatibility oracle on small authored fixtures."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo("HookshotBuilder/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (root / name).read_bytes(), compresslevel=6)


class SetupPartsTests(unittest.TestCase):
    def check_archive(self, zip64=False):
        with tempfile.TemporaryDirectory(prefix="hookshot-setup-parts-") as folder:
            root = Path(folder)
            files = {"empty.bin": b"", "nested/François.txt": "authored fixture\n".encode("utf-8"),
                     "large.bin": random.Random(7).randbytes((1 << 20) + 257)}
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            names = list(files)
            baseline, streamed = root / "baseline.zip", root / "streamed.zip"
            original_zip(baseline, root, names)
            real_open = open

            def bounded_open(path, mode="r", *args, **kwargs):
                stream = real_open(path, mode, *args, **kwargs)
                return BoundedReader(stream) if mode == "rb" else stream

            with mock.patch("builtins.open", side_effect=bounded_open), \
                    mock.patch.object(parts, "verified_bytes", side_effect=AssertionError("unverified payload must stream")):
                sha, size = parts.write_zip(str(streamed), str(root), names, {}, {})
            expected = baseline.read_bytes()
            self.assertEqual(streamed.read_bytes(), expected)
            self.assertEqual(sha, hashlib.sha256(expected).hexdigest())
            self.assertEqual(size, len(expected))
            self.assertFalse(Path(str(streamed) + ".new").exists())
            with zipfile.ZipFile(streamed) as archive:
                self.assertEqual(archive.namelist(), ["HookshotBuilder/" + name for name in sorted(names)])
                self.assertIsNone(archive.testzip())
                for name, data in files.items():
                    info = archive.getinfo("HookshotBuilder/" + name)
                    self.assertEqual(archive.read(info), data)
                    self.assertEqual(info.date_time, (2026, 1, 1, 0, 0, 0))
                    self.assertEqual(info.external_attr, 0o644 << 16)
                if zip64:
                    self.assertIn(b"PK\x06\x06", expected)
            for name in names:
                os.utime(root / name, (1_600_000_000, 1_600_000_000))
            self.assertEqual(parts.write_zip(str(streamed), str(root), list(reversed(names)), {}, {}), (sha, size))
            self.assertEqual(streamed.read_bytes(), expected)

    def test_bounded_reads_and_identical_archive(self):
        self.check_archive()

    def test_zip64_without_large_files(self):
        # Exercise the real ZIP64 path with a small fixture instead of allocating gigabytes.
        with mock.patch.object(zipfile, "ZIP64_LIMIT", 1 << 20):
            self.check_archive(zip64=True)


class VerifiedPartsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hookshot-verified-parts-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "staged"
        self.output = Path(temporary.name) / "part.zip"
        self.files = {"tools/host/authored.c": b"int authored_fixture;\n",
                      "tools/host/tracked.bin": b"authored tracked bytes",
                      "HookshotBuilder.exe": b"authored GUI fixture",
                      "HookshotBuilder.dll": b"authored managed fixture",
                      "tools/python/runtime.bin": b"authored runtime bytes"}
        for name, data in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.sources = {}
        for name, data in self.files.items():
            if name.startswith("tools/host/"):
                self.sources[name[len("tools/"):]] = hashlib.sha1(
                    b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        self.gui_hashes = {name: hashlib.sha256(self.files[name]).hexdigest()
                           for name in ("HookshotBuilder.exe", "HookshotBuilder.dll")}

    def write(self):
        return parts.write_zip(str(self.output), str(self.root), list(self.files), self.sources, self.gui_hashes)

    def test_mixed_archive_preserves_bytes_and_verification(self):
        baseline = self.output.with_name("baseline.zip")
        original_zip(baseline, self.root, self.files)
        with mock.patch.object(parts, "verified_bytes", wraps=parts.verified_bytes) as verify:
            self.write()
        self.assertEqual({Path(call.args[0]).relative_to(self.root).as_posix() for call in verify.call_args_list},
                         set(self.files) - {"tools/python/runtime.bin"})
        self.assertEqual(self.output.read_bytes(), baseline.read_bytes())

    def test_changed_sources_and_gui_are_rejected(self):
        self.output.write_bytes(b"preserve the previous archive")
        for name in self.files:
            if name == "tools/python/runtime.bin":
                continue
            with self.subTest(name=name):
                (self.root / name).write_bytes(b"changed after staging")
                with self.assertRaisesRegex(ValueError, "changed during packaging"):
                    self.write()
                self.assertEqual(self.output.read_bytes(), b"preserve the previous archive")
                self.assertFalse(Path(str(self.output) + ".new").exists())
                (self.root / name).write_bytes(self.files[name])

    def test_archive_uses_the_exact_bytes_checked(self):
        verified_bytes = parts.verified_bytes

        def changed_after_read(path, *args):
            data = verified_bytes(path, *args)
            Path(path).write_bytes(b"changed after verification")
            return data

        with mock.patch.object(parts, "verified_bytes", side_effect=changed_after_read):
            self.write()
        with zipfile.ZipFile(self.output) as archive:
            for name, data in self.files.items():
                self.assertEqual(archive.read("HookshotBuilder/" + name), data)

    def test_source_line_ending_normalization_is_preserved(self):
        name = "tools/host/authored.c"
        data = self.files[name].replace(b"\n", b"\r\n")
        (self.root / name).write_bytes(data)
        self.write()
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("HookshotBuilder/" + name), data)

    def test_stream_failure_cleans_temporary_and_preserves_output(self):
        self.output.write_bytes(b"preserve the previous archive")

        def interrupted_copy(source, destination, size):
            destination.write(source.read(4))
            raise OSError("authored read failure")

        with mock.patch.object(parts.shutil, "copyfileobj", side_effect=interrupted_copy):
            with self.assertRaisesRegex(OSError, "authored read failure"):
                self.write()
        self.assertEqual(self.output.read_bytes(), b"preserve the previous archive")
        self.assertFalse(Path(str(self.output) + ".new").exists())

    def test_existing_temporary_is_not_overwritten(self):
        temporary = Path(str(self.output) + ".new")
        temporary.write_bytes(b"another writer's temporary")
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertEqual(temporary.read_bytes(), b"another writer's temporary")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
