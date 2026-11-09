// (hookshot) NVIDIA DLSS Super Resolution as a backend for the game's own upscale dispatch:
// docs/upscaling.md and gpu/vk/upscale_seam.h. The same shape as gpu/vk/vk_fsr3.h, so the seam can
// hold either one, and the same assembled inputs: the depth the scene passes carry and the motion
// gpu/vk/vk_camera_motion.h reprojects from it, because this dispatch is handed neither.
//
// Nothing of NVIDIA's is in this repository and nothing of NVIDIA's is needed to build it. DLSS is
// reached through NGX on Linux, and NGX's API -- not just its runtime -- is NVIDIA's licensed SDK,
// so the NGX half of the backend sits behind a build-time gate, `RX_VK_DLSS_SDK`, that is off by
// default. With the gate off this file still compiles, links and runs: every entry point below
// answers, and `supported()` answers false. The "DLSS Super Resolution" section of
// docs/upscaling.md says why the ABI is not declared here by hand instead.
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk::dlss {

/// One evaluation's inputs. The first block is FSR's, field for field and meaning for meaning (see
/// gpu/vk/vk_fsr3.h): the colour is HDR and jittered at the render size, the depth is reversed, the
/// motion points at the previous frame, and the jitter is in render pixels.
struct Inputs {
    VkImageView color = VK_NULL_HANDLE;
    VkImageView depth = VK_NULL_HANDLE;
    VkImageView motion = VK_NULL_HANDLE;
    uint32_t render_width = 0, render_height = 0;
    uint32_t upscale_width = 0, upscale_height = 0;
    float jitter[2]{};
    /// Turns a motion vector into render pixels (the render size, for vectors in UV).
    float motion_scale[2]{1.0f, 1.0f};
    /// The game's camera. DLSS does not take a projection, so unlike FSR these two only decide
    /// whether the inputs are believable at all; an evaluation refuses a non-positive one rather
    /// than carrying on with a camera it was not given.
    float near_plane = 0.1f;
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    float frame_time_ms = 16.667f;
    bool reset = false;
    bool sharpen = false;
    /// 0 to 1. DLSS's own sharpening pass is deprecated from 3.x on and newer runtimes ignore it,
    /// so this is passed through and nothing is promised by it.
    float sharpness = 0.0f;

    // ---- what NGX needs and FSR does not ----
    //
    // FSR records its own compute passes, so a view is the whole of an input to it. NGX is handed
    // resources instead: an `NVSDK_NGX_Resource_VK` is a view *and* its image, its subresource
    // range, its format and its extent, and the extent it is given is the extent DLSS reads. There
    // is nothing to default these to -- a wrong format or a null image is a wrong picture or a
    // crash inside the runtime, not a worse picture -- so an evaluation refuses without them. This
    // is the one place `dlss::Inputs` is not `fsr3::Inputs`, and it is why it is its own struct.
    VkImage color_image = VK_NULL_HANDLE;
    VkImage depth_image = VK_NULL_HANDLE;
    VkImage motion_image = VK_NULL_HANDLE;
    VkFormat color_format = VK_FORMAT_UNDEFINED;
    VkFormat depth_format = VK_FORMAT_UNDEFINED;
    VkFormat motion_format = VK_FORMAT_UNDEFINED;
};

/// Whether DLSS can run here at all: asked for (`RX_VK_DLSS=1`), built with an NGX SDK, the NGX
/// runtime loaded, and this driver and GPU reporting Super Resolution as available. Asked once; the
/// answer is cached, and the one line saying why it is false is logged once.
[[nodiscard]] bool supported();

/// Makes the image an evaluation resolves into and remembers these extents, re-making them when the
/// extents change. False when anything could not be made, which leaves DLSS unavailable rather than
/// half-built.
///
/// Unlike FSR's, this does not make the thing that does the work: `NVSDK_NGX_VULKAN_CreateFeature`
/// records into a command buffer, and there is none here. The feature is created at the first
/// `upscale()` after a change of extents instead, which is why this signature can stay FSR's.
/// The instance and device extensions DLSS needs here, asked of NGX itself rather than written out
/// from the documentation: the set depends on the driver, and a list hard-coded here would be wrong
/// on the next one. Both answer before the thing they are about exists, which is why they take what
/// they do and not `dev`. `out` is filled with at most `max` names and the count is returned; zero
/// means DLSS was not asked for, this build has no NGX SDK, or NGX does not answer -- in every one
/// of those the device is created as it would have been. The names are NGX's own storage and last
/// as long as the process.
///
/// Call them before vkCreateInstance / vkCreateDevice. DLSS cannot be made to work afterwards: an
/// extension it needs and the device does not have is the one failure that is not recoverable at
/// evaluation time.
size_t instance_extensions(const char** out, size_t max);
size_t device_extensions(VkInstance instance, VkPhysicalDevice gpu, const char** out, size_t max);

[[nodiscard]] bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
                           uint32_t upscale_height);

/// Records one evaluation. False when the inputs are not usable, which leaves the caller to keep
/// whatever it would otherwise have done.
[[nodiscard]] bool upscale(VkCommandBuffer cmd, const Inputs& in);

/// The image an evaluation resolves into: RGBA16F at the upscale size, or null before prepare().
[[nodiscard]] VkImage output_image();
[[nodiscard]] VkImageView output_view();

/// Frees everything, the NGX feature and NGX itself included. Safe to call when nothing was made.
void shutdown();

} // namespace mkwvk::dlss
