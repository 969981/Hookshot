"""Verify the macOS branch of the production build configuration without a game dump."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MacOSBuildPathTests(unittest.TestCase):
    def test_macos_toolchain_uses_the_xcrun_sdk(self):
        source = ROOT / "third_party" / "a64-kit" / "tools" / "toolchain.py"
        script = r"""
import runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import patch
sys.platform = "darwin"
tools = runpy.run_path(sys.argv[1])
with tempfile.TemporaryDirectory() as sdk:
    with patch.dict("os.environ", {"SDKROOT": ""}), patch("shutil.which", return_value="xcrun"), \
         patch("subprocess.run", return_value=types.SimpleNamespace(returncode=0, stdout=sdk, stderr="")) as run:
        assert tools["macos_sysroot_flags"]() == ["-isysroot", sdk]
        run.assert_called_once_with(["xcrun", "--sdk", "macosx", "--show-sdk-path"],
                                    capture_output=True, text=True)
"""
        subprocess.run([sys.executable, "-I", "-c", script, str(source)], check=True,
                       capture_output=True, text=True)

    def test_selects_native_arm64_posix_and_metal_build_target(self):
        source = ROOT / "nx2" / "build.py"
        script = (
            "import json, runpy, sys; "
            "sys.platform = 'darwin'; "
            "b = runpy.run_path(sys.argv[1]); "
            "b['toolchain'].macos_sysroot_flags = lambda: ['-isysroot', '/MacOSX.sdk']; "
            "print(json.dumps({k: b[k] for k in ('WINDOWS', 'LINUX', 'MACOS', 'POSIX_HOST', 'EXE', 'MARCH', 'CPU')} "
            "| {'target': b['target']()}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", script, str(source)],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(config, {
            "WINDOWS": False,
            "LINUX": False,
            "MACOS": True,
            "POSIX_HOST": True,
            "EXE": "hookshot",
            "MARCH": "apple-m1",
            "CPU": ["-mcpu=apple-m1", "-ffp-contract=off"],
            "target": ["--target=arm64-apple-macos13.3", "-isysroot", "/MacOSX.sdk"],
        })

    def test_game_builder_selects_macos_bundle_files(self):
        source = ROOT / "nx2" / "make_game.py"
        script = (
            "import json, runpy, sys; "
            "sys.platform = 'darwin'; "
            "b = runpy.run_path(sys.argv[1]); "
            "print(json.dumps({k: b[k] for k in ('LINUX', 'MACOS', 'BESIDE')}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", script, str(source)],
                                check=True, capture_output=True, text=True)
        config = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(config, {
            "LINUX": False,
            "MACOS": True,
            "BESIDE": ["hookshot", "libSDL2-2.0.0.dylib", "libwgpu_native.dylib"],
        })

    def test_macos_preflight_verifies_dependency_architectures(self):
        source = ROOT / "nx2" / "make_game.py"
        script = r"""
import os, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import patch
sys.platform = "darwin"
builder = runpy.run_path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    wgpu = root / "wgpu"
    header = wgpu / "include" / "webgpu" / "webgpu.h"
    library = wgpu / "lib" / "libwgpu_native.dylib"
    sdl = root / "libSDL2-2.0.0.dylib"
    for path in (header, library, sdl):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    toolchain = types.SimpleNamespace(find=lambda name: sys.executable,
                                      macos_target_flags=lambda: ["--target=arm64-apple-macos13.3"],
                                      macos_sysroot_flags=lambda: ["-isysroot", "/MacOSX.sdk"])
    with patch.dict(os.environ, {"WGPU_NATIVE": str(wgpu), "HOOKSHOT_SDL2": str(sdl), "HOOKSHOT_FFMPEG": ""}), \
         patch("shutil.which", return_value="tool"), \
         patch("subprocess.run", side_effect=run), \
         patch("platform.machine", return_value="arm64"), \
         patch.dict(sys.modules, {"toolchain": toolchain}):
        builder["check_macos_toolchain"]()
    assert [c for c in commands if c[0] == "lipo"] == [
        ["lipo", str(sdl), "-verify_arch", "arm64"],
        ["lipo", str(library), "-verify_arch", "arm64"],
    ]
"""
        subprocess.run([sys.executable, "-I", "-c", script, str(source)], check=True,
                       capture_output=True, text=True)

    def test_macos_preflight_accepts_bundled_static_ffmpeg(self):
        source = ROOT / "nx2" / "make_game.py"
        script = r"""
import os, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import patch
sys.platform = "darwin"
builder = runpy.run_path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    wgpu = root / "wgpu"
    sdl = root / "libSDL2-2.0.0.dylib"
    ffmpeg = root / "ffmpeg"
    for path in (wgpu / "include/webgpu/webgpu.h", wgpu / "lib/libwgpu_native.dylib", sdl):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    headers = ("libavcodec/avcodec.h", "libswscale/swscale.h", "libswresample/swresample.h",
               "libavutil/avutil.h")
    libs = ("libavcodec.a", "libswscale.a", "libswresample.a", "libavutil.a")
    for relative in headers + libs:
        path = ffmpeg / ("include" if relative.endswith(".h") else "lib") / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    toolchain = types.SimpleNamespace(find=lambda name: sys.executable,
                                      macos_target_flags=lambda: ["--target=arm64-apple-macos13.3"],
                                      macos_sysroot_flags=lambda: ["-isysroot", "/MacOSX.sdk"])
    env = {"WGPU_NATIVE": str(wgpu), "HOOKSHOT_SDL2": str(sdl), "HOOKSHOT_FFMPEG": str(ffmpeg)}
    with patch.dict(os.environ, env), patch("shutil.which", return_value="tool"), \
         patch("subprocess.run", side_effect=run), patch("platform.machine", return_value="arm64"), \
         patch.dict(sys.modules, {"toolchain": toolchain}):
        builder["check_macos_toolchain"]()
    assert [c for c in commands if c[0] == "lipo"] == [
        ["lipo", str(sdl), "-verify_arch", "arm64"],
        ["lipo", str(wgpu / "lib/libwgpu_native.dylib"), "-verify_arch", "arm64"],
    ] + [["lipo", str(ffmpeg / "lib" / lib), "-verify_arch", "arm64"] for lib in libs]
    assert not any(c[0] == "pkg-config" for c in commands)
"""
        subprocess.run([sys.executable, "-I", "-c", script, str(source)], check=True,
                       capture_output=True, text=True)

    def test_macos_runtime_supplies_non_linux_profiler_entry_point(self):
        source = (ROOT / "host" / "posix" / "macos_stubs.c").read_text(encoding="utf-8")
        self.assertIn("void hos_prof_start(void)", source)

    def test_native_arm64_path_has_host_context_and_memory_ordering_support(self):
        context = (ROOT / "host" / "hos_setjmp.c").read_text(encoding="utf-8")
        lifter = (ROOT / "third_party" / "a64-kit" / "lift" / "suyu" / "arm64_to_c.h").read_text(encoding="utf-8")
        self.assertIn("#elif defined(__aarch64__) || defined(__arm64__)", context)
        self.assertIn("stp x19, x20", context)
        self.assertIn("__atomic_thread_fence(__ATOMIC_ACQUIRE)", lifter)
        self.assertIn("__atomic_thread_fence(__ATOMIC_RELEASE)", lifter)

    def test_macos_renderer_uses_sdl_pixel_dimensions(self):
        renderer = (ROOT / "gpu" / "render" / "rx_core.cpp").read_text(encoding="utf-8")
        window = (ROOT / "host" / "posix" / "display_sdl.c").read_text(encoding="utf-8")
        sdl = (ROOT / "host" / "hos_sdl.c").read_text(encoding="utf-8")
        self.assertIn("hos_posix_window_pixel_size(&width, &height)", renderer)
        self.assertIn("void hos_posix_window_pixel_size(int *width, int *height)", window)
        self.assertIn("window_pixel_size(window, width, height)", window)
        renderer_thread = window[window.index("static DWORD WINAPI renderer_main"):window.index("void hos_renderer_wait")]
        display_loop = window[window.index("void hos_display_loop"): ]
        self.assertNotIn("hos_sdl_metal_create_view", renderer_thread)
        self.assertLess(display_loop.index("hos_sdl_metal_create_view(window)"), display_loop.index("CreateThread"))
        self.assertIn('GET(Metal_CreateView, "SDL_Metal_CreateView")', sdl)
        self.assertIn("WIN_METAL", sdl)

    def test_linux_guest_memory_optimizations_are_guarded_from_macos(self):
        source = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
        self.assertIn("#ifdef __linux__\n    /* The window is a memfd's", source)
        self.assertIn("#ifdef __linux__\n                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE | MAP_NORESERVE,", source)
        self.assertIn("#ifdef __linux__\n    if (!getenv(\"HOS_THP\")) madvise", source)
        self.assertIn("#ifdef __linux__\n    return madvise(p, n, hos_guest_memfd >= 0 ? MADV_REMOVE", source)
        self.assertIn("    return 1;\n#endif\n}", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
