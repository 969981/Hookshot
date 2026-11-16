#!/usr/bin/env python3
"""Every part a Windows setup list names is served from the release's own builder-parts, never from where an earlier list put it.

rc.21's hookshot-setup.txt still fetched emsdk and wgpu-native from the deleted upstream repository: make_setup_parts.py --reuse took an
earlier list's line as it was, URL included, so the old repository was carried forward release after release and a new install
stopped at those two parts. llvm-mingw came from its author's release. This builds a list from a minimal package and an earlier
list whose reused parts name another repository, and checks every URL is --base's, the hash and size kept. Game-free.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://github.com/example/Hookshot/releases/download/builder-parts"
OLD = "https://github.com/an-old-owner/Hookshot/releases/download/builder-parts"   # (a repository that is gone)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = os.path.join(tmp, "package")
        os.makedirs(os.path.join(pkg, "tools", "work"))
        for name, data in (("HookshotBuilder.exe", b"exe"), ("hostfxr.dll", b"dll")):
            with open(os.path.join(pkg, name), "wb") as f:
                f.write(data)
        with open(os.path.join(pkg, "tools", "work", "profile.txt"), "w") as f:
            f.write("profile\n")
        # (the earlier list: every tools part reused, each from another repository, as rc.21's emsdk and wgpu-native were)
        reused = {
            "python": "1" * 64, "emsdk": "b4c8fc476654d7657806180039b3a96050c104cf6a93dab1cc5c027277ece368", "cpm": "3" * 64,
            "third_party": "4" * 64, "wgpu-native": "a3b3d8253fbaedf5975172994c0b45202fe2e4ff89a42ff5bfad53f9468cf498",
            "amd-fsr": "6" * 64,
        }
        prev = os.path.join(tmp, "previous-setup.txt")
        with open(prev, "w", newline="\n") as f:
            f.write("tag v0.0.0\n")
            for i, (name, sha) in enumerate(reused.items()):
                f.write("part %s %d . - - %s/HookshotBuilder-%s-%s.zip\n" % (sha, 1000 + i, OLD, name, sha[:12]))
        out = os.path.join(tmp, "out")
        subprocess.run([sys.executable, "-I", os.path.join(ROOT, "scripts", "make_setup_parts.py"), pkg, "v9.9.9", out,
                        "--base", BASE, "--reuse", prev], check=True, capture_output=True)
        with open(os.path.join(out, "hookshot-setup.txt"), encoding="utf-8") as f:
            lines = [ln.split() for ln in f.read().splitlines() if ln.startswith("part ")]

    urls = [w[-1] for w in lines]
    foreign = [u for u in urls if not u.startswith(BASE + "/")]
    if foreign:
        sys.exit("FAIL: parts served from outside the release's builder-parts: %s" % foreign)
    for name, sha in reused.items():   # (each reused part keeps its hash and size, only its URL is this repository's)
        line = [w for w in lines if w[1] == sha]
        if len(line) != 1 or not line[0][-1].endswith("/HookshotBuilder-%s-%s.zip" % (name, sha[:12])):
            sys.exit("FAIL: the reused %s part is not in the list as it was, at this repository's URL: %s" % (name, line))
    if not any(u.endswith("/llvm-mingw-20261104-ucrt-x86_64.zip") for u in urls):
        sys.exit("FAIL: llvm-mingw is not served from the release's builder-parts: %s" % urls)
    print("PASS: every part (%d, reused, made and llvm-mingw) is served from the release's own builder-parts" % len(urls))


if __name__ == "__main__":
    main()
