// (hookshot) AMD FidelityFX Super Resolution 3.1 (the FSR 3.1.4 upscaler from FidelityFX SDK
// v1.1.4, shaders in third_party/fidelityfx-sdk compiled by scripts/fsr3_shaders.py) as a backend
// for the game's own upscale dispatch: docs/upscaling.md and gpu/vk/upscale_seam.h.
//
// This file holds what an evaluation needs before it can be recorded -- the eleven pipelines and
// the SDK's internal images -- and nothing that records one. The dispatch half follows.
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk::fsr3 {

/// Whether this device can run the passes at all: push descriptors, and every storage format the
/// SDK's internal images need. Asked once; the answer is cached.
[[nodiscard]] bool supported();

/// Makes the pipelines and the internal images for an evaluation at these sizes, re-making them
/// when the sizes change. False when anything could not be made, which leaves FSR unavailable
/// rather than half-built.
[[nodiscard]] bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
                           uint32_t upscale_height);

/// The image an evaluation resolves into: RGBA16F at the upscale size, or null before prepare().
[[nodiscard]] VkImage output_image();
[[nodiscard]] VkImageView output_view();

/// Frees everything. Safe to call when nothing was made.
void shutdown();

} // namespace mkwvk::fsr3
