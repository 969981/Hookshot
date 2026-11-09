// (hookshot DLSS) hookshot_ngx.dll: NVIDIA's NGX SDK for Vulkan behind a small C interface (ngx/hookshot_ngx.h).
//
// The NGX SDK ships only as Microsoft-ABI static libraries (nvsdk_ngx_s.lib, the static MSVC runtime), and the
// renderer is built with llvm-mingw, so the SDK lives in a DLL of its own, built for the MSVC target
// (gpu/vk/ngx/build_ngx.sh), which hookshot_vk.dll loads at run time (vk_dlss.cpp). With the DLL missing, an
// older driver or a GPU without DLSS, the renderer keeps the game's own upscaler. Nothing of the SDK crosses the
// interface but plain C types and Vulkan handles.
#define NOMINMAX
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vulkan/vulkan.h>
#include "nvsdk_ngx_vk.h"
#include "nvsdk_ngx_helpers.h"
#include "nvsdk_ngx_helpers_vk.h"
#include "hookshot_ngx.h"

namespace {
std::mutex mu;
NVSDK_NGX_Parameter* params = nullptr;
bool ready = false;
VkDevice ngx_device = VK_NULL_HANDLE;
HsNgxLog app_log = nullptr;
// (build 7) each feature's create size: Ultra Performance at 2560x1440 allows only 853x480, while the game
// draws 854x480 (4/12). Create snaps to the mode's allowed range; evaluate reads that many pixels as a subrect.
struct FeatureSize { uint32_t w, h; };
std::unordered_map<void*, FeatureSize> feature_sizes;

void say(const char* fmt, ...) {
    if (!app_log) return;
    char b[512];
    va_list a;
    va_start(a, fmt);
    vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    app_log(b);
}
void NVSDK_CONV ngx_log(const char* message, NVSDK_NGX_Logging_Level, NVSDK_NGX_Feature) {
    if (!app_log || !message) return;
    char b[480];
    snprintf(b, sizeof b, "ngx: %s", message);
    size_t n = strlen(b);
    while (n && (b[n - 1] == '\n' || b[n - 1] == '\r')) b[--n] = 0;
    app_log(b);
}
/// NGX's own text for a result ("NVSDK_NGX_Result_FAIL_UnsupportedParameter" and the like), ASCII
std::string result_text(NVSDK_NGX_Result r) {
    std::string out;
    if (const wchar_t* w = GetNGXResultAsString(r))
        for (; *w && out.size() < 200; w++) out += *w < 0x80 ? (char)*w : '?';
    return out.empty() ? "(no text)" : "(" + out + ")";
}
NVSDK_NGX_Resource_VK resource(const HsNgxImage& im, bool rw) {
    VkImageSubresourceRange r{(VkImageAspectFlags)im.aspect, 0, 1, 0, 1};
    return NVSDK_NGX_Create_ImageView_Resource_VK((VkImageView)im.view, (VkImage)im.image, r, (VkFormat)im.format, im.width,
                                                  im.height, rw);
}
}  // namespace

extern "C" {

HSNGX_API int hsngx_interface_version(void) { return HSNGX_INTERFACE_VERSION; }

HSNGX_API int hsngx_required_extensions(uint32_t* instance_count, const char*** instance_exts, uint32_t* device_count,
                                        const char*** device_exts) {
    unsigned int ni = 0, nd = 0;
    const char** ie = nullptr;
    const char** de = nullptr;
    const NVSDK_NGX_Result r = NVSDK_NGX_VULKAN_RequiredExtensions(&ni, &ie, &nd, &de);
    if (NVSDK_NGX_FAILED(r)) return (int)r;
    *instance_count = ni, *instance_exts = ie, *device_count = nd, *device_exts = de;
    return 0;
}

HSNGX_API int hsngx_init(void* instance, void* physical, void* device, void* gipa, void* gdpa, const wchar_t* app_dir,
                         HsNgxLog log, int verbose) {
    std::lock_guard lk(mu);
    app_log = log;
    if (ready) return 1;
    NVSDK_NGX_FeatureCommonInfo info{};
    const wchar_t* paths[1] = {app_dir};
    info.PathListInfo.Path = paths;
    info.PathListInfo.Length = app_dir ? 1 : 0;
    info.LoggingInfo.LoggingCallback = ngx_log;
    info.LoggingInfo.MinimumLoggingLevel = verbose ? NVSDK_NGX_LOGGING_LEVEL_VERBOSE : NVSDK_NGX_LOGGING_LEVEL_ON;
    info.LoggingInfo.DisableOtherLoggingSinks = !verbose;
    NVSDK_NGX_Result r = NVSDK_NGX_VULKAN_Init_with_ProjectID(
        "5d1c2f0a-8e3b-4c6d-9a7f-0b0c5d070001", NVSDK_NGX_ENGINE_TYPE_CUSTOM, "2.0", app_dir, (VkInstance)instance,
        (VkPhysicalDevice)physical, (VkDevice)device, (PFN_vkGetInstanceProcAddr)gipa, (PFN_vkGetDeviceProcAddr)gdpa, &info);
    if (NVSDK_NGX_FAILED(r)) {
        say("dlss: NGX init failed: %#x %s", (unsigned)r, result_text(r).c_str());
        return -1;
    }
    ngx_device = (VkDevice)device;
    r = NVSDK_NGX_VULKAN_GetCapabilityParameters(&params);
    if (NVSDK_NGX_FAILED(r) || !params) {
        say("dlss: NGX capability parameters failed: %#x", (unsigned)r);
        NVSDK_NGX_VULKAN_Shutdown1((VkDevice)device);
        return -2;
    }
    int needs_driver = 0, available = 0;
    unsigned int drv_major = 0, drv_minor = 0;
    NVSDK_NGX_Parameter_GetI(params, NVSDK_NGX_Parameter_SuperSampling_NeedsUpdatedDriver, &needs_driver);
    NVSDK_NGX_Parameter_GetUI(params, NVSDK_NGX_Parameter_SuperSampling_MinDriverVersionMajor, &drv_major);
    NVSDK_NGX_Parameter_GetUI(params, NVSDK_NGX_Parameter_SuperSampling_MinDriverVersionMinor, &drv_minor);
    r = NVSDK_NGX_Parameter_GetI(params, NVSDK_NGX_Parameter_SuperSampling_Available, &available);
    if (needs_driver) say("dlss: the NVIDIA driver is too old for DLSS: %u.%u or newer is needed", drv_major, drv_minor);
    if (NVSDK_NGX_FAILED(r) || !available) {
        int init_result = 0;
        NVSDK_NGX_Parameter_GetI(params, NVSDK_NGX_Parameter_SuperSampling_FeatureInitResult, &init_result);
        say("dlss: DLSS Super Resolution is not available on this GPU/driver (feature init result %#x)", (unsigned)init_result);
        NVSDK_NGX_VULKAN_DestroyParameters(params);
        params = nullptr;
        NVSDK_NGX_VULKAN_Shutdown1((VkDevice)device);
        return -3;
    }
    say("dlss: NGX ready: DLSS Super Resolution available (driver needs update: %s)", needs_driver ? "yes" : "no");
    ready = true;
    return 1;
}

HSNGX_API void* hsngx_create(void* cmd, uint32_t render_w, uint32_t render_h, uint32_t out_w, uint32_t out_h, int quality,
                             int preset, int flags) {
    std::lock_guard lk(mu);
    if (!ready || !params) return nullptr;
    // (build 7) Stick to the quality mode the caller asked for (DLAA 5, Quality 2, Balanced 1, Performance 0,
    // Ultra Performance 3). Never fall back to another mode: an 854x480 Ultra Performance input is one pixel past
    // mode 3's 853x480..853x480 range, and the old fallback picked Quality (mode 2), which then failed three times
    // and turned DLSS off. Instead, snap the create size into that mode's allowed range and evaluate a subrect.
    if (quality == 4) {
        say("dlss: NGX mode 4 (UltraQuality) is not supported by DLSS SR");
        return nullptr;
    }
    unsigned int opt_w = 0, opt_h = 0, max_w = 0, max_h = 0, min_w = 0, min_h = 0;
    float sharp = 0.0f;
    const NVSDK_NGX_Result opt = NGX_DLSS_GET_OPTIMAL_SETTINGS(params, out_w, out_h, (NVSDK_NGX_PerfQuality_Value)quality,
                                                               &opt_w, &opt_h, &max_w, &max_h, &min_w, &min_h, &sharp);
    if (NVSDK_NGX_FAILED(opt) || !opt_w || !max_w || !max_h) {
        say("dlss: NGX mode %d for %ux%u output: %s (no usable range): not creating", quality, out_w, out_h,
            NVSDK_NGX_SUCCEED(opt) ? "ok but empty" : result_text(opt).c_str());
        return nullptr;
    }
    if (!min_w) min_w = opt_w;
    if (!min_h) min_h = opt_h;
    say("dlss: NGX mode %d for %ux%u output: ok, render %ux%u optimal, %ux%u..%ux%u allowed", quality, out_w, out_h, opt_w,
        opt_h, min_w, min_h, max_w, max_h);
    uint32_t use_w = render_w, use_h = render_h;
    if (use_w < min_w) use_w = min_w;
    if (use_w > max_w) use_w = max_w;
    if (use_h < min_h) use_h = min_h;
    if (use_h > max_h) use_h = max_h;
    if (use_w != render_w || use_h != render_h)
        say("dlss: snapped create size %ux%u -> %ux%u so NGX mode %d's range holds it (evaluate uses a %ux%u subrect)",
            render_w, render_h, use_w, use_h, quality, use_w, use_h);
    // (preset asked first, then NVIDIA's default when NGX refuses it: preset E, the CNN, is deprecated in 310.x)
    const int presets[2] = {preset, 0};
    for (int attempt = 0; attempt < (preset ? 2 : 1); attempt++) {
        const int ps = presets[attempt];
        const char* keys[] = {NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_DLAA, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Quality,
                              NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Balanced, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Performance,
                              NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraPerformance,
                              NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraQuality};
        for (const char* k : keys) NVSDK_NGX_Parameter_SetUI(params, k, (unsigned int)ps);
        NVSDK_NGX_DLSS_Create_Params cp{};
        cp.Feature.InWidth = use_w, cp.Feature.InHeight = use_h;
        cp.Feature.InTargetWidth = out_w, cp.Feature.InTargetHeight = out_h;
        cp.Feature.InPerfQualityValue = (NVSDK_NGX_PerfQuality_Value)quality;
        cp.InFeatureCreateFlags = flags;
        cp.InEnableOutputSubrects = false;
        NVSDK_NGX_Handle* h = nullptr;
        const NVSDK_NGX_Result r = NGX_VULKAN_CREATE_DLSS_EXT1(ngx_device, (VkCommandBuffer)cmd, 1, 1, &h, params, &cp);
        if (NVSDK_NGX_SUCCEED(r) && h) {
            feature_sizes[h] = {use_w, use_h};
            say("dlss: DLSS made, %ux%u -> %ux%u, NGX mode %d, preset %d, flags %#x%s", use_w, use_h, out_w, out_h, quality, ps,
                flags, (use_w != render_w || use_h != render_h) ? " (snapped)" : "");
            return h;
        }
        say("dlss: creating DLSS %ux%u -> %ux%u (NGX mode %d, preset %d, flags %#x) failed: %#x %s", use_w, use_h, out_w, out_h,
            quality, ps, flags, (unsigned)r, result_text(r).c_str());
    }
    return nullptr;
}

HSNGX_API int hsngx_evaluate(void* cmd, void* handle, const HsNgxImage* color, const HsNgxImage* depth, const HsNgxImage* motion,
                             const HsNgxImage* output, float jitter_x, float jitter_y, float mv_scale_x, float mv_scale_y,
                             int reset, float frame_ms) {
    std::lock_guard lk(mu);
    if (!ready || !params || !handle || !color || !depth || !motion || !output) return -1;
    NVSDK_NGX_Resource_VK c = resource(*color, false), d = resource(*depth, false), m = resource(*motion, false),
                          o = resource(*output, true);
    NVSDK_NGX_VK_DLSS_Eval_Params ep{};
    ep.Feature.pInColor = &c;
    ep.Feature.pInOutput = &o;
    ep.pInDepth = &d;
    ep.pInMotionVectors = &m;
    ep.InJitterOffsetX = jitter_x, ep.InJitterOffsetY = jitter_y;
    // (build 7) use the create size when the colour image is a pixel or two past the mode's range (854 vs 853)
    uint32_t sub_w = color->width, sub_h = color->height;
    if (auto it = feature_sizes.find(handle); it != feature_sizes.end()) {
        if (sub_w > it->second.w) sub_w = it->second.w;
        if (sub_h > it->second.h) sub_h = it->second.h;
    }
    ep.InRenderSubrectDimensions = {sub_w, sub_h};
    ep.InReset = reset;
    ep.InMVScaleX = mv_scale_x, ep.InMVScaleY = mv_scale_y;
    ep.InFrameTimeDeltaInMsec = frame_ms;
    const NVSDK_NGX_Result r = NGX_VULKAN_EVALUATE_DLSS_EXT((VkCommandBuffer)cmd, (NVSDK_NGX_Handle*)handle, params, &ep);
    if (NVSDK_NGX_FAILED(r)) {
        static int told = 0;
        if (told++ < 5) say("dlss: NGX evaluate failed: %#x %s", (unsigned)r, result_text(r).c_str());
        return (int)(r & 0x7fffffff) | 0x40000000;
    }
    return 0;
}

HSNGX_API void hsngx_release(void* handle) {
    std::lock_guard lk(mu);
    if (handle && ready) {
        feature_sizes.erase(handle);
        NVSDK_NGX_VULKAN_ReleaseFeature((NVSDK_NGX_Handle*)handle);
    }
}

// (build 7, optional: an older renderer never asks) the VRAM NGX's DLSS features hold, in bytes; 0: not known
HSNGX_API unsigned long long hsngx_vram_bytes(void) {
    std::lock_guard lk(mu);
    if (!ready || !params) return 0;
    unsigned long long bytes = 0;
    return NVSDK_NGX_SUCCEED(NGX_DLSS_GET_STATS(params, &bytes)) ? bytes : 0;
}

HSNGX_API void hsngx_shutdown(void) {
    std::lock_guard lk(mu);
    if (!ready) return;
    if (params) NVSDK_NGX_VULKAN_DestroyParameters(params);
    params = nullptr;
    NVSDK_NGX_VULKAN_Shutdown1(ngx_device);
    ready = false;
}

}  // extern "C"
