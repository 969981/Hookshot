// (hookshot) AMD FSR 4 upscaling and frame generation, through AMD's own runtime and its FSR API (the
// "FidelityFX API": third_party/ffx-api). The Upscaler row's "AMD FSR 4" and, behind DLSS frame
// generation in vk_framegen.cpp's chain, FSR 4's frame generation.
//
// Why a second AMD backend beside gpu/vk/vk_fsr3.h: FSR 3.1 is open source, so vk_fsr3.cpp builds its
// passes from AMD's shaders and it runs on any GPU and any OS. FSR 4 is a machine-learning network
// AMD ships only inside its signed runtime, amd_fidelityfx_upscaler_vk.dll (and
// amd_fidelityfx_framegeneration_vk.dll), reached through amd_fidelityfx_loader_vk.dll. Those are
// Windows DLLs: on Linux this whole backend answers "not here". Nothing is linked -- the loader is
// LoadLibrary'd from beside the game at run time -- so a PC without the DLLs runs exactly as before.
//
// The runtime picks the version itself (ffxQuery GET_VERSIONS lists what it has): FSR 4 on an RX 9000
// and, with SDK 2.4, on the cards AMD added since; on other GPUs it falls back to the FSR 3.1 it carries.
// upscale_provider() says which one a run got, and the log says it once.
//
// The inputs are vk_fsr3.h's, field for field, plus the images and formats: AMD's API is handed whole
// resources (an image, its format and extent) rather than views, as NGX is.
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk::ffx {

struct UpscaleInputs {
    VkImage color = VK_NULL_HANDLE;   ///< HDR (B10G11R11 here), jittered, at the render size
    VkImage depth = VK_NULL_HANDLE;   ///< reversed, infinite far plane
    VkImage motion = VK_NULL_HANDLE;  ///< render pixels, pointing at the previous frame
    VkFormat color_format = VK_FORMAT_UNDEFINED;
    VkFormat depth_format = VK_FORMAT_UNDEFINED;
    VkFormat motion_format = VK_FORMAT_UNDEFINED;
    uint32_t render_width = 0, render_height = 0;
    uint32_t upscale_width = 0, upscale_height = 0;
    float jitter[2]{};                  ///< render pixels, as vk_fsr3.h's
    float motion_scale[2]{1.0f, 1.0f};  ///< turns a motion vector into render pixels
    float near_plane = 0.1f;
    float far_plane = 0.0f;   ///< 0: infinite
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    bool reset = false;
};

/// Whether this build has the backend at all: Windows only (AMD's runtime is Windows DLLs).
[[nodiscard]] bool built();
/// Whether FSR 4 upscaling can run here: a Windows build, amd_fidelityfx_loader_vk.dll found beside
/// the game (or in RX_VK_FFX_DIR) and loaded, and AMD's runtime naming at least one upscaler for this
/// device. Asked once, the answer cached; RX_VK_FFX=0 keeps the DLL from being loaded at all.
[[nodiscard]] bool upscale_supported();
/// The upscaler AMD's runtime picked here, as it names it ("FSR 4.1.2", ...), or "" before it is known.
[[nodiscard]] const char* upscale_provider();
/// Records one upscale into the backend's own RGBA16F output at the upscale size. False when the
/// inputs are not usable or the runtime refused, which leaves the caller to do what it would have.
[[nodiscard]] bool upscale(VkCommandBuffer cmd, const UpscaleInputs& in);
[[nodiscard]] VkImage output_image();

/// One frame generation's inputs: vk_fsr3fi.h's, with the images the API needs.
struct FrameInputs {
    VkImage color = VK_NULL_HANDLE;   ///< the presented frame (RGBA8 UNORM, display size)
    VkFormat color_format = VK_FORMAT_UNDEFINED;
    uint32_t width = 0, height = 0;
    VkImage depth = VK_NULL_HANDLE;
    VkFormat depth_format = VK_FORMAT_UNDEFINED;
    VkImage motion = VK_NULL_HANDLE;
    VkFormat motion_format = VK_FORMAT_UNDEFINED;
    uint32_t render_width = 0, render_height = 0;
    float motion_scale[2]{1.0f, 1.0f};
    float jitter[2]{};
    float near_plane = 0.1f;
    float far_plane = 0.0f;
    float fov_y = 60.0f * 3.14159265358979f / 180.0f;
    float frame_time_ms = 16.667f;
    /// The camera, row major (vk_draw.h SceneFrameInputs): its position and axes come out of it.
    float view[16]{};
    bool have_view = false;
    bool reset = false;
    uint32_t frames = 1;   ///< how many to make between the last frame and this one
};
/// Whether FSR 4 frame generation can run here (as upscale_supported, for the frame generation
/// effect). Asked once.
[[nodiscard]] bool frames_supported();
/// The most made frames one dispatch gives (AMD's outputs[]: four), capped by RX_VK_FFX_FG_MAX.
[[nodiscard]] uint32_t frames_wanted();
/// Prepares and dispatches frame generation for in.frames made frames, into the backend's own images.
[[nodiscard]] bool generate(VkCommandBuffer cmd, const FrameInputs& in);
/// The k-th made frame (1-based) of the last generate(), RGBA8 UNORM at the display size, GENERAL.
[[nodiscard]] VkImage made_image(uint32_t k);
/// The next generate() starts again (a cut, a resize, frame generation turned off and on).
void reset();

/// Frees the contexts, the images and the DLL. Safe when nothing was made.
void shutdown();

} // namespace mkwvk::ffx
