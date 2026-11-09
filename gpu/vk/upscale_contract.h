// (hookshot) Whether the inputs a temporal upscaler needs appear among a game network's sampled textures: low
// resolution colour, motion and depth. Plain data, no Vulkan and nothing read from
// guest memory, so tests/upscale_contract_test.cpp can drive it; docs/upscaling.md has the contract itself and why
// the game's own upscale dispatch is where a replacement belongs.
#pragma once

#include <cstddef>
#include <cstdint>

namespace mkwvk::upscale {

/// What a binding's format makes it fit for. The renderer maps its VkFormats onto these where it builds the list, so
/// this header needs no Vulkan header and no renderer type.
enum class Kind : uint8_t {
    Other,       ///< a format the contract has no use for
    Colour,      ///< three or four channels, packed or float
    TwoChannel,  ///< two channels, float or signed: a motion candidate
    Depth,       ///< a depth format
};

/// What a binding was taken to be. Reported per binding so a log names them rather than leaving the reader to count.
enum class Role : uint8_t { None, Colour, Motion, Depth, FullSize };

struct Binding {
    Kind kind = Kind::Other;
    uint32_t width = 0;
    uint32_t height = 0;
};

inline constexpr size_t kNone = (size_t)-1;

struct Verdict {
    /// Colour, motion and depth were all found, and at one extent.
    bool inputs_complete = false;
    /// The extent the inputs agree on (0 when they do not agree, or were not all found).
    uint32_t render_width = 0;
    uint32_t render_height = 0;
    size_t colour = kNone;
    size_t motion = kNone;
    size_t depth = kNone;
    /// A colour larger than the render extent: the history, the output, or both. See classify().
    size_t full_size = kNone;
};

inline const char* role_name(Role r) {
    switch (r) {
    case Role::Colour: return "colour";
    case Role::Motion: return "motion";
    case Role::Depth: return "depth";
    case Role::FullSize: return "full-size";
    default: break;
    }
    return "-";
}

/// Reads `n` sampled bindings and says which of them a temporal upscaler would take for what. `roles`, when given,
/// receives one entry per binding.
///
/// Motion and depth decide the render extent, because they are the two a network cannot sample at any other size.
/// The colour at that extent is the upscaler's input.
///
/// A colour at a larger extent is reported as full-size and nothing more: it is the network's own history, its
/// output, or both, and the list this reads cannot tell them apart, because the renderer records the textures a
/// dispatch's descriptors sample *or store* in one list (vk_draw.cpp, draw_sampled). Naming it the history would be
/// a guess; the seam that replaces the dispatch resolves the output image itself and can then say which it is.
///
/// The first binding wins a tie, so the answer does not depend on how the caller happened to order equal
/// candidates.
inline Verdict classify(const Binding* b, size_t n, Role* roles = nullptr) {
    Verdict v;
    if (roles) {
        for (size_t i = 0; i < n; ++i) roles[i] = Role::None;
    }
    if (b == nullptr) return v;
    for (size_t i = 0; i < n; ++i) {
        if (b[i].width == 0 || b[i].height == 0) continue;
        if (b[i].kind == Kind::TwoChannel && v.motion == kNone) v.motion = i;
        if (b[i].kind == Kind::Depth && v.depth == kNone) v.depth = i;
    }
    // (both, and agreeing: one of them alone cannot say what the render extent is, and two that disagree mean the
    // guess about which binding is which is wrong)
    if (v.motion == kNone || v.depth == kNone || b[v.motion].width != b[v.depth].width ||
        b[v.motion].height != b[v.depth].height) {
        v.motion = v.depth = kNone;
        return v;
    }
    const uint32_t rw = b[v.motion].width, rh = b[v.motion].height;
    for (size_t i = 0; i < n; ++i) {
        if (b[i].kind != Kind::Colour || b[i].width == 0 || b[i].height == 0) continue;
        if (b[i].width == rw && b[i].height == rh) {
            if (v.colour == kNone) v.colour = i;
        } else if ((uint64_t)b[i].width * b[i].height > (uint64_t)rw * rh && v.full_size == kNone) {
            v.full_size = i;
        }
    }
    if (v.colour == kNone) {
        v.motion = v.depth = v.full_size = kNone;
        return v;
    }
    v.inputs_complete = true;
    v.render_width = rw, v.render_height = rh;
    if (roles) {
        roles[v.colour] = Role::Colour;
        roles[v.motion] = Role::Motion;
        roles[v.depth] = Role::Depth;
        if (v.full_size != kNone) roles[v.full_size] = Role::FullSize;
    }
    return v;
}

} // namespace mkwvk::upscale
