"""Authored game-free hook fixtures and real C policy helpers; requires a C compiler."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_clock_patches", ROOT / "nx2/oot_patches.py")
PATCHES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHES)
CC = None


def fixture(load=None, name="f_80706be0"):
    # The single load is the generator's known instruction template. All other
    # code here is authored test scaffolding, not a lifted game function.
    return "void %s(Ctx *__restrict A64_NOESC c) {\n    %s\n    authored_first(c);\n    authored_second(c);\n}\n" % (name, load or PATCHES.CLOCK_LOAD)


class NativeClockPatchTests(unittest.TestCase):
    def setUp(self):
        parent = Path(tempfile.gettempdir()).resolve()
        self.temp = Path(tempfile.mkdtemp(prefix="hookshot-native-clock-", dir=parent)).resolve()

        def cleanup():
            target = self.temp.resolve()
            if target.parent != parent or not target.name.startswith("hookshot-native-clock-"):
                raise RuntimeError("test cleanup escaped its owned temporary directory")
            shutil.rmtree(target)

        self.addCleanup(cleanup)
        self.assertEqual(self.temp.parent, parent)

    def run_program(self, args, expected=0):
        result = subprocess.run([str(a) for a in args], cwd=self.temp, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def compile(self, source, name):
        self.assertIsNotNone(CC, "run this file with --cc <C compiler>; compiler checks must not be skipped")
        target = self.temp / (name + (".exe" if os.name == "nt" else ""))
        if Path(CC).stem.lower() == "cl":
            args = [CC, "/nologo", "/TC", "/std:c11", "/O2", "/W4", "/WX", "/I" + str(ROOT / "host"),
                    "/Fo" + str(self.temp / (name + ".obj")), "/Fe" + str(target), source]
        else:
            args = [CC, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", ROOT / "host", source, "-o", target]
        self.run_program(args)
        return target

    def test_insert_preserves_original_and_is_idempotent(self):
        original = fixture()
        patched = PATCHES.patch_native_clock(original)
        self.assertEqual(patched.count(PATCHES.CLOCK_LOAD), 1)
        self.assertIn("authored_first(c);\n    authored_second(c);", patched)
        self.assertEqual(PATCHES.patch_native_clock(patched), patched)
        self.assertEqual(patched.replace(PATCHES.CLOCK_PATCH, "", 1), original)

    def test_mismatch_and_multiple_matches_fail(self):
        cases = [fixture(name="authored_other"), fixture(PATCHES.CLOCK_LOAD.replace("712ULL", "716ULL")),
                 fixture(PATCHES.CLOCK_LOAD + "\n    " + PATCHES.CLOCK_LOAD), fixture() + fixture()]
        for source in cases:
            with self.subTest(source=source[:35]), self.assertRaises(RuntimeError):
                PATCHES.patch_native_clock(source)
        partial = PATCHES.patch_native_clock(fixture()).replace("&hos_clock_applied, 4", "&hos_clock_applied, 8")
        with self.assertRaisesRegex(RuntimeError, "incomplete or unexpected"):
            PATCHES.patch_native_clock(partial)

    def test_only_known_function_is_changed(self):
        other = fixture(name="authored_other")
        source = other + fixture()
        self.assertTrue(PATCHES.patch_native_clock(source).startswith(other))
        source = fixture().replace("    authored_first", '    /* } { */\n    const char *text = "}"; (void)text;\n    authored_first', 1)
        self.assertEqual(PATCHES.patch_native_clock(PATCHES.patch_native_clock(source)), PATCHES.patch_native_clock(source))

    def test_stale_build_rejected_and_patched_build_accepted(self):
        target = self.temp / "code_000.c"
        target.write_text(fixture(), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(RuntimeError, "rerun nx2/lift.py"):
            PATCHES.check_native_clock(self.temp)
        PATCHES.apply(self.temp, native_clock=True)
        first = target.read_bytes()
        PATCHES.check_native_clock(self.temp)
        PATCHES.apply(self.temp, native_clock=True)
        self.assertEqual(target.read_bytes(), first)

    def test_multiple_files_fail_before_any_write(self):
        paths = [self.temp / "code_000.c", self.temp / "code_001.c"]
        for target in paths:
            target.write_text(fixture(), encoding="utf-8", newline="\n")
        before = [path.read_bytes() for path in paths]
        with self.assertRaisesRegex(RuntimeError, "multiple MAIN"):
            PATCHES.apply(self.temp, native_clock=True)
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_non_standalone_and_legacy_default_emit_no_host_hook(self):
        target = self.temp / "code_000.c"
        original = fixture()
        target.write_text(original, encoding="utf-8", newline="\n")
        PATCHES.apply(self.temp)
        PATCHES.apply(self.temp, native_clock=False)
        self.assertEqual(target.read_text(), original)
        self.assertNotIn("hos_vi_clock_input", target.read_text())

    def test_low_four_bytes_and_original_negative(self):
        support = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "hos_rate_policy.h"
#define A64_NOESC
typedef struct { uint64_t x[32], vreg[32][2]; } Ctx;
static uint32_t loaded, state;
static unsigned char after_load[16];
static int calls;
static uint32_t recomp_load32(Ctx *c, uint64_t at) { (void)c; (void)at; return loaded; }
static void a64_vset64(uint64_t v[2], uint64_t n) { v[0] = n; memcpy(after_load, v, 16); }
uint32_t hos_vi_clock_input(uint32_t original) { return hos_rate_clock_input(original, state); }
static void authored_first(Ctx *c) { (void)c; calls++; }
static void authored_second(Ctx *c) { (void)c; calls++; }
'''
        main = r'''
int main(void) {
    const uint32_t originals[] = {60,60,60,14,121};
    const uint32_t rates[] = {75,75,0,75,75}, fps[] = {75,55,0,75,75}, expected[] = {75,55,60,14,121};
    for (unsigned i = 0; i < 5; i++) {
        Ctx c; memset(&c, 0xA5, sizeof c);
        uint64_t saved_x[32]; memcpy(saved_x, c.x, sizeof saved_x);
        loaded = originals[i]; state = hos_rate_committed_nominal_state((int)rates[i], (int)fps[i]); calls = 0;
        f_80706be0(&c);
        uint32_t actual; memcpy(&actual, &c.vreg[0][0], 4);
        if (actual != expected[i]) { fputs("low32 input mismatch\n", stderr); return 1; }
        if (memcmp((unsigned char *)c.vreg[0] + 4, after_load + 4, 12) || memcmp(c.x, saved_x, sizeof saved_x) || calls != 2)
            return 2;
    }
    return 0;
}
'''
        positive = self.temp / "patched.c"
        positive.write_text(support + PATCHES.patch_native_clock(fixture()) + main, encoding="utf-8")
        self.run_program([self.compile(positive, "patched")])
        negative = self.temp / "original.c"
        negative.write_text(support + fixture() + main, encoding="utf-8")
        result = self.run_program([self.compile(negative, "original")], expected=1)
        self.assertIn("low32 input mismatch", result.stderr)

    def test_real_policy_regressions(self):
        for name in ("rate_policy_test", "clock_input_test"):
            with self.subTest(name=name):
                self.run_program([self.compile(ROOT / ("host/tests/" + name + ".c"), name)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", required=True, help="C11 compiler; use a compiler development shell on Windows")
    args, remaining = parser.parse_known_args()
    CC = args.cc
    unittest.main(argv=[__file__, *remaining])
