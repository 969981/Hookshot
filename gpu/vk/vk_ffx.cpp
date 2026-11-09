// (hookshot) AMD FSR 4 through AMD's runtime: gpu/vk/vk_ffx.h says what and why. Everything that
// touches AMD's API is inside _WIN32, because the runtime is Windows DLLs; the stubs at the bottom are
// what a Linux build compiles, so the call sites need no #ifdef.

#include "vk_ffx.h"

// (the backend itself comes in its own change: until then every platform answers "not here")

namespace mkwvk::ffx {

bool built() { return false; }
bool upscale_supported() { return false; }
const char* upscale_provider() { return ""; }
bool upscale(VkCommandBuffer, const UpscaleInputs&) { return false; }
VkImage output_image() { return VK_NULL_HANDLE; }
bool frames_supported() { return false; }
uint32_t frames_wanted() { return 0; }
bool generate(VkCommandBuffer, const FrameInputs&) { return false; }
VkImage made_image(uint32_t) { return VK_NULL_HANDLE; }
void reset() {}
void shutdown() {}

} // namespace mkwvk::ffx

