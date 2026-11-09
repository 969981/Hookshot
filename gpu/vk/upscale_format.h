// (hookshot) The one place that decides what a Vulkan format means to the upscaler input contract. Kept apart from
// upscale_contract.h so that header stays free of Vulkan and apart from vk_draw.cpp so
// tests/upscale_contract_test.cpp covers the mapping itself: which formats count as motion is the part most likely
// to be wrong, and a wrong answer there is handed to an upscaler as if it were motion.
#pragma once

#include <vulkan/vulkan_core.h>

#include "upscale_contract.h"

namespace mkwvk::upscale {

/// A sampled texture's kind, from the format and aspect the renderer gave its image.
///
/// Depth comes from the aspect, not the format: the renderer already knows which images it made as depth, and every
/// depth format is one. Two-channel float or normalized formats are motion candidates; three- and four-channel
/// colour formats are colour. Everything else is Other, which is a refusal rather than a default.
inline Kind kind_of(VkFormat format, VkImageAspectFlags aspect) {
    if (aspect & VK_IMAGE_ASPECT_DEPTH_BIT) return Kind::Depth;
    switch (format) {
    case VK_FORMAT_R16G16_SFLOAT:
    case VK_FORMAT_R16G16_SNORM:
    case VK_FORMAT_R16G16_UNORM:
    case VK_FORMAT_R32G32_SFLOAT:
        return Kind::TwoChannel;
    case VK_FORMAT_B10G11R11_UFLOAT_PACK32:
    case VK_FORMAT_R16G16B16A16_SFLOAT:
    case VK_FORMAT_R32G32B32A32_SFLOAT:
    case VK_FORMAT_A2B10G10R10_UNORM_PACK32:
    case VK_FORMAT_R8G8B8A8_UNORM:
    case VK_FORMAT_R8G8B8A8_SRGB:
    case VK_FORMAT_B8G8R8A8_UNORM:
    case VK_FORMAT_B8G8R8A8_SRGB:
        return Kind::Colour;
    default:
        break;
    }
    return Kind::Other;
}

} // namespace mkwvk::upscale
