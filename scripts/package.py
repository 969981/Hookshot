"""(hookshot) A release folder that builds the game on the player's PC (nx2/module.py, launcher/hookshot.c).

    python3 -I scripts/package.py --target windows|linux [--runtime work/runtime-<target>] [--out dist/Hookshot-<target>]
                                  [--python <folder>] [--llvm <folder>]

What goes in it, and none of it is made from the game:

    hookshot[.exe]              the launcher (launcher/hookshot.c, built here for the target)
    hookshot_rt[.exe] + the Vulkan back end, SDL3.dll (Linux: libSDL3.so.0), wgpu-native: the runtime (build.py --part runtime)
    runtime.stamp               which runtime this is: the launcher rebuilds a game module made for another
    tools/link/                 what the module links against (hookshot_rt.lib or hookshot_rt.exports, the interface)
    tools/hookshot/             the build scripts (nx2/, host/ for the HLE set, a64-kit), with a64emit and zbic prebuilt
                                for the target, so the player's PC compiles no C++ and needs no headers
    tools/hookshot/work/        hot_funcs.txt, hot_order.txt, hookshot_pieces.prof, image/chunks.txt when work/ has them
                                (made from a profiled run: scripts/hotlist.py); without them the build is slower
    tools/python/, tools/llvm/  --python, --llvm: an interpreter with lz4, numpy, capstone and zstandard (python-build-standalone's
                                install_only builds are redistributable), and clang + lld + llvm-nm (an LLVM release).
                                Left out, the launcher uses the system's python3 and the clang on PATH.

The runtime folder comes from `python -I nx2/build.py --part runtime --target <t>`. Building the Windows launcher and
helpers from Linux takes the Windows SDK (scripts/setup-winsysroot.sh), as the runtime did.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "nx2"), os.path.join(ROOT, "third_party", "a64-kit", "tools")]
import module  # noqa: E402
import target  # noqa: E402
import toolchain  # noqa: E402

# the build scripts a player's PC runs (--part game): sources only, no game data
TOOLS = ["nx2", "host", "third_party/a64-kit/lift", "third_party/a64-kit/tools", "third_party/a64-kit/rt",
         "third_party/a64-kit/hos", "third_party/zbic", "LICENSE"]
SKIP = ("__pycache__", ".sha1", ".o", ".obj", ".pyc", "lld-link.exe")


def tracked(rel):
    """the files git has under rel, and those it would add (not ignored): what the repository holds, never work/ or a
    stray build"""
    r = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", rel],
                       capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split("\0") if f and not f.endswith(SKIP)]


def compile_for_target(out, srcs, extra, cxx=False):
    cc = toolchain.need(toolchain.find("clang++" if cxx else "clang"))
    subprocess.run([cc] + target.compile_flags() + target.link_flags() + ["-O2", "-Wno-everything", "-o", out] + srcs + extra,
                   check=True)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--target", choices=target.TARGETS, required=True)
    p.add_argument("--runtime")
    p.add_argument("--out")
    p.add_argument("--python", help="an interpreter folder with lz4, numpy, capstone and zstandard installed")
    p.add_argument("--llvm", help="an LLVM folder with bin/clang, lld and llvm-nm")
    a = p.parse_args()
    os.environ["RX_TARGET"] = a.target
    win = target.windows()
    rt = os.path.abspath(a.runtime or os.path.join(ROOT, "work", "runtime-" + a.target))
    out = os.path.abspath(a.out or os.path.join(ROOT, "dist", "Hookshot-" + a.target))
    need = [module.RUNTIME[a.target], module.STAMP, module.RUNTIME_LIB[a.target], "hookshot_game.interface"]
    missing = [n for n in need if not os.path.exists(os.path.join(rt, n))]
    if missing:
        raise SystemExit("%s lacks %s: build it with  python -I nx2/build.py --part runtime --target %s"
                         % (rt, ", ".join(missing), a.target))
    if os.path.exists(os.path.join(out, module.MODULE[a.target])):
        raise SystemExit("%s holds a game module: a release folder must not. Choose another --out" % out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)

    # the runtime and what it loads
    # (the libraries it loads, SDL3 and a shared FFmpeg among them (Linux: FFmpeg's lib*.so.<major>, libSDL3.so.0), and their licences: *-LICENSE.txt beside them)
    keep = [module.RUNTIME[a.target], module.STAMP, target.VK[a.target], "libwgpu_native.so",
            module.RUNTIME[a.target].replace(".exe", "") + ".map"]
    keep += [f for f in os.listdir(rt) if (f.lower().endswith(".dll") and f != module.MODULE["windows"]) or
             re.match(r"lib(avcodec|avutil|swscale|swresample|SDL3)\.so\.\d+$", f) or f.endswith("-LICENSE.txt")]
    for name in keep:
        if os.path.exists(os.path.join(rt, name)):
            shutil.copy2(os.path.join(rt, name), os.path.join(out, name))
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(out, "LICENSE.txt"))
    link = os.path.join(out, "tools", "link")
    os.makedirs(link)
    for name in [module.STAMP, module.RUNTIME_LIB[a.target], "hookshot_game.interface"]:
        shutil.copy2(os.path.join(rt, name), os.path.join(link, name))

    # the launcher
    launcher = os.path.join(out, "hookshot.exe" if win else "hookshot")
    icon = os.path.join(ROOT, "work", "hookshot.res")   # (nx2/make_icon.py: the game's icon on the launcher too)
    compile_for_target(launcher, [os.path.join(ROOT, "launcher", "hookshot.c")],
                       ["-luser32", "-lgdi32", "-lcomdlg32", "-lcomctl32", "-Wl,/subsystem:windows,/entry:mainCRTStartup"] +
                       ([icon] if os.path.exists(icon) else []) if win else ["-ldl"])

    # the build scripts, and their native helpers made for the target
    tools = os.path.join(out, "tools", "hookshot")
    for rel in TOOLS:
        for f in tracked(rel):
            dst = os.path.join(tools, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, f), dst)
    # what make_game.py takes from work/ when there (lists of addresses and counts from a profiled run, no game code):
    # the hot functions (code_hot.c), their link order, the PGO profile, where the code's files begin, the icon
    for rel in ("hot_funcs.txt", "hot_order.txt", "hookshot_pieces.prof", os.path.join("image", "chunks.txt")) + \
               (("hookshot.res",) if win else ()):
        src = os.path.join(ROOT, "work", rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(tools, "work", rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(tools, "work", rel))
            print("with work/%s" % rel)
    lift = os.path.join(tools, "third_party", "a64-kit", "lift")
    sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "lift"))
    import build_emit
    emit = os.path.join(lift, "a64emit.dll" if win else "liba64emit.so")
    compile_for_target(emit, [os.path.join(ROOT, "third_party", "a64-kit", "lift", "a64emit.cpp")],
                       ["-std=c++20", "-shared"] + ([] if win else ["-fPIC"]), cxx=True)
    import hashlib
    open(emit + ".sha1", "w").write(hashlib.sha1(b"".join(open(f, "rb").read() for f in build_emit.SRC)).hexdigest())
    zsrc = os.path.join(ROOT, "third_party", "zbic")
    compile_for_target(os.path.join(tools, "third_party", "zbic", "zbic.dll" if win else "zbic.so"),
                       [os.path.join(zsrc, "zstd.c"), os.path.join(zsrc, "zbic_api.c")],
                       ["-shared", "-DZSTD_ZBIC_SUPPORT=1", "-I", zsrc] + ([] if win else ["-fPIC"]))

    for name, src in (("python", a.python), ("llvm", a.llvm)):
        if src:
            shutil.copytree(src, os.path.join(out, "tools", name), symlinks=True)
    print("wrote %s%s" % (out, "" if a.python and a.llvm else
                          " (without %s: the launcher uses the system's)" % " and ".join(
                              n for n, s in (("tools/python", a.python), ("tools/llvm", a.llvm)) if not s)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
