// The tree-shadow mask's history (gpu/vk/vk_draw.cpp, mask_stabilize, RX_VK_MASK_BLEND): what it replaces or no longer needs is given back 120
// presents later, once. tests/test_mask_history_retire.py cuts mask_retire and mask_reap out of the source into CUT and builds this file
// around them with stand-ins for the Vulkan handles. No game, no GPU.
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <map>
#include <set>
#include <vector>

typedef uint64_t VkImage;
typedef uint64_t VkImageView;
typedef void* VmaAllocation;
static const VkImage VK_NULL_HANDLE = 0;
struct { int device = 0; } dev;
static std::set<VkImage> live_images;
static std::set<VkImageView> live_views;
static int double_free;
static uint64_t next_handle = 100;
void destroy_image(VkImage image, VmaAllocation) { double_free += !live_images.erase(image); }
void vkDestroyImageView(int, VkImageView view, const void*) { double_free += !live_views.erase(view); }
#include CUT

static int fails;
#define CHECK(name, cond) do { if (!(cond)) { std::printf("FAIL: %s (line %d)\n", name, __LINE__); fails++; } } while (0)

int main() {
    // (mask_stabilize's size change, as its wiring does it: retire the pair, make a new one)
    VkImage hist[2] = {};
    VmaAllocation mem[2] = {};
    VkImageView view[2] = {};
    uint64_t frame = 1;
    for (int size_change = 0; size_change < 40; size_change++) {
        for (int i = 0; i < 2; i++) mask_retire(hist[i], mem[i], view[i], frame);
        for (int i = 0; i < 2; i++) {
            hist[i] = next_handle++, view[i] = next_handle++;
            live_images.insert(hist[i]), live_views.insert(view[i]);
        }
        for (int f = 0; f < 30; f++) mask_reap(++frame);   // (a size change every 30 presents)
        CHECK("at most the live pair and the pairs of the last 120 presents are held", live_images.size() <= 2 * 6);
    }
    CHECK("an image or view nothing destroyed twice", double_free == 0);
    // (nothing goes early)
    frame += 500, mask_reap(frame);   // (what the loop left is given back first)
    VkImage a = next_handle++;
    VkImageView v = next_handle++;
    VmaAllocation m = nullptr;
    live_images.insert(a), live_views.insert(v);
    const size_t before = live_images.size();
    mask_retire(a, m, v, frame);
    CHECK("a retired pair's handles are cleared", !a && !v);
    for (int f = 0; f < 119; f++) mask_reap(frame + (uint64_t)f);
    CHECK("it stays 119 presents", live_images.size() == before);
    mask_reap(frame + 120);
    CHECK("and goes at the 120th", live_images.size() == before - 1);
    // (the views of textures that went, given back with no image: a thousand)
    for (int i = 0; i < 1000; i++) {
        VkImage none = VK_NULL_HANDLE;
        VmaAllocation no_mem = nullptr;
        VkImageView w = next_handle++;
        live_views.insert(w);
        mask_retire(none, no_mem, w, frame);
    }
    mask_reap(frame + 120);
    CHECK("a view given back with no image is destroyed", live_views.size() <= 2 + 1 + 1);
    CHECK("nothing is held once everything is reaped", mask_old.empty());
    if (!fails) std::puts("PASS: the mask's replaced images and views are given back once, 120 presents on");
    return fails ? 1 : 0;
}
