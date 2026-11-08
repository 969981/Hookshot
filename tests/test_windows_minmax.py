"""The renderer's windows.h leaves std::min and std::max alone (gpu/vk/vk_paths.h).

vk_paths.h is the first include of vk_main.cpp, vk_menu.cpp, vk_draw.cpp and vk_shader.cpp. Including <windows.h> there
without NOMINMAX and WIN32_LEAN_AND_MEAN gave them the Windows SDK's min(a,b)/max(a,b) macros (minwindef.h) and
rpcndr.h's "small": the Windows build stopped at "expected unqualified-id" on every std::min/std::max in those files.
Linux's builds use host/posix's windows.h, which has neither, so nothing else catches it.

Game-free, two checks:
- vk_paths.h compiles, after <algorithm> as build.py forces it, against a stand-in windows.h that defines min, max and
  small as the SDK does unless NOMINMAX / WIN32_LEAN_AND_MEAN, with code using std::min, std::max and a "small";
- every renderer file (gpu/, not its third-party trees) that includes <windows.h> defines NOMINMAX before it.
python -I -B tests/test_windows_minmax.py [--cxx clang++]
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CXX = os.environ.get("CXX") or shutil.which("clang++") or shutil.which("g++")
if "--cxx" in sys.argv:
    i = sys.argv.index("--cxx")
    CXX = sys.argv[i + 1]
    del sys.argv[i:i + 2]

STAND_IN = r"""
#pragma once
#ifndef NOMINMAX
#define max(a,b) (((a) > (b)) ? (a) : (b))
#define min(a,b) (((a) < (b)) ? (a) : (b))
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define small char
#endif
typedef unsigned long DWORD;
#define MAX_PATH 260
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
DWORD GetModuleFileNameA(void *, char *, DWORD);
DWORD GetFileAttributesA(const char *);
int _strnicmp(const char *, const char *, unsigned long);
"""

USE = r"""
#include "vk_paths.h"
unsigned use(unsigned a, unsigned b) {
    float small = 1.0f;
    return std::min(a, 16384u) + std::max(b, 1u) + (unsigned)small;
}
"""

SKIP = ("gpu/third_party/", "gpu/vk/eden/", "gpu/vk/sirit/")


class WindowsMinMax(unittest.TestCase):
    @unittest.skipUnless(CXX, "no C++ compiler")
    def test_vk_paths_compiles_with_sdk_macros(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "windows.h").write_text(STAND_IN)
            Path(d, "use.cpp").write_text(USE)
            r = subprocess.run([CXX, "-std=c++20", "-fsyntax-only", "-include", "algorithm", "-I", d,
                                "-I", str(ROOT / "gpu" / "vk"), str(Path(d, "use.cpp"))],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_every_renderer_windows_h_has_nominmax(self):
        missing, seen = [], 0
        for p in sorted((ROOT / "gpu").rglob("*")):
            rel = p.relative_to(ROOT).as_posix()
            if p.suffix not in (".h", ".hpp", ".cpp") or rel.startswith(SKIP):
                continue
            text = p.read_text(errors="replace")
            m = re.search(r"^\s*#\s*include\s*<windows\.h>", text, re.M)
            if not m:
                continue
            seen += 1
            if not re.search(r"^\s*#\s*define\s+NOMINMAX\b", text[:m.start()], re.M):
                missing.append(rel)
        self.assertGreater(seen, 0)
        self.assertEqual(missing, [], "windows.h without NOMINMAX before it")


if __name__ == "__main__":
    unittest.main()
