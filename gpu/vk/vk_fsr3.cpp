// (hookshot) FSR 3.1's pipelines and internal images. The dispatch half follows; see vk_fsr3.h.
//
// The resource table and the per-pass binding lists are the SDK's, not ours: ffx_fsr3upscaler.cpp
// decides the formats and sizes, and each pass file numbers its own bindings (FSR3UPSCALER_BIND_*).
// Getting one wrong is a validation error at best and a wrong picture at worst, so they are written
// out here in the SDK's order rather than derived.
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <vector>

#include "vk_fsr3.h"

#include "shaders/fsr3_accumulate_comp_spv.h"
#include "shaders/fsr3_accumulate_sharpen_comp_spv.h"
#include "shaders/fsr3_luma_instability_comp_spv.h"
#include "shaders/fsr3_luma_pyramid_comp_spv.h"
#include "shaders/fsr3_luma_pyramid_nowave_comp_spv.h"
#include "shaders/fsr3_prepare_inputs_comp_spv.h"
#include "shaders/fsr3_prepare_reactivity_comp_spv.h"
#include "shaders/fsr3_rcas_comp_spv.h"
#include "shaders/fsr3_shading_change_comp_spv.h"
#include "shaders/fsr3_shading_change_pyramid_comp_spv.h"
#include "shaders/fsr3_shading_change_pyramid_nowave_comp_spv.h"

namespace mkwvk::fsr3 {
namespace {

/// A descriptor's kind, in the binding order each pass file declares. Every pass also takes the two
/// samplers at bindings 1000 (point clamp) and 1001 (linear clamp).
enum Kind : uint8_t { T, I, U };   // sampled texture, storage image, uniform buffer

struct PassInfo {
    const char* name;
    const uint32_t* spv;
    size_t spv_bytes;
    const Kind* bindings;
    size_t binding_count;
};

constexpr Kind PREPARE_INPUTS[]{T, T, T, I, I, I, I, I, U};
constexpr Kind LUMA_PYRAMID[]{T, T, I, I, I, I, I, I, I, I, I, U, U};
constexpr Kind SHADING_CHANGE_PYRAMID[]{T, T, T, T, I, I, I, I, I, I, I, U, U};
constexpr Kind SHADING_CHANGE[]{T, I, U};
constexpr Kind PREPARE_REACTIVITY[]{T, T, T, T, T, T, T, T, T, I, I, I, U};
constexpr Kind LUMA_INSTABILITY[]{T, T, T, T, T, T, T, I, I, U};
constexpr Kind ACCUMULATE[]{T, T, T, T, T, T, T, T, T, I, I, I, U};
constexpr Kind RCAS[]{T, T, I, U, U};

#define PASS(n, spv, b) PassInfo{n, spv, sizeof spv, b, sizeof b / sizeof b[0]}

/// The passes, in the order PassId numbers them. The two _nowave pyramids are alternatives to the
/// two before them, for devices without the subgroup operations the single-pass downsampler wants.
const PassInfo PASSES[]{
    PASS("prepare_inputs", FSR3_PREPARE_INPUTS_COMP_SPV, PREPARE_INPUTS),
    PASS("luma_pyramid", FSR3_LUMA_PYRAMID_COMP_SPV, LUMA_PYRAMID),
    PASS("luma_pyramid_nowave", FSR3_LUMA_PYRAMID_NOWAVE_COMP_SPV, LUMA_PYRAMID),
    PASS("shading_change_pyramid", FSR3_SHADING_CHANGE_PYRAMID_COMP_SPV, SHADING_CHANGE_PYRAMID),
    PASS("shading_change_pyramid_nowave", FSR3_SHADING_CHANGE_PYRAMID_NOWAVE_COMP_SPV,
         SHADING_CHANGE_PYRAMID),
    PASS("shading_change", FSR3_SHADING_CHANGE_COMP_SPV, SHADING_CHANGE),
    PASS("prepare_reactivity", FSR3_PREPARE_REACTIVITY_COMP_SPV, PREPARE_REACTIVITY),
    PASS("luma_instability", FSR3_LUMA_INSTABILITY_COMP_SPV, LUMA_INSTABILITY),
    PASS("accumulate", FSR3_ACCUMULATE_COMP_SPV, ACCUMULATE),
    PASS("accumulate_sharpen", FSR3_ACCUMULATE_SHARPEN_COMP_SPV, ACCUMULATE),
    PASS("rcas", FSR3_RCAS_COMP_SPV, RCAS),
};
#undef PASS

constexpr size_t kPassCount = sizeof PASSES / sizeof PASSES[0];
constexpr uint32_t kSpdStoredMips = 6;

struct Pipeline {
    VkDescriptorSetLayout set = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
};

struct Image {
    VkImage image = VK_NULL_HANDLE;
    VmaAllocation memory = nullptr;
    VkImageView view = VK_NULL_HANDLE;          ///< every level
    std::vector<VkImageView> levels;            ///< one per level, for the mipped ones
    uint32_t width = 0, height = 0, level_count = 1;
    VkFormat format = VK_FORMAT_UNDEFINED;
};

struct State {
    bool tried = false;
    bool ok = false;
    Pipeline passes[kPassCount];
    VkSampler point = VK_NULL_HANDLE;
    VkSampler linear = VK_NULL_HANDLE;
    // The SDK's internal images. The pairs ping-pong on frame parity, as its resource tables do.
    Image accumulation[2], luma[2], luma_history[2], internal_upscaled[2];
    Image farthest_depth, shading_change, new_locks, spd_mips, farthest_depth_mip1;
    Image dilated_motion_vectors, dilated_depth, previous_nearest_depth, dilated_reactive_masks;
    Image frame_info, spd_atomic, lanczos_lut, default_reactive, output;
    uint32_t render_width = 0, render_height = 0, upscale_width = 0, upscale_height = 0;
};

State g;

void drop_image(Image& t) {
    for (VkImageView v : t.levels) vkDestroyImageView(dev.device, v, nullptr);
    t.levels.clear();
    if (t.view) vkDestroyImageView(dev.device, t.view, nullptr);
    if (t.image) mkwvk::destroy_image(t.image, t.memory);
    t = Image{};
}

/// One of the SDK's internal images: storage and sampled, GENERAL throughout, with a view per level
/// when it has more than one (the pyramids bind their levels separately).
bool make_image(Image& t, VkFormat format, uint32_t width, uint32_t height, uint32_t levels = 1) {
    drop_image(t);
    t.format = format, t.width = std::max(1u, width), t.height = std::max(1u, height);
    t.level_count = std::max(1u, levels);
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D;
    ci.format = format;
    ci.extent = {t.width, t.height, 1};
    ci.mipLevels = t.level_count;
    ci.arrayLayers = 1;
    ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
               VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (!create_image(ci, t.image, t.memory, false)) {
        logf("vk: fsr3: a %ux%u image (format %d) was not made", t.width, t.height, (int)format);
        return false;
    }
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = t.image;
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, t.level_count, 0, 1};
    if (vkCreateImageView(dev.device, &vi, nullptr, &t.view) != VK_SUCCESS) return false;
    if (t.level_count > 1) {
        for (uint32_t level = 0; level < t.level_count; level++) {
            vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, level, 1, 0, 1};
            VkImageView v = VK_NULL_HANDLE;
            if (vkCreateImageView(dev.device, &vi, nullptr, &v) != VK_SUCCESS) return false;
            t.levels.push_back(v);
        }
    }
    return true;
}

bool make_pipeline(Pipeline& p, const PassInfo& info) {
    std::vector<VkDescriptorSetLayoutBinding> bindings;
    bindings.reserve(info.binding_count + 2);
    for (size_t i = 0; i < info.binding_count; i++) {
        const VkDescriptorType type = info.bindings[i] == T ? VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE
                                      : info.bindings[i] == I ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE
                                                              : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
        bindings.push_back({(uint32_t)i, type, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
    }
    // (the two samplers every pass declares, at the bindings the SDK's GLSL fixes them to)
    bindings.push_back({1000, VK_DESCRIPTOR_TYPE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
    bindings.push_back({1001, VK_DESCRIPTOR_TYPE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    li.flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR;
    li.bindingCount = (uint32_t)bindings.size();
    li.pBindings = bindings.data();
    if (vkCreateDescriptorSetLayout(dev.device, &li, nullptr, &p.set) != VK_SUCCESS) return false;
    VkPipelineLayoutCreateInfo pi{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pi.setLayoutCount = 1, pi.pSetLayouts = &p.set;
    if (vkCreatePipelineLayout(dev.device, &pi, nullptr, &p.layout) != VK_SUCCESS) return false;
    VkShaderModuleCreateInfo mi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    mi.codeSize = info.spv_bytes;
    mi.pCode = info.spv;
    VkShaderModule m = VK_NULL_HANDLE;
    if (vkCreateShaderModule(dev.device, &mi, nullptr, &m) != VK_SUCCESS) return false;
    VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0,
                VK_SHADER_STAGE_COMPUTE_BIT, m, "main", nullptr};
    ci.layout = p.layout;
    const VkResult made = vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &ci, nullptr, &p.pipeline);
    vkDestroyShaderModule(dev.device, m, nullptr);
    if (made != VK_SUCCESS) {
        logf("vk: fsr3: the %s pass pipeline was not made (%d)", info.name, (int)made);
        p.pipeline = VK_NULL_HANDLE;
        return false;
    }
    return true;
}

bool make_samplers() {
    VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    si.minLod = 0.0f, si.maxLod = VK_LOD_CLAMP_NONE;
    si.magFilter = si.minFilter = VK_FILTER_NEAREST;
    si.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
    if (vkCreateSampler(dev.device, &si, nullptr, &g.point) != VK_SUCCESS) return false;
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    si.mipmapMode = VK_SAMPLER_MIPMAP_MODE_LINEAR;
    return vkCreateSampler(dev.device, &si, nullptr, &g.linear) == VK_SUCCESS;
}

/// Every storage format the internal images need. A device missing one cannot run the passes, and
/// finding that out here is better than a failed image allocation in the middle of a frame.
bool formats_supported() {
    static const VkFormat needed[]{
        VK_FORMAT_R8_UNORM,          VK_FORMAT_R16_SFLOAT,       VK_FORMAT_R8G8B8A8_UNORM,
        VK_FORMAT_R16G16B16A16_SFLOAT, VK_FORMAT_R16G16_SFLOAT,  VK_FORMAT_R32_SFLOAT,
        VK_FORMAT_R32_UINT,          VK_FORMAT_R32G32B32A32_SFLOAT, VK_FORMAT_R16_SNORM,
    };
    for (VkFormat f : needed) {
        VkFormatProperties props{};
        vkGetPhysicalDeviceFormatProperties(dev.physical, f, &props);
        if (!(props.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) ||
            !(props.optimalTilingFeatures & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT)) {
            logf("vk: fsr3: format %d cannot be a storage and sampled image here", (int)f);
            return false;
        }
    }
    return true;
}

} // namespace

bool supported() {
    static const bool answer = [] {
        if (!dev.push_descriptor || !vkCmdPushDescriptorSetKHR) {
            logf("vk: fsr3: no push descriptors");
            return false;
        }
        return formats_supported();
    }();
    return answer;
}

bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
             uint32_t upscale_height) {
    if (!supported()) return false;
    if (!g.tried) {
        g.tried = true;
        g.ok = make_samplers();
        for (size_t i = 0; i < kPassCount && g.ok; i++) g.ok = make_pipeline(g.passes[i], PASSES[i]);
        if (g.ok) logf("vk: fsr3: %zu pass pipelines ready", kPassCount);
    }
    if (!g.ok) return false;
    const bool same = g.output.image && g.render_width == render_width &&
                      g.render_height == render_height && g.upscale_width == upscale_width &&
                      g.upscale_height == upscale_height;
    if (same) return true;

    const uint32_t rw = std::max(1u, render_width), rh = std::max(1u, render_height);
    const uint32_t uw = std::max(1u, upscale_width), uh = std::max(1u, upscale_height);
    bool made = true;
    for (size_t i = 0; i < 2 && made; i++) {
        made = made && make_image(g.accumulation[i], VK_FORMAT_R8_UNORM, rw, rh);
        made = made && make_image(g.luma[i], VK_FORMAT_R16_SFLOAT, rw, rh);
        // (the pass files declare the luma history as rgba8; the SDK allocates RGBA16F, which a
        // storage format qualifier does not match, so allocate what the shaders store)
        made = made && make_image(g.luma_history[i], VK_FORMAT_R8G8B8A8_UNORM, rw, rh);
        made = made && make_image(g.internal_upscaled[i], VK_FORMAT_R16G16B16A16_SFLOAT, uw, uh);
    }
    const uint32_t half_w = std::max(1u, rw / 2), half_h = std::max(1u, rh / 2);
    const uint32_t spd_levels =
        (uint32_t)std::floor(std::log2((float)std::max(half_w, half_h))) + 1;
    made = made && make_image(g.farthest_depth, VK_FORMAT_R16_SFLOAT, rw, rh);
    made = made && make_image(g.shading_change, VK_FORMAT_R8_UNORM, half_w, half_h);
    made = made && make_image(g.new_locks, VK_FORMAT_R8_UNORM, uw, uh);
    made = made && make_image(g.spd_mips, VK_FORMAT_R16G16_SFLOAT, half_w, half_h,
                              std::max(spd_levels, kSpdStoredMips));
    made = made && make_image(g.farthest_depth_mip1, VK_FORMAT_R16_SFLOAT, half_w, half_h);
    made = made && make_image(g.dilated_motion_vectors, VK_FORMAT_R16G16_SFLOAT, rw, rh);
    made = made && make_image(g.dilated_depth, VK_FORMAT_R32_SFLOAT, rw, rh);
    made = made && make_image(g.previous_nearest_depth, VK_FORMAT_R32_UINT, rw, rh);
    made = made && make_image(g.dilated_reactive_masks, VK_FORMAT_R8G8B8A8_UNORM, rw, rh);
    made = made && make_image(g.frame_info, VK_FORMAT_R32G32B32A32_SFLOAT, 1, 1);
    made = made && make_image(g.spd_atomic, VK_FORMAT_R32_UINT, 1, 1);
    made = made && make_image(g.lanczos_lut, VK_FORMAT_R16_SNORM, 128, 1);
    made = made && make_image(g.default_reactive, VK_FORMAT_R8_UNORM, 1, 1);
    made = made && make_image(g.output, VK_FORMAT_R16G16B16A16_SFLOAT, uw, uh);
    if (!made) {
        shutdown();
        return false;
    }
    g.render_width = rw, g.render_height = rh, g.upscale_width = uw, g.upscale_height = uh;
    logf("vk: fsr3: %ux%u -> %ux%u", rw, rh, uw, uh);
    return true;
}

VkImage output_image() {
    return g.output.image;
}

VkImageView output_view() {
    return g.output.view;
}

void shutdown() {
    for (Image* t : {&g.farthest_depth, &g.shading_change, &g.new_locks, &g.spd_mips,
                     &g.farthest_depth_mip1, &g.dilated_motion_vectors, &g.dilated_depth,
                     &g.previous_nearest_depth, &g.dilated_reactive_masks, &g.frame_info,
                     &g.spd_atomic, &g.lanczos_lut, &g.default_reactive, &g.output}) {
        drop_image(*t);
    }
    for (size_t i = 0; i < 2; i++) {
        drop_image(g.accumulation[i]);
        drop_image(g.luma[i]);
        drop_image(g.luma_history[i]);
        drop_image(g.internal_upscaled[i]);
    }
    g.render_width = g.render_height = g.upscale_width = g.upscale_height = 0;
}

} // namespace mkwvk::fsr3
