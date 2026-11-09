#!/usr/bin/env python3
"""The NGX library a Windows build links must be the one for its architecture.

nx2/build.py used to walk the SDK's lib/ folder for the first Windows directory holding an
nvsdk_ngx_s.lib. On the real SDK that layout is

    lib/Windows_aarch64/arm64/nvsdk_ngx_s.lib
    lib/Windows_arm64ec/arm64ec/nvsdk_ngx_s.lib
    lib/Windows_x86_64/x64/nvsdk_ngx_s.lib

and the walk reaches Windows_aarch64 first, so an x64 build linked an ARM64 library and ld.lld said
"machine type arm64 conflicts with x64" five times at the end of a two-minute compile. Nothing
caught it because nothing linked DLSS on Windows at all until the link line started calling
dlss_sdk_link(). This is that test.
"""
import ast
import io
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "nx2" / "build.py"


def picker(linux):
    """dlss_sdk_path and dlss_sdk_link, lifted out of build.py and run on their own."""
    tree = ast.parse(io.open(BUILD, encoding="utf-8").read(), filename=str(BUILD))
    names = {"dlss_sdk_path", "dlss_sdk_link"}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in functions} == names, "build.py no longer defines %s" % names
    namespace = {
        "os": os, "platform": __import__("platform"), "LINUX": linux,
        "kit": type("Kit", (), {"HERE": str(ROOT / "nx2")})(),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(BUILD), "exec"), namespace)
    return namespace["dlss_sdk_link"]


class DlssSdkLinkArch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hookshot-dlss-sdk-")
        self.addCleanup(self.temp.cleanup)
        self.sdk = pathlib.Path(self.temp.name) / "dlss-sdk"
        (self.sdk / "include").mkdir(parents=True)
        (self.sdk / "include" / "nvsdk_ngx.h").touch()
        # The SDK's own layout, in the order os.walk sees it.
        for arch, sub in (("Windows_aarch64", "arm64"), ("Windows_arm64ec", "arm64ec"),
                          ("Windows_x86_64", "x64")):
            (self.sdk / "lib" / arch / sub).mkdir(parents=True)
            (self.sdk / "lib" / arch / sub / "nvsdk_ngx_s.lib").touch()
        for arch in ("Linux_aarch64", "Linux_x86_64"):
            (self.sdk / "lib" / arch).mkdir(parents=True)
            (self.sdk / "lib" / arch / "libnvsdk_ngx.a").touch()
        os.environ["RX_VK_DLSS_SDK"] = str(self.sdk)
        self.addCleanup(os.environ.pop, "RX_VK_DLSS_SDK", None)

    def test_windows_links_the_x64_library(self):
        got = picker(linux=False)()
        self.assertEqual(len(got), 1, got)
        self.assertIn("Windows_x86_64", got[0])
        self.assertTrue(os.path.isfile(got[0]), got)

    def test_windows_never_links_an_arm_library(self):
        got = picker(linux=False)()
        joined = " ".join(got)
        for wrong in ("aarch64", "arm64ec", "Windows_arm"):
            self.assertNotIn(wrong, joined,
                             "an x64 build must not link %s: %s" % (wrong, joined))

    def test_linux_still_links_by_minus_l(self):
        got = picker(linux=True)()
        self.assertIn("-lnvsdk_ngx", got, got)
        self.assertTrue(any(a.startswith("-L") and "Linux_" in a for a in got), got)

    def test_no_sdk_links_nothing(self):
        os.environ.pop("RX_VK_DLSS_SDK", None)
        os.environ["RX_VK_DLSS_SDK"] = str(self.sdk / "nowhere")
        self.assertEqual(picker(linux=False)(), [])


if __name__ == "__main__":
    unittest.main()
