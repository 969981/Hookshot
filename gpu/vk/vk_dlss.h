// NVIDIA DLSS Super Resolution in place of OoT's gameplay upscale network (vk_dlss.cpp). Built in only with NVIDIA's NGX
// SDK (HOOKSHOT_NGX, scripts/ngx_sdk.py: Linux and Windows); without it these say DLSS is not there and the game's network runs.
#pragma once

#include <string>
#include <vector>

#include "vk_internal.h"

namespace mkwvk {

struct Texture;

/// Whether this build has DLSS in it (HOOKSHOT_NGX)
bool dlss_built();
/// Why DLSS cannot be chosen here (the Insert menu's Upscaler row says it), DLSS_OK when it can or is not known yet
enum DlssWhy { DLSS_OK, DLSS_NOT_BUILT, DLSS_ENV_OFF, DLSS_NEEDS_RTX, DLSS_NEEDS_DRIVER, DLSS_NO_RUNTIME, DLSS_WHY_COUNT };
DlssWhy dlss_why();
/// Asks NGX once whether DLSS runs on this device, so dlss_why knows before the first upscale (the menu, when it opens).
/// Nothing off an NVIDIA device with NGX's extensions, or in a build without DLSS
void dlss_probe();
/// (hookshot) NGX started on this device, by this file, for NGX's other features to use the same session: DLSS frame
/// generation (vk_ngx.cpp, vk_dlssg.cpp). NGX is initialized once per device and shut down once; a second
/// NVSDK_NGX_VULKAN_Init_with_ProjectID beside this one is what it avoids. True even where super resolution itself is not
/// available, since the session is. Starts it if dlss_probe has not yet (and RX_VK_DLSS is not 0)
bool dlss_ngx_session();
/// (hookshot) Whether a file whose name begins `name` ("nvngx_dlssnr.dll", "libnvidia-ngx-dlssnr.so") is in one of the folders
/// NGX is told to look for its features' runtimes in (ngx_init's: RX_VK_DLSS_DIR, the game folder's ngx, then
/// %LOCALAPPDATA%\Hookshot\ngx or $XDG_DATA_HOME/hookshot/ngx), or the game folder itself, which NGX also searches. `where` gets the folders, in UTF-8 for the log; `found` the
/// first such file's path. False and both empty in a build without DLSS
bool dlss_ngx_runtime(const char* name, std::string* where, std::string* found);
/// What is asked for: 0 off, 1 DLSS (in place of the game's gameplay upscale, at any render share), 2 DLAA (anti-aliasing at the render size, the 3D at full scale):
/// the Insert menu's Upscaler row (opts.dlss), or RX_VK_DLSS=0 / 1 / 2 over it. 0 when dlss_why() says it cannot run
int dlss_mode();
bool dlss_wanted();   // (dlss_mode() != 0)
/// DLAA: the 3D drawn at full scale, whatever the Render resolution row says (rx_option 2 and 3)
bool dlss_full_scale();
/// The instance's extensions DLSS needs, added to `want` when the loader has them (all: what it has)
void dlss_instance_extensions(std::vector<const char*>& want, const std::vector<VkExtensionProperties>& all);
/// The device's extensions DLSS needs, added to `want` when the device has them
void dlss_device_extensions(std::vector<const char*>& want, const std::vector<VkExtensionProperties>& all);
/// A draw into a scene-sized target (colour target 0 and the depth target), with the vertex stage's uniform buffer 3 at
/// `camera` (its size): the camera of the frame, when that buffer holds one
void dlss_note_scene_draw(const Texture* color0, uint32_t width, uint32_t height, Texture* depth, uint64_t camera,
                          uint64_t camera_size);
/// A skinned draw into a scene-sized target (vk_draw.cpp finds them by their vertex layout): what DLSS's motion needs to draw
/// it again with this frame's bones and the frame before's (actor motion: Link, the others, what moves by itself)
struct DlssSkin {
    const Texture* depth = nullptr;   // (its depth target: only the scene's are taken)
    uint64_t vbuf = 0, vsize = 0;     // (vertex stream 0)
    uint32_t stride = 0;
    uint32_t pos = 0;                 // (byte offsets in a vertex: the position, RGB32F or RGBA16F)
    uint32_t joints[2]{}, weights[2]{};   // (RGBA16UI or RGBA8UI joints and RGBA8 weights, four a set; sets: 1 or 2)
    int sets = 0;
    uint32_t formats = 0;             // (dlss_actor::Push::formats)
    uint64_t ibuf = 0;
    int itype = 0, count = 0, base_vertex = 0;
    uint64_t bones = 0, bones_size = 0;   // (the palette: 3 x 4 row-major matrices, model space to the camera's world)
    uint64_t object = 0, object_size = 0; // (the object's buffer: its origin at 0x40)
    uint64_t program = 0;
};
void dlss_note_skinned_draw(const DlssSkin& k);

/// A gameplay upscale's dispatch (program `key`; vk_draw.cpp's dlss_stand_in finds them: the 4/3 network fd029a317a9a4b94 at
/// the game's usual 3/4, the same-size pass 9dc98871eb7fc101 at full size, and one network of its own for each other share): its input as it samples it (in, in_view) and its output's storage
/// view (out at level 0). known: one of the gameplay networks by its key (the 4/3 one, the full-size pass); else a network of
/// that shape (the fixed shares' own), taken only when its input is this frame's scene and its output the picture, and left
/// to run without a word otherwise. true: DLSS wrote the output (the dispatch is left out); false: the network runs, the
/// reason logged
bool dlss_upscale(VkCommandBuffer cmd, uint64_t key, bool known, Texture* in, VkImageView in_view, Texture* out,
                  VkImageView out_view, uint32_t out_level);

/// Once a frame (the game's present): after 120 frames without DLSS (off, or the upscale not taken) its feature, images and
/// deferred frees are let go, so its video memory comes back. Nothing to do costs one atomic load
void dlss_idle();

/// For the renderer's status line (vk_replay.cpp status_tick): while DLSS or DLAA draws, its details since the last call
/// into b ("; DLSS: jitter ..., actor motion ..., its images ... MB") and 1 (DLSS) or 2 (DLAA); else 0 and b left alone
int dlss_status(char* b, size_t n);

/// The scene's width (axis 0) or height (1) for the host's camera jitter (nx2/oot_jitter.py, HOS_OPT_DLSS_JITTER_W and _H): 0 when
/// there is to be none (DLSS not drawing, or RX_VK_DLSS_JITTER=0). Axis 2: the jitter sequence's length for DLSS's scale
/// (dlss_jitter_phases, HOS_OPT_DLSS_JITTER_PHASES)
uint32_t dlss_jitter_size(int axis);

} // namespace mkwvk
