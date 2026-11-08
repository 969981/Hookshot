"""Windows-only checks of the actual setup cleanup helpers, using authored temporary files."""
import argparse
import contextlib
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "builder" / "setup" / "setup.c"
TOKEN = uuid.uuid4().hex
CC = "cl"
HASH = "1" * 64
PAYLOADS = ("A", "B", "C", "D", "orphan", "legacy", "unfinished.part")

FIXTURE = r'''#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
@PART@
@HELPERS@
int wmain(int argc, wchar_t **argv) {
    wchar_t root[MAX_PATH], cwd[MAX_PATH], path[MAX_PATH], versions[MAX_PATH], downloads[MAX_PATH];
    char owner[128], tag[128];
    if ((argc != 3 && argc != 5) || !GetFullPathNameW(argv[1], MAX_PATH, root, NULL) ||
        !GetCurrentDirectoryW(MAX_PATH, cwd) || wcscmp(root, cwd)) return 2;
    const wchar_t *name = wcsrchr(root, L'\\');
    if (!name || wcsncmp(name + 1, L"hookshot-setup-prune-", 21)) return 3;
    swprintf(path, MAX_PATH, L"%ls\\.owned-by-fixture", root);
    read_small(path, owner, sizeof owner);
    if (strcmp(owner, "@TOKEN@")) return 4;
    if (!WideCharToMultiByte(CP_UTF8, 0, argv[2], -1, tag, sizeof tag, NULL, NULL)) return 5;
    if (argc == 5) {
        HANDLE ready = OpenEventW(EVENT_MODIFY_STATE, FALSE, argv[3]);
        HANDLE go = OpenEventW(SYNCHRONIZE, FALSE, argv[4]);
        if (!ready || !go) return 6;
        BOOL signaled = SetEvent(ready);
        DWORD waited = WaitForSingleObject(go, 10000);
        CloseHandle(ready);
        CloseHandle(go);
        if (!signaled || waited != WAIT_OBJECT_0) return 7;
    }
    swprintf(versions, MAX_PATH, L"%ls\\versions", root);
    swprintf(downloads, MAX_PATH, L"%ls\\downloads", root);
    Part part = {0};
    memset(part.sha, '1', 64);
    prune(versions, downloads, tag, &part, 1);
    /* Exercise the unchanged removal helper only on this authored, fixed cleanup target. */
    swprintf(path, MAX_PATH, L"%ls\\fixture-trash", root);
    rmtree(path);
    return 0;
}
'''


def owned(root):
    root = Path(root)
    resolved = root.resolve()
    if root.is_symlink() or resolved.parent != Path(tempfile.gettempdir()).resolve() or not resolved.name.startswith("hookshot-setup-prune-"):
        raise AssertionError("Fixture root is not its authored temporary directory")
    return resolved


def cleanup(temporary):
    root = owned(temporary.name)  # Verify the absolute target before recursive cleanup.
    for path in root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise AssertionError("Unexpected fixture link")
        if path.is_file():
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
    temporary.cleanup()


def compiler_command(cc, source, exe):
    if Path(cc).stem.lower() in ("cl", "clang-cl"):
        return [cc, "/nologo", "/std:c11", "/W4", "/WX", str(source), "/Fe:" + str(exe), "/Fo:" + str(exe.with_suffix(".obj"))]
    return [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-municode", str(source), "-o", str(exe)]


def compiler_environment(cc):
    env = dict(os.environ)
    if Path(cc).stem.lower() not in ("cl", "clang-cl"):
        for name in ("INCLUDE", "LIB", "LIBPATH"):
            env.pop(name, None)
        compiler = Path(shutil.which(cc) or cc).resolve()
        env["PATH"] = str(compiler.parent) + os.pathsep + str(Path(env["SYSTEMROOT"]) / "System32")
    return env


@unittest.skipUnless(os.name == "nt", "Windows file attributes, sharing locks and setup APIs are required")
class SetupPruningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="hookshot-setup-prune-build-")
        cls.addClassCleanup(cleanup, cls.build)
        folder = owned(cls.build.name)
        setup = SOURCE.read_text(encoding="utf-8")
        part = setup[setup.index("typedef struct {"):setup.index("} Part;") + len("} Part;")]
        helpers = setup[setup.index("static void rmtree("):setup.index("static void w(")]
        source = folder / "fixture.c"
        source.write_text(FIXTURE.replace("@PART@", part).replace("@HELPERS@", helpers).replace("@TOKEN@", TOKEN), encoding="utf-8")
        cls.exe = folder / "fixture.exe"
        print("production helper SHA256:", hashlib.sha256(helpers.encode()).hexdigest(), flush=True)
        subprocess.run(compiler_command(CC, source, cls.exe), env=compiler_environment(CC), cwd=folder,
                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS, timeout=60, check=True)
        cls.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        cls.kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        cls.kernel.GetFileAttributesW.restype = ctypes.c_uint32
        cls.kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        cls.kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                         ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        cls.kernel.CreateFileW.restype = ctypes.c_void_p
        cls.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        cls.kernel.DeleteFileW.argtypes = [ctypes.c_wchar_p]
        cls.kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        cls.kernel.CreateEventW.restype = ctypes.c_void_p
        cls.kernel.SetEvent.argtypes = [ctypes.c_void_p]
        cls.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cls.kernel.WaitForSingleObject.restype = ctypes.c_uint32

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hookshot-setup-prune-case-")
        self.addCleanup(cleanup, self.temporary)
        self.root = owned(self.temporary.name)
        (self.root / ".owned-by-fixture").write_text(TOKEN, encoding="ascii")
        self.versions, self.downloads = self.root / "versions", self.root / "downloads"
        self.versions.mkdir()
        self.downloads.mkdir()
        (self.versions / "current.txt").write_text("A", encoding="ascii")
        for version in PAYLOADS:
            for name in ("HookshotBuilder.exe", "tools/nx2/next_step.py", "tools/include/guest.h"):
                path = self.versions / version / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(("authored fixture: " + version + "/" + name).encode())
        readonly = self.versions / "A" / "tools/include/guest.h"
        self.assertTrue(self.kernel.SetFileAttributesW(str(readonly), self.attributes(readonly) | 3))
        (self.downloads / (HASH + ".zip")).write_bytes(b"current referenced fixture archive")
        (self.downloads / ("2" * 64 + ".zip")).write_bytes(b"obsolete fixture archive")
        (self.downloads / "unfinished.part").write_bytes(b"not a zip")

    def attributes(self, path):
        result = self.kernel.GetFileAttributesW(str(path))
        self.assertNotEqual(result, 0xFFFFFFFF, str(path))
        return result

    def snapshot(self):
        result = {}
        for version in PAYLOADS:
            folder = self.versions / version
            if folder.exists():
                for path in (folder, *sorted(folder.rglob("*"))):
                    result[str(path.relative_to(self.versions))] = (self.attributes(path), path.read_bytes() if path.is_file() else None)
        return result

    def prepare(self):
        owned(self.root)
        trash = self.root / "fixture-trash"
        trash.mkdir(exist_ok=True)
        (trash / "authored.txt").write_bytes(b"safe fixture cleanup")

    def start(self, tag, *barrier):
        return subprocess.Popen([str(self.exe), str(self.root), tag, *barrier], cwd=self.root,
                                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)

    def prune(self, tag):
        self.prepare()
        with self.start(tag) as process:
            try:
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()

    def lock_builder(self):
        path = self.versions / "A" / "HookshotBuilder.exe"
        # FILE_SHARE_READ | FILE_SHARE_WRITE, deliberately no FILE_SHARE_DELETE.
        handle = self.kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
        self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value))
        self.addCleanup(self.kernel.CloseHandle, handle)
        self.assertFalse(self.kernel.DeleteFileW(str(path)))
        self.assertEqual(ctypes.get_last_error(), 32)  # ERROR_SHARING_VIOLATION

    def test_locked_gui_keeps_unlocked_headers_and_scripts_across_two_releases(self):
        self.lock_builder()
        before = self.snapshot()
        self.prune("B")
        self.prune("C")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.versions / "current.txt").read_text(), "C")
        self.assertEqual((self.versions / "previous.txt").read_text(), "B")

    def test_unlocked_orphan_legacy_and_partial_payloads_keep_bytes_and_attributes(self):
        before = self.snapshot()
        self.prune("B")
        self.prune("C")
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_pruners_preserve_every_payload(self):
        self.lock_builder()
        before = self.snapshot()
        self.prepare()
        names = ["Local\\HookshotSetupPrune-" + TOKEN + suffix for suffix in ("-ready-C", "-ready-D", "-go")]
        handles = []
        for name in names:
            handle = self.kernel.CreateEventW(None, True, False, name)
            self.assertTrue(handle)
            self.addCleanup(self.kernel.CloseHandle, handle)
            handles.append(handle)
        with contextlib.ExitStack() as stack:
            processes = []
            try:
                for tag, ready in zip(("C", "D"), names):
                    processes.append(stack.enter_context(self.start(tag, ready, names[2])))
                for ready in handles[:2]:
                    self.assertEqual(self.kernel.WaitForSingleObject(ready, 10000), 0)
                self.assertTrue(all(process.poll() is None for process in processes))
                self.assertTrue(self.kernel.SetEvent(handles[2]))
                for process in processes:
                    self.assertEqual(process.wait(timeout=10), 0)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
        self.assertEqual(self.snapshot(), before)

    def test_archive_cleanup_is_unchanged(self):
        self.prune("B")
        self.assertEqual((self.downloads / (HASH + ".zip")).read_bytes(), b"current referenced fixture archive")
        self.assertFalse((self.downloads / ("2" * 64 + ".zip")).exists())
        self.assertEqual((self.downloads / "unfinished.part").read_bytes(), b"not a zip")
        self.assertFalse((self.root / "fixture-trash").exists())

    def test_current_previous_bookkeeping_does_not_rotate_for_same_tag(self):
        self.prune("B")
        self.prune("B")
        self.assertEqual((self.versions / "current.txt").read_text(), "B")
        self.assertEqual((self.versions / "previous.txt").read_text(), "A")
        self.prune("C")
        self.assertEqual((self.versions / "current.txt").read_text(), "C")
        self.assertEqual((self.versions / "previous.txt").read_text(), "B")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cl")
    parser.add_argument("--source", type=Path, default=SOURCE, help="setup source to exercise (also used for the original negative control)")
    args, rest = parser.parse_known_args()
    CC = args.cc
    SOURCE = args.source
    unittest.main(argv=[__file__, *rest])
