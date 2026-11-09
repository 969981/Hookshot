// (hookshot DLSS) NVIDIA DLSS Super Resolution in the place of OoT's gameplay TAAU network (vk_dlss.cpp, docs/dlss.md).
// Off unless the Insert menu asks for it and hookshot_ngx.dll loaded. With either missing, the network runs as before.
#pragma once

#include "vk_internal.h"

namespace mkwvk {

struct Texture;

/// (vk_main.cpp, before the instance and the device are made) what NGX needs enabled, where hookshot_ngx.dll is
/// beside the game: the extensions of `have` it asks for that `want` does not have yet. No DLL: nothing added
void dlss_instance_extensions(const std::vector<VkExtensionProperties>& have, std::vector<const char*>& want);
void dlss_device_extensions(const std::vector<VkExtensionProperties>& have, std::vector<const char*>& want);
void dlss_device_extensions_refused();   // (the device would not take them: DLSS off)
void dlss_shutdown();   // (before the device goes, when a shutdown path calls it)

/// DLSS chosen in the Insert menu (or RX_DLSS), and nothing known yet that rules it out
bool dlss_wanted();
/// A short note for the menu's DLSS row: why it is not running, or "" when it is (or has not been tried yet)
const char* dlss_state();

/// One TAAU dispatch's inputs, resolved from its descriptors (vk_draw.cpp taau_dlss)
struct DlssInputs {
    VkImageView color_view = VK_NULL_HANDLE;   // the network's scene colour (square-root encoded)
    uint32_t render_w = 0, render_h = 0;
    VkImageView mv_view = VK_NULL_HANDLE;      // its motion (null: none found)
    VkImageView depth_view = VK_NULL_HANDLE;   // its depth (null: none found)
    VkImageView out_view = VK_NULL_HANDLE;     // its output, a B10G11R11 storage view of the level it writes
    uint32_t out_w = 0, out_h = 0;
    bool mv_found = false;                     // (what the binding search found, for the menu's note)
    bool mv_unorm = false;                     // the motion is in an unsigned normalized format: 0.5 is no motion
    bool depth_found = false;                  // (a scene depth target was found, vk_dlss.cpp dlss_find_depth)
    bool camera = false;                       // view_proj holds this frame's camera (the game's constants)
    float view_proj[16] = {};                  // row-major, GL clip space (z -w..w), as the game keeps it
};
/// The menu's DLSS quality row: the size DLSS takes now. False: DLSS is not running
bool dlss_input_size(uint32_t& w, uint32_t& h);
/// (build 5, vk_draw.cpp) a TAAU network DLSS can take ran this frame (its key and input size)
void dlss_taau_seen(uint64_t key, uint32_t w, uint32_t h);
/// (build 5, vk_replay.cpp present) once a present: the DLSS / quality / motion rows logged at the start and on every change,
/// and why DLSS is not running when it is chosen and has not run for a while
void dlss_frame_status();
/// (vk_draw.cpp end_pass) a pass's depth target, with its size and draws, for the scene depth DLSS reprojects with.
/// RX_DLSS_LOG_CANDIDATES=1: every pass of a few frames is logged too
void dlss_note_pass(Texture* depth, uint32_t width, uint32_t height, int draws, int colours, VkFormat colour0, uint64_t prog);
/// The scene depth for a render size: the depth target of that size (or its shape) with the most draws this frame (or the
/// last one). Null: none
VkImageView dlss_find_depth(uint32_t w, uint32_t h, uint32_t* found_w = nullptr, uint32_t* found_h = nullptr);
bool dlss_log_candidates();
void dlss_log_candidates_from(uint64_t frame);   // (vk_draw.cpp, the first TAAU)
/// DLSS recorded into cmd in the network's place: true, its output written. False: the caller runs the network (or
/// the stand-in) as it would have, nothing of DLSS's left in the way
bool dlss_run(VkCommandBuffer cmd, const DlssInputs& in);

} // namespace mkwvk
