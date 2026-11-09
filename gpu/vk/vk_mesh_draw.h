// One GPU-only scratch arena, reused in queue order with barriers around every draw.
// The CPU never overwrites it while work is in flight. Recording-owned feedback is
// read only when the recording is retired. No game-result CPU readback is added.
namespace {
// (the scratch: RX_VK_FALLBACK_SCRATCH_MB, default 256. 128 MB cut the Death Mountain Trail cliffs (739 of up to ~2500 groups);
// 512 MB with the textures pushed an 8 GB card over what Windows allows and VRAM paged out: 2 fps in Kakariko. Read at first use,
// after hookshot_env.txt is loaded)
uint64_t fallback_arena_bytes() {
    static const uint64_t v = [] {
        const char* e = getenv("RX_VK_FALLBACK_SCRATCH_MB");
        return (uint64_t)std::clamp(e ? atoi(e) : 256, 64, 1024) << 20;
    }();
    return v;
}
#define kFallbackArena fallback_arena_bytes()
constexpr uint64_t kFallbackCounters = 256, kFallbackCounterBytes = 16384;
VkBuffer fallback_arena = VK_NULL_HANDLE;
VmaAllocation fallback_arena_alloc = nullptr;
uint64_t fallback_address = 0;
VkDescriptorSetLayout fallback_args_set = VK_NULL_HANDLE;
VkPipelineLayout fallback_args_layout = VK_NULL_HANDLE;
VkPipeline fallback_args_pipeline = VK_NULL_HANDLE;

bool fallback_arena_ready() {
    if (fallback_args_pipeline) return true;
    const auto fail = [&] {
        if (fallback_arena) destroy_buffer(fallback_arena, fallback_arena_alloc);
        if (fallback_args_layout) vkDestroyPipelineLayout(dev.device, fallback_args_layout, nullptr);
        if (fallback_args_set) vkDestroyDescriptorSetLayout(dev.device, fallback_args_set, nullptr);
        fallback_arena = VK_NULL_HANDLE; fallback_arena_alloc = nullptr; fallback_address = 0;
        fallback_args_layout = VK_NULL_HANDLE; fallback_args_set = VK_NULL_HANDLE;
        logf("vk: mesh fallback scratch/argument setup failed");
        return false;
    };
    if (!create_device_buffer(kFallbackArena, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
        VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
        fallback_arena, fallback_arena_alloc)) return fail();
    VkBufferDeviceAddressInfo address{VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO}; address.buffer = fallback_arena;
    fallback_address = vkGetBufferDeviceAddress(dev.device, &address);
    if (!fallback_address) return fail();
    VkDescriptorSetLayoutBinding bs[] = {
        {0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr},
        {7, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr}};
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO}; li.bindingCount = 2; li.pBindings = bs;
    if (vkCreateDescriptorSetLayout(dev.device, &li, nullptr, &fallback_args_set) != VK_SUCCESS) return fail();
    const VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, 28};
    VkPipelineLayoutCreateInfo pi{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pi.setLayoutCount = 1; pi.pSetLayouts = &fallback_args_set; pi.pushConstantRangeCount = 1; pi.pPushConstantRanges = &range;
    if (vkCreatePipelineLayout(dev.device, &pi, nullptr, &fallback_args_layout) != VK_SUCCESS) return fail();
    const auto code = spvb::IndirectArgsShader();
    VkShaderModuleCreateInfo mi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO}; mi.codeSize = code.size() * 4; mi.pCode = code.data();
    VkShaderModule module = VK_NULL_HANDLE;
    if (vkCreateShaderModule(dev.device, &mi, nullptr, &module) != VK_SUCCESS) return fail();
    VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", nullptr};
    ci.layout = fallback_args_layout;
    const VkResult result = vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &ci, nullptr, &fallback_args_pipeline);
    vkDestroyShaderModule(dev.device, module, nullptr);
    if (result != VK_SUCCESS) return fail();
    logf("vk: mesh fallback scratch: %llu MB, GPU argument setup ready", (unsigned long long)(kFallbackArena >> 20));
    return true;
}

VkDescriptorSet fallback_set(VkDescriptorSetLayout layout, VkWriteDescriptorSet* writes, uint32_t n) {
    VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    ai.descriptorPool = cur->extra.empty() ? cur->pool : cur->extra.back();
    ai.descriptorSetCount = 1; ai.pSetLayouts = &layout;
    VkDescriptorSet set = VK_NULL_HANDLE;
    if (vkAllocateDescriptorSets(dev.device, &ai, &set) != VK_SUCCESS) {
        const VkDescriptorPool pool = make_pool();
        if (!pool) return VK_NULL_HANDLE;
        cur->extra.push_back(pool); ai.descriptorPool = pool;
        if (vkAllocateDescriptorSets(dev.device, &ai, &set) != VK_SUCCESS) return VK_NULL_HANDLE;
    }
    for (uint32_t i = 0; i < n; i++) writes[i].dstSet = set;
    vkUpdateDescriptorSets(dev.device, n, writes, 0, nullptr);
    return set;
}

// (the scratch's descriptor sets hold the same buffers draw after draw: the argument set always, an output set per program,
// mesh groups per task and capacity. Made once in a pool that is never reset, instead of two allocations and updates of 8
// descriptors for every draw; a full pool falls back to the recording's pool)
VkDescriptorPool fallback_kept_pool = VK_NULL_HANDLE;
std::unordered_map<uint64_t, VkDescriptorSet> fallback_kept_sets;
VkDescriptorSet fallback_set_kept(uint64_t key, VkDescriptorSetLayout layout, VkWriteDescriptorSet* writes, uint32_t n) {
    if (const auto it = fallback_kept_sets.find(key); it != fallback_kept_sets.end()) return it->second;
    if (!fallback_kept_pool) {
        const VkDescriptorPoolSize size{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 8 * 4096};
        VkDescriptorPoolCreateInfo ci{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        ci.maxSets = 4096, ci.poolSizeCount = 1, ci.pPoolSizes = &size;
        if (vkCreateDescriptorPool(dev.device, &ci, nullptr, &fallback_kept_pool) != VK_SUCCESS) fallback_kept_pool = VK_NULL_HANDLE;
    }
    VkDescriptorSet set = VK_NULL_HANDLE;
    if (fallback_kept_pool) {
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        ai.descriptorPool = fallback_kept_pool, ai.descriptorSetCount = 1, ai.pSetLayouts = &layout;
        if (vkAllocateDescriptorSets(dev.device, &ai, &set) != VK_SUCCESS) set = VK_NULL_HANDLE;
    }
    if (!set) return fallback_set(layout, writes, n);   // (full: this recording's pool, as before)
    for (uint32_t i = 0; i < n; i++) writes[i].dstSet = set;
    vkUpdateDescriptorSets(dev.device, n, writes, 0, nullptr);
    fallback_kept_sets.emplace(key, set);
    return set;
}

// (RX_VK_FALLBACK_CONTROL=<file>: the fallback's live switches, read again when the file changes (checked every 300 ms). Words:
// "off": every fallback draw left out; "noshadow": those into depth-only targets (shadow maps, depth prepasses) left out)
struct FallbackLive { bool off = false, noshadow = false; };
FallbackLive fallback_live() {
    static const char* path = getenv("RX_VK_FALLBACK_CONTROL");
    static FallbackLive v;
    if (!path || !*path) return v;
    static int64_t next = 0, last_time = -1, last_size = -1;
    const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    if (now < next) return v;
    next = now + 300;
    std::error_code ec;
    const auto size = std::filesystem::file_size(path, ec);
    const auto time = ec ? std::filesystem::file_time_type{} : std::filesystem::last_write_time(path, ec);
    if (ec) {
        if (v.off || v.noshadow) v = {}, logf("vk: mesh fallback control: no file, all drawn");
        last_size = last_time = -1;
        return v;
    }
    if ((int64_t)size == last_size && (int64_t)time.time_since_epoch().count() == last_time) return v;
    last_size = (int64_t)size, last_time = (int64_t)time.time_since_epoch().count();
    std::string text;
    if (FILE* f = fopen(path, "rb")) {
        char b[512];
        for (size_t n; (n = fread(b, 1, sizeof b, f)) > 0;) text.append(b, n);
        fclose(f);
    }
    v.off = text.find("off") != std::string::npos;
    v.noshadow = text.find("noshadow") != std::string::npos;
    logf("vk: mesh fallback control: %s%s", v.off ? "all left out" : "drawn", v.noshadow ? ", none into depth-only targets" : "");
    return v;
}

// (where the fallback's CPU time goes, per stage, logged every 10 s: the replay thread is the frame's limit, and each mesh draw costs it
// a pass break, barriers, an argument pass and two descriptor binds)
struct FallbackClock {
    uint64_t ns[6]{};
    uint64_t calls = 0, iters = 0;
    std::chrono::steady_clock::time_point since = std::chrono::steady_clock::now();
};
FallbackClock fb_clock;
inline uint64_t fb_now() {
    return (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
void fb_clock_report() {
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - fb_clock.since).count();
    if (secs < 10.0) return;
    static const char* names[6] = {"setup", "prepare", "arguments", "compute", "draw", "report"};
    uint64_t total = 0;
    for (const uint64_t v : fb_clock.ns) total += v;
    char line[512];
    int n = snprintf(line, sizeof line, "vk: mesh fallback CPU in %.0f s: %llu calls, %llu draws; ms a second:", secs,
                     (unsigned long long)fb_clock.calls, (unsigned long long)fb_clock.iters);
    for (int i = 0; i < 6; i++) n += snprintf(line + n, sizeof line - n, " %s %.2f", names[i], fb_clock.ns[i] / 1e6 / secs);
    snprintf(line + n, sizeof line - n, "; total %.2f ms a second, %.1f us a draw", total / 1e6 / secs,
             fb_clock.iters ? total / 1e3 / (double)fb_clock.iters : 0.0);
    logf("%s", line);
    fb_clock = FallbackClock{};
}


bool mesh_fallback_draw(VkCommandBuffer cmd, const NvnState& s, const DrawArgs& d, const Program& p,
                        const Targets& at, VkPipeline graphics, uint16_t writes_mask) {
    {   // (live switches, and which programs draw into depth-only targets: once each)
        const bool depth_only = at.n == 0;
        if (depth_only) {
            static std::unordered_set<uint64_t> told;
            if (told.size() < 300 && told.insert(p.key).second)
                logf("vk: mesh fallback %016llx draws into a depth-only %ux%u target", (unsigned long long)p.key, at.width, at.height);
        }
        const FallbackLive live = fallback_live();
        if (live.off || (live.noshadow && depth_only)) return true;
    }
    uint64_t mark = fb_now();
    const auto lap = [&](int i) { const uint64_t t = fb_now(); fb_clock.ns[i] += t - mark; mark = t; };
    fb_clock.calls++;
    struct RestoreTracking {
        ~RestoreTracking() {
            last_pipeline=VK_NULL_HANDLE; last_dyn_valid=false; last_vb_valid=false;
            pushed[1].cmd = VK_NULL_HANDLE;   // (compute only: the argument program bound set 0 there; graphics kept draw()'s push)
        }
    } restore_tracking;
    MeshFallback& f = mesh_fallback_get(p);
    // (mesh groups per task: what the program's tasks have sent so far, rounded up to a power of two, not the task cap:
    // 4x less scratch and vertex work for OoT's foliage; a task that sends more is reported (OVERFLOW) and the next draws grow)
    uint32_t per_task = f.per_task;
    if (f.task && per_task > 1) {
        uint32_t seen = 0;
        {
            std::lock_guard lk(mesh_emitted_mu);
            const auto it = mesh_emitted.find(p.key);
            if (it != mesh_emitted.end()) seen = it->second;
        }
        if (seen) {
            uint32_t pt = 1;
            while (pt < seen && pt < per_task) pt <<= 1;
            per_task = std::min(per_task, pt);
        }
    }
    const auto samples = pass.sampled;
    const uint16_t sampled_targets = pass.sampled_targets;
    end_pass(cmd);
    if (!fallback_arena_ready()) return false;
    const uint64_t alignment = std::max<uint64_t>(16, dev.props.limits.minStorageBufferOffsetAlignment);
    const auto align = [&](uint64_t x) { return (x + alignment - 1) & ~(alignment - 1); };
    VkDescriptorBufferInfo buffers[8]{};
    const auto fit = [&](uint32_t groups) {
        uint64_t head = align(kFallbackCounters + kFallbackCounterBytes);
        const uint64_t slots = (uint64_t)groups * per_task;
        const uint64_t bytes[] = {slots*f.max_v*16, slots*f.max_p*16, slots*4,
                                  (uint64_t)groups*f.payload*4, (uint64_t)groups*4,
                                  slots*f.max_v*f.nv*16, slots*f.max_p*f.np*16};
        for (uint32_t i = 0; i < 7; i++) {
            const uint64_t size = std::max<uint64_t>(16, bytes[i]);
            if (size > dev.props.limits.maxStorageBufferRange) return false;
            buffers[i] = {fallback_arena, head, size}; head += align(size);
        }
        buffers[7] = {fallback_arena, kFallbackCounters, kFallbackCounterBytes};
        return head <= kFallbackArena && slots*f.max_p*3 <= UINT32_MAX;
    };
    uint32_t lo = 0, hi = dev.props.limits.maxComputeWorkGroupCount[f.task ? 1 : 0];
    while (lo < hi) {
        const uint32_t mid = lo + (hi-lo+1)/2;
        if (fit(mid)) lo = mid; else hi = mid-1;
    }
    if (!lo || !fit(lo)) return false;
    const uint32_t capacity = lo;
    const uint32_t draws = d.indirect ? (uint32_t)std::max(d.max, 1) : 1;
    if (draws > dev.props.limits.maxDrawIndirectCount) return false;
    BufferRef input{}, count{};
    const uint32_t stride = d.stride > 0 ? (uint32_t)d.stride : 8;
    if (d.indirect) {
        input = gpu_buffer_bind(d.indirect, (uint64_t)(draws-1)*stride + 8, false);
        if (d.count_addr) count = gpu_buffer_bind(d.count_addr, 4, false);
        if (!input || (d.count_addr && !count)) return false;
    } else {
        const uint64_t groups = (uint64_t)std::max(d.groups[0],0)*std::max(d.groups[1],1)*std::max(d.groups[2],1);
        if (groups > capacity) {
            logf("vk: mesh fallback direct draw exceeds scratch capacity: %llu > %u", (unsigned long long)groups, capacity);
            return false;
        }
    }
    VkWriteDescriptorSet out_writes[8]{};
    for (uint32_t b = 0; b < 8; b++) {
        out_writes[b] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET}; out_writes[b].dstBinding = b;
        out_writes[b].descriptorCount = 1; out_writes[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        out_writes[b].pBufferInfo = &buffers[b];
    }
    const uint64_t output_key = (p.key * 1099511628211ull ^ (uint64_t)f.buffers) * 1099511628211ull ^ ((uint64_t)per_task << 32 | capacity);
    const VkDescriptorSet output = fallback_set_kept(output_key, f.buffers, out_writes, 8);
    const VkDescriptorBufferInfo setup_buffers[] = {{fallback_arena, 0, 64}, buffers[7]};
    VkWriteDescriptorSet setup_writes[2]{};
    for (uint32_t b = 0; b < 2; b++) {
        setup_writes[b] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET}; setup_writes[b].dstBinding = b ? 7 : 0;
        setup_writes[b].descriptorCount = 1; setup_writes[b].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        setup_writes[b].pBufferInfo = &setup_buffers[b];
    }
    const VkDescriptorSet setup = fallback_set_kept(0x5e7u, fallback_args_set, setup_writes, 2);
    if (!output || !setup) return false;
    lap(0);
    for (uint32_t draw = 0; draw < draws; draw++) {
        full_barrier(cmd); // previous graphics reads / compute writes -> scratch reuse and argument copies
        uint32_t args[16]{};
        args[0] = (uint32_t)std::max(d.groups[0],0);
        args[1] = (uint32_t)std::max(d.groups[1],1); args[2] = (uint32_t)std::max(d.groups[2],1);
        args[7] = (uint32_t)d.first_task;
        vkCmdUpdateBuffer(cmd, fallback_arena, 0, sizeof args, args);
        full_barrier(cmd);
        if (input) {
            const VkBufferCopy copies[] = {{input.offset+(uint64_t)draw*stride,0,4}, {input.offset+(uint64_t)draw*stride+4,28,4}};
            vkCmdCopyBuffer(cmd, input.buffer, fallback_arena, 2, copies);
        }
        if (count) { const VkBufferCopy copy{count.offset,12,4}; vkCmdCopyBuffer(cmd,count.buffer,fallback_arena,1,&copy); }
        vkCmdFillBuffer(cmd, fallback_arena, buffers[2].offset, align(buffers[2].range), 0);
        vkCmdFillBuffer(cmd, fallback_arena, buffers[4].offset, align(buffers[4].range), 0);
        vkCmdFillBuffer(cmd, fallback_arena, kFallbackCounters, kFallbackCounterBytes, 0);
        full_barrier(cmd);
        const VkBufferCopy first_copy{28,kFallbackCounters+8,4};
        vkCmdCopyBuffer(cmd,fallback_arena,fallback_arena,1,&first_copy);
        full_barrier(cmd);
        lap(1);
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,fallback_args_pipeline);
        vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,fallback_args_layout,0,1,&setup,0,nullptr);
        const uint32_t config[] = {per_task,capacity,f.max_p*3,1,f.task ? 1u : 0u,count ? 1u : 0u,draw};
        vkCmdPushConstants(cmd,fallback_args_layout,VK_SHADER_STAGE_COMPUTE_BIT,0,sizeof config,config);
        vkCmdDispatch(cmd,1,1,1);
        full_barrier(cmd);
        lap(2);
        pushed[1].cmd = VK_NULL_HANDLE;   // (the argument program's set 0 replaced the compute push: all of it again)
        if (!bind_descriptors(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,p)) return false;
        vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,p.layout,1,1,&output,0,nullptr);
        push_constants(cmd,p,&at);
        const uint64_t firsts = fallback_address + 28;
        vkCmdPushConstants(cmd,p.layout,p.push_stages,Shader::Backend::SPIRV::MESH_FIRST_PUSH_OFFSET,8,&firsts);
        if (f.task_pipeline) {
            vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,f.task_pipeline);
            vkCmdDispatchIndirect(cmd,fallback_arena,16);
            full_barrier(cmd);
        }
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,f.mesh_pipeline);
        vkCmdDispatchIndirect(cmd,fallback_arena,f.task_pipeline ? 32 : 16);
        full_barrier(cmd); // compute storage writes -> generated vertex shader reads and draw indirect reads
        lap(3);
        begin_pass(cmd,at);
        pass.prog = pass.first_prog = p.key; pass.written |= writes_mask;
        pass.sampled = samples; pass.sampled_targets = sampled_targets;
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,graphics);
        if (!bind_descriptors(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,p)) return false;
        vkCmdBindDescriptorSets(cmd,VK_PIPELINE_BIND_POINT_GRAPHICS,p.layout,1,1,&output,0,nullptr);
        dynamic_state(cmd,s,at,VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST,false);
        vkCmdSetPrimitiveRestartEnable(cmd,VK_FALSE);
        vkCmdDrawIndirect(cmd,fallback_arena,48,1,16);
        pass.draws++;
        end_pass(cmd);
        full_barrier(cmd);
        lap(4);
        // (the GPU's report of what the draw asked for: one draw in 8 is enough to size the scratch, and each costs a copy and a barrier)
        static uint32_t feedback_tick = 0;
        const uint64_t feedback = (feedback_tick++ & 7) == 0 ? stream_alloc(8,4) : UINT64_MAX;
        if (feedback != UINT64_MAX) {
            const VkBufferCopy copy{kFallbackCounters,feedback,8};
            vkCmdCopyBuffer(cmd,fallback_arena,cur->stream,1,&copy);
            VkMemoryBarrier2 host_read{VK_STRUCTURE_TYPE_MEMORY_BARRIER_2};
            host_read.srcStageMask=VK_PIPELINE_STAGE_2_ALL_TRANSFER_BIT; host_read.srcAccessMask=VK_ACCESS_2_TRANSFER_WRITE_BIT;
            host_read.dstStageMask=VK_PIPELINE_STAGE_2_HOST_BIT; host_read.dstAccessMask=VK_ACCESS_2_HOST_READ_BIT;
            VkDependencyInfo dependency{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
            dependency.memoryBarrierCount=1; dependency.pMemoryBarriers=&host_read;
            vkCmdPipelineBarrier2(cmd,&dependency);
            cur->mesh_feedback.push_back({p.key,feedback,capacity,per_task,cur->mapped,cur->stream_alloc});
        }
        lap(5);
        fb_clock.iters++;
    }
    fb_clock_report();
    return true;
}
} // namespace
