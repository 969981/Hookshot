"""Another company's library is loaded only when it is that company's own file, and the file that was checked
(gpu/vk/vk_signed.cpp; its callers vk_streamline.cpp, vk_fsr4.cpp, vk_ffx.cpp).

Windows: NVIDIA's sl.interposer.dll and AMD's amd_fidelityfx_loader_vk.dll come from folders the player can write to. Each
is held open, sharing reads only, from before its signature is checked until it is loaded; Windows must trust the signature
and the signer must be exactly the vendor's. The names are the CN of the signing certificates in the real files (read on
2026-11-21): "NVIDIA Corporation" (sl.interposer.dll) and "Advanced Micro Devices" (AMD's three amd_fidelityfx_*_vk.dll,
no ", Inc."). Before, Streamline's check was wcsstr(name, "NVIDIA") with the file checked and loaded by path separately,
and AMD's DLLs had no check at all. That code is Windows-only (WinTrust, CryptoAPI), so this reads the source.

Linux: shared objects are not signed. AMD's FSR 4 .so is loaded only when it and its folder are this user's or root's and
writable by no one else, its group only when that is the user's own (Ubuntu's umask 002 makes 664 files and 775 folders);
that part is compiled here and run on real files and folders. NVIDIA's NGX on Linux loads its runtimes from the folders it
is given, unsigned: an existing one is given only when it passes the same check (vk_dlss.cpp runtime_dirs).

python -I -B tests/test_vendor_dll_checks.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def body(text, head):
    i = text.index(head)
    return text[i:text.index("\n}\n", i)]


def check_windows(fail):
    h = (ROOT / "gpu/vk/vk_signed.h").read_text(encoding="utf-8")
    src = (ROOT / "gpu/vk/vk_signed.cpp").read_text(encoding="utf-8")
    if 'kNvidia = L"NVIDIA Corporation";' not in h or 'kAmd = L"Advanced Micro Devices";' not in h:
        fail("the vendors' names are no longer the ones in their certificates (NVIDIA Corporation, Advanced Micro Devices)")
    signer = body(src, "bool signed_by(const std::wstring& path, const wchar_t* signer, const char* what) {")
    if "WinVerifyTrust" not in signer or "wcscmp(name, signer) == 0" not in signer or re.search(r"wcsstr\(", signer):
        fail("signed_by must ask Windows' trust and compare the signer's name exactly (no wcsstr)")
    opened = body(src, "HANDLE open_checked(const std::wstring& path, const wchar_t* signer, const char* what) {")
    held = opened[opened.index("CreateFileW("):opened.index(";", opened.index("CreateFileW("))]
    if "FILE_SHARE_READ," not in held or "FILE_SHARE_WRITE" in held or "FILE_SHARE_DELETE" in held:
        fail("open_checked holds the file with sharing beyond reads: it could change between check and load")
    if opened.find("CreateFileW(") > opened.find("signed_by(path"):
        fail("open_checked must open the file before it checks it")
    load = body(src, "HMODULE load(const std::wstring& path, const wchar_t* signer, const char* what, DWORD flags) {")
    order = [load.find(k) for k in ("open_checked(", "LoadLibraryExW(", "CloseHandle(held)")]
    if -1 in order or order != sorted(order):
        fail("signed_dll::load must check, load and only then let the file go: %s" % order)
    sl = (ROOT / "gpu/vk/vk_streamline.cpp").read_text(encoding="utf-8")
    sl_load = body(sl, "bool load(State& s, const std::wstring& path) {")
    if "signed_dll::load(path, signed_dll::kNvidia" not in sl_load or "LOAD_LIBRARY_SEARCH_SYSTEM32" not in sl_load \
            or re.search(r"\bLoadLibrary(W|A|ExW|ExA)\(", sl_load):
        fail("Streamline's interposer must be loaded through signed_dll::load with NVIDIA's name, its imports beside it and "
             "in System32 only")
    fsr4 = (ROOT / "gpu/vk/vk_fsr4.cpp").read_text(encoding="utf-8")
    if "signed_dll::load(signed_dll::wide(path), signed_dll::kAmd" not in fsr4 or "LoadLibraryExA(" in fsr4:
        fail("vk_fsr4.cpp must load AMD's loader through signed_dll::load with AMD's name")
    ffx = (ROOT / "gpu/vk/vk_ffx.cpp").read_text(encoding="utf-8")
    if "signed_dll::open_checked(wpath, signed_dll::kAmd" not in ffx or "LoadLibraryExA(" in ffx:
        fail("vk_ffx.cpp must check AMD's loader with AMD's name before it loads it")
    i, j = ffx.find("signed_dll::open_checked(wpath"), ffx.find("guarded_load(wpath.c_str())")
    if i < 0 or j < 0 or i > j or ffx.find("CloseHandle(held)", j) < 0:
        fail("vk_ffx.cpp must hold AMD's loader from its check until guarded_load has loaded it")
    # AMD's loader loads its providers from its own folder unchecked: each is loaded first, checked, by its full path
    k = fsr4.find("signed_dll::preload_amd_providers(dir, search)")
    if k < 0 or k > fsr4.find("signed_dll::load(signed_dll::wide(path), signed_dll::kAmd"):
        fail("vk_fsr4.cpp must check and load AMD's upscaler and frame generation DLLs before the loader, which loads them "
             "unchecked")
    k = ffx.find("signed_dll::preload_amd_providers(")
    if k < 0 or k > i or "guarded_load)" not in ffx[k:i]:
        fail("vk_ffx.cpp must check and load AMD's providers (through guarded_load) before the loader")
    signed = (ROOT / "gpu/vk/vk_signed.cpp").read_text(encoding="utf-8")
    pre = body(signed, "bool preload_amd_providers(")
    for name in ("amd_fidelityfx_upscaler_vk.dll", "amd_fidelityfx_framegeneration_vk.dll"):
        if '"%s"' % name not in pre:
            fail("preload_amd_providers no longer checks %s" % name)
    if "open_checked(path, kAmd, name)" not in pre or "GetFullPathNameW(" not in pre:
        fail("preload_amd_providers must check each provider as AMD's by its full path")
    # every refusal in the signer check says why (one that did not read as "no such file" further on)
    sig = body(signed, "bool signed_by(const std::wstring& path, const wchar_t* signer, const char* what) {")
    if sig.count("not loading it") < 5:
        fail("signed_by refuses a file somewhere without a line saying why (%d reasons logged, 5 wanted)"
             % sig.count("not loading it"))
    if "refused or not loaded in" not in fsr4:
        fail("vk_fsr4.cpp says there is no AMD loader when one was there and was refused")
    # AMD's DLLs' signatures (WinVerifyTrust, which may fetch certificates) are not checked where the device is made
    main_cpp = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    if re.search(r"\bffx::probe\(\)", main_cpp):
        fail("vk_main.cpp asks AMD's FSR 4 runtime while the device is made again: its signature check can wait on the "
             "network")
    if "ffx::probe();" not in body(menu, "static void probe_upscalers_soon() {"):
        fail("AMD's FSR 4 frame generation is no longer asked on the start-up probes' thread (probe_upscalers_soon)")
    if "std::lock_guard lk(probe_mu);" not in body(ffx, "void probe() {"):
        fail("vk_ffx.cpp probe runs on the start-up probes' thread: it must hold probe_mu")


MAIN = r"""
#include <cstdio>
#include <cstdarg>
namespace mkwvk { void logf(const char* fmt, ...) { va_list a; va_start(a, fmt); std::vprintf(fmt, a); va_end(a); std::printf("\n"); } }
int main(int argc, char** argv) {
    // argv: file, want (0/1), file, want ...
    int failed = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        const bool got = mkwvk::signed_dll::owned_safely(argv[i], "the test's library");
        const bool want = argv[i + 1][0] == '1';
        if (got != want) failed++, std::printf("FAIL %s: %d, wanted %d\n", argv[i], got, want);
        else std::printf("ok   %s -> %d\n", argv[i], got);
    }
    return failed;
}
"""


def linux_src(src):
    return src[src.index("#else\nnamespace {"):src.rindex("#endif")]


def check_linux(cxx, fail):
    src = (ROOT / "gpu/vk/vk_signed.cpp").read_text(encoding="utf-8")
    fsr4 = (ROOT / "gpu/vk/vk_fsr4.cpp").read_text(encoding="utf-8")
    k = fsr4.find("if (!signed_dll::owned_safely(path, kLoader)) continue;")
    if k < 0 or k > fsr4.find("dlopen(path.c_str()", k):
        fail("vk_fsr4.cpp must check AMD's .so is safely owned before it dlopens it")
    dlss = (ROOT / "gpu/vk/vk_dlss.cpp").read_text(encoding="utf-8")
    dirs = body(dlss, "std::vector<fs::path> runtime_dirs(const fs::path& game) {")
    lin = dirs[dirs.index("#else"):]
    if "signed_dll::folder_owned_safely(" not in lin or "dirs.swap(safe);" not in lin:
        fail("Linux's NGX runtime folders are no longer checked: NGX loads unsigned runtimes from a folder others may write")
    linux = linux_src(src)
    # (what the filesystem cannot say is logged, not refused: the drives that keep no Unix permissions, by their f_type)
    for magic, name in (("0x7366746eUL", "ntfs3"), ("0x5346544eUL", "ntfs"), ("0x65735546UL", "FUSE, ntfs-3g's"),
                        ("0x2011bab0UL", "exFAT"), ("0x4d44UL", "FAT")):
        if "case %s:" % magic not in linux_src(src):
            fail("the Linux check no longer knows %s keeps no Unix permissions: a Steam NTFS drive mounted umask=000 (every "
                 "file 0777) loses FSR 4 and DLSS's runtime folder" % name)
    owner = linux_src(src)[linux_src(src).index("if (st.st_uid != geteuid() && st.st_uid != 0) {"):]
    owner = owner[:owner.index("    return true;\n}")]
    if "return false;" not in owner or owner.index("level >= 2") > owner.index("return false;"):
        fail("a library another account owns must be refused only under RX_VK_LIBRARY_CHECK=2 (a drive from another "
             "install has another uid)")
    code = ("#include <cstdlib>\n#include <grp.h>\n#include <pwd.h>\n#include <string>\n#include <sys/stat.h>\n"
            "#include <sys/vfs.h>\n#include <unistd.h>\nnamespace mkwvk { void logf(const char*, ...); }\n"
            "namespace mkwvk::signed_dll {\nbool owned_safely(const std::string& path, const char* what);\n" +
            linux[len("#else\n"):] + "\n}\n" + MAIN)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="hookshot-vendor-dll-") as tmp:
        t = Path(tmp)
        (t / "c.cpp").write_text(code, encoding="utf-8")
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", str(t / "c.cpp"), "-o", str(t / "c")],
                           capture_output=True, text=True, timeout=120, creationflags=flags)
        if r.returncode:
            fail("owned_safely does not compile:\n" + r.stdout + r.stderr)
            return
        good = t / "good"
        good.mkdir(mode=0o755)
        (good / "lib.so").write_bytes(b"x")
        os.chmod(good / "lib.so", 0o644)
        open_dir = t / "open"
        open_dir.mkdir()
        os.chmod(open_dir, 0o777)
        (open_dir / "lib.so").write_bytes(b"x")
        os.chmod(open_dir / "lib.so", 0o644)
        # (group-writable in this user's own group, as Ubuntu makes every file (umask 002): taken; world-writable: not)
        mine = t / "mine"
        mine.mkdir()
        os.chmod(mine, 0o775)
        (mine / "lib.so").write_bytes(b"x")
        os.chmod(mine / "lib.so", 0o664)
        (good / "world.so").write_bytes(b"x")
        os.chmod(good / "world.so", 0o646)
        cases = [(good / "lib.so", "1"), (mine / "lib.so", "1" if os.stat(mine).st_gid == os.getegid() else "0"),
                 (good / "world.so", "0"), (open_dir / "lib.so", "0"), (good / "missing.so", "0")]
        r = subprocess.run([str(t / "c")] + [a for p, w in cases for a in (str(p), w)], capture_output=True, text=True,
                           timeout=30, creationflags=flags)
        print(r.stdout.strip())
        if r.returncode:
            fail("owned_safely took a library other accounts could have written, or refused a safe one")
        # RX_VK_LIBRARY_CHECK=0: what would be refused is loaded (said); a missing file still is not
        cases = [(good / "world.so", "1"), (open_dir / "lib.so", "1"), (good / "missing.so", "0")]
        r = subprocess.run([str(t / "c")] + [a for p, w in cases for a in (str(p), w)], capture_output=True, text=True,
                           timeout=30, creationflags=flags, env=dict(os.environ, RX_VK_LIBRARY_CHECK="0"))
        print(r.stdout.strip())
        if r.returncode:
            fail("RX_VK_LIBRARY_CHECK=0 does not load what the check would refuse")


def check_windows_includes(fail):
    """A file that calls Windows' signature API includes its headers itself (CI compiles no Windows code: moving the signer
    to vk_signed.cpp took <wintrust.h> out of vk_streamline.cpp, whose trusted_signature still needs it)"""
    for path in sorted((ROOT / "gpu" / "vk").glob("*.cpp")):
        src = path.read_text(encoding="utf-8")
        for call, header in (("WinVerifyTrust(", "<wintrust.h>"), ("CryptQueryObject(", "<wincrypt.h>")):
            if call in src and "#include " + header not in src and "#include <softpub.h>" not in src:
                fail("%s calls %s without including %s (or <softpub.h>)" % (path.name, call.rstrip("("), header))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"), help="GCC/Clang C++ compiler")
    args = parser.parse_args()
    failures = []
    check_windows(failures.append)
    check_windows_includes(failures.append)
    if os.name != "nt":
        cxx = shutil.which(args.cxx)
        if not cxx:
            raise SystemExit("C++ compiler not found: " + args.cxx)
        check_linux(cxx, failures.append)
    if failures:
        raise SystemExit("\n".join(failures))
    print("PASS: NVIDIA's and AMD's libraries loaded only when they are the vendor's own file, and the one checked")


if __name__ == "__main__":
    main()
