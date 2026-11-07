// The Insert menu (vk_menu.cpp): options that apply while the game runs, kept in hookshot_options.ini beside the executable.
#pragma once

#include "vk_internal.h"

namespace mkwvk {

// (out of the box every feature is off: what the game does on its own. The ini beside the executable keeps a player's choices)
struct Options {
    int framegen = 0;     // made frames: 0 off, or 2, 3, 4 times the game's frames
    int pace = 0;         // frames shown on an even line (vk_framegen.cpp)
    int resolution = 0;   // the 3D's: 0 Auto (the game's dynamic resolution), else 100%, 75%, 67%, 50% of its full scale (nx2/oot_patches.py)
    int upscale = 0;      // to the window: 0 off (the plain copy to the window), 1 Catmull-Rom, 2 Lanczos
    int sharpness = 0;    // 0 to 100
    int realtime = 1;     // game time follows the clock (host/nn_vi.c); 0: per frame (slower frames, slower game)
    int handheld = 0;     // the game's handheld profile: ~4 fps, softer; read once, at the start (host/nn_applet.c)
    int ultrawide = 0;    // the camera at the monitor's shape when it is wider than 16:9; read once, at the start (host/hos_main.c)
    int show_fps = 0;
    int fullscreen = 0;   // borderless, at the monitor's size: F11 is the same setting
    int mouse_native = 1; // the mouse turns the camera's own angles (host/mouse_native.c); 0: as the right stick
    int mouse_sens = 100; // percent: 0.1 degrees a count at 100 (host/mouse_native.c)
    int mouse_invert = 0; // up and down swapped
    // (the controls: host/nn_hid.c reads them, rx_option 100 + action * 2 + slot and 200 + button)
    // keys: two a action, virtual-key codes (a mouse button's too; 0 none): the 16 buttons in host/nn_hid.c's B_* order
    // (A B X Y LS RS L R ZL ZR Plus Minus, d-pad left up right down), move up down left right, camera up down left right
    int keys[24][2] = {{'E', 0}, {VK_SHIFT, 0}, {VK_SPACE, 0}, {VK_LBUTTON, 0}, {VK_CONTROL, 0}, {'C', 0}, {'Q', 0}, {'R', 0},
                       {'Z', VK_RBUTTON}, {'X', 'F'}, {VK_TAB, VK_RETURN}, {'M', 0}, {'1', 0}, {'2', 0}, {'3', 0}, {'4', 0},
                       {'W', 0}, {'S', 0}, {'A', 0}, {'D', 0}, {VK_UP, 'I'}, {VK_DOWN, 'K'}, {VK_LEFT, 'J'}, {VK_RIGHT, 'L'}};
    // pads: the pad button (B_*, by position) that presses each of the game's 16 buttons; -1 none
    int pads[16] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
};
extern Options opts;

void menu_init();             // (the options from the file, once)
void menu_tick();             // (each frame shown: the keys, while the game's window has the focus)
bool menu_is_open();
void menu_pad(uint64_t buttons);              // (the controllers' buttons, from the game's threads: host/nn_hid.c)
void menu_option_set(int id, int value);      // (a setting from the game's side: 4 the mouse's sensitivity)
void menu_note_frame(bool game_frame);   // (the frame rates the FPS counter shows)
/// Over the window's image (TRANSFER_DST_OPTIMAL), w x h: the menu and the FPS counter. slot: the frame in flight, 0 to 2.
void menu_record(VkCommandBuffer cmd, VkImage dst, int slot, uint32_t w, uint32_t h);

} // namespace mkwvk
