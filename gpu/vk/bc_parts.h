/* ASTC textures re-encoded as BC3, uploaded a few runs of layers a recording (vk_texture.cpp's upload_astc_gpu). */
#pragma once

#include <cstddef>
#include <cstdint>

namespace mkwvk::bc_parts {

/// The end of the run of consecutive layers that starts at layers[r0], max_run layers at most
inline size_t run_end(const uint32_t* layers, size_t n, size_t r0, size_t max_run) {
    size_t r1 = r0 + 1;
    while (r1 < n && r1 - r0 < max_run && layers[r1] == layers[r1 - 1] + 1) r1++;
    return r1;
}

/// Whether a run of `run` texels goes into a recording whose upload holds `used` already: the first run of an upload always
/// does (each one makes progress), the others while they fit the budget (0: no budget, all at once)
inline bool run_fits(uint64_t used, uint64_t run, uint64_t budget, bool first) {
    return !budget || first || used + run <= budget;
}

/// A new image's deferred layers are filled, until their own are in, from the last layer this upload encoded (layers[r0 - 1]):
/// left as they were they showed whatever the image's memory held before, other textures' BC3 blocks from the image pool
/// (opaque black, purple, yellow squares in the sky). ~0u: nothing encoded yet, nothing to fill from
inline uint32_t fill_source(const uint32_t* layers, size_t r0) {
    return r0 ? layers[r0 - 1] : ~0u;
}

/// A continuation that could not record (no staging, no scratch) goes back on the list this many times, then it is given up
/// with a log line; before, it was dropped at once and logged as finished, and the texture kept the old memory for good
constexpr int kContinueTries = 8;
inline bool retry_continue(int tries) {
    return tries < kContinueTries;
}

} // namespace mkwvk::bc_parts
