"""The full Windows setup: Hookshot-<tag>-Windows-x64-full-setup.zip, every part a new install needs in one download, so that the
install itself downloads nothing (builder/setup/setup.c, beside_list and beside_part).

    python -I scripts/make_full_setup.py <dist folder> [--fetch] [--out <zip>]

<dist folder> holds the setup (HookshotBuilder.exe) and its list (hookshot-setup.txt), as the Windows builder workflow makes them
(scripts/make_setup_parts.py), and the parts it made itself (HookshotBuilder-<part>-<sha>.zip). A part the list names that is not
there (one reused from an earlier release, and llvm-mingw) is downloaded from the list's own link with --fetch, into
<dist folder>/full-parts. Every part is checked against the list's size and SHA-256 before it goes in, as the setup checks a
download; one that differs stops this.

The zip, in one folder of its name: HookshotBuilder.exe, hookshot-setup.txt, README.txt and parts/<each part's download name>.
The parts are zips already, so they are stored, not compressed again; the zip is the same bytes for the same files (sorted,
fixed dates). Updates are unchanged: the Builder's updater reads the newer release's list and downloads only what changed.
"""
import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
import zipfile

LIMIT = 2 * 1024 ** 3 - 1   # (GitHub's largest release file)
README = """Hookshot Builder {tag}, the full setup

Run HookshotBuilder.exe in this folder. It installs the Hookshot Builder from the files beside it, with nothing to download, and
starts it. You need your own copy of the game.

Keep the folder together: the setup reads hookshot-setup.txt and the parts folder beside it. After the install the folder can be
deleted. Later updates download only what changed.
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def read_list(path):
    """(tag, [(sha, size, name, url)], its bytes) of a hookshot-setup.txt, by setup.c's rules for a part line. Its bytes as
    they are: the zip holds the very list the release has, whatever its line ends"""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    tag, parts = None, []
    for line in text.splitlines():
        w = line.split()
        if w[:1] == ["tag"] and len(w) == 2:
            tag = w[1]
        elif w[:1] == ["part"]:
            if len(w) != 7 or len(w[1]) != 64 or w[1].strip("0123456789abcdef") or not w[2].isdigit() or not w[6].startswith("https://"):
                sys.exit("make_full_setup: not a part line setup.c reads: %s" % line)
            name = w[6].rsplit("/", 1)[-1]
            if not name or name.strip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-") or name in (".", ".."):
                sys.exit("make_full_setup: a part's download name setup.c does not take: %s" % name)
            parts.append((w[1], int(w[2]), name, w[6]))
    if not tag or not parts:
        sys.exit("make_full_setup: %s names no tag or no part" % path)
    if len({p[2] for p in parts}) != len(parts):
        sys.exit("make_full_setup: two parts share a download name")
    return tag, parts, raw


def checked(path, sha, size):
    return os.path.isfile(path) and os.path.getsize(path) == size and sha256(path) == sha


def fetch(url, dst, sha, size, opener=urllib.request.urlopen, tries=3):
    """url into dst, kept only when it is the part the list names"""
    last = None
    for _ in range(tries):
        try:
            with opener(urllib.request.Request(url, headers={"User-Agent": "Hookshot-release"}), timeout=120) as r, \
                    open(dst + ".part", "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if checked(dst + ".part", sha, size):
                os.replace(dst + ".part", dst)
                return
            last = "its size or SHA-256 is not the list's"
        except OSError as e:
            last = str(e)
        if os.path.exists(dst + ".part"):
            os.unlink(dst + ".part")
    sys.exit("make_full_setup: %s could not be had (%s)" % (url, last))


def gather(dist, parts, allow_fetch, opener=urllib.request.urlopen):
    """{name: path} of every part, each checked"""
    got = {}
    cache = os.path.join(dist, "full-parts")
    for sha, size, name, url in parts:
        for path in (os.path.join(dist, name), os.path.join(cache, name)):
            if checked(path, sha, size):
                got[name] = path
                break
            if os.path.isfile(path):
                sys.exit("make_full_setup: %s is not the part the list names (its size or SHA-256 differs)" % path)
        else:
            if not allow_fetch:
                sys.exit("make_full_setup: %s is not in %s (--fetch downloads it from the list's link)" % (name, dist))
            os.makedirs(cache, exist_ok=True)
            print("fetching %s (%d MB)" % (name, size >> 20), flush=True)
            fetch(url, os.path.join(cache, name), sha, size, opener)
            got[name] = os.path.join(cache, name)
    return got


def entry(z, name, src=None, data=None):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED if data is not None else zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    if data is not None:
        z.writestr(info, data)
        return
    with open(src, "rb") as f, z.open(info, "w", force_zip64=True) as out:
        shutil.copyfileobj(f, out, 1 << 20)


def make(dist, out=None, allow_fetch=False, opener=urllib.request.urlopen):
    setup = os.path.join(dist, "HookshotBuilder.exe")
    if not os.path.isfile(setup):
        sys.exit("make_full_setup: no HookshotBuilder.exe (the setup) in %s" % dist)
    tag, parts, text = read_list(os.path.join(dist, "hookshot-setup.txt"))
    got = gather(dist, parts, allow_fetch, opener)
    # ("setup" in the name: RecompLauncher picks a release's Windows file by the words in its name (assets.rs score), and
    # without it this zip and the RecompLauncher zip (Hookshot-<tag>-Windows-x64.zip) tie, so it asks its players which)
    top = "Hookshot-%s-Windows-x64-full-setup" % tag
    out = out or os.path.join(dist, top + ".zip")
    with zipfile.ZipFile(out + ".new", "w", allowZip64=True) as z:
        entry(z, top + "/HookshotBuilder.exe", setup)
        entry(z, top + "/hookshot-setup.txt", data=text)
        entry(z, top + "/README.txt", data=README.format(tag=tag).replace("\n", "\r\n").encode("utf-8"))
        for _, _, name, _ in sorted(parts, key=lambda p: p[2]):
            entry(z, top + "/parts/" + name, got[name])
    if os.path.getsize(out + ".new") > LIMIT:
        os.unlink(out + ".new")
        sys.exit("make_full_setup: the full setup is over GitHub's 2 GB a file")
    os.replace(out + ".new", out)
    print("%s: %d parts, %d MB" % (os.path.basename(out), len(parts), os.path.getsize(out) >> 20), flush=True)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("dist")
    p.add_argument("--fetch", action="store_true", help="download the parts the folder lacks from the list's links")
    p.add_argument("--out", help="the zip (default: <dist>/Hookshot-<tag>-Windows-x64-full-setup.zip)")
    a = p.parse_args(argv)
    make(a.dist, a.out, a.fetch)


if __name__ == "__main__":
    main()
