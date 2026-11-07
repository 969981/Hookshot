"""(hookshot) The game's target: what nx2/build.py compiles the game for, apart from the host's (a64-kit's
toolchain.native_target: the tools that run during the build, zbic.so and the tests, always for the PC they run on).

RX_TARGET (or build.py / make_game.py --target):
  windows  the default: hookshot.exe + hookshot_vk.dll. On Windows as before; from a Linux PC a cross-build, with
           Microsoft's C library and Windows SDK in a /winsysroot-style folder (scripts/setup-winsysroot.sh, with xwin):
           RX_WINSYSROOT, or ~/.local/share/hookshot/winsysroot. Runs with Proton or Wine.
  linux    a native ELF: hookshot + libhookshot_vk.so, on host/linux (Win32 over POSIX, the window and audio on SDL2).
"""
import os
import shutil

import toolchain

TARGETS = ("windows", "linux")


def name():
    t = os.environ.get("RX_TARGET", "windows").lower()
    if t not in TARGETS:
        raise SystemExit("RX_TARGET=%s: one of %s" % (t, ", ".join(TARGETS)))
    return t


def windows():
    return name() == "windows"


def cross():
    """building for Windows from another OS"""
    return windows() and os.name != "nt"


def sdk():
    """whether the build uses the target's SDK: not the split build's game module (RX_NO_SDK, nx2/module.py), which
    brings its own C library headers"""
    return not os.environ.get("RX_NO_SDK")


def winsysroot():
    return os.environ.get("RX_WINSYSROOT") or os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser(os.path.join("~", ".local", "share")), "hookshot", "winsysroot")


def compile_flags():
    if not windows():   # (no stack protector, as on Windows: the lifted code's every frame would pay for it)
        return ["--target=x86_64-pc-linux-gnu", "-fno-stack-protector"]
    if not cross() or not sdk():
        return toolchain.native_target() if os.name == "nt" else \
            ["--target=x86_64-pc-windows-msvc", "-fms-extensions", "-D_CRT_SECURE_NO_WARNINGS"]
    # (the sysroot for the headers and, as the links run through the same driver, for the libraries)
    return ["--target=x86_64-pc-windows-msvc", "-fms-extensions", "-D_CRT_SECURE_NO_WARNINGS",
            "-Xmicrosoft-windows-sys-root", winsysroot()]


def link_flags():
    if windows() and not cross():
        return toolchain.native_link()
    return ["-fuse-ld=lld"]   # (lld-link for the MSVC target, ld.lld for Linux, from PATH)


def lld_link():
    """the COFF linker on its own (build.py --lib): emsdk's on Windows, the system's elsewhere"""
    if os.name == "nt":
        return toolchain.lld_link()
    return toolchain.need(shutil.which("lld-link") or os.path.join(os.path.dirname(toolchain.find("clang")), "lld-link"))


# what the game folder holds beside the game's data, per target
EXE = {"windows": "hookshot.exe", "linux": "hookshot"}
VK = {"windows": "hookshot_vk.dll", "linux": "libhookshot_vk.so"}
BESIDE = {"windows": ["hookshot.exe", "hookshot_vk.dll", "SDL2.dll", "wgpu_native.dll"],
          "linux": ["hookshot", "libhookshot_vk.so", "libwgpu_native.so"]}
BUILD_DIR = {"windows": "build", "linux": "build-linux"}   # (work/sa/<this>: the two targets' objects apart)
