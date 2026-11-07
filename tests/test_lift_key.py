"""make_game's translation key (the rest of issue 21): what makes a rebuild translate the game again, and what does not.

python -I -B tests/test_lift_key.py
"""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LiftKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tree = Path(cls.tmp.name)
        shutil.copytree(ROOT / "nx2", tree / "nx2")
        shutil.copytree(ROOT / "host", tree / "host")
        shutil.copytree(ROOT / "third_party" / "a64-kit", tree / "third_party" / "a64-kit")
        (tree / "work").mkdir()
        cls.exefs = tree / "exefs"
        cls.exefs.mkdir()
        (cls.exefs / "main").write_bytes(b"NSO0 main")
        (cls.exefs / "sdk").write_bytes(b"NSO0 sdk")
        cls.tree = tree
        spec = importlib.util.spec_from_file_location("make_game_lift_key", tree / "nx2" / "make_game.py")
        cls.mg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mg)
        cls.env = {"OOT_DUMP": "C:/Games/Hookshot", "A64_HOT_CHUNK": "1"}
        cls.key = cls.mg.lift_key(str(cls.exefs), cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def key_with(self, rel, extra=None):
        """The key with `extra` appended to a file of the tree (made if missing), the file as it was afterwards"""
        p = self.tree / rel
        old = p.read_bytes() if p.exists() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write(extra if extra is not None else b"\n# x\n" if rel.endswith(".py") else b"\n// x\n")
        try:
            return self.mg.lift_key(str(self.exefs), self.env)
        finally:
            p.unlink() if old is None else p.write_bytes(old)

    def test_same_inputs_same_key(self):
        self.assertTrue(self.key)
        self.assertEqual(self.mg.lift_key(str(self.exefs), self.env), self.key)

    def test_what_the_translation_reads_changes_it(self):
        for rel in ("nx2/lift.py", "nx2/oot_patches.py", "nx2/chunks.py", "third_party/a64-kit/lift/a64emit.cpp",
                    "third_party/a64-kit/tools/toolchain.py"):
            self.assertNotEqual(self.key_with(rel), self.key, rel)
        self.assertNotEqual(self.key_with("work/hot_funcs.txt", b"80001000\n"), self.key, "the shipped hot list")
        host_c = sorted(p.name for p in (self.tree / "host").glob("*.c"))[0]
        self.assertNotEqual(self.key_with("host/" + host_c, b"\nvoid imp_zz_test(Ctx *c) {}\n"), self.key, "a new imp_ function")
        self.assertNotEqual(self.key_with("host/" + host_c, b"\nHLE(zz_test) { }\n"), self.key, "a new HLE()")

    def test_the_rest_of_the_tree_leaves_it(self):
        host_c = sorted(p.name for p in (self.tree / "host").glob("*.c"))[0]
        for rel in ("nx2/nup.py", "nx2/make_game.py", "nx2/build.py", "nx2/version.py", "host/" + host_c,
                    "nx2/__pycache__/kit.cpython-312.pyc", "third_party/a64-kit/lift/a64emit.dll"):
            self.assertEqual(self.key_with(rel), self.key, rel)

    def test_the_game_and_the_settings(self):
        main = self.exefs / "main"
        main.write_bytes(b"NSO0 main, another build")
        try:
            self.assertNotEqual(self.mg.lift_key(str(self.exefs), self.env), self.key)
        finally:
            main.write_bytes(b"NSO0 main")
        self.assertNotEqual(self.mg.lift_key(str(self.exefs), dict(self.env, A64_LOCAL_FLAGS="1")), self.key)
        self.assertEqual(self.mg.lift_key(str(self.exefs), dict(self.env, OOT_DUMP="D:/Elsewhere")), self.key)

    def test_a_translator_that_does_not_compile_gives_no_key(self):
        self.assertIsNone(self.key_with("nx2/lift.py", b"\n//x not python (\n"))


if __name__ == "__main__":
    unittest.main()
