"""A program cache record is unpacked only when it is a real zstd record of a sane size (gpu/vk/vk_progcache.cpp
program_cache_payload). Audit 2026-11-14, PR-C F1.

A record whose kind word has the compressed bit was unpacked whether or not the cache is the zstd one, into a buffer of the
size its first 8 bytes claim, up to 2 GiB, on each of the threads that load programs: a bad record, or one of another
cache's, asked for gigabytes at every start. Now the bit counts in the zstd cache only, the size must be the zstd frame's
own content size, and it is capped at 64 MiB (the largest record of a whole Ocarina cache, 56,173 of them, is 4.4 MB).

This cuts program_cache_payload out of the source, compiles it with the repository's zstd decoder (third_party/zstd) and
runs it on frames written here: zstd's raw blocks, valid frames with no compressor needed.

python -I -B tests/test_program_cache_payload.py [--cxx clang++]   (zstd's C compiled by it, as C)
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]
ZSTD = ROOT / "third_party" / "zstd" / "lib"
ZSTD_C = ("common/debug.c", "common/entropy_common.c", "common/error_private.c", "common/fse_decompress.c", "common/xxhash.c",
          "common/zstd_common.c", "decompress/huf_decompress.c", "decompress/zstd_ddict.c", "decompress/zstd_decompress.c",
          "decompress/zstd_decompress_block.c")

MAIN = r"""
#include <cstdio>
#include <vector>
// (one zstd frame with a single raw block of `content`, and its content size in the header)
static std::vector<uint8_t> frame(const std::vector<uint8_t>& content) {
    std::vector<uint8_t> f = {0x28, 0xB5, 0x2F, 0xFD};   // (the magic, little-endian)
    f.push_back(0xA0);                                   // (single segment, a 4-byte content size: FCS flag 2)
    const uint32_t n = (uint32_t)content.size();
    for (int i = 0; i < 4; i++) f.push_back((uint8_t)(n >> (8 * i)));
    const uint32_t bh = (n << 3) | 1;                    // (a raw block, the last one)
    for (int i = 0; i < 3; i++) f.push_back((uint8_t)(bh >> (8 * i)));
    f.insert(f.end(), content.begin(), content.end());
    return f;
}
static std::vector<uint8_t> record(uint64_t usize, const std::vector<uint8_t>& fr) {
    std::vector<uint8_t> r(8);
    std::memcpy(r.data(), &usize, 8);
    r.insert(r.end(), fr.begin(), fr.end());
    return r;
}
int main() {
    int failed = 0;
    auto expect = [&](const char* what, bool got, bool want) {
        if (got != want) failed++, std::printf("FAIL %s: %d, wanted %d\n", what, got, want);
        else std::printf("ok   %s\n", what);
    };
    std::vector<uint8_t> content(1000);
    for (size_t i = 0; i < content.size(); i++) content[i] = (uint8_t)(i * 7);
    const std::vector<uint8_t> good = record(content.size(), frame(content));
    std::vector<uint8_t> plain;
    const uint8_t* payload = nullptr;
    size_t payload_size = 0;
    disk_zstd = true;
    expect("a zstd record in the zstd cache is unpacked",
           program_cache_payload(good.data(), good.size(), kCompressedKind, plain, &payload, &payload_size) &&
               payload_size == content.size() && std::memcmp(payload, content.data(), content.size()) == 0, true);
    disk_zstd = false;
    std::vector<uint8_t>().swap(plain);   // (a fresh one: what each case allocates is its own)
    expect("the compressed bit in the other cache is no record's",
           program_cache_payload(good.data(), good.size(), kCompressedKind, plain, &payload, &payload_size), false);
    expect("  and nothing was allocated for it", plain.capacity() == 0, true);
    disk_zstd = true;
    const std::vector<uint8_t> lying = record(500, frame(content));
    std::vector<uint8_t>().swap(plain);
    expect("an unpacked size the frame does not have",
           program_cache_payload(lying.data(), lying.size(), kCompressedKind, plain, &payload, &payload_size), false);
    expect("  and nothing was allocated for it", plain.capacity() == 0, true);
    const std::vector<uint8_t> huge = record(1ull << 31, frame(content));
    std::vector<uint8_t>().swap(plain);
    expect("2 GiB claimed", program_cache_payload(huge.data(), huge.size(), kCompressedKind, plain, &payload, &payload_size),
           false);
    expect("  and nothing was allocated for it", plain.capacity() == 0, true);
    const std::vector<uint8_t> raw = {1, 2, 3};
    expect("an uncompressed record as it is",
           program_cache_payload(raw.data(), raw.size(), 0, plain, &payload, &payload_size) && payload == raw.data(), true);
    return failed;
}
"""


def cut(text, start):
    i = text.index(start)
    return text[i:text.index("\n}\n", i) + 3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("compiler not found: %s" % args.cxx)
    src = (ROOT / "gpu/vk/vk_progcache.cpp").read_text(encoding="utf-8")
    consts = "".join(line + "\n" for line in src.splitlines()
                     if line.startswith(("constexpr uint32_t kCompressedKind", "constexpr uint64_t kMaxUnpacked")))
    if "kMaxUnpacked" not in consts:
        raise SystemExit("no kMaxUnpacked in vk_progcache.cpp: a compressed record's size is not capped")
    body = cut(src, "bool program_cache_payload(")
    code = ('#include <cstdint>\n#include <cstring>\n#include <vector>\n#include "zstd.h"\nbool disk_zstd = false;\n' +
            consts + body + MAIN)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="hookshot-progcache-payload-") as tmp:
        t = Path(tmp)
        (t / "t.cpp").write_text(code, encoding="utf-8")
        objs = []
        for f in ZSTD_C:
            o = t / (Path(f).stem + ".o")
            r = subprocess.run([cxx, "-x", "c", "-O1", "-c", "-w", "-DZSTD_DISABLE_ASM", "-I" + str(ZSTD), "-I" + str(ZSTD / "common"), str(ZSTD / f), "-o", str(o)],
                               capture_output=True, text=True, timeout=180, creationflags=flags)
            if r.returncode:
                raise SystemExit("zstd's %s does not compile:\n%s" % (f, r.stdout + r.stderr))
            objs.append(str(o))
        exe = t / ("t.exe" if os.name == "nt" else "t")
        r = subprocess.run([cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(ZSTD), str(t / "t.cpp")] + objs +
                           ["-o", str(exe)], capture_output=True, text=True, timeout=180, creationflags=flags)
        if r.returncode:
            raise SystemExit("program_cache_payload does not compile:\n" + r.stdout + r.stderr)
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, creationflags=flags)
        print(r.stdout.strip())
        if r.returncode:
            raise SystemExit(1)
    print("PASS: a program cache record is unpacked only when it is a real zstd record of a sane size")


if __name__ == "__main__":
    main()
