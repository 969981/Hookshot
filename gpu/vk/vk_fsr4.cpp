// AMD FSR 4.1 in place of the game's gameplay upscale (vk_fsr4.h). The host side follows the API guide of FidelityFX SDK 2.4
// (Kits/FidelityFX/docs/getting-started/ffx-api.md): a Vulkan backend description chained to the upscale context description,
// inputs read in SHADER_READ_ONLY_OPTIMAL, the output written in GENERAL. Ported from madebyjordan's FSR 4.1 work on Lichii
// (vk_fsr4_upscaler.cpp), GPL-3.0-or-later.
#include "vk_fsr4.h"

#ifdef _WIN32   // (AMD's signed FSR 4 runtime is Windows only: elsewhere FSR is never available, below)

#include "vk_paths.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "vk_internal.h"

#include "api/include/ffx_api.h"
#include "api/include/ffx_api_types.h"
#include "api/include/vk/ffx_api_vk.h"
#include "upscalers/include/ffx_upscale.h"
#include "vk_work.h"   // (after the others: the barriers here count as work for full_barrier)

namespace mkwvk {
namespace {

constexpr const char* kLoader = "amd_fidelityfx_loader_vk.dll";

struct Fsr {
    bool tried = false, ok = false;
    const char* reason = "not started";
    HMODULE lib = nullptr;
    PfnFfxCreateContext create = nullptr;
    PfnFfxDestroyContext destroy = nullptr;
    PfnFfxQuery query = nullptr;
    PfnFfxDispatch dispatch = nullptr;
    uint64_t version_id = 0;
    // (the context keeps pointers into its creation descriptions until it is destroyed)
    VkDeviceContext device_context{};
    ffxCreateBackendVKDesc backend{};
    ffxOverrideVersion version{};
    ffxCreateContextDescUpscale upscale{};
    ffxContext context = nullptr;
    uint32_t max_w = 0, max_h = 0;   // (the context's sizes: the output's, the most the render size can be)
    std::chrono::steady_clock::time_point last{};
    uint64_t evals = 0;
};
Fsr fsr;

void on_message(uint32_t type, const wchar_t* message) {
    char text[512] = {};
    if (message) WideCharToMultiByte(CP_UTF8, 0, message, -1, text, (int)sizeof text - 1, nullptr, nullptr);
    logf("vk: fsr4: AMD FSR %s: %s", type == FFX_API_MESSAGE_TYPE_ERROR ? "error" : "warning", text);
}

/// The folders the loader is looked for in: RX_FSR4_DIR, the executable's (bin\ beside the launcher), the game folder
std::vector<std::string> loader_dirs() {
    std::vector<std::string> d;
    if (const char* e = getenv("RX_FSR4_DIR"); e && *e) d.emplace_back(e);
    char exe[MAX_PATH];
    DWORD n = GetModuleFileNameA(nullptr, exe, MAX_PATH);
    if (n && n < MAX_PATH) {
        std::string s(exe, n);
        d.push_back(s.substr(0, s.find_last_of("\\/")));
    }
    n = game_module_path(exe, MAX_PATH);
    if (n && n < MAX_PATH) {
        std::string s(exe, n);
        s = s.substr(0, s.find_last_of("\\/"));
        if (d.empty() || d.back() != s) d.push_back(s);
    }
    return d;
}

bool start() {
    fsr.tried = true;
    for (const std::string& dir : loader_dirs()) {
        const std::string path = dir + "\\" + kLoader;
        if (GetFileAttributesA(path.c_str()) == INVALID_FILE_ATTRIBUTES) continue;
        // (the upscaler DLL the loader picks lies beside it: the loader's folder first in the search)
        fsr.lib = LoadLibraryExA(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (fsr.lib) {
            logf("vk: fsr4: %s", path.c_str());
            break;
        }
    }
    if (!fsr.lib) {
        fsr.reason = "no AMD FSR DLLs";
        logf("vk: fsr4: %s not found (RX_FSR4_DIR, or beside hookshot.exe with amd_fidelityfx_upscaler_vk.dll)", kLoader);
        return false;
    }
    fsr.create = (PfnFfxCreateContext)(void*)GetProcAddress(fsr.lib, "ffxCreateContext");
    fsr.destroy = (PfnFfxDestroyContext)(void*)GetProcAddress(fsr.lib, "ffxDestroyContext");
    fsr.query = (PfnFfxQuery)(void*)GetProcAddress(fsr.lib, "ffxQuery");
    fsr.dispatch = (PfnFfxDispatch)(void*)GetProcAddress(fsr.lib, "ffxDispatch");
    if (!fsr.create || !fsr.destroy || !fsr.query || !fsr.dispatch) {
        fsr.reason = "the AMD loader lacks the FSR API";
        logf("vk: fsr4: %s lacks the FSR API's entry points", kLoader);
        return false;
    }
    fsr.device_context = VkDeviceContext{dev.device, dev.physical, vkGetDeviceProcAddr};
    // (the loader lists every upscaler this device can run: the newest 4.x is taken explicitly, so an FSR 3.1 provider never
    // stands in silently)
    ffxQueryDescGetVersions versions{};
    versions.header.type = FFX_API_QUERY_DESC_TYPE_GET_VERSIONS;
    versions.createDescType = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
    versions.device = &fsr.device_context;
    uint64_t count = 0;
    versions.outputCount = &count;
    if (fsr.query(nullptr, &versions.header) != FFX_API_RETURN_OK || !count) {
        fsr.reason = "no AMD upscaler for this GPU";
        logf("vk: fsr4: the loader lists no upscaler for this device");
        return false;
    }
    std::vector<uint64_t> ids(count);
    std::vector<const char*> names(count);
    versions.versionIds = ids.data();
    versions.versionNames = names.data();
    if (fsr.query(nullptr, &versions.header) != FFX_API_RETURN_OK) {
        fsr.reason = "no AMD upscaler for this GPU";
        logf("vk: fsr4: the upscaler versions could not be listed");
        return false;
    }
    std::string listed;
    unsigned best[3] = {0, 0, 0};
    for (uint64_t i = 0; i < count; i++) {
        const char* name = names[i] ? names[i] : "";
        listed += (listed.empty() ? "" : ", ") + std::string(name);
        unsigned v[3] = {0, 0, 0};
        sscanf(name, "%u.%u.%u", &v[0], &v[1], &v[2]);
        if (v[0] == 4 && (v[1] > best[1] || (v[1] == best[1] && v[2] >= best[2]))) {
            std::memcpy(best, v, sizeof best);
            fsr.version_id = ids[i];
        }
    }
    if (!fsr.version_id) {
        fsr.reason = "no FSR 4 for this GPU";
        logf("vk: fsr4: no FSR 4 upscaler for this device (the loader has: %s)", listed.c_str());
        return false;
    }
    logf("vk: fsr4: upscalers %s; using %u.%u.%u", listed.c_str(), best[0], best[1], best[2]);
    fsr.ok = true;
    fsr.reason = "";
    return true;
}

void destroy_context() {
    if (!fsr.context) return;
    vkDeviceWaitIdle(dev.device);   // (recorded evaluations may still use it; the sizes seldom change)
    fsr.destroy(&fsr.context, nullptr);
    fsr.context = nullptr;
}

/// The context for outputs of w x h, render sizes up to it (the game's dynamic resolution and the Resolution row's change the
/// render size every so often: one context takes them all)
bool make_context(uint32_t w, uint32_t h) {
    destroy_context();
    fsr.backend = {};
    fsr.backend.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_BACKEND_VK;
    fsr.backend.deviceContext = fsr.device_context;
    fsr.version = {};
    fsr.version.header.type = FFX_API_DESC_TYPE_OVERRIDE_VERSION;
    fsr.version.versionId = fsr.version_id;
    fsr.backend.header.pNext = &fsr.version.header;
    fsr.upscale = {};
    fsr.upscale.header.type = FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE;
    fsr.upscale.header.pNext = &fsr.backend.header;
    // (the game's colour is tonemapped and square-root encoded already: LDR, perceptual, no auto exposure; depth not inverted.
    // The render size changes with the Resolution row and the game's dynamic resolution, under the one context)
    fsr.upscale.flags = FFX_UPSCALE_ENABLE_NON_LINEAR_COLORSPACE | FFX_UPSCALE_ENABLE_DYNAMIC_RESOLUTION;
    fsr.upscale.maxRenderSize = {w, h};
    fsr.upscale.maxUpscaleSize = {w, h};
    fsr.upscale.fpMessage = &on_message;
    ffxContext made = nullptr;
    const ffxReturnCode_t r = fsr.create(&made, &fsr.upscale.header, nullptr);
    if (r != FFX_API_RETURN_OK || !made) {
        logf("vk: fsr4: creating the upscaler failed (%d)", (int)r);
        return false;
    }
    fsr.context = made;
    fsr.max_w = w, fsr.max_h = h;
    ffxQueryGetProviderVersion provider{};
    provider.header.type = FFX_API_QUERY_DESC_TYPE_GET_PROVIDER_VERSION;
    const bool known = fsr.query(&fsr.context, &provider.header) == FFX_API_RETURN_OK && provider.versionName;
    logf("vk: fsr4: AMD FSR %s for outputs of %ux%u", known ? provider.versionName : "?", w, h);
    return true;
}

FfxApiResource resource(const DlssImage& im, VkImageUsageFlags usage, uint32_t state) {
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D, ci.format = im.format, ci.extent = {im.width, im.height, 1};
    ci.mipLevels = 1, ci.arrayLayers = 1, ci.samples = VK_SAMPLE_COUNT_1_BIT, ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = usage, ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    return ffxApiGetResourceVK((void*)im.image, ffxApiGetImageResourceDescriptionVK(im.image, ci, 0), state);
}

VkImageMemoryBarrier2 barrier(const DlssImage& im, VkAccessFlags2 src, VkAccessFlags2 dst, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    b.srcStageMask = b.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    b.srcAccessMask = src, b.dstAccessMask = dst, b.oldLayout = from, b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = im.image, b.subresourceRange = {im.aspect, 0, 1, 0, 1};
    return b;
}
void barriers(VkCommandBuffer cmd, const VkImageMemoryBarrier2* b, uint32_t n) {
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.imageMemoryBarrierCount = n, di.pImageMemoryBarriers = b;
    vkCmdPipelineBarrier2(cmd, &di);
}

}  // namespace

bool fsr4_available() {
    if (!fsr.tried) start();
    return fsr.ok;
}
const char* fsr4_reason() { return fsr.reason; }

bool fsr4_evaluate(VkCommandBuffer cmd, const DlssImage& color, const DlssImage& depth, const DlssImage& motion,
                   const DlssImage& output, float jitter_x, float jitter_y, bool reset) {
    if (!fsr4_available()) return false;
    if (!fsr.context || fsr.max_w != output.width || fsr.max_h != output.height) {
        if (!make_context(output.width, output.height)) {
            fsr.ok = false, fsr.reason = "FSR 4 did not start";
            return false;
        }
        reset = true;
    }
    if (color.width > fsr.max_w || color.height > fsr.max_h) return false;   // (a render size past the output: not an upscale)
    const auto now = std::chrono::steady_clock::now();
    const float frame_ms = fsr.evals ? std::chrono::duration<float, std::milli>(now - fsr.last).count() : 1000.0f / 30.0f;
    fsr.last = now;

    constexpr VkImageLayout G = VK_IMAGE_LAYOUT_GENERAL, R = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    constexpr VkAccessFlags2 any = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    const VkImageMemoryBarrier2 in[4] = {barrier(color, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                         barrier(depth, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                         barrier(motion, VK_ACCESS_2_MEMORY_WRITE_BIT, VK_ACCESS_2_SHADER_READ_BIT, G, R),
                                         barrier(output, any, any, G, G)};
    barriers(cmd, in, 4);

    ffxDispatchDescUpscale d{};
    d.header.type = FFX_API_DISPATCH_DESC_TYPE_UPSCALE;
    d.commandList = cmd;
    d.color = resource(color, VK_IMAGE_USAGE_SAMPLED_BIT, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.depth = resource(depth, VK_IMAGE_USAGE_SAMPLED_BIT, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.motionVectors = resource(motion, VK_IMAGE_USAGE_SAMPLED_BIT, FFX_API_RESOURCE_STATE_COMPUTE_READ);
    d.output = resource(output, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT, FFX_API_RESOURCE_STATE_UNORDERED_ACCESS);
    d.jitterOffset = {jitter_x, jitter_y};
    d.motionVectorScale = {1.0f, 1.0f};   // (render pixels already, towards the previous frame)
    d.renderSize = {color.width, color.height};
    d.upscaleSize = {output.width, output.height};
    d.enableSharpening = false;   // (the Sharpness row sharpens at the window, after the HUD)
    d.sharpness = 0.0f;
    d.frameTimeDelta = frame_ms;
    d.preExposure = 1.0f;
    d.reset = reset;
    // (the game hands no camera; FSR uses these only for its depth heuristics)
    d.cameraNear = 0.1f, d.cameraFar = 1000.0f, d.cameraFovAngleVertical = 1.0f, d.viewSpaceToMetersFactor = 1.0f;
    d.flags = FFX_UPSCALE_FLAG_NON_LINEAR_COLOR_SRGB;
    const ffxReturnCode_t r = fsr.dispatch(&fsr.context, &d.header);
    note_work();   // (AMD's dispatches: full_barrier must not take the next barrier for a repeat of the one before them)

    const VkImageMemoryBarrier2 out[4] = {barrier(color, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                          barrier(depth, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                          barrier(motion, VK_ACCESS_2_SHADER_READ_BIT, any, R, G),
                                          barrier(output, any, any, G, G)};
    barriers(cmd, out, 4);
    if (r != FFX_API_RETURN_OK) {
        logf("vk: fsr4: recording the upscale failed (%d): the game's upscale from now on", (int)r);
        fsr.ok = false, fsr.reason = "FSR 4 failed";
        return false;
    }
    static int told = 0;   // (the first evaluation, and the first resets: a camera cut each)
    if (fsr.evals++ == 0 || (reset && told < 40))
        told++, logf("vk: fsr4: %ux%u -> %ux%u, jitter %.3f %.3f%s", color.width, color.height, output.width, output.height, jitter_x,
                     jitter_y, reset ? " (history reset)" : "");
    return true;
}

void fsr4_stop() {
    if (fsr.context) destroy_context();
}

}  // namespace mkwvk

#else

namespace mkwvk {
bool fsr4_available() { return false; }
const char* fsr4_reason() { return "Windows only"; }
bool fsr4_evaluate(VkCommandBuffer, const DlssImage&, const DlssImage&, const DlssImage&, const DlssImage&, float, float, bool) {
    return false;
}
void fsr4_stop() {}
}  // namespace mkwvk

#endif
