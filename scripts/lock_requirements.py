"""requirements-hashes.txt: every pin of requirements.txt and nx2/prompt_requirements.txt with the SHA-256 of each file PyPI
has for it, so that what the tools' Python installs (scripts/linux/deps.sh for the AppImage, scripts/release_parts.py for the
Windows builder's Python part) is checked by pip --require-hashes: a pin alone takes whatever file the index serves.

    python -I scripts/lock_requirements.py           writes it again from PyPI (after a pin changes)
    python -I scripts/lock_requirements.py --check   the file lists exactly the pins (no network; tests/test_pip_hashes.py)
"""
import argparse
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("requirements.txt", os.path.join("nx2", "prompt_requirements.txt"))
LOCK = "requirements-hashes.txt"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!-]+)$")


def pins(root=ROOT):
    """(name, version) of every pin, in the files' order"""
    out = []
    for rel in SOURCES:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.split("#")[0].strip()
                if not line or line.startswith("-r"):
                    continue
                m = PIN.match(line)
                if not m:
                    sys.exit("%s: %r is not a name==version pin" % (rel, line))
                out.append((m.group(1), m.group(2)))
    return out


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(root=ROOT):
    """{normalised name: (version, [sha256, ...])} of the lock file"""
    with open(os.path.join(root, LOCK), encoding="utf-8") as f:
        text = f.read().replace("\\\n", " ")
    out = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        head, *rest = line.split()
        m = PIN.match(head)
        if not m:
            sys.exit("%s: %r is not a pin" % (LOCK, head))
        hashes = [w[len("--hash=sha256:"):] for w in rest if w.startswith("--hash=sha256:")]
        out[norm(m.group(1))] = (m.group(2), hashes)
    return out


def write(root=ROOT):
    lines = ["# Written by scripts/lock_requirements.py from requirements.txt and nx2/prompt_requirements.txt: each pin with",
             "# the SHA-256 of every file PyPI has for it. pip install --require-hashes -r requirements-hashes.txt", ""]
    for name, version in pins(root):
        with urllib.request.urlopen("https://pypi.org/pypi/%s/%s/json" % (name, version), timeout=60) as r:
            info = json.load(r)
        digests = sorted({u["digests"]["sha256"] for u in info["urls"]})
        if not digests:
            sys.exit("PyPI lists no files for %s %s" % (name, version))
        lines.append("%s==%s \\" % (name, version))
        lines += ["    --hash=sha256:%s%s" % (d, " \\" if i + 1 < len(digests) else "") for i, d in enumerate(digests)]
    with open(os.path.join(root, LOCK), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def check(root=ROOT):
    have, want = locked(root), {norm(n): v for n, v in pins(root)}
    bad = sorted(set(have) ^ set(want)) + sorted(n for n in want if n in have and have[n][0] != want[n])
    bad += sorted(n for n, (_, h) in have.items() if not h or any(not re.fullmatch(r"[0-9a-f]{64}", x) for x in h))
    if bad:
        sys.exit("%s does not match the pins (%s): python -I scripts/lock_requirements.py" % (LOCK, ", ".join(bad)))
    print("%s: %d pins, each with its files' SHA-256" % (LOCK, len(want)))


def subset(names, root=ROOT):
    """The lock's lines for these pins (name==version), as a requirements file's text (scripts/release_parts.py)"""
    have = locked(root)
    out = []
    for p in names:
        name, version = p.split("==")
        v, hashes = have[norm(name)]
        if v != version:
            sys.exit("%s pins %s %s, the requirements %s" % (LOCK, name, v, version))
        out.append("%s==%s %s" % (name, version, " ".join("--hash=sha256:" + h for h in hashes)))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    check() if a.check else write()
