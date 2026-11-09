// Masked write-backs (gpu/vk/wb_mask.h): which parts of a write-back have a pre-image, and the pre-image ring.
#include "wb_mask.h"

#include <cstdio>
#include <vector>

using namespace mkwvk;

static int failures = 0;
#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) {                                                      \
            std::fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); \
            failures++;                                                  \
        }                                                                \
    } while (0)

int main() {
    uint64_t next_at = 0;
    const auto alloc = [&](uint64_t n, uint64_t& at) {
        at = next_at, next_at += n;
        return true;
    };

    {   // a range bound written twice gets one pre-image; an overlapping one only its new parts
        PreCover c;
        std::vector<PreCover::Piece> copies;
        c.add(0x100, 0x200, alloc, copies);
        CHECK(copies.size() == 1 && copies[0].lo == 0x100 && copies[0].size == 0x100 && copies[0].at == 0);
        copies.clear();
        c.add(0x100, 0x200, alloc, copies);
        CHECK(copies.empty());
        c.add(0x180, 0x300, alloc, copies);
        CHECK(copies.size() == 1 && copies[0].lo == 0x200 && copies[0].size == 0x100 && copies[0].at == 0x100);
        copies.clear();
        c.add(0x400, 0x500, alloc, copies);
        c.add(0x000, 0x600, alloc, copies);   // (the gaps before, between and after)
        CHECK(copies.size() == 4);
        CHECK(copies.size() == 4 && copies[1].lo == 0x000 && copies[1].size == 0x100);
        CHECK(copies.size() == 4 && copies[2].lo == 0x300 && copies[2].size == 0x100);
        CHECK(copies.size() == 4 && copies[3].lo == 0x500 && copies[3].size == 0x100);
        CHECK(c.spans.size() == 6);
    }
    {   // a write-back split: the covered parts point into their pre-images, the rest is copied whole
        PreCover c;
        std::vector<PreCover::Piece> copies, masked;
        std::vector<std::pair<uint64_t, uint64_t>> plain;
        next_at = 0x1000;
        c.add(0x100, 0x200, alloc, copies);   // pre-image at 0x1000
        c.add(0x300, 0x400, alloc, copies);   // at 0x1100
        c.split(0x080, 0x380, masked, plain);
        CHECK(masked.size() == 2);
        CHECK(masked.size() == 2 && masked[0].lo == 0x100 && masked[0].at == 0x1000 && masked[0].size == 0x100);
        CHECK(masked.size() == 2 && masked[1].lo == 0x300 && masked[1].at == 0x1100 && masked[1].size == 0x080);
        CHECK(plain.size() == 2);
        CHECK(plain.size() == 2 && plain[0].first == 0x080 && plain[0].second == 0x100);
        CHECK(plain.size() == 2 && plain[1].first == 0x200 && plain[1].second == 0x300);
        masked.clear(), plain.clear();
        c.split(0x340, 0x360, masked, plain);   // (inside one span: its pre-image at the same distance)
        CHECK(masked.size() == 1 && masked[0].at == 0x1140 && masked[0].size == 0x20 && plain.empty());
        masked.clear(), plain.clear();
        PreCover none;
        none.split(0x10, 0x20, masked, plain);
        CHECK(masked.empty() && plain.size() == 1 && plain[0].first == 0x10 && plain[0].second == 0x20);
    }
    {   // a pre-image left out (no room) is written back whole
        PreCover c;
        std::vector<PreCover::Piece> copies, masked;
        std::vector<std::pair<uint64_t, uint64_t>> plain;
        c.add(0, 0x100, [](uint64_t, uint64_t&) { return false; }, copies);
        CHECK(copies.empty() && c.spans.empty());
        c.split(0, 0x100, masked, plain);
        CHECK(masked.empty() && plain.size() == 1);
    }
    {   // the ring: a submission's blocks come back when it completed, not before
        PreRing r;
        r.size = 0x1000;
        uint64_t at = 0;
        CHECK(r.take(0x800, 5, 4, at) && at == 0);
        CHECK(r.take(0x600, 5, 4, at) && at == 0x800);
        CHECK(!r.take(0x400, 6, 4, at));   // (would wrap over submission 5's, not done)
        CHECK(r.take(0x400, 6, 5, at) && at == 0);   // (5 done)
        CHECK(r.take(0x9, 6, 5, at) && at == 0x400);   // (16-byte steps)
        CHECK(r.take(0x10, 6, 5, at) && at == 0x410);
        CHECK(!r.take(0x2000, 7, 6, at));   // (bigger than the ring)
    }
    if (failures) std::fprintf(stderr, "%d failed\n", failures);
    return failures ? 1 : 0;
}
