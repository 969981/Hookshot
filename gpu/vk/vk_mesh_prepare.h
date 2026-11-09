// Pascal fallback. The SPIR-V converter/builder are shared with our Lichii layer.
// Original stages stay in descriptor order; only the host pipeline stages change.
struct MeshFallback {
    VkShaderModule vertex = VK_NULL_HANDLE, fragment = VK_NULL_HANDLE;
    VkShaderModule mesh = VK_NULL_HANDLE, task = VK_NULL_HANDLE;
    VkPipeline mesh_pipeline = VK_NULL_HANDLE, task_pipeline = VK_NULL_HANDLE;
    VkDescriptorSetLayout buffers = VK_NULL_HANDLE;
    uint32_t max_v = 0, max_p = 0, payload = 1, nv = 0, np = 0, per_task = 1;
    bool ok = false;
};
std::unordered_map<const Program*, MeshFallback> mesh_fallbacks;
std::recursive_mutex mesh_fallback_mu;
MeshFallback& mesh_fallback_get(const Program& p) {
    std::lock_guard lk(mesh_fallback_mu);
    return mesh_fallbacks.at(&p);
}

void mesh_fallback_forget(const Program& p) {
    std::lock_guard lk(mesh_fallback_mu);
    auto it = mesh_fallbacks.find(&p);
    if (it == mesh_fallbacks.end()) return;
    MeshFallback& f = it->second;
    for (VkPipeline x : {f.mesh_pipeline, f.task_pipeline})
        if (x) vkDestroyPipeline(dev.device, x, nullptr);
    for (VkShaderModule x : {f.vertex, f.fragment, f.mesh, f.task})
        if (x) vkDestroyShaderModule(dev.device, x, nullptr);
    if (f.buffers) vkDestroyDescriptorSetLayout(dev.device, f.buffers, nullptr);
    mesh_fallbacks.erase(it);
}

bool mesh_fallback_prepare(Program& p) {
    std::lock_guard lk(mesh_fallback_mu);
    auto [it, fresh] = mesh_fallbacks.try_emplace(&p);
    if (!fresh) return it->second.ok;
    MeshFallback& f = it->second;
    const Stage *ms = nullptr, *ts = nullptr, *fs = nullptr;
    for (const Stage& st : p.stages) {
        if (st.vk_stage == VK_SHADER_STAGE_MESH_BIT_EXT) ms = &st;
        if (st.vk_stage == VK_SHADER_STAGE_TASK_BIT_EXT) ts = &st;
        if (st.vk_stage == VK_SHADER_STAGE_FRAGMENT_BIT) fs = &st;
    }
    const auto fail = [&](const std::string& why) {
        logf("vk: mesh fallback %016llx unavailable: %s", (unsigned long long)p.key, why.c_str());
        mesh_fallback_forget(p);
        return false;
    };
    if (!ms || ms->spirv.empty()) return fail("no retained mesh SPIR-V");
    spvrw::FragResult frag;
    if (fs) {
        frag = spvrw::FragmentForVertexPipeline(fs->spirv.data(), fs->spirv.size());
        if (!frag.ok) return fail(frag.error);
    }
    spvrw::Options opt;
    opt.set = 1;
    opt.task_pipeline = ts != nullptr;
    opt.debug_slot = 0;
    for (const auto& input : frag.inputs) opt.keep_locations.push_back(input.location);
    const auto mesh_code = MeshSingleDrawSpirv(ms->spirv);
    const auto mesh = spvrw::MeshToCompute(mesh_code.data(), mesh_code.size(), opt);
    if (!mesh.ok) return fail(mesh.error);
    spvrw::Result task;
    if (ts) {
        const auto task_code = MeshSingleDrawSpirv(ts->spirv);
        task = spvrw::TaskToCompute(task_code.data(), task_code.size(), opt);
        if (!task.ok) return fail(task.error);
        task.spirv = MeshTaskFirstSpirv(task.spirv, ts->spirv);
        // Hookshot's task epilogue limits emitted groups to its workgroup size by default.
        const char* cap = getenv("RX_VK_TASK_CAP");
        f.per_task = cap && atoi(cap) > 0 ? (uint32_t)atoi(cap) : task.local_x;
        if (!f.per_task || f.per_task > dev.props.limits.maxComputeWorkGroupCount[0]) return fail("task cap exceeds device limits");
    }
    f.max_v = mesh.max_vertices; f.max_p = mesh.max_primitives;
    f.payload = std::max(1u, ts ? task.payload_words : mesh.payload_words);
    f.nv = (uint32_t)mesh.vertex_locations.size(); f.np = (uint32_t)mesh.prim_locations.size();
    const auto module = [&](const std::vector<uint32_t>& code, VkShaderModule& dst) {
        VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
        ci.codeSize = code.size() * 4; ci.pCode = code.data();
        return vkCreateShaderModule(dev.device, &ci, nullptr, &dst) == VK_SUCCESS;
    };
    std::vector<spvb::VsOutput> outputs;
    for (const auto& input : frag.inputs) {
        spvb::VsOutput o;
        o.location = input.location; o.component = input.component; o.count = input.count; o.kind = input.kind;
        auto v = std::find(mesh.vertex_locations.begin(), mesh.vertex_locations.end(), input.location);
        auto q = std::find(mesh.prim_locations.begin(), mesh.prim_locations.end(), input.location);
        if (v != mesh.vertex_locations.end()) o.source = 1, o.k = (uint32_t)(v - mesh.vertex_locations.begin());
        else if (q != mesh.prim_locations.end()) o.source = 2, o.k = (uint32_t)(q - mesh.prim_locations.begin());
        outputs.push_back(o);
    }
    if (!module(mesh.spirv, f.mesh) || (ts && !module(task.spirv, f.task)) ||
        !module(spvb::RealVertexShader(1, f.max_v, f.max_p, f.nv, f.np, outputs), f.vertex) ||
        (fs && !module(frag.spirv, f.fragment))) return fail("vkCreateShaderModule");
    VkDescriptorSetLayoutBinding bindings[8]{};
    for (uint32_t b = 0; b < 8; b++)
        bindings[b] = {b, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_VERTEX_BIT, nullptr};
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    li.bindingCount = 8; li.pBindings = bindings;
    if (vkCreateDescriptorSetLayout(dev.device, &li, nullptr, &f.buffers) != VK_SUCCESS) return fail("buffer descriptor layout");
    f.ok = true;
    logf("vk: mesh fallback %016llx converted: %u vertices, %u primitives, %u vertex/%u primitive outputs, task cap %u",
         (unsigned long long)p.key, f.max_v, f.max_p, f.nv, f.np, f.per_task);
    return true;
}

bool mesh_fallback_pipelines(Program& p) {
    MeshFallback& f = mesh_fallback_get(p);
    const auto make = [&](VkShaderModule module, VkPipeline& out) {
        if (!module || out) return true;
        VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, module, "main", nullptr};
        ci.layout = p.layout;
        pin_subgroup(ci.stage);
        const VkResult result = vkCreateComputePipelines(dev.device, VK_NULL_HANDLE, 1, &ci, nullptr, &out);
        if (result != VK_SUCCESS) logf("vk: mesh fallback %016llx compute pipeline failed: %d", (unsigned long long)p.key, (int)result);
        return result == VK_SUCCESS;
    };
    return make(f.task, f.task_pipeline) && make(f.mesh, f.mesh_pipeline);
}
