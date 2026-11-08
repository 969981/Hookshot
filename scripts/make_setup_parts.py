"""The parts HookshotBuilder-Setup.exe downloads (builder/setup/setup.c), from a Hookshot Builder package folder.

    python -I scripts/make_setup_parts.py <package folder> <tag> <out folder> [--base <url the parts are served from>]
                                          [--reuse <an earlier release's hookshot-setup.txt>]
                                          [--reuse-space <its hookshot-setup-space.txt>]

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

--reuse: a part whose tools/<name> folder the package lacks is taken from that list as it is (its line, with its URL), so a
package made in CI (.github/workflows/windows-builder.yml) carries the Python, emsdk, CPM and wgpu-native parts the maintainer
made. Missing folders require --reuse-space bound to that exact prior list. Generate a legacy companion offline with
scripts/release_parts.py space from existing ZIPs, or stage all parts. Nothing is downloaded to infer size facts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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


def archive_space(path, folder=".", old="-", new="-"):
    """Read the just-published ZIP's directory, without extracting or changing it."""
    def components(value):
        parts = value.split("/")
        if any(not p or p in (".", "..") or "\\" in p or ":" in p for p in parts):
            raise ValueError("Unsupported archive destination or member path.")
        return parts
    prefix = [] if folder == "." else components(folder)
    if (old == "-") != (new == "-") or (old != "-" and (len(components(old)) != 1 or len(components(new)) != 1)):
        raise ValueError("Unsupported archive root rename.")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        paths, regular = [], []
        for entry in entries:
            relative = components(entry.filename.rstrip("/"))
            if old != "-":
                if relative[0] != old:
                    raise ValueError("Archive member is outside its declared root.")
                relative[0] = new
            paths.append("/".join(prefix + relative))
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise ValueError("Space metadata requires unencrypted regular files/directories.")
            if not entry.is_dir():
                regular.append(entry)
        return (sum(entry.file_size for entry in regular), len(regular), len(entries),
                max((len(path.encode("utf-16-le")) // 2 for path in paths), default=0))


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


def manifest_parts(manifest):
    parts = [line.split() for line in manifest.decode("utf-8").splitlines() if line.startswith("part ")]
    if not 1 <= len(parts) <= 16:
        raise ValueError("Setup list requires between one and sixteen parts.")
    seen = set()
    for fields in parts:
        if (len(fields) != 7 or not re.fullmatch("[0-9a-f]{64}", fields[1]) or
                not re.fullmatch("[0-9]+", fields[2]) or not 0 < int(fields[2]) < 2**64 or fields[1] in seen):
            raise ValueError("Setup list has an invalid or duplicate part identity.")
        seen.add(fields[1])
    return parts


def read_space(manifest, companion):
    """Verify inherited facts against the exact prior list, without fetching parts."""
    if len(companion) > 262144:
        raise ValueError("Setup space companion is too large.")
    lines = [line.split() for line in companion.decode("ascii").splitlines() if line.strip()]
    if lines[:2] != [["HookshotSetupSpace", "1"], ["manifest", hashlib.sha256(manifest).hexdigest()]]:
        raise ValueError("Setup space companion does not match the exact setup list.")
    facts = {}
    for fields in lines[2:]:
        if len(fields) != 6 or fields[0] != "space" or not re.fullmatch("[0-9a-f]{64}", fields[1]) or fields[1] in facts:
            raise ValueError("Setup space companion has an invalid or duplicate record.")
        if any(not re.fullmatch("[0-9]+", value) or int(value) >= 2**64 for value in fields[2:]):
            raise ValueError("Setup space companion has an invalid byte/count value.")
        expanded, files, entries, maximum = values = tuple(map(int, fields[2:]))
        if (files > entries or maximum > 32767 or (not entries and any(values)) or
                (entries and not maximum) or (not files and expanded)):
            raise ValueError("Setup space companion has inconsistent archive facts.")
        facts[fields[1]] = values
    if set(facts) != {fields[1] for fields in manifest_parts(manifest)}:
        raise ValueError("Setup space companion is missing parts or contains unknown parts.")
    return facts


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


def build_parts(package, tag, out, base=None, repo=None, reuse=None, reuse_space=None):
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
    reused = {}
    if reuse:
        previous = Path(reuse).read_bytes()
        for line in previous.decode("utf-8").splitlines():
            w = line.split()
            if len(w) == 7 and w[0] == "part" and w[6].rsplit("/", 1)[-1].startswith("HookshotBuilder-"):
                reused[w[6].rsplit("/", 1)[-1][len("HookshotBuilder-"):].rsplit("-", 1)[0]] = line
    inherited = {}
    missing = [name for name in TOOLS_PARTS if not parts.get(name)] if reuse else []
    for name in missing:
        if name not in reused:
            sys.exit("no tools/%s in the package and no %s part in %s" % (name, name, reuse))
    if missing:
        if not reuse_space:
            raise ValueError("Reused tool parts need --reuse-space for that exact prior setup list. Generate/upload its "
                             "companion with release_parts.py space using existing verified ZIPs, or stage all tool parts.")
        try:
            inherited = read_space(previous, Path(reuse_space).read_bytes())
        except (OSError, ValueError) as error:
            raise ValueError("Invalid --reuse-space: %s. Generate/upload that prior list's companion from existing "
                             "verified ZIPs, or stage all tool parts." % error) from error
    os.makedirs(out, exist_ok=True)
    # The managed updater requires the current app/source identity even when unchanged tool parts are reused.
    lines = ["tag %s" % tag, "identity 1 %s" % revision]
    sizes = []
    for name in ("app", "dotnet") + TOOLS_PARTS:
        if not parts.get(name):
            if reuse and name in TOOLS_PARTS:
                lines.append(reused[name])
                sha = reused[name].split()[1]
                sizes.append((sha, inherited[sha]))
                print("%-12s (as in %s)" % (name, os.path.basename(reuse)), flush=True)
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
    manifest = ("\n".join(lines) + "\n").encode("utf-8")
    companion = space_text(manifest, sizes)
    read_space(manifest, companion.encode("ascii"))
    Path(out, "hookshot-setup.txt").write_bytes(manifest)
    with open(os.path.join(out, SPACE_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(companion)
    return revision


def main():
    p = argparse.ArgumentParser()
    p.add_argument("package")
    p.add_argument("tag")
    p.add_argument("out")
    p.add_argument("--base", help="where the parts are served from (default: this repository's builder-parts release)")
    p.add_argument("--reuse", help="an earlier hookshot-setup.txt: the parts the package has no folder for, from it")
    p.add_argument("--reuse-space", help="the SHA-bound space companion of --reuse (required for reused tool parts)")
    a = p.parse_args()
    build_parts(a.package, a.tag, a.out, a.base, reuse=a.reuse, reuse_space=a.reuse_space)


if __name__ == "__main__":
    main()
