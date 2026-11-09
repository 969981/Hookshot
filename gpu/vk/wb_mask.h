#pragma once
// (hookshot) Masked write-backs: which parts of a submission's write-back ranges have a pre-image (the mirror's words
// before the submission's work ran), so that only the words the GPU changed go back to guest memory. A write-back
// copied a written binding's whole range from the mirror, and with it the mirror's stale copy of every word the CPU
// wrote since the pool's sync: the targeting cursor's arrows (a 16 KB slot buffer a particle pass binds as written)
// were filled in by the CPU a few frames apart and put back to their 0x6f fill, 1-3 of the 4 arrows gone.
#include <algorithm>
#include <cstdint>
#include <iterator>
#include <map>
#include <utility>
#include <vector>

namespace mkwvk {

struct PreCover {
    struct Span {
        uint64_t hi, at;   // its end, and where its pre-image is in the pre-image buffer
    };
    std::map<uint64_t, Span> spans;   // (disjoint, by start; offsets in the pool)

    struct Piece {
        uint64_t lo, at, size;   // a pool offset, its pre-image's offset, the bytes
    };

    /// [lo, hi) covered: each part without a pre-image yet gets one where alloc(bytes, at) puts it (false: none, that
    /// part is left out and written back whole); copies gets what to copy there
    template <class Alloc> void add(uint64_t lo, uint64_t hi, Alloc&& alloc, std::vector<Piece>& copies) {
        uint64_t cur = lo;
        auto it = spans.upper_bound(lo);
        if (it != spans.begin()) cur = std::max(cur, std::prev(it)->second.hi);
        while (cur < hi) {
            const uint64_t end = it != spans.end() ? std::min(hi, it->first) : hi;
            if (end > cur) {
                uint64_t at = 0;
                if (alloc(end - cur, at)) {
                    spans.emplace(cur, Span{end, at});
                    copies.push_back({cur, at, end - cur});
                }
            }
            if (it == spans.end()) break;
            cur = std::max(cur, it->second.hi);
            ++it;
        }
    }

    /// [lo, hi) split into the parts with a pre-image (masked) and the rest (plain: [lo, hi) pool offsets)
    void split(uint64_t lo, uint64_t hi, std::vector<Piece>& masked, std::vector<std::pair<uint64_t, uint64_t>>& plain) const {
        uint64_t cur = lo;
        auto it = spans.upper_bound(lo);
        if (it != spans.begin()) --it;
        while (cur < hi) {
            if (it != spans.end() && it->second.hi <= cur) {
                ++it;
                continue;
            }
            if (it != spans.end() && it->first <= cur) {
                const uint64_t end = std::min(hi, it->second.hi);
                masked.push_back({cur, it->second.at + (cur - it->first), end - cur});
                cur = end;
                ++it;
                continue;
            }
            const uint64_t end = it != spans.end() ? std::min(hi, it->first) : hi;
            plain.push_back({cur, end});
            cur = end;
        }
    }
};

/// The pre-image buffer as a ring: a submission's pre-images are reused once the GPU finished it
struct PreRing {
    struct Block {
        uint64_t lo, hi, value;
    };
    uint64_t size = 0, next = 0;
    std::vector<Block> live;   // (oldest first)

    /// n bytes (16-aligned) for the submission value; false when the ring holds no such free range now
    bool take(uint64_t n, uint64_t value, uint64_t completed, uint64_t& at) {
        n = (n + 15) & ~uint64_t{15};
        live.erase(std::remove_if(live.begin(), live.end(), [&](const Block& b) { return b.value <= completed; }), live.end());
        if (!n || n > size) return false;
        uint64_t a = next + n > size ? 0 : next;
        for (const Block& b : live)
            if (a < b.hi && b.lo < a + n) return false;
        at = a, next = a + n;
        if (!live.empty() && live.back().value == value && live.back().hi == a) live.back().hi = a + n;
        else live.push_back({a, a + n, value});
        return true;
    }
};

}  // namespace mkwvk
