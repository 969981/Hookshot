// (hookshot) Which FSR engine the Insert menu's upscaler row resolves to, and the camera FSR is told, read from the
// view projection. Plain data and no Vulkan, so tests/upscale_engine_test.cpp checks them without a game or a GPU.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>

namespace mkwvk::upscale {

/// The Insert menu's upscaler row. Values are what hookshot_options.ini's `upscaler=` keeps; 0 is the default.
enum class Choice : int { Off = 0, Auto = 1, Fsr4 = 2, Fsr3 = 3 };

/// What actually draws the upscale.
enum class Engine : uint8_t { None, Fsr4, Fsr3 };

/// What a choice is resolved against. The replay thread fills it (it may load AMD's library); the menu reads the same
/// state without loading anything, so `fsr4_pending` says "not looked for yet".
struct EngineState {
    bool dlss_on = false;
    bool fsr4_usable = false;   ///< loaded, with an FSR 4 model for this GPU, and nothing has failed
    bool fsr4_pending = false;  ///< not looked for yet: the menu only
    bool fsr4_failed = false;   ///< it started and then failed: FSR 3.1 takes over, whatever the choice was
    const char* fsr4_why = "";  ///< why not usable, short
    bool fsr3_usable = false;
    const char* fsr3_why = "";
};

/// The engine a choice resolves to. Auto: FSR 4.1 if it is usable, else FSR 3.1, else nothing (the game's own upscale).
/// A forced choice resolves only to itself, except FSR 4.1 failing in play, which falls back to FSR 3.1.
inline Engine engine_for(Choice c, const EngineState& s) {
    if (c == Choice::Off || s.dlss_on) return Engine::None;
    switch (c) {
    case Choice::Auto:
        return s.fsr4_usable ? Engine::Fsr4 : s.fsr3_usable ? Engine::Fsr3 : Engine::None;
    case Choice::Fsr4:
        return s.fsr4_usable ? Engine::Fsr4 : (s.fsr4_failed && s.fsr3_usable) ? Engine::Fsr3 : Engine::None;
    case Choice::Fsr3:
        return s.fsr3_usable ? Engine::Fsr3 : Engine::None;
    default: break;
    }
    return Engine::None;
}

inline const char* engine_name(Engine e) {
    switch (e) {
    case Engine::Fsr4: return "FSR 4.1";
    case Engine::Fsr3: return "FSR 3.1";
    default: break;
    }
    return "none";
}

/// The row's text: the engine in use, and why a forced choice is not running.
inline void row_text(char* out, size_t n, Choice c, const EngineState& s) {
    static const char* const names[] = {"Off", "Auto", "FSR 4.1", "FSR 3.1"};
    if (c == Choice::Off) {
        snprintf(out, n, "Off");
        return;
    }
    const char* const name = names[(int)c];
    if (s.dlss_on) {
        snprintf(out, n, "%s (DLSS is on)", name);
        return;
    }
    if (c == Choice::Auto) {
        if (s.fsr4_pending) snprintf(out, n, "Auto");
        else if (const Engine e = engine_for(c, s); e != Engine::None) snprintf(out, n, "Auto (%s)", engine_name(e));
        else snprintf(out, n, "Auto (the game's upscale)");
        return;
    }
    if (c == Choice::Fsr4) {
        if (s.fsr4_pending || s.fsr4_usable) snprintf(out, n, "FSR 4.1");
        else if (s.fsr4_failed && s.fsr3_usable) snprintf(out, n, "FSR 4.1 (failed: using FSR 3.1)");
        else snprintf(out, n, "FSR 4.1 (%s)", *s.fsr4_why ? s.fsr4_why : "unavailable");
        return;
    }
    if (s.fsr3_usable) snprintf(out, n, "FSR 3.1");
    else snprintf(out, n, "FSR 3.1 (%s)", *s.fsr3_why ? s.fsr3_why : "unavailable");
}

/// The near and far planes and tan(fovy / 2) a view-projection matrix was built with (row major, [-1, 1] clip depth, w = -view z:
/// how the game's own scene camera is laid out). Not valid when it is not such a matrix: a backend keeps its defaults.
struct Camera {
    float near_plane = 0.0f, far_plane = 0.0f, tan_half_fov = 0.0f;
    bool valid = false;
};
inline Camera camera_from_view_projection(const float* m) {
    Camera c;
    const float r3[3] = {m[12], m[13], m[14]};
    const float r2[3] = {m[8], m[9], m[10]};
    const float r1[3] = {m[4], m[5], m[6]};
    const float r3_len2 = r3[0] * r3[0] + r3[1] * r3[1] + r3[2] * r3[2];
    const float r1_len2 = r1[0] * r1[0] + r1[1] * r1[1] + r1[2] * r1[2];
    if (!(r3_len2 > 0.0f) || !(r1_len2 > 0.0f)) return c;
    const float a = -(r2[0] * r3[0] + r2[1] * r3[1] + r2[2] * r3[2]) / r3_len2;
    const float b = m[11] + a * m[15];
    const float near_plane = b / (a - 1.0f), far_plane = b / (a + 1.0f);
    const float tan_half = std::sqrt(r3_len2 / r1_len2);
    if (!std::isfinite(near_plane) || !std::isfinite(far_plane) || !std::isfinite(tan_half) || near_plane <= 0.0f ||
        far_plane <= near_plane || tan_half <= 0.0f || tan_half > 100.0f)
        return c;
    c.near_plane = near_plane, c.far_plane = far_plane, c.tan_half_fov = tan_half, c.valid = true;
    return c;
}

} // namespace mkwvk::upscale
