"""The parts HookshotBuilder-Setup.exe downloads (builder/setup/setup.c), from a Hookshot Builder package folder.

    python -I scripts/make_setup_parts.py <package folder> <tag> <out folder> [--base <url the parts are served from>]

A release is split by how often each part changes, so an update downloads only what changed: the builder's own code (a few MB)
in every release, the .NET runtime, Python, emsdk, the CPM packages and third_party only when they change. llvm-mingw comes from
its official release. Each zip is deterministic (sorted names, fixed dates and attributes): a part whose files did not change
has the same SHA-256 in the next release, and the setup takes it from its download cache instead of downloading it again.

Writes <out>/hookshot-setup.txt, its SHA-bound hookshot-setup-space.txt companion, and one
HookshotBuilder-<part>-<sha256 prefix>.zip per part (upload the ones the builder-parts release lacks).
Upload BOTH text files beside the thin bootstrap in the same official release. The companion is not served from --base;
it binds to the exact setup-list bytes and must not be copied from another release. The consent-based bootstrap requires
this companion; it cannot install an older release that lacks it. Scripts the build does not use (.ps1, .bat, .js,
.html: antivirus machine-learning guesses fire on them) are left out.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from package_builder import verify_staged, verified_bytes

SKIP_EXT = {".ps1", ".psm1", ".bat", ".cmd", ".js", ".vbs", ".html", ".htm", ".hta", ".wsf", ".pyc"}
SKIP_DIRS = {"__pycache__"}
TOOLS_PARTS = ("python", "emsdk", "cpm", "third_party", "wgpu-native")   # (tools/<name>: each its own part)
LLVM = ("552191b5754c49a5a20723276a6fc56541ea1922c4c50b7b669022333fc1b40f", 191037046,
        "llvm-mingw-20261104-ucrt-x86_64",
        "https://github.com/mstorsjo/llvm-mingw/releases/download/20261104/llvm-mingw-20261104-ucrt-x86_64.zip")
# Central-directory facts for this exact external archive, after its declared rename.
# These are logical file bytes, not allocated disk space or generated build files.
LLVM_SPACE = {
    "552191b5754c49a5a20723276a6fc56541ea1922c4c50b7b669022333fc1b40f":
        (191037046, "llvm-mingw-20261104-ucrt-x86_64", 753120375, 9070, 9316, 118),
}
SPACE_NAME = "hookshot-setup-space.txt"


def archive_space(path):
    """Read the just-published ZIP's directory, without extracting or changing it."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        regular = [entry for entry in entries if not entry.is_dir()]
        return (sum(entry.file_size for entry in regular), len(regular), len(entries),
                max((len(entry.filename.rstrip("/").encode("utf-16-le")) // 2 for entry in entries), default=0))


def llvm_space():
    facts = LLVM_SPACE.get(LLVM[0])
    if facts is None or facts[:2] != LLVM[1:3]:
        raise ValueError("Record verified space metadata for this exact LLVM archive before publishing.")
    return facts[2:]


def space_text(manifest, records):
    """A companion asset keeps all existing setup-list parsers compatible."""
    lines = ["HookshotSetupSpace 1", "manifest " + hashlib.sha256(manifest).hexdigest()]
    lines += ["space %s %d %d %d %d" % (sha, *facts) for sha, facts in records]
    return "\n".join(lines) + "\n"


def files_under(root, top):
    out = []
    for d, ds, fs in os.walk(os.path.join(root, top)):
        ds[:] = sorted(x for x in ds if x not in SKIP_DIRS)
        for f in fs:
            if os.path.splitext(f)[1].lower() not in SKIP_EXT:
                out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return out


def write_zip(path, root, names, sources, gui_hashes):
    """Deterministic: the same files give the same bytes (sorted, a fixed date, fixed attributes)"""
    created = False
    try:
        with open(path + ".new", "xb") as stream:
            created = True
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for n in sorted(names):
                    info = zipfile.ZipInfo("HookshotBuilder/" + n, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    data = verified_bytes(os.path.join(root, n.replace("/", os.sep)), root, sources, gui_hashes)
                    z.writestr(info, data, compresslevel=6)
    except BaseException:
        if created:
            os.remove(path + ".new")
        raise
    os.replace(path + ".new", path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest(), os.path.getsize(path)


def build_parts(package, tag, out, base=None, repo=None):
    root = os.path.abspath(package)
    if not os.path.isfile(os.path.join(root, "HookshotBuilder.exe")):
        sys.exit("not a Hookshot Builder package folder: %s" % root)
    # (the parts live in one release of their own, named by their checksum: a part that did not change is not uploaded again, and a
    # release itself holds only HookshotBuilder.exe (the setup) and the list it reads)
    revision, sources, _ = verify_staged(repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), root, tag)
    with open(os.path.join(root, "builder-identity.json"), encoding="utf-8") as f:
        gui_hashes = json.load(f)["files"]
    if Path(out).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Write setup parts outside the staging folder.")
    external_space = llvm_space()
    base = (base or "https://github.com/NotRexed/Hookshot/releases/download/builder-parts").rstrip("/")
    os.makedirs(out, exist_ok=True)

    top = sorted(os.listdir(root))
    builder_files = [n for n in top if os.path.isfile(os.path.join(root, n)) and
                     (n.startswith("HookshotBuilder") or n in ("LICENSE", "README.txt", "builder-revision.txt", "builder-identity.json"))]
    runtime_files = [n for n in top if os.path.isfile(os.path.join(root, n)) and n not in builder_files]
    runtime_dirs = [n for n in top if os.path.isdir(os.path.join(root, n)) and n != "tools"]
    tools = sorted(os.listdir(os.path.join(root, "tools")))
    parts = {
        "app": builder_files + [n for t in tools if t not in TOOLS_PARTS and t != "llvm-mingw"
                                for n in (["tools/" + t] if os.path.isfile(os.path.join(root, "tools", t)) else files_under(root, "tools/" + t))],
        "dotnet": runtime_files + [n for d in runtime_dirs for n in files_under(root, d)],
    }
    for t in TOOLS_PARTS:
        if os.path.isdir(os.path.join(root, "tools", t)):
            parts[t] = files_under(root, "tools/" + t)
    # Unknown lines are ignored by the standalone installer; the managed updater requires this capability and identity.
    lines = ["tag %s" % tag, "identity 1 %s" % revision]
    sizes = []
    for name in ("app", "dotnet") + TOOLS_PARTS:
        if not parts.get(name):
            continue
        zp = os.path.join(out, "HookshotBuilder-%s.zip" % name)
        sha, size = write_zip(zp, root, parts[name], sources, gui_hashes)
        named = os.path.join(out, "HookshotBuilder-%s-%s.zip" % (name, sha[:12]))
        os.replace(zp, named)
        zp = named
        lines.append("part %s %d . - - %s/%s" % (sha, size, base, os.path.basename(zp)))
        sizes.append((sha, archive_space(zp)))
        print("%-12s %5d files %5d MB  %s" % (name, len(parts[name]), size >> 20, sha[:16]), flush=True)
    lines.append("part %s %d HookshotBuilder/tools %s llvm-mingw %s" % LLVM)
    sizes.append((LLVM[0], external_space))
    lines.append("run HookshotBuilder/HookshotBuilder.exe")
    with open(os.path.join(out, "hookshot-setup.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    manifest = Path(out, "hookshot-setup.txt").read_bytes()
    with open(os.path.join(out, SPACE_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(space_text(manifest, sizes))
    return revision


def main():
    p = argparse.ArgumentParser()
    p.add_argument("package")
    p.add_argument("tag")
    p.add_argument("out")
    p.add_argument("--base", help="where the parts are served from (default: this repository's builder-parts release)")
    a = p.parse_args()
    build_parts(a.package, a.tag, a.out, a.base)


if __name__ == "__main__":
    main()
