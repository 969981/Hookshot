"""The build's Python with its sources, work folder and game folder past 260 characters (#1115), game-free.

A portable launcher in Downloads put the builder's tools 176 characters deep: 145 of its sources then pass Windows' 260
(MAX_PATH), 53 of them in gpu/vk/eden that the renderer compiles. With the work folder as deep, the objects (named after
their sources, up to ~90 characters) and their stamps pass it too. clang and the linker take such paths; the build's
Python did not: a plain open() stopped with FileNotFoundError, os.path.exists and isfile said False, and a walk left a
folder past 260 out without a word.

Each check below runs nx2/build.py's own functions on paths past 260 and asks for what they give for a short path:
- its sources: the header blob (tree_headers), the renderer's sources (vk_sources), each object's key (object_key,
  key_of, the hash cache's stat, content_sha1) and the size table (src_size);
- its work folder: an object linked in from the object cache and stamped (compile_one), and found stamped by the next
  build; an object cached (cache_put); the link record over such objects (link_key);
- and each script the build runs, imported in a fresh process, sees a file past 260 (nx2/long_paths.py installed).

On Windows with the long-paths setting off (the default) they fail without the fix; elsewhere they check only that the
depth doesn't matter. The fingerprint has nx2/test_version_long_paths.py.

    python -I -B tests/test_build_long_paths.py
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

RUN_ALL_MODULES = ["capstone", "lz4"]   # (the lift case loads nx2/lift.py: capstone reads the code, lz4 the NSO's segments)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "nx2"), str(ROOT / "third_party" / "a64-kit" / "tools")]
spec = importlib.util.spec_from_file_location("build_long_paths", ROOT / "nx2" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

MAX_PATH = 260
# (a source shaped like the report's deepest, under gpu/vk/eden: what the renderer compiles)
DEEP_FILE = "vk/eden/shader_recompiler/frontend/maxwell/translate/impl/floating_point_conversion_integer.cpp"
DEEP_FOLDER = "vk/eden/" + "/".join(["d" * 40] * 3) + "/emit_spirv_folder.cpp"
TREE = {
    "vk/sirit/src/sirit.cpp": "sirit\n",
    "vk/compat/compat.h": "#pragma once\n",
    "vk/vk_shader.cpp": "shader\n",
    "vk/eden/common.h": "#pragma once\n",
}
# (the longest object this build names: its source's path flattened, as nx2/build.py's obj_name does)
DEEP_OBJ = "vk_eden_shader_recompiler_backend_spirv_emit_spirv_bitwise_conversion.o"
# the scripts the build runs (nx2/make_game.py and the steps it starts), each to install nx2/long_paths.py as it loads
ENTRY_SCRIPTS = ("make_game", "build", "lift", "nup", "unpack", "split_hot", "shader_census", "prompt_assets")


def ext(p):
    """p as Windows' extended-length path, to make and clear the trees past MAX_PATH; elsewhere as is."""
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" else p


def make(root, files):
    for rel, text in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(ext(os.path.dirname(p)), exist_ok=True)
        with open(ext(p), "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode())


def scratch(test):
    base = os.path.abspath(tempfile.mkdtemp(prefix="lp-"))
    test.addCleanup(shutil.rmtree, ext(base), True)
    return base


def deep_folder(base, name, depth):
    """a folder depth characters deep under base"""
    head = os.path.join(base, name)
    pad = depth - len(head) - 1
    assert pad > 0, "the temporary folder is too deep for this check: %s" % base
    return os.path.join(head, "p" * pad)


class Sources(unittest.TestCase):
    """Short and deep trees of one shape: deep_rel's file past 260, and (folder=True) its folder too."""

    def trees(self, deep_rel, folder):
        base = scratch(self)
        short = os.path.join(base, "s", "gpu")
        target = MAX_PATH + 10 if folder else MAX_PATH - 8   # (the deep file's folder past 260, or just under it)
        deep = os.path.join(deep_folder(base, "l", target - len(os.path.join("gpu", os.path.dirname(deep_rel))) - 1), "gpu")
        files = dict(TREE, **{deep_rel: "the deep source\n"})
        make(short, files)
        make(deep, files)
        path = os.path.join(deep, *deep_rel.split("/"))
        self.assertGreater(len(path), MAX_PATH)
        self.assertEqual(len(os.path.dirname(path)) > MAX_PATH, folder, "not the case meant: %d" % len(path))
        return short, deep, path

    def both(self, test):
        for name, rel, folder in (("a file past 260", DEEP_FILE, False), ("a folder past 260", DEEP_FOLDER, True)):
            with self.subTest(name):
                test(*self.trees(rel, folder))

    def test_header_blob(self):
        def check(short, deep, path):
            h = path[:-4] + ".h"   # (a header beside the deep source)
            make(deep, {os.path.relpath(h, deep).replace(os.sep, "/"): "#pragma once\n"})
            make(short, {os.path.relpath(h, deep).replace(os.sep, "/"): "#pragma once\n"})
            self.assertEqual(build.tree_headers(deep), build.tree_headers(short))
        self.both(check)

    def test_renderer_sources(self):
        def check(short, deep, path):
            saved = build.GPU
            self.addCleanup(setattr, build, "GPU", saved)
            build.GPU = deep
            self.assertIn(path, build.vk_sources(), "the deep source is not compiled")
        self.both(check)

    def test_object_keys_and_sizes(self):
        def check(short, deep, path):
            plain = os.path.join(short, *os.path.relpath(path, deep).split(os.sep))
            fl, deps = ["-O1"], b"/* headers */"
            self.assertEqual(build.object_key(path, fl, deps), build.object_key(plain, fl, deps))
            self.assertEqual(build.src_size(path), build.src_size(plain))
            self.assertIsNotNone(build.known(path)[1], "the hash cache took the deep source for missing")
            self.assertEqual(build.key_of(path, fl, deps), build.key_of(plain, fl, deps))
            self.assertEqual(build.content_sha1(path), build.content_sha1(plain))
        self.both(check)


class WorkFolder(unittest.TestCase):
    """A work folder 180 characters deep, as deep as the tools in the report: its objects and stamps pass 260."""

    def setUp(self):
        base = scratch(self)
        self.work = deep_folder(base, "w", 180)
        self.obj_dir = os.path.join(self.work, "sa", "build", "obj")
        os.makedirs(ext(self.obj_dir))
        self.obj = os.path.join(self.obj_dir, DEEP_OBJ)
        self.assertGreater(len(self.obj + ".sha1"), MAX_PATH)
        self.src = os.path.join(base, "s", "x.cpp")   # (a short source: only the work folder is deep here)
        make(os.path.dirname(self.src), {"x.cpp": "int x;\n"})
        saved = build.OBJ_CACHE, build.GATE, dict(build.PLANNED)
        self.addCleanup(self.restore, saved)
        build.OBJ_CACHE = os.path.join(self.work, "objcache")
        build.GATE = None

    def restore(self, saved):
        build.OBJ_CACHE, build.GATE = saved[0], saved[1]
        build.PLANNED.clear()
        build.PLANNED.update(saved[2])

    def test_an_object_from_the_cache_is_stamped_and_found_again(self):
        fl, deps = ["-O1"], b"/* headers */"
        key = build.object_key(self.src, fl, deps)
        make(build.OBJ_CACHE, {key + ".o": b"the object"})
        job = ("clang-not-run", self.src, self.obj, fl, deps)
        try:
            self.assertEqual(build.compile_one(job), (self.src, 0.0, None))
        except OSError as e:
            self.fail("compile_one stopped in a work folder 180 characters deep: %r" % e)
        with open(ext(self.obj + ".sha1")) as f:
            self.assertEqual(f.read(), key, "the object's stamp")
        os.remove(ext(os.path.join(build.OBJ_CACHE, key + ".o")))   # (the next build must find the object by its stamp)
        self.assertEqual(build.compile_one(job), (self.src, 0.0, None), "the next build did not find the stamped object")

    def test_an_object_is_cached(self):
        make(self.obj_dir, {DEEP_OBJ: b"the object"})
        try:
            build.cache_put(self.obj, "k" * 40)
        except OSError as e:
            self.fail("cache_put stopped at a %d-character object: %r" % (len(self.obj), e))
        with open(ext(os.path.join(build.OBJ_CACHE, "k" * 40 + ".o")), "rb") as f:
            self.assertEqual(f.read(), b"the object")

    def test_the_link_record(self):
        make(self.obj_dir, {DEEP_OBJ: b"the object", DEEP_OBJ + ".sha1": "key one"})
        rsp = os.path.join(os.path.dirname(self.src), "objs.rsp")   # (a short list naming the deep object)
        make(os.path.dirname(rsp), {"objs.rsp": '"%s"\n' % self.obj.replace("\\", "/")})
        cmd = [sys.executable, "@" + rsp, "-o", os.path.join(os.path.dirname(self.src), "out.exe")]
        objs, _ = build.link_inputs(cmd, cmd[-1])
        self.assertEqual([os.path.normcase(os.path.abspath(o)) for o in objs], [os.path.normcase(self.obj)])
        try:
            first = build.link_key(cmd, cmd[-1])
        except OSError as e:
            self.fail("link_key stopped at a %d-character object: %r" % (len(self.obj), e))
        make(self.obj_dir, {DEEP_OBJ + ".sha1": "key two"})
        self.assertNotEqual(build.link_key(cmd, cmd[-1]), first, "a deep object's new stamp kept the link record")


@unittest.skipUnless(os.name == "nt", "long_paths.install() does nothing off Windows, and loading every script needs the "
                     "build's packages (capstone, ...)")
class EntryScripts(unittest.TestCase):
    """Each script the build runs, loaded in a fresh process as python -I runs it, then reads a file past 260"""

    def test_each_script_reads_a_file_past_260(self):
        base = scratch(self)
        deep = os.path.join(deep_folder(base, "e", 250), "f" * 30 + ".txt")
        make(os.path.dirname(deep), {os.path.basename(deep): "deep\n"})
        for name in ENTRY_SCRIPTS:
            with self.subTest(name):
                script = str(ROOT / "nx2" / (name + ".py"))
                code = ("import importlib.util, os, sys\n"
                        "s = importlib.util.spec_from_file_location('entry', %r)\n"
                        "m = importlib.util.module_from_spec(s)\n"
                        "s.loader.exec_module(m)\n"
                        "p = %r\n"
                        "ok = os.path.isfile(p) and open(p).read() == 'deep\\n' and os.path.getsize(p) > 0\n"
                        "print('READ' if ok else 'NOT READ')\n" % (script, deep))
                r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120,
                                   cwd=str(ROOT))
                lines = r.stdout.strip().splitlines()
                self.assertEqual(lines[-1:], ["READ"], "nx2/%s.py, loaded, can't read a %d-character path: %s %s" % (
                    name, len(deep), r.stdout[-300:], r.stderr[-600:]))


if __name__ == "__main__":
    unittest.main()
