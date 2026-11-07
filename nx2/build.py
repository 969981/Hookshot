"""Compile the lifted game (work/gen, from `lift.py --write`) into work/build/hookshot_game.dll (or a static library).

The flags are a64-kit's for lifted code (its build.py, cmd_native): -O1, LoopRotate's header copies capped (most of
the compile time in goto-dense giant functions otherwise), capture tracking past LLVM's 100 uses (the context
parameter is A64_NOESC), one section per function. The guest model is the 64-bit window (rtgen.py), shadowing the
vendored rt/, with the loop-header interrupt polls on (A64_POLLING, nx2/poll.py). Incremental: an object is rebuilt
only when its source or the flags changed.

hookshot_game.dll = the lifted chunks + a64-kit's tables and runtime + nx2/rt/eden_shim.c (the emulator ABI, mkw_host.h)
+ mkw_info.c (the image base and main's build id, generated here from work/image/image.json). Linking it is also the
check that the whole lifted game closes over itself: any unresolved symbol fails the link.

Heavy: 44 chunks of ~25 MB, ~100-200 s and ~2-3.5 GB each at -O1. The PC is shared with games: run it when the
machine is idle, and keep --jobs low otherwise.

--standalone builds work/sa/build/hookshot.exe instead: the standalone lift (work/sa/gen, `lift.py --standalone --write`)
+ the native runtime in host/ (TOTKPort's hos/, adapted). Objects are cached by their key in work/objcache, shared by
both builds: the two lifts share their chunk boundaries, so every chunk without a hooked function is the same source
and is not compiled twice.

Usage:  python -I nx2/build.py [--jobs 1] [--only 0,7] [--opt O1] [--no-polls] [--dll] [--lib] [--standalone] [--target windows]
                                [--part runtime|game [--runtime <folder>]]
  --only: just these code_NNN.c chunks (to measure); --dll: link hookshot_game.dll; --lib: archive hookshot_game.lib
  --target: windows (the default, also from a Linux PC: nx2/target.py) or linux (work/sa/build-linux/hookshot)
  --part: the split build (nx2/module.py): runtime, the game-independent executable (work/runtime-<target>), or game,
          the module made from the lift against that runtime (work/sa/game-<target>; --runtime: another runtime folder)
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # (python -I leaves the script folder off)
import kit
import module
import rtgen
import target
import toolchain

NX2_RT = os.path.join(kit.HERE, "rt")
HOST = os.path.join(kit.HERE, "..", "host")
# (hookshot) Linux: host/linux/ is on the include path (its windows.h: the Win32 that host/ and gpu/vk use) and its
# .c files take the place of the Windows-only ones
HOST_LINUX = os.path.join(HOST, "linux")
WINDOWS_ONLY = ("display.c", "mf_av1.c")


def host_sources():
    """(hookshot) host/*.c for the game's target"""
    srcs = [os.path.join(HOST, f) for f in os.listdir(HOST) if f.endswith(".c") and (f != "pgo_rt.c" or PGO == "gen")]
    if not target.windows():
        srcs = [s for s in srcs if os.path.basename(s) not in WINDOWS_ONLY] + \
               [os.path.join(HOST_LINUX, f) for f in os.listdir(HOST_LINUX) if f.endswith(".c")]
    return sorted(srcs)


def pkg_cflags(name):
    """(hookshot) a system library's compile flags (pkg-config), or None when it is not there"""
    try:
        r = subprocess.run(["pkg-config", "--cflags", name], capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout.split() if r.returncode == 0 else None
# (hookshot) Linux: an SDL3 build's prefix (scripts/fetch-deps.sh: work/deps/linux-sdl3), its libSDL3.so.0 bundled
# beside the runtime (rpath $ORIGIN) for systems that have no SDL3; unset, the system's
SDL3 = os.environ.get("RX_SDL3", "")


# (hookshot) Linux: LLVM's libc++ (scripts/fetch-deps.sh: work/deps/linux-libcxx), the renderer's C++ library, linked
# in: older systems' libstdc++ (Ubuntu 22.04's) has no <format>, and the release asks for none; unset, the system's
LIBCXX = os.environ.get("RX_LIBCXX", "")


def libcxx_cflags():
    return ["-nostdinc++", "-isystem", os.path.join(LIBCXX, "include", "c++", "v1"),
            "-isystem", os.path.join(LIBCXX, "include", "x86_64-unknown-linux-gnu", "c++", "v1")] if LIBCXX else []


def libcxx_libs():
    return ["-nostdlib++", os.path.join(LIBCXX, "lib", "libc++.a"), os.path.join(LIBCXX, "lib", "libc++abi.a"),
            "-lpthread"] if LIBCXX else []


def sdl3_cflags():
    return ["-I" + os.path.join(SDL3, "include")] if SDL3 else (pkg_cflags("sdl3") or [])
OPUS = os.path.join(kit.HERE, "..", "third_party", "opus")
OBJ_CACHE = os.path.join(kit.WORK, "objcache")
# (hookshot) work/hot_order.txt: the functions that run, hottest first (sampled), packed together by the linker; RX_NO_ORDER=1: off
PGO = os.environ.get("RX_PGO", "")



def no_throttle(handle=None):
    """(issue 87) Windows' power throttling (EcoQoS) slows a background window's processes to almost nothing on some machines: the
    build sat at 0% CPU. Turned off for this process, or the one whose handle this is"""
    if os.name != "nt":
        return
    import ctypes
    class State(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]
    k = ctypes.WinDLL("kernel32")
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    s = State(1, 1, 0)   # (PROCESS_POWER_THROTTLING_EXECUTION_SPEED in the mask, off in the state)
    try:
        k.SetProcessInformation(ctypes.c_void_p(int(handle)) if handle else k.GetCurrentProcess(), 4, ctypes.byref(s), ctypes.sizeof(s))
    except (OSError, ValueError, TypeError):
        pass

def HOT_ORDER():
    p = os.path.join(kit.WORK, "hot_order.txt")
    if not os.path.exists(p) or os.environ.get("RX_NO_ORDER"):
        return []
    if not target.windows():   # (hookshot) the same list, one symbol a line, as ld.lld reads it
        return ["-Wl,--symbol-ordering-file=" + p, "-Wl,--no-warn-symbol-ordering"]
    return ["-Wl,/order:@" + p.replace("\\", "/"), "-Wl,/ignore:4037"]


def HOST_LIBS():   # (hookshot) per target: nx2/target.py
    if not target.windows():   # (SDL3: the window, audio and controllers, host/linux)
        return (["-L" + os.path.join(SDL3, "lib"), "-Wl,-rpath,$ORIGIN"] if SDL3 else []) + \
               ["-lSDL3", "-lpthread", "-ldl", "-lm"]
    return ["-luser32", "-lwinmm", "-ladvapi32", "-lole32", "-lshell32", "-lgdi32", "-ldbghelp"]


def MAP(path):
    """(hookshot) the linker map beside a binary (crash offsets -> f_<guest address>), per target"""
    return "-Wl,/map:" + path if target.windows() else "-Wl,-Map=" + path


GPU = os.path.join(kit.HERE, "..", "gpu")
# FFmpeg (libavcodec's VP8/VP9 decoders, swscale) for nn::codec's video decoders (host/nn_video.c): the static build
# in the Eglantine clone's package cache
FFMPEG = []     # (found in the CPM caches below, once cpm_dirs is defined)
# the renderer's WebGPU: a wgpu-native release (include/, lib/); TOTKPort's build of it by default
WGPU = os.environ.get("WGPU_NATIVE", os.path.join(os.path.expanduser("~"), "Desktop", "TOTKPort", "third_party",
                                                  "wgpu-native"))


def gpu_sources():
    """The renderer (gpu/render, TOTKPort's, NVN replayed with WebGPU) and what it builds on, from suyu
    (gpu/third_party/suyu): the shader recompiler (Maxwell frontend, IR passes, the WGSL backend; the Ampere
    lowering is header-only), surface formats, texture decoders (block linear, ASTC)."""
    suyu = os.path.join(GPU, "third_party", "suyu")
    out = []
    for d, dirs, files in os.walk(os.path.join(suyu, "shader_recompiler")):
        dirs[:] = [x for x in dirs if x != "glsl"]   # (the GLSL backend: TOTKPort's shader tests' reference only)
        out += [os.path.join(d, f) for f in files if f.endswith(".cpp") and f not in ("attribute.cpp", "flow_test.cpp")]
    vc = os.path.join(suyu, "video_core")
    out += [os.path.join(vc, *f.split("/")) for f in ("surface.cpp", "texture_cache/format_lookup_table.cpp",
                                                      "textures/astc.cpp", "textures/decoders.cpp")]
    render = os.path.join(GPU, "render")
    out += [os.path.join(render, f) for f in os.listdir(render) if f.endswith(".cpp")]
    return sorted(out)


def tree_headers(*roots, skip=()):
    """Every header under these folders, as one dependency blob (suyu's headers sit in many folders); folders named
    in skip are left out"""
    h = hashlib.sha1()
    for root in roots:
        found = []
        # (the walk pruned as it goes: sorting the whole walk first, as this once did, made skip do nothing, and every
        # gpu/vk header change rebuilt the WebGPU renderer)
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in skip)
            found += [os.path.join(d, f) for f in files if f.endswith((".h", ".hpp", ".inc"))]
        for path in sorted(found):
            h.update(os.path.basename(path).encode() + open(path, "rb").read())
    return h.hexdigest().encode()


def vk_sources():
    """The Vulkan back end (hookshot_vk.dll, gpu/vk/vk_api.h): Eden's shader recompiler with its SPIR-V back end
    (gpu/vk/eden), sirit with Eden's mesh shader patch (gpu/vk/sirit) and the back end itself (gpu/vk/*.cpp)"""
    vk = os.path.join(GPU, "vk")
    out = []
    for sub in ("eden", os.path.join("sirit", "src")):
        for d, _, files in os.walk(os.path.join(vk, sub)):
            out += [os.path.join(d, f) for f in files if f.endswith(".cpp")]
    out += [os.path.join(vk, f) for f in os.listdir(vk) if f.endswith(".cpp")]
    vc = os.path.join(GPU, "third_party", "suyu", "video_core")
    out += [os.path.join(vc, "textures", f) for f in ("decoders.cpp", "astc.cpp")]   # (the WebGPU back end's unswizzle)
    return sorted(out)


def cpm_dirs():
    """Eden's CPM package caches, read only: RX_CPM, this project's work/eg clone, mkw-recomp's, the shared
    Eglantine checkout's (Vulkan and SPIR-V headers, VMA, FFmpeg)"""
    eden_ns2 = os.path.dirname(kit.ROOT)
    return [d for d in (os.environ.get("RX_CPM"), os.path.join(kit.WORK, "eg", ".cache", "cpm"),
                        os.path.join(eden_ns2, "mkw-recomp", "work", "eg", ".cache", "cpm"),
                        os.path.join(eden_ns2, "Eglantine", ".cache", "cpm")) if d and os.path.isdir(d)]


def cpm_package(name):
    """The newest version of a package in the first CPM cache that has it (cpm_dirs), or None"""
    for root in cpm_dirs():
        d = os.path.join(root, name)
        if os.path.isdir(d) and os.listdir(d):
            return os.path.join(d, sorted(os.listdir(d))[-1])
    return None


def cache_put(obj, key):
    """Hard-link a built object into the cache under its key (a copy across volumes)"""
    os.makedirs(OBJ_CACHE, exist_ok=True)
    dst = os.path.join(OBJ_CACHE, key + ".o")
    if os.path.exists(dst):
        return
    try:
        os.link(obj, dst)
    except OSError:
        import shutil
        shutil.copyfile(obj, dst)


FFMPEG = [os.path.join(d, n) for d in [os.path.join(r, "ffmpeg") for r in cpm_dirs()] if os.path.isdir(d)
          for n in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, n, "lib"))]


def ffmpeg_dir():
    """(hookshot) the CPM FFmpeg build of the game's target's kind (avcodec.lib; libavcodec.so, BtbN's linux64-lgpl-shared,
    or libavcodec.a), else None"""
    libs = ("avcodec.lib",) if target.windows() else ("libavcodec.so", "libavcodec.a")
    return next((d for lib in libs for d in FFMPEG if os.path.exists(os.path.join(d, "lib", lib))), None)


def ffmpeg_libs():
    """(hookshot) FFmpeg's libraries for the game's target: the CPM build's (ffmpeg_dir), else for Linux the system's"""
    names = ("avcodec", "swscale", "swresample", "avutil")
    d = ffmpeg_dir()
    if target.windows():
        return [os.path.join(d, "lib", l + ".lib") for l in names] + \
               ["-lbcrypt", "-lmfplat", "-lmfuuid", "-lstrmiids", "-lole32"] if d else []
    if d and os.path.exists(os.path.join(d, "lib", "libavcodec.so")):   # (shared: the .so files beside the runtime, rpath $ORIGIN)
        return ["-L" + os.path.join(d, "lib")] + ["-l" + l for l in names]
    return [os.path.join(d, "lib", "lib%s.a" % l) for l in names] if d else ["-l" + l for l in names]


# the soft write watch (rt/a64.h A64_MARK): lifted stores mark their 4 KB page, and the renderer reads the marks
# instead of Windows' write watch (GetWriteWatch over the 6 GB MKW's GPU sees took ~20 ms a scan, in one thread or
# four: the race's replay waited ~12 ms a frame for it). RX_SOFT_WRITE_WATCH=0: Windows' write watch (a full rebuild)
WRITE_TRACK = [] if os.environ.get("RX_SOFT_WRITE_WATCH") == "0" else ["-DA64_WRITE_TRACK"]

# (hookshot) the host CPU the lifted game and the runtime are built for: x86-64-v3 (AVX2, FMA, BMI1/2, LZCNT,
# MOVBE; Haswell and Zen 1 on). With FMA the FPX1 fast paths cover double-precision FMA (RECOMP_FPX_FMA64; it took
# the exact software body on baseline x86-64) and the bit counts are single instructions. No contraction, so a C
# expression is rounded as written (the exact bodies and FPX1's proofs assume it). RX_MARCH=x86-64: the old baseline
CPU = ["-march=" + os.environ.get("RX_MARCH", "x86-64-v3"), "-ffp-contract=off"]


def flags(gen, rt64, opt, polls):
    return target.compile_flags() + CPU + [
        "-I" + rt64, "-I" + gen, "-I" + os.path.join(kit.KIT, "rt"), "-I" + NX2_RT, "-Wno-everything",
        "-fno-strict-aliasing", "-mcx16", "-" + opt, "-g0", "-mllvm", "-rotation-max-header-size=0",
        "-ffunction-sections", "-mllvm", "-capture-tracking-max-uses-to-explore=5000"] + \
        (["-DA64_POLLING"] if polls else []) + WRITE_TRACK


def write_info(out):
    meta = json.load(open(os.path.join(kit.WORK, "image", "image.json")))
    mods = {m["name"]: m for m in meta["modules"]}
    path = os.path.join(out, "mkw_info.c")
    text = ('/* Generated by nx2/build.py from work/image/image.json. */\n#include <stdint.h>\n'
            'const uint64_t mkw_image_base = 0x%xULL;\nconst char mkw_main_build_id[] = "%s";\n'
            % (meta["base"], mods["main"]["build_id"]))
    if not os.path.exists(path) or open(path).read() != text:
        open(path, "w", newline="\n").write(text)
    return path


def object_key(src, fl, deps):
    """What an object depends on: its source, the flags without the include folders (the two builds' generated
    headers are in different folders; their contents are in deps), and the headers"""
    return hashlib.sha1(open(src, "rb").read() + " ".join(f for f in fl if not f.startswith("-I")).encode() +
                        deps).hexdigest()


def migrate_stamps(gen, obj_dir, fl, deps):
    """Objects stamped with the old key (flags with include folders) are restamped with object_key and cached, so
    a build from before the cache does not have to be compiled again"""
    if not os.path.isdir(obj_dir):
        return
    if not os.path.isdir(gen) or not os.path.isdir(obj_dir):   # (a first build in a fresh folder)
        return
    for f in os.listdir(gen):
        if not (f.startswith("code_") and f.endswith(".c")):
            continue
        obj = os.path.join(obj_dir, f[:-2] + ".o")
        stamp = obj + ".sha1"
        if not (os.path.exists(obj) and os.path.exists(stamp)):
            continue
        src = os.path.join(gen, f)
        content = open(src, "rb").read()
        if open(stamp).read() == hashlib.sha1(content + " ".join(fl).encode() + deps).hexdigest():
            key = object_key(src, fl, deps)
            open(stamp, "w").write(key)
            cache_put(obj, key)


def compile_one(job):
    cc, src, obj, fl, deps = job
    key = object_key(src, fl, deps)
    stamp = obj + ".sha1"
    if os.path.exists(obj) and os.path.exists(stamp) and open(stamp).read() == key:
        return src, 0.0, None
    cached = os.path.join(OBJ_CACHE, key + ".o")
    if os.path.exists(cached):   # (the same source and flags built for the other target)
        if os.path.exists(obj):
            os.remove(obj)
        try:
            os.link(cached, obj)
        except OSError:
            import shutil
            shutil.copyfile(cached, obj)
        open(stamp, "w").write(key)
        return src, 0.0, None
    t = time.time()
    with subprocess.Popen([cc] + fl + ["-c", src, "-o", obj], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as p:
        no_throttle(getattr(p, "_handle", None))
        out, err = p.communicate()
    r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
    if r.returncode:
        return src, time.time() - t, r.stderr[-4000:]
    open(stamp, "w").write(key)
    cache_put(obj, key)
    return src, time.time() - t, None


def main():
    kit.wait_for_quiet('the compile')
    p = argparse.ArgumentParser()
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--only", default="")
    p.add_argument("--opt", default="O1")
    p.add_argument("--no-polls", action="store_true")
    p.add_argument("--dll", action="store_true")
    p.add_argument("--lib", action="store_true")
    p.add_argument("--standalone", action="store_true")
    p.add_argument("--target", choices=target.TARGETS,   # (hookshot) nx2/target.py
                   default=os.environ.get("RX_TARGET", "windows").lower(),
                   help="what the game is built for (RX_TARGET): windows (the default; from Linux a cross-build) or linux")
    p.add_argument("--part", choices=module.PARTS, help="(hookshot) the split build's half: nx2/module.py")
    p.add_argument("--runtime", help="(hookshot) --part game: the runtime folder to build against")
    a = p.parse_args()
    a.standalone = a.standalone or bool(a.part)
    os.environ["RX_TARGET"] = a.target
    if a.part == "game":   # (hookshot) the module needs no SDK: nx2/module.py
        os.environ["RX_NO_SDK"] = "1"
    if target.cross() and target.sdk() and not os.path.isdir(target.winsysroot()):
        raise SystemExit("no Windows SDK at %s (RX_WINSYSROOT): scripts/setup-winsysroot.sh makes it" % target.winsysroot())
    build_dir = target.BUILD_DIR[a.target]
    sa = os.path.join(kit.WORK, "sa")
    gen = os.path.join(sa if a.standalone else kit.WORK, "gen")
    out = os.path.join(sa if a.standalone else kit.WORK, build_dir)
    if a.part:   # (hookshot) the split build's folders: nx2/module.py
        out = os.path.join(kit.WORK, "runtime-" + a.target) if a.part == "runtime" else os.path.join(sa, "game-" + a.target)
        a.runtime = os.path.abspath(a.runtime or os.path.join(kit.WORK, "runtime-" + a.target))
    obj_dir = os.path.join(out, "obj")
    os.makedirs(obj_dir, exist_ok=True)
    rt64 = rtgen.write(os.path.join(out, "rt64"), a.part, HOST)
    if a.part == "runtime":   # (no lift: what host/ includes from one, and the module's runtime, for its interface)
        gen = module.runtime_includes(out)
        rt_game = rtgen.write(os.path.join(out, "rt64-game"), "game", HOST)
    cc = toolchain.need(toolchain.find("clang"))
    fl = flags(gen, rt64, a.opt, not a.no_polls) + module.compile_flags(a.part if a.part == "game" else None)
    # (the lifted chunks include only the guest model: the emulator ABI header is a dependency of the shim alone)
    deps = b"".join(open(os.path.join(d, f), "rb").read() for d in (gen, rt64) for f in sorted(os.listdir(d))
                    if f.endswith(".h"))
    shim_deps = deps + open(os.path.join(NX2_RT, "mkw_host.h"), "rb").read()
    eden_gen, eden_out = os.path.join(kit.WORK, "gen"), os.path.join(kit.WORK, build_dir)
    if not a.part:
        migrate_stamps(eden_gen, os.path.join(eden_out, "obj"),
                       flags(eden_gen, os.path.join(eden_out, "rt64"), a.opt, not a.no_polls), deps)
        migrate_stamps(gen, obj_dir, fl, deps)
    chunks = sorted(f for f in os.listdir(gen) if f.startswith("code_") and f.endswith(".c"))
    if a.only:
        want = {"code_%03d.c" % int(n) for n in a.only.split(",")}
        chunks = [c for c in chunks if c in want]
    srcs = [os.path.join(gen, c) for c in chunks]
    host_srcs = []
    if not a.only:
        srcs += [os.path.join(gen, f) for f in ("a64_table.c", "a64_hooks.c", "a64_suyu.c")] + \
                [os.path.join(rt64, "a64_rt.c")]
        if a.standalone:
            srcs += [os.path.join(gen, f) for f in ("a64_stubs.c", "hos_image.c", "nvn2_exports.c")]
            host_srcs = host_sources()
        else:
            srcs += [os.path.join(NX2_RT, "eden_shim.c"), write_info(out)]
        if a.part == "runtime":   # (hookshot) the runtime alone; the module's runtime only for its symbols
            srcs = [os.path.join(rt_game, "a64_rt.c"), os.path.join(gen, "a64_suyu.c")]
        elif a.part == "game":   # (hookshot) the game alone, with its C library's leftovers on Windows
            host_srcs = []
            if target.windows():
                srcs.append(module.MODULE_CRT)
    # biggest first, so the long ones do not end up alone at the end
    srcs.sort(key=lambda s: -os.path.getsize(s))
    # (hookshot) code_hot.c, the lift's hot functions together (A64_HOT_FUNCS + A64_HOT_CHUNK=1): at RX_HOT_OPT
    # (default O2), so the hot callees inline into their hot callers
    hot_fl = [("-" + os.environ.get("RX_HOT_OPT", "O2")) if f == "-" + a.opt else f for f in fl]
    # (hookshot) RX_PGO=gen: code_hot.c counts its branches (host/pgo_rt.c writes them; -O1 builds much sooner, and the counters are
    # placed before the optimiser, so a profile from it fits the -O2 build). RX_PGO=<merged .prof>: built with that profile
    if PGO == "gen":
        hot_fl = [f if f != "-O2" else "-O1" for f in hot_fl] + ["-fprofile-instr-generate", "-mllvm", "-disable-vp=true"]
    elif PGO:
        hot_fl = hot_fl + ["-fprofile-instr-use=" + PGO]
    jobs = [(cc, s, os.path.join(obj_dir, os.path.basename(s)[:-2] + ".o"),
             hot_fl if os.path.basename(s).startswith("code_hot") else fl,
             shim_deps if os.path.dirname(s) == NX2_RT else deps) for s in srcs]
    if a.part == "runtime":   # (built as the module builds them: only their names are used)
        jobs = [(tool, src, obj, f + module.compile_flags("game"), dep) for tool, src, obj, f, dep in jobs]
    rt_objs = [j[2] for j in jobs] if a.part == "runtime" else []
    split = module.compile_flags("runtime") if a.part == "runtime" else []
    # the runtime: ordinary C at -O2, with its own headers (any host/*.h change rebuilds all of it)
    host_fl = target.compile_flags() + CPU + ["-I" + HOST, "-I" + rt64, "-I" + gen, "-Wno-everything", "-O2", "-g0",
                                           "-mcx16", "-DA64_POLLING"] + WRITE_TRACK + split
    host_deps = deps + b"".join(open(os.path.join(HOST, f), "rb").read() for f in sorted(os.listdir(HOST))
                                if f.endswith(".h"))
    if not target.windows():   # (hookshot) host/linux: its windows.h first, SDL3's headers (the system's)
        host_fl = ["-I" + HOST_LINUX] + host_fl + ["-D_GNU_SOURCE"] + sdl3_cflags()
        host_deps += b"".join(open(os.path.join(HOST_LINUX, f), "rb").read() for f in sorted(os.listdir(HOST_LINUX))
                              if f.endswith(".h"))
        for fl in sdl3_cflags() or ["-I/usr/include"]:   # (and SDL's version: its headers are not ours either)
            ver = os.path.join(fl[2:], "SDL3", "SDL_version.h") if fl.startswith("-I") else ""
            if os.path.exists(ver):
                host_deps += open(ver, "rb").read()
                break
    # (hookshot) the FFmpeg the host is compiled against, in its objects' keys: its headers are not ours, and an object
    # built against the system's (another major version, other codec numbers and struct layouts) was taken from the
    # cache for the bundled one: the AV1 movies opened as another codec, and the heap was written over
    for inc in ([os.path.join(ffmpeg_dir(), "include")] if ffmpeg_dir() else []) + ["/usr/include"]:
        ver = os.path.join(inc, "libavcodec", "version_major.h")
        if os.path.exists(ver):
            host_deps += open(ver, "rb").read() + open(os.path.join(inc, "libavcodec", "version.h"), "rb").read()
            break
    jobs += [(cc, s, os.path.join(obj_dir, "host_" + os.path.basename(s)[:-2] + ".o"),
              host_fl + ["-I" + os.path.join(OPUS, "include")] +
              (["-I" + os.path.join(ffmpeg_dir(), "include")] if ffmpeg_dir() else []), host_deps) for s in host_srcs]
    # libopus 1.5.2 (third_party/opus, from TOTKPort): nn::codec's hardware Opus decoder (host/nn_codec.c)
    if host_srcs:
        oflags = target.compile_flags() + ["-O2", "-g0", "-Wno-everything", "-DOPUS_BUILD", "-DUSE_ALLOCA",
                                              "-DHAVE_LRINTF", "-DHAVE_LRINT"] +             ["-I" + os.path.join(OPUS, d) for d in ("include", "celt", "silk", "silk/float", "src")]
        for rel in open(os.path.join(OPUS, "sources.txt")).read().split():
            jobs.append((cc, os.path.join(OPUS, rel), os.path.join(obj_dir, "opus_" + rel.replace("/", "_")[:-2] + ".o"),
                         oflags, b"opus-1.5.2"))
    # the renderer (C++20, NDEBUG: no asserts in it or suyu), when a wgpu-native release is there
    wgpu_lib = os.path.join(WGPU, "lib", "wgpu_native.dll.lib" if target.windows() else "libwgpu_native.so")
    wgpu = a.standalone and not a.only and a.part != "game" and os.path.isfile(os.path.join(WGPU, "include", "webgpu", "webgpu.h")) and \
        os.path.isfile(wgpu_lib)
    if a.standalone and not a.only and a.part != "game" and not wgpu:
        print("no wgpu-native at %s (WGPU_NATIVE): building without the renderer" % WGPU)
        # (hookshot) its entry points answer "none": Vulkan is then the only renderer (gpu/render/rx_no_webgpu.c)
        stub = os.path.join(GPU, "render", "rx_no_webgpu.c")
        jobs.append((cc, stub, os.path.join(obj_dir, "gpu_rx_no_webgpu.o"), host_fl + ["-I" + os.path.join(GPU, "render")],
                     host_deps + open(stub, "rb").read()))
    if wgpu:
        cxx = toolchain.need(toolchain.find("clang++"))
        gflags = target.compile_flags() + ["-std=c++20", "-O2", "-g0", "-DNDEBUG", "-Wno-everything",
                                              "-I" + os.path.join(GPU, "compat"),
                                              "-I" + os.path.join(GPU, "third_party", "suyu"),
                                              "-I" + os.path.join(WGPU, "include"), "-I" + HOST, "-I" + rt64] + WRITE_TRACK + split +                  ["-include", os.path.join(GPU, "render", "rx_wgpu_names.h")]   # (rx_* -> wgr_*: host/rx_backend.c)
        gdeps = tree_headers(GPU, HOST, skip=("vk",)) + open(os.path.join(WGPU, "include", "webgpu", "webgpu.h"), "rb").read()
        for src in gpu_sources():
            name = os.path.relpath(src, GPU).replace(os.sep, "_")[:-4]
            jobs.append((cxx, src, os.path.join(obj_dir, "gpu_" + name + ".o"), gflags, gdeps))
    # the Vulkan back end, a DLL of its own (RX_BACKEND=vulkan): built when the Vulkan headers are in the CPM cache
    vk_jobs = []
    vk_headers, spirv_headers = cpm_package("vulkanheaders"), cpm_package("spirv-headers")
    if a.standalone and not a.only and a.part != "game" and vk_headers and spirv_headers:
        cxx = toolchain.need(toolchain.find("clang++"))
        vk = os.path.join(GPU, "vk")
        # (what the shader caches are good for: what translates shaders, not the whole DLL, so a build that changes something else
        # keeps them)
        stamp = hashlib.sha1()
        translator = [os.path.join(d, f) for top in ("eden", "sirit", "compat") for d, _, fs in os.walk(os.path.join(vk, top)) for f in fs]
        for f in sorted(translator) + [os.path.join(vk, n) for n in ("vk_shader.cpp", "vk_state.h", "vk_api.h")]:
            with open(f, "rb") as src:
                stamp.update(os.path.relpath(f, vk).replace("\\", "/").encode() + src.read().replace(bytes((13, 10)), bytes((10,))))
        # (hookshot) Linux: host/linux's windows.h, FreeType's headers for the Insert menu's text (gpu/vk/vk_gdi_linux.h)
        linux_vk = [] if target.windows() else ["-fPIC", "-I" + HOST_LINUX] + (pkg_cflags("freetype2") or ["-DVK_GDI_NO_FREETYPE"]) + \
            libcxx_cflags()
        vflags = target.compile_flags() + CPU + linux_vk + [   # (hookshot: the replay is the frame's limit now)
            "-DHOOKSHOT_SHADER_STAMP=0x%sull" % stamp.hexdigest()[:16],
            # (what the code takes for granted, which newer C++ libraries bring in by themselves and older ones do not: issue 48)
            "-include", "algorithm", "-include", "cstdio", "-include", "cstdlib", "-include", "cstring", "-include", "functional",
            "-include", "string", "-include", "vector",
            "-std=c++20", "-O2", "-g0", "-DNDEBUG", "-Wno-everything", "-DVK_NO_PROTOTYPES", "-DMKWVK_BUILDING",
            *(["-DHOOKSHOT_RELEASE"] if os.environ.get("RX_RELEASE") == "1" else []),   # (the shaders precompiled before the game)
            # (Eden's recompiler before suyu's root: suyu has its own shader_recompiler/ headers, and a Shader::Info
            # of the other layout in some objects is what crashed the descriptor walk. MKWVK_INCLUDES changes the
            # object keys, which leave the include folders out, when this order changes)
            "-DMKWVK_INCLUDES=2",
            "-I" + os.path.join(vk, "compat"), "-I" + os.path.join(GPU, "compat"), "-I" + os.path.join(vk, "eden"),
            "-I" + os.path.join(GPU, "third_party", "suyu"),
            "-I" + os.path.join(vk, "sirit", "include"), "-I" + os.path.join(vk, "sirit", "src"),
            "-I" + os.path.join(spirv_headers, "include"), "-I" + os.path.join(spirv_headers, "include", "spirv", "unified1"),
            "-I" + os.path.join(vk_headers, "include"), "-I" + vk] +             (["-I" + os.path.join(cpm_package("vulkanmemoryallocator"), "include")] if cpm_package("vulkanmemoryallocator") else [])
        # (Eden's recompiler and sirit see only their own headers: a change to the back end's vk_*.h does not
        # rebuild their ~500 files; the back end itself depends on everything, suyu's texture decoders included)
        vk_lib_deps = tree_headers(os.path.join(vk, "eden"), os.path.join(vk, "sirit"), os.path.join(vk, "compat"),
                                   os.path.join(GPU, "compat"))
        vdeps = tree_headers(vk, os.path.join(GPU, "render"), os.path.join(GPU, "compat"),
                             os.path.join(GPU, "third_party", "suyu", "video_core", "textures")) + \
            (b"" if target.windows() else tree_headers(HOST_LINUX))
        for src in vk_sources():
            name = os.path.relpath(src, vk).replace(os.sep, "_")[:-4]
            lib = os.path.relpath(src, vk).split(os.sep)[0] in ("eden", "sirit")
            vk_jobs.append((cxx, src, os.path.join(obj_dir, "vk_" + name + ".o"), vflags, vk_lib_deps if lib else vdeps))
        jobs += vk_jobs
    vk_only = bool(os.environ.get("RX_VK_ONLY"))   # (the renderer alone, the game's objects as they are)
    if vk_only:
        jobs = list(vk_jobs)
    print("%d files, %d jobs, %s" % (len(jobs), a.jobs, " ".join(f for f in fl if f.startswith("-D") or f.startswith("-O"))),
          flush=True)
    t0, failed = time.time(), 0
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        # (as each ends, not in order: the builder app shows progress from these lines, and the biggest file comes first)
        for src, secs, err in (f.result() for f in concurrent.futures.as_completed([ex.submit(compile_one, j) for j in jobs])):
            size = os.path.getsize(src) >> 20
            if err:
                failed += 1
                print("FAILED %s (%d MB, %.0fs):\n%s" % (os.path.basename(src), size, secs, err), flush=True)
            else:
                print("  %-16s %4d MB  %6.0fs  (%.0fs)" % (os.path.basename(src), size, secs, time.time() - t0), flush=True)
    print("compiled in %.0fs, %d failed" % (time.time() - t0, failed), flush=True)
    if failed:
        return 1
    vk_objs = {j[2] for j in vk_jobs}
    objs = [j[2] for j in jobs if j[2] not in vk_objs and j[2] not in rt_objs]
    if a.part == "game":   # (hookshot) nx2/module.py
        return 0 if module.link_game(cc, objs, out, a.runtime) else 1
    rsp = os.path.join(out, "objs.rsp")
    open(rsp, "w").write("\n".join('"%s"' % o.replace("\\", "/") for o in objs))
    if a.lib:
        lib = os.path.join(out, "hookshot_game.lib" if target.windows() else "libhookshot_game.a")
        subprocess.run([target.lld_link(), "/lib", "/out:" + lib, "@" + rsp] if target.windows() else
                       [toolchain.need(toolchain.find("llvm-ar")), "rcs", lib, "@" + rsp], check=True)
        print("wrote %s (%d objects, %d MB)" % (lib, len(objs), os.path.getsize(lib) >> 20))
    if a.standalone and not a.only and not vk_only:
        exe = os.path.join(out, (module.RUNTIME if a.part else target.EXE)[a.target])   # (+ hookshot.map: crash offsets -> f_<guest address>)
        win = target.windows()
        t = time.time()
        r = subprocess.run([cc] + target.compile_flags() + target.link_flags() +
                           ["-o", exe, MAP(os.path.splitext(exe)[0] + ".map"), "@" + rsp] + HOT_ORDER() + HOST_LIBS() +
                           (["-Wl,/stack:8388608"] if win else []) +   # (Linux: the main thread's stack is ulimit -s)
                           (["-Wl,/nodefaultlib:clang_rt.profile.lib"] if PGO == "gen" and win else []) +
                           ([os.path.join(kit.WORK, "hookshot.res")] if win and os.path.exists(os.path.join(kit.WORK, "hookshot.res")) else []) +   # (nx2/make_icon.py)
                           (["-Wl,/subsystem:windows,/entry:mainCRTStartup"] if win and os.environ.get("RX_RELEASE") == "1" else []) +   # (no console window)
                           ([wgpu_lib] + ([] if win else ["-Wl,-rpath,$ORIGIN"]) if wgpu else []) +
                           ffmpeg_libs() +
                           (module.runtime_link_args(out, [o for o in objs if "host_" in os.path.basename(o)], rt_objs, cc)
                            if a.part == "runtime" else []),
                           capture_output=True, text=True)
        undefined = sorted({l.strip() for l in (r.stdout + r.stderr).splitlines() if "undefined symbol" in l})
        if r.returncode:
            print("link FAILED (%.0fs), %d undefined symbols" % (time.time() - t, len(undefined)))
            print("\n".join(undefined[:60]) if undefined else (r.stdout + r.stderr)[-3000:])
            return 1
        print("linked %s: %d MB (%.0fs)" % (exe, os.path.getsize(exe) >> 20, time.time() - t))
        if a.part == "runtime" and not win:
            module.write_runtime_exports(exe, out)
        if wgpu:   # (wgpu_native.dll beside it; in use while the game runs)
            import shutil
            so = "wgpu_native.dll" if win else "libwgpu_native.so"
            dll, dst = os.path.join(WGPU, "lib", so), os.path.join(out, so)
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(dll):
                shutil.copyfile(dll, dst)
        # (hookshot) SDL3.dll beside it: host/pad_sdl.c's controllers (DualSense, DualShock 4, Switch Pro ...). Linux: the
        # window, audio and controllers, RX_SDL3's libSDL3.so.0 when given (else the system's). Its licence as SDL3-LICENSE.txt
        sdl = os.path.join(kit.HERE, "..", "third_party", "sdl3", "SDL3.dll") if win else \
            os.path.join(SDL3, "lib", "libSDL3.so.0") if SDL3 else ""
        sdl_dst = os.path.join(out, os.path.basename(sdl))
        if sdl and os.path.exists(sdl):
            import shutil
            if not os.path.exists(sdl_dst) or os.path.getsize(sdl_dst) != os.path.getsize(sdl):
                shutil.copyfile(sdl, sdl_dst)   # (the file a .so.0 link names)
            shutil.copyfile(os.path.join(kit.HERE, "..", "third_party", "sdl3", "LICENSE.txt"),
                            os.path.join(out, "SDL3-LICENSE.txt"))
        # (hookshot) a shared FFmpeg build (import libraries in lib/, its DLLs in bin/): those DLLs beside it too
        # (Linux: its lib<name>.so.<major> files, the names the runtime asks for) and its licence as FFmpeg-LICENSE.txt
        fbin = os.path.join(ffmpeg_dir(), "bin" if win else "lib") if ffmpeg_dir() else None
        if fbin and os.path.isdir(fbin):
            import shutil
            for f in os.listdir(fbin):
                if win and f.lower().endswith(".dll") and f.split("-")[0].lower() in ("avcodec", "avutil", "swscale", "swresample"):
                    shutil.copyfile(os.path.join(fbin, f), os.path.join(out, f))
                elif not win and re.match(r"lib(avcodec|avutil|swscale|swresample)\.so\.\d+$", f):
                    shutil.copyfile(os.path.join(fbin, f), os.path.join(out, f))
            lic = os.path.join(ffmpeg_dir(), "LICENSE.txt")
            if os.path.exists(lic):
                shutil.copyfile(lic, os.path.join(out, "FFmpeg-LICENSE.txt"))
    if vk_jobs:   # (hookshot_vk.dll beside the exe: loaded by host/rx_backend.c with RX_BACKEND=vulkan)
        vdll = os.path.join(out, target.VK[a.target])
        vrsp = os.path.join(out, "vk_objs.rsp")
        open(vrsp, "w").write("\n".join('"%s"' % j[2].replace("\\", "/") for j in vk_jobs))
        t = time.time()
        r = subprocess.run([cc] + target.compile_flags() + target.link_flags() +
                           ["-shared", "-o", vdll, MAP(os.path.splitext(vdll)[0] + ".map"), "@" + vrsp] +
                           (["-luser32", "-lgdi32"] if target.windows() else
                            # (libc++'s symbols kept inside: a driver's libstdc++ in the same process stays its own)
                            (libcxx_libs() + ["-Wl,--exclude-libs,ALL"] if LIBCXX else ["-lstdc++"]) + ["-lpthread", "-ldl", "-lm"]),
                           capture_output=True, text=True)
        if r.returncode:
            undefined = sorted({l.strip() for l in (r.stdout + r.stderr).splitlines() if "undefined symbol" in l})
            print("%s link FAILED, %d undefined symbols" % (os.path.basename(vdll), len(undefined)))
            print("\n".join(undefined[:60]) if undefined else (r.stdout + r.stderr)[-3000:])
            return 1
        print("linked %s: %d MB (%.0fs)" % (vdll, os.path.getsize(vdll) >> 20, time.time() - t))
    if a.dll and not a.only:
        dll = os.path.join(out, "hookshot_game.dll" if target.windows() else "libhookshot_game.so")   # (+ its .map: crash offsets -> f_<guest address>)
        t = time.time()
        r = subprocess.run([cc] + target.compile_flags() + target.link_flags() +
                           ["-shared", "-o", dll, MAP(os.path.splitext(dll)[0] + ".map"), "@" + rsp],
                           capture_output=True, text=True)
        undefined = sorted({l.strip() for l in (r.stdout + r.stderr).splitlines() if "undefined symbol" in l})
        if r.returncode:
            print("link FAILED (%.0fs), %d undefined symbols" % (time.time() - t, len(undefined)))
            print("\n".join(undefined[:40]) if undefined else (r.stdout + r.stderr)[-3000:])
            return 1
        print("linked %s: %d MB, no undefined symbols (%.0fs)" % (dll, os.path.getsize(dll) >> 20, time.time() - t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
