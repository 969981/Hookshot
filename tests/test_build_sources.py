"""The build's downloads from hosts other than GitHub are taken from this repository's copies on linux-parts first
(scripts/linux/deps.sh, scripts/windows/dav1d.sh), and scripts/mirror_build_sources.sh keeps those copies.

rc.27's Linux and Windows builds stopped when code.videolan.org answered 502 for dav1d's source. Checked here: every
download in the build scripts from a host other than GitHub is one deps.sh lists (--outside-sources), so its copy is
kept and tried first; deps.sh's fetch and dav1d.sh take the copy before the host; and mirror_build_sources.sh, run
against a stand-in gh and curl, leaves a copy that is there, uploads a new one only once it matches its pin (SHA-256
or what it unpacks to), refuses one that does not, and skips a host that does not answer.
"""
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

# (Linux only: the scripts are bash, run by Linux runners)
RUN_ALL_SKIP = {"windows": "the build-source scripts are bash, run by Linux runners; exercised on Linux"}

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "scripts" / "linux" / "deps.sh"
DAV1D = ROOT / "scripts" / "windows" / "dav1d.sh"
MIRROR = ROOT / "scripts" / "mirror_build_sources.sh"
GITHUB = re.compile(r"^https://(github\.com|api\.github\.com|raw\.githubusercontent\.com|uploads\.github\.com)/")


def outside_sources():
    out = subprocess.run(["bash", str(DEPS), "--outside-sources"], capture_output=True, text=True, check=True,
                         cwd=ROOT).stdout
    return [line.split(" ") for line in out.splitlines()]


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "bash")
class Listed(unittest.TestCase):
    def test_every_outside_download_has_a_copy(self):
        rows = outside_sources()
        self.assertTrue(rows)
        names = [r[0] for r in rows]
        self.assertEqual(len(names), len(set(names)), "two downloads would share a copy's name")
        listed = {r[1] for r in rows}
        for row in rows:
            self.assertEqual(len(row), 3, row)
            self.assertRegex(row[2], r"^(sha256:[0-9a-f]{64}|tree:[0-9a-f]{64}|-)$")
        # (every URL the scripts fetch, with deps.sh's variables put in, is GitHub's or listed)
        values = dict(re.findall(r"^([A-Z0-9_]+)=([^\s$(]\S*)", DEPS.read_text(encoding="utf-8"), re.M))
        for script in (DEPS, DAV1D):
            for url in re.findall(r"https://[^\s\"')]+", script.read_text(encoding="utf-8")):
                # (a regular expression's escaped dots read as dots: fetch_github_api matches GitHub's links)
                full = re.sub(r"\$\{?([A-Z0-9_]+)\}?", lambda m: values.get(m.group(1), m.group(0)),
                              url.replace("\\.", "."))
                if GITHUB.match(full):
                    continue
                self.assertIn(full, listed, "%s fetches %s from outside GitHub without a copy on linux-parts"
                              % (script.name, full))

    def test_the_copy_is_taken_first(self):
        deps = DEPS.read_text(encoding="utf-8")
        fetch = deps[deps.index("\nfetch() {"):]
        fetch = fetch[:fetch.index("\n}\n")]
        self.assertIn('from_copy "$1" "$DL/$2.part" ||', fetch)
        self.assertNotIn("code.videolan.org", deps.split("--outside-sources ]")[1].split("\nfi\n", 1)[1],
                         "a download from videolan bypasses fetch")
        self.assertNotIn("curl -fsSL -o \"$L/dav1d-COPYING\"", deps)
        dav1d = DAV1D.read_text(encoding="utf-8")
        self.assertLess(dav1d.index('"$SOURCES/$name"'), dav1d.index('"$url"'))


# The stand-in gh: `release view --json assets` lists ASSETS (one name a line) as the --jq asks, `release upload`
# appends to UPLOADS. The stand-in curl copies HOST/<the URL's last part> to -o, or fails as a down host does.
GH = r'''#!/bin/bash
case "$1 $2" in
    "release view") cat "$ASSETS" ;;
    "release upload") echo "$(basename "$4")" >> "$UPLOADS"; cp "$4" "$UPLOADS.d/" ;;
    *) echo "gh: $*" >&2; exit 2 ;;
esac
'''
CURL = r'''#!/bin/bash
out= url=
while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift 2 ;; --retry|--retry-delay|--proto) shift 2 ;; -*) shift ;; *) url=$1; shift ;; esac; done
src="$HOST/${url##*/}"
[ -f "$src" ] || { echo "curl: (22) The requested URL returned error: 502" >&2; exit 22; }
cp "$src" "$out"
'''


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "bash")
class Mirror(unittest.TestCase):
    def run_mirror(self, have, host_files):
        d = Path(self.tmp.name)
        (d / "bin").mkdir(exist_ok=True)
        for name, text in (("gh", GH), ("curl", CURL)):
            (d / "bin" / name).write_text(text, encoding="utf-8")
            (d / "bin" / name).chmod(0o755)
        host = d / "host"
        shutil.rmtree(host, ignore_errors=True)
        host.mkdir()
        urls = {row[0]: row[1] for row in self.rows}
        for name, data in host_files.items():
            (host / urls[name].rsplit("/", 1)[1]).write_bytes(data)
        (d / "assets").write_text("".join(n + "\n" for n in have), encoding="utf-8")
        uploads = d / "uploads"
        uploads.unlink(missing_ok=True)
        shutil.rmtree(str(uploads) + ".d", ignore_errors=True)
        Path(str(uploads) + ".d").mkdir()
        env = dict(os.environ, PATH="%s:%s" % (d / "bin", os.environ["PATH"]), ASSETS=str(d / "assets"),
                   UPLOADS=str(uploads), HOST=str(host), GITHUB_REPOSITORY="owner/repo")
        r = subprocess.run(["bash", str(MIRROR)], capture_output=True, text=True, env=env, cwd=ROOT)
        up = uploads.read_text(encoding="utf-8").split() if uploads.exists() else []
        return r, up

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rows = outside_sources()

    def tearDown(self):
        self.tmp.cleanup()

    def test_present_copies_are_left(self):
        r, up = self.run_mirror([row[0] for row in self.rows], {})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(up, [])

    def test_a_download_unlike_its_pin_is_refused(self):
        sha_rows = [row for row in self.rows if row[2].startswith("sha256:")]
        self.assertTrue(sha_rows)
        name = sha_rows[0][0]
        others = [row[0] for row in self.rows if row[0] != name]
        r, up = self.run_mirror(others, {name: b"not the release"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not the pinned download", r.stdout)
        self.assertEqual(up, [])

    def test_a_down_host_is_skipped_and_a_matching_copy_uploaded(self):
        # (a file with no pin ("-": dav1d's COPYING) is uploaded as it comes; the rest of the hosts are down)
        free = [row for row in self.rows if row[2] == "-"]
        self.assertTrue(free)
        name = free[0][0]
        r, up = self.run_mirror([], {name: b"BSD 2-Clause"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(up, [name])
        self.assertIn("did not answer", r.stdout)

    def test_a_tree_pin_is_checked_unpacked(self):
        row = next(row for row in self.rows if row[2].startswith("tree:"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            data = b"int main(void) { return 0; }\n"
            info = tarfile.TarInfo("dav1d-x/src.c")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        want = hashlib.sha256(("%s  ./src.c\n" % hashlib.sha256(data).hexdigest()).encode()).hexdigest()
        self.assertNotEqual("tree:" + want, row[2])
        others = [x[0] for x in self.rows if x[0] != row[0]]
        r, up = self.run_mirror(others, {row[0]: buf.getvalue()})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(want, r.stdout, "the tree is hashed as deps.sh's tree_is hashes it")
        self.assertEqual(up, [])


if __name__ == "__main__":
    unittest.main()
