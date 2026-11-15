"""The full Windows setup, Hookshot-<tag>-Windows-x64-full-setup.zip: a new install with nothing to download.

  - scripts/make_full_setup.py puts the setup, its list and every part the list names in one zip, each part checked against
    the list's size and SHA-256 (the ones this run made from the dist folder, the others downloaded from the list's links
    with --fetch), stored and the same bytes for the same files; a part that differs, or is missing without --fetch, stops it;
  - builder/setup/setup.c reads the list beside it before asking GitHub, and takes each part from beside it after the
    installed version's folders and before its download cache and GitHub (beside_list, beside_part: their behaviour is
    tests/test_setup_beside_parts.py's, on Windows);
  - a release gets it (the Windows builder workflow's release job, after the parts are on builder-parts), the release
    workflow checks it is there, and the notes' table names it;
  - the Builder's AMD FSR 4 tick takes AMD's runtime from the builder's own amd-fsr part, AMD's license with it, and
    downloads nothing (nx2/make_game.py seed_fsr_runtime, scripts/release_parts.py, the workflow's AMD step).

    python -I -B tests/test_full_setup.py
"""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


full = load("make_full_setup_under_test", ROOT / "scripts" / "make_full_setup.py")
BASE = "https://github.com/o/r/releases/download/builder-parts/"


class Served:
    """urlopen's stand-in: the bytes of each URL, and the URLs asked for"""
    def __init__(self, files):
        self.files, self.asked = files, []

    def __call__(self, req, timeout=None):
        self.asked.append(req.full_url)
        if req.full_url not in self.files:
            raise OSError("404 %s" % req.full_url)
        return io.BytesIO(self.files[req.full_url])


class Maker(unittest.TestCase):
    def dist(self, d, parts, made):
        """a dist folder: the setup, its list naming parts {name: bytes}, and the parts in made beside them"""
        Path(d, "HookshotBuilder.exe").write_bytes(b"MZ the setup")
        lines = ["tag v9.9.9"]
        for name, data in parts.items():
            lines.append("part %s %d . - - %s%s" % (hashlib.sha256(data).hexdigest(), len(data), BASE, name))
        lines.append("run HookshotBuilder/HookshotBuilder.exe")
        Path(d, "hookshot-setup.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for name in made:
            Path(d, name).write_bytes(parts[name])

    def test_every_part_goes_in_checked_and_the_missing_ones_are_fetched(self):
        parts = {"HookshotBuilder-app-aaaaaaaaaaaa.zip": b"app" * 50, "HookshotBuilder-emsdk-bbbbbbbbbbbb.zip": b"emsdk" * 70,
                 "llvm-mingw-20261104-ucrt-x86_64.zip": b"llvm" * 90}
        with tempfile.TemporaryDirectory() as d:
            self.dist(d, parts, ["HookshotBuilder-app-aaaaaaaaaaaa.zip"])
            served = Served({BASE + n: v for n, v in parts.items()})
            out = full.make(d, allow_fetch=True, opener=served)
            self.assertEqual(os.path.basename(out), "Hookshot-v9.9.9-Windows-x64-full-setup.zip")
            self.assertEqual(sorted(served.asked), sorted(BASE + n for n in parts if "app" not in n))   # (made here: not fetched)
            top = "Hookshot-v9.9.9-Windows-x64-full-setup/"
            with zipfile.ZipFile(out) as z:
                self.assertEqual(sorted(z.namelist()), sorted([top + "HookshotBuilder.exe", top + "hookshot-setup.txt", top + "README.txt"]
                                                              + [top + "parts/" + n for n in parts]))
                for n, data in parts.items():
                    self.assertEqual(z.read(top + "parts/" + n), data)
                    self.assertEqual(z.getinfo(top + "parts/" + n).compress_type, zipfile.ZIP_STORED)   # (zips already)
                self.assertEqual(z.read(top + "hookshot-setup.txt"), Path(d, "hookshot-setup.txt").read_bytes())
                self.assertIn(b"HookshotBuilder.exe", z.read(top + "README.txt"))
            first = Path(out).read_bytes()
            again = full.make(d, allow_fetch=True, opener=Served({}))   # (the fetched ones are kept: nothing asked for)
            self.assertEqual(Path(again).read_bytes(), first)

    def test_recomplauncher_keeps_taking_its_own_zip(self):
        # (RecompLauncher scores a release's files by the words in their names, elripalda/RecompLauncher assets.rs score:
        # windows +50, x64 +20, a zip +12, and setup, installer or install -15. Its own zip and this one tie but for that)
        words = lambda n: [w for w in n.lower().replace("x86_64", "x64").replace(".", "-").split("-") if w]
        full_words, own = words("Hookshot-v9.9.9-Windows-x64-full-setup.zip"), words("Hookshot-v9.9.9-Windows-x64.zip")
        self.assertIn("setup", full_words)
        self.assertFalse({"setup", "installer", "install"} & set(own))

    def test_the_list_goes_in_as_its_bytes(self):
        # (Windows line ends too: the setup reads either, and the zip holds the release's very list)
        parts = {"HookshotBuilder-app-aaaaaaaaaaaa.zip": b"app" * 50}
        with tempfile.TemporaryDirectory() as d:
            self.dist(d, parts, list(parts))
            listed = Path(d, "hookshot-setup.txt")
            listed.write_bytes(listed.read_bytes().replace(b"\n", b"\r\n"))
            with zipfile.ZipFile(full.make(d)) as z:
                self.assertEqual(z.read("Hookshot-v9.9.9-Windows-x64-full-setup/hookshot-setup.txt"), listed.read_bytes())

    def test_a_part_that_differs_or_is_missing_stops_it(self):
        parts = {"HookshotBuilder-app-aaaaaaaaaaaa.zip": b"app" * 50, "HookshotBuilder-cpm-cccccccccccc.zip": b"cpm" * 30}
        with tempfile.TemporaryDirectory() as d:
            self.dist(d, parts, ["HookshotBuilder-app-aaaaaaaaaaaa.zip"])
            with self.assertRaises(SystemExit):   # (missing, and no --fetch)
                full.make(d)
            with self.assertRaises(SystemExit):   # (served, but not the part the list names)
                full.make(d, allow_fetch=True, opener=Served({BASE + "HookshotBuilder-cpm-cccccccccccc.zip": b"cpm" * 31}))
            self.assertFalse(Path(d, "full-parts", "HookshotBuilder-cpm-cccccccccccc.zip").exists())
            Path(d, "HookshotBuilder-app-aaaaaaaaaaaa.zip").write_bytes(b"app" * 51)   # (made here, and not the list's)
            with self.assertRaises(SystemExit):
                full.make(d, allow_fetch=True, opener=Served({BASE + n: v for n, v in parts.items()}))
            self.assertFalse(any(Path(d).glob("*-full.zip*")))

    def test_a_list_setup_c_would_refuse_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("part %s 3 . - - http://x/a.zip" % ("0" * 64), "part %s 3 . - - https://x/.." % ("0" * 64),
                        "part %s 3 . - - https://x/a.zip" % ("A" * 64)):
                Path(d, "l.txt").write_text("tag v1\n%s\n" % bad)
                with self.subTest(bad=bad), self.assertRaises(SystemExit):
                    full.read_list(os.path.join(d, "l.txt"))


class SetupOrder(unittest.TestCase):
    """setup.c: the list beside it before GitHub, a part beside it before the cache and the download"""
    def test_the_files_beside_come_first(self):
        src = (ROOT / "builder" / "setup" / "setup.c").read_text(encoding="utf-8")
        work = src[src.index("static DWORD WINAPI work("):src.index("static LRESULT CALLBACK wndproc(")]
        self.assertLess(work.index("ok = beside_list(&man, versions);"), work.index("http_get(manifest_url()"))
        loop = work[work.index("for (int i = 0; i < nparts; i++) {"):]
        self.assertLess(loop.index("reuse_part(versions, tag, p, install)"), loop.index("beside_part(p, beside)"))
        self.assertLess(loop.index("beside_part(p, beside)"), loop.index("file_sha256(zip, have)"))
        self.assertLess(loop.index("beside_part(p, beside)"), loop.index("http_get("))
        self.assertIn("beside_init();", src[src.index("int WINAPI wWinMain("):])
        self.assertNotIn("DeleteFileW(beside", work)   # (the player's files beside the setup are never removed)


class Release(unittest.TestCase):
    def test_a_release_gets_it_and_is_checked_for_it(self):
        wb = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
        job = wb[wb.index("  release:\n    name: Release assets"):]
        made = job.index("python3 -I scripts/make_full_setup.py dist --fetch")
        self.assertLess(job.index("New parts to builder-parts"), made)   # (the parts it fetches are there first)
        self.assertLess(job.index("Every part in the setup list downloads"), made)   # (and every link answers)
        upload = job[job.index("name: Setup and its list to the release"):]
        self.assertIn("dist/Hookshot-*-Windows-x64-full-setup.zip", upload[:upload.index("\n      - ")])
        self.assertIn("subject-path: dist/Hookshot-*-Windows-x64-full-setup.zip", job)
        rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        check = rel[rel.index("scripts/release_files.sh check"):rel.index("x86_64.AppImage.zsync")]
        self.assertIn('"Hookshot-$TAG-Windows-x64-full-setup.zip"', check)

    def test_every_run_installs_it_with_the_network_off(self):
        wb = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
        job = wb[wb.index("  offline:\n"):wb.index("  vulkan:\n")]
        self.assertIn("needs: [package]", job)
        self.assertIn("python -I scripts/make_full_setup.py dist --fetch", job)   # (with the network: the reused parts)
        blocked = job.index("Set-NetFirewallProfile -All -Enabled True -DefaultOutboundAction Block")
        self.assertLess(job.index("make_full_setup.py"), blocked)
        self.assertLess(blocked, job.index("Invoke-WebRequest https://github.com"))   # (the block proven first)
        self.assertLess(job.index("Invoke-WebRequest https://github.com"), job.index("HookshotBuilder.exe"))
        self.assertLess(job.index("HookshotBuilder.exe"), job.index("-DefaultOutboundAction Allow"))
        for check in ("installed.txt", "no Visual Studio needed", "not a NUP package", "Could not reach GitHub"):
            self.assertIn(check, job)

    @unittest.skipIf(os.name == "nt", "the release scripts are bash, run by Linux runners (tests/test_release_scripts.py too)")
    def test_the_table_names_it_for_a_release_only(self):
        env = dict(os.environ, GH_REPO="o/r", HOOKSHOT_PRODUCT="Hookshot")   # (product_name.sh: no python3 on Windows)
        run = lambda tag: subprocess.run(["bash", str(ROOT / "scripts" / "release_files.sh"), "table", tag], capture_output=True,
                                         text=True, env=env, check=True).stdout
        self.assertIn("`Hookshot-v9.9.9-Windows-x64-full-setup.zip`: unzip it and run `HookshotBuilder.exe`", run("v9.9.9"))
        self.assertNotIn("-full.zip", run("nightly"))


class AmdFsr(unittest.TestCase):
    def ffx(self, folder_files):
        """scripts/ffx_sdk.py's table with digests of authored files"""
        files = [(p, hashlib.sha256(folder_files[n]).hexdigest(), n) for p, _, n in load("ffx_t", ROOT / "scripts" / "ffx_sdk.py").FILES]
        sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return types.SimpleNamespace(FILES=files, sha256=sha, complete=lambda f: all(
            os.path.isfile(os.path.join(f, n)) and sha(os.path.join(f, n)) == d for _, d, n in files))

    def seed(self):
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        code = src[src.index("def seed_fsr_runtime("):src.index("def fetch_fsr_runtime(")]
        return code

    def test_the_builders_own_copy_is_taken_and_only_the_pinned_files(self):
        names = ["amd_fidelityfx_loader_vk.dll", "amd_fidelityfx_upscaler_vk.dll", "LICENSE-AMD-FSR-SDK.md"]
        data = {n: ("pinned " + n).encode() for n in names}
        with tempfile.TemporaryDirectory() as root:
            tools, cache = Path(root, "tools"), Path(root, "ffx-sdk")
            (tools / "amd-fsr").mkdir(parents=True)
            for n in names:
                (tools / "amd-fsr" / n).write_bytes(data[n])
            g = {"os": os, "shutil": __import__("shutil"), "ROOT": str(tools)}
            exec(self.seed(), g)
            ffx = self.ffx(data)
            self.assertTrue(g["seed_fsr_runtime"](ffx, str(cache)))
            self.assertEqual({n: (cache / n).read_bytes() for n in names}, data)
            (tools / "amd-fsr" / names[1]).write_bytes(b"another upscaler")   # (not the pinned one: not taken)
            (cache / names[1]).unlink()
            self.assertFalse(g["seed_fsr_runtime"](ffx, str(cache)))
            self.assertFalse((cache / names[1]).exists())

    def test_the_tick_seeds_before_it_downloads(self):
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        fetch = src[src.index("def fetch_fsr_runtime("):src.index("def place_fsr_runtime(")]
        self.assertLess(fetch.index("accept_amd_license"), fetch.index("seed_fsr_runtime(ffx, ffx_folder())"))
        self.assertLess(fetch.index("seed_fsr_runtime(ffx, ffx_folder())"), fetch.index("ffx.fetch("))

    def test_the_part_carries_amds_license_under_the_pinned_name(self):
        wb = (ROOT / ".github" / "workflows" / "windows-builder.yml").read_text(encoding="utf-8")
        self.assertIn("Kits/FidelityFX/docs\n", wb)
        self.assertIn('git -C _amd cat-file blob HEAD:Kits/FidelityFX/docs/license.md > "$RUNNER_TEMP/amd-fsr/LICENSE-AMD-FSR-SDK.md"', wb)
        self.assertIn("f.complete(sys.argv[1])", wb)   # (the three are the pinned ones, or the package stops)
        parts = (ROOT / "scripts" / "release_parts.py").read_text(encoding="utf-8")
        self.assertIn('for name in ("LICENSE.txt", "LICENSE-AMD-FSR-SDK.md"):', parts)


if __name__ == "__main__":
    unittest.main()
