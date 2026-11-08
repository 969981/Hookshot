// Opt-in Native AA experiment. All scene observations come from the ordered replay.
#include "vk_fsr_native.h"
#include "vk_internal.h"
#include "vk_state.h"
#include "vk_texture.h"
#include "fsr_camera.h"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#ifdef _WIN32
#define _WINDOWS
#include "../../third_party/fidelityfx/api/include/vk/ffx_api_vk.h"
#include "../../third_party/fidelityfx/api/include/ffx_api_loader.h"
#include "../../third_party/fidelityfx/upscalers/include/ffx_upscale.h"
#include "shaders/fsr_prepare_comp_spv.h"
#include "shaders/fsr_resolve_comp_spv.h"
#endif

namespace mkwvk {
bool fsr_native_enabled() {
#ifdef _WIN32
    static const bool on = getenv("RX_VK_FSR_NATIVE_AA") && atoi(getenv("RX_VK_FSR_NATIVE_AA")) != 0 &&
        (!getenv("RX_VK_PARALLEL") || atoi(getenv("RX_VK_PARALLEL")) <= 1);
    return on;
#else
    return false;
#endif
}
#ifdef _WIN32
namespace {
struct Camera {
    float vp[16]{}, projection[16]{}, near_z = 0, far_z = 0, fov = 0;
    bool valid = false;
};
struct Scene { Texture* depth = nullptr; Camera camera; uint32_t draws = 0; bool jittered = false; };
struct Image {
    VkImage image{}; VmaAllocation allocation{}; VkImageView view{}; VkImageCreateInfo ci{};
};
struct Pipeline { VkPipeline pipe{}; VkPipelineLayout layout{}; VkDescriptorSetLayout set{}; };
struct State {
    // Fixed-size resources live with the Vulkan device. A size change falls back, never destroys in-flight work.
    bool attempted = false, ready = false, failed = false, previous = false;
    uint32_t width = 0, height = 0;
    uint64_t frame = UINT64_MAX, generation = 0, input_frame = UINT64_MAX, last_frame = UINT64_MAX;
    float jitter_x = 0, jitter_y = 0;
    Camera previous_camera;
    std::map<uint64_t, Camera> cameras;
    std::map<uint32_t, Scene> scenes;
    Texture* input = nullptr; VkImageView input_view{};
    std::chrono::steady_clock::time_point last_time{};
    HMODULE library{}; ffxFunctions functions{}; ffxContext context{};
    // API creation pointers must remain live until context destruction.
    ffxCreateContextDescUpscale create{};
    ffxCreateContextDescUpscaleVersion api{};
    ffxCreateBackendVKDesc backend{};
    ffxOverrideVersion version{};
    Image color, motion, output;
    Pipeline prepare, resolve;
    VkSampler sampler{};
    uint64_t dispatches = 0, resets = 0;
} fsr;

void sdk_message(uint32_t kind, const wchar_t* message) {
    char text[1200]{};
    WideCharToMultiByte(CP_UTF8, 0, message, -1, text, sizeof text - 1, nullptr, nullptr);
    logf("fsr-native: SDK %s: %s", kind == FFX_API_MESSAGE_TYPE_ERROR ? "error" : "warning", text);
}
float sharpening() {
    static const float value = [] {
        const char* text = getenv("RX_VK_FSR_SHARPNESS");
        float requested = text ? std::strtof(text, nullptr) : 0;
        return std::isfinite(requested) ? std::clamp(requested, 0.0f, 1.0f) : 0.0f;
    }();
    return value;
}
void next_frame() {
    const uint64_t frame = texture_frame_no(), generation = texture_destroy_generation();
    if (fsr.frame == frame && fsr.generation == generation) return;
    fsr.frame = frame; fsr.generation = generation;
    fsr.cameras.clear(); fsr.scenes.clear(); fsr.input = nullptr; fsr.input_view = VK_NULL_HANDLE;
    fsr.jitter_x = fsr.jitter_y = 0;
    if (fsr.ready && !fsr.failed && fsr.last_frame + 1 == frame) {
        fsr.jitter_x = fsr_camera::halton(unsigned(frame % 8) + 1, 2);
        fsr.jitter_y = fsr_camera::halton(unsigned(frame % 8) + 1, 3);
    }
}
bool pipeline(Pipeline& p, const uint32_t* code, size_t bytes, unsigned count, unsigned sampled, unsigned push) {
    std::vector<VkDescriptorSetLayoutBinding> bindings;
    for (unsigned i = 0; i < count; ++i)
        bindings.push_back({i, i < sampled ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                            1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
    VkDescriptorSetLayoutCreateInfo set{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    set.flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR; set.bindingCount = count; set.pBindings = bindings.data();
    if (vkCreateDescriptorSetLayout(dev.device, &set, nullptr, &p.set) != VK_SUCCESS) return false;
    VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, push};
    VkPipelineLayoutCreateInfo layout{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    layout.setLayoutCount = 1; layout.pSetLayouts = &p.set;
    layout.pushConstantRangeCount = push ? 1 : 0; layout.pPushConstantRanges = &range;
    if (vkCreatePipelineLayout(dev.device, &layout, nullptr, &p.layout) != VK_SUCCESS) return false;
    VkShaderModuleCreateInfo module{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    module.codeSize = bytes; module.pCode = code;
    VkShaderModule shader{};
    if (vkCreateShaderModule(dev.device, &module, nullptr, &shader) != VK_SUCCESS) return false;
    VkComputePipelineCreateInfo info{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    info.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, shader, "main", nullptr};
    info.layout = p.layout;
    auto result = vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &info, nullptr, &p.pipe);
    vkDestroyShaderModule(dev.device, shader, nullptr);
    return result == VK_SUCCESS;
}
void transition(VkCommandBuffer cmd, VkImage image, VkImageAspectFlags aspect, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier2 barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    barrier.srcStageMask = from == VK_IMAGE_LAYOUT_UNDEFINED ? VK_PIPELINE_STAGE_2_NONE : VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    barrier.srcAccessMask = from == VK_IMAGE_LAYOUT_UNDEFINED ? 0 : VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    barrier.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    barrier.oldLayout = from; barrier.newLayout = to;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = image; barrier.subresourceRange = {aspect, 0, 1, 0, 1};
    VkDependencyInfo info{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    info.imageMemoryBarrierCount = 1; info.pImageMemoryBarriers = &barrier;
    vkCmdPipelineBarrier2(cmd, &info);
}
bool image(Image& image, VkCommandBuffer cmd, VkFormat format) {
    auto& ci = image.ci; ci.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    ci.imageType = VK_IMAGE_TYPE_2D; ci.format = format; ci.extent = {fsr.width, fsr.height, 1};
    ci.mipLevels = ci.arrayLayers = 1; ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT;
    if (!create_image(ci, image.image, image.allocation)) return false;
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = image.image; vi.viewType = VK_IMAGE_VIEW_TYPE_2D; vi.format = format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    if (vkCreateImageView(dev.device, &vi, nullptr, &image.view) != VK_SUCCESS) return false;
    transition(cmd, image.image, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL);
    return true;
}
bool initialize(VkCommandBuffer cmd, uint32_t width, uint32_t height) {
    if (fsr.attempted) return fsr.ready && !fsr.failed && width == fsr.width && height == fsr.height;
    fsr.attempted = true; fsr.failed = true; fsr.width = width; fsr.height = height;
    if (!dev.push_descriptor || !vkCmdPushDescriptorSetKHR) return false;
    wchar_t executable[32768]{};
    DWORD length = GetModuleFileNameW(nullptr, executable, 32768);
    if (!length || length >= 32768) return false;
    std::wstring path(executable, length);
    path = path.substr(0, path.find_last_of(L"\\/") + 1) + L"amd_fidelityfx_loader_vk.dll";
    fsr.library = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!fsr.library) { logf("fsr-native: optional AMD loader not available (%lu); native game AA retained", GetLastError()); return false; }
    ffxLoadFunctions(&fsr.functions, fsr.library);
    if (!fsr.functions.CreateContext || !fsr.functions.Query || !fsr.functions.Dispatch || !fsr.functions.DestroyContext) return false;
    fsr.backend.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_VK;
    fsr.backend.deviceContext = {dev.device, dev.physical, vkGetDeviceProcAddr};
    ffxQueryDescGetVersions versions{};
    versions.header.type = FFX_API_QUERY_DESC_TYPE_GET_VERSIONS;
    versions.createDescType = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
    versions.device = &fsr.backend.deviceContext;
    uint64_t count = 0; versions.outputCount = &count;
    if (fsr.functions.Query(nullptr, &versions.header) != FFX_API_RETURN_OK || !count || count > 100) return false;
    std::vector<uint64_t> ids(count); std::vector<const char*> names(count);
    versions.versionIds = ids.data(); versions.versionNames = names.data();
    if (fsr.functions.Query(nullptr, &versions.header) != FFX_API_RETURN_OK || count > ids.size()) return false;
    for (size_t i = 0; i < count; ++i) if (names[i] && !strncmp(names[i], "4.", 2)) {
        fsr.version.versionId = ids[i]; break;
    }
    if (!fsr.version.versionId) { logf("fsr-native: FSR 4 provider unavailable; native game AA retained"); return false; }
    fsr.version.header.type = FFX_API_DESC_TYPE_OVERRIDE_VERSION;
    fsr.backend.header.pNext = &fsr.version.header;
    fsr.api.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE_VERSION;
    fsr.api.version = FFX_UPSCALER_VERSION; fsr.api.header.pNext = &fsr.backend.header;
    fsr.create.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
    fsr.create.header.pNext = &fsr.api.header;
    fsr.create.maxRenderSize = fsr.create.maxUpscaleSize = {width, height};
    fsr.create.flags = FFX_UPSCALE_ENABLE_AUTO_EXPOSURE | FFX_UPSCALE_ENABLE_DEBUG_CHECKING;
    fsr.create.fpMessage = sdk_message;
    auto result = fsr.functions.CreateContext(&fsr.context, &fsr.create.header, nullptr);
    if (result != FFX_API_RETURN_OK || !fsr.context) { logf("fsr-native: context creation failed (%u)", result); return false; }
    ffxQueryGetProviderVersion provider{}; provider.header.type = FFX_API_QUERY_DESC_TYPE_GET_PROVIDER_VERSION;
    if (fsr.functions.Query(&fsr.context, &provider.header) != FFX_API_RETURN_OK ||
        !provider.versionName || strncmp(provider.versionName, "4.", 2)) return false;
    if (!pipeline(fsr.prepare, FSR_PREPARE_COMP_SPV, sizeof FSR_PREPARE_COMP_SPV, 4, 2, 88) ||
        !pipeline(fsr.resolve, FSR_RESOLVE_COMP_SPV, sizeof FSR_RESOLVE_COMP_SPV, 2, 1, 0) ||
        !image(fsr.color, cmd, VK_FORMAT_R16G16B16A16_SFLOAT) ||
        !image(fsr.motion, cmd, VK_FORMAT_R16G16_SFLOAT) ||
        !image(fsr.output, cmd, VK_FORMAT_R16G16B16A16_SFLOAT)) return false;
    VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    if (vkCreateSampler(dev.device, &si, nullptr, &fsr.sampler) != VK_SUCCESS) return false;
    fsr.failed = false; fsr.ready = true;
    logf("fsr-native: FSR %s context ready, Native AA %ux%u, camera motion, pre-HUD, depth_minus_w=%d",
         provider.versionName, width, height, host->depth_minus_w());
    logf("fsr-native: SDK sharpening %.3f (RX_VK_FSR_SHARPNESS, 0 disables it)", sharpening());
    return true;
}
FfxApiResource resource(const Image& image, uint32_t state) {
    return ffxApiGetResourceVK((void*)image.image, ffxApiGetImageResourceDescriptionVK(image.image, image.ci, 0), state);
}
void dispatch_pipe(VkCommandBuffer cmd, Pipeline& p, const std::vector<VkImageView>& views, unsigned sampled,
                   const void* constants = nullptr, unsigned bytes = 0) {
    std::vector<VkDescriptorImageInfo> infos; std::vector<VkWriteDescriptorSet> writes;
    infos.reserve(views.size()); writes.reserve(views.size());
    for (unsigned i = 0; i < views.size(); ++i) {
        infos.push_back({i < sampled ? fsr.sampler : VK_NULL_HANDLE, views[i], VK_IMAGE_LAYOUT_GENERAL});
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w.dstBinding = i; w.descriptorCount = 1;
        w.descriptorType = i < sampled ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        w.pImageInfo = &infos.back(); writes.push_back(w);
    }
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p.pipe);
    vkCmdPushDescriptorSetKHR(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p.layout, 0, (uint32_t)writes.size(), writes.data());
    if (bytes) vkCmdPushConstants(cmd, p.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, bytes, constants);
    vkCmdDispatch(cmd, (fsr.width + 7) / 8, (fsr.height + 7) / 8, 1);
}
}
#endif

void fsr_native_draw(const NvnState& s, Texture* depth, uint32_t width, uint32_t height, float& jx, float& jy) {
    jx = jy = 0;
#ifdef _WIN32
    if (!fsr_native_enabled() || fsr.failed || !depth || width < 640 || height < 360 ||
        depth->format != VK_FORMAT_D32_SFLOAT || depth->samples != VK_SAMPLE_COUNT_1_BIT ||
        depth->levels != 1 || depth->layers != 1) return;
    next_frame();
    if (s.depth_near != 0 || s.depth_far != 1 || s.viewport[0][0] != 0 || s.viewport[0][1] != 0 ||
        (s.viewport[0][2] != 0 && s.viewport[0][2] != float(width)) ||
        (s.viewport[0][3] != 0 && s.viewport[0][3] != float(height))) return;
    for (int stage : {0, 6, 7}) {
        const auto& buffer = s.ubo[stage][0]; // hardware c3 -> application uniform slot 0
        if (!buffer.addr || (buffer.size && buffer.size < 0xb0) || fsr.cameras.size() >= 256) continue;
        auto [it, inserted] = fsr.cameras.try_emplace(buffer.addr);
        Camera& camera = it->second;
        if (inserted) {
            camera.valid = ub_read(buffer.addr + 0x30, camera.vp, sizeof camera.vp) &&
                ub_read(buffer.addr + 0x70, camera.projection, sizeof camera.projection) &&
                fsr_camera::perspective(camera.projection, camera.near_z, camera.far_z, camera.fov);
            for (float value : camera.vp) camera.valid &= std::isfinite(value);
        }
        if (!camera.valid || fsr.scenes.size() >= 24) continue;
        auto& scene = fsr.scenes[depth->id];
        scene.depth = depth; scene.camera = camera; ++scene.draws;
        const bool matches = fsr.previous && width == fsr.width && height == fsr.height &&
            std::abs(camera.fov - fsr.previous_camera.fov) < 0.01f;
        if (matches) { jx = fsr.jitter_x; jy = fsr.jitter_y; scene.jittered = true; }
        break;
    }
#endif
}
void fsr_native_input(Texture* color, VkImageView view) {
#ifdef _WIN32
    if (!fsr_native_enabled() || fsr.failed) return;
    next_frame();
    if (!color || !view || color->format != VK_FORMAT_B10G11R11_UFLOAT_PACK32 || color->levels != 1 ||
        color->layers != 1 || color->samples != VK_SAMPLE_COUNT_1_BIT) return;
    fsr.input = color; fsr.input_view = view; fsr.input_frame = fsr.frame;
#endif
}
bool fsr_native_resolve(VkCommandBuffer cmd, Texture* output, VkImageView view, bool& disturbed) {
    disturbed = false;
#ifdef _WIN32
    if (!fsr_native_enabled() || fsr.failed) return false;
    next_frame();
    if (!fsr.input || fsr.input_frame != fsr.frame || !output || !view || output == fsr.input ||
        output->format != VK_FORMAT_B10G11R11_UFLOAT_PACK32 || output->levels != 1 || output->layers != 1 ||
        output->samples != VK_SAMPLE_COUNT_1_BIT ||
        output->width != fsr.input->width || output->height != fsr.input->height || fsr.last_frame == fsr.frame) return false;
    Scene* scene = nullptr;
    for (auto& [id, candidate] : fsr.scenes) if (candidate.depth->width == output->width && candidate.depth->height == output->height &&
        (!scene || candidate.draws > scene->draws)) scene = &candidate;
    if (!scene || scene->draws < 8) return false;
    float inverse[16];
    if (!fsr_camera::inverse(scene->camera.vp, inverse)) return false;
    if (!initialize(cmd, output->width, output->height)) return false;
    auto now = std::chrono::steady_clock::now();
    float ms = fsr.previous ? std::chrono::duration<float, std::milli>(now - fsr.last_time).count() : 16.667f;
    bool reset = !fsr.previous || fsr.last_frame + 1 != fsr.frame || ms > 250 || !scene->jittered ||
        std::abs(scene->camera.fov - fsr.previous_camera.fov) > 0.01f;
    struct Constants { float matrix[16]; uint32_t width, height; float jx, jy; uint32_t minus_w, reset; } pc{};
    static_assert(sizeof pc == 88);
    fsr_camera::multiply(fsr.previous ? fsr.previous_camera.vp : scene->camera.vp, inverse, pc.matrix);
    float mx, my;
    if (!fsr_camera::motion(pc.matrix, float(fsr.width) / 2, float(fsr.height) / 2, 0.99f, float(fsr.width), float(fsr.height),
        0, 0, host->depth_minus_w() != 0, mx, my) || std::abs(mx) > fsr.width * 0.5f || std::abs(my) > fsr.height * 0.5f) reset = true;
    pc.width = fsr.width; pc.height = fsr.height;
    pc.jx = scene->jittered ? fsr.jitter_x : 0; pc.jy = scene->jittered ? fsr.jitter_y : 0;
    pc.minus_w = host->depth_minus_w() != 0; pc.reset = reset;
    Texture* depth = texture_touch(scene->depth, true);
    texture_touch(fsr.input, true);
    nvn_tex native{}; native.d = depth->d;
    VkImageView depth_view = texture_view(depth, &native, nullptr, VK_IMAGE_VIEW_TYPE_2D, ViewUse::Sample);
    if (!depth_view) return false;
    full_barrier(cmd);
    disturbed = true;
    dispatch_pipe(cmd, fsr.prepare, {fsr.input_view, depth_view, fsr.color.view, fsr.motion.view}, 2, &pc, sizeof pc);
    transition(cmd, fsr.color.image, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    transition(cmd, fsr.motion.image, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    transition(cmd, depth->image, VK_IMAGE_ASPECT_DEPTH_BIT, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    ffxDispatchDescUpscale dispatch{};
    dispatch.header.type = FFX_API_DISPATCH_DESC_TYPE_UPSCALE; dispatch.commandList = (void*)cmd;
    dispatch.color = resource(fsr.color, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    dispatch.motionVectors = resource(fsr.motion, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    Image depth_resource{}; depth_resource.image = depth->image; depth_resource.ci = fsr.color.ci;
    depth_resource.ci.format = depth->format; depth_resource.ci.usage = depth->usage;
    dispatch.depth = resource(depth_resource, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    dispatch.output = resource(fsr.output, FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
    dispatch.jitterOffset = {pc.jx, pc.jy}; dispatch.motionVectorScale = {1, 1};
    dispatch.renderSize = dispatch.upscaleSize = {fsr.width, fsr.height};
    dispatch.frameTimeDelta = std::clamp(ms, 1.0f, 250.0f); dispatch.preExposure = 1; dispatch.reset = reset;
    dispatch.enableSharpening = sharpening() > 0; dispatch.sharpness = sharpening();
    dispatch.cameraNear = scene->camera.near_z; dispatch.cameraFar = scene->camera.far_z;
    dispatch.cameraFovAngleVertical = scene->camera.fov; dispatch.viewSpaceToMetersFactor = 1;
    auto result = fsr.functions.Dispatch(&fsr.context, &dispatch.header);
    transition(cmd, depth->image, VK_IMAGE_ASPECT_DEPTH_BIT, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL);
    transition(cmd, fsr.color.image, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL);
    transition(cmd, fsr.motion.image, VK_IMAGE_ASPECT_COLOR_BIT, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL);
    if (result != FFX_API_RETURN_OK) {
        fsr.failed = true;
        logf("fsr-native: dispatch failed (%u); native game AA retained, jitter disabled", result);
        return false;
    }
    full_barrier(cmd);
    dispatch_pipe(cmd, fsr.resolve, {fsr.output.view, view}, 1);
    full_barrier(cmd); texture_written(output);
    fsr.previous_camera = scene->camera; fsr.previous = true; fsr.last_frame = fsr.frame; fsr.last_time = now;
    ++fsr.dispatches; fsr.resets += reset;
    if (fsr.dispatches <= 4 || fsr.dispatches % 180 == 0)
        logf("fsr-native: dispatch %llu frame %llu %ux%u draws %u jitter %.4f %.4f reset %d total-resets %llu near %.4g far %.4g",
             (unsigned long long)fsr.dispatches, (unsigned long long)fsr.frame, fsr.width, fsr.height, scene->draws,
             pc.jx, pc.jy, (int)reset, (unsigned long long)fsr.resets, dispatch.cameraNear, dispatch.cameraFar);
    return true;
#else
    return false;
#endif
}
}
