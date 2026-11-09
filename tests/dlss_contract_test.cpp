// (hookshot) What gpu/vk/vk_dlss.cpp promises when no NVIDIA SDK built it, driven without a GPU, a
// game or a DLSS runtime: the real translation unit, linked against the seven symbols it needs.
//
// That promise is the whole basis of the licensing decision in docs/upscaling.md -- the renderer
// compiles and links with no NVIDIA SDK present and runs with DLSS reported unavailable -- and it
// is the kind of thing that passes review by inspection and then stops being true. So it is a
// check rather than a comment.
//
// The checks that matter are the ones that would fail if nothing ran: in "on" mode `supported()`
// has to say *why* it is false, which it can only do by reaching the branch that looks for the
// SDK, and in "nodev" mode it has to say the earlier thing instead. A test that only asserted
// "false" would pass against a `supported()` that had been deleted. tests/test_dlss_contract.py
// compiles and runs this, once per mode.
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "vk_dlss.h"
#include "vk_menu.h"   // (Options: vk_dlss.cpp reads the menu where RX_VK_DLSS is not set)

// ---- the seven symbols gpu/vk/vk_dlss.cpp needs, and nothing else ----

namespace {
std::vector<std::string> log_lines;
int images_created = 0, images_destroyed = 0, barriers = 0;

bool said(const char* needle) {
    for (const std::string& line : log_lines)
        if (line.find(needle) != std::string::npos) return true;
    return false;
}
}   // namespace

namespace {
VKAPI_ATTR VkResult VKAPI_CALL stub_create_view(VkDevice, const VkImageViewCreateInfo*,
                                                const VkAllocationCallbacks*, VkImageView* out) {
    *out = (VkImageView)(uintptr_t)0x4000;
    return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL stub_destroy_view(VkDevice, VkImageView, const VkAllocationCallbacks*) {}
VKAPI_ATTR void VKAPI_CALL stub_barrier2(VkCommandBuffer, const VkDependencyInfo*) {
    barriers++;
}
}   // namespace

namespace vkx {
PFN_vkCreateImageView vkCreateImageView = stub_create_view;
PFN_vkDestroyImageView vkDestroyImageView = stub_destroy_view;
PFN_vkCmdPipelineBarrier2 vkCmdPipelineBarrier2 = stub_barrier2;
}   // namespace vkx

namespace mkwvk {
Device dev;
/// (hookshot) The Insert menu's options. vk_dlss.cpp's `wanted()` reads `opts.scene_upscaler` where
/// RX_VK_DLSS is not set, so the backend does not link without one. Default-constructed, which is
/// every feature off: the checks below are about what the backend does when it is *not* asked for,
/// and a stub that asked for it would be testing the opposite.
Options opts;

void logf(const char* fmt, ...) {
    char b[1024];
    std::va_list a;
    va_start(a, fmt);
    std::vsnprintf(b, sizeof b, fmt, a);
    va_end(a);
    log_lines.emplace_back(b);
}

bool create_image(const VkImageCreateInfo&, VkImage& image, VmaAllocation& allocation, bool) {
    images_created++;
    image = (VkImage)(uintptr_t)0x5000;
    allocation = (VmaAllocation)(uintptr_t)0x6000;
    return true;
}

void destroy_image(VkImage, VmaAllocation) {
    images_destroyed++;
}
}   // namespace mkwvk

// ---- the checks ----

static int failures = 0;

static void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

/// An input set with nothing missing: a 1536x864 scene resolved to 2048x1152, which is the shape
/// docs/upscaling.md measured in this game. Every field DLSS refuses without is filled, so a
/// refusal of this one is about DLSS being unavailable and nothing else.
static mkwvk::dlss::Inputs complete_inputs() {
    mkwvk::dlss::Inputs in;
    in.color = (VkImageView)(uintptr_t)0x11;
    in.depth = (VkImageView)(uintptr_t)0x12;
    in.motion = (VkImageView)(uintptr_t)0x13;
    in.color_image = (VkImage)(uintptr_t)0x21;
    in.depth_image = (VkImage)(uintptr_t)0x22;
    in.motion_image = (VkImage)(uintptr_t)0x23;
    in.color_format = VK_FORMAT_R16G16B16A16_SFLOAT;
    in.depth_format = VK_FORMAT_D32_SFLOAT;
    in.motion_format = VK_FORMAT_R16G16_SFLOAT;
    in.render_width = 1536, in.render_height = 864;
    in.upscale_width = 2048, in.upscale_height = 1152;
    in.motion_scale[0] = 1.0f, in.motion_scale[1] = 1.0f;
    in.near_plane = 1.0f;
    in.fov_y = 45.0f * 3.14159265358979f / 180.0f;
    return in;
}

/// Whatever the mode, none of this may allocate, record or hand back anything. A backend that is
/// unavailable and still makes its output image is a VRAM cost for a feature that cannot run.
static void nothing_was_made(const char* mode) {
    const VkCommandBuffer cmd = (VkCommandBuffer)(uintptr_t)0x1;
    check(!mkwvk::dlss::prepare(1536, 864, 2048, 1152), "prepare() refuses");
    check(!mkwvk::dlss::upscale(cmd, complete_inputs()), "upscale() refuses complete inputs");
    check(mkwvk::dlss::output_image() == VK_NULL_HANDLE, "there is no output image");
    check(mkwvk::dlss::output_view() == VK_NULL_HANDLE, "there is no output view");
    check(images_created == 0, "no image was allocated");
    check(barriers == 0, "nothing was recorded into the command buffer");
    mkwvk::dlss::shutdown();
    mkwvk::dlss::shutdown();   // (safe when nothing was made, and safe twice)
    check(images_destroyed == 0, "nothing was freed, because nothing was made");
    std::printf("dlss %s: %zu log line(s)\n", mode, log_lines.size());
    for (const std::string& line : log_lines) std::printf("  %s\n", line.c_str());
}

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "off";

    if (!std::strcmp(mode, "off")) {
        // RX_VK_DLSS is unset. The switch is neutral: nothing is loaded, nothing is looked at, and
        // nothing is said -- a renderer that logged about DLSS on every start would be a cost of
        // having the backend at all.
        check(getenv("RX_VK_DLSS") == nullptr, "the mode's environment is what it says");
        check(!mkwvk::dlss::supported(), "supported() is false with the switch off");
        check(log_lines.empty(), "nothing is logged with the switch off");
        nothing_was_made(mode);
    } else if (!std::strcmp(mode, "nodev")) {
        // Asked for before there is a device. This has to be the answer it gives, and not the one
        // below, or the "no SDK" line would be the only thing a user ever sees.
        check(getenv("RX_VK_DLSS") != nullptr, "the mode's environment is what it says");
        check(mkwvk::dev.device == VK_NULL_HANDLE, "no device in this mode");
        check(!mkwvk::dlss::supported(), "supported() is false before there is a device");
        check(said("no device"), "it says there is no device yet");
        check(!said("SDK"), "it does not blame the SDK for a missing device");
        nothing_was_made(mode);
    } else if (!std::strcmp(mode, "on")) {
        // Asked for, with a device, in a build with no NGX SDK. This is the case the licensing
        // decision rests on, and the log line is the proof that `supported()` reached the branch
        // that looks for one rather than refusing somewhere earlier.
        check(getenv("RX_VK_DLSS") != nullptr, "the mode's environment is what it says");
        mkwvk::dev.device = (VkDevice)(uintptr_t)0x3000;
        check(!mkwvk::dlss::supported(), "supported() is false in a build with no NGX SDK");
        check(said("NGX SDK"), "it says the build has no NGX SDK");
        check(said("RX_VK_DLSS_SDK"), "it names the gate, so the message is actionable");
        nothing_was_made(mode);
    } else {
        std::fprintf(stderr, "unknown mode %s\n", mode);
        return 2;
    }

    if (failures) std::fprintf(stderr, "%d check(s) failed in mode %s\n", failures, mode);
    return failures ? 1 : 0;
}
