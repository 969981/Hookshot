// Authored allocator/address failures only; no Vulkan device, driver or game data.
// Null mapping and zero BDA are defensive injections, not conforming-driver outcomes.
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

using VkBuffer = uint64_t;
using VkBufferUsageFlags = uint32_t;
struct Allocation {};
using VmaAllocation = Allocation*;
constexpr VkBuffer VK_NULL_HANDLE = 0;
constexpr VkBufferUsageFlags VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT = 1;
constexpr VkBufferUsageFlags VK_BUFFER_USAGE_TRANSFER_DST_BIT = 2;
constexpr VkBufferUsageFlags VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT = 4;
constexpr int VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO = 1;
struct VkBufferDeviceAddressInfo { int sType; VkBuffer buffer = 0; };
struct { int device = 1; } dev;

static Allocation confirmed, poison;
static std::vector<uint32_t> storage;
static bool live;
static unsigned creates, destroys, addresses;
static std::string mode;
static constexpr uint32_t sentinel = 0xcccccccc;
static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
static uint64_t ring_bytes();
static bool create_buffer(uint64_t size, VkBufferUsageFlags usage, bool host_visible,
                          VkBuffer& buffer, VmaAllocation& allocation, void** mapped) {
    ++creates;
    require(creates == 1, "initialization allocated more than once");
    require(size == ring_bytes(), "unexpected allocation size");
    require(usage == (VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                      VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT) && host_visible && mapped,
            "changed production allocation flags");
    if (mode == "allocation_failure" || mode == "allocation_failure_outputs") {
        if (mode == "allocation_failure_outputs") {
            buffer = 0xffff;
            allocation = &poison; // Failure outputs are not a confirmed allocation.
        }
        return false;
    }
    buffer = 7;
    allocation = &confirmed;
    live = true;
    storage.assign(size / sizeof(uint32_t) + 2, sentinel);
    *mapped = mode == "null_mapping" ? nullptr : storage.data() + 1;
    return true;
}
[[maybe_unused]] static void destroy_buffer(VkBuffer buffer, VmaAllocation allocation) {
    require(live && buffer == 7 && allocation == &confirmed,
            "destroyed an unconfirmed or already-destroyed allocation");
    live = false;
    ++destroys;
}
static uint64_t vkGetBufferDeviceAddress(int device, const VkBufferDeviceAddressInfo* info) {
    require(live && device == 1 && info->sType == VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO &&
            info->buffer == 7, "queried an unconfirmed allocation");
    ++addresses;
    return mode == "zero_address" ? 0 : 0x7000;
}

#include "mesh_args_actual.inc"

static uint64_t ring_bytes() { return static_cast<uint64_t>(kMeshArgs) * 4 * sizeof(uint32_t); }

static void initialized_ring() {
    require(storage.size() == static_cast<std::size_t>(kMeshArgs) * 4 + 2,
            "allocation does not hold the actual ring");
    require(storage.front() == sentinel && storage.back() == sentinel,
            "initialization wrote beyond the ring");
    for (uint32_t i = 0; i < kMeshArgs; ++i) {
        const auto* w = storage.data() + 1 + i * 4;
        require(w[0] == 0 && w[1] == 1 && w[2] == 1 && w[3] == 0,
                "changed initial indirect argument contents");
    }
}
static void failed_no_retry() {
    const auto prior_addresses = addresses;
    require(!mesh_args_ready(), "failed initialization became ready");
    require(creates == 1 && addresses == prior_addresses, "failed initialization was retried");
}

int main(int argc, char** argv) {
    mode = argc == 2 ? argv[1] : "";
    try {
        if (mode == "allocation_failure" || mode == "allocation_failure_outputs") {
            require(!mesh_args_ready(), "failed allocation reported ready");
            require(!live && destroys == 0 && addresses == 0,
                    "failed create destroyed or used unconfirmed output");
            require(mesh_args_buffer == (mode == "allocation_failure_outputs" ? 0xffffu : 0u) &&
                    mesh_args_alloc == (mode == "allocation_failure_outputs" ? &poison : nullptr) &&
                    !mesh_args_words && !mesh_args_address,
                    "changed initial allocation-failure behavior");
            failed_no_retry();
        } else if (mode == "null_mapping" || mode == "zero_address") {
            require(!mesh_args_ready(), "invalid initialization reported ready");
            require(!live && destroys == 1, "failed initialization retained a confirmed buffer");
            require(!mesh_args_buffer && !mesh_args_alloc && !mesh_args_words && !mesh_args_address,
                    "failed initialization retained stale handles, pointer or address");
            require(addresses == (mode == "zero_address" ? 1u : 0u), "unexpected BDA query");
            if (mode == "zero_address") initialized_ring();
            else require(std::all_of(storage.begin(), storage.end(), [](auto w) { return w == sentinel; }),
                         "null mapping caused a write");
            failed_no_retry();
            require(destroys == 1, "permanent failure repeated cleanup");
        } else if (mode == "success_reuse") {
            require(mesh_args_ready(), "valid initialization failed");
            require(live && destroys == 0 && creates == 1 && addresses == 1 && mesh_args_buffer == 7 &&
                    mesh_args_alloc == &confirmed && mesh_args_words == storage.data() + 1 &&
                    mesh_args_address == 0x7000, "successful result incomplete");
            initialized_ring();
            mesh_args_words[4] = 123;
            mesh_args_words[7] = 456;
            require(mesh_args_ready(), "successful initialization stopped being ready");
            require(creates == 1 && destroys == 0 && addresses == 1 &&
                    mesh_args_words[4] == 123 && mesh_args_words[7] == 456,
                    "reuse recreated, destroyed or reset the ring");
        } else if (mode == "take_ranges") {
            // Entries handed out stay in use until kMeshArgs / 2 more have been handed out: no range may reuse one
            // sooner. A big draw right before the old modulus wrapped, then a one-entry draw, overlapped.
            std::vector<uint64_t> taken_at(kMeshArgs, UINT64_MAX);
            uint64_t handed = 1;   // (the ring's counter starts at 1)
            const auto take = [&](uint32_t n) {
                const uint32_t at = mesh_args_take(n);
                n = std::clamp<uint32_t>(n, 1, kMeshArgs / 2);
                require(at >= 1 && (uint64_t)at + n <= kMeshArgs, "range outside the ring or on entry 0");
                for (uint32_t i = 0; i < n; ++i) {
                    const uint64_t prior = taken_at[at + i];
                    require(prior == UINT64_MAX || handed + n - prior > kMeshArgs / 2,
                            "range reuses entries still in use");
                    taken_at[at + i] = handed;
                }
                handed += n;
            };
            constexpr uint32_t big = 4096;
            while (handed < (uint64_t)kMeshArgs - big) take(1);
            take(big);
            for (uint32_t k = 0; k < 64; ++k) take(1 + k % 5);
            for (uint32_t k = 0; k < 3 * kMeshArgs / big; ++k) take(k % 3 ? 1 + k % 97 : big);
        } else {
            throw std::runtime_error("unknown fixture case");
        }
        std::printf("PASS %s\n", mode.c_str());
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "FAIL %s: %s\n", mode.c_str(), e.what());
        return 1;
    }
}
