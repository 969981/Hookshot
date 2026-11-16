"""A portable Builder home that other accounts on the PC can write is said once, in plain words, and recorded in the home,
by the setup (builder/setup/shared_folder.h, setup.c) and the Builder (builder/HookshotBuilder/SharedFolder.cs, App.xaml.cs);
nothing is changed, so a portable home still moves between PCs. On Windows, with real NTFS ACLs: a folder only this
account can write says nothing; the same folder with Everyone granted Modify (icacls) is said, once, and its record names it.
Everywhere: the setup and the Builder use the same words, the same record and the same groups.

    python -I -B tests/test_portable_shared_warning.py --cxx <clang++ of llvm-mingw>
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]
TEXT = ("Other accounts on this PC can change files in %s. If you share this PC, move the Builder to a folder only you can "
        "write.")

DRIVER = r'''
#include "%s"
#include <shellapi.h>
#include <stdio.h>
int main(void) {
    int argc;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv || argc != 2) return 2;
    wchar_t who[64], text[1024];
    const int can = hb_others_can_write(argv[1], who, 64);
    const int due = hb_shared_warning_due(argv[1], text, 1024);
    const int again = hb_shared_warning_due(argv[1], text, 1024);
    printf("%%d %%d %%d %%ls\n", can, due, again, can ? who : L"-");
    LocalFree(argv);
    return 0;
}
'''


def sources():
    setup = (ROOT / "builder" / "setup" / "setup.c").read_text(encoding="utf-8")
    header = (ROOT / "builder" / "setup" / "shared_folder.h").read_text(encoding="utf-8")
    cs = (ROOT / "builder" / "HookshotBuilder" / "SharedFolder.cs").read_text(encoding="utf-8")
    app = (ROOT / "builder" / "HookshotBuilder" / "App.xaml.cs").read_text(encoding="utf-8")
    assert '#define HB_SHARED_TEXT L"%s"' % (TEXT % "%ls") in header, "the setup's words are not the agreed ones"
    assert '$"%s"' % (TEXT % "{home}") in cs, "the Builder's words are not the agreed ones"
    assert '".hookshot-shared-folder.txt"' in cs and 'L".hookshot-shared-folder.txt"' in header, "two records"
    for c, w in (("WinWorldSid", "WorldSid"), ("WinBuiltinUsersSid", "BuiltinUsersSid"), ("WinAuthenticatedUserSid", "AuthenticatedUserSid")):
        assert c in header and w in cs, "the groups differ: %s" % w
    assert "DRIVE_REMOVABLE" in header and "FILE_PERSISTENT_ACLS" in header and "DriveType.Removable" in cs and "exFAT" in cs
    assert "hb_shared_warning_due(g_home.root" in setup and "if (g_home_ok && g_home.portable)" in setup
    assert "BuilderHome.Portable ? SharedFolder.Due(BuilderHome.Base)" in app
    for text in (header, cs):   # (nothing changed: the ACL is read, never written)
        assert "SetNamedSecurityInfo" not in text and "SetAccessControl" not in text


def windows(cxx):
    with tempfile.TemporaryDirectory(prefix="hookshot-shared-") as temp:
        root = Path(temp)
        src = root / "driver.c"
        src.write_text(DRIVER % str(ROOT / "builder" / "setup" / "shared_folder.h").replace("\\", "/"), encoding="utf-8")
        exe = root / "driver.exe"
        subprocess.run([cxx, "-x", "c", str(src), "-x", "none", "-w", "-ladvapi32", "-lshell32", "-o", str(exe)], check=True)
        home = Path(os.environ.get("LOCALAPPDATA", temp)) / ("hookshot-shared-test-%d" % os.getpid())
        home.mkdir()
        try:
            # (only this account, SYSTEM and Administrators, inherited from nothing)
            me = os.environ["USERNAME"]
            subprocess.run(["icacls", str(home), "/inheritance:r", "/grant:r", "%s:(OI)(CI)F" % me, "*S-1-5-18:(OI)(CI)F",
                            "*S-1-5-32-544:(OI)(CI)F"], check=True, capture_output=True)
            out = subprocess.run([str(exe), str(home)], check=True, capture_output=True, text=True).stdout.split()
            assert out[:3] == ["0", "0", "0"], "a folder only this account can write was said: %r" % out
            subprocess.run(["icacls", str(home), "/grant", "*S-1-1-0:(OI)(CI)M"], check=True, capture_output=True)
            out = subprocess.run([str(exe), str(home)], check=True, capture_output=True, text=True).stdout.split()
            assert out[:4] == ["1", "1", "0", "Everyone"], "Everyone: Modify was not said once: %r" % out
            record = (home / ".hookshot-shared-folder.txt").read_text(encoding="utf-8")
            assert "Everyone may write " + str(home) in record, record
        finally:
            subprocess.run(["icacls", str(home), "/reset", "/T"], capture_output=True)
            for f in home.iterdir():
                f.unlink()
            home.rmdir()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cxx", default="clang++")
    a = ap.parse_args()
    sources()
    if sys.platform == "win32":
        windows(a.cxx)
        print("PASS: a portable home others can write is said once, recorded; one only you can write says nothing")
    else:
        print("PASS: the setup and the Builder say the same, once, and change nothing (the ACL checks run on Windows)")


if __name__ == "__main__":
    main()
