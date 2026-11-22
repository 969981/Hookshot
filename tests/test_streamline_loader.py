"""Streamline's interposer is loaded only when it is NVIDIA's, and it is the file that was checked (gpu/vk/vk_streamline.cpp
signed_by_nvidia, load).

sl.interposer.dll is code from a folder the player can write to. It is loaded only when Windows trusts its signature and the
signer is exactly "NVIDIA Corporation": the check was wcsstr(name, "NVIDIA"), which any certificate whose name holds the
word passes. And the file was checked by path, then loaded by path: between the two, another file could take its place.
Now it is held open from before the check until it is loaded, sharing reads only (no write, rename or delete), and loaded
with its imports looked for beside it and in System32 only.

The code is Windows-only (WinTrust, CryptoAPI), so this reads the source: the order of the calls in load(), the exact
signer, the share mode and the load flags.

python -I -B tests/test_streamline_loader.py
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def body(text, head):
    i = text.index(head)
    j = text.index("\n}\n", i)
    return text[i:j]


def main():
    src = (ROOT / "gpu/vk/vk_streamline.cpp").read_text(encoding="utf-8")
    signer = body(src, "bool signed_by_nvidia(const std::wstring& path) {")
    load = body(src, "bool load(State& s, const std::wstring& path) {")
    if 'constexpr const wchar_t* kNvidiaSigner = L"NVIDIA Corporation";' not in src:
        raise SystemExit("the signer's name is no longer exactly \"NVIDIA Corporation\"")
    if "wcscmp(name, kNvidiaSigner) == 0" not in signer or re.search(r"wcsstr\(name", signer):
        raise SystemExit("signed_by_nvidia matches the signer's name loosely again (wcsstr): it must be exactly NVIDIA's")
    if "WinVerifyTrust" not in signer:
        raise SystemExit("signed_by_nvidia no longer asks Windows whether the signature is trusted")
    order = [load.find(k) for k in ("CreateFileW(", "signed_by_nvidia(path)", "LoadLibraryExW(", "CloseHandle(held)")]
    if -1 in order or order != sorted(order):
        raise SystemExit("load() must open the file, check it, load it and only then let it go, in that order: %s" % order)
    held = load[load.index("CreateFileW("):load.index(";", load.index("CreateFileW("))]
    if "FILE_SHARE_READ," not in held or "FILE_SHARE_WRITE" in held or "FILE_SHARE_DELETE" in held:
        raise SystemExit("the interposer is held open with sharing beyond reads: it could be changed between check and load")
    flags = load[load.index("LoadLibraryExW("):load.index(";", load.index("LoadLibraryExW("))]
    if "LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR" not in flags or "LOAD_LIBRARY_SEARCH_SYSTEM32" not in flags:
        raise SystemExit("the interposer's imports are looked for outside its own folder and System32")
    if re.search(r"\bLoadLibraryW\(", load):
        raise SystemExit("load() calls LoadLibraryW, whose search takes the current folder and PATH")
    print("PASS: sl.interposer.dll loaded only when signed by NVIDIA Corporation, held from the check to the load")


if __name__ == "__main__":
    main()
