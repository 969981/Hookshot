// (hookshot) The sparse mirrors' buffers kept for the next mirror of their size (vk_memory.cpp, RX_VK_SPARSE_SPARE_MB), apart
// from Vulkan so that tests/test_sparse_spare.py can run it.
//
// A sparse mirror's buffer holds no memory of its own, only address space, yet NVIDIA's Windows driver takes about 0.06 ms a
// MB to create one and 0.07 ms a MB to destroy one, and the game lets go of its pools and makes them again by the hundred
// at an area change. Castle Town to Hyrule Field, one run of 95 s (RTX 4070 SUPER, 617.42): 405 buffers created (7.8 GB of
// address space, 475 ms, single ones up to 29 ms) and 336 destroyed (424 ms), the destructions 41 to 56 at a time at the
// start of one submission: four stalls of 52 to 62 ms that no column of the frame trace holds. 174 of the 405 were of 4 MB,
// 61 of 8 MB: the sizes come again.
//
// So a freed mirror's buffer, once the GPU is past it, has what is bound to it unbound (every such buffer's blocks in one
// bind) and is kept here; the next mirror made with exactly its size takes it and binds its own blocks. A spare holds
// address space and no memory. Over the cap the oldest spares go, destroyed once their unbinding is done on the GPU.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace sparse_spare {

/// The address space the spares may hold together, in MB, where RX_VK_SPARSE_SPARE_MB does not say (0 there: no spares,
/// every buffer destroyed, as before). The run above never held more than 2.3 GB in 108 buffers
constexpr uint64_t kDefaultMb = 4096;

/// A buffer nothing uses: its handle, the size it was created with, what its memory requirements said then (its blocks
/// are asked for with them again), and the value of the binds' timeline at which nothing is bound to it any more
template <class Handle>
struct Spare {
    Handle buffer{};
    uint64_t made = 0;    // (the size it was created with: a mirror of exactly this size takes it)
    uint64_t size = 0;    // (its requirements' size: whole blocks)
    uint64_t block = 0;   // (its block: the requirements' alignment)
    uint32_t types = 0;   // (the memory types its blocks can be of)
    uint64_t after = 0;   // (its unbinding's value on the binds' timeline: not destroyed before the GPU has reached it)
};

template <class Handle>
struct List {
    std::vector<Spare<Handle>> items;   // (oldest first)
    uint64_t held = 0;                  // (their created sizes together)

    /// Keeps the buffer if it fits in `cap` bytes at all, and returns true; the oldest spares that have to go for it are
    /// added to `gone` for the caller to destroy (each after its own `after`). False: not kept (no size, or larger than
    /// the cap), and it is the caller's to destroy
    bool put(const Spare<Handle>& s, uint64_t cap, std::vector<Spare<Handle>>* gone) {
        if (!s.made || s.made > cap) return false;
        size_t n = 0;
        while (n < items.size() && held > cap - s.made) {
            held -= items[n].made;
            gone->push_back(items[n]);
            n++;
        }
        items.erase(items.begin(), items.begin() + (std::ptrdiff_t)n);
        items.push_back(s);
        held += s.made;
        return true;
    }

    /// The spare created with exactly `made` bytes, the one kept last; false: none
    bool take(uint64_t made, Spare<Handle>* s) {
        for (size_t i = items.size(); i-- > 0;) {
            if (items[i].made != made) continue;
            *s = items[i];
            held -= made;
            items.erase(items.begin() + (std::ptrdiff_t)i);
            return true;
        }
        return false;
    }
};

} // namespace sparse_spare
