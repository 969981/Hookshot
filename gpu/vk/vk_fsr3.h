// (hookshot) AMD FidelityFX Super Resolution 3.1 (the FSR 3.1.4 upscaler from FidelityFX SDK
// v1.1.4, shaders in third_party/fidelityfx-sdk compiled by scripts/fsr3_shaders.py) as a backend
// for the game's own upscale dispatch.
//
// This file holds what an evaluation needs before it can be recorded -- the eleven pipelines and
// the SDK's internal images -- and nothing that records one. The dispatch half follows.
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk::fsr3 {

/// One evaluation's inputs, the ones vk_draw.cpp builds for FSR 4.1 too. The colour is tonemapped and
/// square-root encoded (LDR, in [0, 1]) and jittered at the render size, the depth is standard (0 near,
/// 1 far), the motion points at the previous frame in render pixels, and the jitter is in render pixels
/// (input texel k is sampled at k + 0.5 - jitter). The pass shaders are compiled for exactly this:
/// scripts/fsr3_shaders.py. A view of the same inputs in another colour or depth convention would need
/// new shaders.
struct Inputs {
    VkImageView color = VK_NULL_HANDLE;
    VkImageView depth = VK_NULL_HANDLE;
    VkImageView motion = VK_NULL_HANDLE;
    uint32_t render_width = 0, render_height = 0;
    uint32_t upscale_width = 0, upscale_height = 0;
    float jitter[2]{};
    /// Turns a motion vector into render pixels (the render size, for vectors in UV).
    float motion_scale[2]{1.0f, 1.0f};
    /// The game's camera, which device_to_view_depth and tan_half_fov are built from. That
    /// conversion only scales FSR's disocclusion thresholds, so a typical camera is usable and is
    /// what these default to; supplying the real one is better but is not a precondition.
    float near_plane = 0.1f;
    float far_plane = 1000.0f;
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    float frame_time_ms = 16.667f;
    bool reset = false;
    bool sharpen = false;
    float sharpness = 0.0f;   ///< 0 to 1 as FSR takes it
};

/// Whether this device can run the passes at all: push descriptors, and every storage format the
/// SDK's internal images need. Asked once; the answer is cached.
[[nodiscard]] bool supported();

/// supported(), and nothing has failed for good since (a pipeline or an image that could not be
/// made). What the Insert menu and the upscaler choice ask each frame.
[[nodiscard]] bool available();

/// Why not available(), short, for the menu and the log; empty while it is. Starts nothing.
[[nodiscard]] const char* reason();

/// Makes the pipelines and the internal images for an evaluation at these sizes, re-making them
/// when the sizes change. False when anything could not be made, which leaves FSR unavailable
/// rather than half-built.
[[nodiscard]] bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
                           uint32_t upscale_height);

/// Records one evaluation. False when the inputs are not usable, which leaves the caller to keep
/// whatever it would otherwise have done.
[[nodiscard]] bool upscale(VkCommandBuffer cmd, const Inputs& in);

/// The image an evaluation resolves into: RGBA16F at the upscale size, or null before prepare().
[[nodiscard]] VkImage output_image();
[[nodiscard]] VkImageView output_view();

/// Frees everything. Safe to call when nothing was made.
void shutdown();

} // namespace mkwvk::fsr3
