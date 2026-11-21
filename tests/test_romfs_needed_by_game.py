"""What a romfs must hold before a build is the game's own (nx2/game_profile.py romfs_needed), not Ocarina's for every game.

Longshot #534 and #863 refuse a romfs without Pack/ZsDic.pack.zs, System/Resource or the heap definition: Ocarina's files.
Mario Kart World and Donkey Kong Bananza have no ZsDic.pack.zs (make_game.py's button prompts say so), so the list is
Ocarina's profile's, read through main's build id in the exefs beside the romfs. A game whose profile names none, or a build
no profile knows, only has to hold something. No game files: authored folders and an NSO header with each profile's id.

python -I -B tests/test_romfs_needed_by_game.py
"""
import importlib.util
import io
import contextlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
spec = importlib.util.spec_from_file_location("make_game_romfs_needed", ROOT / "nx2" / "make_game.py")
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)
game_profile = make_game.game_profile


def exefs_of(folder, build_id):
    """An exefs whose main is an NSO header with this build id (the bytes make_game reads, nothing of a game's)"""
    folder.mkdir(parents=True)
    header = bytearray(0x60)
    header[:4] = b"NSO0"
    header[0x40:0x40 + len(bytes.fromhex(build_id))] = bytes.fromhex(build_id)
    (folder / "main").write_bytes(bytes(header))
    return folder


def refused(romfs, exefs):
    """The check's message when it refuses romfs, else None"""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            make_game.check_romfs_folder(str(romfs), str(exefs) if exefs else None)
    except SystemExit:
        return out.getvalue()
    return None


class RomfsNeededByGame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hookshot-romfs-needed-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def mkw_shaped(self):
        """A romfs as Mario Kart World's is laid out at its top: folders of its own, no Pack/ZsDic.pack.zs, no System/Resource"""
        romfs = self.root / "mkw" / "romfs"
        for d in ("Model", "Sound", "UI"):
            (romfs / d).mkdir(parents=True)
        (romfs / "Model" / "authored.bin").write_bytes(b"authored")
        return romfs

    def test_only_ocarina_names_files(self):
        self.assertTrue(game_profile.OOT.romfs_needed, "Ocarina's profile names what its romfs needs")
        for key in ("mkw", "dkb", "none"):
            self.assertEqual(game_profile.GAMES[key].romfs_needed, (), "%s's romfs is its own: no list of Ocarina's" % key)

    def test_mario_kart_world_without_zsdic_passes(self):
        exefs = exefs_of(self.root / "mkw" / "exefs", game_profile.MKW.build_id)
        self.assertIsNone(refused(self.mkw_shaped(), exefs))

    def test_bananza_without_zsdic_passes(self):
        exefs = exefs_of(self.root / "dkb" / "exefs", game_profile.DKB.build_id)
        self.assertIsNone(refused(self.mkw_shaped(), exefs))

    def test_ocarina_without_zsdic_is_refused(self):
        exefs = exefs_of(self.root / "oot" / "exefs", game_profile.OOT.build_id)
        said = refused(self.mkw_shaped(), exefs)
        self.assertIsNotNone(said)
        self.assertIn("does not hold the game's files (no Pack/ZsDic.pack.zs)", said)

    def test_ocarina_with_its_files_passes(self):
        exefs = exefs_of(self.root / "oot" / "exefs", game_profile.OOT.build_id)
        romfs = self.root / "oot" / "romfs"
        for names, kind in game_profile.OOT.romfs_needed:
            path = romfs / names[0]
            if kind == "dir":
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"authored")
        self.assertIsNone(refused(romfs, exefs))

    def test_an_empty_romfs_is_refused_for_any_game(self):
        romfs = self.root / "empty" / "romfs"
        romfs.mkdir(parents=True)
        for key, exefs in (("mkw", exefs_of(self.root / "e1", game_profile.MKW.build_id)), ("none", None)):
            said = refused(romfs, exefs)
            self.assertIsNotNone(said, key)
            self.assertIn("(no files)", said)


if __name__ == "__main__":
    unittest.main()
