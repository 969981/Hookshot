"""scripts/release_parts.py previous finds the newest stable release's hookshot-setup.txt however many release candidates
came after it.

rc.29's Windows package stopped at "The latest release's setup list": the lookup read one page of 30 releases, and the
newest stable one (v2.2.0) was the 32nd, behind 29 release candidates, the nightly and the rc prerelease. Checked here
against a stand-in for the GitHub API: the stable release found on a later page, drafts, prereleases and --skip-tag
passed over, a release without the list passed over, the pages stopping at a short one, and none found said so.
"""
import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_parts  # noqa: E402


def rel(tag, pre=True, draft=False, setup=True):
    assets = [{"name": "hookshot-setup.txt", "browser_download_url": "https://example.test/%s/hookshot-setup.txt" % tag}]
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "assets": assets if setup else []}


class Previous(unittest.TestCase):
    def run_previous(self, releases, skip_tag=""):
        asked = []

        def get(url, api=False, tries=5):
            asked.append(url)
            if url.startswith("https://example.test/"):
                return ("setup list of " + url.split("/")[3]).encode()
            page = int(url.rsplit("page=", 1)[1])
            per = int(url.split("per_page=")[1].split("&")[0])
            return json.dumps(releases[(page - 1) * per:page * per]).encode()

        old = release_parts.get
        release_parts.get = get
        try:
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "previous.txt")
                code = release_parts.previous(argparse.Namespace(repo=["o/r"], out=out, skip_tag=skip_tag))
                text = open(out, encoding="utf-8").read() if os.path.exists(out) else None
        finally:
            release_parts.get = old
        return code, text, asked

    def test_the_stable_release_past_the_first_page(self):
        # (as on 2026-11-21: a draft rc, the nightly, 28 more prereleases, then 140 more before the stable one)
        releases = [rel("v2.3.0-rc.29", draft=True, setup=False), rel("nightly")]
        releases += [rel("v2.3.0-rc.%d" % n) for n in range(28, 0, -1)]
        releases += [rel("old-pre-%d" % n) for n in range(140)]
        releases += [rel("v2.2.0", pre=False), rel("v2.0.18", pre=False)]
        code, text, asked = self.run_previous(releases)
        self.assertEqual(code, 0)
        self.assertEqual(text, "setup list of v2.2.0")
        self.assertEqual(sum("page=" in u for u in asked), 2, asked)

    def test_drafts_skip_tag_and_releases_without_the_list_are_passed_over(self):
        releases = [rel("v3.0.0", pre=False, draft=True), rel("v2.9.0", pre=False), rel("v2.8.0", pre=False, setup=False),
                    rel("v2.7.0", pre=False)]
        code, text, _ = self.run_previous(releases, skip_tag="v2.9.0")
        self.assertEqual((code, text), (0, "setup list of v2.7.0"))

    def test_none_found(self):
        code, text, asked = self.run_previous([rel("v1.0.0-rc.%d" % n) for n in range(5)])
        self.assertEqual(code, 1)
        self.assertIsNone(text)
        self.assertEqual(sum("page=" in u for u in asked), 1, "a short page is the last")


if __name__ == "__main__":
    unittest.main()
