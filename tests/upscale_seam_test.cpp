// (hookshot) gpu/vk/upscale_seam.h without a game, a GPU or Vulkan: which backend takes a recognized upscale
// dispatch, and every reason a temporal backend is refused one. Each refusal here is a wrong picture rather than a
// missing feature, which is why they are tested one at a time. tests/test_upscale_seam.py compiles and runs this.
#include <cstdio>

#include "upscale_seam.h"

using namespace mkwvk::upscale;

static int failures = 0;

static void check(bool ok, const char* what) {
    if (!ok) {
        std::fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    }
}

/// A dispatch whose inputs are complete at 1280x720 and whose output is a writable 1920x1080.
static Request good() {
    static const Binding b[] = {{Kind::Colour, 1280, 720},
                                {Kind::TwoChannel, 1280, 720},
                                {Kind::Depth, 1280, 720},
                                {Kind::Colour, 1920, 1080}};
    Request r;
    r.program_key = 0xfd029a317a9a4b94ull;
    r.inputs = classify(b, 4);
    r.colour_binding = r.inputs.colour;
    r.output_width = 1920, r.output_height = 1080;
    r.output_writable = true;
    return r;
}

int main() {
    check(good().inputs.inputs_complete, "the fixture's inputs are complete");
    check(temporal_possible(good()), "a resolved dispatch with complete inputs allows a temporal backend");
    check(temporal_refusal(good()) == nullptr, "and gives no refusal reason");

    // Native resolution is anti-aliasing, not an error: equal extents are allowed.
    {
        Request r = good();
        r.output_width = 1280, r.output_height = 720;
        check(temporal_possible(r), "an output at the render extent is allowed");
    }
    // Each refusal, one at a time, from the same otherwise-good dispatch.
    {
        Request r = good();
        r.inputs = Verdict{};
        check(!temporal_possible(r), "incomplete inputs are refused");
        check(temporal_refusal(r) != nullptr, "and say why");
    }
    {
        // The renderer hands over a different binding than the one the contract called the colour. Before this seam
        // the renderer simply used the first binding, so this is the case that has to be caught rather than assumed.
        Request r = good();
        r.colour_binding = r.inputs.colour + 1;
        check(!temporal_possible(r), "a bound colour that is not the contract's colour is refused");
        Request unbound = good();
        unbound.colour_binding = kNone;
        check(!temporal_possible(unbound), "no bound colour at all is refused");
    }
    {
        Request r = good();
        r.output_writable = false;
        check(!temporal_possible(r), "an output the renderer cannot write is refused");
    }
    {
        Request r = good();
        r.output_width = 0;
        check(!temporal_possible(r), "an unresolved output width is refused");
        Request h = good();
        h.output_height = 0;
        check(!temporal_possible(h), "an unresolved output height is refused");
    }
    {
        // An upscaler cannot resolve into something smaller than what it was handed.
        Request r = good();
        r.output_width = 640, r.output_height = 360;
        check(!temporal_possible(r), "an output below the render extent is refused");
        Request one = good();
        one.output_height = 719;
        check(!temporal_possible(one), "one axis below the render extent is enough to refuse");
    }

    // Backend choice. The stand-in is a spatial resolve: it must keep running wherever the key list asks for it,
    // including where the contract is not met, because that is what it did before the seam existed.
    {
        Availability a;
        a.push_descriptor = true, a.stand_in_wanted = true;
        check(choose(a, good()) == Backend::StandIn, "the stand-in takes a dispatch its key list asks for");
        Request bare;
        check(choose(a, bare) == Backend::StandIn, "the stand-in does not need the input contract");
        a.stand_in_wanted = false;
        check(choose(a, good()) == Backend::None, "nothing takes a dispatch no backend was asked for");
        a.stand_in_wanted = true, a.push_descriptor = false;
        check(choose(a, good()) == Backend::None, "without push descriptors nothing can be recorded");
    }

    // The vendor backends. Each one is a wrong picture rather than a worse one when the inputs are short, so
    // neither may be chosen where the contract is not met -- that is the difference between them and the stand-in.
    {
        Availability a;
        a.push_descriptor = true;
        a.fsr3 = true;
        check(choose(a, good()) == Backend::Fsr3, "FSR takes a dispatch whose inputs are complete");
        Request bare;
        check(choose(a, bare) == Backend::None, "FSR is not given a dispatch without the inputs");
        a.stand_in_wanted = true;
        check(choose(a, bare) == Backend::StandIn, "the stand-in still takes what FSR cannot");
        check(choose(a, good()) == Backend::Fsr3, "a vendor backend is preferred over the stand-in");
        a.dlss = true;
        check(choose(a, good()) == Backend::Dlss, "DLSS is preferred where the hardware has it");
        a.fsr3 = false;
        check(choose(a, good()) == Backend::Dlss, "DLSS does not need FSR to be available");
        a.push_descriptor = false;
        check(choose(a, good()) == Backend::None, "without push descriptors no backend can be recorded");
    }
    check(backend_name(Backend::Fsr3)[0] == 'F' && backend_name(Backend::Dlss)[0] == 'D',
          "the vendor backends are named");
    check(backend_name(Backend::StandIn)[0] == 's' && backend_name(Backend::None)[0] == 'n', "backends are named");

    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("upscale seam: all checks passed\n");
    return 0;
}
