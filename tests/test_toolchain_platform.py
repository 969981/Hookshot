"""Host-versus-target tool selection for native helpers and cross-builds."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = ROOT / "third_party" / "a64-kit" / "tools" / "toolchain.py"
SPEC = importlib.util.spec_from_file_location("hookshot_toolchain_platform_test", TOOLCHAIN)
toolchain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(toolchain)

RUNNER = ROOT / "tests" / "run_all.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("hookshot_regression_runner_platform_test", RUNNER)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


class ToolchainPlatformTests(unittest.TestCase):
    def test_find_uses_host_tool_for_native_helper_during_cross_build(self):
        cross = str(ROOT / "fake-llvm-mingw")
        cross_clang = toolchain.os.path.join(cross, "bin", "clang.exe")

        def mingw(host=False):
            return "" if host else cross

        with mock.patch.object(toolchain.sys, "platform", "linux"), \
                mock.patch.object(toolchain, "mingw", side_effect=mingw), \
                mock.patch.object(toolchain.os.path, "isfile", side_effect=lambda path: path == cross_clang), \
                mock.patch.object(toolchain.shutil, "which", side_effect=lambda name: "host-" + name):
            self.assertEqual(toolchain.find("clang"), cross_clang)
            self.assertEqual(toolchain.find("clang", host=True), "host-clang")

    def test_regression_runner_identifies_each_supported_host(self):
        for platform, expected in (("win32", "windows"), ("linux", "linux"), ("darwin", "macos")):
            with self.subTest(platform=platform), mock.patch.object(runner.sys, "platform", platform):
                self.assertEqual(runner.host_os(), expected)


if __name__ == "__main__":
    unittest.main()
