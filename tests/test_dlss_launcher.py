"""NVIDIA DLSS for games RecompLauncher builds on Windows: recomplauncher.json's prepare.options carry a DLSS tick
(nx2/game_folders.py dlss_option) that adds `--dlss --accept-nvidia-license` to every game's setup, which
HookshotBuilder.exe --cli turns into the build that fetches NVIDIA's runtimes into each game folder's ngx\\ and keeps the
release's renderer. Off until ticked (NVIDIA's license, the one scripts/ngx_sdk.py downloads under), shown on Windows with
an RTX card only (the AppImage's renderer has no DLSS). Unticked passes nothing, and never --no-dlss.

    python -I -B tests/test_dlss_launcher.py
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import game_folders  # noqa: E402


class Tick(unittest.TestCase):
    def test_the_entry_carries_the_tick_as_game_folders_writes_it(self):
        entry = json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8"))
        self.assertEqual([o for o in entry["prepare"].get("options", []) if o["id"] == "dlss"], [game_folders.dlss_option()])
        self.assertNotIn("--no-dlss", json.dumps(entry))   # (only for an explicit "remove DLSS", not an unticked box)

    def test_the_tick_is_nvidias_license_on_windows_with_an_rtx_card(self):
        o = game_folders.dlss_option()
        spec = importlib.util.spec_from_file_location("ngx_sdk", ROOT / "scripts" / "ngx_sdk.py")
        ngx = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ngx)
        self.assertEqual(o["args"], ["--dlss", "--accept-nvidia-license"])
        self.assertEqual((o["license"]["url"], o["gpu"], o["os"]), (ngx.LICENSE_URL, "nvidia", ["windows"]))
        self.assertTrue(o["license"]["url"].startswith("https://"))
        self.assertNotIn("default", o)   # (a license: the tick starts off)
        self.assertLessEqual(set(o), {"id", "label", "note", "args", "os", "gpu", "license", "default"})   # (the schema's)

    def test_writing_the_entry_twice_keeps_one_tick(self):
        entry = json.loads((ROOT / "recomplauncher.json").read_text(encoding="utf-8"))
        again = game_folders.launcher_entry(game_folders.launcher_entry(entry))
        self.assertEqual(again, entry)


if __name__ == "__main__":
    unittest.main()
