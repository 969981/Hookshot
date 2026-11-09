// (hookshot) DLSS Super Resolution's output image, its refusals and its NGX evaluation. See
// vk_dlss.h for the shape and docs/upscaling.md for where a backend plugs in.
//
// The file is in two halves on purpose. Everything that is not NVIDIA's -- the output image, the
// extents, the layout transitions, the barriers, and every clause that refuses an evaluation --
// is vendor-neutral and compiles in every build. The NGX half is five functions behind
// `RX_VK_DLSS_SDK`, and with that gate off each of them refuses; that is what makes a tree with no
// NVIDIA SDK compile and link, and a machine with no DLSS runtime run.
//
// Why the gate rather than a hand-written NGX ABI: NGX is not a handful of C entry points. Every
// call goes through `NVSDK_NGX_Parameter`, which is a C++ class of pure-virtual Set/Get overloads
// -- its vtable order is the ABI -- and the resources, the feature-common info and the create
// parameters are struct layouts, and the ~20 parameter names are strings. None of that can be
// derived from the function names, and none of it can be checked by anything here: a wrong vtable
// slot or a shifted field is memory corruption inside NVIDIA's runtime on a user's machine, which
// is a worse failure than "DLSS is unavailable". Behind the gate the SDK's own headers declare all
// of it and the compiler checks every line of this file against them. docs/upscaling.md says the
// same in the section this change appends.
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#include "vk_dlss.h"
#include "vk_paths.h"   // (game_module_path: where the model library sits, which NGX is told)

#if defined(RX_VK_DLSS_SDK)
// NVIDIA's own headers, from the SDK this build was pointed at (`-I<sdk>/include
// -DRX_VK_DLSS_SDK=1`). They are not vendored here and must not be: CONTRIBUTING.md's licensing
// rule and docs/upscaling.md. Vulkan is already included by vk_dlss.h, which is what
// nvsdk_ngx_vk.h wants to find.
#include <nvsdk_ngx.h>
#include <nvsdk_ngx_defs.h>
#include <nvsdk_ngx_vk.h>
#include <nvsdk_ngx_helpers_vk.h>
#endif

namespace mkwvk::dlss {
namespace {

/// Whether this build has an NGX SDK to compile the NGX half against. A constant rather than an
/// `#ifdef` at each use, so both arms of every decision below are compiled in every build.
#if defined(RX_VK_DLSS_SDK)
constexpr bool kHaveSdk = true;
#else
constexpr bool kHaveSdk = false;
#endif

/// `RX_VK_DLSS=1` asks for this backend. Off is the default and costs nothing: with it off nothing
/// is loaded, nothing is made and `supported()` is false before it looks at anything else.
bool wanted() {
    static const bool yes = getenv("RX_VK_DLSS") && atoi(getenv("RX_VK_DLSS")) != 0;
    return yes;
}

struct Image {
    VkImage image = VK_NULL_HANDLE;
    VmaAllocation memory = nullptr;
    VkImageView view = VK_NULL_HANDLE;
    uint32_t width = 0, height = 0;
    VkFormat format = VK_FORMAT_UNDEFINED;
    bool in_general = false;   ///< the UNDEFINED -> GENERAL barrier has been recorded
};

struct State {
    Image output;
    uint32_t render_width = 0, render_height = 0, upscale_width = 0, upscale_height = 0;
    bool first_evaluation = true;
};

State g;

constexpr VkImageSubresourceRange ONE_LEVEL{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};

/// A memory dependency over the whole queue, in the synchronization2 form this renderer loads
/// (`vkCmdPipelineBarrier` is not in vk_loader.h).
void memory_barrier(VkCommandBuffer cmd, VkPipelineStageFlags2 src_stage, VkAccessFlags2 src,
                    VkPipelineStageFlags2 dst_stage, VkAccessFlags2 dst) {
    VkMemoryBarrier2 b{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
    b.srcStageMask = src_stage, b.srcAccessMask = src;
    b.dstStageMask = dst_stage, b.dstAccessMask = dst;
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.memoryBarrierCount = 1, di.pMemoryBarriers = &b;
    vkCmdPipelineBarrier2(cmd, &di);
}

void drop_image(Image& t) {
    if (t.view) vkDestroyImageView(dev.device, t.view, nullptr);
    if (t.image) mkwvk::destroy_image(t.image, t.memory);
    t = Image{};
}

/// The image an evaluation resolves into: RGBA16F at the upscale size, GENERAL throughout, a
/// storage image because DLSS writes it and a transfer source because the caller blits it into the
/// game's own target. (vk_fsr3.cpp's output asks for TRANSFER_DST and not TRANSFER_SRC although its
/// call site blits *from* it; that is left as it is rather than changed from here.)
bool make_output(Image& t, uint32_t width, uint32_t height) {
    drop_image(t);
    t.format = VK_FORMAT_R16G16B16A16_SFLOAT;
    t.width = std::max(1u, width), t.height = std::max(1u, height);
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D;
    ci.format = t.format;
    ci.extent = {t.width, t.height, 1};
    ci.mipLevels = 1;
    ci.arrayLayers = 1;
    ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
               VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (!create_image(ci, t.image, t.memory, false)) {
        logf("vk: dlss: a %ux%u output image was not made", t.width, t.height);
        return false;
    }
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = t.image;
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = t.format;
    vi.subresourceRange = ONE_LEVEL;
    if (vkCreateImageView(dev.device, &vi, nullptr, &t.view) != VK_SUCCESS) {
        logf("vk: dlss: the output image's view was not made");
        drop_image(t);
        return false;
    }
    return true;
}

/// The output's first layout. Recorded once per image, and the flag is set only where the barrier
/// really went into a command buffer, so a run that got this far and then refused does not leave
/// the image remembered as GENERAL while it is still UNDEFINED.
void output_to_general(VkCommandBuffer cmd, Image& t) {
    if (t.in_general || !t.image) return;
    VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    b.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
    b.dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
    b.dstAccessMask = VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT;
    b.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    b.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = t.image;
    b.subresourceRange = ONE_LEVEL;
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.imageMemoryBarrierCount = 1, di.pImageMemoryBarriers = &b;
    vkCmdPipelineBarrier2(cmd, &di);
    t.in_general = true;
}

// ---------------------------------------------------------------------------------------------
// The NGX half. Five functions, two implementations, and the only part of this file that needs
// anything of NVIDIA's.
// ---------------------------------------------------------------------------------------------

/// NGX started, and this driver and GPU reporting Super Resolution as available. Asked once by
/// `supported()`, which logs the one line saying why not.
bool ngx_available();
/// The feature handle for these extents, made on first use because `CreateFeature` records into a
/// command buffer. True when a handle is there afterwards.
bool ngx_feature(VkCommandBuffer cmd);
/// One evaluation, into `out`. False when NGX refused it.
bool ngx_evaluate(VkCommandBuffer cmd, const Inputs& in, const Image& out);
/// Lets the feature handle go: the extents changed, or we are shutting down.
void ngx_drop_feature();
/// Lets NGX itself go.
void ngx_shutdown();

#if defined(RX_VK_DLSS_SDK)

/// The entry points this backend calls, resolved by name from the NGX core the NVIDIA driver ships
/// (`libnvidia-ngx.so`), through the loader pattern the rest of the renderer uses for
/// vulkan-1.dll (vk_main.cpp; on Linux host/posix/win32_posix.c makes that dlopen/dlsym).
///
/// They are resolved rather than linked for two reasons: the SDK's own library is then not a link
/// requirement of a gated build, and a machine with the SDK-built binary but no NVIDIA driver has
/// every pointer null here, so `supported()` is false instead of the process failing to start.
///
/// The two `NGX_VULKAN_*_DLSS_EXT*` helpers in nvsdk_ngx_helpers_vk.h are deliberately *not* used,
/// which is the one place this backend departs from the obvious reading of NVIDIA's sample code.
/// They are inline wrappers that call `NVSDK_NGX_VULKAN_CreateFeature` / `..._EvaluateFeature` by
/// name, so using them puts those symbols in this object file and hands the link back to NVIDIA's
/// library. What they do besides that is set the parameter keys below -- and those keys are the
/// SDK's own macros here, so a wrong one is a compile error, the same check using the helper would
/// have given. The risk this trades for is a key DLSS wants and this code does not set; the set is
/// written out in docs/upscaling.md so that it can be reviewed against the SDK's helper.
struct Ngx {
    HMODULE module = nullptr;
    decltype(&NVSDK_NGX_VULKAN_Init_with_ProjectID) init = nullptr;
    decltype(&NVSDK_NGX_VULKAN_Shutdown1) shutdown = nullptr;
    decltype(&NVSDK_NGX_VULKAN_GetCapabilityParameters) capability = nullptr;
    decltype(&NVSDK_NGX_VULKAN_AllocateParameters) allocate = nullptr;
    decltype(&NVSDK_NGX_VULKAN_DestroyParameters) destroy = nullptr;
    decltype(&NVSDK_NGX_VULKAN_CreateFeature) create = nullptr;
    decltype(&NVSDK_NGX_VULKAN_EvaluateFeature) evaluate = nullptr;
    decltype(&NVSDK_NGX_VULKAN_ReleaseFeature) release = nullptr;
    bool started = false;
    NVSDK_NGX_Parameter* params = nullptr;    ///< the evaluation's own parameters
    NVSDK_NGX_Handle* feature = nullptr;
};
Ngx ngx;

/// A project id of our own. NVIDIA's analytics key one is for licensed titles; the project-id form
/// exists exactly so a project without one can initialize.
constexpr const char* kProjectId = "a6b7c0de-7a1f-4f1e-9d2a-hookshot-dlss";
constexpr const char* kEngineVersion = "1.0";

bool ngx_load() {
    if (ngx.module) return true;
    // The core ships with the driver and is found on the usual search path; the model library
    // (libnvidia-ngx-dlss.so.*) sits beside the binary and is NGX's to find, which is what the
    // application data path below is for.
#ifdef _WIN32
    static const char* const names[]{"nvngx.dll", "_nvngx.dll"};
#else
    static const char* const names[]{"libnvidia-ngx.so.1", "libnvidia-ngx.so"};
#endif
    for (const char* name : names) {
        ngx.module = LoadLibraryA(name);
        if (ngx.module) {
            logf("vk: dlss: NGX loaded from %s", name);
            break;
        }
    }
    if (!ngx.module) {
        logf("vk: dlss: no NGX runtime here (%s), so DLSS is unavailable", names[0]);
        return false;
    }
#define NGX_RESOLVE(field, name)                                                                   \
    ngx.field = (decltype(ngx.field))(void*)GetProcAddress(ngx.module, name);                      \
    if (!ngx.field) {                                                                              \
        logf("vk: dlss: the NGX runtime has no %s", name);                                         \
        return false;                                                                              \
    }
    NGX_RESOLVE(init, "NVSDK_NGX_VULKAN_Init_with_ProjectID")
    NGX_RESOLVE(shutdown, "NVSDK_NGX_VULKAN_Shutdown1")
    NGX_RESOLVE(capability, "NVSDK_NGX_VULKAN_GetCapabilityParameters")
    NGX_RESOLVE(allocate, "NVSDK_NGX_VULKAN_AllocateParameters")
    NGX_RESOLVE(destroy, "NVSDK_NGX_VULKAN_DestroyParameters")
    NGX_RESOLVE(create, "NVSDK_NGX_VULKAN_CreateFeature")
    NGX_RESOLVE(evaluate, "NVSDK_NGX_VULKAN_EvaluateFeature")
    NGX_RESOLVE(release, "NVSDK_NGX_VULKAN_ReleaseFeature")
#undef NGX_RESOLVE
    return true;
}

/// The game folder, wide, for NGX's application data path: where it writes what it keeps and where
/// the model library beside the binary is looked for.
const wchar_t* data_path() {
    static wchar_t wide[MAX_PATH + 1];
    static const bool once = [] {
        char path[MAX_PATH + 1]{};
        const DWORD n = game_module_path(path, (DWORD)sizeof path);
        if (!n || n >= sizeof path) {
            wide[0] = L'.', wide[1] = 0;
            return true;
        }
        char* name = path + n;
        while (name > path && name[-1] != '\\' && name[-1] != '/') name--;
        *name = 0;
        size_t i = 0;
        for (; path[i] && i < MAX_PATH; i++) wide[i] = (wchar_t)(unsigned char)path[i];
        wide[i] = 0;
        if (!i) wide[0] = L'.', wide[1] = 0;
        return true;
    }();
    (void)once;
    return wide;
}

bool ngx_available() {
    if (!ngx_load()) return false;
    if (!ngx.started) {
        NVSDK_NGX_FeatureCommonInfo common{};
        const wchar_t* paths[]{data_path()};
        common.PathListInfo.Path = paths;
        common.PathListInfo.Length = 1;
        const NVSDK_NGX_Result res =
            ngx.init(kProjectId, NVSDK_NGX_ENGINE_TYPE_CUSTOM, kEngineVersion, data_path(),
                     dev.instance, dev.physical, dev.device, instance_proc(), vkGetDeviceProcAddr,
                     &common, NVSDK_NGX_Version_API);
        if (NVSDK_NGX_FAILED(res)) {
            logf("vk: dlss: NGX did not initialize (0x%08x)", (unsigned)res);
            return false;
        }
        ngx.started = true;
    }
    NVSDK_NGX_Parameter* caps = nullptr;
    if (NVSDK_NGX_FAILED(ngx.capability(&caps)) || !caps) {
        logf("vk: dlss: NGX gave no capability parameters");
        return false;
    }
    int available = 0;
    if (NVSDK_NGX_FAILED(caps->Get(NVSDK_NGX_Parameter_SuperSampling_Available, &available)) ||
        !available) {
        // (what NGX says about it: an old driver and a feature that failed to initialize are
        // different reports, and a user with the first can act on it)
        int needs_driver = 0, major = 0, minor = 0;
        caps->Get(NVSDK_NGX_Parameter_SuperSampling_NeedsUpdatedDriver, &needs_driver);
        caps->Get(NVSDK_NGX_Parameter_SuperSampling_MinDriverVersionMajor, &major);
        caps->Get(NVSDK_NGX_Parameter_SuperSampling_MinDriverVersionMinor, &minor);
        if (needs_driver)
            logf("vk: dlss: this driver is too old for DLSS (%d.%d or newer wanted)", major, minor);
        else
            logf("vk: dlss: this GPU and driver do not report Super Resolution");
        return false;
    }
    if (!ngx.params && NVSDK_NGX_FAILED(ngx.allocate(&ngx.params))) {
        logf("vk: dlss: NGX gave no parameters to evaluate with");
        ngx.params = nullptr;
        return false;
    }
    return ngx.params != nullptr;
}

bool ngx_feature(VkCommandBuffer cmd) {
    if (ngx.feature) return true;
    if (!ngx.params) return false;
    // Quality: the extents are the seam's, not a preset's, so DLSS is told the ratio it is being
    // given rather than being asked to pick one. Presets belong with the rest of the settings
    // (docs/upscaling.md, row 5) and are not invented here.
    ngx.params->Set(NVSDK_NGX_Parameter_Width, g.render_width);
    ngx.params->Set(NVSDK_NGX_Parameter_Height, g.render_height);
    ngx.params->Set(NVSDK_NGX_Parameter_OutWidth, g.upscale_width);
    ngx.params->Set(NVSDK_NGX_Parameter_OutHeight, g.upscale_height);
    ngx.params->Set(NVSDK_NGX_Parameter_PerfQualityValue,
                    (int)NVSDK_NGX_PerfQuality_Value_MaxQuality);
    // The three flags that are statements about this game's inputs rather than preferences: the
    // colour is HDR, the motion is at the render extent, and the depth is reversed. The input
    // contract in docs/upscaling.md is where each comes from; getting one wrong is a wrong picture.
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Feature_Create_Flags,
                    (int)(NVSDK_NGX_DLSS_Feature_Flags_IsHDR |
                          NVSDK_NGX_DLSS_Feature_Flags_MVLowRes |
                          NVSDK_NGX_DLSS_Feature_Flags_DepthInverted));
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Enable_Output_Subrects, 0);
    ngx.params->Set(NVSDK_NGX_Parameter_CreationNodeMask, 1u);
    ngx.params->Set(NVSDK_NGX_Parameter_VisibilityNodeMask, 1u);
    const NVSDK_NGX_Result res =
        ngx.create(cmd, NVSDK_NGX_Feature_SuperSampling, ngx.params, &ngx.feature);
    if (NVSDK_NGX_FAILED(res) || !ngx.feature) {
        logf("vk: dlss: the feature was not created for %ux%u -> %ux%u (0x%08x)", g.render_width,
             g.render_height, g.upscale_width, g.upscale_height, (unsigned)res);
        ngx.feature = nullptr;
        return false;
    }
    logf("vk: dlss: feature ready, %ux%u -> %ux%u", g.render_width, g.render_height,
         g.upscale_width, g.upscale_height);
    return true;
}

/// One input as NGX takes it: the view, its image, its range, its format and the extent it is read
/// at. Nothing here is defaulted; `upscale()` has already refused inputs that cannot fill it.
NVSDK_NGX_Resource_VK as_resource(VkImageView view, VkImage image, VkFormat format, uint32_t width,
                                  uint32_t height, bool read_write) {
    VkImageSubresourceRange range{};
    range.aspectMask =
        is_depth_format(format) ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT;
    range.baseMipLevel = 0, range.levelCount = 1;
    range.baseArrayLayer = 0, range.layerCount = 1;
    return NVSDK_NGX_Create_ImageView_Resource_VK(view, image, range, format, width, height,
                                                  read_write);
}

bool ngx_evaluate(VkCommandBuffer cmd, const Inputs& in, const Image& out) {
    if (!ngx.feature || !ngx.params) return false;
    NVSDK_NGX_Resource_VK color = as_resource(in.color, in.color_image, in.color_format,
                                              in.render_width, in.render_height, false);
    NVSDK_NGX_Resource_VK depth = as_resource(in.depth, in.depth_image, in.depth_format,
                                              in.render_width, in.render_height, false);
    NVSDK_NGX_Resource_VK motion = as_resource(in.motion, in.motion_image, in.motion_format,
                                               in.render_width, in.render_height, false);
    NVSDK_NGX_Resource_VK target =
        as_resource(out.view, out.image, out.format, out.width, out.height, true);
    ngx.params->Set(NVSDK_NGX_Parameter_Color, &color);
    ngx.params->Set(NVSDK_NGX_Parameter_Depth, &depth);
    ngx.params->Set(NVSDK_NGX_Parameter_MotionVectors, &motion);
    ngx.params->Set(NVSDK_NGX_Parameter_Output, &target);
    ngx.params->Set(NVSDK_NGX_Parameter_Jitter_Offset_X, in.jitter[0]);
    ngx.params->Set(NVSDK_NGX_Parameter_Jitter_Offset_Y, in.jitter[1]);
    ngx.params->Set(NVSDK_NGX_Parameter_MV_Scale_X, in.motion_scale[0]);
    ngx.params->Set(NVSDK_NGX_Parameter_MV_Scale_Y, in.motion_scale[1]);
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Render_Subrect_Dimensions_Width, g.render_width);
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Render_Subrect_Dimensions_Height, g.render_height);
    ngx.params->Set(NVSDK_NGX_Parameter_Reset, in.reset ? 1 : 0);
    ngx.params->Set(NVSDK_NGX_Parameter_Sharpness, in.sharpen ? in.sharpness : 0.0f);
    // The game does not expose an exposure, and the colour it hands over is already in the range
    // its own resolve worked in, so both of these are the neutral value rather than a guess.
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Pre_Exposure, 1.0f);
    ngx.params->Set(NVSDK_NGX_Parameter_DLSS_Exposure_Scale, 1.0f);
    const NVSDK_NGX_Result res = ngx.evaluate(cmd, ngx.feature, ngx.params, nullptr);
    if (NVSDK_NGX_FAILED(res)) {
        logf("vk: dlss: the evaluation was refused (0x%08x)", (unsigned)res);
        return false;
    }
    return true;
}

void ngx_drop_feature() {
    if (ngx.feature && ngx.release) ngx.release(ngx.feature);
    ngx.feature = nullptr;
}

void ngx_shutdown() {
    ngx_drop_feature();
    if (ngx.params && ngx.destroy) ngx.destroy(ngx.params);
    ngx.params = nullptr;
    if (ngx.started && ngx.shutdown) ngx.shutdown(dev.device);
    ngx.started = false;
    // (the module is left loaded: the driver's own libraries are not ours to unload under a device
    // that may still be alive)
}

#else   // no NGX SDK: the gate is off, and each of the five refuses

bool ngx_available() {
    return false;
}
bool ngx_feature(VkCommandBuffer) {
    return false;
}
bool ngx_evaluate(VkCommandBuffer, const Inputs&, const Image&) {
    return false;
}
void ngx_drop_feature() {}
void ngx_shutdown() {}

#endif   // RX_VK_DLSS_SDK

// ---- Windows and Streamline, deliberately not written ----
//
// On Windows the normal route to DLSS is Streamline (sl.dlss) rather than NGX directly, and
// Streamline is also the only route to DLSS frame generation. It is not implemented here, and that
// is a decision rather than an omission: it needs a second runtime, a second set of headers and a
// Windows machine to try it on, and none of that is wanted in the same change as the Linux path.
//
// The shape it would take is already here. Streamline would be a third arm of the gate above --
// `#elif defined(RX_VK_DLSS_SL)` -- implementing the same five `ngx_*` functions (named for the
// job, not for NGX, for exactly this reason) with slInit / slSetFeatureLoaded /
// slAllocateResources / slEvaluateFeature. Nothing outside those five would change: `Inputs`, the
// output image, the layout transitions, the barriers and every refusal below are vendor-neutral.
// Streamline has no Linux build, so the NGX arm stays the Linux one whatever happens on Windows.

} // namespace

bool supported() {
    static const bool answer = [] {
        if (!wanted()) return false;   // (RX_VK_DLSS=1: nothing below runs unless it is asked for)
        if (!dev.device) {
            logf("vk: dlss: no device yet");
            return false;
        }
        if (!kHaveSdk) {
            logf("vk: dlss: this build has no NGX SDK (RX_VK_DLSS_SDK), so DLSS is unavailable; "
                 "docs/upscaling.md says what to point it at");
            return false;
        }
        return ngx_available();
    }();
    return answer;
}

bool prepare(uint32_t render_width, uint32_t render_height, uint32_t upscale_width,
             uint32_t upscale_height) {
    if (!supported()) return false;
    const uint32_t rw = std::max(1u, render_width), rh = std::max(1u, render_height);
    const uint32_t uw = std::max(1u, upscale_width), uh = std::max(1u, upscale_height);
    if (g.output.image && g.render_width == rw && g.render_height == rh && g.upscale_width == uw &&
        g.upscale_height == uh) {
        return true;
    }
    // A feature is created for one pair of extents, so a change of either ends this one. The next
    // evaluation makes another, because that is the first place there is a command buffer.
    ngx_drop_feature();
    if (!make_output(g.output, uw, uh)) {
        shutdown();
        return false;
    }
    g.render_width = rw, g.render_height = rh, g.upscale_width = uw, g.upscale_height = uh;
    g.first_evaluation = true;
    logf("vk: dlss: %ux%u -> %ux%u", rw, rh, uw, uh);
    return true;
}

bool upscale(VkCommandBuffer cmd, const Inputs& in) {
    // Every clause here is a refusal rather than a default, for the reason docs/upscaling.md gives:
    // an upscaler running on inputs it has guessed produces ghosting that is reported as a renderer
    // bug. The caller keeps whatever it would otherwise have done.
    if (!in.color || !in.depth || !in.motion) return false;
    // NGX is handed resources, not views (vk_dlss.h): without the images and their formats an
    // evaluation would be describing something NVIDIA's runtime cannot see.
    if (!in.color_image || !in.depth_image || !in.motion_image) return false;
    if (in.color_format == VK_FORMAT_UNDEFINED || in.depth_format == VK_FORMAT_UNDEFINED ||
        in.motion_format == VK_FORMAT_UNDEFINED) {
        return false;
    }
    if (!in.render_width || !in.render_height || !in.upscale_width || !in.upscale_height) return false;
    // An upscaler cannot resolve into something smaller than what it was given; equal is DLAA.
    if (in.upscale_width < in.render_width || in.upscale_height < in.render_height) return false;
    if (!(in.near_plane > 0.0f) || !(in.fov_y > 0.0f)) return false;   // (Inputs defaults them)
    if (!prepare(in.render_width, in.render_height, in.upscale_width, in.upscale_height)) return false;

    const bool first = g.first_evaluation;
    output_to_general(cmd, g.output);
    if (!ngx_feature(cmd)) return false;
    g.first_evaluation = false;

    // What the game's draws and the motion pass wrote has to be visible to NGX's own passes.
    memory_barrier(cmd, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                   VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT |
                       VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT |
                       VK_ACCESS_2_SHADER_WRITE_BIT | VK_ACCESS_2_TRANSFER_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                   VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_SHADER_WRITE_BIT);
    Inputs evaluated = in;
    evaluated.reset = in.reset || first;   // (the first evaluation has no history to carry)
    if (!ngx_evaluate(cmd, evaluated, g.output)) return false;
    // (what reads the output: the blit into the game's target, and anything sampling it after)
    memory_barrier(cmd, VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_2_SHADER_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_TRANSFER_BIT | VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT |
                       VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                   VK_ACCESS_2_SHADER_READ_BIT | VK_ACCESS_2_TRANSFER_READ_BIT);
    return true;
}

VkImage output_image() {
    return g.output.image;
}

VkImageView output_view() {
    return g.output.view;
}

void shutdown() {
    ngx_shutdown();
    drop_image(g.output);
    g.render_width = g.render_height = g.upscale_width = g.upscale_height = 0;
    g.first_evaluation = true;
}

} // namespace mkwvk::dlss
