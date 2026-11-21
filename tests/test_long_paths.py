"""nx2/long_paths.py, game-free: what install() promises (#1115).

- long_form(): a path under 248 characters once absolute, and one in \\\\?\\ or \\\\.\\ form, bytes, a descriptor, as they
  are; a longer one as \\\\?\\ plus its absolute form, a share as \\\\?\\UNC\\...; elsewhere than Windows, as it is.
- install() once is enough and again does nothing; it leaves every short path to the calls as it was, and with it a tree
  past 260 characters (a folder and a file) is made, written, read, listed, walked, stat'd, renamed, replaced, linked,
  copied (shutil.copyfile, copy2, move) and removed (os.remove, shutil.rmtree) by plain paths, and a walk gives every
  folder back spelled as os.walk spells it.

On Windows with the long-paths setting off (the default) the tree part needs install(); elsewhere it checks only that the
calls still work. It runs in a process of its own (install() is for the whole process).

    python -I -B tests/test_long_paths.py
"""
import builtins
import glob
import os
from pathlib import Path
import shutil
import sys
import tarfile   # (loaded before install(), as make_game.py loads it: it keeps open as it was then)
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import long_paths  # noqa: E402

ORIGINAL_OPEN = builtins.open
WINDOWS = os.name == "nt"


class LongForm(unittest.TestCase):
    def test_short_and_special_paths_are_as_they_are(self):
        for p in ("a.txt", r"C:\x\y", "C:/x/y", b"C:\\x", 3, None, "\\\\?\\C:\\" + "d" * 300, "\\\\.\\pipe\\" + "p" * 300,
                  "//?/C:/" + "d" * 300, "//./pipe/" + "p" * 300, Path("rel") / "x"):
            self.assertIs(long_paths.long_form(p), p, repr(p))

    @unittest.skipUnless(WINDOWS, "the extended-length form is Windows'")
    def test_a_long_path_takes_the_extended_form(self):
        p = "C:\\" + "\\".join(["d" * 50] * 5) + "\\f.txt"
        self.assertEqual(long_paths.long_form(p), "\\\\?\\" + p)
        self.assertEqual(long_paths.long_form(p.replace("\\", "/")), "\\\\?\\" + p, "slashes made Windows' own")
        self.assertEqual(long_paths.long_form("C:\\x\\..\\" + p[3:]), "\\\\?\\" + p, "'..' resolved first")
        share = "\\\\server\\share\\" + "d" * 250
        self.assertEqual(long_paths.long_form(share), "\\\\?\\UNC\\server\\share\\" + "d" * 250)
        self.assertEqual(long_paths.long_form(Path(p)), "\\\\?\\" + p)
        just = "C:\\" + "d" * (long_paths.LIMIT - 4)   # (one under the limit: as it is)
        self.assertIs(long_paths.long_form(just), just)

    @unittest.skipUnless(WINDOWS, "the extended-length form is Windows'")
    def test_the_limit_counts_windows_units(self):
        """Windows counts a path in UTF-16 units: a character past U+FFFF (an emoji) is two of them, one in Python"""
        p = "C:\\" + "\\".join(["\U0001F600" * 30] * 5)   # (157 characters, 307 units)
        self.assertLess(len(p), long_paths.LIMIT)
        self.assertEqual(long_paths.long_form(p), "\\\\?\\" + p)
        just = "C:\\" + "\U0001F600" * ((long_paths.LIMIT - 4) // 2)   # (125 characters, one unit under the limit: as it is)
        self.assertIs(long_paths.long_form(just), just)

    @unittest.skipIf(WINDOWS, "elsewhere than Windows")
    def test_elsewhere_nothing_changes(self):
        p = "/" + "/".join(["d" * 50] * 6)
        self.assertIs(long_paths.long_form(p), p)
        long_paths.install()
        self.assertIs(builtins.open, ORIGINAL_OPEN, "install() changed something off Windows")


@unittest.skipUnless(WINDOWS, "install() does nothing elsewhere")
class Installed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        long_paths.install()
        cls.once = (builtins.open,)   # (in a tuple: a function kept on the class would come back a bound method)
        long_paths.install()

    def setUp(self):
        self.base = os.path.abspath(tempfile.mkdtemp(prefix="lp-"))
        self.addCleanup(shutil.rmtree, "\\\\?\\" + self.base, True)
        self.deep = os.path.join(self.base, *(["d" * 60] * 4))   # (the folder past 260)
        self.assertGreater(len(self.deep), 260)

    def test_installed_once(self):
        self.assertIsNot(builtins.open, ORIGINAL_OPEN)
        self.assertIs(builtins.open, self.once[0], "a second install() wrapped the calls again")
        import io
        self.assertIs(io.open, builtins.open)

    def test_short_paths_go_on_as_they_are(self):
        p = os.path.join(self.base, "short.txt")
        with open(p, "w") as f:
            f.write("x")
        with open(p) as f:
            self.assertEqual(f.name, p, "a short path was changed on its way")
        self.assertTrue(os.path.isfile(p))
        self.assertEqual([e.path for e in os.scandir(self.base) if e.name == "short.txt"], [p])

    def test_a_tree_past_260_by_plain_paths(self):
        os.makedirs(self.deep)
        f = os.path.join(self.deep, "file.txt")
        with open(f, "w") as out:
            out.write("one")
        self.assertTrue(os.path.exists(f) and os.path.isfile(f) and os.path.isdir(self.deep) and not os.path.islink(f))
        self.assertEqual(os.path.getsize(f), 3)
        self.assertGreater(os.path.getmtime(f), 0)
        self.assertEqual(os.stat(f).st_size, 3)
        self.assertEqual(os.listdir(self.deep), ["file.txt"])
        os.utime(f, (1, 1))
        self.assertEqual(int(os.path.getmtime(f)), 1)
        g = os.path.join(self.deep, "renamed.txt")
        os.rename(f, g)
        with open(f, "w") as out:
            out.write("two")
        os.replace(f, g)
        with open(g) as src:
            self.assertEqual(src.read(), "two")
        os.link(g, os.path.join(self.deep, "linked.txt"))
        shutil.copyfile(g, os.path.join(self.deep, "copied.txt"))
        shutil.copy2(g, os.path.join(self.deep, "copied2.txt"))
        shutil.move(os.path.join(self.deep, "copied2.txt"), os.path.join(self.deep, "moved.txt"))
        self.assertEqual(sorted(os.listdir(self.deep)), ["copied.txt", "linked.txt", "moved.txt", "renamed.txt"])
        os.remove(os.path.join(self.deep, "linked.txt"))
        self.assertFalse(os.path.exists(os.path.join(self.deep, "linked.txt")))
        shutil.rmtree(os.path.join(self.base, "d" * 60))
        self.assertEqual(os.listdir(self.base), [])

    def test_calls_keep_their_own_ways(self):
        """no argument, keywords, and what os says each call supports: as before install()"""
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.base)
        with open(file="kw.txt", mode="w") as f:
            f.write("x")
        self.assertEqual(os.listdir(), ["kw.txt"])
        with os.scandir() as it:
            self.assertEqual([e.name for e in it], ["kw.txt"])
        self.assertTrue(os.path.exists(path="kw.txt"))
        self.assertEqual(os.stat(path="kw.txt").st_size, 1)
        for group in ("supports_fd", "supports_follow_symlinks", "supports_dir_fd", "supports_effective_ids"):
            held = getattr(os, group)
            for name in ("stat", "lstat", "listdir", "scandir", "open", "chmod", "utime", "access", "truncate", "link"):
                fn = getattr(os, name, None)
                if fn is not None:
                    self.assertEqual(fn in held, getattr(fn, "__wrapped__", fn) in held, "os.%s in os.%s" % (name, group))
        self.assertIn(os.stat, os.supports_follow_symlinks)

    def test_more_calls_past_260(self):
        """the ways the build reaches files that go through os.open, a copy2 kept as a default, or an open kept at import"""
        os.makedirs(os.path.join(self.deep, "sub"))
        with open(os.path.join(self.deep, "sub", "a.txt"), "w") as out:
            out.write("a")
        fd, temp = tempfile.mkstemp(dir=self.deep)
        os.close(fd)
        with tempfile.NamedTemporaryFile(dir=self.deep, delete=False) as t:
            t.write(b"t")
        Path(self.deep, "touched").touch()
        fd = os.open(os.path.join(self.deep, "raw"), os.O_WRONLY | os.O_CREAT)
        os.close(fd)
        shutil.copytree(os.path.join(self.deep, "sub"), os.path.join(self.deep, "copied"))
        self.assertEqual(os.listdir(os.path.join(self.deep, "copied")), ["a.txt"])
        tar = os.path.join(self.deep, "x.tar")
        with tarfile.open(tar, "w") as t:
            t.add(os.path.join(self.deep, "sub", "a.txt"), arcname="a.txt")
        with tarfile.open(tar) as t:
            self.assertEqual(t.getnames(), ["a.txt"])
        with zipfile.ZipFile(os.path.join(self.deep, "x.zip"), "w") as z:
            z.writestr("z.txt", "z")
        self.assertEqual(sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.deep, "*.zip"))), ["x.zip"])
        for name in (os.path.basename(temp), os.path.basename(t.name), "touched", "raw"):
            self.assertTrue(os.path.isfile(os.path.join(self.deep, name)), name)

    def test_paths_come_back_as_given(self):
        os.makedirs(os.path.join(self.deep, "sub"))
        f = os.path.join(self.deep, "f.txt")
        with open(f, "w") as out:
            out.write("x")
        with os.scandir(self.deep) as it:
            self.assertEqual(sorted(e.path for e in it), [f, os.path.join(self.deep, "sub")])
        self.assertEqual(os.fspath(next(iter(os.scandir(self.deep)))).startswith(self.deep), True)
        for topdown in (True, False):
            with self.subTest(topdown=topdown):
                folders = [d for d, _, _ in os.walk(self.base, topdown=topdown)]
                self.assertIn(os.path.join(self.deep, "sub"), folders)
                self.assertTrue(all(d.startswith(self.base) for d in folders), "a folder came back as \\\\?\\...")
        self.assertEqual(shutil.copy(f, os.path.join(self.deep, "c.txt")), os.path.join(self.deep, "c.txt"))
        self.assertEqual(shutil.copy2(f, os.path.join(self.deep, "c2.txt")), os.path.join(self.deep, "c2.txt"))
        self.assertEqual(shutil.move(os.path.join(self.deep, "c2.txt"), os.path.join(self.deep, "m.txt")),
                         os.path.join(self.deep, "m.txt"))

    def test_a_tree_past_260_windows_units(self):
        """folders each within their own limit, under 248 characters in all but past 260 UTF-16 units"""
        deep = os.path.join(self.base, *(["\U0001F600" * 30] * 4))
        self.assertLess(len(deep), long_paths.LIMIT)
        self.assertGreater(len(deep.encode("utf-16-le")) // 2, 260)
        os.makedirs(deep)
        f = os.path.join(deep, "f.txt")
        with open(f, "w") as out:
            out.write("x")
        self.assertTrue(os.path.isfile(f))
        self.assertEqual(os.listdir(deep), ["f.txt"])

    def test_every_call_by_its_own_keyword(self):
        """each call wrapped, its path given by the keyword the call itself names it with (os.open's path,
        os.path.isdir's s or path, getsize's filename...), reaches past 260 (islink: False for a file either way)"""
        os.makedirs(self.deep)
        f = os.path.join(self.deep, "f.txt")
        os.close(os.open(path=f, flags=os.O_WRONLY | os.O_CREAT))
        self.assertTrue(os.path.isfile(f), "os.open(path=...)")
        import inspect
        for mod, names, value in ((os.path, ("exists", "lexists", "isfile", "getsize", "getmtime",
                                            "getatime", "getctime"), f),
                                  (os.path, ("isdir",), self.deep),
                                  (os, ("stat", "lstat", "access"), f)):
            for name in names:
                fn = getattr(mod, name)
                key = next(iter(inspect.signature(getattr(fn, "__wrapped__", fn)).parameters))
                extra = {"mode": os.F_OK} if name == "access" else {}
                with self.subTest(call=name, keyword=key):
                    got = fn(**{key: value}, **extra)
                    self.assertTrue(got is not False and got is not None, "%s(%s=...) missed the file" % (name, key))
        os.rename(src=f, dst=os.path.join(self.deep, "g.txt"))
        self.assertEqual(os.listdir(self.deep), ["g.txt"])

    def _symlink(self, target, link):
        try:
            os.symlink(target, link)
        except OSError as e:
            if getattr(e, "winerror", None) == 1314:   # (ERROR_PRIVILEGE_NOT_HELD: no Developer Mode, not elevated)
                self.skipTest("making a symbolic link needs Developer Mode or an elevated prompt here")
            raise

    def test_a_link_target_as_symlink_is_given_it(self):
        """what os.symlink's wrapper hands the real call, without making a link (that needs a privilege): a relative
        target as written from a current folder deep enough to cross the limit, the link and an absolute target long"""
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        cwd = os.path.join(self.base, "c" * (240 - len(self.base) - 1))
        os.makedirs(cwd)
        os.chdir(cwd)
        got = []
        record = long_paths._symlink(lambda *args, **kw: got.append((args, kw)))
        target = os.path.join("assets", "file.txt")
        self.assertGreaterEqual(len(os.path.abspath(target)), long_paths.LIMIT)
        deep_link = os.path.join(self.deep, "link")
        deep_target = os.path.join(self.deep, "f.txt")
        short_link = os.path.join(self.base, "link")
        want = []
        for rel in (target, "\\" + target, "C:" + target):   # (relative, the current drive's root's, drive C:'s current)
            record(rel, short_link)
            record(src=rel, dst=deep_link)
            want += [((rel, short_link), {}), ((), {"src": rel, "dst": "\\\\?\\" + deep_link})]
        record(deep_target, deep_link, True)
        want.append((("\\\\?\\" + deep_target, "\\\\?\\" + deep_link, True), {}))
        self.assertEqual(got, want)
        self.assertEqual(os.symlink.__qualname__, "_symlink.<locals>.symlink", "install() put it on os.symlink")

    def test_a_relative_link_target_stays_relative(self):
        """a relative target is the link's folder's, not the current folder's: its text is kept, even from a current
        folder deep enough that it would cross the limit once absolute"""
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        cwd = os.path.join(self.base, "c" * (240 - len(self.base) - 1))
        os.makedirs(cwd)
        os.chdir(cwd)
        short = os.path.join(self.base, "s")
        os.makedirs(os.path.join(short, "assets"))
        with open(os.path.join(short, "assets", "file.txt"), "w") as out:
            out.write("x")
        target = os.path.join("assets", "file.txt")
        self.assertGreaterEqual(len(os.path.abspath(target)), long_paths.LIMIT)
        link = os.path.join(short, "link")
        self._symlink(target, link)
        self.assertEqual(os.readlink(link), target)
        with open(link) as src:
            self.assertEqual(src.read(), "x")

    def test_a_link_past_260(self):
        """an absolute target past 260, and the link itself past 260, by plain paths"""
        os.makedirs(self.deep)
        f = os.path.join(self.deep, "f.txt")
        with open(f, "w") as out:
            out.write("x")
        link = os.path.join(self.base, "link")
        self._symlink(f, link)
        with open(link) as src:
            self.assertEqual(src.read(), "x")
        deep_link = os.path.join(self.deep, "link")
        self._symlink("f.txt", deep_link)
        self.assertTrue(os.path.islink(deep_link))
        with open(deep_link) as src:
            self.assertEqual(src.read(), "x")

    def test_a_walk_reaches_folders_past_260_spelled_as_given(self):
        os.makedirs(os.path.join(self.deep, "sub"))
        with open(os.path.join(self.deep, "sub", "f.c"), "w") as out:
            out.write("x")
        for top in (self.base, self.base + "\\", self.base.replace("\\", "/")):
            with self.subTest(top=top):
                got = [(d, sorted(fs)) for d, _, fs in os.walk(top)]
                folders = [d for d, _ in got]
                self.assertEqual(folders[0], top, "the top as given")
                want = os.path.join(top, *(["d" * 60] * 4), "sub")
                self.assertIn(want, folders, "the folder past 260, spelled as os.walk joins it")
                self.assertIn((want, ["f.c"]), got)


if __name__ == "__main__":
    unittest.main()
