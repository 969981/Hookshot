"""Where DLSS 5 neural rendering's files are looked for on Windows (gpu/vk/vk_streamline.cpp search_dirs, gpu/vk/vk_dlss.cpp
runtime_dirs).

A game the Builder installs has its real hookshot.exe in bin\\ beside ..\\image.bin, and a small launcher of the same name
in the game folder. Streamline looked only beside the exe (bin\\, bin\\streamline\\), so files put "beside the game" as the
readme said were never found. Now the game folder's streamline\\, its ngx\\ (where the DLSS tick puts NVIDIA's runtimes,
and where a player's Windows log showed all of DLSS 5's files) and the game folder are looked in too, after the exe's;
and NGX direct finds nvngx_dlssnr.dll in that streamline\\ folder, so one copy serves both back ends.

This cuts search_dirs out of the source and compiles it with stand-ins for the six Win32 calls it makes (an exe path, the
files that exist, the environment, the current folder), then runs it for an installed game, a folder without image.bin,
an exe not in bin\\ and RX_VK_STREAMLINE_DIR set, whole or relative (made whole: the interposer is loaded with
LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, which takes only a full path). runtime_dirs is checked in the source.

python -I -B tests/test_dlss5_folders.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

STUBS = r"""
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <cwctype>
#include <set>
#include <string>
#include <vector>
typedef unsigned long DWORD;
typedef void* HMODULE;
#define CP_UTF8 65001
#define INVALID_FILE_ATTRIBUTES ((DWORD)-1)
static std::wstring g_exe, g_env;
static std::set<std::wstring> g_files;
static DWORD GetEnvironmentVariableW(const wchar_t* name, wchar_t* out, DWORD cap) {
    if (std::wstring(name) != L"RX_VK_STREAMLINE_DIR" || g_env.empty() || g_env.size() + 1 > cap) return 0;
    wcscpy(out, g_env.c_str());
    return (DWORD)g_env.size();
}
static DWORD GetModuleFileNameW(HMODULE, wchar_t* out, DWORD cap) {
    if (g_exe.size() + 1 > cap) return cap;
    wcscpy(out, g_exe.c_str());
    return (DWORD)g_exe.size();
}
// (the current folder is C:\\cwd: a drive-rooted path is whole already, a relative one is made whole against it)
static DWORD GetFullPathNameW(const wchar_t* in, DWORD cap, wchar_t* out, wchar_t**) {
    const std::wstring p(in);
    const std::wstring full = p.size() > 2 && p[1] == L':' ? p : L"C:\\cwd\\" + p;
    if (full.size() + 1 > cap) return (DWORD)full.size() + 1;
    wcscpy(out, full.c_str());
    return (DWORD)full.size();
}
static DWORD GetFileAttributesW(const wchar_t* path) { return g_files.count(path) ? 0x80 : INVALID_FILE_ATTRIBUTES; }
static int _wcsicmp(const wchar_t* a, const wchar_t* b) {
    for (; *a && *b; a++, b++)
        if (towlower(*a) != towlower(*b)) return towlower(*a) - towlower(*b);
    return towlower(*a) - towlower(*b);
}
static int WideCharToMultiByte(unsigned, DWORD, const wchar_t* w, int n, char* out, int cap, const char*, int*) {
    if (!out) return n;
    for (int i = 0; i < n && i < cap; i++) out[i] = (char)w[i];
    return n;
}
"""

MAIN = r"""
static int failed = 0;
static void expect(const char* what, const std::vector<std::wstring>& want) {
    const std::vector<std::wstring> got = search_dirs();
    if (got != want) {
        failed++;
        std::printf("FAIL %s:\n", what);
        for (const auto& d : got) std::printf("  got  %ls\n", d.c_str());
        for (const auto& d : want) std::printf("  want %ls\n", d.c_str());
    } else {
        std::printf("ok   %s\n", what);
    }
}
int main() {
    g_exe = L"C:\\Games\\OoT\\bin\\hookshot.exe";
    g_files = {L"C:\\Games\\OoT\\image.bin"};
    expect("an installed game: bin's folders, then the game folder's",
           {L"C:\\Games\\OoT\\bin\\streamline", L"C:\\Games\\OoT\\bin", L"C:\\Games\\OoT\\streamline", L"C:\\Games\\OoT\\ngx",
            L"C:\\Games\\OoT"});
    g_exe = L"C:\\Games\\OoT\\BIN\\hookshot.exe";
    g_files = {L"C:\\Games\\OoT\\image.bin"};
    expect("BIN in capitals is bin",
           {L"C:\\Games\\OoT\\BIN\\streamline", L"C:\\Games\\OoT\\BIN", L"C:\\Games\\OoT\\streamline", L"C:\\Games\\OoT\\ngx",
            L"C:\\Games\\OoT"});
    g_exe = L"C:\\Games\\OoT\\bin\\hookshot.exe";
    g_files = {};
    expect("a bin folder with no image.bin above it is no game folder", {L"C:\\Games\\OoT\\bin\\streamline", L"C:\\Games\\OoT\\bin"});
    g_exe = L"C:\\Games\\OoT\\hookshot.exe";
    g_files = {L"C:\\Games\\OoT\\image.bin"};
    expect("an exe in the game folder itself", {L"C:\\Games\\OoT\\streamline", L"C:\\Games\\OoT"});
    g_exe = L"C:\\Games\\OoT\\bin\\hookshot.exe";
    g_files = {L"C:\\Games\\OoT\\image.bin"};
    g_env = L"D:\\sl";
    expect("RX_VK_STREAMLINE_DIR first",
           {L"D:\\sl", L"C:\\Games\\OoT\\bin\\streamline", L"C:\\Games\\OoT\\bin", L"C:\\Games\\OoT\\streamline",
            L"C:\\Games\\OoT\\ngx", L"C:\\Games\\OoT"});
    g_env = L"sl";
    expect("a relative RX_VK_STREAMLINE_DIR, made whole (LoadLibraryExW's LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR takes no other)",
           {L"C:\\cwd\\sl", L"C:\\Games\\OoT\\bin\\streamline", L"C:\\Games\\OoT\\bin", L"C:\\Games\\OoT\\streamline",
            L"C:\\Games\\OoT\\ngx", L"C:\\Games\\OoT"});
    return failed ? 1 : 0;
}
"""


def cut(text, start, end_marker="\n}\n"):
    i = text.index(start)
    return text[i:text.index(end_marker, i) + len(end_marker)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"), help="GCC/Clang C++ compiler")
    args = parser.parse_args()
    streamline = (ROOT / "gpu/vk/vk_streamline.cpp").read_text(encoding="utf-8")
    dlss = (ROOT / "gpu/vk/vk_dlss.cpp").read_text(encoding="utf-8")
    # (runtime_dirs, Windows: the game folder's streamline folder after NGX's own folders)
    dirs = cut(dlss, "std::vector<fs::path> runtime_dirs(const fs::path& game) {")
    win = dirs[dirs.index("#ifdef _WIN32"):dirs.index("#else")]
    if 'dirs.push_back(game / L"streamline");' not in win or win.index('L"streamline"') < win.index('L"ngx"'):
        raise SystemExit("runtime_dirs no longer looks in the game folder's streamline folder after ngx: NGX direct's "
                         "nvngx_dlssnr.dll is found only in ngx\\ again")
    # (the log names the folders looked in)
    if "for (const std::wstring& d : search_dirs()) looked +=" not in streamline:
        raise SystemExit("the no-sl.interposer.dll line no longer names the folders looked in")
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    body = cut(streamline, "std::string narrow(const std::wstring& w) {") + "\n" + \
        cut(streamline, "std::vector<std::wstring> search_dirs() {")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="hookshot-dlss5-folders-") as tmp:
        src = Path(tmp) / "folders.cpp"
        src.write_text(STUBS + body + MAIN, encoding="utf-8")
        exe = Path(tmp) / ("folders.exe" if os.name == "nt" else "folders")
        # (_CRT_SECURE_NO_WARNINGS: the Windows C runtime marks the stand-ins' wcscpy deprecated, an error with -Werror)
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-D_CRT_SECURE_NO_WARNINGS", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-function", str(src), "-o", str(exe)], capture_output=True, text=True, timeout=120, creationflags=flags)
        if r.returncode:
            print(r.stdout + r.stderr)
            return 1
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, creationflags=flags)
        print((r.stdout + r.stderr).strip())
        if r.returncode:
            return 1
    print("PASS: DLSS 5's files found beside the game, in its streamline folder and in bin\\")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
