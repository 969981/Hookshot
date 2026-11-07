"""Compile/run the portable update policy and its prior tag-only negative."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--compile-host", action="store_true", help="also compile the Windows offer, without linking or running it")
    args = parser.parse_args()
    msvc = Path(args.cc).stem.lower() == "cl"
    with tempfile.TemporaryDirectory(prefix="hookshot-update-c-") as temporary:
        work = Path(temporary)
        for original in (False, True):
            exe = work / ("original.exe" if original else "identity.exe")
            source = ROOT / "tests/update_identity_test.c"
            if msvc:
                command = [args.cc, "/nologo", "/TC", "/std:c11", "/W4", "/WX", "/D_CRT_SECURE_NO_WARNINGS",
                           "/Fo" + str(work / ("original.obj" if original else "identity.obj")), "/Fe" + str(exe)]
                if original:
                    command.append("/DHOS_TEST_ORIGINAL_TAG_ONLY")
            else:
                command = [args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(exe)]
                if original:
                    command.append("-DHOS_TEST_ORIGINAL_TAG_ONLY")
            subprocess.run(command + [str(source)], check=True)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            if original:
                if result.returncode != 1 or "FAIL same tag corrected source is offered" not in result.stderr:
                    raise AssertionError("The original tag-only guard did not fail the intended regression: " + result.stderr)
                print("Original tag-only guard failed the corrected-source case as expected.")
            else:
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                print(result.stdout.strip())
        if args.compile_host:
            source = ROOT / "host/hos_update.c"
            obj = work / "hos_update.obj"
            command = ([args.cc, "/nologo", "/TC", "/std:c11", "/W4", "/WX", "/D_CRT_SECURE_NO_WARNINGS", "/c", "/Fo" + str(obj)] if msvc else
                       [args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", "-c", "-o", str(obj)])
            subprocess.run(command + [str(source)], check=True)
            print("Native update offer compiled; no network or UI was run.")


if __name__ == "__main__":
    main()
