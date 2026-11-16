"""Files and answers from outside, taken with care (audit 2026-11-14, PR-E):

  - an NSO header whose sizes are larger than any module, or outside the file, is refused before anything is allocated
    (third_party/a64-kit/tools/nso.py), not 4 GB allocated for it;
  - the offline translator is built into and run from the user's own cache, never /tmp, and only when it is theirs alone
    (nx2/bake_ds/bake_all.py, build.sh);
  - the team room's session token goes over https only (or this PC's loopback), never to where a redirect points, and a
    .well-known answer naming http:// is not taken (scripts/team_chat.py; test_requests.py's GitHub token the same);
  - the Windows setup fetches over https only, whatever names the URL (builder/setup/setup.c http_get);
  - the tools' Python installs every package checked by its SHA-256 (requirements-hashes.txt, scripts/lock_requirements.py).

    python -I -B tests/test_untrusted_inputs.py
"""
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
try:   # (lz4 unpacks NSO segments: nothing here does, and a runner without it still runs these checks)
    import lz4.block  # noqa: F401
except ImportError:
    import types
    sys.modules["lz4"] = types.ModuleType("lz4")
    sys.modules["lz4.block"] = sys.modules["lz4"].block = types.ModuleType("lz4.block")
sys.path[:0] = [str(ROOT / "third_party" / "a64-kit" / "tools"), str(ROOT / "scripts"), str(ROOT / "nx2" / "bake_ds")]


class Nso(unittest.TestCase):
    def header(self, size):
        raw = bytearray(0x100)
        raw[:4] = b"NSO0"
        struct.pack_into("<III", raw, 0x10, 0x100, 0, size)   # (.text: in the file at 0x100, memory 0, size)
        return bytes(raw)

    def test_a_header_larger_than_any_module_is_refused(self):
        import nso
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "main")
            for size in (0xFFFFFFFF, nso.MAX_IMAGE + 1, 16):   # (16: more than the file holds)
                path.write_bytes(self.header(size))
                with self.subTest(size=size), self.assertRaises(ValueError):
                    nso.Nso(str(path)) if hasattr(nso, "Nso") else nso.load(str(path))


class BakeDs(unittest.TestCase):
    def test_built_in_the_users_cache_and_run_only_when_theirs(self):
        import bake_all
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/home/u/.cache"}):
            self.assertEqual(bake_all.default_build_dir(), os.path.join("/home/u/.cache", "hookshot-ds"))
        script = (ROOT / "nx2" / "bake_ds" / "build.sh").read_text(encoding="utf-8")
        self.assertIn("OUT=${OUT:-${XDG_CACHE_HOME:-$HOME/.cache}/hookshot-ds}", script)
        self.assertNotIn("/tmp}/hookshot-ds", script)
        if os.name == "posix":
            with tempfile.TemporaryDirectory() as d:
                exe = Path(d, "bake_ds")
                exe.write_bytes(b"#!/bin/sh\n")
                os.chmod(d, 0o700)
                self.assertTrue(bake_all.own_file(str(exe)))
                os.chmod(d, 0o777)   # (anyone could have swapped it)
                self.assertFalse(bake_all.own_file(str(exe)))
                os.chmod(d, 0o700)


class ChatToken(unittest.TestCase):
    def test_https_only_and_never_carried_by_a_redirect(self):
        import team_chat
        seen = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            seen.append(req)
            return Answer(b"{}")
        with mock.patch.object(team_chat.urllib.request, "urlopen", urlopen):
            team_chat.request("GET", "https://matrix.example/_matrix/client/v3/x", token="secret")
            self.assertNotIn("Authorization", seen[-1].headers)   # (urllib copies these on a redirect)
            self.assertEqual(seen[-1].unredirected_hdrs.get("Authorization"), "Bearer secret")
            with self.assertRaises(team_chat.ChatError):
                team_chat.request("GET", "http://matrix.example/_matrix/client/v3/x", token="secret")
            team_chat.request("GET", "http://127.0.0.1:8008/_matrix/client/v3/x", token="secret")   # (loopback)
        with mock.patch.object(team_chat, "request", return_value={"m.homeserver": {"base_url": "http://evil.example"}}):
            self.assertEqual(team_chat.homeserver("magicbean.dev"), "https://magicbean.dev")
        with self.assertRaises(team_chat.ChatError):
            team_chat.homeserver("http://magicbean.dev")
        src = (ROOT / "scripts" / "test_requests.py").read_text(encoding="utf-8")
        self.assertIn('req.add_unredirected_header("Authorization", "Bearer " + token)', src)
        self.assertNotIn('req.add_header("Authorization"', src)


class SetupHttps(unittest.TestCase):
    def test_the_setup_fetches_over_https_only(self):
        src = (ROOT / "builder" / "setup" / "setup.c").read_text(encoding="utf-8")
        get = src[src.index("static int http_get("):src.index("static int mem_sink(")]
        self.assertLess(get.index("WinHttpCrackUrl"), get.index("if (uc.nScheme != INTERNET_SCHEME_HTTPS) return 0;"))
        self.assertLess(get.index("if (uc.nScheme != INTERNET_SCHEME_HTTPS) return 0;"), get.index("WinHttpOpen("))


class PipHashes(unittest.TestCase):
    def test_every_pin_is_installed_by_its_hash(self):
        import lock_requirements
        lock_requirements.check()   # (the lock lists exactly the pins, each with SHA-256s)
        deps = (ROOT / "scripts" / "linux" / "deps.sh").read_text(encoding="utf-8")
        self.assertIn("--require-hashes -r requirements-hashes.txt", deps)
        parts = (ROOT / "scripts" / "release_parts.py").read_text(encoding="utf-8")
        self.assertIn('"--require-hashes", "-r", locked', parts)
        want = lock_requirements.subset(["archspec==0.2.7"])
        self.assertTrue(want.startswith("archspec==0.2.7 --hash=sha256:"), want)


if __name__ == "__main__":
    unittest.main()
