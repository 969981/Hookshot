// (hookshot DLSS) The interface of hookshot_ngx.dll (ngx/hookshot_ngx.cpp): NVIDIA's NGX SDK for Vulkan, loaded at run
// time by vk_dlss.cpp. Plain C: the DLL is built for the MSVC target and the renderer with llvm-mingw.
#pragma once
#include <stdint.h>
#include <wchar.h>

#define HSNGX_INTERFACE_VERSION 1
#if defined(HSNGX_BUILDING)
#define HSNGX_API __declspec(dllexport)
#else
#define HSNGX_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*HsNgxLog)(const char* line);

/// A Vulkan image as NGX is given it: the handles as integers, the format, the size, and the view's aspect
typedef struct HsNgxImage {
    uint64_t image, view;
    uint32_t format, width, height, aspect;
} HsNgxImage;

typedef int (*PFN_hsngx_interface_version)(void);
typedef int (*PFN_hsngx_required_extensions)(uint32_t* instance_count, const char*** instance_exts, uint32_t* device_count,
                                             const char*** device_exts);
/// 1: DLSS Super Resolution is available; below 0: not (the reason logged through log)
typedef int (*PFN_hsngx_init)(void* instance, void* physical, void* device, void* gipa, void* gdpa, const wchar_t* app_dir,
                              HsNgxLog log, int verbose);
typedef void* (*PFN_hsngx_create)(void* cmd, uint32_t render_w, uint32_t render_h, uint32_t out_w, uint32_t out_h, int quality,
                                  int preset, int flags);
/// 0: recorded; else NGX's result code
typedef int (*PFN_hsngx_evaluate)(void* cmd, void* handle, const HsNgxImage* color, const HsNgxImage* depth,
                                  const HsNgxImage* motion, const HsNgxImage* output, float jitter_x, float jitter_y,
                                  float mv_scale_x, float mv_scale_y, int reset, float frame_ms);
typedef void (*PFN_hsngx_release)(void* handle);
typedef void (*PFN_hsngx_shutdown)(void);
typedef unsigned long long (*PFN_hsngx_vram_bytes)(void);   // (build 7, optional)

#ifdef __cplusplus
}
#endif
