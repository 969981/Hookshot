// tests/test_sparse_spare.py: gpu/vk/sparse_spare.h, the sparse mirrors' buffers kept for the next mirror of their size
#include "../gpu/vk/sparse_spare.h"

#include <cstdio>
#include <vector>

namespace {
int failures = 0;
void check(bool ok, const char* what) {
    if (!ok) failures++, std::printf("FAIL: %s\n", what);
}
using Spare = sparse_spare::Spare<int>;
using List = sparse_spare::List<int>;
constexpr uint64_t MB = 1 << 20;
Spare spare(int handle, uint64_t made, uint64_t after = 0) {
    return {handle, made, made, 64 * 1024, 0x82, after};
}
}  // namespace

int main() {
    const uint64_t cap = 64 * MB;
    std::vector<Spare> gone;
    Spare s;

    {   // a mirror of the same size takes the buffer, with what its requirements said; another size does not
        List l;
        check(l.put(spare(1, 4 * MB, 7), cap, &gone) && gone.empty() && l.held == 4 * MB, "a freed buffer is kept");
        check(!l.take(8 * MB, &s) && !l.take(4 * MB - 1, &s) && !l.take(4 * MB + 65536, &s), "only its exact size takes it");
        check(l.held == 4 * MB && l.items.size() == 1, "a miss takes nothing");
        check(l.take(4 * MB, &s) && s.buffer == 1 && s.made == 4 * MB && s.size == 4 * MB && s.block == 64 * 1024 && s.types == 0x82 && s.after == 7,
              "the spare comes back whole: its handle, sizes, block, memory types and its unbinding's value");
        check(l.held == 0 && l.items.empty() && !l.take(4 * MB, &s), "taken once");
    }
    {   // of several of one size, the one kept last; the others stay, in their order
        List l;
        for (int h = 1; h <= 3; h++) l.put(spare(h, 4 * MB), cap, &gone);
        l.put(spare(9, 8 * MB), cap, &gone);
        check(l.take(4 * MB, &s) && s.buffer == 3, "the spare kept last goes first");
        check(l.take(4 * MB, &s) && s.buffer == 2 && l.take(8 * MB, &s) && s.buffer == 9 && l.take(4 * MB, &s) && s.buffer == 1, "then the earlier ones");
        check(gone.empty() && l.held == 0, "nothing was given up under the cap");
    }
    {   // over the cap the oldest go, as many as the new one needs, each with its own value; the new one is kept
        List l;
        for (int h = 1; h <= 4; h++) l.put(spare(h, 16 * MB, (uint64_t)h * 10), cap, &gone);
        check(gone.empty() && l.held == cap, "exactly at the cap: all kept");
        check(l.put(spare(5, 8 * MB, 50), cap, &gone), "one more is kept");
        check(gone.size() == 1 && gone[0].buffer == 1 && gone[0].after == 10 && l.held == 56 * MB, "the oldest went for it, with its value");
        gone.clear();
        check(l.put(spare(6, 40 * MB, 60), cap, &gone) && gone.size() == 2 && gone[0].buffer == 2 && gone[1].buffer == 3,
              "a large one sends as many of the oldest as it needs and no more, oldest first");
        check(l.held == cap && l.items.size() == 3 && l.items[0].buffer == 4 && l.items[1].buffer == 5 && l.items[2].buffer == 6, "and the rest stay");
        gone.clear();
        check(l.put(spare(7, cap), cap, &gone) && gone.size() == 3 && l.held == cap && l.items.size() == 1, "one as large as the cap empties the list");
    }
    {   // what is never kept: no size (a mirror made before its size was recorded), larger than the cap, no cap at all
        List l;
        l.put(spare(1, 4 * MB), cap, &gone);
        gone.clear();
        check(!l.put(spare(2, 0), cap, &gone) && !l.put(spare(3, cap + 1), cap, &gone) && !l.put(spare(4, 4 * MB), 0, &gone),
              "no size, over the cap, or no cap: the caller's to destroy");
        check(gone.empty() && l.held == 4 * MB && l.items.size() == 1, "and nothing else was touched for it");
    }
    check(sparse_spare::kDefaultMb == 4096, "the default cap is 4 GB of address space");
    if (!failures) std::printf("sparse_spare: ok\n");
    return failures != 0;
}
