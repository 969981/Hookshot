"""Authored setup metadata and preflight contracts; --native opts into the Windows C fixture."""
import argparse
from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
from types import SimpleNamespace

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
release = load("setup_space_release_fixture", ROOT / "scripts/release_parts.py")


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


class ReusedSpaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = identity.PackageTests("test_parts_share_the_publisher_guard_and_gui_identity")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        guard = mock.patch.object(producer, "verify_staged", side_effect=identity.packer.verify_staged)
        guard.start()
        self.addCleanup(guard.stop)
        self.base = self.fixture.output.parent
        self.previous = self.base / "previous.txt"
        self.companion = self.base / "previous-space.txt"
        self.archives = self.base / "archives"
        self.archives.mkdir()
        self.out = self.base / "parts-space"
        self.lines, self.facts = [], []
        for name in producer.TOOLS_PARTS:
            archive = self.archives / ("HookshotBuilder-" + name + "-fixture.zip")
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("HookshotBuilder/tools/" + name + "/empty/", b"")
                zipped.writestr("HookshotBuilder/tools/" + name + "/payload", name.encode())
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.lines.append("part %s %d . - - https://fixture.invalid/%s" % (sha, archive.stat().st_size, archive.name))
            self.facts.append((sha, producer.archive_space(archive)))
        self.previous.write_text("tag old\n" + "\n".join(self.lines) + "\n", encoding="utf-8")
        self.companion.write_text(producer.space_text(self.previous.read_bytes(), self.facts), encoding="ascii", newline="\n")

    def publish(self, companion=True):
        return producer.build_parts(self.fixture.stage, identity.TAG, self.out, repo=self.fixture.repo,
            reuse=self.previous, reuse_space=self.companion if companion else None)

    def test_real_publisher_reuses_bound_facts_and_recomputes_staged_tool_part(self):
        self.assertEqual(self.publish(), self.fixture.revision)
        manifest = (self.out / "hookshot-setup.txt").read_bytes()
        facts = producer.read_space(manifest, (self.out / producer.SPACE_NAME).read_bytes())
        for name, line, (sha, values) in zip(producer.TOOLS_PARTS, self.lines, self.facts):
            if (self.fixture.stage / "tools" / name).is_dir():
                self.assertNotIn(line.encode(), manifest)
                archive = next(self.out.glob("HookshotBuilder-" + name + "-*.zip"))
                self.assertEqual(facts[hashlib.sha256(archive.read_bytes()).hexdigest()], producer.archive_space(archive))
            else:
                self.assertIn(line.encode(), manifest)
                self.assertEqual(facts[sha], values)
        self.assertIn(("identity 1 " + self.fixture.revision).encode(), manifest)

    def test_missing_or_stale_facts_fail_before_any_output(self):
        with self.assertRaisesRegex(ValueError, "--reuse-space"):
            self.publish(False)
        self.assertFalse(self.out.exists())
        original = self.companion.read_bytes()
        self.companion.unlink()
        with self.assertRaisesRegex(ValueError, "existing verified ZIPs"):
            self.publish()
        self.assertFalse(self.out.exists())
        self.companion.write_bytes(original)
        self.previous.write_bytes(self.previous.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "exact setup list"):
            self.publish()
        self.assertFalse(self.out.exists())

    def test_fully_staged_parts_do_not_require_inherited_facts(self):
        for name in producer.TOOLS_PARTS:
            path = self.fixture.stage / "tools" / name
            path.mkdir(exist_ok=True)
            (path / "authored-dependency").write_bytes(name.encode())
        self.companion.unlink()
        self.assertEqual(self.publish(False), self.fixture.revision)
        manifest = (self.out / "hookshot-setup.txt").read_bytes()
        facts = producer.read_space(manifest, (self.out / producer.SPACE_NAME).read_bytes())
        self.assertTrue(all(sha not in facts for sha, _ in self.facts))

    def test_duplicate_unknown_missing_overflow_and_inconsistent_facts_fail(self):
        original = self.companion.read_bytes()
        for bad in (original + original.splitlines()[2] + b"\n", b"\n".join(original.splitlines()[:-1]) + b"\n",
                    original.replace(self.facts[0][0].encode(), b"a" * 64),
                    original.replace(b"space ", b"unknown ", 1),
                    original + b"space " + b"c" * 64 + b" 18446744073709551616 1 1 1\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    producer.read_space(self.previous.read_bytes(), bad)

    def test_offline_bootstrap_uses_actual_zip_facts_and_never_fetches(self):
        before = {p.name: p.read_bytes() for p in self.archives.iterdir()}
        output = self.base / "bootstrapped-space.txt"
        with mock.patch.object(release, "get", side_effect=AssertionError("offline bootstrap requested network")), \
                mock.patch.object(release.sys, "argv", ["release_parts.py", "space", str(self.previous), str(self.archives), str(output)]):
            self.assertEqual(release.main(), 0)
        self.assertEqual(output.read_bytes(), self.companion.read_bytes())
        self.assertEqual({p.name: p.read_bytes() for p in self.archives.iterdir()}, before)
        for _, (expanded, regular, entries, _) in producer.read_space(self.previous.read_bytes(), output.read_bytes()).items():
            self.assertGreater(expanded, 0)
            self.assertEqual((regular, entries), (1, 2))

    def test_offline_bad_sha_truncation_or_missing_zip_preserves_previous_companion(self):
        output = self.base / "preserve-space.txt"
        output.write_bytes(b"previous companion")
        archive = next(self.archives.iterdir())
        data = archive.read_bytes()
        for replacement in (b"x" * len(data), data[:-1], None):
            if replacement is None:
                archive.unlink()
            else:
                archive.write_bytes(replacement)
            with mock.patch.object(release, "get", side_effect=AssertionError("offline bootstrap requested network")):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    release.space(SimpleNamespace(list=self.previous, archives=self.archives, out=output))
            self.assertEqual(output.read_bytes(), b"previous companion")
            archive.write_bytes(data)

    def test_offline_rename_counts_empty_directories_and_utf16_units(self):
        archive = self.archives / "llvm.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("old/", b"")
            zipped.writestr("old/empty/\U00010437/", b"")
            zipped.writestr("old/file", b"123")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.previous.write_text("part %s %d HookshotBuilder/tools old llvm-mingw https://fixture.invalid/llvm.zip\n" %
                                 (sha, archive.stat().st_size), encoding="ascii")
        output = self.base / "rename-space.txt"
        self.assertEqual(release.space(SimpleNamespace(list=self.previous, archives=self.archives, out=output)), 0)
        facts = producer.read_space(self.previous.read_bytes(), output.read_bytes())
        self.assertEqual(facts[sha], (3, 1, 3, len("HookshotBuilder/tools/llvm-mingw/empty/\U00010437".encode("utf-16-le")) // 2))

    def test_offline_output_cannot_overwrite_any_input(self):
        for output in [self.previous, next(self.archives.iterdir())]:
            before = output.read_bytes()
            with self.assertRaisesRegex(ValueError, "separately"):
                release.space(SimpleNamespace(list=self.previous, archives=self.archives, out=output))
            self.assertEqual(output.read_bytes(), before)

    def previous_release(self, companion=True, corrupt=False):
        list_url, space_url = "https://fixture.invalid/list", "https://fixture.invalid/space"
        assets = [{"name": "hookshot-setup.txt", "browser_download_url": list_url}]
        if companion:
            assets.append({"name": producer.SPACE_NAME, "browser_download_url": space_url})
        data = {"https://api.github.com/repos/fixture/repo/releases?per_page=30":
                json.dumps([{"draft": False, "prerelease": False, "tag_name": "old", "assets": assets}]).encode(),
                list_url: self.previous.read_bytes(), space_url: b"stale" if corrupt else self.companion.read_bytes()}
        return data

    def test_previous_fetches_only_matching_small_pair(self):
        data = self.previous_release()
        output, companion = self.base / "fetched-list", self.base / "fetched-space"
        with mock.patch.object(release, "get", side_effect=lambda url, **kwargs: data[url]) as fetch:
            self.assertEqual(release.previous(SimpleNamespace(repo=["fixture/repo"], skip_tag="current", out=output, space_out=companion)), 0)
        self.assertEqual(output.read_bytes(), self.previous.read_bytes())
        self.assertEqual(companion.read_bytes(), self.companion.read_bytes())
        self.assertEqual(len(fetch.call_args_list), 3)
        self.assertEqual([call.kwargs.get("limit") for call in fetch.call_args_list[1:]], [262144, 262144])

    def test_first_adoption_or_stale_pair_is_explicit_failure_without_output_writes(self):
        output, companion = self.base / "fetched-list", self.base / "fetched-space"
        output.write_bytes(b"old list")
        companion.write_bytes(b"old space")
        for has_space, corrupt in ((False, False), (True, True)):
            data = self.previous_release(has_space, corrupt)
            with mock.patch.object(release, "get", side_effect=lambda url, **kwargs: data[url]), redirect_stderr(io.StringIO()) as errors:
                status = release.previous(SimpleNamespace(repo=["fixture/repo"], skip_tag="current", out=output, space_out=companion))
            self.assertEqual(status, 2)
            self.assertIn("existing verified ZIPs", errors.getvalue())
            self.assertEqual((output.read_bytes(), companion.read_bytes()), (b"old list", b"old space"))

    def test_metadata_fetch_rejects_oversized_response_without_payload_download(self):
        with mock.patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 17)):
            with self.assertRaisesRegex(ValueError, "size limit"):
                release.get("https://fixture.invalid/space", tries=1, limit=16)

    def test_pair_write_failure_is_not_the_no_prior_release_fallback(self):
        data = self.previous_release()
        output, companion = self.base / "fetched-list", self.base / "fetched-space"
        with mock.patch.object(release, "get", side_effect=lambda url, **kwargs: data[url]), \
                mock.patch.object(Path, "write_bytes", side_effect=PermissionError("authored denied output")), redirect_stderr(io.StringIO()):
            self.assertEqual(release.previous(SimpleNamespace(repo=["fixture/repo"], skip_tag="current", out=output, space_out=companion)), 2)
        self.assertFalse(output.exists())
        self.assertFalse(companion.exists())

    def test_pair_outputs_must_be_separate_before_fetch_or_write(self):
        data = self.previous_release()
        output = self.base / "same-output"
        with mock.patch.object(release, "get", side_effect=lambda url, **kwargs: data[url]) as fetch, redirect_stderr(io.StringIO()):
            self.assertEqual(release.previous(SimpleNamespace(repo=["fixture/repo"], skip_tag="current", out=output, space_out=output)), 2)
        self.assertEqual(len(fetch.call_args_list), 1)
        self.assertFalse(output.exists())


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
    def test_source_version_must_also_be_marked_owned(self):
        (self.root / "owned-parent/HookshotBuilder/versions/v1/.hookshot-install-version").unlink()
        self.check_case("source-unowned")
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
