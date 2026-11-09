// (hookshot) AMD FSR 3 frame interpolation, and the optical flow it runs on, recorded as our own
// Vulkan compute passes: the second source of the frames the renderer shows between the game's.
//
// The renderer already shows made frames. vk_framegen.cpp interpolates between the last two
// presented frames from colour alone, which is all it has at present time, and vk_dlssg.cpp answers
// the same question with the scene's motion and depth through NVIDIA's network. This backend answers
// it with AMD's: seventeen compute passes -- seven of optical flow over two frames' luma, ten of
// interpolation over the game's motion and depth -- all of them our own dispatches over AMD's
// vendored GLSL, with no FidelityFX host library linked and nothing of AMD's loaded at run time.
//
// It slots in where dlssg does, and where it refuses -- not asked for, a device without the storage
// formats or the image features the passes need, inputs the seam could not assemble -- the
// hand-written colour interpolation runs exactly as before. Nothing here changes what the game
// submits.
//
// One caveat worth knowing before reading `frames_wanted()`: FSR 3.1's interpolation has no
// interpolation-phase input at all (ffx_frameinterpolation.h fixes t at 0.5), so a set of made
// frames is one midpoint frame repeated. A count above one buys pacing, not new pictures.
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk::fsr3fi {

/// One made frame's inputs.
///
/// The colour is the frame the game has just finished, at the size it is presented, and it is the
/// only colour this backend is given: the frame before it is kept here, because the interpolation
/// needs both ends of the interval and the present path only ever hands over the newest one. The
/// depth and the motion are the scene's at the render size, with `motion_scale` taking a vector to
/// render pixels, as the SDK's FFX_FRAMEINTERPOLATION_OPTION_LOW_RESOLUTION_MOTION_VECTORS
/// permutation the vendored GLSL was compiled under expects.
///
/// The camera's planes and vertical field of view build the device-to-view-depth factors the
/// disocclusion passes compare depths with; they only scale thresholds, so the defaults are usable
/// and the real camera is better.
struct Inputs {
    VkImageView color = VK_NULL_HANDLE;
    VkImage color_image = VK_NULL_HANDLE;
    VkFormat color_format = VK_FORMAT_UNDEFINED;
    uint32_t width = 0, height = 0;

    VkImageView depth = VK_NULL_HANDLE;
    VkImageView motion = VK_NULL_HANDLE;
    uint32_t render_width = 0, render_height = 0;

    /// Turns a motion vector into render pixels (the render size, for vectors in UV).
    float motion_scale[2]{1.0f, 1.0f};
    float jitter[2]{};

    float near_plane = 0.1f;
    float far_plane = 1000.0f;
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    float frame_time_ms = 16.667f;

    /// A cut, a load or a resize: the preparation passes are skipped for this frame, as the SDK's
    /// own dispatch skips them, because every history they read belongs to a scene that is gone.
    bool reset = false;

    /// How many frames to make between this one and the last, and which of them this call is
    /// (1-based, as dlssg takes them).
    uint32_t frames_made = 1;
    uint32_t frame_index = 1;
};

/// Whether this backend can run at all: asked for (`RX_VK_FSR_FG=<1-3>`), push descriptors, every
/// storage format the SDK's internal images need, and the three image features and the subgroup
/// operations the vendored passes compiled down to. Asked once; the one line saying why it is false
/// is logged once, and nothing is logged at all when it was not asked for.
[[nodiscard]] bool supported();

/// How many frames the run asks to be made between two of the game's: `RX_VK_FSR_FG=<1-3>`, zero
/// when this backend was not asked for. See the caveat at the top of this file: FSR 3.1 interpolates
/// the midpoint and only the midpoint, so a count above one repeats that frame.
[[nodiscard]] uint32_t frames_wanted();

/// Makes the pipelines, the internal images and the made frames' images for these sizes, re-making
/// them when the sizes or the presented format change. False when anything could not be made, which
/// leaves this backend unavailable rather than half-built.
[[nodiscard]] bool prepare(uint32_t width, uint32_t height, uint32_t render_width,
                           uint32_t render_height, VkFormat present_format);

/// Records one made frame. False when the inputs are not usable, which leaves the caller to show
/// whatever it would otherwise have shown.
[[nodiscard]] bool generate(VkCommandBuffer cmd, const Inputs& in);

/// The made frame of the last `generate`, by index (1-based, as `Inputs::frame_index`), or null.
[[nodiscard]] VkImage made_image(uint32_t index);
[[nodiscard]] VkImageView made_view(uint32_t index);

/// Forgets the sequence, so the next frame is made with the reset flag set: a cut, a load, a resize.
void reset();

/// Frees everything this backend made. Safe to call when nothing was made.
void shutdown();

} // namespace mkwvk::fsr3fi
