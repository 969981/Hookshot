#pragma once
#include "vk_loader.h"
namespace mkwvk {
struct NvnState;
struct Texture;
bool fsr_native_enabled();
void fsr_native_draw(const NvnState& s, Texture* depth, uint32_t width, uint32_t height, float& jitter_x, float& jitter_y);
void fsr_native_input(Texture* color, VkImageView view);
bool fsr_native_resolve(VkCommandBuffer cmd, Texture* output, VkImageView view, bool& disturbed);
}
