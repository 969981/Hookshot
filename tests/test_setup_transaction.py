"""Actual Win32 setup transaction; only authored temporary data, no installer/payload execution."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CC = "cl"
ORIGINAL = None
PREVIEW_OUTPUT = None
FLAGS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)


def owned(temporary):
    root = Path(temporary.name).resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("setup-transaction-"):
        raise AssertionError("Not an authored setup-transaction temporary directory")
    return root


def cleanup(temporary):
    owned(temporary)
    temporary.cleanup()


def function(source, declaration):
    start = source.index(declaration)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@unittest.skipUnless(os.name == "nt", "Exercises real Win32 filesystem and sharing semantics")
class SetupTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="setup-transaction-build-")
        cls.addClassCleanup(cleanup, cls.temporary)
        cls.build = owned(cls.temporary)
        cls.exe = cls.build / "setup_transaction_test.exe"
        env = dict(os.environ)
        defines = []
        if ORIGINAL:
            source = ORIGINAL.read_text(encoding="utf-8")
            # Actual destructive install prefix and helper bodies from the pinned original source.
            prefix = "rmtree(install);\n        rmtree(final_dir);\n        mkdirs(install);"
            if prefix not in source:
                raise AssertionError("Original production prefix changed; do not silently mirror it")
            code = function(source, "static void mkdirs(") + "\n" + function(source, "static void rmtree(")
            code += "\nstatic void original_install_prefix(const wchar_t *install, const wchar_t *final_dir) {\n" + prefix + "\n}\n"
            (cls.build / "original_install_prefix.h").write_text(code, encoding="utf-8")
            defines = ["/DORIGINAL_INSTALL_PREFIX"] if Path(CC).stem.lower() in ("cl", "clang-cl") else ["-DORIGINAL_INSTALL_PREFIX"]
        if PREVIEW_OUTPUT:
            defines = ["/DSETUP_DIALOG_PREVIEW"] if Path(CC).stem.lower() in ("cl", "clang-cl") else ["-DSETUP_DIALOG_PREVIEW"]
        includes = [str(cls.build), str(ROOT / "builder/setup")]
        source = str(ROOT / "tests/setup_transaction_test.c")
        if Path(CC).stem.lower() in ("cl", "clang-cl"):
            command = [CC, "/nologo", "/std:c11", "/W4", "/WX", *defines, *["/I" + x for x in includes], source,
                       "/Fe:" + str(cls.exe), "/Fo:" + str(cls.build / "fixture.obj"), "comctl32.lib", "shell32.lib", "ole32.lib", "user32.lib"]
            if PREVIEW_OUTPUT:
                command += ["/link", "/SUBSYSTEM:WINDOWS", "/MANIFEST:EMBED",
                            "/MANIFESTINPUT:" + str(ROOT / "builder/setup/setup.manifest")]
        else:
            for name in ("INCLUDE", "LIB", "LIBPATH"):
                env.pop(name, None)
            compiler = Path(shutil.which(CC) or CC).resolve()
            env["PATH"] = str(compiler.parent) + os.pathsep + str(Path(env["SYSTEMROOT"]) / "System32")
            command = [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-municode", *defines,
                       *["-I" + x for x in includes], source, "-o", str(cls.exe), "-lcomctl32", "-lshell32", "-lole32", "-luser32"]
            if PREVIEW_OUTPUT:
                resource = cls.build / "preview.rc"
                resource.write_text('1 24 "' + (ROOT / "builder/setup/setup.manifest").as_posix() + '"\n', encoding="utf-8")
                windres = compiler.parent / "x86_64-w64-mingw32-windres.exe"
                subprocess.run([str(windres), "-i", str(resource), "-o", str(cls.build / "preview.o")],
                               env=env, cwd=cls.build, check=True, timeout=60, creationflags=FLAGS)
                command += [str(cls.build / "preview.o"), "-mwindows"]
        compiled = subprocess.run(command, env=env, cwd=cls.build, timeout=60, creationflags=FLAGS,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if compiled.stdout:
            print(compiled.stdout, end="", flush=True)
        compiled.check_returncode()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="setup-transaction-case-")
        self.addCleanup(cleanup, self.temporary)
        self.parent = owned(self.temporary)
        self.root = self.parent / "HookshotBuilder"
        self.root.mkdir()
        (self.root / ".hookshot-install-root").write_bytes(b"HookshotInstallRoot 1\n")
        old = self.root / "versions/v1"
        (old / "HookshotBuilder").mkdir(parents=True)
        (old / ".hookshot-install-version").write_bytes(b"HookshotInstallVersion 1\n")
        (old / "HookshotBuilder/HookshotBuilder.exe").write_bytes(b"original authored GUI, never executed")
        (old / "HookshotBuilder/script.py").write_bytes(b"unlocked orphan/legacy script must survive failure")
        (self.root / "versions/current.txt").write_bytes(b"v1")
        (self.root / "versions/unrecognized").mkdir()
        (self.root / "versions/unrecognized/keep.txt").write_bytes(b"foreign user bytes")
        (self.root / "downloads").mkdir()
        (self.root / "downloads/old.zip").write_bytes(b"previous cache bytes")

    def snapshot(self):
        return {path.relative_to(self.parent).as_posix(): (path.stat().st_file_attributes, path.read_bytes() if path.is_file() else None)
                for path in self.parent.rglob("*")}

    def check_case(self, mode, success=False):
        before = self.snapshot()
        result = subprocess.run([str(self.exe), str(self.root.parent), mode], timeout=20, creationflags=FLAGS,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        after = self.snapshot()
        if success:
            for name, value in before.items():
                if name != "HookshotBuilder/versions/current.txt":
                    self.assertEqual(after[name], value)
            self.assertEqual((self.root / "versions/current.txt").read_bytes(), b"v1-0123456789abcdef")
            self.assertFalse(any(path.name.startswith(".setup-") for path in self.root.iterdir()))
        else:
            self.assertEqual(after, before, "Failure/Cancel may remove only this attempt's artifacts")

    def test_cancel_and_change_before_any_installer_write(self):
        self.check_case("cancel")
        self.check_case("change")

    def test_actual_consent_loop_install_change_cancel_has_no_effects(self):
        self.check_case("consent-install")
        self.check_case("consent-change")
        self.check_case("consent-cancel")

    def test_display_path_wraps_losslessly_without_mutating_filesystem_path(self): self.check_case("path-text")

    def test_insufficient_default_drive_can_change_without_weakening_install_gate(self):
        (self.parent / "alternate").mkdir()
        self.check_case("consent-low-change")
        self.check_case("consent-low-cancel")
        self.check_case("consent-low-forced")
        self.check_case("consent-low-fixed")

    def test_partial_preparation_failure_preserves_same_tag_and_cache(self): self.check_case("prepare-failure")
    def test_cancel_during_preparation_preserves_same_tag_and_cache(self): self.check_case("prepare-cancel")
    def test_atomic_bookkeeping_failure_rolls_back_only_new_payload(self): self.check_case("publish-failure")
    def test_existing_final_leaf_is_not_overwritten(self): self.check_case("collision")
    def test_concurrent_attempt_is_bounded_busy_before_preparation(self): self.check_case("busy")
    def test_success_publishes_unique_payload_then_current(self): self.check_case("success", success=True)

    def test_first_install_cancel_failure_and_success(self):
        parent = self.parent / "fresh"
        parent.mkdir()
        self.root = parent / "HookshotBuilder"
        self.check_case("cancel")
        self.assertFalse(self.root.exists())
        self.check_case("prepare-failure")
        self.assertFalse(self.root.exists())
        self.check_case("new-root", success=True)

    def test_real_locked_previous_gui_and_unlocked_scripts_survive_failure(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        exe = self.root / "versions/v1/HookshotBuilder/HookshotBuilder.exe"
        # Real Windows sharing denial; this is authored data and never executed.
        handle = kernel.CreateFileW(str(exe), 0x80000000, 1 | 2, None, 3, 0, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.check_case("prepare-failure")
        finally:
            self.assertTrue(kernel.CloseHandle(handle))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cl")
    parser.add_argument("--original-source", type=Path)
    parser.add_argument("--build-preview", type=Path, help="Compile only; never opens the production dialogs")
    args, rest = parser.parse_known_args()
    CC, ORIGINAL, PREVIEW_OUTPUT = args.cc, args.original_source, args.build_preview
    if PREVIEW_OUTPUT:
        if os.name != "nt" or ORIGINAL:
            parser.error("Preview requires Windows and the current production helpers")
        try:
            SetupTransactionTests.setUpClass()
            shutil.copy2(SetupTransactionTests.exe, PREVIEW_OUTPUT)
        finally:
            SetupTransactionTests.doClassCleanups()
        raise SystemExit(0)
    unittest.main(argv=[__file__, *rest])
