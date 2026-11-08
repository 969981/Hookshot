"""Authored setup metadata and preflight contracts; --native opts into the Windows C fixture."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NATIVE = False
CC = "cl"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = load("setup_plan_identity_fixture", ROOT / "tests/test_release_identity.py")
producer = identity.setup_parts


class PublisherSpaceTests(unittest.TestCase):
    def test_archive_facts_are_unpacked_bytes_not_compressed_or_allocated_size(self):
        with tempfile.TemporaryDirectory(prefix="setup-space-") as temporary:
            archive = Path(temporary) / "authored.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
                out.writestr("HookshotBuilder/a.txt", b"x" * 17)
                out.writestr("HookshotBuilder/empty.txt", b"")
            self.assertEqual(producer.archive_space(archive), (17, 2, 2, 25))
            self.assertNotEqual(archive.stat().st_size, 17)

    def test_real_publisher_emits_companion_without_changing_legacy_manifest_grammar(self):
        fixture = identity.PackageTests("test_parts_share_the_publisher_guard_and_gui_identity")
        fixture.setUp()
        try:
            # A tiny authored stand-in for a .NET runtime part; never loaded or executed.
            (fixture.stage / "runtime.dll").write_bytes(b"authored runtime bytes, not PE")
            out = fixture.output.parent / "parts-space"
            with mock.patch.object(producer, "verify_staged", side_effect=identity.packer.verify_staged):
                producer.build_parts(fixture.stage, identity.TAG, out, base="https://fixture.invalid/parts", repo=fixture.repo)
            manifest = (out / "hookshot-setup.txt").read_bytes()
            self.assertTrue(all(line.split()[0] in (b"tag", b"identity", b"part", b"run") for line in manifest.splitlines()))
            lines = (out / "hookshot-setup-space.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[:2], ["HookshotSetupSpace 1", "manifest " + hashlib.sha256(manifest).hexdigest()])
            hints = {fields[1]: tuple(map(int, fields[2:])) for fields in map(str.split, lines[2:])}
            parts = [line.split() for line in manifest.decode().splitlines() if line.startswith("part ")]
            self.assertEqual(set(hints), {part[1] for part in parts})
            runtime_sha = next(part[1] for part in parts if "dotnet-" in part[6])
            self.assertEqual(hints[runtime_sha][:3], (30, 1, 1))
            self.assertEqual(hints[producer.LLVM[0]], (753120375, 9070, 9316, 118))
            for archive in out.glob("*.zip"):
                self.assertIn(hashlib.sha256(archive.read_bytes()).hexdigest(), hints)
        finally:
            fixture.doCleanups()

    def test_changed_external_archive_cannot_reuse_old_space_facts(self):
        with mock.patch.object(producer, "LLVM", ("a" * 64, *producer.LLVM[1:])):
            with self.assertRaisesRegex(ValueError, "exact LLVM archive"):
                producer.llvm_space()
        with mock.patch.object(producer, "LLVM", (producer.LLVM[0], producer.LLVM[1] + 1, *producer.LLVM[2:])):
            with self.assertRaisesRegex(ValueError, "exact LLVM archive"):
                producer.llvm_space()

    def test_metadata_digest_changes_when_manifest_changes(self):
        facts = [("a" * 64, (0, 1, 1, 24))]
        before = producer.space_text(b"authored manifest\n", facts)
        after = producer.space_text(b"different manifest\n", facts)
        self.assertNotEqual(before.splitlines()[1], after.splitlines()[1])
        self.assertEqual(before.splitlines()[2:], after.splitlines()[2:])


def owned(temporary):
    root = Path(temporary.name).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("setup-plan-"):
        raise AssertionError("Not an authored setup-plan temporary directory")
    return root


def cleanup(temporary):
    owned(temporary)  # Verify the resolved absolute target before the library's recursive removal.
    temporary.cleanup()


class NativeSetupPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NATIVE or os.name != "nt":
            raise unittest.SkipTest("Native Win32 plan fixture requires Windows and explicit --native")
        cls.temporary = tempfile.TemporaryDirectory(prefix="setup-plan-build-")
        cls.addClassCleanup(cleanup, cls.temporary)
        cls.build = owned(cls.temporary)
        records, parts = [], []
        for name, payload in (("a.txt", b"x" * 17), ("empty.txt", b"")):
            archive = cls.build / (name + ".zip")
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
                out.writestr("HookshotBuilder/" + name, payload)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            parts.append((digest, archive.stat().st_size))
            records.append((digest, producer.archive_space(archive)))
        manifest = "".join("part %s %d . - - https://fixture.invalid/part\n" % part for part in parts).encode()
        digest = hashlib.sha256(manifest).hexdigest()
        good = producer.space_text(manifest, records)
        duplicate = good + good.splitlines()[2] + "\n"
        overflow = "HookshotSetupSpace 1\nmanifest " + digest + "\nspace " + parts[0][0] + " 18446744073709551616 1 1 24\n"
        unknown = good + "unknown data\n"
        defines = {"GOOD_SPACE": good, "DUPLICATE_SPACE": duplicate, "OVERFLOW_SPACE": overflow, "UNKNOWN_SPACE": unknown,
                   "MANIFEST_SHA": digest, "WRONG_MANIFEST_SHA": "f" * 64, "UNKNOWN_PART_SHA": "e" * 64,
                   "PART_A_SHA": parts[0][0], "PART_B_SHA": parts[1][0]}
        text = "\n".join("#define " + name + " " + json.dumps(value) for name, value in defines.items()) + "\n"
        text += "#define PART_A_SIZE %dull\n#define PART_B_SIZE %dull\n" % (parts[0][1], parts[1][1])
        (cls.build / "fixture_data.h").write_text(text, encoding="utf-8")
        cls.exe = cls.build / "setup_plan_test.exe"
        source = ROOT / "tests/setup_plan_test.c"
        env = dict(os.environ)
        if Path(CC).stem.lower() in ("cl", "clang-cl"):
            command = [CC, "/nologo", "/std:c11", "/W4", "/WX", "/I" + str(cls.build), "/I" + str(ROOT / "builder/setup"),
                       str(source), "/Fe:" + str(cls.exe), "/Fo:" + str(cls.build / "fixture.obj")]
        else:
            for name in ("INCLUDE", "LIB", "LIBPATH"):
                env.pop(name, None)
            compiler = Path(shutil.which(CC) or CC).resolve()
            env["PATH"] = str(compiler.parent) + os.pathsep + str(Path(env["SYSTEMROOT"]) / "System32")
            command = [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-municode", "-I" + str(cls.build),
                       "-I" + str(ROOT / "builder/setup"), str(source), "-o", str(cls.exe)]
        subprocess.run(command, env=env, cwd=cls.build, check=True, timeout=60,
                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="setup-plan-case-")
        self.addCleanup(cleanup, self.temporary)
        self.root = owned(self.temporary)
        for parent in ("new-parent", "empty-parent", "foreign-parent", "owned-parent", "other-parent", "space 雪"):
            folder = self.root / parent
            folder.mkdir()
            if parent != "new-parent":
                (folder / "HookshotBuilder").mkdir()
        (self.root / "foreign-parent/HookshotBuilder/user-file.txt").write_bytes(b"preserve authored user file")
        for parent in ("owned-parent", "other-parent"):
            base = self.root / parent / "HookshotBuilder"
            (base / ".hookshot-install-root").write_bytes(b"HookshotInstallRoot 1\n")
            version = base / "versions/v1"
            version.mkdir(parents=True)
            (version / ".hookshot-install-version").write_bytes(b"HookshotInstallVersion 1\n")
            (version / "HookshotBuilder").mkdir()
            (version / "HookshotBuilder/HookshotBuilder-Setup.exe").write_bytes(b"authored bytes, never executed")
            (base / "versions/unrelated").mkdir()
            (base / "versions/unrelated/keep.txt").write_bytes(b"not an installer version")
        (self.root / "portable.exe").write_bytes(b"authored bytes, never executed")
        # At root length 141 the pending 64-character SHA download path is exactly 260 UTF-16 units.
        prefix = str(self.root / "cache-boundary")
        padding = 141 - (len(prefix.encode("utf-16-le")) // 2 + 2 + len("HookshotBuilder"))
        self.assertGreater(padding, 0)
        parent = Path(prefix) / ("x" * padding)
        parent.mkdir(parents=True)
        self.boundary_root = parent / "HookshotBuilder"

    def check_case(self, name):
        before = {str(path.relative_to(self.root)): (path.stat().st_file_attributes, path.read_bytes() if path.is_file() else None)
                  for path in self.root.rglob("*")}
        subprocess.run([str(self.exe), str(self.root), name, str(self.boundary_root)], check=True, timeout=20,
                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
        after = {str(path.relative_to(self.root)): (path.stat().st_file_attributes, path.read_bytes() if path.is_file() else None)
                 for path in self.root.rglob("*")}
        self.assertEqual(before, after, "Preflight must not write, create, remove or persist installer data")

    def test_sha_bound_complete_totals(self): self.check_case("metadata")
    def test_reject_malformed_stale_duplicate_and_overflow_metadata(self): self.check_case("malformed")
    def test_missing_parts_overflow_and_space_budget_fail_closed(self): self.check_case("totals")
    def test_new_empty_owned_and_foreign_roots_and_version_ownership(self): self.check_case("roots")
    def test_spaces_and_unicode(self): self.check_case("unicode")
    def test_source_root_wins_over_saved_preference(self): self.check_case("source")
    def test_cancel_change_commit_gates_and_stale_root(self): self.check_case("gate")
    def test_unsupported_names_and_long_paths(self): self.check_case("paths")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--cc", default="cl")
    parser.add_argument("--publisher-source", type=Path)
    args, rest = parser.parse_known_args()
    NATIVE, CC = args.native, args.cc
    if args.publisher_source:
        producer = load("setup_space_original_producer", args.publisher_source)
    unittest.main(argv=[__file__, *rest])
