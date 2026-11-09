"""DLSS's no-SDK contract and the licensing gate it rests on. No game, no GPU, no NVIDIA SDK and no
DLSS runtime.

Three things are checked, and each one is a rule from CONTRIBUTING.md or docs/upscaling.md that
would otherwise only be a comment:

1. gpu/vk/vk_dlss.cpp compiles and links with no NVIDIA SDK, and the backend it makes reports DLSS
   unavailable without allocating, recording or handing back anything. tests/dlss_contract_test.cpp
   drives the real translation unit; its own header says why its checks read the log.
2. With the gate on, the file cannot compile without NVIDIA's own headers. That is the proof that
   the NGX ABI is *not* written out in this tree: if someone replaces the gated include with a
   hand-copied declaration, this compile starts succeeding and this check fails.
3. Every NGX name in gpu/vk/vk_dlss.cpp and gpu/vk/vk_dlss.h is inside that gate, and no NVIDIA SDK
   header or library is tracked in the repository.

python -I -B tests/test_dlss_contract.py [--cxx clang++] [--headers <vulkan include dir>]

clang++ rather than g++: gpu/render/rx_nvn.h, which vk_api.h pulls in, uses C's _Static_assert,
which clang accepts in C++ and GCC does not. The renderer itself is built with clang.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gpu/vk/vk_dlss.cpp"
HEADER = ROOT / "gpu/vk/vk_dlss.h"
DRIVER = ROOT / "tests/dlss_contract_test.cpp"
GATE = "RX_VK_DLSS_SDK"

# Anything that is NVIDIA's NGX API by name: the identifiers, the helper macros, the headers and the
# libraries. Deliberately wider than what vk_dlss.cpp uses, so a new call is caught rather than
# needing the pattern extended with it.
NGX = re.compile(r"NVSDK_NGX[A-Za-z0-9_]*|NGX_VULKAN[A-Za-z0-9_]*|nvsdk_ngx[A-Za-z0-9_.]*"
                 r"|nvngx[A-Za-z0-9_.]*|libnvidia-ngx[A-Za-z0-9_.]*")
# Files that would be NVIDIA's SDK, vendored. CONTRIBUTING.md's licensing rule forbids it and
# docs/upscaling.md says why DLSS in particular cannot be vendored at all.
VENDORED = re.compile(r"^(nvsdk_ngx|nvngx|_nvngx|libnvidia-ngx|sl\.|sl_)", re.I)


def strip_comments(text):
    """Comments out, string and character literals kept.

    Both halves matter. This file's own prose names NVSDK_NGX_Parameter outside the gate while
    explaining why the ABI is not written here -- a scan that read comments would report the
    explanation as the violation. And the NGX entry points are resolved by name, so a *string*
    holding one outside the gate is a real violation, which a scan that stripped strings could
    never see.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, end))   # (line numbers survive)
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:min(j + 1, n)])
            i = min(j + 1, n)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def scan(text, what):
    """Every NGX name in `text`, split by whether the gate encloses it: (inside, outside).

    Scope comes from the preprocessor's own nesting, not from a line window: a frame per #if, the
    arm flag flipped by #elif and #else, popped by #endif. A fixed lookback would read the
    neighbouring block's #if as this line's, in either direction.

    The floors are here rather than in the caller, so no later edit can leave a caller asserting
    nothing: a scan that read no text, or whose pattern has stopped matching, fails as a scan.
    """
    assert text.strip(), "%s: nothing to scan" % what
    inside, outside, frames = [], [], []
    for number, line in enumerate(strip_comments(text).splitlines(), 1):
        stripped = line.strip()
        directive = stripped.split(None, 1)[0] if stripped.startswith("#") else ""
        rest = stripped[len(directive):]
        if directive in ("#if", "#ifdef", "#ifndef"):
            frames.append(GATE in rest and directive != "#ifndef")
        elif directive in ("#elif", "#else") and frames:
            frames[-1] = directive == "#elif" and GATE in rest
        elif directive == "#endif" and frames:
            frames.pop()
        gated = any(frames)
        for name in NGX.findall(line):
            (inside if gated else outside).append((number, name))
    assert inside or outside, "%s: the NGX pattern matched nothing, so this scan says nothing" % what
    return inside, outside


FIXTURE = """
// A comment naming NVSDK_NGX_Parameter_Width outside the gate: prose, not a violation.
#if defined(RX_VK_DLSS_SDK)
    NVSDK_NGX_Result ok = ngx.init(p);
#endif
    NVSDK_NGX_Result leaked = other_init();
    const char* resolved = "NVSDK_NGX_VULKAN_CreateFeature";
#if defined(RX_VK_DLSS_SDK)
    NVSDK_NGX_Handle* right_arm = nullptr;
#else
    NVSDK_NGX_Handle* wrong_arm = nullptr;
#endif
#ifdef SOMETHING_ELSE
#if defined(RX_VK_DLSS_SDK)
    NVSDK_NGX_Feature nested = NVSDK_NGX_Feature_SuperSampling;
#endif
#endif
"""


def check_scanner():
    """The scan, against text whose answer is known.

    Every decoy sits directly beside a correctly gated line, because that adjacency is the false
    negative a fixture of separate blocks cannot see: a gated call one line above an ungated one is
    exactly what a line-window scan excuses. The expected set is asserted exactly -- "nothing was
    reported" is also what a dead pattern and an unread file look like.
    """
    inside, outside = scan(FIXTURE, "the scanner's own fixture")
    got = sorted(name for _, name in outside)
    want = sorted(["NVSDK_NGX_Result", "NVSDK_NGX_VULKAN_CreateFeature", "NVSDK_NGX_Handle"])
    assert got == want, "the scanner reported %s outside the gate, wanted %s" % (got, want)
    gated = sorted(name for _, name in inside)
    assert gated == sorted(["NVSDK_NGX_Result", "NVSDK_NGX_Handle", "NVSDK_NGX_Feature",
                            "NVSDK_NGX_Feature_SuperSampling"]), \
        "the scanner reported %s inside the gate" % gated


def check_sources():
    inside, outside = scan(SOURCE.read_text(encoding="utf-8"), SOURCE.name)
    if outside:
        lines = ", ".join("%s:%d %s" % (SOURCE.name, n, name) for n, name in outside)
        raise SystemExit("NGX outside the %s gate: %s" % (GATE, lines))
    assert len(inside) >= 20, "only %d NGX names in %s: has the gated half gone?" % (len(inside), SOURCE.name)
    print("%s: %d NGX name(s), all inside the %s gate" % (SOURCE.name, len(inside), GATE))
    # The header is included by every build, gate or no gate, so it may not name NGX at all -- not
    # even inside one. Absence is the property here, and an empty result is also what a pattern that
    # has stopped matching looks like, so the planted name makes the control inseparable from the
    # assertion rather than a separate check someone can delete.
    text = strip_comments(HEADER.read_text(encoding="utf-8"))
    found = NGX.findall(text + "\nNVSDK_NGX_Handle* planted;\n")
    assert found == ["NVSDK_NGX_Handle"], \
        "%s names NGX (%s), or the pattern has died" % (HEADER.name, found)
    print("%s: no NGX name at all, as a header every build reads must have" % HEADER.name)


def check_nothing_vendored():
    # Tracked and not-yet-tracked alike, .gitignore honoured: a vendored header is a problem while
    # it sits in the working tree, not only once it is committed.
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others",
                          "--exclude-standard"], check=True, stdout=subprocess.PIPE,
                         text=True).stdout.split()
    assert "gpu/vk/vk_dlss.cpp" in out, "this is not the repository the checks above read"
    bad = [f for f in out if VENDORED.match(os.path.basename(f))]
    if bad:
        raise SystemExit("NVIDIA SDK files in the repository: %s" % ", ".join(bad))
    print("%d files in the tree, none an NVIDIA SDK header or library" % len(out))


def compile_flags(a):
    flags = [a.cxx, "-std=c++20", "-O1", "-DVK_NO_PROTOTYPES", "-I" + str(ROOT / "gpu/vk")]
    if sys.platform != "win32":   # (the Win32 calls of gpu/vk on POSIX, as nx2/build.py does)
        flags += ["-D_GNU_SOURCE", "-I" + str(ROOT / "host/posix")]
    if a.headers:
        flags.append("-I" + a.headers)
    return flags


def check_runs(a, tmp):
    exe = Path(tmp) / ("dlss_contract_test" + (".exe" if sys.platform == "win32" else ""))
    subprocess.run(compile_flags(a) + [str(SOURCE), str(DRIVER), "-o", str(exe)], check=True)
    for mode in ("off", "nodev", "on"):
        env = dict(os.environ)
        env.pop("RX_VK_DLSS", None)
        if mode != "off":
            env["RX_VK_DLSS"] = "1"
        subprocess.run([str(exe), mode], check=True, env=env)


def check_gate_needs_nvidias_headers(a, tmp):
    done = subprocess.run(compile_flags(a) + ["-fsyntax-only", "-D%s=1" % GATE, str(SOURCE)],
                          cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if done.returncode == 0:
        raise SystemExit("gpu/vk/vk_dlss.cpp compiles with %s on and no NVIDIA SDK present, so the "
                         "NGX ABI is declared somewhere in this tree. It must not be." % GATE)
    if "nvsdk_ngx" not in done.stdout:
        raise SystemExit("with %s on the compile failed for some other reason than NVIDIA's missing "
                         "headers, so this check proves nothing:\n%s" % (GATE, done.stdout))
    print("with %s on, the build asks for NVIDIA's own headers and stops without them" % GATE)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    p.add_argument("--headers", help="a Vulkan headers include folder; the compiler's own path by default")
    a = p.parse_args()
    check_scanner()
    check_sources()
    check_nothing_vendored()
    with tempfile.TemporaryDirectory(prefix="dlss-contract-") as tmp:
        check_runs(a, tmp)
        check_gate_needs_nvidias_headers(a, tmp)


if __name__ == "__main__":
    main()
