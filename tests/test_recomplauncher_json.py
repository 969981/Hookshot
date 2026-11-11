"""Game-free checks of recomplauncher.json: it parses, and every settings key is a row of the in-game menu.

Run with: python -I tests/test_recomplauncher_json.py
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class RecompLauncherJsonTests(unittest.TestCase):
    def test_settings_are_menu_rows(self):
        manifest = json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], 1)
        menu = (ROOT / "gpu" / "vk" / "vk_menu.cpp").read_text(encoding="utf-8")
        rows = {re.sub(" ", "_", name.lower()) for name in re.findall(r'\{TAB_[A-Z]+, "([^"]+)"', menu)}
        # (vk_menu.cpp ini_key: the rows whose key is not their name, renamed rows keeping the key an options file has:
        # output_res, resolution, upscaling, frame_rate_cap, dlss)
        ini_key = menu.split("const char* ini_key(const Row& r) {", 1)[1].split("\n}", 1)[0]
        rows |= set(re.findall(r'return "([a-z_0-9]+)";', ini_key))
        keys = {key for setting in manifest["settings"] for choice in setting["choices"] for key in choice["set"]}
        self.assertTrue(keys)
        self.assertEqual(keys - rows, set())

    def test_appimage_carries_it(self):
        self.assertIn('cp recomplauncher.json "$APPDIR/recomplauncher.json"',
                      (ROOT / "scripts" / "linux" / "appimage.sh").read_text(encoding="utf-8"))

    def test_windows_builder_parts_carry_it(self):
        # The Windows installer now downloads content-addressed parts instead of
        # distributing the old monolithic Windows ZIP. Check the real part
        # builder rather than asserting removed workflow text.
        source = (ROOT / "scripts" / "release_parts.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
        self.assertIn('copy(os.path.join(ROOT, "recomplauncher.json"), os.path.join(out, "recomplauncher.json"))', source)
        self.assertIn("scripts/release_parts.py package", workflow)
        self.assertIn("scripts/make_setup_parts.py", workflow)

        with tempfile.TemporaryDirectory(prefix="hookshot-launcher-parts-") as temp:
            package = Path(temp) / "package"
            output = Path(temp) / "dist"
            package.mkdir()
            (package / "tools").mkdir()
            (package / "HookshotBuilder.exe").write_bytes(b"test-fixture")
            shutil.copyfile(ROOT / "recomplauncher.json", package / "recomplauncher.json")
            subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts/make_setup_parts.py"),
                 str(package), "test", str(output)],
                check=True, capture_output=True, text=True,
            )
            archives = list(output.glob("HookshotBuilder-dotnet-*.zip"))
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as archive:
                manifest = json.loads(archive.read("HookshotBuilder/recomplauncher.json"))
            self.assertEqual(manifest, json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
