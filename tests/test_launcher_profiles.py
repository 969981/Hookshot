"""The Linux launcher's profiles (builder/linux/profiles.py): each one has saves of its own, and a save from an emulator is
imported into one without losing the saves it had.

python -I -B tests/test_launcher_profiles.py
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("launcher_profiles", ROOT / "builder" / "linux" / "profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def save(folder, slots=2, marker="a"):
    """a save folder of the game as the console and emulators keep it"""
    folder.mkdir(parents=True)
    (folder / "account.sav").write_text(marker)
    (folder / "option.sav").write_text(marker)
    for n in range(slots):
        slot = folder / ("slot_%02d" % n)
        slot.mkdir()
        (slot / "progress.sav").write_text(marker)
        (slot / "caption.sav").write_text(marker)
    return folder


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hookshot-profiles-")
        self.root = Path(self.tmp.name)
        self.user = str(self.root / "user")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_default_profile_is_the_user_every_build_had(self):
        self.assertEqual(profiles.load(self.user), [(profiles.DEFAULT, "Player")])
        self.assertEqual(profiles.DEFAULT, "5a3c9e1f0b7d24680000000000000001")   # (host/nn_applet.c's user_uid)

    def test_new_profiles_get_save_folders_of_their_own(self):
        a = profiles.add(self.user, "  Link  ")
        b = profiles.add(self.user, "Zelda")
        self.assertEqual(profiles.load(self.user), [(profiles.DEFAULT, "Player"), (a, "Link"), (b, "Zelda")])
        self.assertEqual(len({profiles.DEFAULT, a, b}), 3)
        for uid in (a, b):
            self.assertRegex(uid, r"^[0-9a-f]{32}$")
        with self.assertRaises(ValueError):
            profiles.add(self.user, "link")
        with self.assertRaises(ValueError):
            profiles.add(self.user, "   ")
        profiles.rename(self.user, profiles.DEFAULT, "Tim")
        self.assertEqual(profiles.load(self.user)[0], (profiles.DEFAULT, "Tim"))
        self.assertEqual(profiles.shown_name(profiles.DEFAULT, "Tim"), "Tim (default)")   # (why it cannot be removed)
        self.assertEqual(profiles.shown_name(a, "Link"), "Link")

    def test_a_removed_profile_keeps_its_saves_and_its_folder_is_not_reused(self):
        uid = profiles.add(self.user, "Link")
        save(Path(self.user) / "save" / uid)
        moved = profiles.remove(self.user, uid)
        self.assertTrue(os.path.isfile(os.path.join(moved, "slot_00", "progress.sav")))
        self.assertFalse(os.path.exists(os.path.join(self.user, "save", uid)))
        self.assertEqual(profiles.load(self.user), [(profiles.DEFAULT, "Player")])
        with self.assertRaises(ValueError):
            profiles.remove(self.user, profiles.DEFAULT)
        save(Path(self.user) / "save" / uid)   # (a folder left over: a new profile does not take it)
        self.assertNotEqual(profiles.add(self.user, "Again"), uid)

    def test_an_emulators_save_folder_lists_one_save_for_each_of_its_users(self):
        emu = self.root / "2026-11-09 save"
        users = emu / "0000000000000000"
        first = save(users / "45EB517681BCEAF4B834E0FA511E3212" / "0400486024DEC000", slots=6)
        second = save(users / "533C42419DD767B01211726DADA4670A" / "0400486024DEC000", slots=6)
        save(users / "45EB517681BCEAF4B834E0FA511E3212" / "0400C3F00006E000" / "Play")   # (another game's)
        os.utime(first / "slot_00" / "progress.sav", (2e9, 2e9))
        self.assertEqual(profiles.find_saves(str(emu)), [str(first), str(second)])
        self.assertEqual(profiles.find_saves(str(second)), [str(second)])
        self.assertEqual(profiles.find_saves(str(self.root / "nothing")), [])
        self.assertTrue(profiles.describe(str(first), str(emu)).startswith(
            "0000000000000000/45EB517681BCEAF4B834E0FA511E3212/0400486024DEC000  (6 save slots, last saved "))

    def test_an_import_replaces_the_profiles_saves_and_keeps_the_old_ones(self):
        uid = profiles.add(self.user, "Link")
        own = save(Path(self.user) / "save" / uid, slots=1, marker="old")
        source = save(self.root / "emu" / "0400486024DEC000", slots=3, marker="new")
        moved = profiles.import_save(str(source), self.user, uid)
        self.assertEqual((own / "slot_02" / "progress.sav").read_text(), "new")
        self.assertEqual((Path(moved) / "slot_00" / "progress.sav").read_text(), "old")
        self.assertTrue((source / "account.sav").exists())   # (the source is copied, not moved)
        self.assertFalse(os.path.exists(str(own) + ".importing"))
        other = profiles.add(self.user, "Zelda")
        self.assertIsNone(profiles.import_save(str(source), self.user, other))   # (no saves before: nothing to keep)
        with self.assertRaises(ValueError):
            profiles.import_save(str(own), self.user, uid)
        with self.assertRaises(ValueError):
            profiles.import_save(str(self.root), self.user, uid)

    def test_open_save_folder_shows_the_profiles_saves_and_makes_no_folder(self):
        uid = profiles.add(self.user, "Link")
        self.assertEqual(profiles.folder_to_show(self.user, uid), self.user)   # (no save folder yet: none is made)
        self.assertFalse(os.path.exists(os.path.join(self.user, "save", uid)))
        save(Path(self.user) / "save" / profiles.DEFAULT)
        self.assertEqual(profiles.folder_to_show(self.user, uid), os.path.join(self.user, "save"))
        save(Path(self.user) / "save" / uid)
        self.assertEqual(profiles.folder_to_show(self.user, uid), os.path.join(self.user, "save", uid))
        self.assertIsNone(profiles.folder_to_show(str(self.root / "none"), uid))

    def test_the_user_folder_is_the_one_the_game_uses(self):
        game = self.root / "game"
        game.mkdir()
        self.assertEqual(profiles.user_dir(str(game), {"XDG_DATA_HOME": "/data", "HOME": "/home/p"}), "/data/hookshot")
        self.assertEqual(profiles.user_dir(str(game), {"XDG_DATA_HOME": "rel", "HOME": "/home/p"}),
                         "/home/p/.local/share/hookshot")
        (game / "user" / "save").mkdir(parents=True)   # (saves the game could not move: hos_main.c keeps using them)
        self.assertEqual(profiles.user_dir(str(game), {"HOME": "/home/p"}), str(game / "user"))


if __name__ == "__main__":
    unittest.main()
