"""The full setup's install from the files beside it (builder/setup/setup.c beside_list, beside_part), run with Windows' own
file APIs on authored files: the list beside the setup is used only with a parts folder, for a new install or the same
version, never against --tag or HOOKSHOT_SETUP_LIST; a part beside it is used only at the list's size and SHA-256.

The driver includes setup.c whole (wWinMain renamed) and calls the two functions; nothing is downloaded or installed.

    python -I -B tests/test_setup_beside_parts.py --cxx <clang++ of llvm-mingw>
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]
RUN_ALL_SKIP = {"linux": "Windows setup uses Win32 file APIs"}

DRIVER = r'''
#define wWinMain hookshot_setup_wWinMain
#include "%s"
#include <shellapi.h>

static void case_list(const wchar_t *versions, const wchar_t *cmdline, const char *name) {
    wcscpy(g_cmdline, cmdline);
    Mem m = {0};
    const int ok = beside_list(&m, versions);
    printf("list %%s %%d %%s\n", name, ok, ok && m.p && strstr(m.p, "tag v9.9.9\n") ? "tagged" : "-");
    free(m.p);
}

int main(void) {
    int argc;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv || argc != 3) return 2;
    const wchar_t *kit = argv[1], *versions = argv[2];
    wchar_t cur[MAX_PATH], parts[MAX_PATH];
    swprintf(cur, MAX_PATH, L"%%ls\\current.txt", versions);
    swprintf(parts, MAX_PATH, L"%%ls\\parts", kit);
    wcscpy(g_beside, kit);
    case_list(versions, L"", "new");
    write_small(cur, "v9.9.9");
    case_list(versions, L"", "same");
    write_small(cur, "v9.9.10");
    case_list(versions, L"", "other-installed");
    DeleteFileW(cur);
    case_list(versions, L"--tag v9.9.9 --update x", "tag-same");
    case_list(versions, L"--tag v9.9.10", "tag-other");
    _putenv("HOOKSHOT_SETUP_LIST=https://example.invalid/hookshot-setup.txt");
    case_list(versions, L"", "list-asked");
    _putenv("HOOKSHOT_SETUP_LIST=");
    wchar_t moved[MAX_PATH];
    swprintf(moved, MAX_PATH, L"%%ls\\parts-away", kit);
    MoveFileW(parts, moved);
    case_list(versions, L"", "no-parts");
    MoveFileW(moved, parts);

    static const char *const parts_txt[] = {%s};
    for (int i = 0; i < (int)(sizeof parts_txt / sizeof *parts_txt); i++) {
        Part p;
        memset(&p, 0, sizeof p);
        if (sscanf(parts_txt[i], "%%64s %%llu %%1023s", p.sha, &p.size, p.url) != 3) return 3;
        wchar_t path[MAX_PATH] = L"";
        const int ok = beside_part(&p, path);
        const wchar_t *base = wcsrchr(path, L'\\');
        printf("part %%d %%d %%ls\n", i, ok, ok && base ? base + 1 : L"-");
    }
    LocalFree(argv);
    return 0;
}
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--source", type=Path, default=ROOT / "builder/setup/setup.c")
    ap.add_argument("--run", default="", help="a program to run the driver with (wine, cross-compiling)")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="hookshot-setup-beside-") as temp:
        root = Path(temp)
        kit = root / "Hookshot full é"
        (kit / "parts").mkdir(parents=True)
        versions = root / "home" / "versions"
        versions.mkdir(parents=True)
        good = b"a part, as the list names it"
        (kit / "parts" / "HookshotBuilder-app-0123456789ab.zip").write_bytes(good)
        (kit / "parts" / "HookshotBuilder-cpm-0123456789ab.zip").write_bytes(b"x" * len(good))   # (its size, other bytes)
        sha = hashlib.sha256(good).hexdigest()
        base = "https://github.com/o/r/releases/download/builder-parts/"
        (kit / "hookshot-setup.txt").write_bytes(("tag v9.9.9\npart %s %d . - - %sHookshotBuilder-app-0123456789ab.zip\n"
                                                  "run HookshotBuilder/HookshotBuilder.exe\n" % (sha, len(good), base)).encode())
        parts = [
            (sha, len(good), base + "HookshotBuilder-app-0123456789ab.zip", 1),       # (the list's size and SHA-256)
            (sha, len(good) + 1, base + "HookshotBuilder-app-0123456789ab.zip", 0),   # (another size)
            (sha, len(good), base + "HookshotBuilder-cpm-0123456789ab.zip", 0),       # (its size, not its SHA-256)
            (sha, len(good), base + "HookshotBuilder-missing-0123456789ab.zip", 0),   # (not beside it)
            (sha, len(good), "https://github.com/o/r/releases/download/x/..", 0),     # (a name setup.c refuses)
        ]
        source = root / "driver.c"
        listed = ", ".join('"%s %d %s"' % (s, n, u) for s, n, u, _ in parts)
        source.write_text(DRIVER % (str(args.source.resolve()).replace("\\", "/"), listed), encoding="utf-8")
        exe = root / "driver.exe"
        subprocess.run([args.cxx, "-x", "c", str(source), "-x", "none", "-O1", "-lwinhttp", "-lbcrypt", "-lcomctl32",
                        "-lshell32", "-lole32", "-lgdi32", "-luser32", "-ladvapi32", "-lkernel32", "-w", "-o", str(exe)], check=True)
        out = subprocess.run(([args.run] if args.run else []) + [str(exe), str(kit), str(versions)], check=True,
                             capture_output=True, text=True).stdout.split("\n")
        got = {tuple(l.split()[:2]): l.split()[2:] for l in out if l.strip()}
        want_list = {"new": ["1", "tagged"], "same": ["1", "tagged"], "other-installed": ["0", "-"], "tag-same": ["1", "tagged"],
                     "tag-other": ["0", "-"], "list-asked": ["0", "-"], "no-parts": ["0", "-"]}
        for name, want in want_list.items():
            assert got.get(("list", name)) == want, ("list", name, got.get(("list", name)), out)
        for i, (_, _, url, ok) in enumerate(parts):
            want = [str(ok), url.rsplit("/", 1)[-1] if ok else "-"]
            assert got.get(("part", str(i))) == want, ("part", i, got.get(("part", str(i))), out)
    print("PASS: the full setup installs from the files beside it only when they are the list's and the install is new")


if __name__ == "__main__":
    main()
