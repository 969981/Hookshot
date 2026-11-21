"""A swapchain image the system has not given back holds no work on the replay's queue (#1376, #316, #1355).

AMD's driver on Windows hands an image back from vkAcquireNextImageKHR at once and leaves the wait for it to the acquire's
semaphore or fence. The copy into the image waited for that semaphore on the GPU, on the queue every submission of the
game's work goes to: with the game's fullscreen window behind a browser or OBS the system gave the image back late or never,
the queue finished nothing, and the GPU hang watch ended the game 30 s later ("no submission finished in 30 s, and the driver
never reported the device lost"). RX 9070 XT, Windows, 1 to 20 s after Alt+Tab.

present_image's acquire, from its image index to the frame's command pool reset, and its copy's submit info, from the end of
the copy's recording to the queue's lock, are cut out of gpu/vk/vk_replay.cpp (with the helpers before present_image that
they call, where there are any) and compiled against a model of a presentation engine that hands an image back before it
lets it go, as AMD's does. Each platform's side of the source's _WIN32 lines is compiled on its own. Checked:

- an image not let go yet: no submission that waits on the GPU for it (the bug), and no second acquire while it is held;
- the held image let go: the next present copies into that image, with no wait left on the GPU;
- the acquire's fence: unsignaled and not in use at each acquire (the Vulkan spec), also after the swapchain was made again
  while an image was held;
- a lost device while the image is waited for is reported, not taken as a timeout.

No GPU.

python -I -B tests/test_present_image_free.py [--cxx clang++]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (tests/srccut.py: python -I leaves this folder off the path)
from srccut import between, block   # noqa: E402

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def platform_side(text, windows):
    """The text with only one platform's side of its `#ifdef _WIN32` / `#ifndef _WIN32` lines (there are no others in the cut)"""
    out, stack = [], []   # (stack: whether each open conditional's current side is kept)
    for line in text.splitlines():
        s = line.strip()
        if s in ("#ifdef _WIN32", "#ifndef _WIN32"):
            stack.append(windows if s == "#ifdef _WIN32" else not windows)
        elif s == "#else" and stack:
            stack[-1] = not stack[-1]
        elif s == "#endif" and stack:
            stack.pop()
        elif s.startswith("#"):
            raise SystemExit("FAIL: the cut has a preprocessor line this check does not read: " + s)
        elif all(stack):
            out.append(line)
    return "\n".join(out) + "\n"


FIXTURE_HEAD = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <vector>
using VkResult = int;
using VkFence = uint64_t; using VkSemaphore = uint64_t; using VkSwapchainKHR = uint64_t; using VkCommandBuffer = uint64_t;
using VkCommandPool = uint64_t;
constexpr uint64_t VK_NULL_HANDLE = 0;
constexpr VkResult VK_SUCCESS = 0, VK_NOT_READY = 1, VK_TIMEOUT = 2, VK_ERROR_DEVICE_LOST = -4,
    VK_ERROR_SURFACE_LOST_KHR = -1000000000, VK_SUBOPTIMAL_KHR = 1000001003, VK_ERROR_OUT_OF_DATE_KHR = -1000001004;
constexpr int VK_TRUE = 1, VK_STRUCTURE_TYPE_FENCE_CREATE_INFO = 8, VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO = 1000314005,
    VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO = 1000314006, VK_STRUCTURE_TYPE_SUBMIT_INFO_2 = 1000314004;
constexpr uint64_t VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT = 0x10000;
struct VkFenceCreateInfo { int sType; const void* pNext = nullptr; int flags = 0; };
struct VkSemaphoreSubmitInfo { int sType; const void* pNext = nullptr; VkSemaphore semaphore = 0; uint64_t value = 0;
    uint64_t stageMask = 0; uint32_t deviceIndex = 0; };
struct VkCommandBufferSubmitInfo { int sType; const void* pNext = nullptr; VkCommandBuffer commandBuffer = 0; uint32_t deviceMask = 0; };
struct VkSubmitInfo2 { int sType; const void* pNext = nullptr; int flags = 0; uint32_t waitSemaphoreInfoCount = 0;
    const VkSemaphoreSubmitInfo* pWaitSemaphoreInfos = nullptr; uint32_t commandBufferInfoCount = 0;
    const VkCommandBufferSubmitInfo* pCommandBufferInfos = nullptr; uint32_t signalSemaphoreInfoCount = 0;
    const VkSemaphoreSubmitInfo* pSignalSemaphoreInfos = nullptr; };
struct Extent { uint32_t width = 1920, height = 1080; };
struct Device { int device = 1; VkSwapchainKHR swapchain = 1; uint64_t swapchain_generation = 0; Extent swap_extent;
    std::vector<VkSemaphore> swap_rendered{901, 902, 903, 904}; std::mutex present_mu; } dev;
struct Frame { VkCommandPool pool = 11; VkCommandBuffer cmd = 12; VkFence done = 13; VkSemaphore acquired = 14; };
namespace fps_stats { enum { kGpuWait, kPace }; }
namespace gpu_wait_stats { struct Exclude { Exclude() {} }; }   // (vk_work.h: a wait left out of the GPU wait counts)
void fps_wait(int, int64_t) {}
int64_t fps_clock() { return 0; }
template<class... T> void logf(const char*, T...) {}
void present_thread_drain() {}
bool invalidate_lost_surface(VkResult) { return true; }
bool create_swapchain(uint32_t, uint32_t) { return true; }
struct Lost {};
int lost_reports = 0;
[[noreturn]] void device_lost() { ++lost_reports; throw Lost{}; }

// ---- the presentation engine: an acquire hands an image back at once; its semaphore and fence are signaled when the engine
// lets it go (AMD's driver on Windows). `shown`: whether the engine lets images go (the window seen) ----
constexpr int kImages = 4;
bool shown = true;
int next_image = 0, acquires = 0, problems = 0;
bool fence_lost = false;   // (a wait on a fence reports the device lost)
struct Pending { int image = -1; VkSemaphore semaphore = 0; VkFence fence = 0; };
std::vector<Pending> pending;          // (images handed back, not let go yet)
std::vector<VkSemaphore> signaled_semaphores;
std::vector<VkFence> signaled_fences, in_use_fences, made_fences;
uint64_t next_fence = 500;
template<class T> bool has(const std::vector<T>& v, T x) { for (T y : v) if (y == x) return true; return false; }
template<class T> void drop(std::vector<T>& v, T x) { for (size_t i = 0; i < v.size(); i++) if (v[i] == x) { v.erase(v.begin() + i); return; } }
void problem(const char* what) { ++problems; std::printf("FAIL (%s): %s\n", PLATFORM, what); }
void let_go(const Pending& p) {
    if (p.semaphore) signaled_semaphores.push_back(p.semaphore);
    if (p.fence) { signaled_fences.push_back(p.fence); drop(in_use_fences, p.fence); }
}
void engine_lets_go() {   // (the window seen again: every image handed back is let go)
    for (const Pending& p : pending) let_go(p);
    pending.clear();
}
VkResult vkAcquireNextImageKHR(int, VkSwapchainKHR, uint64_t, VkSemaphore semaphore, VkFence fence, uint32_t* index) {
    ++acquires;
    if (!semaphore && !fence) problem("an acquire with neither a semaphore nor a fence");
    if (fence && (has(in_use_fences, fence) || has(signaled_fences, fence)))
        problem("the acquire's fence is signaled or still in use (VUID-vkAcquireNextImageKHR-fence-01287)");
    const Pending p{next_image, semaphore, fence};
    *index = (uint32_t)next_image;
    next_image = (next_image + 1) % kImages;
    if (shown) let_go(p);
    else {
        if (fence) in_use_fences.push_back(fence);
        pending.push_back(p);
    }
    return VK_SUCCESS;
}
VkResult vkWaitForFences(int, uint32_t n, const VkFence* fences, int, uint64_t) {
    if (fence_lost) return VK_ERROR_DEVICE_LOST;
    for (uint32_t i = 0; i < n; i++)
        if (fences[i] != 13 && !has(signaled_fences, fences[i])) return VK_TIMEOUT;   // (13: the frame slot's own, done)
    return VK_SUCCESS;
}
VkResult vkResetFences(int, uint32_t n, const VkFence* fences) {
    for (uint32_t i = 0; i < n; i++) {
        if (has(in_use_fences, fences[i])) problem("a fence reset while the presentation engine may still signal it");
        drop(signaled_fences, fences[i]);
    }
    return VK_SUCCESS;
}
VkResult vkCreateFence(int, const VkFenceCreateInfo*, void*, VkFence* out) { *out = ++next_fence; made_fences.push_back(*out); return VK_SUCCESS; }
VkResult vkEndCommandBuffer(VkCommandBuffer) { return VK_SUCCESS; }
'''

FIXTURE_TAIL = r'''
// ---- a present: the acquire, then (when it goes on) the copy's submission, read as the queue would run it ----
int submits = 0, copied_into = -1;
bool queue_held = false;   // (a submission waits on the GPU for a semaphore nothing has signaled: the replay's queue stops there)
void present(Frame& f) {
    const int64_t fence_t0 = 0;
    (void)fence_t0;
@@ACQUIRE@@
@@SUBMIT@@
    ++submits, copied_into = (int)index;
    for (uint32_t i = 0; i < si.waitSemaphoreInfoCount; i++) {
        const VkSemaphore s = si.pWaitSemaphoreInfos[i].semaphore;
        if (has(signaled_semaphores, s)) drop(signaled_semaphores, s);   // (a binary semaphore's wait takes its signal)
        else queue_held = true;
    }
}

int main() {
    Frame f;
    @@INIT@@
    // 1. the window seen: each present acquires once and copies, and nothing waits
    for (int i = 0; i < 6; i++) present(f);
    if (acquires != 6 || submits != 6 || queue_held) problem("a present with the window seen did not copy at once");
    // 2. the window behind another one: the engine keeps the next image
    shown = false;
    const int image = next_image;
    present(f);
    if (queue_held)
        problem("the copy waits on the GPU, on the replay's queue, for an image the system has not given back: every submission "
                "after it waits too, and the GPU hang watch ends the game (#1376)");
    if (submits != 6) problem("a copy was submitted into an image the system has not given back");
    if (!queue_held) {
        const int a = acquires;
        for (int i = 0; i < 5; i++) present(f);
        if (acquires != a) problem("another image acquired while one is held (the swapchain has only so many to spare)");
        if (submits != 6 || queue_held) problem("a held image was copied into before the system gave it back");
        // 3. the window seen again: the held image is the next one copied into
        shown = true;
        engine_lets_go();
        present(f);
        if (submits != 7 || copied_into != image || queue_held) problem("the held image was not copied into once given back");
        if (acquires != a) problem("an acquire before the held image was used");
        present(f);
        if (submits != 8 || acquires != a + 1 || queue_held) problem("the next present after the held image did not go on");
        // 4. the swapchain made again while an image is held: its fence may never be signaled; the next acquire takes another
        shown = false;
        present(f);
        dev.swapchain_generation++, dev.swapchain++, next_image = 0;
        pending.clear();   // (the old swapchain's images: never given back)
        shown = true;
        const int s = submits;
        present(f);
        if (submits != s + 1 || queue_held) problem("no copy after the swapchain was made again while an image was held");
        present(f);
        if (submits != s + 2) problem("the second present on the new swapchain did not copy");
        // 5. a lost device while the image is waited for: reported
        fence_lost = true;
        bool thrown = false;
        try { present(f); } catch (const Lost&) { thrown = true; }
        if (!thrown || lost_reports != 1) problem("a lost device in the image's wait was not reported");
        fence_lost = false;
    }
    if (problems) return 1;
    std::printf("PASS (%s): an image the system keeps holds no work on the queue; held, given back, copied; fence reuse and "
                "a swapchain made again; device loss reported\n", PLATFORM);
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    replay = (a.source / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    present = block(replay, "void present_image(VkImage src,")
    acquire = between(present, "    uint32_t index = 0;", "    if (vkResetCommandPool(dev.device, f.pool, 0)")
    submit = between(present, "    if (vkEndCommandBuffer(f.cmd) == VK_ERROR_DEVICE_LOST) device_lost();",
                     "    std::unique_lock qk(dev.present_mu);")
    # (the helpers the acquire calls, from the image's fence to present_image, where there are any)
    marker = "static VkFence acquire_fence = VK_NULL_HANDLE;"
    helpers = between(replay, marker, "void present_image(VkImage src,") if marker in replay else ""
    # (the acquire's own fence where the source makes one, as replay_start does)
    init = "if (vkCreateFence(dev.device, &fi, nullptr, &acquire_fence) != VK_SUCCESS) return 1;"
    init = ("{ VkFenceCreateInfo fi{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO}; %s }" % init
            if "&acquire_fence) != VK_SUCCESS) return false;" in replay else "")
    failed = 0
    with tempfile.TemporaryDirectory(prefix="present-image-free-") as temp:
        for name, windows in (("Windows", True), ("Linux", False)):
            body = (FIXTURE_TAIL.replace("@@INIT@@", init).replace("@@ACQUIRE@@", platform_side(acquire, windows))
                    .replace("@@SUBMIT@@", platform_side(submit, windows)))
            fixture = '#define PLATFORM "%s"\n' % name + FIXTURE_HEAD + platform_side(helpers, windows) + body
            cpp, exe = Path(temp) / ("%s.cpp" % name), Path(temp) / ("%s.exe" % name)
            cpp.write_text(fixture, encoding="utf-8")
            subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable",
                            "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-missing-field-initializers", str(cpp), "-o", str(exe)], check=True)
            failed |= subprocess.run([str(exe)], timeout=20).returncode
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
