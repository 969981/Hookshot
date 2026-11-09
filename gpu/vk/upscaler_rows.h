/* The Insert menu's scaling rows (vk_menu.cpp), as plain functions: what the Render resolution row steps through for each
   Upscaler, and the names it shows for DLSS's modes. No Vulkan, no menu: tests/test_upscaler_rows.cpp checks them. */
#pragma once

namespace mkwvk::upscaler_rows {

/// The Upscaler row (opts.dlss, saved as dlss; RX_VK_DLSS and RX_VK_FSR over it): the game's own network, NVIDIA DLSS,
/// NVIDIA DLAA, AMD FSR 4, AMD FSR 4's native anti-aliasing (FSR_AA). Each engine has two: one from a smaller 3D, and one
/// with the 3D at full scale (anti-aliasing alone)
enum { GAME = 0, DLSS = 1, DLAA = 2, FSR = 3, FSR_AA = 4, kUpscalers = 5 };
inline bool is_dlss(int u) { return u == DLSS || u == DLAA; }
inline bool is_fsr(int u) { return u == FSR || u == FSR_AA; }
/// The 3D drawn at full scale, the engine anti-aliasing alone: the Render resolution row does not apply
inline bool full_scale(int u) { return u == DLAA || u == FSR_AA; }
/// From a smaller 3D, by an engine: the Render resolution row steps through its modes
inline bool scaled(int u) { return u == DLSS || u == FSR; }

/// opts.resolution: 0 Auto (the game's dynamic resolution), else a share of the output in percent. 1 to 4 are the values from
/// before (100, 75, 67, 50: saved options keep their meaning); 5 and 6 came with DLSS's Balanced and Ultra Performance. Each
/// is a twelfth of the output (vk_menu.cpp twelfths): 8/12, 7/12, 6/12 and 4/12 are DLSS's own ratios, and FSR's (1.5, 1.7,
/// 2 and 3 times)
constexpr int kPercent[] = {0, 100, 75, 67, 50, 58, 33};
constexpr int kValues = sizeof kPercent / sizeof kPercent[0];
inline int percent(int v) { return v > 0 && v < kValues ? kPercent[v] : 0; }

/// The steps the Render resolution row takes, best first after Auto. DLSS and FSR: their Quality, Balanced, Performance and
/// Ultra Performance; Auto too, since they follow the game's dynamic size (vk_dlss.cpp makes its feature again when the size
/// leaves the mode's range; FSR's context takes any size up to the output). DLAA, FSR's native AA: none (the 3D is drawn at
/// full size)
constexpr int kGameSteps[] = {0, 1, 2, 3, 4};
constexpr int kEngineSteps[] = {0, 3, 5, 4, 6};
inline const int* steps(int upscaler, int& n) {
    if (scaled(upscaler)) return n = sizeof kEngineSteps / sizeof kEngineSteps[0], kEngineSteps;
    return n = sizeof kGameSteps / sizeof kGameSteps[0], kGameSteps;
}

/// DLSS's and FSR's name for a Render resolution value (the same four modes): nullptr for Auto, or a share that is not one
/// of them
inline const char* mode_name(int v) {
    switch (percent(v)) {
    case 67: return "Quality";
    case 58: return "Balanced";
    case 50: return "Performance";
    case 33: return "Ultra Performance";
    default: return nullptr;
    }
}

/// The Render resolution row's next value from v (dir +1 Right, -1 Left), wrapping. A value that is not one of the upscaler's
/// steps (one saved with another upscaler) moves to its neighbour by size. DLAA, FSR's native AA: v, unchanged
inline int step(int upscaler, int v, int dir) {
    if (full_scale(upscaler)) return v;
    int n = 0;
    const int* s = steps(upscaler, n);
    for (int i = 0; i < n; i++)
        if (s[i] == v) return s[(i + (dir > 0 ? 1 : n - 1)) % n];
    // (not a step: where it would sit, Auto first and then larger shares first)
    const int p = percent(v);
    int pos = 1;
    while (pos < n && percent(s[pos]) > p) pos++;
    return dir > 0 ? s[pos % n] : s[pos - 1];
}

/// v as one of the upscaler's steps: itself when it is one, else the step nearest in size (the larger on a tie). Auto stays
/// Auto. DLAA, FSR's native AA: v, unchanged (its row does not apply; v comes back with the next upscaler)
inline int snap(int upscaler, int v) {
    if (full_scale(upscaler) || percent(v) == 0) return percent(v) == 0 ? 0 : v;
    int n = 0;
    const int* s = steps(upscaler, n);
    int best = v, best_d = 1000;
    for (int i = 0; i < n; i++) {
        if (s[i] == v) return v;
        if (!percent(s[i])) continue;
        const int d = percent(s[i]) > percent(v) ? percent(s[i]) - percent(v) : percent(v) - percent(s[i]);
        if (d < best_d || (d == best_d && percent(s[i]) > percent(best))) best = s[i], best_d = d;
    }
    return best;
}

/// The Upscaler row's next value from v (dir +1 / -1), wrapping over the choices there are: DLSS and DLAA only when
/// dlss_ok (a build with NVIDIA's SDK, on a GPU DLSS runs on), FSR 4 and its native AA only when fsr_ok (a build with FSR,
/// AMD's library found with an FSR 4 for this GPU). Without either it stays on the game's own
inline int step_upscaler(int v, int dir, bool dlss_ok, bool fsr_ok) {
    if (v < 0 || v >= kUpscalers) v = GAME;
    for (int i = 0; i < kUpscalers; i++) {
        v = (v + (dir > 0 ? 1 : kUpscalers - 1)) % kUpscalers;
        if (v == GAME || (is_dlss(v) && dlss_ok) || (is_fsr(v) && fsr_ok)) return v;
    }
    return GAME;
}

/// The DLSS model row (opts.dlss_model, saved as dlss_model): which of NVIDIA's networks DLSS and DLAA draw with, by its
/// preset (NVSDK_NGX_DLSS_Hint_Render_Preset_*). 0 the transformer, preset K (the default here, the one tested); 1 the second
/// generation transformer, preset M (NVIDIA's own pick for Performance); 2 the same generation as tuned for Ultra
/// Performance, preset L; 3 NVIDIA's default, preset 0 (the runtime picks per mode, and a runtime update can change it)
enum { MODEL_K = 0, MODEL_M = 1, MODEL_L = 2, MODEL_DEFAULT = 3, kModels = 4 };
inline unsigned model_preset(int model) {
    switch (model) {
    case MODEL_M: return 13;
    case MODEL_L: return 12;
    case MODEL_DEFAULT: return 0;
    default: return 11;   // (K, also for a value past the row)
    }
}
/// The preset DLSS is asked for: RX_VK_DLSS_PRESET over the row (env: its value, or nullptr), a letter (A = 1 ... Z = 26,
/// either case) or 0 for NVIDIA's default; anything else leaves the row's
inline bool preset_env(const char* env) {
    return env && ((*env >= 'A' && *env <= 'Z') || (*env >= 'a' && *env <= 'z') || (env[0] == '0' && !env[1]));
}
inline unsigned dlss_preset(int model, const char* env) {
    if (!preset_env(env)) return model_preset(model);
    if (*env == '0') return 0;
    return (unsigned)(*env >= 'a' ? *env - 'a' + 1 : *env - 'A' + 1);
}
/// A preset's letter for the log ('A' + preset - 1), or '0' for NVIDIA's default
inline char preset_letter(unsigned preset) { return preset ? (char)('A' + preset - 1) : '0'; }

/// The Upscaler row is there only in a build with DLSS or FSR (built: dlss_built() || fsr4_built()): elsewhere the game's
/// own is all there is, and the Render resolution row is the game's own's
inline bool upscaler_shown(bool built) { return built; }
/// Rows with nothing else to choose here (shown without arrows; Left and Right leave the saved value alone): the Upscaler
/// where neither engine can run (dlss_ok: dlss_why() == DLSS_OK; fsr_ok: fsr4_why() == FSR_OK), the Render resolution with
/// DLAA or FSR's native AA (the 3D at full size), the DLSS model while DLSS and DLAA are not drawing
inline bool upscaler_fixed(bool dlss_ok, bool fsr_ok) { return !dlss_ok && !fsr_ok; }
inline bool resolution_fixed(int upscaler) { return full_scale(upscaler); }
/// The DLSS model row: shown where the Upscaler is (a build with DLSS), with nothing to choose while the game's own upscales
inline bool model_shown(bool built) { return built; }
/// (env_set: RX_VK_DLSS_PRESET names a preset, which wins over the row for this run)
inline bool model_fixed(int upscaler, bool env_set) { return !is_dlss(upscaler) || env_set; }
/// The Window scaling filter does something only when the window's size is not the game's picture's (the Output resolution);
/// a size not known yet (0) counts as different
inline bool filter_needed(unsigned picture_w, unsigned picture_h, unsigned window_w, unsigned window_h) {
    return !picture_w || !window_w || picture_w != window_w || picture_h != window_h;
}

} // namespace mkwvk::upscaler_rows
