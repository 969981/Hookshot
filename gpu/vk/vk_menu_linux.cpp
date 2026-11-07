// Linux Vulkan menu fallback. The full menu uses Windows GDI to draw text into
// a bitmap; keep the runtime options available without requiring GDI.
#include "vk_menu.h"

namespace mkwvk {

Options opts;

void menu_init() {}
void menu_tick() {}
bool menu_is_open() { return false; }
void menu_note_frame(bool game_frame) { (void)game_frame; }
void menu_record(VkCommandBuffer cmd, VkImage dst, int slot, uint32_t w, uint32_t h) {
    (void)cmd; (void)dst; (void)slot; (void)w; (void)h;
}

} // namespace mkwvk
