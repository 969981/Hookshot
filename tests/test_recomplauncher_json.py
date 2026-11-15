"""Game-free checks of recomplauncher.json: it parses, and every settings key is a row of the in-game menu.

Run with: python -I tests/test_recomplauncher_json.py
"""
import json
from pathlib import Path
import re
import unittest

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
        # (keys the game reads that are no menu row: Compile shaders, which the preparation reads, vk_draw.cpp compile_all)
        draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
        read_elsewhere = {"compile_shaders"} if '"compile_shaders="' in draw else set()
        self.assertEqual(keys - rows - read_elsewhere, set())

    def test_the_launcher_schema_holds(self):
        """The parts of RecompLauncher's schema (recomplauncher.com/schema/recomplauncher.json, as of 0.90 Beta 49) this
        entry uses, checked here so the suite needs no network: min, inputs[], games[] and the {input:...} placeholders"""
        m = json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8"))
        top = {"$schema", "actions", "config", "exit_codes", "folders", "format", "game", "games", "groups", "inputs",
               "looks", "min", "mods", "name", "prepare", "repo", "requires", "saves", "settings", "start"}
        self.assertLessEqual(set(m), top)
        self.assertRegex(m["min"], r"^v?\d+\.\d+\.\d+(-[A-Za-z]+\.\d+)?$")
        inputs = m.get("inputs", [])
        self.assertLessEqual(len(inputs), 32)
        ids = [i["id"] for i in inputs]
        self.assertEqual(len(ids), len(set(ids)))
        for i in inputs:
            self.assertRegex(i["id"], r"^[A-Za-z0-9_-]{1,64}$")
            self.assertTrue(i["label"].strip())
            self.assertIn(i.get("role", "game"), {"game", "disc", "dlc", "update", "key", "runtime"})
            for f in i.get("formats", []):
                self.assertIn(f["type"], {"file", "folder"})
                if f["type"] == "folder":
                    self.assertNotIn("extensions", f)
                for e in f.get("extensions", []):
                    self.assertFalse(e.startswith("."))
        if "game" in m:   # (the legacy copy reserves the input id "game")
            self.assertNotIn("game", ids)
        games = m.get("games", [])
        if games:
            self.assertTrue(2 <= len(games) <= 32)
            self.assertEqual(len({g["id"] for g in games}), len(games))
            for g in games:
                self.assertLessEqual(set(g), {"id", "name", "args", "title_id", "inputs", "saves"})
                self.assertRegex(g["id"], r"^[a-z0-9]{1,16}$")
                self.assertTrue(g["name"].strip())
                self.assertLessEqual(set(g.get("inputs", [])), set(ids), "a game names an input there is none of")
        # (an unknown placeholder id is a configuration error; a copy's flag and token in one argument, so an absent
        # input drops the whole argument)
        text = json.dumps(m)
        self.assertLessEqual(set(re.findall(r"\{input:([A-Za-z0-9_-]+)\}", text)), set(ids))
        for a in m["prepare"]["args"]:
            if "{input:" in a:
                self.assertRegex(a, r"^--[a-z0-9-]+=\{input:[A-Za-z0-9_-]+\}$")
        self.assertNotIn("{game}", text, "the legacy copy's placeholder with no legacy copy")

    def test_appimage_carries_it(self):
        self.assertIn('cp recomplauncher.json "$APPDIR/recomplauncher.json"',
                      (ROOT / "scripts" / "linux" / "appimage.sh").read_text(encoding="utf-8"))

    def test_windows_zip_carries_it(self):
        workflow = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
        self.assertIn('z.write("recomplauncher.json", "recomplauncher.json")', workflow)
        self.assertIn("dist/Hookshot-*-Windows-x64.zip", workflow)


if __name__ == "__main__":
    unittest.main()
