"""Authored scalar/uniform and immediate/GPR roles exercise production native lowering."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0

    def run(command):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, creationflags=flags)
        print(result.stdout, end="", flush=True)
        result.check_returncode()

    with tempfile.TemporaryDirectory(prefix="native-register-roles-") as tmp:
        exe = Path(tmp) / "fixture.exe"
        run([args.cxx, "-std=c++20", "-O1", "-DREGISTER_ROLES_LOWERING_ONLY",
             "-I" + str(ROOT / "gpu/vk/compat"), "-I" + str(ROOT / "gpu/vk/eden"),
             str(ROOT / "tests/native_register_roles.cpp"), "-o", str(exe)])
        run([str(exe)])


if __name__ == "__main__":
    main()
