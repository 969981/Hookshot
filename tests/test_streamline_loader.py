"""Streamline's interposer is loaded only when it is NVIDIA's, and it is the file that was checked (gpu/vk/vk_streamline.cpp
load, through gpu/vk/vk_signed.cpp signed_by, open_checked and load).

sl.interposer.dll is code from a folder the player can write to. It is loaded only when Windows trusts its signature and the
signer is exactly "NVIDIA Corporation": the check was wcsstr(name, "NVIDIA"), which any certificate whose name holds the
word passes. And the file was checked by path, then loaded by path: between the two, another file could take its place.
Now it is held open from before the check until it is loaded, sharing reads only (no write, rename or delete), and loaded
with its imports looked for beside it and in System32 only.

The code is Windows-only (WinTrust, CryptoAPI), so this reads the source: vk_streamline.cpp's load hands the file to
vk_signed.cpp's check (shared with AMD's DLLs) with NVIDIA's exact name and its load flags, and that check's order of
calls, signer comparison and share mode.

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
    signed = (ROOT / "gpu/vk/vk_signed.cpp").read_text(encoding="utf-8")
    header = (ROOT / "gpu/vk/vk_signed.h").read_text(encoding="utf-8")
    # (the check is vk_signed.cpp's, shared with AMD's DLLs: vk_streamline.cpp's load hands it NVIDIA's exact name)
    sl = body(src, "bool load(State& s, const std::wstring& path) {")
    call = sl[sl.index("signed_dll::load("):sl.index(";", sl.index("signed_dll::load("))] if "signed_dll::load(" in sl else ""
    if "signed_dll::kNvidia" not in call:
        raise SystemExit("vk_streamline.cpp's load no longer loads sl.interposer.dll through signed_dll::load with NVIDIA's name")
    if 'inline constexpr const wchar_t* kNvidia = L"NVIDIA Corporation";' not in header:
        raise SystemExit("the signer's name is no longer exactly \"NVIDIA Corporation\"")
    signer = body(signed, "bool signed_by(const std::wstring& path, const wchar_t* signer, const char* what) {")
    if "wcscmp(name, signer) == 0" not in signer or re.search(r"wcsstr\(name", signer):
        raise SystemExit("signed_by matches the signer's name loosely again (wcsstr): it must be exactly the vendor's")
    if "WinVerifyTrust" not in signer:
        raise SystemExit("signed_by no longer asks Windows whether the signature is trusted")
    held_open = body(signed, "HANDLE open_checked(const std::wstring& path, const wchar_t* signer, const char* what) {")
    load = body(signed, "HMODULE load(const std::wstring& path, const wchar_t* signer, const char* what, DWORD flags) {")
    if held_open.find("CreateFileW(") < 0 or held_open.find("CreateFileW(") > held_open.find("signed_by(path"):
        raise SystemExit("open_checked must open the file before it checks it")
    order = [load.find(k) for k in ("open_checked(", "LoadLibraryExW(", "CloseHandle(held)")]
    if -1 in order or order != sorted(order):
        raise SystemExit("load() must open and check the file, load it and only then let it go, in that order: %s" % order)
    held = held_open[held_open.index("CreateFileW("):held_open.index(";", held_open.index("CreateFileW("))]
    if "FILE_SHARE_READ," not in held or "FILE_SHARE_WRITE" in held or "FILE_SHARE_DELETE" in held:
        raise SystemExit("the interposer is held open with sharing beyond reads: it could be changed between check and load")
    if "LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR" not in call or "LOAD_LIBRARY_SEARCH_SYSTEM32" not in call:
        raise SystemExit("the interposer's imports are looked for outside its own folder and System32")
    if re.search(r"\bLoadLibraryW\(", sl + load):
        raise SystemExit("the interposer is loaded with LoadLibraryW, whose search takes the current folder and PATH")
    print("PASS: sl.interposer.dll loaded only when signed by NVIDIA Corporation, held from the check to the load")


if __name__ == "__main__":
    main()
