// (hookshot) gpu/vk/upscale_contract.h and upscale_format.h, driven without a game or a GPU: what the classifier
// takes each sampled binding to be, the cases where it has to refuse rather than guess, and what each Vulkan format
// means to it. tests/test_upscale_contract.py compiles and runs this; it needs the Vulkan headers and nothing else.
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "upscale_format.h"

using namespace mkwvk::upscale;

static int failures = 0;

static void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

int main() {
    // A network that samples what a temporal upscaler needs: 1280x720 colour, motion and depth, with the previous
    // output or history at the full size.
    {
        const Binding b[] = {
            {Kind::Colour, 1280, 720},
            {Kind::TwoChannel, 1280, 720},
            {Kind::Depth, 1280, 720},
            {Kind::Colour, 1920, 1080},
        };
        Role roles[4];
        const Verdict v = classify(b, 4, roles);
        check(v.inputs_complete, "a complete input set is complete");
        check(v.render_width == 1280 && v.render_height == 720, "the render extent is motion's");
        check(v.colour == 0 && v.motion == 1 && v.depth == 2 && v.full_size == 3, "each input is found");
        check(roles[0] == Role::Colour && roles[1] == Role::Motion && roles[2] == Role::Depth &&
              roles[3] == Role::FullSize, "the roles are reported per binding");
    }
    // The order the caller happens to use must not change the answer.
    {
        const Binding b[] = {
            {Kind::Colour, 1920, 1080},
            {Kind::Depth, 1280, 720},
            {Kind::Colour, 1280, 720},
            {Kind::TwoChannel, 1280, 720},
        };
        const Verdict v = classify(b, 4);
        check(v.inputs_complete, "a reordered set is still complete");
        check(v.colour == 2 && v.motion == 3 && v.depth == 1 && v.full_size == 0, "order does not decide the roles");
    }
    // Native resolution: nothing is larger than the render extent, so there is no full-size colour, which is fine.
    {
        const Binding b[] = {{Kind::Colour, 1920, 1080}, {Kind::TwoChannel, 1920, 1080}, {Kind::Depth, 1920, 1080}};
        const Verdict v = classify(b, 3);
        check(v.inputs_complete, "no full-size colour is still a complete set");
        check(v.full_size == kNone, "and no full-size colour is reported");
    }
    // Each refusal below would otherwise become a wrong guess handed to an upscaler.
    {
        const Binding no_motion[] = {{Kind::Colour, 1280, 720}, {Kind::Depth, 1280, 720}};
        check(!classify(no_motion, 2).inputs_complete, "no motion is refused");
        const Binding no_depth[] = {{Kind::Colour, 1280, 720}, {Kind::TwoChannel, 1280, 720}};
        check(!classify(no_depth, 2).inputs_complete, "no depth is refused");
        const Binding no_colour[] = {{Kind::TwoChannel, 1280, 720}, {Kind::Depth, 1280, 720}};
        check(!classify(no_colour, 2).inputs_complete, "no colour at the render extent is refused");
        // Motion and depth at different sizes: one of them is not what it was taken for.
        const Binding mismatch[] = {
            {Kind::Colour, 1280, 720}, {Kind::TwoChannel, 1280, 720}, {Kind::Depth, 1920, 1080}};
        const Verdict v = classify(mismatch, 3);
        check(!v.inputs_complete, "motion and depth at different extents are refused");
        check(v.motion == kNone && v.depth == kNone, "and nothing is reported as found");
        // Only a larger colour: there is something full-size, but not the input it would upscale.
        const Binding full_size_only[] = {
            {Kind::Colour, 1920, 1080}, {Kind::TwoChannel, 1280, 720}, {Kind::Depth, 1280, 720}};
        check(!classify(full_size_only, 3).inputs_complete, "a full-size colour without an input colour is refused");
    }
    // A zero extent is a binding the renderer could not resolve; it must not become the render extent.
    {
        const Binding b[] = {{Kind::Colour, 1280, 720},
                             {Kind::TwoChannel, 0, 0},
                             {Kind::TwoChannel, 1280, 720},
                             {Kind::Depth, 1280, 720}};
        const Verdict v = classify(b, 4);
        check(v.inputs_complete && v.motion == 2, "an unresolved binding is skipped, not taken for motion");
    }
    // Nothing at all, and a null list: both are the stand-in's case, not a crash.
    check(!classify(nullptr, 0).inputs_complete, "a null list is refused");
    {
        const Binding b[] = {{Kind::Other, 64, 64}};
        Role roles[1];
        check(!classify(b, 1, roles).inputs_complete, "a set with nothing useful is refused");
        check(roles[0] == Role::None, "and its binding has no role");
    }
    // Roles are cleared even when the set is refused, so a caller cannot print last call's answer.
    {
        const Binding b[] = {{Kind::Colour, 1280, 720}, {Kind::Depth, 1280, 720}};
        Role roles[2] = {Role::Motion, Role::FullSize};
        classify(b, 2, roles);
        check(roles[0] == Role::None && roles[1] == Role::None, "a refusal leaves no roles set");
    }
    check(role_name(Role::Motion)[0] == 'm' && role_name(Role::None)[0] == '-', "role_name names them");

    // The format mapping, which is the half most likely to be wrong: a format wrongly called motion is handed to an
    // upscaler as motion.
    {
        check(kind_of(VK_FORMAT_D32_SFLOAT, VK_IMAGE_ASPECT_DEPTH_BIT) == Kind::Depth, "D32 depth is depth");
        check(kind_of(VK_FORMAT_D24_UNORM_S8_UINT, VK_IMAGE_ASPECT_DEPTH_BIT) == Kind::Depth,
              "a packed depth-stencil is depth");
        // The aspect decides, so a depth image the renderer made with a two-channel format is still depth and never
        // becomes a motion candidate.
        check(kind_of(VK_FORMAT_R16G16_SFLOAT, VK_IMAGE_ASPECT_DEPTH_BIT) == Kind::Depth, "the aspect wins");
        check(kind_of(VK_FORMAT_R16G16_SFLOAT, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::TwoChannel, "R16G16F is motion");
        check(kind_of(VK_FORMAT_R16G16_SNORM, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::TwoChannel, "R16G16 snorm is motion");
        check(kind_of(VK_FORMAT_R32G32_SFLOAT, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::TwoChannel, "R32G32F is motion");
        // OoT's own upscale output format.
        check(kind_of(VK_FORMAT_B10G11R11_UFLOAT_PACK32, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Colour,
              "B10G11R11 is colour");
        check(kind_of(VK_FORMAT_R16G16B16A16_SFLOAT, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Colour, "RGBA16F is colour");
        check(kind_of(VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Colour, "RGBA8 is colour");
        // One channel is not motion, however float it is, and an unknown format is a refusal rather than a default.
        check(kind_of(VK_FORMAT_R32_SFLOAT, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Other, "one channel is not motion");
        check(kind_of(VK_FORMAT_R8_UNORM, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Other, "R8 is nothing useful");
        check(kind_of(VK_FORMAT_UNDEFINED, 0) == Kind::Other, "an undefined format is nothing useful");
        check(kind_of(VK_FORMAT_BC7_UNORM_BLOCK, VK_IMAGE_ASPECT_COLOR_BIT) == Kind::Other,
              "a compressed texture is not an upscaler input");
    }

    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("upscale contract: all checks passed\n");
    return 0;
}
