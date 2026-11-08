"""Native setup integration with actual production bodies and authored transport/extractor adapters."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CC = "cl"
FLAGS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
REVISION = "b" * 40


def owned(temporary):
    root = Path(temporary.name).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("setup-native-"):
        raise AssertionError("Not an authored setup-native temporary directory")
    return root


def cleanup(temporary):
    owned(temporary)
    temporary.cleanup()


def function(source, name):
    match = re.search(r"^static [^\n]*\b" + name + r"\(", source, re.M)
    if not match:
        raise AssertionError("Missing actual production function: " + name)
    brace = source.index("{", match.start())
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def gui_manifest(exe, dll):
    return json.dumps({"format": 1, "tag": "fixture", "revision": REVISION,
                       "files": {"HookshotBuilder.exe": hashlib.sha256(exe).hexdigest(),
                                 "HookshotBuilder.dll": hashlib.sha256(dll).hexdigest()}}, sort_keys=True).encode()


@unittest.skipUnless(os.name == "nt", "Actual Win32 file sharing/rename and BCrypt integration")
class NativeSetupIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="setup-native-build-")
        cls.addClassCleanup(cleanup, cls.temporary)
        cls.build = owned(cls.temporary)
        source = (ROOT / "builder/setup/setup.c").read_text(encoding="utf-8")
        part = re.search(r"typedef struct \{\s*char sha\[65\];.*?\} Part;", source, re.S).group()
        sink = re.search(r"typedef int \(\*Sink\)[^;]*;", source).group()
        file_sink = re.search(r"typedef struct \{ HANDLE f; BCRYPT_HASH_HANDLE h; \} FileSink;", source).group()
        start = source.index("typedef struct {\n    Part *parts;")
        context = source[start:source.index("} PrepareContext;", start) + len("} PrepareContext;")]
        (cls.build / "native_types.h").write_text("\n".join([part, sink, file_sink, context]), encoding="utf-8")
        names = ["hex", "file_sink", "file_sha256", "w", "mkdirs", "rmtree", "read_small", "write_small", "prune", "retryable",
                 "prepare_error", "prepare_payload", "cache_and_prune", "current_builder", "install_selected"]
        constants = re.search(r"static const int waits\[\].*?enum \{ TRIES = 8 \};", source, re.S).group()
        (cls.build / "native_helpers.h").write_text(constants + "\n" + "\n\n".join(function(source, name) for name in names), encoding="utf-8")
        cls.exe_bytes, cls.dll_bytes = b"authored GUI bytes; never executed", b"authored DLL bytes; never loaded"
        cls.manifest = gui_manifest(cls.exe_bytes, cls.dll_bytes)
        cls.files = {"HookshotBuilder.exe": cls.exe_bytes, "HookshotBuilder.dll": cls.dll_bytes,
                     "builder-identity.json": cls.manifest, "tools/version.txt": b"fixture\n", "tools/revision.txt": (REVISION + "\n").encode()}
        cls.archive = cls.build / "authored.zip"
        with zipfile.ZipFile(cls.archive, "w", zipfile.ZIP_DEFLATED) as out:
            for name, payload in cls.files.items():
                out.writestr("HookshotBuilder/" + name, payload)
        cls.sha = hashlib.sha256(cls.archive.read_bytes()).hexdigest()
        (cls.build / "native_data.h").write_text('#define PART_SHA "' + cls.sha + '"\n#define PART_SIZE ' + str(cls.archive.stat().st_size) +
                                                'ull\n#define TEST_REVISION "' + REVISION + '"\n', encoding="utf-8")
        env = dict(os.environ)
        cls.exe = cls.build / "setup_native_test.exe"
        includes = [str(cls.build), str(ROOT / "builder/setup")]
        c_source = str(ROOT / "tests/setup_native_test.c")
        if Path(CC).stem.lower() in ("cl", "clang-cl"):
            command = [CC, "/nologo", "/std:c11", "/W4", "/WX", *["/I" + p for p in includes], c_source,
                       "/Fe:" + str(cls.exe), "/Fo:" + str(cls.build / "native.obj"), "bcrypt.lib", "shell32.lib"]
        else:
            for key in ("INCLUDE", "LIB", "LIBPATH"):
                env.pop(key, None)
            compiler = Path(shutil.which(CC) or CC).resolve()
            env["PATH"] = str(compiler.parent) + os.pathsep + str(Path(env["SYSTEMROOT"]) / "System32")
            command = [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-municode", *["-I" + p for p in includes], c_source,
                       "-o", str(cls.exe), "-lbcrypt", "-lshell32"]
        compiled = subprocess.run(command, cwd=cls.build, env=env, timeout=60, creationflags=FLAGS,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if compiled.stdout:
            print(compiled.stdout, end="", flush=True)
        compiled.check_returncode()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="setup-native-case-")
        self.addCleanup(cleanup, self.temporary)
        self.parent = owned(self.temporary) / "space 雪"
        self.parent.mkdir()
        (self.parent / "preferences").mkdir()
        self.root = self.parent / "HookshotBuilder"
        (self.root / "versions/v1/HookshotBuilder").mkdir(parents=True)
        (self.root / ".hookshot-install-root").write_bytes(b"HookshotInstallRoot 1\n")
        (self.root / "versions/v1/.hookshot-install-version").write_bytes(b"HookshotInstallVersion 1\n")
        (self.root / "versions/v1/HookshotBuilder/HookshotBuilder.exe").write_bytes(b"previous authored GUI")
        (self.root / "versions/v1/HookshotBuilder/unlocked.py").write_bytes(b"previous unlocked script")
        (self.root / "versions/current.txt").write_bytes(b"v1")
        for unknown in ("legacy", "legacy.part", "unknown.gone"):
            directory = self.root / "versions" / unknown
            directory.mkdir()
            (directory / "user.txt").write_bytes(b"unknown user content")
        (self.root / "downloads").mkdir()
        (self.root / "downloads/.hookshot-download-cache").write_bytes(b"HookshotDownloadCache 1\n")
        (self.root / ("downloads/" + "c" * 64 + ".zip")).write_bytes(b"unreferenced owned cache")
        (self.root / "downloads/user.zip").write_bytes(b"unknown cache filename preserved")
        shutil.copy2(self.archive, self.parent / "authored.zip")
        for name, payload in self.files.items():
            path = self.parent / "payload-source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (self.parent / "old-gui.txt").write_bytes(b"previous authored GUI")
        (self.parent / "forged-identity.json").write_bytes(gui_manifest(b"previous authored GUI", self.dll_bytes))

    def snapshot(self, root):
        return {path.relative_to(root).as_posix(): (path.stat().st_file_attributes, path.read_bytes() if path.is_file() else None)
                for path in root.rglob("*")}

    def run_case(self, mode):
        result = subprocess.run([str(self.exe), str(self.parent), mode], timeout=20, creationflags=FLAGS,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_actual_cancel_coordinator_starts_no_transport_or_writes(self):
        before = self.snapshot(self.parent)
        self.run_case("cancel")
        self.assertEqual(self.snapshot(self.parent), before)

    def test_actual_native_argv_preserves_repair_substring_unicode_quotes_and_slashes(self):
        before = self.snapshot(self.parent)
        self.run_case("argv")
        self.assertEqual(self.snapshot(self.parent), before)

    def test_bad_part_digest_preserves_old_payload_and_cache(self):
        before = self.snapshot(self.root)
        self.run_case("bad-hash")
        self.assertEqual(self.snapshot(self.root), before)

    def test_stale_prepared_gui_is_rejected_before_publish(self):
        before = self.snapshot(self.root)
        self.run_case("stale-prepared")
        self.assertEqual(self.snapshot(self.root), before)

    def check_success(self, mode):
        unknown = {name: self.snapshot(self.root / "versions" / name) for name in ("legacy", "legacy.part", "unknown.gone")}
        self.run_case(mode)
        for name, before in unknown.items():
            self.assertEqual(self.snapshot(self.root / "versions" / name), before)
        self.assertFalse((self.root / ("downloads/" + "c" * 64 + ".zip")).exists())
        self.assertEqual((self.root / ("downloads/" + self.sha + ".zip")).read_bytes(), self.archive.read_bytes())
        self.assertEqual((self.root / "downloads/user.zip").read_bytes(), b"unknown cache filename preserved")
        self.assertFalse(any(path.name.startswith(".setup-") for path in self.root.iterdir()))

    def test_actual_success_cache_cleanup_and_current_only_owned_prune(self):
        self.check_success("success")
        self.assertFalse((self.root / "versions/v1").exists())

    def test_old_gui_and_substituted_local_metadata_cannot_reuse(self): self.check_success("stale-reuse")

    def test_actual_success_only_locator_unicode_and_denied_replace_preserve_old(self):
        self.check_success("locator")
        preferences = self.parent / "preferences/HookshotBuilder"
        self.assertEqual([path.name for path in preferences.iterdir()], ["install-location.txt"])
        self.assertEqual((preferences / "install-location.txt").read_bytes(),
                         ("HookshotInstallLocation 1\n" + str(self.root) + "\n").encode("utf-8"))

    def test_real_locked_old_gui_preserves_all_old_version_bytes_and_attributes(self):
        exe = self.root / "versions/v1/HookshotBuilder/HookshotBuilder.exe"
        exe.chmod(0o444)
        before = self.snapshot(self.root / "versions/v1")
        self.check_success("locked-prune")
        self.assertEqual(self.snapshot(self.root / "versions/v1"), before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cl")
    args, rest = parser.parse_known_args()
    CC = args.cc
    unittest.main(argv=[__file__, *rest])
