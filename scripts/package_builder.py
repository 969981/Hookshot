"""Verify a staged Windows builder, stamp its actual source revision, then create its ZIP.

The staged folder must contain the freshly published builder and its tools. No
download, compiler or game build is run here. See docs/release-identity.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

# build.py compiles both renderer trees, including gpu/third_party/suyu, and
# reads a64-kit/Opus/zBic from third_party. Do not omit vendored build sources.
TREES = ("host", "nx2", "gpu", "third_party")
SOURCE_SUFFIXES = {".c", ".cpp", ".h", ".hpp", ".hh", ".inc", ".inl", ".py", ".glsl", ".wgsl", ".comp",
                   ".vert", ".frag", ".metal", ".asm", ".s", ".S", ".rs", ".cmake", ".def"}
TEXT_SUFFIXES = SOURCE_SUFFIXES | {".txt", ".md", ".rst", ".json", ".toml", ".yml", ".yaml", ".sh", ".bat", ".ps1"}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8", stderr=subprocess.PIPE).rstrip("\n")


def files_under(root):
    """Do not follow staging links into unrelated folders."""
    for item in os.scandir(root):
        path = Path(item.path)
        if item.is_symlink() or getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
            raise ValueError("Staging links are not packaged: " + str(path))
        if item.is_dir(follow_symlinks=False):
            yield from files_under(path)
        else:
            yield path


def source_matches(data, expected, textual=True):
    def blob_hash(value):
        return hashlib.sha1(b"blob " + str(len(value)).encode("ascii") + b"\0" + value).hexdigest()
    return blob_hash(data) == expected or (textual and blob_hash(data.replace(b"\r\n", b"\n")) == expected)


def embedded_revision(path):
    """Read the SDK's compile-time ProductVersion without loading or running the GUI."""
    if os.name != "nt":
        raise ValueError("Package the Windows builder on Windows to verify its embedded source revision.")
    import ctypes
    from ctypes import wintypes
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if size:
        data = ctypes.create_string_buffer(size)
        value, length = ctypes.c_void_p(), wintypes.UINT()
        if (version.GetFileVersionInfoW(str(path), 0, size, data) and
                version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(value), ctypes.byref(length))):
            translations = ctypes.cast(value, ctypes.POINTER(wintypes.WORD))
            for i in range(0, length.value // ctypes.sizeof(wintypes.WORD) - 1, 2):
                key = "\\StringFileInfo\\%04x%04x\\ProductVersion" % (translations[i], translations[i + 1])
                text, count = ctypes.c_void_p(), wintypes.UINT()
                if version.VerQueryValueW(data, key, ctypes.byref(text), ctypes.byref(count)) and 0 < count.value <= 256:
                    product = ctypes.wstring_at(text, count.value).rstrip("\0")
                    match = re.fullmatch(r"[^+]+\+([0-9a-fA-F]{40})", product)
                    if match:
                        return match.group(1).lower()
    raise ValueError("The builder has no supported compile-time source revision: " + str(path))


def verify_staged(repo, staged, tag):
    """One publisher guard for full ZIPs and checksum-cached setup parts."""
    repo, staged = Path(repo).resolve(), Path(staged).resolve()
    if not tag or tag != tag.strip() or len(tag.encode("utf-8")) > 127 or any(ord(c) < 32 or ord(c) == 127 for c in tag):
        raise ValueError("A valid release tag is required.")
    try:
        git(repo, "check-ref-format", "refs/tags/" + tag)
    except subprocess.CalledProcessError as error:
        raise ValueError("A valid Git release tag is required.") from error
    revision = git(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("The checkout has no supported source revision.")
    try:
        tagged = git(repo, "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}")
    except subprocess.CalledProcessError as error:
        raise ValueError("Create the release tag at this source revision before packaging.") from error
    if tagged != revision:
        raise ValueError("The release tag does not identify this source revision.")
    changes = git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", *TREES, "builder/HookshotBuilder",
                  "builder/setup", "scripts/package_builder.py", "scripts/make_setup_parts.py")
    for change in changes.split("\0"):
        if not change:
            continue
        name = change[3:]
        # A normal dotnet publish creates these; the SDK excludes them from source globbing.
        generated_gui = name.startswith("builder/HookshotBuilder/") and {"bin", "obj"}.intersection(Path(name).parts)
        if not (change.startswith("?? ") and generated_gui):
            raise ValueError("Commit the build sources before packaging a release: " + name)
    marker = staged / "builder-revision.txt"
    if not marker.is_file() or marker.read_text(encoding="utf-8-sig").strip().lower() != revision:
        raise ValueError("Publish the builder from this revision before packaging it.")
    if not (staged / "HookshotBuilder.exe").is_file():
        raise ValueError("The staged builder executable is missing.")
    # The sidecar alone is not proof: --no-build can refresh it beside old output.
    # The SDK embeds SourceRevisionId in both the apphost and managed DLL at compile time.
    for name in ("HookshotBuilder.exe", "HookshotBuilder.dll"):
        binary = staged / name
        if not binary.is_file() or embedded_revision(binary) != revision:
            raise ValueError("The staged GUI was compiled from a different source revision: " + name)
    tracked = git(repo, "ls-tree", "-rz", "--format=%(objectname) %(path)", "HEAD", "--", *TREES).split("\0")
    sources = {}
    for item in tracked:
        if item:
            blob, _, name = item.partition(" ")
            sources[name] = blob
    tools = staged / "tools"
    if not tools.is_dir():
        raise ValueError("The staged tools folder is missing.")
    staged_files = list(files_under(staged))
    for path in staged_files:
        parts = path.relative_to(staged).parts
        if (any(p in {"romfs", "exefs", ".hookshot_exefs", ".git"} for p in parts) or path.suffix.lower() == ".nup" or
                path.name in {"hookshot.exe", "image.bin", "hookshot_shaders.bin", "a64_table.c"} or
                re.fullmatch(r"code_(?:\d+|hot.*)\.c", path.name)):
            raise ValueError("Game data/generated game code must not be in a release: " + str(path))
        if path.is_relative_to(tools):
            name = path.relative_to(tools).as_posix()
            if any(name.startswith(prefix + "/") for prefix in TREES):
                if name not in sources and path.suffix in SOURCE_SUFFIXES:
                    raise ValueError("An untracked build source is staged: " + name)
                if name in sources and not source_matches(path.read_bytes(), sources[name], path.suffix in TEXT_SUFFIXES or not path.suffix):
                    raise ValueError("A staged source differs from this revision: " + name)
    # Tests and docs may be omitted, but the actual build source must be present.
    required = {name for name in sources if not {"tests", "test", "docs", "doc"}.intersection(Path(name).parts) and
                not Path(name).name.startswith("test_") and Path(name).suffix not in {".md", ".rst"}}
    missing = sorted(name for name in required if not (tools / name).is_file())
    if missing:
        raise ValueError("Build sources are missing from staging: " + ", ".join(missing[:5]))
    # These files identify the verified source tree, not the ZIP containing them.
    (tools / "version.txt").write_text(tag + "\n", encoding="utf-8", newline="\n")
    (tools / "VERSION").write_text(tag + "\n", encoding="utf-8", newline="\n")
    (tools / "revision.txt").write_text(revision + "\n", encoding="utf-8", newline="\n")
    identity = staged / "builder-identity.json"
    identity.write_text(json.dumps({"format": 1, "tag": tag, "revision": revision,
        "files": {name: hashlib.sha256((staged / name).read_bytes()).hexdigest()
                  for name in ("HookshotBuilder.exe", "HookshotBuilder.dll")}}, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return revision, sources, set(staged_files) | {tools / "version.txt", tools / "VERSION", tools / "revision.txt", identity}


def package(repo, staged, tag, output):
    repo, staged, output = Path(repo).resolve(), Path(staged).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(staged):
        raise ValueError("Choose a new ZIP outside the staging folder.")
    revision, sources, paths = verify_staged(repo, staged, tag)
    gui_hashes = json.loads((staged / "builder-identity.json").read_text(encoding="utf-8"))["files"]
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(paths):
                    data = verified_bytes(path, staged, sources, gui_hashes)
                    archive.writestr("HookshotBuilder/" + path.relative_to(staged).as_posix(), data)
    except BaseException:
        if created:
            output.unlink()
        raise
    return revision


def verified_bytes(path, staged, sources, gui_hashes):
    """Check the bytes actually written, including a source/GUI changed after the initial staging check."""
    path, staged = Path(path), Path(staged)
    data = path.read_bytes()
    tools = staged / "tools"
    if path.is_relative_to(tools):
        name = path.relative_to(tools).as_posix()
        if name in sources and not source_matches(data, sources[name], path.suffix in TEXT_SUFFIXES or not path.suffix):
            raise ValueError("A staged source changed during packaging: " + name)
    if path.parent == staged and path.name in gui_hashes and hashlib.sha256(data).hexdigest() != gui_hashes[path.name]:
        raise ValueError("A staged GUI changed during packaging: " + path.name)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print("Packaged source revision " + package(args.repo, args.staged, args.tag, args.out))
