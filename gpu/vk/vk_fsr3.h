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

/// One evaluation's inputs. Every field is the SDK's meaning, not a guess: the colour is HDR and
/// jittered at the render size, the depth is reversed, the motion points at the previous frame, and
/// the jitter is in render pixels (input texel k is sampled at k + 0.5 - jitter).
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
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    float frame_time_ms = 16.667f;
    bool reset = false;
    bool sharpen = false;
    float sharpness = 0.0f;   ///< 0 to 1 as FSR takes it
};

/// Whether this device can run the passes at all: push descriptors, and every storage format the
/// SDK's internal images need. Asked once; the answer is cached.
[[nodiscard]] bool supported();

/// Makes the pipelines and the internal images for an evaluation at these sizes, re-making them
/// when the sizes change. False when anything could not be made, which leaves FSR unavailable
/// rather than half-built.
[[nodiscard]] bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
                           uint32_t upscale_height);

/// Records one evaluation. False when the inputs are not usable, which leaves the caller to keep
/// whatever it would otherwise have done.
[[nodiscard]] bool upscale(VkCommandBuffer cmd, const Inputs& in);

/// (diagnostics) How far down the upscale path a run goes: RX_VK_FSR_STAGE, 0 (the whole path) by
/// default. 1 stops before FSR, 2 drops the nine dispatches, 3 drops the blit into the game's
/// target. It exists so a run names a cost instead of a hypothesis; every stage but 0 is wrong
/// output on purpose.
int fsr_stage();

/// (diagnostics) Called once where the scene's upscale is reached, whatever stage the run is in:
/// every 30 calls it logs the milliseconds between them, which is the frame time when the scene
/// evaluates once a frame. One line per run, so a stage that is the cost shows up as a stage whose
/// removal returns the rate.
void note_frame();

/// The image an evaluation resolves into: RGBA16F at the upscale size, or null before prepare().
[[nodiscard]] VkImage output_image();
[[nodiscard]] VkImageView output_view();

/// Frees everything. Safe to call when nothing was made.
void shutdown();

} // namespace mkwvk::fsr3
