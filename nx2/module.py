"""(hookshot) The split build: a runtime that is built once and shipped (build.py --part runtime), and the game module
the player's PC builds from their own copy of the game (build.py --part game). No game code is in the runtime, so it
can be distributed; the module is everything made from the game, and needs no Windows SDK, Visual Studio or libc
headers to build (nx2/rt/crt).

    windows: hookshot_rt.exe  imports  hookshot_game.dll     (+ hookshot_rt.lib: the runtime's import library)
    linux:   hookshot_rt      needs    libhookshot_game.so   (+ hookshot_rt.exports: what the runtime exports)

The runtime and the module call each other:
  - the module calls the runtime: the HLE functions (imp_*), the hooks and splices, a64-kit's weak hooks host/
    overrides (nx2/rtgen.py --part game), nvn2_dispatch, hookshot_crt_* (host/module_crt.c). On Windows the runtime
    exports every global of host/ (a .def made from its objects; data marked DATA, so a reference without dllimport
    fails the link rather than reading a thunk), on Linux everything (-rdynamic).
  - the runtime reads the module: a64-kit's runtime (rt/a64_rt.c, a64_suyu.c, which stay with the lifted code: the
    call-site caches hold offsets from a64_lookup), the soft write watch's marks (a64_dirty) and gen/hos_image.c's
    constants. That interface does not depend on the game, so the runtime links against an import library (Windows) or a
    stub shared object (Linux) made from INTERFACE, with no game at hand.

Lifted code calls lifted code directly, as in the one executable: the module exports only INTERFACE (a .def on Windows,
a version script with -fvisibility=protected on Linux). runtime.stamp says which runtime a module was built for:
the launcher rebuilds the module when they differ.
"""
import hashlib
import os
import re
import subprocess

import kit
import target
import toolchain

HERE = os.path.dirname(os.path.abspath(__file__))
CRT = os.path.join(HERE, "rt", "crt")
MODULE_CRT = os.path.join(HERE, "rt", "module_crt.c")
PARTS = ("runtime", "game")

RUNTIME = {"windows": "hookshot_rt.exe", "linux": "hookshot_rt"}
MODULE = {"windows": "hookshot_game.dll", "linux": "libhookshot_game.so"}
RUNTIME_LIB = {"windows": "hookshot_rt.lib", "linux": "hookshot_rt.exports"}
STAMP = "runtime.stamp"
GAME_STAMP = "game.stamp"   # (the runtime.stamp a module was built against, beside it: launcher/hookshot.c)

# gen/hos_image.c (nx2/standalone.py write_image_c): the boot constants the runtime reads
IMAGE_DATA = ["hos_main_stack_size", "hos_main_priority", "hos_main_core", "hos_image_base", "hos_image_end",
              "hos_module_count", "hos_module_init", "hos_module_name", "hos_entry_nninitStartup", "hos_entry_nnMain",
              "hos_program_id", "hos_main_build_id", "hos_entry_init_libc0", "hos_entry_init_libc1",
              "hos_entry_init_libc2", "hos_entry_nnosInitialize", "hos_entry_nninitInitializeSdkModule",
              "hos_entry_nvnBootstrapLoaderInternal", "hos_thread_start_hook", "hos_thread_exit_hook"]
# what a Linux module may leave to the process's C library (the calls clang did not inline)
LIBC = {"memcpy", "memmove", "memset", "memcmp", "strlen", "strcmp", "strncmp"}


def compile_flags(part):
    """what the part adds to every object's flags"""
    if part == "game":
        fl = ["-nostdlibinc", "-I" + CRT]
        if target.windows():   # (the runtime's data the lifted code reads: nx2/oot_patches.py)
            return fl + ["-DA64_HOST_DATA=__declspec(dllimport)"]
        return fl + ["-fPIC", "-fvisibility=protected"]
    if part == "runtime":
        if target.windows():
            return ["-DA64_GAME_DATA=__declspec(dllimport)", "-DHOS_GAME_DATA=__declspec(dllimport)"]
        return ["-fPIC"]   # (the module's data through the GOT: no copy relocations of arrays whose size is the game's)
    return []


def runtime_includes(out):
    """what host/ includes from the lift, without one: a64-kit's suyu runtime header (the same for every game) and
    an empty a64_fpx.h (the lift's FP fast paths: static inline helpers only the lifted code calls)"""
    import a64_rtgen
    inc = os.path.join(out, "rtinc")
    os.makedirs(inc, exist_ok=True)
    a64_rtgen.write(inc)
    fpx = os.path.join(inc, "a64_fpx.h")
    if not os.path.exists(fpx):
        open(fpx, "w").write("/* (hookshot) the runtime's: the lift's FP helpers are the module's (nx2/module.py) */\n")
    return inc


def nm(objs, defined):
    """{name: "data" | "func"} of the objects' globals, defined or undefined ones"""
    tool = toolchain.need(toolchain.find("llvm-nm"))
    out = {}
    for i in range(0, len(objs), 500):
        r = subprocess.run([tool, "--defined-only" if defined else "--undefined-only", "-g", "--no-sort"] + objs[i:i + 500],
                           capture_output=True, text=True, check=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts or line.endswith(":"):
                continue
            name, kind = parts[-1], parts[-2] if len(parts) > 1 else "U"
            out[name] = "func" if kind in ("T", "t", "W", "w", "U") else "data"
    return out


def interface(rt_objs):
    """what the runtime takes from the module: the global definitions of a64-kit's runtime in its game flavour
    (a64_rt.o, a64_suyu.o), the soft write watch and hos_image.c's constants"""
    # (C names only: not the compiler's constant pools, __real@..., or COFF's .weak.* aliases, which differ by -O)
    syms = {n: k for n, k in nm(rt_objs, True).items() if re.match(r"[A-Za-z_]\w*$", n)}
    syms.update({n: "data" for n in IMAGE_DATA})
    return dict(sorted(syms.items()))


def write_def(path, library, syms):
    lines = ["LIBRARY %s" % library, "EXPORTS"] + ["    %s%s" % (n, " DATA" if k == "data" else "") for n, k in syms.items()]
    text = "\n".join(lines) + "\n"
    if not os.path.exists(path) or open(path).read() != text:
        open(path, "w", newline="\n").write(text)
    return path


def import_lib(def_path, out):
    subprocess.run([target.lld_link(), "/lib", "/machine:x64", "/def:" + def_path, "/out:" + out], check=True,
                   capture_output=True)
    return out


def stub_shared(syms, out, cc):
    """a stand-in libhookshot_game.so with the interface's names, for linking the runtime without a game: the loader
    finds the real one beside the runtime (rpath $ORIGIN)"""
    src = out[:-3] + "_stub.c"
    body = ["/* (hookshot) nx2/module.py: the module's interface, for the runtime's link only. */"]
    body += ["void %s(void) {}" % n if k == "func" else "char %s[1];" % n for n, k in syms.items()]
    open(src, "w", newline="\n").write("\n".join(body) + "\n")
    subprocess.run([cc] + target.compile_flags() + target.link_flags() +
                   ["-shared", "-fPIC", "-nostdlib", "-Wl,-soname," + MODULE["linux"], "-o", out, src], check=True)
    return out


def stamp(paths):
    """what a module must have been built against: the interface files and the headers both sides compile"""
    h = hashlib.sha1()
    for p in sorted(paths):
        h.update(os.path.basename(p).encode() + open(p, "rb").read())
    return h.hexdigest()


def runtime_link_args(out, host_objs, rt_objs, cc):
    """the runtime executable's extra link arguments: exports + its import library (Windows) or -rdynamic (Linux),
    and the module's interface to link against. Writes runtime.stamp"""
    syms = interface(rt_objs)
    listing = os.path.join(out, "hookshot_game.interface")   # (for the module's exports, on the player's PC)
    text = "".join("%s %s\n" % kv for kv in syms.items())
    if not os.path.exists(listing) or open(listing).read() != text:
        open(listing, "w", newline="\n").write(text)
    if target.windows():
        mod_lib = import_lib(write_def(os.path.join(out, "hookshot_game.def"), MODULE["windows"], syms),
                             os.path.join(out, "hookshot_game.lib"))
        exports = {n: k for n, k in nm(host_objs, True).items() if n != "main" and re.match(r"[A-Za-z_]\w*$", n)}
        rt_def = write_def(os.path.join(out, "hookshot_rt.def"), RUNTIME["windows"], exports)
        args = [mod_lib, "-Wl,/def:" + rt_def, "-Wl,/implib:" + os.path.join(out, RUNTIME_LIB["windows"])]
        tracked = [listing, rt_def]
    else:
        os.makedirs(os.path.join(out, "stub"), exist_ok=True)
        args = ["-rdynamic", "-fPIC", stub_shared(syms, os.path.join(out, "stub", MODULE["linux"]), cc), "-Wl,-rpath,$ORIGIN"]
        tracked = [listing]
    with open(os.path.join(out, STAMP), "w", newline="\n") as f:
        f.write(stamp(tracked + [os.path.join(CRT, n) for n in sorted(os.listdir(CRT))]) + "\n")
    return args


def write_runtime_exports(exe, out):
    """(Linux) the runtime's dynamic symbols, for the module's closure check on the player's PC"""
    tool = toolchain.need(toolchain.find("llvm-nm"))
    r = subprocess.run([tool, "-D", "--defined-only", exe], capture_output=True, text=True, check=True)
    names = sorted({l.split()[-1] for l in r.stdout.splitlines() if l.strip()})
    open(os.path.join(out, RUNTIME_LIB["linux"]), "w", newline="\n").write("\n".join(names) + "\n")


def link_game(cc, objs, out, runtime_dir):
    """the game module, against the prebuilt runtime in runtime_dir; on Linux checked to close over the runtime's
    exports (a module that does not loads and then fails at its first missing symbol)"""
    win = target.windows()
    rt_lib = os.path.join(runtime_dir, RUNTIME_LIB[target.name()])
    if not os.path.exists(rt_lib):
        raise SystemExit("no %s in %s: build the runtime first (build.py --part runtime) or point --runtime at it"
                         % (os.path.basename(rt_lib), runtime_dir))
    syms = dict(l.split() for l in open(os.path.join(runtime_dir, "hookshot_game.interface")))
    rsp = os.path.join(out, "game_objs.rsp")
    open(rsp, "w").write("\n".join('"%s"' % o.replace("\\", "/") for o in objs))
    dst = os.path.join(out, MODULE[target.name()])
    if win:
        def_path = write_def(os.path.join(out, "hookshot_game.def"), MODULE["windows"], syms)
        args = ["-shared", "-nostdlib", "-Wl,/noentry", "-Wl,/nodefaultlib", "-Wl,/def:" + def_path,
                "-Wl,/map:" + dst[:-4] + ".map", "@" + rsp, rt_lib]
    else:
        script = os.path.join(out, "hookshot_game.ver")
        open(script, "w", newline="\n").write("{\n  global:\n%s  local: *;\n};\n" % "".join("    %s;\n" % n for n in syms))
        args = ["-shared", "-nostdlib", "-fPIC", "-Wl,-soname," + MODULE["linux"], "-Wl,--version-script=" + script,
                "-Wl,-Map=" + dst[:-3] + ".map", "@" + rsp]
    r = subprocess.run([cc] + target.compile_flags() + target.link_flags() + ["-o", dst] + args,
                       capture_output=True, text=True)
    if r.returncode:
        undefined = sorted({l.strip() for l in (r.stdout + r.stderr).splitlines() if "undefined symbol" in l})
        print("%s link FAILED, %d undefined symbols" % (os.path.basename(dst), len(undefined)))
        print("\n".join(undefined[:60]) if undefined else (r.stdout + r.stderr)[-3000:])
        return None
    if not win:
        exported = set(open(rt_lib).read().split())
        missing = sorted(set(nm([dst], False)) - exported - LIBC)
        if missing:
            print("%s needs %d symbols the runtime does not export:\n  %s" % (os.path.basename(dst), len(missing),
                                                                             "\n  ".join(missing[:60])))
            return None
    import shutil
    shutil.copyfile(os.path.join(runtime_dir, STAMP), os.path.join(out, GAME_STAMP))
    return dst
