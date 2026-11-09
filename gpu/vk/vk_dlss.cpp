// (hookshot DLSS) NVIDIA DLSS Super Resolution in the place of OoT's gameplay TAAU networks (one per render scale).
//
// The game renders its 3D at a lower resolution and resolves it with its own temporal network; with DLSS chosen in the
// Insert menu, that dispatch is recorded as DLSS instead (vk_draw.cpp taau_dlss finds its inputs):
//   1. shaders/dlss_prep.comp: the network's scene colour made linear (RGBA16F), its motion as render pixels in NVIDIA's
//      convention (RG16F, the menu's "DLSS motion" choosing the network's sign and units) and its depth (R32F);
//   2. NGX's DLSS evaluate, render size to the network's output size, into an RGBA16F image;
//   3. shaders/dlss_out.comp: that into the network's own output (B10G11R11), which the game then composes as before.
// NGX itself is in hookshot_ngx.dll (gpu/vk/ngx), loaded from beside this DLL. With it missing, a GPU or driver without
// DLSS, inputs it cannot use, or any NGX failure, the network runs as before and the reason is logged once.
// Motion: OoT's network takes the scene colour alone (no motion, depth or jitter), so DLSS's motion is made here: the
// scene's depth target of the render size (vk_draw.cpp end_pass notes every pass's) reprojected by the camera, the
// view-projection found in the network's constant buffers (vk_draw.cpp taau_dlss), this frame's to the last one's.
// That is the camera's motion only: what moves by itself (Link, enemies) gets the motion of what is behind it.
// Env: RX_DLSS=<0-4> the menu's DLSS row at start; RX_DLSS_MOTION=<0-2> its motion row (camera, camera Y flipped, none);
// RX_DLSS_OFF=1 does not load the bridge. RX_DLSS_DEPTH_INVERTED=1 (reversed Z); RX_DLSS_NO_SQRT=1 the colour taken as
// linear already. Diagnostic: RX_DLSS_NGX_LOG=1, RX_DLSS_LOG_CANDIDATES=1, RX_DLSS_LOG_CB=<n>, RX_DLSS_JITTER=<x>,<y>,
// RX_DLSS_MV=<slot> (RX_DLSS_MV_SCALE=<x>,<y>), RX_DLSS_COLOR / RX_DLSS_DEPTH=<slot>, RX_DLSS_KEYS=<key prefixes>.
// The bridge is a Windows DLL (gpu/vk/ngx). Without it, or on a GPU that is not NVIDIA's, nothing here runs.
#include "vk_dlss.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>

#include "vk_menu.h"
#include "vk_paths.h"
#include "vk_texture.h"
#include "ngx/hookshot_ngx.h"
#include "shaders/dlss_prep_comp_spv.h"
#include "shaders/dlss_out_comp_spv.h"

// Linux's Win32 shim has LoadLibraryA. A full path loads that file; NGX then looks beside it for nvngx_dlss.dll.
#ifndef LOAD_WITH_ALTERED_SEARCH_PATH
#define LOAD_WITH_ALTERED_SEARCH_PATH 0x00000008
static inline HMODULE LoadLibraryExA(const char* name, HANDLE, DWORD) { return LoadLibraryA(name); }
#endif

namespace mkwvk {
namespace {

struct Ngx {
    HMODULE dll = nullptr;
    PFN_hsngx_required_extensions required = nullptr;
    PFN_hsngx_init init = nullptr;
    PFN_hsngx_create create = nullptr;
    PFN_hsngx_evaluate evaluate = nullptr;
    PFN_hsngx_release release = nullptr;
    PFN_hsngx_shutdown shutdown = nullptr;
    PFN_hsngx_vram_bytes vram = nullptr;   // (build 7, optional: an older hookshot_ngx.dll has none)
    bool tried = false;
} ngx;

enum class State { Untried, NoDll, NotNvidia, Unsupported, Running, Failed };
std::atomic<State> state{State::Untried};
std::atomic<bool> no_motion_seen{false};
std::mutex mu;

void ngx_log(const char* line) { logf("%s", line); }

/// The folder hookshot_vk.dll is in, with its separator ('\\' or '/'), or "" when not known
std::string own_dir() {
    HMODULE self = nullptr;
    char path[MAX_PATH];
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCSTR)(void*)&own_dir, &self))
        return "";
    const DWORD n = GetModuleFileNameA(self, path, MAX_PATH);
    if (!n || n >= MAX_PATH) return "";
    std::string s(path, n);
    const size_t at = s.find_last_of("\\/");
    return at == std::string::npos ? "" : s.substr(0, at + 1);
}

/// hookshot_ngx.dll from beside this DLL, once. False: not there (the reason logged)
bool load_ngx() {
    if (ngx.tried) return ngx.dll != nullptr;
    ngx.tried = true;
    const std::string path = own_dir() + "hookshot_ngx.dll";
    ngx.dll = LoadLibraryExA(path.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!ngx.dll) {
        // (quiet: DLSS is off by default. The menu note and dlss_frame_status say why once it is turned on)
        state = State::NoDll;
        return false;
    }
    const auto version = (PFN_hsngx_interface_version)(void*)GetProcAddress(ngx.dll, "hsngx_interface_version");
    ngx.required = (PFN_hsngx_required_extensions)(void*)GetProcAddress(ngx.dll, "hsngx_required_extensions");
    ngx.init = (PFN_hsngx_init)(void*)GetProcAddress(ngx.dll, "hsngx_init");
    ngx.create = (PFN_hsngx_create)(void*)GetProcAddress(ngx.dll, "hsngx_create");
    ngx.evaluate = (PFN_hsngx_evaluate)(void*)GetProcAddress(ngx.dll, "hsngx_evaluate");
    ngx.release = (PFN_hsngx_release)(void*)GetProcAddress(ngx.dll, "hsngx_release");
    ngx.shutdown = (PFN_hsngx_shutdown)(void*)GetProcAddress(ngx.dll, "hsngx_shutdown");
    ngx.vram = (PFN_hsngx_vram_bytes)(void*)GetProcAddress(ngx.dll, "hsngx_vram_bytes");
    if (!version || version() != HSNGX_INTERFACE_VERSION || !ngx.required || !ngx.init || !ngx.create || !ngx.evaluate ||
        !ngx.release || !ngx.shutdown) {
        logf("dlss: %s is not the version this renderer was built with: DLSS is off", path.c_str());
        FreeLibrary(ngx.dll);
        ngx.dll = nullptr;
        state = State::NoDll;
        return false;
    }
    logf("dlss: %s loaded", path.c_str());
    return true;
}

void add_extensions(uint32_t n, const char** names, const std::vector<VkExtensionProperties>& have,
                    std::vector<const char*>& want, const char* what) {
    for (uint32_t i = 0; i < n; i++) {
        if (!names || !names[i]) continue;
        // (core in Vulkan 1.2, whose feature the renderer turns on: the EXT beside it is not allowed, the KHR is there already)
        if (!strcmp(names[i], "VK_EXT_buffer_device_address") || !strcmp(names[i], "VK_KHR_buffer_device_address")) continue;
        bool present = false, already = false;
        for (const auto& e : have) present |= !strcmp(e.extensionName, names[i]);
        for (const char* w : want) already |= !strcmp(w, names[i]);
        if (present && !already) want.push_back(names[i]);
        logf("dlss: NGX %s extension %s%s", what, names[i], !present ? ": not available" : already ? ": on already" : ": on");
    }
}

// ---- the images and pipelines of the passes around NGX

struct Image {
    VkImage image = VK_NULL_HANDLE;
    VmaAllocation mem = nullptr;
    VkImageView view = VK_NULL_HANDLE;
    VkFormat format = VK_FORMAT_UNDEFINED;
    uint32_t w = 0, h = 0;
};
struct Old { Image im; void* feature; uint64_t frame; };
std::vector<Old> old;   // (given back some presents later, when no work in flight can use them)

struct Pass {
    VkPipeline pipe = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
};
Pass prep, out_pass;
VkSampler nearest = VK_NULL_HANDLE, linear = VK_NULL_HANDLE;
bool passes_tried = false, passes_ok = false;

Pass make_pass(const uint32_t* code, size_t size, uint32_t samplers, uint32_t storages, uint32_t push) {
    Pass p;
    std::vector<VkDescriptorSetLayoutBinding> b;
    for (uint32_t i = 0; i < samplers + storages; i++)
        b.push_back({i, i < samplers ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1,
                     VK_SHADER_STAGE_COMPUTE_BIT, nullptr});
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    li.flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR;
    li.bindingCount = (uint32_t)b.size(), li.pBindings = b.data();
    VkDescriptorSetLayout set{};
    if (vkCreateDescriptorSetLayout(dev.device, &li, nullptr, &set) != VK_SUCCESS) return p;
    const VkPushConstantRange pr{VK_SHADER_STAGE_COMPUTE_BIT, 0, push};
    VkPipelineLayoutCreateInfo pi{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pi.setLayoutCount = 1, pi.pSetLayouts = &set, pi.pushConstantRangeCount = 1, pi.pPushConstantRanges = &pr;
    if (vkCreatePipelineLayout(dev.device, &pi, nullptr, &p.layout) != VK_SUCCESS) return p;
    VkShaderModuleCreateInfo mi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    mi.codeSize = size, mi.pCode = code;
    VkShaderModule m{};
    if (vkCreateShaderModule(dev.device, &mi, nullptr, &m) != VK_SUCCESS) return p;
    VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, m, "main", nullptr};
    ci.layout = p.layout;
    if (vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &ci, nullptr, &p.pipe) != VK_SUCCESS) p.pipe = VK_NULL_HANDLE;
    vkDestroyShaderModule(dev.device, m, nullptr);
    return p;
}

bool make_passes() {
    if (passes_tried) return passes_ok;
    passes_tried = true;
    prep = make_pass(DLSS_PREP_COMP_SPV, sizeof DLSS_PREP_COMP_SPV, 3, 3, 96);
    out_pass = make_pass(DLSS_OUT_COMP_SPV, sizeof DLSS_OUT_COMP_SPV, 1, 1, 8);
    VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    si.addressModeU = si.addressModeV = si.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    si.magFilter = si.minFilter = VK_FILTER_NEAREST;
    vkCreateSampler(dev.device, &si, nullptr, &nearest);
    si.magFilter = si.minFilter = VK_FILTER_LINEAR;
    vkCreateSampler(dev.device, &si, nullptr, &linear);
    passes_ok = prep.pipe && out_pass.pipe && nearest && linear;
    if (!passes_ok) logf("dlss: the passes around DLSS were not made: DLSS is off");
    return passes_ok;
}

void barrier(VkCommandBuffer cmd) {
    VkMemoryBarrier2 mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
    mb.srcStageMask = mb.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    mb.srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT;
    mb.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.memoryBarrierCount = 1, di.pMemoryBarriers = &mb;
    vkCmdPipelineBarrier2(cmd, &di);
}

void retire(Image& im, void* feature = nullptr) {
    if (im.image || feature) old.push_back({im, feature, texture_frame_no()});
    im = Image{};
}
void collect() {
    for (size_t i = 0; i < old.size();) {
        if (texture_frame_no() - old[i].frame < 8) { i++; continue; }
        if (old[i].im.view) vkDestroyImageView(dev.device, old[i].im.view, nullptr);
        if (old[i].im.image) destroy_image(old[i].im.image, old[i].im.mem);
        if (old[i].feature) ngx.release(old[i].feature);
        old.erase(old.begin() + (ptrdiff_t)i);
    }
}

/// im at w x h in format (made again, the old one retired, when either differs). False: not made
bool ensure(VkCommandBuffer cmd, Image& im, VkFormat format, uint32_t w, uint32_t h) {
    if (im.image && im.w == w && im.h == h && im.format == format) return true;
    retire(im);
    VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ic.imageType = VK_IMAGE_TYPE_2D, ic.format = format;
    ic.extent = {w, h, 1}, ic.mipLevels = 1, ic.arrayLayers = 1, ic.samples = VK_SAMPLE_COUNT_1_BIT;
    ic.tiling = VK_IMAGE_TILING_OPTIMAL, ic.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    if (!create_image(ic, im.image, im.mem)) { im = Image{}; return false; }
    VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vc.image = im.image, vc.viewType = VK_IMAGE_VIEW_TYPE_2D, vc.format = format;
    vc.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    if (vkCreateImageView(dev.device, &vc, nullptr, &im.view) != VK_SUCCESS) {
        destroy_image(im.image, im.mem);
        im = Image{};
        return false;
    }
    im.format = format, im.w = w, im.h = h;
    VkImageMemoryBarrier2 ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};   // (UNDEFINED -> GENERAL, once)
    ib.srcStageMask = VK_PIPELINE_STAGE_2_NONE, ib.srcAccessMask = VK_ACCESS_2_NONE;
    ib.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    ib.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    ib.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, ib.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    ib.srcQueueFamilyIndex = ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    ib.image = im.image, ib.subresourceRange = vc.subresourceRange;
    VkDependencyInfo di{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    di.imageMemoryBarrierCount = 1, di.pImageMemoryBarriers = &ib;
    vkCmdPipelineBarrier2(cmd, &di);
    return true;
}

HsNgxImage ngx_image(const Image& im) {
    return HsNgxImage{(uint64_t)im.image, (uint64_t)im.view, (uint32_t)im.format, im.w, im.h, VK_IMAGE_ASPECT_COLOR_BIT};
}

// ---- the menu's choices

int choice() { return dlss_choice(); }
int motion_choice() {
    static const int env = getenv("RX_DLSS_MOTION") ? atoi(getenv("RX_DLSS_MOTION")) : -1;
    const int m = env >= 0 ? env : opts.dlss_motion;
    return m == 6 ? 2 : m < 0 || m > 2 ? 1 : m;   // (0 camera, 1 camera with Y flipped (OoT's), 2 none; build 1's 0-6, 6 none)
}
/// NGX's render preset for the DLSS row: 1 CNN (E), 2 transformer (K), 3 second transformer (M), 4 NVIDIA's default
int preset_of(int c) {
    switch (c) {
    case 1: return 5;    // NVSDK_NGX_DLSS_Hint_Render_Preset_E: the last CNN model
    case 2: return 11;   // _K: DLSS 4's transformer
    case 3: return 13;   // _M: DLSS 4.5's second-generation transformer
    default: return opts.dlss_quality == 5 ? 11 : 0;   // _Default (DLAA: _K, the transformer, as NVIDIA's own default for it)
    }
}
/// The quality mode NGX is told, from the ratio of the sizes (the sizes themselves are given; the Resolution row sets them)
int quality_of(uint32_t rw, uint32_t ow) {
    static const int asked[] = {-1, 2, 1, 0, 3, 5};   // (the DLSS quality row: Quality, Balanced, Performance, Ultra Performance, DLAA)
    if (opts.dlss_quality > 0 && opts.dlss_quality <= 5) return asked[opts.dlss_quality];
    const double r = ow ? (double)rw / ow : 1.0;
    if (r >= 0.99) return 5;   // DLAA
    if (r >= 0.62) return 2;   // MaxQuality (UltraQuality, 4, is refused by DLSS SR: FAIL_UnsupportedParameter)
    if (r >= 0.54) return 1;   // Balanced
    if (r >= 0.42) return 0;   // MaxPerf
    return 3;                  // UltraPerformance
}
/// (RX_DLSS_MV, a motion texture named by hand) its motion to render pixels, current to previous: RX_DLSS_MV_SCALE=<x>,<y>
/// multiplies UV (default -1,-1: a velocity buffer of current - previous in UV)
void mv_texture_scale(uint32_t rw, uint32_t rh, float& sx, float& sy) {
    float x = -1.0f, y = -1.0f;
    if (const char* e = getenv("RX_DLSS_MV_SCALE")) sscanf(e, "%f,%f", &x, &y);
    sx = x * (float)rw, sy = y * (float)rh;
}

// ---- the scene depth: the passes' depth targets (vk_draw.cpp end_pass), per present

struct Cand {
    VkImage image = VK_NULL_HANDLE;
    VkFormat format = VK_FORMAT_UNDEFINED;
    VkImageAspectFlags aspect = 0;
    uint32_t id = 0, level = 0, layer = 0, w = 0, h = 0;
    int draws = 0;
};
std::mutex cand_mu;
uint64_t cand_frame = 0;
std::vector<Cand> cands, cands_prev;   // (this present's, and the one before's)
std::atomic<uint64_t> log_cands_until{0};
struct DepthView { VkImage image; uint32_t id, level, layer; VkFormat format; VkImageView view; uint64_t used; };
std::vector<DepthView> depth_views;

bool candidates_env() {
    static const bool on = getenv("RX_DLSS_LOG_CANDIDATES") && atoi(getenv("RX_DLSS_LOG_CANDIDATES"));
    return on;
}

/// A depth-aspect 2D view of a candidate (kept per image; ones unused for a while are dropped)
VkImageView depth_view_of(const Cand& c) {
    static std::mutex views_mu;
    std::lock_guard lk(views_mu);
    const uint64_t frame = texture_frame_no();
    for (auto& v : depth_views)
        if (v.image == c.image && v.id == c.id && v.level == c.level && v.layer == c.layer && v.format == c.format) {
            v.used = frame;
            return v.view;
        }
    for (size_t i = 0; i < depth_views.size();)   // (dropped long after their last use: no work in flight has them)
        if (frame - depth_views[i].used > 120) {
            vkDestroyImageView(dev.device, depth_views[i].view, nullptr);
            depth_views.erase(depth_views.begin() + (ptrdiff_t)i);
        } else i++;
    VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vc.image = c.image, vc.viewType = VK_IMAGE_VIEW_TYPE_2D, vc.format = c.format;
    vc.subresourceRange = {VK_IMAGE_ASPECT_DEPTH_BIT, c.level, 1, c.layer, 1};
    VkImageView view = VK_NULL_HANDLE;
    if (vkCreateImageView(dev.device, &vc, nullptr, &view) != VK_SUCCESS) return VK_NULL_HANDLE;
    depth_views.push_back({c.image, c.id, c.level, c.layer, c.format, view, frame});
    return view;
}


std::atomic<uint32_t> input_w{0}, input_h{0};

// ---- the camera: this present's view-projection and the last one's, made one reprojection (current NDC to previous)

bool invert4(const double m[16], double out[16]) {
    double inv[16];
    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
    const double det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    if (!std::isfinite(det) || std::fabs(det) < 1e-12) return false;
    for (int i = 0; i < 16; i++) out[i] = inv[i] / det;
    return true;
}

// ---- DLSS's own state
struct Run {
    Image color, mv, depth, output;
    void* feature = nullptr;
    uint32_t rw = 0, rh = 0, ow = 0, oh = 0;
    int preset = -1, flags = -1, motion = -1, quality = -1;
    uint64_t last_frame = 0;
    std::chrono::steady_clock::time_point last_time{};
    int failures = 0;
    double prev_vp[16] = {};   // (the camera of the last frame DLSS ran in, and that frame)
    uint64_t prev_vp_frame = 0;
    std::string said;          // (the last 'dlss: quality' line: logged again on every change)
} run;
std::atomic<int> missing{0};   // (what the last frame lacked, for the menu: 1 depth, 2 the camera)

}  // namespace

void dlss_instance_extensions(const std::vector<VkExtensionProperties>& have, std::vector<const char*>& want) {
    if (getenv("RX_DLSS_OFF") || !load_ngx()) return;
    uint32_t ni = 0, nd = 0;
    const char** ie = nullptr;
    const char** de = nullptr;
    if (ngx.required(&ni, &ie, &nd, &de) == 0) add_extensions(ni, ie, have, want, "instance");
}

void dlss_device_extensions(const std::vector<VkExtensionProperties>& have, std::vector<const char*>& want) {
    if (!ngx.dll) return;
    if (dev.props.vendorID != 0x10DE) {
        logf("dlss: not an NVIDIA GPU (vendor %#x): DLSS is off", dev.props.vendorID);
        state = State::NotNvidia;
        return;
    }
    uint32_t ni = 0, nd = 0;
    const char** ie = nullptr;
    const char** de = nullptr;
    if (ngx.required(&ni, &ie, &nd, &de) == 0) add_extensions(nd, de, have, want, "device");
}

void dlss_device_extensions_refused() { state = State::Unsupported; }

void dlss_shutdown() {
    std::lock_guard lk(mu);
    if (state != State::Running) return;
    if (run.feature) ngx.release(run.feature);
    run.feature = nullptr;
    for (auto& o : old)
        if (o.feature) ngx.release(o.feature), o.feature = nullptr;
    for (auto& v : depth_views) if (v.view) vkDestroyImageView(dev.device, v.view, nullptr);
    depth_views.clear();
    ngx.shutdown();
    state = State::Failed;
}

bool dlss_wanted() {
    if (choice() <= 0) return false;
    const State s = state.load();
    return s == State::Untried || s == State::Running;
}

const char* dlss_state() {
    if (choice() <= 0) return "";
    switch (state.load()) {
    case State::NoDll: return " (no hookshot_ngx.dll)";
    case State::NotNvidia: return " (needs an NVIDIA RTX GPU)";
    case State::Unsupported: return " (not supported here)";
    case State::Failed: return " (failed, see the log)";
    default: break;
    }
    const int m = missing.load();
    return m == 1 ? " (no depth found)" : m == 2 ? " (no camera found)" : m == 3 ? " (no depth or camera found)" : "";
}

// ---- (build 5) what the log says about the rows and the TAAU
std::atomic<uint64_t> taau_frame{0}, taau_key{0};
std::atomic<uint32_t> taau_w{0}, taau_h{0};
void dlss_taau_seen(uint64_t key, uint32_t w, uint32_t h) {
    taau_frame = texture_frame_no(), taau_key = key, taau_w = w, taau_h = h;
}
const char* hookshot_build_version();   // (vk_menu.cpp)
void dlss_frame_status() {
    static std::string said;
    static uint64_t chosen_at = 0, told_missing = 0;
    static const char* rows[] = {"Off", "CNN (DLSS 3)", "Transformer (DLSS 4)", "Transformer 2 (DLSS 4.5)", "NVIDIA default"};
    static const char* qs[] = {"Auto (from Resolution)", "Quality", "Balanced", "Performance", "Ultra Performance", "DLAA"};
    static const char* ms[] = {"Camera", "Camera, Y flipped", "None"};
    const uint64_t frame = texture_frame_no();
    const int c = choice(), q = opts.dlss_quality, m = motion_choice();
    const int k = dlss_quality_twelfths();
    char line[400];
    snprintf(line, sizeof line, "dlss: settings (%s): DLSS %s, DLSS quality %s, DLSS motion %s, Resolution row %d%s; the 3D %s; "
             "Sharpness %d%%, Upscaling %d", hookshot_build_version(), c >= 0 && c <= 4 ? rows[c] : "?", q >= 0 && q <= 5 ? qs[q] : "?", m >= 0 && m <= 2 ? ms[m] : "?",
             opts.resolution, k ? " (set by DLSS quality)" : "",
             k ? (std::to_string(k) + "/12 of the output").c_str() : resolution_auto() ? "dynamic (Auto)" : "by the Resolution row",
             opts.sharpness, opts.upscale);
    if (said != line && (c > 0 || !said.empty())) {   // (DLSS off, and it has not been on: the log stays as it was)
        said = line;
        chosen_at = frame, told_missing = 0;
        logf("%s", line);
        if (c > 0 && state.load() == State::NoDll)
            logf("dlss: hookshot_ngx.dll is not beside the renderer: the game's own upscaler stays");
    }
    const uint64_t seen = taau_frame.load();
    static uint64_t reported = ~0ull;
    if (c > 0 && seen > chosen_at && reported != chosen_at) {   // (the first TAAU network after a change of the rows)
        reported = chosen_at;
        logf("dlss: hook point after this setting: TAAU network %016llx, its input %ux%u", (unsigned long long)taau_key.load(),
             taau_w.load(), taau_h.load());
    }
    {   // (build 7) every 300 presents while DLSS runs: what it holds in VRAM, and the driver's use and allowance
        static uint64_t presents = 0;
        std::unique_lock lk(mu, std::defer_lock);   // (DLSS's state as the recording thread leaves it; busy: the next present)
        if (state.load() == State::Running && ++presents >= 300 && lk.try_lock()) {
            presents = 0;
            const auto mb = [](const Image& im, uint32_t bpp) { return (double)im.w * im.h * bpp / (1024.0 * 1024.0); };
            const double ours = mb(run.color, 8) + mb(run.mv, 4) + mb(run.depth, 4) + mb(run.output, 8);
            const unsigned long long nb = ngx.vram ? ngx.vram() : 0;
            uint64_t used = 0, allowed = 0;
            const bool known = vram_usage(&used, &allowed);
            char ngx_mb[32];
            if (nb) snprintf(ngx_mb, sizeof ngx_mb, "%.0f MB", nb / (1024.0 * 1024.0));
            else snprintf(ngx_mb, sizeof ngx_mb, "not known");
            char drv[80];
            if (known) snprintf(drv, sizeof drv, "%llu MB used of %llu MB allowed", (unsigned long long)(used >> 20), (unsigned long long)(allowed >> 20));
            else snprintf(drv, sizeof drv, "not known");
            logf("dlss: VRAM: DLSS's own images %.0f MB (%ux%u in, %ux%u out), NGX %s, %zu depth views, %zu retired; the driver: %s%s",
                 ours, run.rw, run.rh, run.ow, run.oh, ngx_mb, depth_views.size(), old.size(), drv,
                 known && used > allowed ? " - OVER: the driver pages to system memory, the GPU slows down" : "");
        }
    }
    if (c <= 0) return;
    const State st = state.load();
    // (chosen, and no TAAU network DLSS can take for 10 s of presents: why, once per change of the rows)
    if (!told_missing && frame - chosen_at > 600 && (!seen || frame - seen > 600)) {
        told_missing = frame;
        logf("dlss: DLSS chosen but not running: %s", st == State::NoDll ? "hookshot_ngx.dll is missing"
                                                     : st == State::NotNvidia ? "not an NVIDIA GPU"
                                                     : st == State::Unsupported ? "NGX / the driver refused DLSS"
                                                     : st == State::Failed ? "it failed (see the lines above)"
                                                     : "no TAAU network ran for 10 s (a menu, a movie, or a network of a shape "
                                                       "it does not know: the 'vk: network' lines list them)");
    } else if (told_missing && seen && seen > told_missing) {
        told_missing = 0;   // (build 7: chosen_at kept, so the hook-point line is not said twice)
        logf("dlss: TAAU network %016llx found again (%ux%u)", (unsigned long long)taau_key.load(), taau_w.load(), taau_h.load());
    }
}

bool dlss_input_size(uint32_t& w, uint32_t& h) {
    w = input_w.load(), h = input_h.load();
    return state == State::Running && choice() > 0 && w && texture_frame_no() - run.last_frame < 30;
}

bool dlss_log_candidates() {
    const uint64_t f = texture_frame_no(), until = log_cands_until.load();
    return candidates_env() && until && f + 3 > until && f <= until;
}
void dlss_log_candidates_from(uint64_t frame) {
    if (!candidates_env()) return;
    log_cands_until = frame + 3;   // (the three whole frames after the first TAAU)
    logf("dlss candidates: every pass of frames %llu..%llu follows (RX_DLSS_LOG_CANDIDATES)", (unsigned long long)frame + 1,
         (unsigned long long)frame + 3);
}

void dlss_note_pass(Texture* depth, uint32_t width, uint32_t height, int draws, int colours, VkFormat colour0, uint64_t prog) {
    const bool want = choice() > 0 && (state == State::Untried || state == State::Running);
    const bool log = dlss_log_candidates();
    if (!want && !log) return;
    if (log)
        logf("dlss candidates: frame %llu pass %ux%u, %d colour target(s) (first VkFormat %d), depth %s %ux%u VkFormat %d id %u, "
             "%d draws, program %016llx", (unsigned long long)texture_frame_no(), width, height, colours, (int)colour0,
             depth ? "yes" : "no", depth ? depth->width : 0, depth ? depth->height : 0, depth ? (int)depth->format : 0,
             depth ? depth->id : 0, draws, (unsigned long long)prog);
    if (!depth || draws <= 0 || !depth->image || !(depth->aspect & VK_IMAGE_ASPECT_DEPTH_BIT) || !(depth->usage & VK_IMAGE_USAGE_SAMPLED_BIT) || depth->samples != VK_SAMPLE_COUNT_1_BIT ||
        depth->type != VK_IMAGE_TYPE_2D)
        return;
    std::lock_guard lk(cand_mu);
    const uint64_t frame = texture_frame_no();
    if (frame != cand_frame) {
        cands_prev = frame == cand_frame + 1 ? std::move(cands) : std::vector<Cand>{};
        cands.clear();
        cand_frame = frame;
    }
    for (auto& c : cands)   // (one target drawn into by several passes: their draws together)
        if (c.image == depth->image && c.id == depth->id) { c.draws += draws; return; }
    Cand c;
    c.image = depth->image, c.format = depth->format, c.aspect = depth->aspect, c.id = depth->id;
    c.w = width, c.h = height, c.draws = draws;
    if (cands.size() < 64) cands.push_back(c);
}

VkImageView dlss_find_depth(uint32_t w, uint32_t h, uint32_t* found_w, uint32_t* found_h) {
    Cand best;
    {
        std::lock_guard lk(cand_mu);
        const uint64_t frame = texture_frame_no();
        const std::vector<Cand>* lists[2] = {frame == cand_frame ? &cands : nullptr,
                                             frame == cand_frame ? &cands_prev : frame == cand_frame + 1 ? &cands : nullptr};
        for (int pass = 0; pass < 2 && !best.image; pass++)       // (this present's first, then the last one's)
            for (int shape = 0; shape < 2 && !best.image; shape++)   // (the render size, then its shape)
                if (lists[pass])
                    for (const Cand& c : *lists[pass]) {
                        const bool ok = shape == 0 ? c.w == w && c.h == h
                                                   : c.w >= w / 2 && std::fabs((double)c.w * h - (double)c.h * w) <= 0.01 * (double)c.h * w;
                        if (ok && c.draws > best.draws) best = c;
                    }
    }
    if (!best.image) return VK_NULL_HANDLE;
    if (found_w) *found_w = best.w;
    if (found_h) *found_h = best.h;
    return depth_view_of(best);
}

bool dlss_run(VkCommandBuffer cmd, const DlssInputs& in) {
    std::lock_guard lk(mu);
    if (!dlss_wanted() || !in.color_view || !in.out_view || in.render_w < 16 || in.render_h < 16 || in.out_w < 32 ||
        in.out_h < 32)
        return false;
    if (state == State::Untried) {
        if (!ngx.dll || dev.props.vendorID != 0x10DE) {
            state = ngx.dll ? State::NotNvidia : State::NoDll;
            logf("dlss: %s: the game's own upscaler stays",
                 ngx.dll ? "not an NVIDIA GPU" : "hookshot_ngx.dll is not beside the renderer");
            return false;
        }
        const std::string dir = own_dir();
        std::wstring wdir(dir.begin(), dir.end());   // (the game folder's path; non-ASCII names: NGX gets none, its default)
        bool ascii = true;
        for (char ch : dir) ascii &= (unsigned char)ch < 0x80;
        if (!wdir.empty() && (wdir.back() == L'\\' || wdir.back() == L'/')) wdir.pop_back();
        const int r = ngx.init((void*)dev.instance, (void*)dev.physical, (void*)dev.device, (void*)instance_proc(),
                               (void*)vkGetDeviceProcAddr, ascii && !wdir.empty() ? wdir.c_str() : nullptr, ngx_log,
                               getenv("RX_DLSS_NGX_LOG") ? 1 : 0);
        if (r != 1) {
            state = State::Unsupported;
            logf("dlss: NGX did not start (%d): the game's own upscaler stays", r);
            return false;
        }
        if (!make_passes()) {
            state = State::Failed;
            return false;
        }
        state = State::Running;
        logf("dlss: running");
    }
    collect();
    const int motion = motion_choice();   // 0 camera, 1 camera with Y flipped, 2 none
    const bool have_mv = in.mv_view && motion != 2;   // (a motion texture named by hand, RX_DLSS_MV)
    const bool camera = !have_mv && motion != 2 && in.depth_view && in.camera;
    missing = motion == 2 || have_mv ? 0 : (in.depth_view ? 0 : 1) | (in.camera ? 0 : 2);
    if (!have_mv && !camera && motion != 2) {
        // (without motion DLSS smears whatever moves: it runs only when the motion row says None)
        if (!no_motion_seen.exchange(true))
            logf("dlss: %s: the game's own upscaler stays (DLSS motion: None runs DLSS without motion)",
                 !in.depth_view && !in.camera ? "no scene depth and no camera found"
                 : !in.depth_view            ? "no scene depth found for the render size"
                                             : "no camera (view-projection) found in the TAAU's constants");
        return false;
    }
    const uint32_t rw = in.render_w, rh = in.render_h, ow = in.out_w, oh = in.out_h;
    static const bool inverted = getenv("RX_DLSS_DEPTH_INVERTED") && atoi(getenv("RX_DLSS_DEPTH_INVERTED"));
    const int flags = (1 << 0) | (1 << 1) | (1 << 6) | (inverted ? 1 << 3 : 0);   // IsHDR | MVLowRes | AutoExposure | DepthInverted
    const int preset = preset_of(choice());
    if (!ensure(cmd, run.color, VK_FORMAT_R16G16B16A16_SFLOAT, rw, rh) || !ensure(cmd, run.mv, VK_FORMAT_R16G16_SFLOAT, rw, rh) ||
        !ensure(cmd, run.depth, VK_FORMAT_R32_SFLOAT, rw, rh) || !ensure(cmd, run.output, VK_FORMAT_R16G16B16A16_SFLOAT, ow, oh)) {
        logf("dlss: its images (%ux%u, %ux%u) were not made", rw, rh, ow, oh);
        return false;
    }
    bool reset = false;
    if (!run.feature || run.rw != rw || run.rh != rh || run.ow != ow || run.oh != oh || run.preset != preset || run.flags != flags ||
        run.quality != quality_of(rw, ow)) {
        if (run.feature) {
            Image none;
            retire(none, run.feature);
        }
        run.feature = ngx.create((void*)cmd, rw, rh, ow, oh, quality_of(rw, ow), preset, flags);
        run.rw = rw, run.rh = rh, run.ow = ow, run.oh = oh, run.preset = preset, run.flags = flags, run.quality = quality_of(rw, ow);
        if (!run.feature) {
            if (++run.failures >= 3) {
                state = State::Failed;
                logf("dlss: DLSS could not be made three times: off until the next start");
            }
            return false;
        }
        reset = true;
    }
    {   // what the quality row asked and what this network's input actually is, on every change
        static const char* names[] = {"Auto (from Resolution)", "Quality", "Balanced", "Performance", "Ultra Performance", "DLAA"};
        const int q = opts.dlss_quality >= 0 && opts.dlss_quality <= 5 ? opts.dlss_quality : 0;
        const int k = dlss_quality_twelfths();
        int twelfths = k;
        if (!twelfths && !resolution_auto()) {
            const int s = resolution_scale();
            if (s > 0) twelfths = std::clamp((int)(s * 12.0 / 10000.0 + 0.5), 1, 12);
        }
        char asked[96], line[400];
        if (twelfths) {
            const uint32_t ew = (ow * (uint32_t)twelfths + 11) / 12, eh = oh * (uint32_t)twelfths / 12;
            snprintf(asked, sizeof asked, "%ux%u (%d/12 of the output)", ew, eh, twelfths);
        } else if (resolution_auto()) snprintf(asked, sizeof asked, "the game's own size (Resolution Auto: dynamic)");
        else snprintf(asked, sizeof asked, "the game's own size (by the Resolution row)");
        snprintf(line, sizeof line, "dlss: quality %s%s: the 3D is drawn at %s; DLSS input %ux%u (the TAAU network's own input) -> "
                 "%ux%u, NGX mode %d, preset %d", names[q], k || !twelfths ? "" : " (by the Resolution row)", asked, rw, rh, ow, oh,
                 run.quality, preset);
        if (run.said != line) {
            run.said = line;
            logf("%s", line);
        }
        input_w = rw, input_h = rh;
    }
    const uint64_t frame = texture_frame_no();
    if (!run.last_frame || frame - run.last_frame > 3 || run.motion != motion) reset = true;   // (a pause, a menu, a new motion)
    // the camera's reprojection: current NDC (GL, z -1..1) to the last frame's, prev_vp * inverse(vp)
    float reproj[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};   // (column-major, for the shader)
    if (in.camera) {
        double vp[16], inv[16];
        for (int i = 0; i < 16; i++) vp[i] = in.view_proj[i];
        const bool have_prev = run.prev_vp_frame && frame - run.prev_vp_frame <= 3 && !reset;
        if (camera && have_prev && invert4(vp, inv)) {
            double m[16];   // (row-major)
            for (int r = 0; r < 4; r++)
                for (int c = 0; c < 4; c++) {
                    double v = 0;
                    for (int k = 0; k < 4; k++) v += run.prev_vp[r * 4 + k] * inv[k * 4 + c];
                    m[r * 4 + c] = v;
                }
            bool finite = true;
            for (double v : m) finite &= std::isfinite(v);
            if (finite)
                for (int r = 0; r < 4; r++)
                    for (int c = 0; c < 4; c++) reproj[c * 4 + r] = (float)m[r * 4 + c];
        } else if (camera && !have_prev) reset = true;
        memcpy(run.prev_vp, vp, sizeof vp);
        run.prev_vp_frame = frame;
    }
    run.last_frame = frame, run.motion = motion;
    const auto now = std::chrono::steady_clock::now();
    float frame_ms = run.last_time.time_since_epoch().count()
                         ? (float)std::chrono::duration<double, std::milli>(now - run.last_time).count() : 16.7f;
    run.last_time = now;
    if (frame_ms <= 0.0f || frame_ms > 250.0f) frame_ms = 16.7f;

    // 1. the inputs, made what NGX takes
    struct { int32_t w, h; float sx, sy; int32_t flags; float far_depth, pad0, pad1; float reproj[16]; } pp{};
    pp.w = (int32_t)rw, pp.h = (int32_t)rh;
    if (have_mv) mv_texture_scale(rw, rh, pp.sx, pp.sy);
    if (in.mv_unorm) pp.flags |= 8;
    if (camera) pp.flags |= 16 | (motion == 1 ? 32 : 0) | (inverted ? 64 : 0);
    memcpy(pp.reproj, reproj, sizeof reproj);
    static const bool no_sqrt = getenv("RX_DLSS_NO_SQRT") != nullptr;
    if (!no_sqrt) pp.flags |= 1;   // (the TAAU colour is the square root of the linear colour, as taau_upscale.comp reads it)
    pp.flags |= (have_mv ? 2 : 0) | (in.depth_view ? 4 : 0);
    pp.far_depth = inverted ? 0.0f : 1.0f;
    const VkDescriptorImageInfo ii[6] = {
        {linear, in.color_view, VK_IMAGE_LAYOUT_GENERAL},
        {nearest, have_mv ? in.mv_view : in.color_view, VK_IMAGE_LAYOUT_GENERAL},
        {nearest, in.depth_view ? in.depth_view : in.color_view, VK_IMAGE_LAYOUT_GENERAL},
        {VK_NULL_HANDLE, run.color.view, VK_IMAGE_LAYOUT_GENERAL},
        {VK_NULL_HANDLE, run.mv.view, VK_IMAGE_LAYOUT_GENERAL},
        {VK_NULL_HANDLE, run.depth.view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet w[6];
    for (uint32_t i = 0; i < 6; i++) {
        w[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w[i].dstBinding = i, w[i].descriptorCount = 1;
        w[i].descriptorType = i < 3 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        w[i].pImageInfo = &ii[i];
    }
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prep.pipe);
    vkCmdPushDescriptorSetKHR(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, prep.layout, 0, 6, w);
    vkCmdPushConstants(cmd, prep.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pp, &pp);
    vkCmdDispatch(cmd, (rw + 7) / 8, (rh + 7) / 8, 1);
    barrier(cmd);

    // 2. DLSS
    float jx = 0.0f, jy = 0.0f;
    if (const char* e = getenv("RX_DLSS_JITTER")) sscanf(e, "%f,%f", &jx, &jy);
    const HsNgxImage c = ngx_image(run.color), d = ngx_image(run.depth), m = ngx_image(run.mv), o = ngx_image(run.output);
    const int r = ngx.evaluate((void*)cmd, run.feature, &c, &d, &m, &o, jx, jy, 1.0f, 1.0f, reset ? 1 : 0, frame_ms);
    if (r != 0) {
        logf("dlss: evaluate failed (%#x)%s", (unsigned)r, run.failures + 1 >= 3 ? ": off until the next start" : "");
        if (++run.failures >= 3) state = State::Failed;
        return false;   // (what the prep pass wrote is DLSS's own: the network runs as before)
    }
    barrier(cmd);

    // 3. into the network's output
    const VkDescriptorImageInfo oi[2] = {{linear, run.output.view, VK_IMAGE_LAYOUT_GENERAL},
                                         {VK_NULL_HANDLE, in.out_view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet ow2[2];
    for (uint32_t i = 0; i < 2; i++) {
        ow2[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        ow2[i].dstBinding = i, ow2[i].descriptorCount = 1;
        ow2[i].descriptorType = i == 0 ? VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER : VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
        ow2[i].pImageInfo = &oi[i];
    }
    const int32_t os[2] = {(int32_t)ow, (int32_t)oh};
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, out_pass.pipe);
    vkCmdPushDescriptorSetKHR(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, out_pass.layout, 0, 2, ow2);
    vkCmdPushConstants(cmd, out_pass.layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof os, os);
    vkCmdDispatch(cmd, (ow + 7) / 8, (oh + 7) / 8, 1);
    run.failures = 0;
    static std::atomic<int> told{0};
    if (told++ < 3 || reset)
        if (told < 40)
            logf("dlss: frame %llu: %ux%u -> %ux%u, preset %d, motion %s%s%s",
                 (unsigned long long)frame, rw, rh, ow, oh, preset,
                 have_mv ? "texture" : camera ? (motion == 1 ? "camera (Y flipped)" : "camera") : "none",
                 in.depth_view ? "" : ", no depth", reset ? ", reset" : "");
    return true;
}

} // namespace mkwvk
