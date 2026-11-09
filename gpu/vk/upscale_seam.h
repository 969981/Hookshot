// (hookshot) The seam a vendor upscaler takes the game's own upscale dispatch through. The renderer resolves the
// dispatch once into a Request, asks which backend takes it, and runs that backend; a backend never re-derives the
// dispatch for itself. Plain data and no Vulkan, so tests/upscale_seam_test.cpp drives it; docs/upscaling.md has the
// contract and why this dispatch is the place.
#pragma once

#include <cstdint>

#include "upscale_contract.h"

namespace mkwvk::upscale {

/// What handles a recognized upscale dispatch.
enum class Backend : uint8_t {
    None,     ///< nothing does: the dispatch runs as itself
    StandIn,  ///< the compute resolve in shaders/taau_upscale.comp
    Fsr3,     ///< AMD FSR 3.1, recorded as our own compute passes (gpu/vk/vk_fsr3.h)
    Dlss,     ///< NVIDIA DLSS Super Resolution through NGX (gpu/vk/vk_dlss.h)
};

/// What the renderer can offer this dispatch. A backend that is not here cannot be chosen, whatever the inputs say.
struct Availability {
    /// Push descriptors, which every backend here records through.
    bool push_descriptor = false;
    /// The program key is in the stand-in's list (RX_VK_TAAU_KEYS), so the translated network is not to be trusted.
    bool stand_in_wanted = false;
    /// FSR 3.1 was asked for (RX_VK_FSR=1) and this device can run its passes.
    bool fsr3 = false;
    /// DLSS was asked for (RX_VK_DLSS=1), this build has an NGX SDK, and the driver reports Super Resolution.
    bool dlss = false;
};

/// A dispatch resolved once, in the terms a backend needs. Extents are in pixels; zero means the renderer could not
/// resolve that side, which is a refusal and never a default.
struct Request {
    uint64_t program_key = 0;
    /// What the sampled bindings provide (upscale_contract.h).
    Verdict inputs;
    /// The binding a backend will read as its colour input. The contract says which binding *is* the colour; this
    /// says which one the renderer is going to hand over. They have to be the same for a temporal backend to be
    /// given the dispatch, and historically the renderer has simply used the first.
    size_t colour_binding = kNone;
    /// The image the dispatch writes, as the renderer resolved it.
    uint32_t output_width = 0;
    uint32_t output_height = 0;
    /// The output's format is one a backend can write into.
    bool output_writable = false;
};

/// Whether a temporal backend could be given this dispatch.
///
/// Every clause is a refusal that would otherwise become a wrong picture rather than a missing feature:
///   - the inputs must be complete, so there is motion and depth to reproject with;
///   - the colour the renderer hands over must be the colour the contract identified, or the backend is upscaling
///     whichever texture happened to be bound first;
///   - the output must be resolved and writable;
///   - the output must be at least the render extent, since an upscaler cannot resolve into something smaller than
///     what it was given (equal is native resolution, which is a valid anti-aliasing case).
inline bool temporal_possible(const Request& r) {
    if (!r.inputs.inputs_complete) return false;
    if (r.colour_binding == kNone || r.colour_binding != r.inputs.colour) return false;
    if (!r.output_writable || r.output_width == 0 || r.output_height == 0) return false;
    if (r.output_width < r.inputs.render_width || r.output_height < r.inputs.render_height) return false;
    return true;
}

/// Why `temporal_possible` said no, for a log. Null when it said yes.
inline const char* temporal_refusal(const Request& r) {
    if (!r.inputs.inputs_complete) return "no colour, motion and depth at one extent";
    if (r.colour_binding == kNone || r.colour_binding != r.inputs.colour) return "the bound colour is not the input";
    if (!r.output_writable || r.output_width == 0 || r.output_height == 0) return "the output is not resolved";
    if (r.output_width < r.inputs.render_width || r.output_height < r.inputs.render_height)
        return "the output is smaller than the render extent";
    return nullptr;
}

/// Which backend takes this dispatch.
///
/// The stand-in does not need the contract: it is a spatial resolve, and it runs wherever the key list asks for it,
/// exactly as it did before this seam existed. A temporal backend will be preferred here once there is one, and only
/// where `temporal_possible` agrees.
inline Backend choose(const Availability& a, const Request& r) {
    if (!a.push_descriptor) return Backend::None;
    // A vendor upscaler only where the inputs are all there: both of them resolve from colour, motion and depth at
    // one extent, and neither degrades gracefully when handed less -- it produces a wrong picture instead.
    if (temporal_possible(r)) {
        // DLSS first where the hardware has it, because on that hardware it is the better of the two and its
        // availability already means an NVIDIA driver reporting Super Resolution; FSR runs anywhere.
        if (a.dlss) return Backend::Dlss;
        if (a.fsr3) return Backend::Fsr3;
    }
    if (a.stand_in_wanted) return Backend::StandIn;
    return Backend::None;
}

inline const char* backend_name(Backend b) {
    switch (b) {
    case Backend::StandIn: return "stand-in";
    case Backend::Fsr3: return "FSR 3.1";
    case Backend::Dlss: return "DLSS";
    default: break;
    }
    return "none";
}

} // namespace mkwvk::upscale
