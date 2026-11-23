"""Which Hookshot a build is: the release it came from and a fingerprint of its sources.

The game prints it as the first line of hookshot_log.txt and in the Insert menu, so a report says what was run.

    python -I nx2/version.py            prints it for this folder

The release name comes from, in order: HOOKSHOT_VERSION; a VERSION file at the root (written when a release is packaged,
since the builder's tools folder has no git history); `git describe --tags --always --dirty` in a checkout, matching
only version tags (`v0.1.0`, not the branch-like tags from before the scheme). The
fingerprint is a hash of the sources in this folder (host/, gpu/, nx2/), line endings aside: every build from the
same release zip gives the same one, so even a build that says "unknown" can be traced to the zip it came from. (A
checkout and a release's tools folder hold different sets of files, so their fingerprints differ.)
"""
import hashlib
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("host", "gpu", "nx2", "game", "packs")   # (packs/: the game packs, whose pack.json the profiles are built from)
SOURCE_EXT = (".c", ".cc", ".cpp", ".h", ".hpp", ".inc", ".py", ".glsl", ".comp", ".vert", ".frag", ".spv", ".json")


def release(root=ROOT):
    name = os.environ.get("HOOKSHOT_VERSION", "").strip()
    if not name:
        for file in ("VERSION", "version.txt"):   # (version.txt: the release's tag, written into the builder's tools when it is packed)
            try:
                with open(os.path.join(root, file), encoding="utf-8") as f:
                    name = f.readline().strip()
            except OSError:
                pass
            if name:
                break
    if not name and os.path.exists(os.path.join(root, ".git")):
        try:
            r = subprocess.run(["git", "-C", root, "describe", "--tags", "--always", "--dirty", "--match", "v[0-9]*"],
                               capture_output=True, text=True, timeout=20)
            name = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return name or "unknown"


def release_key(tag):
    """A version to compare, by the Linux updater's order (builder/linux/linux_update.py release_key, which
    tests/test_game_library.py holds this one to): a release tag (v2.0.5, v2.1.0-rc.1) or git
    describe's v2.0.5-3-g1a2b3c4[-dirty], after v2.0.5 and before any later release. None for a version that names no
    release (a bare commit, dev, unknown). Prereleases in semver order: v2.1.0-rc.1 < v2.1.0-rc.2 < v2.1.0."""
    m = re.match(r"^(?=v|\d+\.)v?(\d+(?:\.\d+)*)(?:-(?!\d+-g|dirty$)([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?(-\d+-g[0-9a-f]+)?(-dirty)?$",
                 (tag or "").strip())
    if not m:
        return None
    pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in (m.group(2) or "").split(".") if p)
    return tuple(int(n) for n in m.group(1).split(".")), m.group(2) is None, pre, bool(m.group(3) or m.group(4))


def update_due(installed, engine=None):
    """Whether a game built with version installed is to be built again by this engine (engine, default release()): the
    Linux updater's rule for a game (linux_update.choose_mode's "update-game"): another version, unless that one is known
    to be newer than the engine, so a game is never taken back to an older release. A nightly whose build differs is
    updated too. (The Windows Builder's Updater.cs is not the same: it rebuilds a game whose version differs from its
    own, a newer one too. This one, for --update-all and the library, keeps the Linux rule and never downgrades.) None
    when it can't be told: no installed version (a folder built from a checkout, which writes none), or an engine that
    names no version."""
    engine = release() if engine is None else engine
    installed, engine = (installed or "").strip(), (engine or "").strip()
    if not installed or not engine or engine == "unknown":
        return None
    if installed == engine:
        return False
    have, new = release_key(installed), release_key(engine)
    return not (have and new and new < have)


def long_path(p):
    """(Windows) p as an extended-length path (\\\\?\\...): a builder unpacked in a deep folder has sources past 260
    characters (#1115: a portable launcher in Downloads), which a plain path can't open and a plain walk doesn't list.
    Python takes these without Windows' long-paths setting. Elsewhere, or one already: as is (nup.py's)"""
    if os.name != "nt" or not p:
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


def walk(top):
    """os.walk(top), folders past 260 characters included, each folder given back in top's own form (not \\\\?\\...): the
    paths go on to command lines and into keys as before. (A plain walk leaves such a folder out without a word.)"""
    long_top = long_path(top)
    for d, dirs, files in os.walk(long_top):
        rel = d[len(long_top):].lstrip("\\/")
        yield (os.path.join(top, rel) if rel else top), dirs, files   # (as os.walk joins them: top's own spelling kept)


def fingerprint(root=ROOT):
    h = hashlib.sha1(usedforsecurity=False)
    root = long_path(root)   # (walked, opened and made relative as one long path, so the names hashed are as before)
    for top in SOURCES:
        base = os.path.join(root, top)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                if not f.endswith(SOURCE_EXT):
                    continue
                p = os.path.join(d, f)
                with open(p, "rb") as src:
                    data = src.read().replace(b"\r\n", b"\n")
                h.update(os.path.relpath(p, root).replace("\\", "/").encode() + b"\0" + data + b"\0")
    return h.hexdigest()[:8]


DEFAULT_REPOSITORY = "notquiteog/Hookshot"


def repository(root=ROOT):
    """(hookshot) The GitHub repository this release came from, as owner/name: where the game's update check, the Builder's
    updater and the setup look for releases. HOOKSHOT_REPOSITORY, else a repository.txt at the root (written when a release
    is packaged, from the repository its CI ran in: scripts/release_parts.py package, scripts/linux/appimage.sh), else
    notquiteog/Hookshot. A fork's release then updates from the fork, and a source build from this repository as before."""
    name = os.environ.get("HOOKSHOT_REPOSITORY", "").strip()
    if not name:
        try:
            with open(os.path.join(root, "repository.txt"), encoding="utf-8") as f:
                name = f.readline().strip()
        except OSError:
            pass
    return name if re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", name or "") else DEFAULT_REPOSITORY


def repository_define(root=ROOT):
    return '-DHOOKSHOT_REPOSITORY="%s"' % repository(root)


def describe(root=ROOT):
    """'movie-sync (source 1a2b3c4d)': safe to pass as a C string literal on any command line."""
    text = "%s (source %s)" % (release(root), fingerprint(root))
    return re.sub(r"[^A-Za-z0-9 ._+()/-]", "_", text)[:80]


def define(root=ROOT):
    return '-DHOOKSHOT_VERSION="%s"' % describe(root)


if __name__ == "__main__":
    print(describe())
