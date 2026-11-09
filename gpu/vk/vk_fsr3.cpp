// (hookshot) FSR 3.1's pipelines and internal images. The dispatch half follows; see vk_fsr3.h.
//
// The resource table and the per-pass binding lists are the SDK's, not ours: ffx_fsr3upscaler.cpp
// decides the formats and sizes, and each pass file numbers its own bindings (FSR3UPSCALER_BIND_*).
// Getting one wrong is a validation error at best and a wrong picture at worst, so they are written
// out here in the SDK's order rather than derived.
#include <algorithm>
#include <cfloat>
#include <cmath>
#include <cstring>
#include <initializer_list>
#include <utility>
#include <cstdlib>
#include <vector>

#include "vk_fsr3.h"
#include "vk_texture.h"   // (full_barrier: what the rest of the renderer uses between passes)

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

/// cbFSR3Upscaler, exactly as ffx_fsr3upscaler.cpp lays it out. The size is asserted because every
/// pass reads it by offset: a field out of place is a wrong picture, not a compile error.
struct Constants {
    int32_t render_size[2];
    int32_t previous_render_size[2];
    int32_t upscale_size[2];
    int32_t previous_upscale_size[2];
    int32_t max_render_size[2];
    int32_t max_upscale_size[2];
    float device_to_view_depth[4];
    float jitter[2];
    float previous_jitter[2];
    float motion_vector_scale[2];
    float downscale_factor[2];
    float motion_vector_jitter_cancellation[2];
    float tan_half_fov;
    float jitter_phase_count;
    float delta_time;
    float delta_pre_exposure;
    float view_space_to_meters;
    float frame_index;
    float velocity_factor;
    float reactiveness_scale;
    float shading_change_scale;
    float accumulation_added_per_frame;
    float min_disocclusion_accumulation;
};
static_assert(sizeof(Constants) == 148, "cbFSR3Upscaler must match the SDK's layout");

struct SpdConstants {
    uint32_t mips;
    uint32_t num_work_groups;
    uint32_t work_group_offset[2];
    uint32_t render_size[2];
};

struct RcasConstants {
    uint32_t config[4];
};

/// ffxFsr3UpscalerGetJitterPhaseCount.
int32_t jitter_phase_count_for(uint32_t render_width, uint32_t display_width) {
    const float ratio = (float)display_width / (float)std::max(1u, render_width);
    return (int32_t)(8.0f * ratio * ratio);
}

/// FidelityFX's AU1_AH1_AF1: a float's bits as a half's.
uint32_t half_bits(float v) {
    const _Float16 h = (_Float16)v;
    uint16_t bits = 0;
    memcpy(&bits, &h, 2);
    return bits;
}

/// FsrRcasCon from ffx_fsr1.h: the sharpness in both halves of the second word.
void rcas_con(uint32_t* con, float sharpness) {
    sharpness = std::exp2(-sharpness);
    memcpy(&con[0], &sharpness, 4);
    con[1] = half_bits(sharpness) | (half_bits(sharpness) << 16);
    con[2] = 0;
    con[3] = 0;
}

/// One descriptor of a dispatch: an image view, or a range of the uniform buffer.
struct Resource {
    VkImageView view = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceSize offset = 0;
    VkDeviceSize size = 0;
};

/// A memory dependency over the whole queue, in the synchronization2 form this renderer loads.
void memory_barrier(VkCommandBuffer cmd, VkPipelineStageFlags2 src_stage, VkAccessFlags2 src,
                    VkPipelineStageFlags2 dst_stage, VkAccessFlags2 dst) {
    VkMemoryBarrier2 b{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
    b.srcStageMask = src_stage, b.srcAccessMask = src;
    b.dstStageMask = dst_stage, b.dstAccessMask = dst;
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.memoryBarrierCount = 1, di.pMemoryBarriers = &b;
    vkCmdPipelineBarrier2(cmd, &di);
}

constexpr VkImageSubresourceRange ALL_LEVELS{VK_IMAGE_ASPECT_COLOR_BIT, 0, VK_REMAINING_MIP_LEVELS, 0, 1};

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
    // The uniform buffer the three constant blocks are written into, one set of slots per frame in
    // flight so a frame being read is not overwritten.
    VkBuffer uniforms = VK_NULL_HANDLE;
    VmaAllocation uniforms_memory = nullptr;
    void* uniforms_mapped = nullptr;
    VkDeviceSize slot_fsr = 0, slot_spd = 0, slot_rcas = 0, frame_stride = 0;
    uint32_t uniform_frame = 0;
    // History, as fsr3upscalerDispatch carries it.
    int32_t previous_render_size[2]{};
    int32_t previous_upscale_size[2]{};
    float previous_jitter[2]{};
    float jitter_phase_count = 0.0f;
    float frame_index = 0.0f;
    uint32_t parity = 0;
    bool first_evaluation = true;
    bool lut_uploaded = false;
};

constexpr uint32_t kUniformFrames = 4;   // (more than the swapchain's images in flight)

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
    // (TRANSFER_SRC as well as DST: the output is blitted from, which is how the seam gets it into
    //  the game's target. Asking only for DST while blitting from it is a validation error waiting
    //  on a device that checks; caught in review, not by a run)
    ci.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
               VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
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

/// The uniform buffer the three constant blocks are written into: host visible and mapped, with a
/// set of slots per frame in flight so writing one frame cannot disturb a frame still being read.
bool make_uniforms() {
    const VkDeviceSize align = std::max<VkDeviceSize>(dev.props.limits.minUniformBufferOffsetAlignment, 16);
    const auto round_up = [align](VkDeviceSize n) { return (n + align - 1) / align * align; };
    g.slot_fsr = round_up(sizeof(Constants));
    g.slot_spd = round_up(sizeof(SpdConstants));
    g.slot_rcas = round_up(sizeof(RcasConstants));
    g.frame_stride = g.slot_fsr + g.slot_spd + g.slot_rcas + round_up(128 * sizeof(int16_t));
    return create_buffer(g.frame_stride * kUniformFrames,
                         VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT, true,
                         g.uniforms, g.uniforms_memory, &g.uniforms_mapped);
}

/// Records one pass: its descriptors pushed in the order the pass file numbers them, then the two
/// samplers it declares at 1000 and 1001, then the dispatch.
void record_pass(VkCommandBuffer cmd, const Pipeline& p, const PassInfo& info, const Resource* res,
                 size_t count, uint32_t groups_x, uint32_t groups_y) {
    std::vector<VkDescriptorImageInfo> images(count);
    std::vector<VkDescriptorBufferInfo> buffers(count);
    std::vector<VkWriteDescriptorSet> writes;
    writes.reserve(count + 2);
    for (size_t i = 0; i < count; i++) {
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w.dstBinding = (uint32_t)i;
        w.descriptorCount = 1;
        if (info.bindings[i] == U) {
            buffers[i] = {res[i].buffer, res[i].offset, res[i].size};
            w.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
            w.pBufferInfo = &buffers[i];
        } else {
            images[i] = {VK_NULL_HANDLE, res[i].view, VK_IMAGE_LAYOUT_GENERAL};
            w.descriptorType = info.bindings[i] == T ? VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE
                                                     : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
            w.pImageInfo = &images[i];
        }
        writes.push_back(w);
    }
    const VkDescriptorImageInfo point{g.point, VK_NULL_HANDLE, VK_IMAGE_LAYOUT_UNDEFINED};
    const VkDescriptorImageInfo linear{g.linear, VK_NULL_HANDLE, VK_IMAGE_LAYOUT_UNDEFINED};
    for (const auto& [binding, info_ptr] :
         {std::pair<uint32_t, const VkDescriptorImageInfo*>{1000, &point},
          std::pair<uint32_t, const VkDescriptorImageInfo*>{1001, &linear}}) {
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w.dstBinding = binding;
        w.descriptorCount = 1;
        w.descriptorType = VK_DESCRIPTOR_TYPE_SAMPLER;
        w.pImageInfo = info_ptr;
        writes.push_back(w);
    }
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p.pipeline);
    vkCmdPushDescriptorSetKHR(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p.layout, 0,
                              (uint32_t)writes.size(), writes.data());
    vkCmdDispatch(cmd, std::max(1u, groups_x), std::max(1u, groups_y), 1);
    // (every pass reads what the one before it wrote)
    memory_barrier(cmd, VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_2_SHADER_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                   VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT);
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
        g.ok = make_samplers() && make_uniforms();
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

bool upscale(VkCommandBuffer cmd, const Inputs& in) {
    if (!in.color || !in.depth || !in.motion) return false;
    if (!(in.near_plane > 0.0f) || !(in.fov_y > 0.0f)) return false;   // (Inputs defaults them)
    if (!prepare(in.render_width, in.render_height, in.upscale_width, in.upscale_height)) return false;

    const bool first = g.first_evaluation;
    const bool reset = in.reset || first;
    g.first_evaluation = false;
    const int32_t rw = (int32_t)g.render_width, rh = (int32_t)g.render_height;
    const int32_t uw = (int32_t)g.upscale_width, uh = (int32_t)g.upscale_height;

    Constants k{};
    k.render_size[0] = rw, k.render_size[1] = rh;
    k.upscale_size[0] = uw, k.upscale_size[1] = uh;
    k.max_render_size[0] = rw, k.max_render_size[1] = rh;
    k.max_upscale_size[0] = uw, k.max_upscale_size[1] = uh;
    for (int i = 0; i < 2; i++) {
        k.previous_render_size[i] = first ? k.render_size[i] : g.previous_render_size[i];
        k.previous_upscale_size[i] = first ? k.upscale_size[i] : g.previous_upscale_size[i];
        k.jitter[i] = in.jitter[i];
        k.previous_jitter[i] = first ? in.jitter[i] : g.previous_jitter[i];
    }
    {   // (setupDeviceDepthToViewSpaceDepthParams for reversed infinite depth)
        const float aspect = (float)rw / (float)std::max(1, rh);
        const float cot_half_fov = std::cos(0.5f * in.fov_y) / std::sin(0.5f * in.fov_y);
        k.device_to_view_depth[0] = -1.0f * FLT_EPSILON;
        k.device_to_view_depth[1] = in.near_plane;
        k.device_to_view_depth[2] = 1.0f / (cot_half_fov / aspect);
        k.device_to_view_depth[3] = 1.0f / cot_half_fov;
        k.tan_half_fov = std::tan(std::atan(std::tan(in.fov_y * 0.5f) * aspect));
    }
    k.motion_vector_scale[0] = in.motion_scale[0] / (float)std::max(1, rw);
    k.motion_vector_scale[1] = in.motion_scale[1] / (float)std::max(1, rh);
    k.downscale_factor[0] = (float)rw / (float)std::max(1, uw);
    k.downscale_factor[1] = (float)rh / (float)std::max(1, uh);
    const float phases = (float)jitter_phase_count_for(g.render_width, g.upscale_width);
    if (reset || g.jitter_phase_count == 0.0f) g.jitter_phase_count = phases;
    else if (phases > g.jitter_phase_count) g.jitter_phase_count += 1.0f;
    else if (phases < g.jitter_phase_count) g.jitter_phase_count -= 1.0f;
    k.jitter_phase_count = g.jitter_phase_count;
    k.delta_time = std::clamp(in.frame_time_ms / 1000.0f, 0.0f, 1.0f);
    k.delta_pre_exposure = 1.0f;
    k.view_space_to_meters = 1.0f;
    g.frame_index = reset ? 0.0f : g.frame_index + 1.0f;
    k.frame_index = g.frame_index;
    k.velocity_factor = 1.0f;             // (the SDK's FFX_FSR3UPSCALER_CONFIGURE_UPSCALE_KEY_* defaults)
    k.reactiveness_scale = 1.0f;
    k.shading_change_scale = 1.0f;
    k.accumulation_added_per_frame = 1.0f / 3.0f;
    k.min_disocclusion_accumulation = -1.0f / 3.0f;
    for (int i = 0; i < 2; i++) {
        g.previous_render_size[i] = k.render_size[i];
        g.previous_upscale_size[i] = k.upscale_size[i];
        g.previous_jitter[i] = in.jitter[i];
    }

    const uint32_t spd_x = (g.render_width - 1) / 64 + 1;
    const uint32_t spd_y = (g.render_height - 1) / 64 + 1;
    SpdConstants spd{};
    spd.mips = (uint32_t)std::min(std::floor(std::log2((float)std::max(g.render_width, g.render_height))), 12.0f);
    spd.num_work_groups = spd_x * spd_y;
    spd.render_size[0] = g.render_width, spd.render_size[1] = g.render_height;
    RcasConstants rcas{};
    rcas_con(rcas.config, 2.0f - 2.0f * std::clamp(in.sharpness, 0.0f, 1.0f));

    // This frame's slots, written through the mapped buffer.
    const VkDeviceSize base = (VkDeviceSize)(g.uniform_frame % kUniformFrames) * g.frame_stride;
    g.uniform_frame++;
    uint8_t* const mapped = (uint8_t*)g.uniforms_mapped + base;
    memcpy(mapped, &k, sizeof k);
    memcpy(mapped + g.slot_fsr, &spd, sizeof spd);
    memcpy(mapped + g.slot_fsr + g.slot_spd, &rcas, sizeof rcas);
    const VkDeviceSize lut_at = g.slot_fsr + g.slot_spd + g.slot_rcas;
    if (!g.lut_uploaded) {   // (the accumulate pass's Lanczos weights, 128 taps as the SDK builds them)
        int16_t weights[128];
        for (size_t i = 0; i < 128; i++) {
            const float x = 2.0f * (float)i / 127.0f;
            const float pi_x = 3.14159265358979f * x;
            const float y = std::abs(x) < FLT_EPSILON
                                ? 1.0f
                                : (std::sin(pi_x) / pi_x) * (std::sin(0.5f * pi_x) / (0.5f * pi_x));
            weights[i] = (int16_t)std::lround(y * 32767.0f);
        }
        memcpy(mapped + lut_at, weights, sizeof weights);
    }
    flush_mapped(g.uniforms_memory, g.frame_stride * kUniformFrames);

    const uint32_t p = g.parity, q = p ^ 1u;

    // What the game's draws wrote has to be visible, and our images need their first layout.
    {
        memory_barrier(cmd, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                       VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT |
                           VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT |
                           VK_ACCESS_2_SHADER_WRITE_BIT | VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_TRANSFER_BIT | VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                       VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT |
                           VK_ACCESS_2_TRANSFER_WRITE_BIT);
    }
    std::vector<Image*> all{&g.farthest_depth, &g.shading_change, &g.new_locks, &g.spd_mips,
                            &g.farthest_depth_mip1, &g.dilated_motion_vectors, &g.dilated_depth,
                            &g.previous_nearest_depth, &g.dilated_reactive_masks, &g.frame_info,
                            &g.spd_atomic, &g.lanczos_lut, &g.default_reactive, &g.output};
    for (size_t i = 0; i < 2; i++) {
        all.push_back(&g.accumulation[i]);
        all.push_back(&g.luma[i]);
        all.push_back(&g.luma_history[i]);
        all.push_back(&g.internal_upscaled[i]);
    }
    if (first) {
        std::vector<VkImageMemoryBarrier2> to_general;
        to_general.reserve(all.size());
        for (const Image* t : all) {
            VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
            b.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
            b.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
            b.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
            b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
            b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            b.image = t->image;
            b.subresourceRange = ALL_LEVELS;
            to_general.push_back(b);
        }
        VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
        di.imageMemoryBarrierCount = (uint32_t)to_general.size();
        di.pImageMemoryBarriers = to_general.data();
        vkCmdPipelineBarrier2(cmd, &di);
        const VkClearColorValue zero{};
        for (const Image* t : all) {
            vkCmdClearColorImage(cmd, t->image, VK_IMAGE_LAYOUT_GENERAL, &zero, 1, &ALL_LEVELS);
        }
    }
    if (!g.lut_uploaded) {
        g.lut_uploaded = true;
        VkBufferImageCopy copy{};
        copy.bufferOffset = base + lut_at;
        copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        copy.imageExtent = {128, 1, 1};
        vkCmdCopyBufferToImage(cmd, g.uniforms, g.lanczos_lut.image, VK_IMAGE_LAYOUT_GENERAL, 1, &copy);
    }
    // Per frame: the nearest-depth scratch, the SPD counter and its mips; the history on a reset.
    const VkClearColorValue zero{};
    for (const Image* t : {&g.previous_nearest_depth, &g.spd_atomic, &g.spd_mips}) {
        vkCmdClearColorImage(cmd, t->image, VK_IMAGE_LAYOUT_GENERAL, &zero, 1, &ALL_LEVELS);
    }
    if (reset) {
        vkCmdClearColorImage(cmd, g.accumulation[p].image, VK_IMAGE_LAYOUT_GENERAL, &zero, 1, &ALL_LEVELS);
        VkClearColorValue info{};
        info.float32[0] = -1.0f, info.float32[1] = 1.0f;
        vkCmdClearColorImage(cmd, g.frame_info.image, VK_IMAGE_LAYOUT_GENERAL, &info, 1, &ALL_LEVELS);
    }
    {
        memory_barrier(cmd, VK_PIPELINE_STAGE_2_TRANSFER_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                       VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT);
    }

    const auto tex = [](const Image& t) { return Resource{t.view}; };
    const auto mip = [](uint32_t level) {
        const size_t n = g.spd_mips.levels.size();
        return Resource{g.spd_mips.levels[std::min<size_t>(level, n ? n - 1 : 0)]};
    };
    const Resource color{in.color}, depth{in.depth}, motion{in.motion};
    const Resource exposure = tex(g.frame_info);   // (FSR computes its own exposure)
    const Resource current_luma = tex(g.luma[p]);
    const Resource cb_fsr{VK_NULL_HANDLE, g.uniforms, base, g.slot_fsr};
    const Resource cb_spd{VK_NULL_HANDLE, g.uniforms, base + g.slot_fsr, g.slot_spd};
    const Resource cb_rcas{VK_NULL_HANDLE, g.uniforms, base + g.slot_fsr + g.slot_spd, g.slot_rcas};
    const auto groups = [](int32_t size, uint32_t tile) {
        return ((uint32_t)std::max(1, size) + tile - 1) / tile;
    };
    const auto run = [&](size_t pass, std::initializer_list<Resource> res, uint32_t gx, uint32_t gy) {
        record_pass(cmd, g.passes[pass], PASSES[pass], res.begin(), res.size(), gx, gy);
    };

    run(0, {motion, depth, color, tex(g.dilated_motion_vectors), tex(g.dilated_depth),
            tex(g.previous_nearest_depth), tex(g.farthest_depth), current_luma, cb_fsr},
        groups(rw, 8), groups(rh, 8));
    run(1, {current_luma, tex(g.farthest_depth), tex(g.spd_atomic), tex(g.frame_info), mip(0), mip(1),
            mip(2), mip(3), mip(4), mip(5), tex(g.farthest_depth_mip1), cb_fsr, cb_spd},
        spd_x, spd_y);
    // (hookshot) The two pyramid passes share one atomic counter, and the single-pass downsampler
    // uses it to elect the workgroup that finishes the chain: it counts up to num_work_groups and
    // the last one through does the tail. Clearing it only once a frame leaves the second pass
    // starting at the total, so no workgroup is elected and the ones waiting on the tail spin. One
    // FSR evaluation cost about half a second before this, with the frame rate falling to 1.9 fps.
    {
        const VkClearColorValue zero_again{};
        memory_barrier(cmd, VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_2_SHADER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_TRANSFER_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT);
        vkCmdClearColorImage(cmd, g.spd_atomic.image, VK_IMAGE_LAYOUT_GENERAL, &zero_again, 1, &ALL_LEVELS);
        memory_barrier(cmd, VK_PIPELINE_STAGE_2_TRANSFER_BIT, VK_ACCESS_2_TRANSFER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                       VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT);
    }
    run(3, {current_luma, tex(g.luma[q]), tex(g.dilated_motion_vectors), exposure, tex(g.spd_atomic),
            mip(0), mip(1), mip(2), mip(3), mip(4), mip(5), cb_fsr, cb_spd},
        spd_x, spd_y);
    run(5, {tex(g.spd_mips), tex(g.shading_change), cb_fsr}, groups(rw / 2, 8), groups(rh / 2, 8));
    run(6, {tex(g.previous_nearest_depth), tex(g.dilated_motion_vectors), tex(g.dilated_depth),
            tex(g.default_reactive), tex(g.default_reactive), tex(g.accumulation[p]),
            tex(g.shading_change), current_luma, exposure, tex(g.dilated_reactive_masks),
            tex(g.new_locks), tex(g.accumulation[q]), cb_fsr},
        groups(rw, 8), groups(rh, 8));
    run(7, {exposure, tex(g.dilated_reactive_masks), tex(g.dilated_motion_vectors), tex(g.frame_info),
            tex(g.luma_history[p]), tex(g.farthest_depth_mip1), current_luma, tex(g.luma_history[q]),
            tex(g.farthest_depth), cb_fsr},
        groups(rw, 8), groups(rh, 8));
    run(in.sharpen ? 9 : 8,
        {exposure, tex(g.dilated_reactive_masks), tex(g.dilated_motion_vectors),
         tex(g.internal_upscaled[p]), tex(g.lanczos_lut), tex(g.farthest_depth_mip1), current_luma,
         tex(g.farthest_depth), color, tex(g.internal_upscaled[q]), tex(g.output), tex(g.new_locks),
         cb_fsr},
        groups(uw, 8), groups(uh, 8));
    if (in.sharpen) {
        run(10, {exposure, tex(g.internal_upscaled[q]), tex(g.output), cb_fsr, cb_rcas},
            groups(uw, 16), groups(uh, 16));
    }
    {
        memory_barrier(cmd, VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_2_SHADER_WRITE_BIT,
                       VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT | VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                       VK_ACCESS_2_SHADER_READ_BIT);
    }
    g.parity = q;
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
    g.first_evaluation = true;
    g.lut_uploaded = false;
    g.parity = 0;
}

} // namespace mkwvk::fsr3
