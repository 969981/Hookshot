// (hookshot) The shared NGX session: gpu/vk/vk_ngx.h says why there is only one.
#if defined(RX_VK_DLSS_SDK)

#include "vk_ngx.h"

#include <cstdlib>
#include <cstring>

#include "vk_loader.h"

namespace mkwvk::ngx {
namespace {

/// The entry points the backends call, resolved by name from the NGX core the NVIDIA driver ships
/// (`libnvidia-ngx.so`), through the loader pattern the rest of the renderer uses for vulkan-1.dll.
///
/// Resolved rather than linked for two reasons: the SDK's own library is then not a link requirement
/// of a gated build, and a machine with the SDK-built binary but no NVIDIA driver has every pointer
/// null here, so every backend reports itself unavailable instead of the process failing to start.
struct Session {
    HMODULE module = nullptr;
    decltype(&NVSDK_NGX_VULKAN_Init_with_ProjectID) init = nullptr;
    decltype(&NVSDK_NGX_VULKAN_Shutdown1) shutdown = nullptr;
    decltype(&NVSDK_NGX_VULKAN_GetCapabilityParameters) capability = nullptr;
    decltype(&NVSDK_NGX_VULKAN_AllocateParameters) allocate = nullptr;
    decltype(&NVSDK_NGX_VULKAN_DestroyParameters) destroy = nullptr;
    decltype(&NVSDK_NGX_VULKAN_CreateFeature) create = nullptr;
    decltype(&NVSDK_NGX_VULKAN_EvaluateFeature) evaluate = nullptr;
    decltype(&NVSDK_NGX_VULKAN_ReleaseFeature) release = nullptr;
    /// The two discovery calls, which answer before a device exists. Optional: an NGX without them
    /// is one whose features need nothing beyond the core, and refusing to load over a missing
    /// discovery call would turn a working configuration into an unavailable one.
    decltype(&NVSDK_NGX_VULKAN_GetFeatureInstanceExtensionRequirements) instance_exts = nullptr;
    decltype(&NVSDK_NGX_VULKAN_GetFeatureDeviceExtensionRequirements) device_exts = nullptr;
    bool started = false;
    bool load_failed = false;
};
Session s;

/// A project id of our own. NVIDIA's analytics-key form is for licensed titles; the project-id form
/// exists exactly so a project without one can initialize.
constexpr const char* kProjectId = "a6b7c0de-7a1f-4f1e-9d2a-hookshot-dlss";
constexpr const char* kEngineVersion = "1.0";

/// The game folder, wide, for NGX's application data path: where it writes what it keeps and where
/// it looks for the feature libraries (libnvidia-ngx-dlss.so.* and its siblings), which the player
/// supplies beside the binary and which are never ours to ship.
const wchar_t* data_path() {
    static wchar_t wide[MAX_PATH + 1];
    static const bool once = []() {
        char path[MAX_PATH + 1]{};
        const DWORD n = GetModuleFileNameA(nullptr, path, MAX_PATH);
        if (!n) {
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

} // namespace

bool load() {
    if (s.module) return true;
    if (s.load_failed) return false;
    // The core ships with the driver and is found on the usual search path; the model libraries sit
    // beside the binary and are NGX's to find, which is what the application data path is for.
#ifdef _WIN32
    static const char* const names[]{"nvngx.dll", "_nvngx.dll"};
#else
    static const char* const names[]{"libnvidia-ngx.so.1", "libnvidia-ngx.so"};
#endif
    for (const char* name : names) {
        s.module = LoadLibraryA(name);
        if (s.module) {
            logf("vk: ngx loaded from %s", name);
            break;
        }
    }
    if (!s.module) {
        logf("vk: ngx: no NGX runtime here (%s), so every DLSS feature is unavailable", names[0]);
        s.load_failed = true;
        return false;
    }
#define NGX_RESOLVE(field, name)                                                                   \
    s.field = (decltype(s.field))(void*)GetProcAddress(s.module, name);                            \
    if (!s.field) {                                                                                \
        logf("vk: ngx: the NGX runtime has no %s", name);                                          \
        s.load_failed = true;                                                                      \
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
    s.instance_exts = (decltype(s.instance_exts))(void*)GetProcAddress(
        s.module, "NVSDK_NGX_VULKAN_GetFeatureInstanceExtensionRequirements");
    s.device_exts = (decltype(s.device_exts))(void*)GetProcAddress(
        s.module, "NVSDK_NGX_VULKAN_GetFeatureDeviceExtensionRequirements");
    return true;
}

bool start() {
    if (!load()) return false;
    if (s.started) return true;
    NVSDK_NGX_FeatureCommonInfo common{};
    const wchar_t* paths[]{data_path()};
    common.PathListInfo.Path = paths;
    common.PathListInfo.Length = 1;
    const NVSDK_NGX_Result res =
        s.init(kProjectId, NVSDK_NGX_ENGINE_TYPE_CUSTOM, kEngineVersion, data_path(), dev.instance,
               dev.physical, dev.device, instance_proc(), vkGetDeviceProcAddr, &common,
               NVSDK_NGX_Version_API);
    if (NVSDK_NGX_FAILED(res)) {
        logf("vk: ngx did not initialize (0x%08x)", (unsigned)res);
        return false;
    }
    s.started = true;
    return true;
}

bool feature_available(const char* available_key, const char* needs_driver_key,
                       const char* major_key, const char* minor_key, const char* name) {
    if (!start()) return false;
    NVSDK_NGX_Parameter* caps = nullptr;
    if (NVSDK_NGX_FAILED(s.capability(&caps)) || !caps) {
        logf("vk: ngx gave no capability parameters");
        return false;
    }
    int available = 0;
    if (NVSDK_NGX_FAILED(caps->Get(available_key, &available)) || !available) {
        // What NGX says about it: an old driver and a feature that failed to initialize are
        // different reports, and a user with the first can act on it.
        int needs_driver = 0, major = 0, minor = 0;
        caps->Get(needs_driver_key, &needs_driver);
        caps->Get(major_key, &major);
        caps->Get(minor_key, &minor);
        if (needs_driver)
            logf("vk: ngx: this driver is too old for %s (%d.%d or newer wanted)", name, major,
                 minor);
        else
            logf("vk: ngx: this GPU and driver do not report %s", name);
        return false;
    }
    return true;
}

NVSDK_NGX_Parameter* allocate_params() {
    if (!start()) return nullptr;
    NVSDK_NGX_Parameter* params = nullptr;
    if (NVSDK_NGX_FAILED(s.allocate(&params))) {
        logf("vk: ngx gave no parameters to evaluate with");
        return nullptr;
    }
    return params;
}

void destroy_params(NVSDK_NGX_Parameter* params) {
    if (params && s.destroy) s.destroy(params);
}

NVSDK_NGX_Result create_feature(VkCommandBuffer cmd, NVSDK_NGX_Feature feature,
                                NVSDK_NGX_Parameter* params, NVSDK_NGX_Handle** out_handle) {
    if (!s.create) return NVSDK_NGX_Result_Fail;
    return s.create(cmd, feature, params, out_handle);
}

NVSDK_NGX_Result evaluate_feature(VkCommandBuffer cmd, NVSDK_NGX_Handle* handle,
                                  NVSDK_NGX_Parameter* params) {
    if (!s.evaluate) return NVSDK_NGX_Result_Fail;
    return s.evaluate(cmd, handle, params, nullptr);
}

void release_feature(NVSDK_NGX_Handle* handle) {
    if (handle && s.release) s.release(handle);
}

NVSDK_NGX_FeatureDiscoveryInfo discovery_info(NVSDK_NGX_Feature feature) {
    NVSDK_NGX_FeatureDiscoveryInfo info{};
    info.SDKVersion = NVSDK_NGX_Version_API;
    info.FeatureID = feature;
    info.Identifier.IdentifierType = NVSDK_NGX_Application_Identifier_Type_Project_Id;
    info.Identifier.v.ProjectDesc.ProjectId = kProjectId;
    info.Identifier.v.ProjectDesc.EngineType = NVSDK_NGX_ENGINE_TYPE_CUSTOM;
    info.Identifier.v.ProjectDesc.EngineVersion = kEngineVersion;
    info.ApplicationDataPath = data_path();
    return info;
}

size_t instance_extensions(NVSDK_NGX_Feature feature, const char** out, size_t max) {
    if (!load() || !s.instance_exts) return 0;
    const NVSDK_NGX_FeatureDiscoveryInfo info = discovery_info(feature);
    uint32_t n = 0;
    VkExtensionProperties* props = nullptr;
    if (NVSDK_NGX_FAILED(s.instance_exts(&info, &n, &props)) || !props) return 0;
    size_t kept = 0;
    for (uint32_t i = 0; i < n && kept < max; i++) out[kept++] = props[i].extensionName;
    return kept;
}

size_t device_extensions(VkInstance instance, VkPhysicalDevice gpu, NVSDK_NGX_Feature feature,
                         const char** out, size_t max) {
    if (!instance || !gpu || !load() || !s.device_exts) return 0;
    const NVSDK_NGX_FeatureDiscoveryInfo info = discovery_info(feature);
    uint32_t n = 0;
    VkExtensionProperties* props = nullptr;
    if (NVSDK_NGX_FAILED(s.device_exts(instance, gpu, &info, &n, &props)) || !props) return 0;
    size_t kept = 0;
    for (uint32_t i = 0; i < n && kept < max; i++) out[kept++] = props[i].extensionName;
    return kept;
}

NVSDK_NGX_Resource_VK resource(VkImageView view, VkImage image, VkFormat format, uint32_t width,
                               uint32_t height, bool read_write) {
    VkImageSubresourceRange range{};
    range.aspectMask =
        is_depth_format(format) ? VK_IMAGE_ASPECT_DEPTH_BIT : VK_IMAGE_ASPECT_COLOR_BIT;
    range.baseMipLevel = 0, range.levelCount = 1;
    range.baseArrayLayer = 0, range.layerCount = 1;
    return NVSDK_NGX_Create_ImageView_Resource_VK(view, image, range, format, width, height,
                                                  read_write);
}

void shutdown() {
    if (s.started && s.shutdown) s.shutdown(dev.device);
    s.started = false;
}

} // namespace mkwvk::ngx

#endif // RX_VK_DLSS_SDK
