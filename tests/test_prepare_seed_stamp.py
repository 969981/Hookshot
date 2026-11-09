"""The shaders are prepared again when the seed changes, not only when the translator does.

prepare_run (gpu/vk/vk_draw.cpp) prepared once per translator: hookshot_prepared.bin held program_cache_stamp() and nothing
else. The seed (hookshot_shaders.bin, nx2/shader_census.py) grows apart from the translator (4221050d added 19 compute
programs), and a folder prepared before such an update never prepared the new programs: each was translated in play when
first met. The record now also holds the seed's hash, size and write time; prepare_check hashes the seed only when its size
or time is not the recorded one. This builds the record's helpers as they are in the source and checks, on files in a
temporary folder: the same seed is not prepared again (and not hashed again); a changed seed is, and so is a changed
translator; a seed rewritten as it was (a new time) is hashed once and not prepared; a record from before (the stamp alone)
prepares once; and that prepare_run uses all of this.
python -I -B tests/test_prepare_seed_stamp.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAW = ROOT / "gpu" / "vk" / "vk_draw.cpp"


def block(text, marker):
    start = text.index(marker)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cxx", default="g++")
    a = ap.parse_args()
    src = DRAW.read_text(encoding="utf-8")
    start = src.index("struct PreparedFor {")
    helpers = src[start:src.index("} // namespace", start)]
    cpp = r"""
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>
""" + helpers + r"""
int fails = 0;
std::string dir, rec, seed;
void put(const std::string& path, const std::string& bytes) {
    FILE* f = fopen(path.c_str(), "wb");
    fwrite(bytes.data(), 1, bytes.size(), f);
    fclose(f);
}
void later() {   // (a write time the file system tells apart from the last)
    std::error_code ec;
    const auto t = std::filesystem::last_write_time(seed, ec);
    std::filesystem::last_write_time(seed, t + std::chrono::seconds(5), ec);
}
/// One start: the check, and the record written when it prepares (as prepare_run does once done)
PrepareWhy start(uint64_t stamp, double* hash_ms = nullptr) {
    PreparedFor now;
    double ms = 0;
    const PrepareWhy why = prepare_check(rec, seed, stamp, &now, &ms);
    if (why != PrepareWhy::None) prepared_write(rec, now);
    if (hash_ms) *hash_ms = ms;
    return why;
}
void want(const char* what, PrepareWhy got, PrepareWhy wanted) {
    if (got != wanted) std::printf("%s: got %d, wanted %d\n", what, (int)got, (int)wanted), fails++;
}
int main(int argc, char** argv) {
    dir = argv[1], rec = dir + "/hookshot_prepared.bin", seed = dir + "/hookshot_shaders.bin";
    std::string bytes(3 * (1 << 20) + 77, '\0');   // (more than one read, and bytes over the last 32)
    for (size_t i = 0; i < bytes.size(); i++) bytes[i] = (char)(i * 2654435761u >> 13);
    put(seed, bytes);
    double ms = 0;
    want("first start", start(1), PrepareWhy::First);
    want("same seed, same translator", start(1, &ms), PrepareWhy::None);
    if (ms != 0) std::puts("same seed: hashed again"), fails++;
    want("changed translator", start(2), PrepareWhy::Translator);
    want("then the same again", start(2), PrepareWhy::None);
    // a seed rewritten with the same bytes (a build): hashed once, not prepared, and its new time kept
    later();
    want("rewritten as it was", start(2, &ms), PrepareWhy::None);
    if (ms == 0) std::puts("rewritten as it was: not hashed"), fails++;
    want("rewritten, the next start", start(2, &ms), PrepareWhy::None);
    if (ms != 0) std::puts("rewritten, the next start: hashed again"), fails++;
    // a grown seed (an update's new programs)
    put(seed, bytes + std::string(4096, 'x'));
    want("grown seed", start(2), PrepareWhy::Seed);
    want("grown seed, the next start", start(2), PrepareWhy::None);
    // one byte changed in the middle, the size the same (with a new write time, as any rewrite has)
    std::string changed = bytes + std::string(4096, 'x');
    changed[changed.size() / 2] ^= 1;
    put(seed, changed);
    later();
    want("one byte changed", start(2), PrepareWhy::Seed);
    // one byte in the last, partial block
    changed[changed.size() - 3] ^= 1;
    put(seed, changed);
    later();
    want("a tail byte changed", start(2), PrepareWhy::Seed);
    // a record from before the seed was in it: the translator's stamp alone, 8 bytes
    {
        const uint64_t stamp = 2;
        FILE* f = fopen(rec.c_str(), "wb");
        fwrite(&stamp, 8, 1, f);
        fclose(f);
    }
    want("old record, same translator", start(2), PrepareWhy::Seed);
    want("old record, then", start(2), PrepareWhy::None);
    {
        const uint64_t stamp = 2;
        FILE* f = fopen(rec.c_str(), "wb");
        fwrite(&stamp, 8, 1, f);
        fclose(f);
    }
    want("old record, changed translator", start(3), PrepareWhy::Translator);
    // no seed: the translator alone, as before
    std::filesystem::remove(seed);
    want("no seed, same translator", start(3), PrepareWhy::None);
    want("no seed, changed translator", start(4), PrepareWhy::Translator);
    if (fails) return 1;
    std::puts("ok");
}
"""
    with tempfile.TemporaryDirectory(prefix="seed-stamp-") as tmp:
        c, exe = Path(tmp) / "fixture.cpp", Path(tmp) / "fixture"
        c.write_text(cpp, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-O1", str(c), "-o", str(exe)], check=True)
        work = Path(tmp) / "game"
        work.mkdir()
        got = subprocess.run([str(exe), str(work)], capture_output=True, text=True)
        if got.returncode or got.stdout.strip() != "ok":
            raise SystemExit("prepare_check:\n" + got.stdout + got.stderr)
    run = block(src, "bool prepare_run(")
    for need, why in (("prepare_check(path, seed_path(), program_cache_stamp()", "asks prepare_check"),
                      ("prepared_write(path, prepared_for)", "records what it prepared for"),
                      ("why == PrepareWhy::Seed", "says when the seed made it prepare again")):
        if need not in run:
            raise SystemExit("prepare_run no longer " + why)
    if "fwrite(&stamp, 8, 1" in run:
        raise SystemExit("prepare_run writes the translator's stamp alone")
    print("PASS: the shaders are prepared again for a new seed or translator, and not for the same ones")


if __name__ == "__main__":
    main()
