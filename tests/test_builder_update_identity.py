"""Run the real managed identity/transport/folder fixtures, without UI or a network update."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default="dotnet")
    args = parser.parse_args()
    project = ROOT / "tests/update_identity/UpdateIdentityTests.csproj"
    with tempfile.TemporaryDirectory(prefix="hookshot-update-managed-") as temporary:
        work = Path(temporary)
        properties = ["-p:BaseIntermediateOutputPath=" + (work / "obj").as_posix() + "/",
                      "-p:MSBuildProjectExtensionsPath=" + (work / "obj").as_posix() + "/",
                      "-p:BaseOutputPath=" + (work / "bin").as_posix() + "/"]
        subprocess.run([args.dotnet, "restore", str(project), "--configfile",
                        str(project.parent / "NuGet.Config"), *properties], cwd=ROOT, check=True)
        subprocess.run([args.dotnet, "build", str(project), "--configuration", "Release", "--no-restore",
                        "-maxcpucount:1", "-nodeReuse:false", *properties], cwd=ROOT, check=True)
        assembly = work / "bin/Release/net8.0/UpdateIdentityTests.dll"
        assert assembly.is_file(), "The actual managed fixture was not built into its isolated output."
        subprocess.run([args.dotnet, str(assembly)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
