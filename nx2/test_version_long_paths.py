"""The source fingerprint of a builder unpacked in a deep folder (#1115), game-free.

A portable launcher in Downloads put the builder's tools 176 characters deep, and its deepest sources past Windows' 260
(MAX_PATH). nx2/version.py's fingerprint() opened them by their plain paths and the build stopped with FileNotFoundError;
a folder past 260 was not listed at all, so its sources were left out without a word. It hashes each file's path relative
to its root, so the same tree must give the same answer at any depth.

nx2/version.py is loaded alone by other tools (scripts/dev_packs.py, the bake tools, the checks), so it keeps long paths of
its own: this check loads it alone too, in a process where nothing else made long paths work (nx2/long_paths.py).

On Windows with the long-paths setting off (the default) it fails without the fix; elsewhere, and with that setting on,
plain paths reach the files already and it checks only that the depth doesn't matter.

    python -I -B nx2/test_version_long_paths.py
"""
import builtins
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import version  # noqa: E402

ORIGINAL_OPEN = builtins.open

MAX_PATH = 260
FOLDER_REL = "gpu/third_party/suyu/shader_recompiler/frontend/maxwell"
FILE_REL = FOLDER_REL + "/indirect_branch_table_track.cpp"
DEEP_FOLDER_REL = "gpu/" + "/".join(["d" * 40] * 3) + "/b.cpp"
STAMP_TREE = ("gpu/vk/sirit/src/sirit.cpp", "gpu/vk/compat/compat.h", "gpu/vk/vk_shader.cpp", "gpu/vk/vk_bank.cpp",
              "gpu/vk/vk_state.h")


def ext(p):
    """p as Windows' extended-length path, to make the tree past MAX_PATH; elsewhere as is."""
    return "\\\\?\\" + os.path.abspath(p) if os.name == "nt" else p


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(ext(os.path.dirname(p)), exist_ok=True)
    with open(ext(p), "wb") as f:
        f.write(text.encode())
    return p


def trees(base, name, deep_rel, folder):
    """the same tree under a short root and under one deep enough: deep_rel's file past 260, its folder too (folder)"""
    short, head = os.path.join(base, name + "-s"), os.path.join(base, name + "-l")
    target = MAX_PATH + 20 if folder else MAX_PATH - 8
    pad = target - len(os.path.join(head, "x", os.path.dirname(deep_rel)))
    assert pad > 0, "the temporary folder is too deep for this check: %s" % base
    deep = os.path.join(head, "p" * pad)
    for root in (short, deep):
        for rel in ("host/a.c", "nx2/c.py") + STAMP_TREE:
            write(root, rel, rel + "\n")
        path = write(root, deep_rel, "the deep source\n")
    assert len(path) > MAX_PATH and (len(os.path.dirname(path)) > MAX_PATH) == folder, len(path)
    return short, deep, path


def fingerprint_case(short, deep, path):
    try:
        got = version.fingerprint(deep)
    except OSError as e:
        raise AssertionError("fingerprint() stopped at a %d-character path: %r" % (len(path), e))
    expect = version.fingerprint(short)
    assert got == expect, "%d characters deep the fingerprint is %s, at a short root %s (a source left out)" % (
        len(path), got, expect)


CASES = [
    ("fingerprint: a file past 260", fingerprint_case, FILE_REL, False),
    ("fingerprint: a folder past 260", fingerprint_case, DEEP_FOLDER_REL, True),
]


def main():
    assert builtins.open is ORIGINAL_OPEN, "something made long paths work for the whole process: this check needs a plain one"
    os.environ.pop("HOOKSHOT_VERSION", None)
    base = os.path.abspath(tempfile.mkdtemp())
    failed = []
    try:
        for i, (name, case, rel, folder) in enumerate(CASES):
            try:
                case(*trees(base, "t%d" % i, rel, folder))
                print("  ok: %s" % name)
            except AssertionError as e:
                print("FAIL: %s: %s" % (name, e))
                failed.append(name)
    finally:
        shutil.rmtree(ext(base), ignore_errors=True)   # (by its long path: a plain one can't clear the deep trees)
    if failed:
        sys.exit("version long paths: %d of %d cases failed" % (len(failed), len(CASES)))
    print("version long paths: ok")


if __name__ == "__main__":
    main()
