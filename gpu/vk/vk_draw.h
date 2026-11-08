// Draws and dispatches (vk_draw.cpp).
#pragma once

#include "vk_internal.h"

namespace mkwvk {
/// Something other than the draw path pushed or bound descriptors in the recording (its next push is whole)
void descriptors_disturbed();
}

#include <functional>

#include "vk_internal.h"
#include "vk_state.h"

namespace mkwvk {

struct DrawArgs {
    int prim = 0;
    bool indexed = false;
    int itype = 0;            // NVNindexType: byte, short, int
    int count = 0, first = 0, base_vertex = 0, base_instance = 0, instances = 1;
    uint64_t ibuf = 0;
    // indirect: arguments at indirect (stride, up to max), their count at count_addr (or 0)
    uint64_t indirect = 0, count_addr = 0;
    int max = 0, stride = 0;
    // mesh shading (NVN2 task / mesh programs): workgroups, or indirect as above
    bool mesh = false;
    int groups[3] = {0, 1, 1};
    int first_task = 0;   // (hookshot: a direct mesh draw's first task workgroup, NVN's DrawMeshTasks(first, count))
};

/// A recording starts (its slot's descriptor pool and upload stream are reset); value: the timeline value it signals
void draw_begin_recording(int slot, VkCommandBuffer cmd, uint64_t value);
/// The recording's stream still holds [at, at + n) in the mapping at base (a reset starts used over)
bool stream_holds(const uint8_t* base, uint64_t at, uint64_t n);
/// The open render pass ends (before transfers, clears, barriers and the recording's end)
void end_pass(VkCommandBuffer cmd);
void draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d);
/// Clears of the bound targets, inside the open pass (NOP_CLEAR_COLOR, NOP_CLEAR_DS)
void clear_color_target(VkCommandBuffer cmd, NvnState& s, int index, const float rgba[4], uint32_t mask);
void clear_depth_stencil(VkCommandBuffer cmd, NvnState& s, float depth, bool depth_mask, uint32_t stencil,
                         uint32_t stencil_mask);
void clear_stats(int64_t* clears, int64_t* masked);
/// indirect: the workgroup counts' GPU address (x, y, z unused)
void dispatch(VkCommandBuffer cmd, NvnState& s, uint32_t x, uint32_t y, uint32_t z, uint64_t indirect);
void dry_mark_written(NvnState& s, bool compute, bool first);   // (hookshot: the parallel replay's dry pass)
void draw_stats(int64_t* draws, int64_t* skipped, int64_t* dispatches, int64_t* pipelines);
void draw_stats2(int64_t* passes, int64_t* mesh, double* pipeline_ms);
void draw_stats3(int64_t* pending_skips, int64_t* waits, double* wait_ms);
void draw_skip_report(double frames);   // (hookshot) skips by the line that made them
int64_t draw_desc_reused();
int64_t draw_desc_prog_reused();
/// RX_VK_FLASH_RECORD=<key>,...: the last frames' draws of those programs, written to the log for frames around a flash
void draw_records_log(uint64_t frame_lo, uint64_t frame_hi);
void pass_traces_log(uint64_t frame_lo, uint64_t frame_hi);   // (RX_VK_PASS_TRACE=1)
/// Changes with every uniform update the stream makes (vk_replay.cpp): descriptors bound before may be stale
uint64_t ub_generation();
/// Uniform updates this replay place can see (a worker: those before its segment)
size_t ub_update_count();
/// True when an update at or after mark overlaps a range, or the list shrank below mark. No ranges: false.
bool ub_since_miss(size_t mark, const uint64_t* addr, const uint64_t* len, int count);
/// True when a range of the first list overlaps a range of the second. Empty overlaps nothing. A wrapping length overlaps.
bool range_lists_overlap(const uint64_t* a, const uint64_t* an, int na, const uint64_t* b, const uint64_t* bn, int nb);
/// Set while a descriptor set is built, so the uniform ranges that build reads are remembered (off: one load)
extern thread_local bool desc_watch_on;
void desc_note_range_slow(uint64_t addr, uint64_t n);
inline void desc_note_range(uint64_t addr, uint64_t n) {
    if (desc_watch_on && n) desc_note_range_slow(addr, n);
}
/// The pipeline cache to disk (force: whatever was built since the last save; else only after a few new ones)
void pipeline_cache_save(bool force);

/// The stream's uniform updates (UPDATE_UB, vk_replay.cpp) that overlap [addr, addr + n) and may not be in guest
/// memory yet when the GPU reads it: a copy of the range with them applied, at the offset alloc returns in the
/// recording's stream (mapped at base), or UINT64_MAX when none overlaps
uint64_t ub_overlay(uint64_t addr, uint64_t n, uint8_t* base, const std::function<uint64_t(uint64_t)>& alloc);
/// Guest memory as the stream's place sees it: as submitted, the stream's uniform updates before it over it
bool ub_read(uint64_t addr, void* dst, uint64_t n);

} // namespace mkwvk
