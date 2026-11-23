"""The cooperative-matrix probe (run_shader in vk_coopmat.cpp) cut out and run against a stand-in driver whose map,
fence or wait fails: nothing is written through an unmapped pointer, nothing is submitted without a fence, and nothing
the dispatch may still be using is destroyed when the wait gives up."""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ['--cxx', '{cxx}', '--headers', '{vulkan_headers}']

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r"""
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static bool fail_map, fail_fence, fail_wait;
static int submits, destroyed_buffers, freed_memory, destroyed_pools, destroyed_pipelines;
static uint32_t mapped[64];
template <class T> T handle(uintptr_t v) { return reinterpret_cast<T>(v); }

struct { VkDevice device = handle<VkDevice>(1); VkPhysicalDevice physical = handle<VkPhysicalDevice>(2);
         VkQueue queue = handle<VkQueue>(3); uint32_t queue_family = 0; VkShaderStageFlags sg32_stages = 0; } dev;
void logf(const char*, ...) {}
struct IntelDriverGate { ~IntelDriverGate() {} };
VkResult vkCreateShaderModule(VkDevice, const VkShaderModuleCreateInfo*, const VkAllocationCallbacks*, VkShaderModule*);
VkResult vkCreateComputePipelines(VkDevice, VkPipelineCache, uint32_t, const VkComputePipelineCreateInfo*, const VkAllocationCallbacks*, VkPipeline*);
VkResult extern_guarded_shader_module(const VkShaderModuleCreateInfo* ci, VkShaderModule* m) { return vkCreateShaderModule(dev.device, ci, nullptr, m); }
VkResult extern_guarded_compute(VkPipelineCache cache, const VkComputePipelineCreateInfo* ci, VkPipeline* pl) { return vkCreateComputePipelines(dev.device, cache, 1, ci, nullptr, pl); }

VkResult vkCreateBuffer(VkDevice, const VkBufferCreateInfo*, const VkAllocationCallbacks*, VkBuffer* b) { *b = handle<VkBuffer>(10); return VK_SUCCESS; }
void vkGetBufferMemoryRequirements(VkDevice, VkBuffer, VkMemoryRequirements* r) { *r = {256, 4, 1}; }
void vkGetPhysicalDeviceMemoryProperties(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties* p) {
    *p = {}; p->memoryTypeCount = 1;
    p->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
}
VkResult vkAllocateMemory(VkDevice, const VkMemoryAllocateInfo*, const VkAllocationCallbacks*, VkDeviceMemory* m) { *m = handle<VkDeviceMemory>(11); return VK_SUCCESS; }
VkResult vkBindBufferMemory(VkDevice, VkBuffer, VkDeviceMemory, VkDeviceSize) { return VK_SUCCESS; }
VkResult vkMapMemory(VkDevice, VkDeviceMemory, VkDeviceSize, VkDeviceSize, VkMemoryMapFlags, void** p) {
    if (fail_map) return VK_ERROR_MEMORY_MAP_FAILED;
    *p = mapped; return VK_SUCCESS;
}
VkResult vkCreateDescriptorSetLayout(VkDevice, const VkDescriptorSetLayoutCreateInfo*, const VkAllocationCallbacks*, VkDescriptorSetLayout* o) { *o = handle<VkDescriptorSetLayout>(12); return VK_SUCCESS; }
VkResult vkCreatePipelineLayout(VkDevice, const VkPipelineLayoutCreateInfo*, const VkAllocationCallbacks*, VkPipelineLayout* o) { *o = handle<VkPipelineLayout>(13); return VK_SUCCESS; }
VkResult vkCreateShaderModule(VkDevice, const VkShaderModuleCreateInfo*, const VkAllocationCallbacks*, VkShaderModule* o) { *o = handle<VkShaderModule>(14); return VK_SUCCESS; }
VkResult vkCreateComputePipelines(VkDevice, VkPipelineCache, uint32_t, const VkComputePipelineCreateInfo*, const VkAllocationCallbacks*, VkPipeline* o) { *o = handle<VkPipeline>(15); return VK_SUCCESS; }
VkResult vkCreateDescriptorPool(VkDevice, const VkDescriptorPoolCreateInfo*, const VkAllocationCallbacks*, VkDescriptorPool* o) { *o = handle<VkDescriptorPool>(16); return VK_SUCCESS; }
VkResult vkAllocateDescriptorSets(VkDevice, const VkDescriptorSetAllocateInfo*, VkDescriptorSet* o) { *o = handle<VkDescriptorSet>(17); return VK_SUCCESS; }
void vkUpdateDescriptorSets(VkDevice, uint32_t, const VkWriteDescriptorSet*, uint32_t, const VkCopyDescriptorSet*) {}
VkResult vkCreateCommandPool(VkDevice, const VkCommandPoolCreateInfo*, const VkAllocationCallbacks*, VkCommandPool* o) { *o = handle<VkCommandPool>(18); return VK_SUCCESS; }
VkResult vkAllocateCommandBuffers(VkDevice, const VkCommandBufferAllocateInfo*, VkCommandBuffer* o) { *o = handle<VkCommandBuffer>(19); return VK_SUCCESS; }
VkResult vkBeginCommandBuffer(VkCommandBuffer, const VkCommandBufferBeginInfo*) { return VK_SUCCESS; }
void vkCmdBindPipeline(VkCommandBuffer, VkPipelineBindPoint, VkPipeline) {}
void vkCmdBindDescriptorSets(VkCommandBuffer, VkPipelineBindPoint, VkPipelineLayout, uint32_t, uint32_t, const VkDescriptorSet*, uint32_t, const uint32_t*) {}
void vkCmdDispatch(VkCommandBuffer, uint32_t, uint32_t, uint32_t) {}
void vkCmdPipelineBarrier2(VkCommandBuffer, const VkDependencyInfo*) {}
VkResult vkEndCommandBuffer(VkCommandBuffer) { return VK_SUCCESS; }
VkResult vkCreateFence(VkDevice, const VkFenceCreateInfo*, const VkAllocationCallbacks*, VkFence* o) {
    if (fail_fence) return VK_ERROR_OUT_OF_HOST_MEMORY;   // (*o left as it was)
    *o = handle<VkFence>(20); return VK_SUCCESS;
}
VkResult vkQueueSubmit2(VkQueue, uint32_t, const VkSubmitInfo2*, VkFence f) {
    assert(f != VK_NULL_HANDLE);   // (a submit with no fence can never be waited on)
    submits++; return VK_SUCCESS;
}
VkResult vkWaitForFences(VkDevice, uint32_t, const VkFence*, VkBool32, uint64_t) { return fail_wait ? VK_TIMEOUT : VK_SUCCESS; }
void vkDestroyFence(VkDevice, VkFence, const VkAllocationCallbacks*) {}
void vkDestroyCommandPool(VkDevice, VkCommandPool, const VkAllocationCallbacks*) { destroyed_pools++; }
void vkDestroyDescriptorPool(VkDevice, VkDescriptorPool, const VkAllocationCallbacks*) { destroyed_pools++; }
void vkDestroyPipeline(VkDevice, VkPipeline, const VkAllocationCallbacks*) { destroyed_pipelines++; }
void vkDestroyShaderModule(VkDevice, VkShaderModule, const VkAllocationCallbacks*) {}
void vkDestroyPipelineLayout(VkDevice, VkPipelineLayout, const VkAllocationCallbacks*) {}
void vkDestroyDescriptorSetLayout(VkDevice, VkDescriptorSetLayout, const VkAllocationCallbacks*) {}
void vkDestroyBuffer(VkDevice, VkBuffer, const VkAllocationCallbacks*) { destroyed_buffers++; }
void vkFreeMemory(VkDevice, VkDeviceMemory, const VkAllocationCallbacks*) { freed_memory++; }

/* ACTUAL */

int main(int argc, char** argv) {
    const std::string test = argc > 1 ? argv[1] : "";
    std::vector<uint32_t> code(4, 0), data(64, 7);
    if (test == "healthy") {
        assert(run_shader(code, data)); assert(submits == 1);
        assert(destroyed_buffers == 1 && freed_memory == 1 && destroyed_pools == 2 && destroyed_pipelines == 1);
    } else if (test == "map-fail") {
        fail_map = true;
        assert(!run_shader(code, data)); assert(submits == 0);
        assert(destroyed_buffers == 1 && freed_memory == 1);
    } else if (test == "fence-fail") {
        fail_fence = true;
        assert(!run_shader(code, data)); assert(submits == 0);
        assert(destroyed_buffers == 1 && freed_memory == 1 && destroyed_pools == 2);
    } else if (test == "wait-fail") {
        fail_wait = true;
        assert(!run_shader(code, data)); assert(submits == 1);
        // (the dispatch may still run: its buffer, memory, pools and pipeline stay alive)
        assert(destroyed_buffers == 0 && freed_memory == 0 && destroyed_pools == 0 && destroyed_pipelines == 0);
    } else {
        return 2;
    }
    return 0;
}
"""


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--headers", type=Path, required=True)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    coopmat = (a.source / "gpu/vk/vk_coopmat.cpp").read_text(encoding="utf-8")
    fixture = FIXTURE.replace("/* ACTUAL */", block(coopmat, "bool run_shader("))
    cases = ["healthy", "map-fail", "fence-fail", "wait-fail"]
    with tempfile.TemporaryDirectory(prefix="coopmat-probe-") as temp:
        cpp, exe = Path(temp) / "fixture.cpp", Path(temp) / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                        "-Wno-unused-parameter", "-I" + str(a.headers), str(cpp), "-o", str(exe)], check=True)
        for case in cases:
            subprocess.run([str(exe), case], check=True, timeout=20)
    print(f"{len(cases)} cooperative-matrix probe failure cases passed")


if __name__ == "__main__":
    main()
