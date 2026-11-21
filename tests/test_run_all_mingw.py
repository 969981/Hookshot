"""tests/run_all.py gives its checks the clone's llvm-mingw folder as HOOKSHOT_MINGW, as nx2/make_game.py's
bundled_mingw() gives it to the build: CONTRIBUTING.md's second way to get the compiler on Windows. Without it a check
that builds with toolchain.native_target() (test_spawn_emit, test_fpx_sticky, test_fastfp_fma) asks that folder's clang,
found on PATH, for the MSVC target, which llvm-mingw has no C++ library for: "fatal error: 'algorithm' file not found".

A copy of run_all.py runs in a scratch repository with a stand-in llvm-mingw/bin/clang and one check that writes down
the HOOKSHOT_MINGW it was started with. On Windows the folder is used when the variable is unset; a variable already
set wins; HOOKSHOT_MSVC=1 (the Visual Studio route) and a folder without its compiler leave it unset. Linux builds
with its own clang (make_game does not look for the folder there), so it stays unset there.

python -I -B tests/test_run_all_mingw.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
CLANG = "clang.exe" if WINDOWS else "clang"
PROBE = ('import os\nopen(os.environ["MINGW_PROBE_OUT"], "w", encoding="utf-8")'
         '.write(os.environ.get("HOOKSHOT_MINGW", "<unset>"))\n')


class CloneLlvmMingw(unittest.TestCase):
    def seen(self, env_extra, compiler=True):
        """The HOOKSHOT_MINGW the probe check saw, and the folder the scratch clone's llvm-mingw is at"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "tests").mkdir()
            (tmp / "nx2").mkdir()
            (tmp / "llvm-mingw" / "bin").mkdir(parents=True)
            if compiler:
                (tmp / "llvm-mingw" / "bin" / CLANG).write_bytes(b"")
            shutil.copy(ROOT / "tests" / "run_all.py", tmp / "tests" / "run_all.py")
            (tmp / "tests" / "test_probe.py").write_text(PROBE, encoding="utf-8")
            out = tmp / "seen.txt"
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("PYTHON", "GITHUB_")) and k not in ("HOOKSHOT_MINGW", "HOOKSHOT_MSVC")}
            env.update(MINGW_PROBE_OUT=str(out), **env_extra)
            r = subprocess.run([sys.executable, "-I", "-B", str(tmp / "tests" / "run_all.py"), "--jobs", "1",
                                "--exact", "tests/test_probe.py"],
                               cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace"))
            return out.read_text(encoding="utf-8"), str(tmp / "llvm-mingw")

    def test_the_clone_folder_when_the_variable_is_unset(self):
        seen, folder = self.seen({})
        self.assertEqual(seen, folder if WINDOWS else "<unset>")

    def test_a_variable_already_set_wins(self):
        mine = os.path.join(tempfile.gettempdir(), "my-llvm-mingw")
        seen, _ = self.seen({"HOOKSHOT_MINGW": mine})
        self.assertEqual(seen, mine)

    def test_the_visual_studio_route_leaves_it_unset(self):
        seen, _ = self.seen({"HOOKSHOT_MSVC": "1"})
        self.assertEqual(seen, "<unset>")

    def test_a_folder_without_its_compiler_leaves_it_unset(self):
        seen, _ = self.seen({}, compiler=False)
        self.assertEqual(seen, "<unset>")


if __name__ == "__main__":
    unittest.main()
