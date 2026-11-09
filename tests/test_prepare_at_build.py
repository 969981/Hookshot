"""make_game.py's last step, hookshot --prepare-shaders, relayed to the builder: its stage and progress lines go on, its own
@@done and @@error don't (the builders read those as the build's end or failure), and a failure leaves the work to the first start.

python -I -B tests/test_prepare_at_build.py
"""
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "nx2" / "make_game.py"
spec = importlib.util.spec_from_file_location("make_game_prepare", SOURCE)
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


def relayed(child):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = make_game.prepare_shaders([sys.executable, "-c", child])
    return code, out.getvalue().splitlines()


class PrepareAtBuildTests(unittest.TestCase):
    def test_progress_goes_on_and_done_does_not(self):
        code, lines = relayed("print('hookshot v'); print('@@stage shaders Preparing shaders'); print('@@progress 0.500');"
                              "print('@@done shaders')")
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["hookshot v", "@@stage shaders Preparing shaders", "@@progress 0.500", "done shaders"])

    def test_no_vulkan_is_not_a_failed_build(self):
        code, lines = relayed("import sys; print('@@error no Vulkan device'); sys.exit(3)")
        self.assertEqual(code, 3)
        self.assertFalse(any(l.startswith("@@") for l in lines))
        self.assertIn("error no Vulkan device", lines)
        self.assertTrue(lines[-1].startswith("the shaders were not prepared now (exit 3)"))

    def test_missing_game_is_not_a_failed_build(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = make_game.prepare_shaders([str(ROOT / "no-such-folder" / "hookshot.exe"), "--prepare-shaders"])
        self.assertEqual(code, 1)
        self.assertNotIn("@@", out.getvalue())

    def test_the_build_runs_it_after_the_game_folder_is_complete(self):
        text = SOURCE.read_text(encoding="utf-8")
        main = text[text.index("\ndef main("):]
        at = main.index("prepare_shaders([os.path.join(game_dir, BESIDE[0]), \"--prepare-shaders\"])")
        for before in ("os.replace(dst + \".new\", dst)", "shader_census_cmd(", "seed_pipelines(out)"):
            self.assertLess(main.index(before), at, before)
        self.assertLess(at, main.index("say(\"done\", out)"))

    def test_the_builders_can_leave_it_to_the_first_start(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertIn('"--no-prepare-shaders"', text)
        main = text[text.index("\ndef main("):]
        guard = main[:main.index("prepare_shaders([os.path.join(game_dir")].rsplit("\n", 2)[-2]
        self.assertIn("a.no_prepare_shaders", guard)
        self.assertIn('HOOKSHOT_NO_PREPARE', guard)
        for builder in ("builder/HookshotBuilder/MainWindow.xaml.cs", "builder/linux/hookshot_builder.py"):
            self.assertEqual((ROOT / builder).read_text(encoding="utf-8").count("--no-prepare-shaders"), 2, builder)   # (build and update)


if __name__ == "__main__":
    unittest.main()
