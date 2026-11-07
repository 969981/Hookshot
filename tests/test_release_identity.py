"""Authored metadata/staging fixtures only: no game, download or compiler."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_game = load("release_metadata_make_game", ROOT / "nx2/make_game.py")
packer = load("release_metadata_packer", ROOT / "scripts/package_builder.py")
TAG = "foliage-linux-updater"
SHA = "170ce53ca3d69309b3f5151a334136be8b4c38d6"


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tools = Path(self.temp.name) / "tools"
        self.out = Path(self.temp.name) / "game"
        self.tools.mkdir()
        self.out.mkdir()
        self.root_patch = mock.patch.object(make_game, "ROOT", str(self.tools))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def write(self, revision=SHA, protocol="2"):
        (self.tools / "version.txt").write_text(TAG + "\n", encoding="utf-8")
        if revision is not None:
            (self.tools / "revision.txt").write_text(revision + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"HOOKSHOT_BUILDER_EXE": "C:/Fixture/Builder.exe",
                                        "HOOKSHOT_BUILDER_UPDATE_PROTOCOL": protocol}, clear=True):
            make_game.write_release_metadata(str(self.out))
        return (self.out / "hookshot_version.txt").read_text(encoding="utf-8")

    def test_packaged_revision_is_propagated_not_fetched(self):
        self.assertEqual(self.write(), "version=" + TAG + "\nrevision=" + SHA + "\nbuilder=C:/Fixture/Builder.exe\nbuilder_protocol=2\n")

    def test_legacy_tools_do_not_claim_current_or_revision_aware_builder(self):
        self.assertEqual(self.write(None), "version=" + TAG + "\n")

    def test_invalid_revision_is_unknown(self):
        self.assertEqual(self.write("main"), "version=" + TAG + "\n")

    def test_old_builder_is_not_blessed_as_protocol2(self):
        self.assertEqual(self.write(protocol="1"), "version=" + TAG + "\nrevision=" + SHA + "\n")

    def test_source_build_removes_stale_release_stamp_only(self):
        (self.out / "hookshot_version.txt").write_text("version=old\n", encoding="utf-8")
        save = self.out / "authored-save.dat"
        save.write_bytes(b"preserve authored save")
        make_game.write_release_metadata(str(self.out))
        self.assertFalse((self.out / "hookshot_version.txt").exists())
        self.assertEqual(save.read_bytes(), b"preserve authored save")

    def test_failed_stamp_does_not_replace_previous_metadata(self):
        path = self.out / "hookshot_version.txt"
        path.write_text("previous", encoding="utf-8")
        with mock.patch.object(make_game.os, "replace", side_effect=OSError("authored write failure")):
            with self.assertRaisesRegex(OSError, "authored write failure"):
                self.write()
        self.assertEqual(path.read_text(encoding="utf-8"), "previous")
        self.assertEqual(list(self.out.glob(".hookshot-version-*")), [])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.repo, self.stage, self.output = base / "repo", base / "stage", base / "builder.zip"
        self.repo.mkdir()
        self.stage.mkdir()
        self.git("init", "--quiet")
        sources = {"host/runtime.c": "int authored_fixture;\n", "nx2/make_game.py": "# authored fixture\n",
                   "gpu/vk/vk_api.h": "// authored fixture\n",
                   "gpu/third_party/suyu/shader_recompiler/runtime.cpp": "// authored vendored renderer fixture\n",
                   "third_party/opus/include/opus.h": "// authored vendored codec fixture\n",
                   "builder/HookshotBuilder/Updater.cs": "// authored fixture\n"}
        for name, text in sources.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "Authored fixture")
        self.revision = self.git("rev-parse", "HEAD")
        self.git("tag", TAG)
        for tree in ("host", "nx2", "gpu", "third_party"):
            shutil.copytree(self.repo / tree, self.stage / "tools" / tree)
        (self.stage / "builder-revision.txt").write_text(self.revision + "\n", encoding="utf-8")
        (self.stage / "HookshotBuilder.exe").write_text("authored fixture, not executable", encoding="utf-8")
        (self.stage / "HookshotBuilder.dll").write_text("authored fixture, not executable", encoding="utf-8")
        # These authored files are not PE binaries. Actual Windows publish checks
        # exercise the version-resource reader; these cases exercise the producer's guard.
        gui_patch = mock.patch.object(packer, "embedded_revision", return_value=self.revision)
        self.gui_revision = gui_patch.start()
        self.addCleanup(gui_patch.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True,
                                       encoding="utf-8", stderr=subprocess.PIPE).strip()

    def package(self):
        return packer.package(self.repo, self.stage, TAG, self.output)

    def test_verified_source_is_stamped_before_zip(self):
        self.assertEqual(self.package(), self.revision)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("HookshotBuilder/tools/revision.txt").decode(), self.revision + "\n")
            self.assertEqual(archive.read("HookshotBuilder/tools/version.txt").decode(), TAG + "\n")

    def test_old_staged_source_cannot_receive_new_revision(self):
        (self.stage / "tools/host/runtime.c").write_text("old staged code", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "staged source differs"):
            self.package()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.stage / "tools/revision.txt").exists())

    def test_modified_checkout_is_not_head(self):
        (self.repo / "host/runtime.c").write_text("uncommitted", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Commit the build sources"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_stale_vendored_renderer_cannot_receive_new_revision(self):
        (self.stage / "tools/gpu/third_party/suyu/shader_recompiler/runtime.cpp").write_text("old vendored renderer", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "staged source differs.*gpu/third_party"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_missing_vendored_codec_is_rejected(self):
        (self.stage / "tools/third_party/opus/include/opus.h").unlink()
        with self.assertRaisesRegex(ValueError, "sources are missing.*third_party/opus"):
            self.package()

    def test_old_gui_publish_is_rejected(self):
        (self.stage / "builder-revision.txt").write_text(SHA, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Publish the builder"):
            self.package()

    def test_stale_exe_with_fresh_sidecar_is_rejected(self):
        self.gui_revision.side_effect = lambda path: SHA if path.suffix == ".exe" else self.revision
        with self.assertRaisesRegex(ValueError, "GUI was compiled.*HookshotBuilder.exe"):
            self.package()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.stage / "tools/revision.txt").exists())

    def test_stale_managed_gui_with_fresh_exe_is_rejected(self):
        self.gui_revision.side_effect = lambda path: SHA if path.suffix == ".dll" else self.revision
        with self.assertRaisesRegex(ValueError, "GUI was compiled.*HookshotBuilder.dll"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_untracked_gui_source_is_not_the_recorded_commit(self):
        (self.repo / "builder/HookshotBuilder/Untracked.cs").write_text("// not committed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Commit the build sources"):
            self.package()

    def test_missing_and_untracked_build_sources_are_rejected(self):
        path = self.stage / "tools/host/runtime.c"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "sources are missing"):
            self.package()
        path.write_bytes(original)
        (path.parent / "unknown.c").write_text("untracked", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "untracked build source"):
            self.package()

    def test_game_data_is_never_packaged(self):
        (self.stage / "authored.nup").write_text("not real game data", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Game data"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_existing_archive_is_not_overwritten(self):
        self.output.write_bytes(b"preserve existing output")
        with self.assertRaisesRegex(ValueError, "new ZIP"):
            self.package()
        self.assertEqual(self.output.read_bytes(), b"preserve existing output")

    def test_revision_expression_is_not_a_tag(self):
        with self.assertRaisesRegex(ValueError, "valid Git release tag"):
            packer.package(self.repo, self.stage, TAG + "^{commit}", self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
