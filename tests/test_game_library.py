"""One program for every game (docs/one-engine.md, step 4): the library of games under one base, --update-all over it,
--base for a build from a copy, and no game built over another. Game-free: placeholder folders (an empty image.bin and
executable, a hookshot_version.txt, a kept main that is only an NSO header naming a profile's build), and no build runs.

Checked here:
  - nx2/game_folders.py library: every buildable game in profile order, installed or not, found in any of the base's
    folders (its own first, another game's folder too, two folders both named), its own folder and the game sitting in it,
    the version and Builder the folder was built with, and the update flag by the Linux updater's rule (nx2/version.py
    update_due, its release_key held to builder/linux/linux_update.py's);
  - `nx2/game_folders.py --library <base> --json`: a list of objects with exactly the documented fields and types;
  - nx2/make_game.py --update-all: each built folder updated once by a make_game --update of its own, as the game it holds
    (in another game's folder too), in profile order, one that fails not stopping the others, a game in two folders updated
    in both, a game at this version skipped unless --force, --dry-run running nothing, the @@game lines, the summary lines
    and the exit status;
  - --update-all: a folder that keeps no game files is "rebuild", not a failure; each game other than the default one
    gets its own work folder when the work folder is the builder's own; --dry-run makes no folder and touches no power
    state; an update's reason is its @@error line; the parser takes no abbreviations;
  - --base: the copy's game picks its own folder under the base; not with --out or --update;
  - a copy of one game is refused over a folder that holds another, before the folder is touched, and an empty folder, the
    same game's, one that keeps no copy, and --update are built as before.

    python -I -B tests/test_game_library.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(ROOT / "builder" / "linux"))
import game_folders  # noqa: E402
import game_profile  # noqa: E402
import linux_update  # noqa: E402
import version  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


placeholder = load("builder_games_helpers", ROOT / "tests" / "test_builder_games.py").placeholder   # (the "Your games" check's)
ENGINE = "v2.3.0-rc.39"
FIELDS = {"key": str, "name": str, "folder": str, "base": str, "state": str, "built": bool, "holds": (str, type(None)),
          "other_folders": list, "own_folder": str, "own_folder_holds": (str, type(None)),
          "version": (str, type(None)), "builder": (str, type(None)), "kept": bool, "update": (bool, type(None))}


def keys():
    return [p.key for p in game_folders.buildable()]


def built(folder, key=None, version=None, builder=None, kept=True, exe=os.path.join("bin", "hookshot")):
    """A folder that looks like a built game: key's copy kept (none without key), and the version file the build writes"""
    placeholder(folder, game_profile.GAMES[key].build_id if key else None, exe=exe)
    if kept:
        os.makedirs(os.path.join(folder, "romfs"), exist_ok=True)
    if version:
        with open(os.path.join(folder, "hookshot_version.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write("version=%s\n" % version + ("builder=%s\n" % builder if builder else ""))


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="game-library-")
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.base = os.path.join(self.tmp, "Games", "Hookshot")
        self.ks = keys()
        self.assertGreaterEqual(len(self.ks), 3, "three games build: the checks need the default and two others")
        self.default, self.second, self.third = self.ks[0], self.ks[1], self.ks[2]
        self.assertEqual(self.default, game_profile.DEFAULT)

    def name(self, key):
        return game_profile.GAMES[key].game


class Library(Fixture):
    def test_every_game_and_its_state(self):
        second, third = (game_folders.folder(self.base, k) for k in (self.second, self.third))
        built(self.base, self.default, "v2.3.0-rc.38", builder="/opt/HookshotBuilder.exe")
        built(second, self.third, ENGINE)   # (the third game built into the second's folder)
        games = game_folders.library(self.base, engine=ENGINE)
        self.assertEqual([g["key"] for g in games], self.ks, "every buildable game, in profile order, installed or not")
        by = {g["key"]: g for g in games}
        d, s, t = by[self.default], by[self.second], by[self.third]
        self.assertEqual((d["state"], d["built"], d["holds"], d["version"], d["builder"], d["kept"], d["update"]),
                         ("built", True, self.default, "v2.3.0-rc.38", "/opt/HookshotBuilder.exe", True, True))
        self.assertEqual((d["name"], d["folder"], d["base"], d["own_folder"], d["own_folder_holds"], d["other_folders"]),
                         (self.name(self.default), self.base, self.base, self.base, None, []))
        self.assertEqual((s["state"], s["built"], s["folder"], s["own_folder"], s["own_folder_holds"], s["version"],
                          s["holds"], s["kept"], s["update"]),
                         ("missing", False, second, second, self.third, None, None, False, False),
                         "the second game's folder holds the third: said by own_folder_holds alone")
        self.assertEqual((t["state"], t["built"], t["holds"], t["folder"], t["own_folder"], t["own_folder_holds"],
                          t["version"], t["update"]),
                         ("built", True, self.third, second, third, None, ENGINE, False), "the third game, where it is")
        for g in games:
            self.assertEqual(set(g), set(FIELDS))
            self.assertIn(g["state"], ("built", "missing"))

    def test_a_game_in_the_default_games_folder(self):
        built(self.base, self.second, "v2.3.0-rc.1")   # (an older GUI build: the second game into the default game's folder)
        by = {g["key"]: g for g in game_folders.library(self.base, engine=ENGINE)}
        self.assertEqual((by[self.default]["state"], by[self.default]["folder"], by[self.default]["own_folder_holds"],
                          by[self.default]["holds"], by[self.default]["kept"]),
                         ("missing", self.base, self.second, None, False))
        self.assertEqual((by[self.second]["state"], by[self.second]["folder"], by[self.second]["update"],
                          by[self.second]["own_folder"], by[self.second]["other_folders"]),
                         ("built", self.base, True, game_folders.folder(self.base, self.second), []))

    def test_a_game_in_two_folders_its_own_first(self):
        second, third = (game_folders.folder(self.base, k) for k in (self.second, self.third))
        built(third, self.second, "v2.3.0-rc.1")
        built(second, self.second, ENGINE)
        g = {g["key"]: g for g in game_folders.library(self.base, engine=ENGINE)}[self.second]
        self.assertEqual((g["folder"], g["other_folders"], g["version"], g["update"]), (second, [third], ENGINE, False))
        self.assertEqual({g["key"]: g for g in game_folders.library(self.base, engine=ENGINE)}[self.third]["own_folder_holds"],
                         self.second)

    def test_a_folder_with_no_copy_is_the_game_whose_folder_it_is(self):
        built(game_folders.folder(self.base, self.second), None, "v2.0.0", kept=False)
        g = {g["key"]: g for g in game_folders.library(self.base, engine=ENGINE)}[self.second]
        self.assertEqual((g["state"], g["holds"], g["kept"]), ("built", None, False))

    def test_the_same_list_from_any_games_folder(self):
        built(self.base, self.default, ENGINE)
        want = game_folders.library(self.base, engine=ENGINE)
        for out in (self.base + os.sep, self.base + "-" + self.second, self.base + "-" + self.third + os.sep):
            self.assertEqual(game_folders.library(out, engine=ENGINE), want, out)
        self.assertEqual(game_folders.library("", engine=ENGINE), [])

    def test_update_flags(self):
        nightly = ENGINE + "-4-gabcdef0"
        for have, engine, want in (("v2.3.0-rc.38", ENGINE, True), (ENGINE, ENGINE, False), ("v2.4.0", ENGINE, False),
                                   ("v2.2.9", ENGINE, True), (None, ENGINE, None),
                                   (nightly, ENGINE, False),                    # (built after this release: never taken back)
                                   (ENGINE + "-2-g1234567", nightly, True)):    # (another nightly's build: updated, as the updaters do)
            with self.subTest(have=have, engine=engine):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(self.base, "hookshot_version.txt"))
                built(self.base, self.default, have)
                self.assertIs(game_folders.library(self.base, engine=engine)[0]["update"], want)
        self.assertIsNone(game_folders.library(self.base, engine="unknown")[0]["update"], "an engine with no version can't tell")

    def test_a_folder_built_before_it_kept_its_copy(self):
        built(self.base, None, "v2.0.0", kept=False, exe="hookshot")   # (no .hookshot_exefs, the executable beside image.bin)
        g = game_folders.library(self.base, engine=ENGINE)[0]
        self.assertEqual((g["state"], g["built"], g["holds"], g["kept"], g["update"], g["own_folder_holds"], g["version"]),
                         ("built", True, None, False, False, None, "v2.0.0"), "no files to update from: no update offered")

    def test_the_updaters_rule(self):
        tags = ["v2.0.7", "v2.1.0", "v2.1.0-rc.1", "v2.1.0-rc.12", "v2.1.0-rc.1-3-g1a2b3c4", "v2.0.14-12-gabcdef01",
                "v2.0.5-3-g1a2b3c4-dirty", "v3.0.0-rc.1", "2.0.1", "nightly", "rc", "dev", "1a2b3c4", "", None]
        for tag in tags:
            self.assertEqual(version.release_key(tag), linux_update.release_key(tag), tag)
        for game in tags:
            for engine in tags:
                if game and engine and engine != "unknown":
                    choose = linux_update.choose_mode(game, engine, engine, False, True) == "update-game"
                    self.assertEqual(version.update_due(game, engine), choose, (game, engine))

    def test_the_json(self):
        self.base = os.path.join(self.tmp, "Spiele \u00c4", "Hookshot")   # (a folder's letters: UTF-8 paths, tests/test_utf8_paths.py)
        built(self.base, self.default, "v2.3.0-rc.38")
        r = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "nx2" / "game_folders.py"), "--library", self.base, "--json"],
                           capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, HOOKSHOT_VERSION=ENGINE),
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        games = json.loads(r.stdout)
        self.assertIsInstance(games, list)
        self.assertEqual([g["key"] for g in games], self.ks)
        for g in games:
            self.assertEqual(set(g), set(FIELDS), g)
            for field, kind in FIELDS.items():
                self.assertIsInstance(g[field], kind, (g["key"], field))
        self.assertEqual((games[0]["state"], games[0]["update"], games[0]["folder"]), ("built", True, self.base))
        self.assertTrue(r.stdout.isascii(), "the JSON is ASCII whatever the folder's letters")
        text = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "nx2" / "game_folders.py"), "--library", self.base],
                              capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, HOOKSHOT_VERSION=ENGINE),
                              timeout=60)
        self.assertEqual(len(text.stdout.splitlines()), len(self.ks), text.stdout + text.stderr)


class MakeGame(Fixture):
    def setUp(self):
        super().setUp()
        self.mg = load("make_game_game_library", ROOT / "nx2" / "make_game.py")
        self.addCleanup(lambda: setattr(self.mg, "PREPARE", False))
        self.addCleanup(getattr(self.mg, "release_work", lambda: None))
        self.nup = os.path.join(self.tmp, "copy.nup")
        Path(self.nup).write_bytes(b"authored")

    def parse(self, *argv):
        with mock.patch.object(sys, "argv", ["make_game.py", *argv]):
            return self.mg.parse_args()

    def refused(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            self.parse(*argv)
        self.assertEqual(stop.exception.code, 1)
        return out.getvalue()


class UpdateAll(MakeGame):
    def run_all(self, *argv, fail=()):
        ran = []

        def run_one(cmd):
            folder = cmd[cmd.index("--update") + 1]
            ran.append((folder, cmd))
            return (1, "the compile stopped") if folder in fail else (0, None)
        a = self.parse("--update-all", self.base, *argv)
        out = io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["make_game.py", "--update-all", self.base, "--jobs", "4", *argv]), \
                mock.patch.dict(os.environ, HOOKSHOT_VERSION=ENGINE), contextlib.redirect_stdout(out):
            try:
                self.mg.update_all(a, run_one=run_one)
            except SystemExit as e:
                code = e.code
        return ran, out.getvalue().splitlines(), code

    def test_each_game_in_turn_and_a_failure_stops_none(self):
        built(self.base, self.default, "v2.3.0-rc.38")
        built(game_folders.folder(self.base, self.second), self.second, "v2.3.0-rc.38")
        built(game_folders.folder(self.base, self.third), self.third, "v2.3.0-rc.38")
        first, second, third = (game_folders.folder(self.base, k) for k in self.ks[:3])
        ran, lines, code = self.run_all(fail={first})
        self.assertEqual([f for f, _ in ran], [first, second, third], "every built game, in profile order, the first failing")
        for _, cmd in ran:
            self.assertEqual(cmd[-2:-1], ["--update"])
            self.assertIn("--jobs", cmd)
            self.assertNotIn("--update-all", cmd)
            self.assertNotIn(self.base, cmd[:-1])
        self.assertEqual(code, 1, "a failure: exit status 1")
        self.assertIn("@@game start %s %s" % (self.default, first), lines)
        self.assertIn("@@game end %s failed the compile stopped" % self.default, lines)
        self.assertIn("@@game end %s updated %s" % (self.second, ENGINE), lines)
        self.assertLess(lines.index("@@game start %s %s" % (self.default, first)), lines.index("@@game start %s %s" % (self.second, second)))
        self.assertEqual(lines[-len(self.ks):][:3], ["%s: failed: the compile stopped" % self.name(self.default),
                                                     "%s: updated" % self.name(self.second), "%s: updated" % self.name(self.third)])

    def test_current_skipped_missing_said_and_force(self):
        built(self.base, self.default, ENGINE)
        built(game_folders.folder(self.base, self.second), self.second, "v2.3.0-rc.1")
        ran, lines, code = self.run_all()
        self.assertEqual([f for f, _ in ran], [game_folders.folder(self.base, self.second)])
        self.assertEqual(code, 0)
        self.assertIn("@@game end %s current %s" % (self.default, ENGINE), lines)
        self.assertIn("@@game end %s missing" % self.third, lines)
        self.assertEqual(lines[-len(self.ks):][:3], ["%s: up to date (%s)" % (self.name(self.default), ENGINE),
                                                     "%s: updated" % self.name(self.second), "%s: not installed" % self.name(self.third)])
        self.assertEqual(sum(1 for l in lines if l.startswith("@@game end ")), len(self.ks), "one end line per game")
        self.assertEqual(sum(1 for l in lines if l.startswith("@@game start ")), len(self.ks), "each after its start line")
        ran, lines, code = self.run_all("--force")
        self.assertEqual([f for f, _ in ran], [self.base, game_folders.folder(self.base, self.second)])
        self.assertTrue(all("--force" not in cmd for _, cmd in ran))

    def test_a_game_in_another_games_folder_is_updated_there(self):
        built(self.base, self.second, "v2.3.0-rc.1")   # (an older GUI build: the second game in the default game's folder)
        ran, lines, code = self.run_all()
        self.assertEqual(([f for f, _ in ran], code), ([self.base], 0), "updated as the game it holds, no failure")
        self.assertIn("@@game start %s %s" % (self.second, self.base), lines)
        self.assertIn("@@game end %s updated %s" % (self.second, ENGINE), lines)
        self.assertIn("@@game end %s missing" % self.default, lines)
        self.assertFalse([l for l in lines if " failed" in l], lines)
        self.assertIn("%s: updated" % self.name(self.second), lines)

    def test_a_game_in_two_folders_is_updated_in_both(self):
        second, third = (game_folders.folder(self.base, k) for k in (self.second, self.third))
        built(second, self.second, "v2.3.0-rc.1")
        built(third, self.second, "v2.3.0-rc.2")
        ran, lines, code = self.run_all()
        self.assertEqual(([f for f, _ in ran], code), ([second, third], 0), "each folder once, its own first")
        self.assertEqual([l for l in lines if l.startswith("@@game start %s " % self.second)],
                         ["@@game start %s %s" % (self.second, second), "@@game start %s %s" % (self.second, third)])
        self.assertIn("%s in %s: updated" % (self.name(self.second), second), lines)
        self.assertIn("%s in %s: updated" % (self.name(self.second), third), lines)
        self.assertTrue([l for l in lines if l.startswith("%s is installed in 2 folders" % self.name(self.second))], lines)

    def test_dry_run_changes_nothing(self):
        second = game_folders.folder(self.base, self.second)
        built(self.base, self.default, ENGINE)
        built(second, self.second, "v2.3.0-rc.1")
        ran, lines, code = self.run_all("--dry-run")
        self.assertEqual((ran, code), ([], 0), "nothing run")
        framing = [l for l in lines if l.startswith("@@game ")]
        self.assertEqual(framing[:6], ["@@game start %s %s" % (self.default, self.base), "@@game end %s current %s" % (self.default, ENGINE),
                                       "@@game start %s %s" % (self.second, second), "@@game end %s update from v2.3.0-rc.1" % self.second,
                                       "@@game start %s %s" % (self.third, game_folders.folder(self.base, self.third)),
                                       "@@game end %s missing" % self.third])
        self.assertIn("%s: would be updated (from v2.3.0-rc.1)" % self.name(self.second), lines)
        ran, lines, code = self.run_all("--dry-run", "--force")
        self.assertEqual(ran, [])
        self.assertIn("@@game end %s update from %s" % (self.default, ENGINE), lines)
        with mock.patch.object(sys, "argv", ["make_game.py", "--update-all", self.base, "--dry-run"]), \
                mock.patch.dict(os.environ, HOOKSHOT_VERSION=ENGINE), contextlib.redirect_stdout(io.StringIO()):
            a = self.mg.parse_args()
            with mock.patch.object(self.mg, "update_one", side_effect=AssertionError("a dry run ran an update")):
                self.mg.update_all(a)

    def test_a_folder_without_its_game_files_is_rebuild_not_a_failure(self):
        built(self.base, None, "v2.0.0", kept=False)
        built(game_folders.folder(self.base, self.second), self.second, "v2.3.0-rc.1")
        for extra in ((), ("--force",), ("--dry-run",)):
            with self.subTest(extra=extra):
                ran, lines, code = self.run_all(*extra)
                self.assertNotIn(self.base, [f for f, _ in ran], "never run: it would fail with no files to update from")
                self.assertEqual(code, 0, "not a failure")
                self.assertIn("@@game end %s rebuild %s" % (self.default, self.mg.REBUILD), lines)
                self.assertIn("%s: not updated: %s" % (self.name(self.default), self.mg.REBUILD), lines)

    def test_each_game_its_own_work_folder(self):
        built(self.base, self.default, "v2.3.0-rc.1")
        built(game_folders.folder(self.base, self.second), self.second, "v2.3.0-rc.1")
        built(self.base + "-" + self.third, self.second, "v2.3.0-rc.1")   # (the second game in the third's folder too)
        default_work = self.parse("--update-all").default_work
        ran, _, _ = self.run_all()
        works = {f: cmd[cmd.index("--work") + 1] if "--work" in cmd else None for f, cmd in ran}
        self.assertEqual(works, {self.base: None, game_folders.folder(self.base, self.second): default_work + "-" + self.second,
                                 self.base + "-" + self.third: default_work + "-" + self.second},
                         "the default game's work folder as it was; another's <work>-<key>, by the game the folder holds")
        mine = os.path.join(self.tmp, "my work")
        ran, _, _ = self.run_all("--work", mine)
        for f, cmd in ran:
            self.assertEqual([cmd[i + 1] for i, x in enumerate(cmd) if x == "--work"], [mine], "a work folder given is kept")

    def test_two_folders_say_what_happened_to_each(self):
        second, third = (game_folders.folder(self.base, k) for k in (self.second, self.third))
        built(second, self.second, "v2.3.0-rc.1")
        built(third, self.second, "v2.3.0-rc.1")
        ran, lines, code = self.run_all(fail={third})
        self.assertEqual(code, 1)
        self.assertIn("%s is installed in 2 folders under %s: updated in %s; failed in %s." % (
            self.name(self.second), self.base, second, third), lines)

    def test_no_abbreviations(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            self.parse("--update-al", self.base)
        self.assertEqual(stop.exception.code, 2)
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        self.assertIn("allow_abbrev=False", src[src.index("def parse_args():"):src.index("def spinning_disk(")])

    def test_a_dry_run_touches_nothing(self):
        work = os.path.join(self.tmp, "no such work")
        built(self.base, self.default, "v2.3.0-rc.1")
        a = self.parse("--update-all", self.base, "--dry-run", "--work", work)
        with mock.patch.object(self.mg, "no_throttle", side_effect=AssertionError("a dry run changed a power state")), \
                mock.patch("ctypes.WinDLL", create=True, side_effect=AssertionError("a dry run kept the PC awake")), \
                mock.patch.object(self.mg, "update_one", side_effect=AssertionError("a dry run ran an update")), \
                mock.patch.object(sys, "argv", ["make_game.py", "--update-all", self.base, "--dry-run", "--work", work]), \
                mock.patch.dict(os.environ, HOOKSHOT_VERSION=ENGINE), contextlib.redirect_stdout(io.StringIO()) as out:
            self.mg.main(a)
        self.assertFalse(os.path.exists(work), "no work folder made")
        self.assertIn("@@game end %s update from v2.3.0-rc.1" % self.default, out.getvalue())

    def test_the_arguments_and_the_options_it_refuses(self):
        self.assertEqual(self.mg.update_all_args(["--update-all", "/b", "--force", "--log", "x.txt", "--jobs", "2", "--prepare"]),
                         ["--jobs", "2", "--prepare"])
        self.assertEqual(self.mg.update_all_args(["--update-all", "--no-dlss", "--log=x"]), ["--no-dlss"])
        self.assertEqual(self.parse("--update-all").update_all, "")
        for extra in (["--out", "o"], ["--update", "o"], ["--nup", self.nup], ["--base", "b"], ["--game-%s=%s" % (self.second, self.nup)]):
            self.assertIn("--update-all updates the games already built", self.refused("--update-all", *extra), extra)
        self.assertIn("--force goes with --update-all", self.refused("--nup", self.nup, "--out", "o", "--force"))
        self.assertIn("--dry-run goes with --update-all", self.refused("--nup", self.nup, "--out", "o", "--dry-run"))
        self.assertEqual(self.mg.update_all_args(["--update-all", "--dry-run", "--jobs", "2"]), ["--jobs", "2"])

    def test_main_runs_it(self):
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        body = src[src.index("\ndef main(a=None):"):]
        self.assertLess(body.index("return update_all(a)"), body.index("return build_each(a)"))

    def test_one_games_lines_and_its_reason(self):
        code = ("import sys; print('@@stage read Reading'); print('@@error The drive is full.'); "
                "print('error: a compiler said this later'); print('on stderr', file=sys.stderr); sys.exit(1)")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = self.mg.update_one([sys.executable, "-I", "-c", code])
        self.assertEqual(got, (1, "The drive is full."))
        self.assertIn("@@stage read Reading", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mg.update_one([sys.executable, "-I", "-c", "pass"]), (0, None))
            self.assertEqual(self.mg.update_one([sys.executable, "-I", "-c", "print('error: not a reason'); raise SystemExit(3)"]),
                             (3, "exit status 3"), "only an @@error line is a reason")
            self.mg.PREPARE = True   # (under --prepare the update says RecompLauncher's error line instead)
            self.assertEqual(self.mg.update_one([sys.executable, "-I", "-c", "print('error The drive is full.'); raise SystemExit(1)"]),
                             (1, "The drive is full."))


class Base(MakeGame):
    def test_the_copy_picks_its_folder(self):
        for key in self.ks:
            for given in (self.base, self.base + os.sep, self.base + "-" + self.ks[-1]):
                with self.subTest(key=key, given=given):
                    a = self.parse("--base", given, "--nup", self.nup, "--work", os.path.join(self.tmp, "work"))
                    self.assertEqual((a.out, a.out_from_game), (self.base, True))
                    self.mg.dump_build_id = lambda a, work, key=key: game_profile.GAMES[key].build_id
                    a.copy_bid = None
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.mg.game_folders_for_dump(a)
                    self.assertEqual(a.out, game_folders.folder(self.base, key))
                    self.assertEqual(a.work, os.path.join(self.tmp, "work"), "a work folder given is kept")

    def test_with_prepare(self):
        a = self.parse("--base", self.base, "--nup", self.nup, "--prepare")
        self.assertEqual((a.out, a.out_from_game), (self.base, True))

    def test_not_with_out_or_update(self):
        self.assertIn("not both", self.refused("--base", self.base, "--out", "o", "--nup", self.nup))
        self.assertIn("takes no --base", self.refused("--base", self.base, "--update", self.base))
        self.assertEqual(self.parse("--nup", self.nup, "--out", "o").out, "o", "--out as before")


class Refusal(MakeGame):
    def refuse(self, out, copy_key, update=None, nup=None):
        """refuse_other_game for a copy of copy_key: the text it stopped with, or None; and whether the copy was read"""
        read = []

        def dump_build_id(a, work):
            read.append(1)
            return game_profile.GAMES[copy_key].build_id
        self.mg.dump_build_id = dump_build_id
        a = types.SimpleNamespace(update=update, nup=nup or self.nup, exefs=None, work=os.path.join(self.tmp, "work"))
        out_text = io.StringIO()
        with contextlib.redirect_stdout(out_text):
            try:
                self.mg.refuse_other_game(a, out)
            except SystemExit:
                return out_text.getvalue(), bool(read)
        return None, bool(read)

    def test_another_games_folder_is_refused(self):
        built(self.base, self.second)
        said, _ = self.refuse(self.base, self.default)
        self.assertTrue(said, "a copy of the default game over another game's folder")
        for named in (self.base, self.name(self.second), self.name(self.default)):
            self.assertIn(named, said)
        own = game_folders.folder(self.base, self.third)
        built(own, self.second)
        said, _ = self.refuse(own, self.third)
        self.assertIn(own, said)

    def test_under_prepare_the_player_is_told_to_move_the_folder(self):
        built(self.base, self.second)
        self.mg.PREPARE = True
        said, _ = self.refuse(self.base, self.default)
        self.assertTrue(said.startswith("error "), said)
        self.assertIn("Move or rename that folder", said)
        self.assertNotIn("Choose an empty folder", said)
        for named in (self.base, self.name(self.second), self.name(self.default)):
            self.assertIn(named, said)

    def test_empty_same_game_old_folder_and_update_are_built_as_before(self):
        self.assertEqual(self.refuse(os.path.join(self.tmp, "empty"), self.second), (None, False), "an empty folder: not even read")
        built(self.base, self.default)
        self.assertEqual(self.refuse(self.base, self.default)[0], None, "the same game")
        self.assertEqual(self.refuse(self.base, self.second, update=self.base), (None, False), "--update builds the folder's own copy")
        old = os.path.join(self.tmp, "old")
        built(old, None, kept=False)
        self.assertEqual(self.refuse(old, self.second), (None, False), "a folder that keeps no copy, as before")

    def test_the_nup_the_folder_was_unpacked_from_is_not_read_again(self):
        built(self.base, self.default)
        st = os.stat(self.nup)
        Path(self.base, ".hookshot_romfs").write_text(self.mg.unpack_stamp(self.nup, st.st_size) + "\n", encoding="utf-8")
        self.assertEqual(self.refuse(self.base, self.second), (None, False))

    def test_main_refuses_before_the_folder_is_touched(self):
        built(self.base, self.second)
        before = sorted(p.relative_to(self.base) for p in Path(self.base).rglob("*"))
        work = os.path.join(self.tmp, "work")
        a = self.parse("--nup", self.nup, "--out", self.base, "--work", work, "--jobs", "1")
        self.mg.dump_build_id = lambda a, work: game_profile.GAMES[self.default].build_id
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(self.mg, "no_throttle"), \
                mock.patch("ctypes.WinDLL", create=True), self.assertRaises(SystemExit):
            self.mg.main(a)
        self.assertIn("@@error The game folder %s holds %s" % (os.path.abspath(self.base), self.name(self.second)), out.getvalue())
        self.assertEqual(sorted(p.relative_to(self.base) for p in Path(self.base).rglob("*")), before)
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        body = src[src.index("\ndef main(a=None):"):]
        at = body.index("refuse_other_game(a, out)")
        self.assertLess(body.index("lock_work(work)"), at)
        for later in ("build_language(a, out)", "check_writable(out)", "fsr_wanted(a, out)", "clear_folder(exefs)"):
            self.assertLess(at, body.index(later), later)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
