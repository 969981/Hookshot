"""Verify the macOS branch of the production build configuration without a game dump."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MacOSBuildPathTests(unittest.TestCase):
    def test_selects_x86_64_posix_and_metal_build_target(self):
        source = ROOT / "nx2" / "build.py"
        script = (
            "import json, runpy, sys; "
            "sys.platform = 'darwin'; "
            "b = runpy.run_path(sys.argv[1]); "
            "print(json.dumps({k: b[k] for k in ('WINDOWS', 'LINUX', 'MACOS', 'POSIX_HOST', 'EXE', 'MARCH')} "
            "| {'target': b['target']()}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", script, str(source)],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(config, {
            "WINDOWS": False,
            "LINUX": False,
            "MACOS": True,
            "POSIX_HOST": True,
            "EXE": "hookshot",
            "MARCH": "x86-64",
            "target": ["--target=x86_64-apple-macos10.15"],
        })

    def test_game_builder_selects_macos_bundle_files(self):
        source = ROOT / "nx2" / "make_game.py"
        script = (
            "import json, runpy, sys; "
            "sys.platform = 'darwin'; "
            "b = runpy.run_path(sys.argv[1]); "
            "print(json.dumps({k: b[k] for k in ('LINUX', 'MACOS', 'BESIDE')}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", script, str(source)],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(config, {
            "LINUX": False,
            "MACOS": True,
            "BESIDE": ["hookshot", "libSDL2-2.0.0.dylib", "libwgpu_native.dylib"],
        })

    def test_macos_runtime_supplies_non_linux_profiler_entry_point(self):
        source = (ROOT / "host" / "posix" / "macos_stubs.c").read_text(encoding="utf-8")
        self.assertIn("void hos_prof_start(void)", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
