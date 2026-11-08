"""Game-free check that make_game stages SDL3.dll beside SDL2.dll on Windows (issue 713).

SDL2.dll is sdl2-compat and loads SDL3.dll from its own folder; without it the game shows
"Failed loading SDL3 library." on launch and the controllers fall back to XInput.

Run with: python -I tests/test_make_game_sdl3.py
"""
import importlib.util
import os
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "nx2" / "make_game.py"
spec = importlib.util.spec_from_file_location("make_game_under_test", SOURCE)
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


class Sdl3StagingTests(unittest.TestCase):
    @unittest.skipIf(make_game.LINUX, "the Windows staging list only")
    def test_windows_game_folder_gets_sdl3(self):
        self.assertIn("SDL3.dll", make_game.BESIDE,
                      "SDL3.dll not staged beside SDL2.dll in %r" % (make_game.BESIDE,))


if __name__ == "__main__":
    unittest.main()
