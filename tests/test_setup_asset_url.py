"""The setup finds a release asset through the GitHub API when a release page's link answers with an error (issue 544)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "builder" / "setup" / "setup.c"

FIXTURE = r'''#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@HELPERS@
static void check(const char *json, const char *name, const char *want) {
    char out[256];
    if (!release_asset_from_json(json, strlen(json), name, out, sizeof out)) {
        fprintf(stderr, "no URL for %s\n", name);
        exit(1);
    }
    if (strcmp(out, want)) {
        fprintf(stderr, "%s: got %s, want %s\n", name, out, want);
        exit(1);
    }
}
int main(void) {
    const char *release =
        "{\n"
        "  \"url\": \"https://api.github.com/repos/o/r/releases/1\",\n"
        "  \"name\": \"a release title\",\n"
        "  \"assets\": [\n"
        "    {\n"
        "      \"url\": \"https://api.github.com/repos/o/r/releases/assets/1\",\n"
        "      \"name\": \"HookshotBuilder.exe\",\n"
        "      \"uploader\": {\"login\": \"o\", \"url\": \"https://api.github.com/users/o\"}\n"
        "    },\n"
        "    {\n"
        "      \"url\": \"https://api.github.com/repos/o/r/releases/assets/2\",\n"
        "      \"name\": \"hookshot-setup.txt\",\n"
        "      \"browser_download_url\": \"https://github.com/o/r/releases/download/t/hookshot-setup.txt\"\n"
        "    }\n"
        "  ]\n"
        "}";
    check(release, "hookshot-setup.txt", "https://api.github.com/repos/o/r/releases/assets/2");
    check(release, "HookshotBuilder.exe", "https://api.github.com/repos/o/r/releases/assets/1");
    const char *compact =
        "{\"url\":\"https://api.github.com/repos/o/r/releases/1\",\"name\":\"title\",\"assets\":["
        "{\"url\":\"https://api.github.com/repos/o/r/releases/assets/3\",\"name\":\"part.zip\"}]}";
    check(compact, "part.zip", "https://api.github.com/repos/o/r/releases/assets/3");
    char out[64];
    if (release_asset_from_json(compact, strlen(compact), "missing.zip", out, sizeof out)) {
        fputs("a missing asset found a URL\n", stderr);
        return 1;
    }
    return 0;
}
'''


class SetupAssetUrlTests(unittest.TestCase):
    def test_release_json_selects_the_named_asset(self):
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not compiler:
            self.skipTest("no C compiler")
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("/* The string after")
        end = source.index("/* The GitHub API's URL")
        with tempfile.TemporaryDirectory(prefix="hookshot-setup-asset-") as tmp:
            fixture = Path(tmp) / "asset_url_test.c"
            binary = Path(tmp) / "asset_url_test"
            fixture.write_text(FIXTURE.replace("@HELPERS@", source[start:end]), encoding="utf-8")
            build = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(fixture), "-o", str(binary)],
                cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=1))
