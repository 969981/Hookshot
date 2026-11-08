// AMD FSR 4.1 through the AMD FSR API (vk_fsr4.cpp), in place of the game's gameplay upscale, beside NVIDIA DLSS (vk_dlss.h):
// the same colour, depth and camera motion from vk_draw.cpp. Only the API's MIT headers are built in (third_party/fidelityfx);
// AMD's signed amd_fidelityfx_loader_vk.dll and amd_fidelityfx_upscaler_vk.dll are loaded from the user's PC at run time and
// none ship with Hookshot. Ported from madebyjordan's FSR 4.1 work on Lichii (vk_fsr4_upscaler.cpp), GPL-3.0-or-later.
#pragma once

#include <cstdint>

#include "vk_loader.h"
#include "vk_dlss.h"   // (DlssImage: the images both upscalers take)

namespace mkwvk {

/// (Windows only) The loader DLL from RX_FSR4_DIR, else the executable's folder, else the game folder; an FSR 4.x upscaler for this device
/// among those it lists. Made at the first use (no instance or device extensions needed), once. True when there is one
bool fsr4_available();
const char* fsr4_reason();   // (why not, short: the menu and the log)

/// Records one FSR evaluation on cmd: color (perceptual, tonemapped: FSR's LDR mode), depth (standard, not inverted) and motion
/// (render pixels, towards the previous frame) at the render size, output (RGBA16F, STORAGE | SAMPLED) at the output size. All
/// in GENERAL before and after. jitter: the sub-pixel offset this frame's scene was drawn with, in render pixels. False when FSR
/// failed (then never again: fsr4_available() is false from then on)
bool fsr4_evaluate(VkCommandBuffer cmd, const DlssImage& color, const DlssImage& depth, const DlssImage& motion,
                   const DlssImage& output, float jitter_x, float jitter_y, bool reset);
void fsr4_stop();   // (before the device goes; safe when never started)

}  // namespace mkwvk
