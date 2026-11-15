"""Names from a game's modules go into generated C only as C names: nx2/standalone.py's stubs (gen/a64_stubs.c) and NVN
exports (gen/nvn2_exports.c) refuse a symbol outside [A-Za-z0-9_.$] instead of pasting it into C (a crafted dump's name
with a quote, a newline or a brace in it was C the build compiled), and a demangled name in a comment is printable ASCII
with no comment end. nx_image.c_ident keeps to ASCII, and the lifter's comment of a function's name is made safe too.

    python -I -B tests/test_dump_names_in_c.py
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
try:   # (lz4 unpacks NSO segments: nothing here does, and a runner without it still runs these checks)
    import lz4.block  # noqa: F401
except ImportError:
    import types
    sys.modules["lz4"] = types.ModuleType("lz4")
    sys.modules["lz4.block"] = sys.modules["lz4"].block = types.ModuleType("lz4.block")
sys.path[:0] = [str(ROOT / "nx2"), str(ROOT / "third_party" / "a64-kit" / "tools"), str(ROOT / "third_party" / "a64-kit" / "hos")]
import standalone  # noqa: E402
import nx_image  # noqa: E402

GOOD = "_ZN2nn2os13SleepThreadENS_8TimeSpanE"
BAD = ['x", 0); system("rm -rf ~"); //', "a\nint evil;", "a}b", "imp_été", "", "a b", "a/*b"]


class Stubs(unittest.TestCase):
    def gen(self, hooks, demangled=None):
        with tempfile.TemporaryDirectory() as gen, mock.patch.object(standalone, "implemented", return_value=set()), \
                mock.patch.object(standalone, "demangle", lambda syms: demangled or {s: s for s in syms}):
            standalone.gen_stubs(gen, {"hooks": hooks})
            return Path(gen, "a64_stubs.c").read_text()

    def test_a_c_name_is_written(self):
        text = self.gen([{"sym": GOOD, "c": nx_image.c_ident(GOOD)}])
        self.assertIn('a64_unimplemented(c, "%s", &seen)' % GOOD, text)

    def test_a_name_that_is_not_c_stops_the_build(self):
        for bad in BAD:
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.gen([{"sym": bad, "c": nx_image.c_ident(bad)}])

    def test_the_demangled_comment_cannot_end_the_comment(self):
        text = self.gen([{"sym": GOOD, "c": nx_image.c_ident(GOOD)}], {GOOD: "nn::os::Sleep*/ int evil; /*\n\x1b[2J"})
        comment = text.split("/* ", 1)[1].split(" */", 1)[0]
        self.assertNotIn("*/", comment)
        self.assertNotIn("/*", comment)
        self.assertNotIn("\n", comment)
        self.assertNotIn("\x1b", comment)


class NvnExports(unittest.TestCase):
    def test_only_c_names_are_written(self):
        with tempfile.TemporaryDirectory() as gen:
            standalone.write_nvn_exports(gen, ["nvnDeviceInitialize"])
            self.assertIn('nvn2_dispatch(c, "nvnDeviceInitialize", k)', Path(gen, "nvn2_exports.c").read_text())
            for bad in ('nvnX", k); evil(); //', "nvn\nint evil;"):
                with self.subTest(bad=bad), self.assertRaises(SystemExit):
                    standalone.write_nvn_exports(gen, [bad])


class Idents(unittest.TestCase):
    def test_c_ident_is_ascii(self):
        for sym in ("aéb", "x٣y", "Аbc"):
            ident = nx_image.c_ident(sym)
            self.assertTrue(ident.isascii() and standalone.C_SYMBOL.fullmatch(ident), ident)

    def test_the_lifters_comment_is_made_safe(self):
        src = (ROOT / "third_party" / "a64-kit" / "lift" / "a64_lift.py").read_text(encoding="utf-8")
        at = src.index('out[0] = "/* %s */\\n" % name + out[0]')
        self.assertIn('.replace("*/", "* /")', src[at - 300:at])


if __name__ == "__main__":
    unittest.main()
