// The Insert menu (vk_menu.cpp): options that apply while the game runs, kept in hookshot_options.ini beside the executable.
#pragma once

#include "vk_internal.h"

namespace mkwvk {

// (out of the box every feature is off: what the game does on its own. The ini beside the executable keeps a player's choices)
struct Options {
    int framegen = 0;     // made frames: 0 off, or 2, 3, 4 times the game's frames
    int pace = 0;         // frames shown on an even line (vk_framegen.cpp)
    int resolution = 0;   // the 3D's: 0 Auto (the game's dynamic resolution), else 100%, 75%, 67%, 50% of its full scale (nx2/oot_patches.py)
    int output_res = 0;   // the game's whole frame, docked: 0 its 2048x1152, 1 2560x1440, 2 3840x2160, 3 1920x1080 (host/nn_applet.c)
    int upscale = 0;      // to the window: 0 off (the plain copy to the window), 1 Catmull-Rom, 2 Lanczos
    int sharpness = 0;    // 0 to 100
    int realtime = 1;     // game time follows the clock (host/nn_vi.c); 0: per frame (slower frames, slower game)
    int handheld = 0;     // the game's handheld profile: ~4 fps, softer; read once, at the start (host/nn_applet.c)
    int ultrawide = 0;    // the camera at the monitor's shape when it is wider than 16:9; read once, at the start (host/hos_main.c)
    int show_fps = 0;
    int fullscreen = 0;   // borderless, at the monitor's size: F11 is the same setting
    int gpu_device = 0;   // graphics card index: 0 Auto, 1..N device 0..N-1 (gpu/vk/vk_main.cpp; RX_VK_DEVICE)
    int pad_background = 0;   // the controllers count while another window has the focus (host/nn_hid.c; HOS_INPUT_BACKGROUND)
    int mouse_native = 0; // the mouse turns the camera's own angles (host/mouse_native.c); 0: as the right stick
    int mouse_sens = 100; // the mouse's sensitivity in percent: 0.1 degrees a count at 100 with native look (host/nn_hid.c)
    int mouse_invert = 0; // the mouse's up and down swapped
    int text_speed = 0;   // how fast dialogue prints: 0 the game's, 1 2x, 2 4x, 3 instant (host/nn_vi.c, nx2/oot_patches.py; HOS_TEXT_SPEED)
    int invert_cam_x = 0; // right-stick camera horizontal inversion: 0 normal, 1 inverted
    int invert_cam_y = 0; // right-stick camera vertical inversion: 0 normal, 1 inverted
    int rumble = 100;     // the controllers' rumble in percent of the game's: 0 Off, 25 to 150 (host/pad_sdl.c)
    int pad_layout = 0;   // 0 Nintendo (default), 1 Xbox / PC, 2 Swap Face Buttons, 3 Custom
    int gyro = 1;         // motion controls from a pad's gyroscope (host/pad_sdl.c, host/hos_motion.h); on by default
    int gyro_sens_x = 100;   // the motion aim's horizontal sensitivity in percent
    int gyro_sens_y = 100;   // the motion aim's vertical sensitivity in percent
    int gyro_inv_x = 0;   // the motion aim's horizontal turned around
    int gyro_inv_y = 0;   // the motion aim's vertical turned around
    int robust = 0;       // bounds-checked buffer access: 0 Auto (on), 1 Off, 2 On (gpu/vk/vk_main.cpp; RX_VK_ROBUST)
    int preset = 0;       // quality preset: 0 Custom, 1 Minimum (4GB), 2 Low (6GB), 3 Balanced (8GB), 4 High (12GB), 5 Ultra (16GB+)
    int vram_mb = 0;      // dynamic texture cache budget limit in MB
    int mirror_mb = 0;    // staging mirror limit in MB
    // (hookshot) DLSS 5 neural rendering (gpu/vk/vk_dlssnr.h). Off out of the box, like every other
    // feature here. The model's own knobs are percentages because that is what a slider can say; the
    // backend divides by 100 for the float Streamline wants.
    int nr = 0;                 // 0 off, 1 on
    int nr_model = 2;           // 0 Performance, 1 Balanced, 2 Quality, 3 DLAA
    int nr_style = 0;           // 0 Default, 1 Natural, 2 Cinematic
    int nr_preset = 0;          // 0 the plugin's default model, 1 to 7 pin presets A to G
    int nr_intensity = 100;     // how much of the uplift is mixed in, percent
    int nr_local_tone = 100;    // local tone strength, percent
    int nr_local_structure = 100;   // local structure strength, percent
    int nr_global_tone = 100;   // global tone strength, percent
    int nr_skin = 100;          // skin structure strength, percent (Auto mask decides where)
    int nr_auto_mask = 0;       // the model picks what to leave alone
    int mic = 1;          // (hookshot) the ocarina's microphone: 0 Off, 1 the system's default, 2.. a device as host->mic_name lists it (host/nn_audio_in.c)
};
extern Options opts;

// (hookshot) The Upscaling row carries two different things under one question a player can answer:
// 0-2 are filters over the finished picture, 3 and 4 are a backend resolving the 3D scene itself in
// the game's own upscaler's place. These two say which is meant, so no caller has to know the
// encoding -- and so a scene upscaler never reaches the window filter's shader as a mode it does
// not have.
inline int window_filter() {
    return opts.upscale >= 0 && opts.upscale <= 2 ? opts.upscale : 0;
}
/// 0 none (the game's own network), 1 FSR 3.1, 2 DLSS.
inline int scene_upscaler() {
    return opts.upscale >= 3 ? opts.upscale - 2 : 0;
}
/// (hookshot) DLSS 5 neural rendering's settings as the backend wants them, read live so that a
/// change in the menu reaches the next frame. The quality field is an sl::DLSSMode, which is what
/// DLSS-NR takes: 1 performance, 2 balanced, 3 quality, 6 DLAA.
struct NrSettings {
    bool on;
    unsigned quality;
    unsigned style;
    unsigned preset;
    float intensity, local_tone, local_structure, global_tone, skin;
    bool auto_mask;
    bool operator==(const NrSettings&) const = default;
};
inline NrSettings nr_settings() {
    const unsigned quality = opts.nr_model == 0 ? 1u : opts.nr_model == 1 ? 2u
                           : opts.nr_model == 3 ? 6u : 3u;
    return NrSettings{
        opts.nr != 0, quality, (unsigned)(opts.nr_style < 0 ? 0 : opts.nr_style),
        (unsigned)(opts.nr_preset < 0 ? 0 : opts.nr_preset),
        (float)opts.nr_intensity / 100.0f, (float)opts.nr_local_tone / 100.0f,
        (float)opts.nr_local_structure / 100.0f, (float)opts.nr_global_tone / 100.0f,
        (float)opts.nr_skin / 100.0f, opts.nr_auto_mask != 0,
    };
}

/// (vk_memory.cpp) true when the quality preset's memory budgets (vram_mb, mirror_mb) are to be used: the card has no more
/// video memory than the preset is for. A 4GB preset on an 8 GB card kept 256 MB of mirrors and 2.2 GB of textures: the
/// game's memory was read over PCIe and the textures made again every frame, 5 fps with the CPU idle (Discord, 11-08)
bool preset_caps_memory();

void menu_init();             // (the options from the file, once)
void menu_set_gpu_count(int count);
void menu_tick();             // (each frame shown: the keys, while the game's window has the focus)
bool menu_is_open();
bool resolution_auto();       // (the game's dynamic resolution: Resolution Auto)
int resolution_scale();       // (the fixed path's scale in 1/10000: the Resolution row's, as a twelfth of the output)
void menu_option_set(int id, int value);   // (from the game's side: 7 the mouse's sensitivity, the - and = keys)
int menu_heart_protection();
int menu_player_flight();
int menu_free_camera();
int menu_take_cheat_action();
void menu_note_frame(bool game_frame);   // (the frame rates the FPS counter shows)
/// Over the window's image (TRANSFER_DST_OPTIMAL), w x h: the menu and the FPS counter. slot: the frame in flight, 0 to 2.
void menu_record(VkCommandBuffer cmd, VkImage dst, int slot, uint32_t w, uint32_t h);
/// (hookshot) The start-up screen while the shaders are prepared (vk_main.cpp, vk_startup_paint.h), drawn by menu_record under the
/// counter and the menu. Null takes it away.
struct StartupView;
void startup_screen(const StartupView* view);

} // namespace mkwvk
