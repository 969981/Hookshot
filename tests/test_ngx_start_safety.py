"""NGX at start-up: started only on a device NGX runs on, asked once whichever thread is first, and its folders read without
a throw (gpu/vk/vk_dlss.cpp dlss_ngx_session, entries; gpu/vk/vk_dlssnr_ngx.cpp probe).

The start-up block (dlss_status_log) runs on a detached thread and asks NGX direct whether DLSS 5 can run. Three things went
wrong there:

- On an AMD or Intel GPU, or a laptop's Intel GPU with NVIDIA's driver installed, asking started NGX on that device (and
  made an empty ngx folder in the game folder at every start). dlss_ngx_session now starts NGX only on a device with
  NVIDIA's NVX extensions, which dlss_device_extensions records as the device is made. It is not gated on dlss_why(): that
  is super resolution's answer, and NGX runs DLSS 5 or frame generation when only DLSS's own runtime is missing.
- probe's "asked already" flag and its reason were plain fields, written by the start-up thread while the frame's (apply,
  running_backend, the Neural tab) read or wrote them. Now the first caller asks under a mutex while the other waits, and
  the reason is an atomic pointer to a string literal, so a pointer read on one thread stays valid.
- A range-for over std::filesystem::directory_iterator calls its throwing operator++: a folder failing part way through
  ended the game from that thread. The folders are now read through entries(), which uses increment(ec).

This cuts probe and entries out of the source and runs them (probe from eight threads at once, with a stand-in question
that takes a while), and checks the rest in the source.

python -I -B tests/test_ngx_start_safety.py [--cxx clang++]
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

PROBE_STUBS = r"""
#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>
#include <thread>
#include <vector>
struct { void* device = (void*)1; } dev;
struct State {
    std::atomic<bool> probed{};
    bool ok{};
    std::mutex probe_mu;
};
static std::atomic<int> asked{0};
bool probe_once(State&) {   // (NGX's question: slow enough that the threads meet in it)
    asked++;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    return true;
}
"""

PROBE_MAIN = r"""
int main() {
    State s;
    std::atomic<int> yes{0};
    std::vector<std::thread> threads;
    for (int i = 0; i < 8; i++) threads.emplace_back([&] { yes += probe(s) ? 1 : 0; });
    for (auto& t : threads) t.join();
    std::printf("asked %d time(s), %d of 8 callers told yes\n", asked.load(), yes.load());
    return asked == 1 && yes == 8 ? 0 : 1;
}
"""

ENTRIES_STUBS = r"""
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <system_error>
#include <vector>
namespace fs = std::filesystem;
"""

ENTRIES_MAIN = r"""
int main(int, char** argv) {
    const fs::path d = argv[1];
    if (!entries(d / "missing").empty()) return std::printf("a missing folder has entries\n"), 1;
    for (const char* n : {"a.dll", "b.dll", "c.dll"}) std::ofstream(d / n) << "x";
    const size_t n = entries(d).size();
    if (n != 3) return std::printf("%zu entries, not 3\n", n), 1;
    if (!entries(d / "a.dll").empty()) return std::printf("a file has entries\n"), 1;
    std::printf("a missing folder: none; a folder of three: 3; a file: none\n");
    return 0;
}
"""


def cut(text, start, end_marker="\n}\n"):
    i = text.index(start)
    return text[i:text.index(end_marker, i) + len(end_marker)]


def run(cxx, tmp, name, source, args=()):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    src = Path(tmp) / (name + ".cpp")
    src.write_text(source, encoding="utf-8")
    exe = Path(tmp) / (name + (".exe" if os.name == "nt" else ""))
    r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread", str(src), "-o", str(exe)],
                       capture_output=True, text=True, timeout=120, creationflags=flags)
    if r.returncode:
        raise SystemExit(name + " did not build:\n" + r.stdout + r.stderr)
    r = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30, creationflags=flags)
    print((r.stdout + r.stderr).strip())
    if r.returncode:
        raise SystemExit(name + " failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"), help="GCC/Clang C++ compiler")
    args = parser.parse_args()
    dlss = (ROOT / "gpu/vk/vk_dlss.cpp").read_text(encoding="utf-8")
    nr = (ROOT / "gpu/vk/vk_dlssnr_ngx.cpp").read_text(encoding="utf-8")

    # NGX only on a device it runs on, known from the device's extensions, not from super resolution's dlss_why()
    session = cut(dlss, "bool dlss_ngx_session() {")
    guard = session[:session.index("ngx_init();")]
    if "!g_ngx_device" not in guard:
        raise SystemExit("dlss_ngx_session starts NGX on a device without NVIDIA's extensions again (an AMD or Intel GPU)")
    if "DLSS_OK" in guard:
        raise SystemExit("dlss_ngx_session is gated on dlss_why() == DLSS_OK: that is super resolution's answer, and NGX "
                         "runs DLSS 5 and frame generation without DLSS's runtime")
    extensions = cut(dlss, "void dlss_device_extensions(")
    if "g_ngx_device = both;" not in extensions:
        raise SystemExit("dlss_device_extensions no longer records whether the device has NVIDIA's NVX extensions")
    block = cut(dlss, "void dlss_status_log() {")
    if "dlss_why() == DLSS_OK && dlssnr_ngx::capable()" in block:
        raise SystemExit("the start-up block says DLSS 5 through NGX direct is off whenever super resolution is, though "
                         "running_backend runs it")

    # no throwing directory iteration in vk_dlss.cpp
    throwing = re.findall(r"for \([^;)]*:\s*(?:std::)?(?:fs|filesystem)::(?:recursive_)?directory_iterator\(", dlss)
    if throwing:
        raise SystemExit("vk_dlss.cpp iterates a folder with a range-for again (operator++ throws): %s; use entries()"
                         % throwing)

    # the reason: a string literal, always
    st = cut(nr, "struct State {", "\n};\n")
    if "std::atomic<const char*> why" not in st:
        raise SystemExit("dlssnr_ngx's reason is no longer an atomic pointer: a thread reading it races the frame's writes")
    bad = [m.group(0) for m in re.finditer(r"\bs\.why\s*=\s*([^;]*);", nr) if not re.fullmatch(r'"[^"]*"', m.group(1).strip())]
    if bad:
        raise SystemExit("dlssnr_ngx's reason is set to something other than a string literal (a pointer to it would not "
                         "stay valid): %s" % bad)

    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    probe = cut(nr, "bool probe(State& s) {")
    with tempfile.TemporaryDirectory(prefix="hookshot-ngx-start-") as tmp:
        run(cxx, tmp, "probe", PROBE_STUBS + probe + PROBE_MAIN)
        folder = Path(tmp) / "folder"
        folder.mkdir()
        run(cxx, tmp, "entries", ENTRIES_STUBS + cut(dlss, "std::vector<fs::path> entries(const fs::path& d) {") +
            ENTRIES_MAIN, [str(folder)])
    print("PASS: NGX starts only on a device it runs on, is asked once from any thread, and its folders are read "
          "without a throw")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
