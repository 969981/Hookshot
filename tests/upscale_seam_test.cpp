// (hookshot) gpu/vk/upscale_seam.h without a game, a GPU or Vulkan: which backend takes a recognized upscale
// dispatch, and every reason a temporal backend is refused one. Each refusal here is a wrong picture rather than a
// missing feature, which is why they are tested one at a time. tests/test_upscale_seam.py compiles and runs this.
#include <cmath>
#include <cstdio>
#include <cstring>

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

static void check_text(Choice c, const EngineState& e, const char* want, const char* what) {
    char got[96];
    row_text(got, sizeof got, c, e);
    if (std::strcmp(got, want) != 0) {
        std::fprintf(stderr, "FAIL: %s: got \"%s\", wanted \"%s\"\n", what, got, want);
        ++failures;
    }
}

static void test_engine_choice() {
    EngineState none;   // (no FSR 4 library, no FSR 3.1 either)
    EngineState only3;
    only3.fsr3_usable = true;
    only3.fsr4_why = "no AMD FSR DLLs";
    EngineState both = only3;
    both.fsr4_usable = true;
    both.fsr4_why = "";
    EngineState failed = only3;
    failed.fsr4_failed = true;
    failed.fsr4_why = "FSR 4 failed";

    check(engine_for(Choice::Off, both) == Engine::None, "Off draws nothing");
    check(engine_for(Choice::Auto, both) == Engine::Fsr4, "Auto takes FSR 4.1 when it is usable");
    check(engine_for(Choice::Auto, only3) == Engine::Fsr3, "Auto falls back to FSR 3.1 without the library");
    check(engine_for(Choice::Auto, none) == Engine::None, "Auto leaves the game's upscale with neither");
    check(engine_for(Choice::Fsr4, only3) == Engine::None, "a forced FSR 4.1 without the library is not FSR 3.1");
    check(engine_for(Choice::Fsr4, failed) == Engine::Fsr3, "FSR 4.1 failing in play falls back to FSR 3.1");
    check(engine_for(Choice::Fsr3, both) == Engine::Fsr3, "a forced FSR 3.1 ignores FSR 4.1");
    check(engine_for(Choice::Fsr3, none) == Engine::None, "a forced FSR 3.1 needs FSR 3.1");
    {
        EngineState dlss = both;
        dlss.dlss_on = true;
        check(engine_for(Choice::Auto, dlss) == Engine::None, "DLSS on leaves the choice waiting");
        check_text(Choice::Auto, dlss, "Auto (DLSS is on)", "DLSS on says so");
    }
    check_text(Choice::Off, both, "Off", "off");
    check_text(Choice::Auto, both, "Auto (FSR 4.1)", "auto with FSR 4.1");
    check_text(Choice::Auto, only3, "Auto (FSR 3.1)", "auto with FSR 3.1");
    check_text(Choice::Auto, none, "Auto (the game's upscale)", "auto with neither");
    check_text(Choice::Fsr4, both, "FSR 4.1", "forced FSR 4.1");
    check_text(Choice::Fsr4, only3, "FSR 4.1 (no AMD FSR DLLs)", "forced FSR 4.1 says why not");
    check_text(Choice::Fsr4, failed, "FSR 4.1 (failed: using FSR 3.1)", "forced FSR 4.1 failed");
    check_text(Choice::Fsr3, only3, "FSR 3.1", "forced FSR 3.1");
    {
        EngineState no3;
        no3.fsr3_why = "no push descriptors";
        check_text(Choice::Fsr3, no3, "FSR 3.1 (no push descriptors)", "forced FSR 3.1 says why not");
    }
    {
        EngineState pending = only3;
        pending.fsr4_pending = true;
        check_text(Choice::Auto, pending, "Auto", "the menu says nothing before the first frame");
    }
}

static void test_camera() {
    const float n = 0.5f, f = 2000.0f, tan_half = 0.57735f;   // (a 60 degree vertical view, an aspect of 16:9)
    const float A = -(f + n) / (f - n), B = -2.0f * f * n / (f - n);
    const float view[16] = {1, 0, 0, 3,  0, 0.8f, -0.6f, 2,  0, 0.6f, 0.8f, -5,  0, 0, 0, 1};   // (a rotation about x and a translation)
    const float cx = (9.0f / 16.0f) / tan_half, cy = 1.0f / tan_half;
    const float proj[16] = {cx, 0, 0, 0,  0, cy, 0, 0,  0, 0, A, B,  0, 0, -1, 0};
    float vp[16];
    for (int r = 0; r < 4; r++)
        for (int c = 0; c < 4; c++) {
            float sum = 0.0f;
            for (int k = 0; k < 4; k++) sum += proj[r * 4 + k] * view[k * 4 + c];
            vp[r * 4 + c] = sum;
        }
    const Camera cam = camera_from_view_projection(vp);
    check(cam.valid, "a game-style view projection is read");
    check(std::fabs(cam.near_plane - n) / n < 1e-3f, "its near plane");
    check(std::fabs(cam.far_plane - f) / f < 1e-2f, "its far plane");
    check(std::fabs(cam.tan_half_fov - tan_half) / tan_half < 1e-3f, "its tan(fovy / 2)");
    const float zero[16] = {};
    check(!camera_from_view_projection(zero).valid, "a zero matrix is not a camera");
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
    check(backend_name(Backend::StandIn)[0] == 's' && backend_name(Backend::None)[0] == 'n', "backends are named");
    test_engine_choice();
    test_camera();

    if (failures != 0) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("upscale seam: all checks passed\n");
    return 0;
}
