// Compute: the compute engine's launches (B1C0 LAUNCH with a launch descriptor, the QMD) -> WebGPU compute passes.
// Storage buffers are bound from the buffer cache, read-write: what a dispatch writes stays on the GPU, where the
// draws and dispatches after it read it (the guest's copy of that memory is not updated).
#include <algorithm>
#include <unordered_set>

#include "common/div_ceil.h"

#include "rx_internal.h"

namespace rx {
uint64_t dispatch_write_epoch = 0;
namespace {

struct ComputePipeline {
    WGPUComputePipeline pipeline = nullptr;
    WGPUBindGroupLayout layout = nullptr;
    bool failed = false;
};
std::unordered_map<uint64_t, std::unique_ptr<ComputePipeline>> pipelines;

/// The launch descriptor (64 words; suyu's KeplerCompute::LaunchParams).
ComputeLaunch read_launch(const uint32_t* regs) {
    uint32_t q[64];
    gpu_read((uint64_t)regs[0xAD] << 8, q, sizeof q);
    ComputeLaunch l;
    l.code_base = (uint64_t)regs[0x582] << 32 | regs[0x583];
    l.program_start = q[8];
    l.linked_tsc = q[11] >> 30 & 1;
    l.grid[0] = q[12] & 0x7FFFFFFF;
    l.grid[1] = q[13] & 0xFFFF;
    l.grid[2] = q[13] >> 16;
    l.shared_size = q[17] & 0x3FFFF;
    l.block[0] = q[18] >> 16;
    l.block[1] = q[19] & 0xFFFF;
    l.block[2] = q[19] >> 16;
    const uint32_t mask = q[20] & 0xFF;
    for (int i = 0; i < 8; i++) {
        const uint32_t lo = q[29 + 2 * i], hi = q[30 + 2 * i];
        l.cbufs[i].address = (uint64_t)(hi & 0xFF) << 32 | lo;
        l.cbufs[i].size = hi >> 15;
        l.cbufs[i].enabled = mask >> i & 1;
    }
    l.local_size = (q[45] & 0xFFFFF) + (q[47] & 0xFFFFF);
    l.texture_bound = regs[0x982];
    return l;
}

uint32_t read_handle(const ComputeLaunch& l, const Shader::TextureDescriptor& d) {
    const uint64_t addr = l.cbufs[d.cbuf_index % 8].address + d.cbuf_offset;
    if (d.has_secondary) {
        const uint64_t sec = l.cbufs[d.secondary_cbuf_index % 8].address + d.secondary_cbuf_offset;
        return (gpu_get<uint32_t>(addr) << d.shift_left) | (gpu_get<uint32_t>(sec) << d.secondary_shift_left);
    }
    return gpu_get<uint32_t>(addr);
}

/// The web: the browser forbids writable storage bindings that overlap. Overlapping ranges of one buffer are bound
/// once, the lowest one's binding covering them all (end[j]: where each binding ends), and the others read it at
/// their offset in a variant of the program (alias[j] = that storage index + 1, alias_words[j] = the offset in
/// words). RX_FORCE_SSBO_ALIAS=1: the web's rule natively (testing). Returns whether any range was aliased.
bool alias_overlapping_storage(const std::vector<std::pair<uint32_t, BufferRef>>& storage, std::vector<uint64_t>& end,
                               std::array<uint8_t, 64>& alias, std::array<uint32_t, 64>& alias_words) {
    end.resize(storage.size());
    for (size_t j = 0; j < storage.size(); j++) end[j] = storage[j].second.offset + storage[j].second.size;
    static const bool force_alias = env_flag("RX_FORCE_SSBO_ALIAS");
    if (!(RX_WEB || force_alias) || storage.size() <= 1 || storage.size() > 64) return false;
    std::vector<size_t> order(storage.size());
    for (size_t j = 0; j < order.size(); j++) order[j] = j;
    std::sort(order.begin(), order.end(), [&](size_t a, size_t b2) {
        const auto& x = storage[a].second;
        const auto& y = storage[b2].second;
        return x.buffer != y.buffer ? (uintptr_t)x.buffer < (uintptr_t)y.buffer : x.offset < y.offset;
    });
    bool any = false;
    size_t base = order[0];
    for (size_t k = 1; k < order.size(); k++) {
        const size_t j = order[k];
        if (storage[j].second.buffer == storage[base].second.buffer && storage[j].second.offset < end[base]) {
            alias[j] = (uint8_t)(base + 1);
            alias_words[j] = (uint32_t)((storage[j].second.offset - storage[base].second.offset) / 4);
            end[base] = std::max(end[base], end[j]);
            any = true;
        } else {
            base = j;
        }
    }
    return any;
}

void dispatch(const ComputeLaunch& l) {
    ShaderModule* m = compute_module(l);
    if (!m) return count("cs_skipped");
    const Shader::Info& info = m->info;
    if (!info.image_descriptors.empty() || !info.image_buffer_descriptors.empty() ||
        !info.texture_buffer_descriptors.empty()) {
        log_limited("cs_images", 8, "rx: a compute program uses images or texture buffers: not dispatched yet");
        return count("cs_images");
    }
    if (!l.grid[0] || !l.grid[1] || !l.grid[2]) return count("cs_empty");
    if (l.block[0] * l.block[1] * l.block[2] > dev.limits.maxComputeInvocationsPerWorkgroup ||
        l.shared_size > dev.limits.maxComputeWorkgroupStorageSize)
        return count("cs_too_big");

    // room in the upload streams first: a flush must not come between the cache's bindings and the dispatch
    const uint64_t estimate = 64 * 1024 + info.constant_buffer_descriptors.size() * 0x10100 +
                              info.texture_descriptors.size() * 256 + info.storage_buffers_descriptors.size() * (1 << 20);
    if (stream_ro.used + estimate > stream_ro.capacity || stream_rw.used + (8u << 20) > stream_rw.capacity)
        flush_batch();

    std::vector<WGPUBindGroupLayoutEntry> layout;
    std::vector<WGPUBindGroupEntry> entries;
    std::vector<uint32_t> key = {(uint32_t)(uintptr_t)m, (uint32_t)((uintptr_t)m >> 32)};
    std::vector<BufferRef> written;
    std::vector<std::pair<uint32_t, BufferRef>> storage;   // (binding, range) of each storage descriptor
    auto buffer_entry = [&](uint32_t b, WGPUBufferBindingType type, WGPUBuffer buf, uint64_t off, uint64_t size) {
        WGPUBindGroupLayoutEntry e{};
        e.binding = b;
        e.visibility = WGPUShaderStage_Compute;
        e.buffer.type = type;
        layout.push_back(e);
        WGPUBindGroupEntry g{};
        g.binding = b;
        g.buffer = buf;
        g.offset = off;
        g.size = size;
        entries.push_back(g);
    };
    // binding 0: the uniform block (as the WGSL backend lays it out: rescale, swizzles, the constant buffers that fit)
    const bool indirect = info.uses_global_memory || info.uses_cbuf_indirect;
    std::array<uint32_t, 18> base{};
    std::array<bool, 18> in_ub{};
    uint32_t ub_size = 4 + 2 * (uint32_t)info.texture_descriptors.size();
    if (!indirect)
        for (const auto& d : info.constant_buffer_descriptors) {
            const uint32_t size = std::max(Common::DivCeil(info.constant_buffer_used_sizes[d.index], 16U), 1U);
            if (ub_size + size > 0x1000) continue;
            in_ub[d.index % 18] = true;
            base[d.index % 18] = ub_size;
            ub_size += size;
        }
    const uint64_t ub = stream_ro.alloc((uint64_t)ub_size * 16, dev.limits.minUniformBufferOffsetAlignment);
    if (ub == UINT64_MAX) return count("cs_space");
    uint8_t* block = stream_ro.at(ub);
    std::memset(block, 0, 64);
    const float scaling_z = 1.0f;
    std::memcpy(block + 8, &scaling_z, 4);
    for (const auto& d : info.constant_buffer_descriptors) {
        if (!in_ub[d.index % 18]) continue;
        const auto& cb = l.cbufs[d.index % 8];
        const uint64_t size = (uint64_t)std::max(Common::DivCeil(info.constant_buffer_used_sizes[d.index], 16U), 1U) * 16;
        const uint64_t n = cb.enabled ? std::min<uint64_t>(size, cb.size) : 0;
        uint8_t* dst = block + (uint64_t)base[d.index % 18] * 16;
        if (n) gpu_read(cb.address, dst, n);
        if (n < size) std::memset(dst + n, 0, size - n);
        count("up_cbuf", (int64_t)size);
    }
    buffer_entry(0, WGPUBufferBindingType_Uniform, stream_ro.buffer, ub, (uint64_t)ub_size * 16);
    uint32_t b = 1;
    for (const auto& d : info.constant_buffer_descriptors) {
        if (in_ub[d.index % 18]) continue;
        const auto& cb = l.cbufs[d.index % 8];
        const uint64_t size = std::clamp<uint64_t>((cb.enabled ? cb.size : 0) + 15 & ~15ull, 16, 0x10000);
        const uint64_t off = stream_ro.alloc(size, dev.limits.minStorageBufferOffsetAlignment);
        if (off == UINT64_MAX) return count("cs_space");
        const uint64_t n = cb.enabled ? std::min<uint64_t>(size, cb.size) : 0;
        if (n) gpu_read(cb.address, stream_ro.at(off), n);
        if (n < size) std::memset(stream_ro.at(off + n), 0, size - n);
        count("up_cbuf_storage", (int64_t)size);
        buffer_entry(b, WGPUBufferBindingType_ReadOnlyStorage, stream_ro.buffer, off, size);
        b += d.count;
    }
    for (const auto& d : info.storage_buffers_descriptors) {
        const auto& cb = l.cbufs[d.cbuf_index % 8];
        // the program reads from the address aligned down to the storage offset alignment (the frontend's
        // min_ssbo_alignment): the binding starts there
        const uint64_t raw_va = gpu_get<uint64_t>(cb.address + d.cbuf_offset);
        const uint64_t va = raw_va & ~(uint64_t)(dev.limits.minStorageBufferOffsetAlignment - 1);
        uint64_t size = gpu_get<uint32_t>(cb.address + d.cbuf_offset + 8) + (raw_va - va);
        size = std::clamp<uint64_t>((size + 3) & ~3ull, 16, 16ull << 20);
        BufferRef ref = guest_buffer(va, size);
        if (!ref.buffer || ref.offset % dev.limits.minStorageBufferOffsetAlignment) {
            // not bindable in place: a copy (what the dispatch writes there is lost)
            count("cs_ssbo_copied");
            const uint64_t off = stream_rw.alloc(size, dev.limits.minStorageBufferOffsetAlignment);
            if (off == UINT64_MAX) return count("cs_space");
            gpu_read(va, stream_rw.at(off), size);
            ref = {stream_rw.buffer, off, size};
            if (d.is_written) writeback_after_batch(off, size, va);
        } else if (d.is_written) {
            written.push_back(ref);
            note_dispatch_write(va, size);
        }
        // RX_CS_WATCH=<GPU VA hex> (debugging): dispatches that write storage covering it are logged
        if (static const uint64_t watch = getenv("RX_CS_WATCH") ? strtoull(getenv("RX_CS_WATCH"), nullptr, 16) : 0;
            (watch && d.is_written && va <= watch && watch < va + size) || (d.is_written && guest_watch_hit(va, size))) {
            static int logged;
            if (logged++ < 100)
                hos_log("rx: dispatch %#llx grid %u,%u,%u writes %#llx+%#llx (watch %#llx)",
                        (unsigned long long)(l.code_base + l.program_start), l.grid[0], l.grid[1], l.grid[2],
                        (unsigned long long)va, (unsigned long long)size, (unsigned long long)watch);
        }
        storage.push_back({b, ref});
        b += d.count;
    }
    std::array<uint8_t, 64> alias{};
    std::array<uint32_t, 64> alias_words{};
    std::vector<uint64_t> end;
    if (alias_overlapping_storage(storage, end, alias, alias_words)) {
        m = compute_module(l, &alias, &alias_words);
        if (!m) return count("cs_skipped");
        key[0] = (uint32_t)(uintptr_t)m;
        key[1] = (uint32_t)((uintptr_t)m >> 32);
        count("cs_ssbo_aliased");
    }
    for (size_t j = 0; j < storage.size(); j++)
        if (j >= 64 || !alias[j])
            buffer_entry(storage[j].first, WGPUBufferBindingType_Storage, storage[j].second.buffer,
                         storage[j].second.offset, end[j] - storage[j].second.offset);
    uint32_t t_index = 0;
    for (const auto& d : info.texture_descriptors) {
        const uint32_t handle = read_handle(l, d);
        const BoundTexture t = texture_for_handle(*l.tex_regs, handle, d);
        // the header's component swizzle and the sampler's border, in the uniform block
        const auto tic = read_tic(*l.tex_regs, Tegra::Texture::TexturePair(handle, l.linked_tsc).first);
        texture_slots(block + (uint64_t)(4 + 2 * t_index++) * 16, tic, read_tsc_for_dump(*l.tex_regs, handle));
        WGPUBindGroupLayoutEntry tl{};
        tl.binding = b;
        tl.visibility = WGPUShaderStage_Compute;
        tl.texture.sampleType = t.kind == TexKind::Float               ? WGPUTextureSampleType_Float
                                : t.kind == TexKind::UnfilterableFloat ? WGPUTextureSampleType_UnfilterableFloat
                                : t.kind == TexKind::Depth             ? WGPUTextureSampleType_Depth
                                : t.kind == TexKind::Uint              ? WGPUTextureSampleType_Uint
                                                                       : WGPUTextureSampleType_Sint;
        tl.texture.viewDimension = view_dimension(d.type);
        tl.texture.multisampled = d.is_multisample;
        layout.push_back(tl);
        WGPUBindGroupLayoutEntry sl{};
        sl.binding = b + 1;
        sl.visibility = WGPUShaderStage_Compute;
        sl.sampler.type = t.kind == TexKind::Depth             ? WGPUSamplerBindingType_Comparison
                          : t.kind == TexKind::UnfilterableFloat ? WGPUSamplerBindingType_NonFiltering
                                                                 : WGPUSamplerBindingType_Filtering;
        layout.push_back(sl);
        WGPUBindGroupEntry te{};
        te.binding = b;
        te.textureView = t.view;
        entries.push_back(te);
        WGPUBindGroupEntry se{};
        se.binding = b + 1;
        se.sampler = t.sampler;
        entries.push_back(se);
        key.push_back((uint32_t)t.kind);
        b += 2;
    }
    {
        const uint64_t lh = hash_bytes(layout.data(), layout.size() * sizeof(WGPUBindGroupLayoutEntry));
        key.insert(key.end(), {(uint32_t)lh, (uint32_t)(lh >> 32)});
    }

    const uint64_t h = hash_bytes(key.data(), key.size() * 4);
    auto it = pipelines.find(h);
    if (it == pipelines.end()) {
        auto p = std::make_unique<ComputePipeline>();
        WGPUBindGroupLayoutDescriptor ld{};
        ld.entryCount = layout.size();
        ld.entries = layout.data();
        scope_push();
        p->layout = wgpuDeviceCreateBindGroupLayout(dev.device, &ld);
        WGPUPipelineLayoutDescriptor pld{};
        pld.bindGroupLayoutCount = 1;
        pld.bindGroupLayouts = &p->layout;
        WGPUPipelineLayout pl = wgpuDeviceCreatePipelineLayout(dev.device, &pld);
        WGPUComputePipelineDescriptor cd{};
        cd.layout = pl;
        cd.compute.module = m->module;
        cd.compute.entryPoint = sv("main");
        p->pipeline = wgpuDeviceCreateComputePipeline(dev.device, &cd);
        const std::string error = scope_pop();
        if (RX_WEB) pipecache_record_compute(m->source(), layout);
        wgpuPipelineLayoutRelease(pl);
        if (!error.empty()) {
            log_limited("cs_pipeline", 20, "rx: compute pipeline not created: %s", error.c_str());
            p->failed = true;
        }
        count("pipelines");
        it = pipelines.emplace(h, std::move(p)).first;
    }
    if (it->second->failed) return count("cs_skipped");

    WGPUBindGroupDescriptor bd{};
    bd.layout = it->second->layout;
    bd.entryCount = entries.size();
    bd.entries = entries.data();
    // (the threaded web build: made where the pass is replayed, rx_record.cpp cp_set_bind_group_desc)
    WGPUBindGroup group = RX_WEB_MT ? nullptr : checked_bind_group(bd, "dispatch");
    if (!RX_WEB_MT && !group) return count("cs_bind_invalid");
    end_render_pass("pe_compute");
    WGPUComputePassEncoder pass = ce_begin_compute_pass(encoder());
    cp_set_pipeline(pass, it->second->pipeline);
    if (group) cp_set_bind_group(pass, 0, group, 0, nullptr);
    else cp_set_bind_group_desc(pass, 0, bd.layout, entries.data(), entries.size());
    const uint32_t max = dev.limits.maxComputeWorkgroupsPerDimension;
    cp_dispatch(pass, std::min(l.grid[0], max), std::min(l.grid[1], max),
                                             std::min(l.grid[2], max));
    cp_end(pass);
    if (group) wgpuBindGroupRelease(group);
    for (const BufferRef& w : written) buffer_cache_gpu_written(w);
    if (!written.empty()) dispatch_write_epoch++;
    count("dispatches");
    if (frame_dump_active()) {
        char b[512];
        int n = std::snprintf(b, sizeof b, "dispatch %#llx grid %u,%u,%u block %u,%u,%u: %zu ssbo (%zu written), %zu tex",
                              (unsigned long long)(l.code_base + l.program_start), l.grid[0], l.grid[1], l.grid[2],
                              l.block[0], l.block[1], l.block[2], info.storage_buffers_descriptors.size(),
                              written.size(), info.texture_descriptors.size());
        for (const auto& d : info.storage_buffers_descriptors) {
            const auto& cb = l.cbufs[d.cbuf_index % 8];
            const uint64_t va = gpu_get<uint64_t>(cb.address + d.cbuf_offset);
            const uint32_t size = gpu_get<uint32_t>(cb.address + d.cbuf_offset + 8);
            if (n < 480) n += std::snprintf(b + n, sizeof b - n, " %s%#llx+%#x", d.is_written ? "w" : "r",
                                            (unsigned long long)va, size);
        }
        dump_draw(b);
    }
}

/// (macOS port, as the Vulkan back end's upscale_stub, gpu/vk/vk_draw.cpp) A compute program that does not translate here
/// (OoT's movie upscaler networks: HMMA matrix multiplies, whose emulation takes Metal minutes to compile) is replaced by
/// what the network's last pass makes: its input sampled linearly into its output, the picture a little softer. The
/// output is the largest 2D image the dispatch has bound; the input the largest texture it samples that is no image of
/// it and smaller than the output (an earlier pass of the network: none, and nothing is done). RX_NO_UPSCALE_STUB=1:
/// left out, as before. The first decision for each program is logged.
bool nvn_upscale_stub(const NvnState& s) {
    static const bool off = getenv("RX_NO_UPSCALE_STUB") != nullptr;
    // (only a network's pass: other programs that do not translate keep image bindings an earlier pass left, and the
    // stand-in copied the scene over the game's final picture with them: the white 3D after the opening)
    if (off || !nvn_cs_matrix(s)) return false;
    struct Pick {
        uint64_t handle = 0, area = 0;
        nvn_tex t{};
        nvn_view v{};
        bool has_view = false;
        uint32_t format() const { return has_view && v.set_format ? v.format : t.d.format; }   // (as the shader sees it)
    };
    Pick out, in;
    uint64_t img_gpu[32];
    int nimg = 0;
    for (int i = 0; i < 32; i++) {
        Pick p;
        if (!s.img[5][i] || !nvn_texture_of((int)(s.img[5][i] & 0xFFFFF), &p.t, &p.v, &p.has_view)) continue;
        img_gpu[nimg++] = p.t.gpu;
        if (p.t.d.depth > 1) continue;
        p.handle = s.img[5][i], p.area = (uint64_t)p.t.d.width * p.t.d.height;
        if (p.area > out.area) out = p;
    }
    // (as gpu/vk/vk_draw.cpp taau_stand_in) OoT's gameplay TAAU network: its output R11G11B10F, its first texture the scene
    // colour in RGB9E5 that its first passes made, as the square root (the network's perceptual encoding, which its output
    // undoes). That texture upscaled and squared; the scene's HDR colour beside it (same format as the output) upscaled as
    // it was came out all white
    bool squared = false;
    if (out.area && out.format() == NVN_FORMAT_R11G11B10F && s.tex[5][0]) {
        Pick p;
        if (nvn_texture_of((int)(s.tex[5][0] & 0xFFFFF), &p.t, &p.v, &p.has_view) && p.format() == NVN_FORMAT_RGB9E5F &&
            p.t.d.depth <= 1 && p.t.d.width >= 64 && p.t.d.height >= 64) {
            p.handle = s.tex[5][0], p.area = (uint64_t)p.t.d.width * p.t.d.height;
            in = p, squared = true;
        }
    }
    // (the network's own pass wrote that colour as packed RGB9E5 bits through an R32UI image at the same memory: WebGPU
    // cannot view one as the other, and the texture came out as the gray placeholder. That image read as words, decoded)
    Image* packed = nullptr;
    if (squared)
        for (int i = 0; i < 32 && !packed; i++) {
            Pick p;
            if (!s.img[5][i] || !nvn_texture_of((int)(s.img[5][i] & 0xFFFFF), &p.t, &p.v, &p.has_view)) continue;
            if (p.t.gpu != in.t.gpu || p.t.d.depth > 1) continue;
            Image* im = nvn_target(&p.t, p.has_view ? &p.v : nullptr);
            if (im && im->format == WGPUTextureFormat_R32Uint && im->rendered) packed = im;
        }
    if (out.area && !squared)
        for (int i = 0; i < 128; i++) {
            Pick p;
            if (!s.tex[5][i] || !nvn_texture_of((int)(s.tex[5][i] & 0xFFFFF), &p.t, &p.v, &p.has_view)) continue;
            if (p.t.d.depth > 1 || p.t.d.target != out.t.d.target) continue;
            // (the network's colour input, as its output: OoT's earlier passes sample the movie's chroma plane, RG8,
            // which upscaled into the output gave junk until the last pass)
            // (and a picture: the network's 4x4 placeholders are not one)
            if (p.format() != out.format() || p.t.d.width < 64 || p.t.d.height < 64) continue;
            if (std::find(img_gpu, img_gpu + nimg, p.t.gpu) != img_gpu + nimg) continue;   // (the network's own buffers)
            p.handle = s.tex[5][i], p.area = (uint64_t)p.t.d.width * p.t.d.height;
            if (p.area > in.area) in = p;
        }
    Image* dst = out.area ? nvn_target(&out.t, out.has_view ? &out.v : nullptr) : nullptr;
    const char* store = dst ? wgsl_storage_format(dst->format) : nullptr;
    BoundTexture src;
    if (dst && store && in.area && in.area < out.area && !packed) {
        Shader::TextureDescriptor d{};
        d.type = Shader::TextureType::Color2D;
        d.count = 1;
        src = nvn_texture(s, in.handle, 0, d);
    }
    const bool ok = dst && store && in.area && in.area < out.area &&
                    (packed ? true : src.view && src.kind == TexKind::Float);
    {
        static std::unordered_set<uint64_t> logged;
        if (logged.size() < 64 && logged.insert(s.prog.serial).second)
            hos_log("rx: compute program (serial %llu) does not run here (grid %u,%u,%u): %s %ux%u (NVN format %#x) -> %ux%u "
                    "(NVN format %#x, GPU %llx, guest %llx, image GPU %llx guest %llx)", (unsigned long long)s.prog.serial,
                    s.grid[0], s.grid[1], s.grid[2],
                    ok ? (squared ? "its scene colour (squared) upscaled into its output instead, as gameplay's TAAU network:"
                                  : "its input upscaled linearly into its output instead,") : "left out, no upscale for it:",
                    in.t.d.width, in.t.d.height, in.format(), out.t.d.width, out.t.d.height, out.format(),
                    (unsigned long long)out.t.gpu, (unsigned long long)nvn_gpu_guest(out.t.gpu),
                    (unsigned long long)(dst ? dst->gpu_va : 0), (unsigned long long)(dst ? dst->guest : 0));
    }
    if (!ok) return false;
    struct Stub {
        WGPUComputePipeline pipeline = nullptr;
        WGPUBindGroupLayout layout = nullptr;
    };
    static std::unordered_map<uint32_t, Stub> stubs;   // (per output format, and squared or not)
    static WGPUSampler linear = [] {
        WGPUSamplerDescriptor sd{};
        sd.addressModeU = sd.addressModeV = sd.addressModeW = WGPUAddressMode_ClampToEdge;
        sd.magFilter = sd.minFilter = WGPUFilterMode_Linear;
        sd.mipmapFilter = WGPUMipmapFilterMode_Nearest;
        sd.lodMaxClamp = 0.25f;
        sd.maxAnisotropy = 1;
        return wgpuDeviceCreateSampler(dev.device, &sd);
    }();
    const uint32_t mode = packed ? 2u : squared ? 1u : 0u;   // (0 plain, 1 squared, 2 packed RGB9E5 words, squared)
    Stub& st = stubs[(uint32_t)dst->format << 2 | mode];
    if (!st.pipeline) {
        std::string wgsl = packed ? "@group(0) @binding(0) var src: texture_2d<u32>;\n" : "@group(0) @binding(0) var src: texture_2d<f32>;\n";
        wgsl += "@group(0) @binding(1) var samp: sampler;\n"
                           "@group(0) @binding(2) var dst: texture_storage_2d<";
        wgsl += store;
        wgsl += ", write>;\n"
                "@compute @workgroup_size(8, 8) fn main(@builtin(global_invocation_id) g: vec3<u32>) {\n"
                "  let size = textureDimensions(dst);\n"
                "  if (g.x >= size.x || g.y >= size.y) { return; }\n"
                "  let uv = (vec2<f32>(g.xy) + 0.5) / vec2<f32>(size);\n"
                "";
        if (packed)   // (RGB9E5: three 9-bit mantissas and a shared exponent, bias 15; bilinear by hand, as words do not filter)
            wgsl += "  let n = vec2<i32>(textureDimensions(src));\n"
                    "  let q = uv * vec2<f32>(n) - 0.5;\n"
                    "  let i0 = vec2<i32>(floor(q));\n"
                    "  let f = q - floor(q);\n"
                    "  let a = e5_(src, clamp(i0, vec2<i32>(0), n - 1));\n"
                    "  let b = e5_(src, clamp(i0 + vec2<i32>(1, 0), vec2<i32>(0), n - 1));\n"
                    "  let c0 = e5_(src, clamp(i0 + vec2<i32>(0, 1), vec2<i32>(0), n - 1));\n"
                    "  let d = e5_(src, clamp(i0 + vec2<i32>(1, 1), vec2<i32>(0), n - 1));\n"
                    "  let c = mix(mix(a, b, f.x), mix(c0, d, f.x), f.y);\n"
                    "  textureStore(dst, vec2<i32>(g.xy), vec4<f32>(c * c, 1.0));\n}\n"
                    "fn e5_(t: texture_2d<u32>, p: vec2<i32>) -> vec3<f32> {\n"
                    "  let v = textureLoad(t, p, 0).x;\n"
                    "  let m = vec3<f32>(f32(v & 511u), f32((v >> 9u) & 511u), f32((v >> 18u) & 511u));\n"
                    "  return m * exp2(f32(v >> 27u) - 24.0);\n}\n";
        else
            wgsl += "  let c = textureSampleLevel(src, samp, uv, 0.0);\n" +
                    std::string(squared ? "  textureStore(dst, vec2<i32>(g.xy), vec4<f32>(c.rgb * c.rgb, 1.0));\n}\n"
                                        : "  textureStore(dst, vec2<i32>(g.xy), c);\n}\n");
        WGPUShaderSourceWGSL code{};
        code.chain.sType = WGPUSType_ShaderSourceWGSL;
        code.code = sv(wgsl.c_str());
        WGPUShaderModuleDescriptor md{};
        md.nextInChain = &code.chain;
        WGPUBindGroupLayoutEntry e[3]{};
        e[0].binding = 0, e[0].visibility = WGPUShaderStage_Compute;
        e[0].texture.sampleType = packed ? WGPUTextureSampleType_Uint : WGPUTextureSampleType_Float;
        e[0].texture.viewDimension = WGPUTextureViewDimension_2D;
        e[1].binding = 1, e[1].visibility = WGPUShaderStage_Compute;
        e[1].sampler.type = WGPUSamplerBindingType_Filtering;
        e[2].binding = 2, e[2].visibility = WGPUShaderStage_Compute;
        e[2].storageTexture.access = WGPUStorageTextureAccess_WriteOnly;
        e[2].storageTexture.format = dst->format;
        e[2].storageTexture.viewDimension = WGPUTextureViewDimension_2D;
        WGPUBindGroupLayoutDescriptor ld{};
        ld.entryCount = 3, ld.entries = e;
        scope_push();
        WGPUShaderModule mod = wgpuDeviceCreateShaderModule(dev.device, &md);
        st.layout = wgpuDeviceCreateBindGroupLayout(dev.device, &ld);
        WGPUPipelineLayoutDescriptor pld{};
        pld.bindGroupLayoutCount = 1, pld.bindGroupLayouts = &st.layout;
        WGPUPipelineLayout pl = wgpuDeviceCreatePipelineLayout(dev.device, &pld);
        WGPUComputePipelineDescriptor cd{};
        cd.layout = pl;
        cd.compute.module = mod;
        cd.compute.entryPoint = sv("main");
        st.pipeline = wgpuDeviceCreateComputePipeline(dev.device, &cd);
        const std::string error = scope_pop();
        wgpuPipelineLayoutRelease(pl);
        wgpuShaderModuleRelease(mod);
        if (!error.empty()) {
            log_limited("cs_upscale_stub", 4, "rx: the upscale stand-in's pipeline was not created: %s", error.c_str());
            if (st.pipeline) wgpuComputePipelineRelease(st.pipeline);
            st.pipeline = nullptr;
            return false;
        }
    }
    WGPUBindGroupEntry g[3]{};
    g[0].binding = 0, g[0].textureView = packed ? packed->attachment : src.view;
    g[1].binding = 1, g[1].sampler = linear;
    g[2].binding = 2, g[2].textureView = dst->attachment;
    WGPUBindGroupDescriptor bd{};
    bd.layout = st.layout, bd.entryCount = 3, bd.entries = g;
    WGPUBindGroup group = checked_bind_group(bd, "upscale stub");
    if (!group) return false;
    end_render_pass("pe_compute");
    WGPUComputePassEncoder pass = ce_begin_compute_pass(encoder());
    cp_set_pipeline(pass, st.pipeline);
    cp_set_bind_group(pass, 0, group, 0, nullptr);
    cp_dispatch(pass, (dst->width + 7) / 8, (dst->height + 7) / 8, 1);
    cp_end(pass);
    wgpuBindGroupRelease(group);
    dst->rendered = true, dst->version++;
    if (frame_dump_active()) {   // (RX_DUMP_SAMPLED=1: both pictures saved with the frame)
        char line[160];
        std::snprintf(line, sizeof line, "upscale stand-in: %ux%u (%c) -> %ux%u", packed ? packed->width : src.width,
                      packed ? packed->height : src.height, packed ? 'p' : src.source, dst->width, dst->height);
        dump_draw(line);
        dump_sampled(dst);
        if (packed) dump_sampled(packed);
        else if (src.image) dump_sampled(src.image);
    }
    return true;
}

} // namespace

// ---- the native NVN layer's dispatches: the bound program's compute stage, its banks from NVN state (c0: the
// driver's storage buffer table at 0x310, the grid at 0x200, the uniform buffer table at 0x210; c2: handles) ----
void nvn_dispatch(NvnState& s, uint32_t x, uint32_t y, uint32_t z, uint64_t indirect_va) {
    static const bool off = getenv("RX_NO_COMPUTE") != nullptr;   // (debugging)
    if (off || !s.render_enabled) return;
    // (mkw-recomp) an indirect dispatch: the grid stays on the GPU, where the dispatch that wrote it left it (in the
    // buffer cache); copied into c0 there for the program. Reading it back first stopped the GPU 6 times a frame
    BufferRef args;
    if (indirect_va) {
        args = guest_buffer(indirect_va, 12);
        if (!args.buffer || args.offset % 4) return count("cs_indirect_unmapped");
        x = y = z = 1;
    }
    s.grid[0] = x, s.grid[1] = y, s.grid[2] = z;
    if (!x || !y || !z) return count("cs_empty");
    ShaderModule* m = nvn_compute_module(s);
    if (frame_dump_active()) {   // (macOS port) the dispatch's bound images and textures: which pass of a network reads what
        char line[1600];
        int n = std::snprintf(line, sizeof line, "nvn dispatch program serial %llu key %016llx %s grid %u,%u,%u | img:",
                              (unsigned long long)s.prog.serial, (unsigned long long)nvn_cs_key(s),
                              m ? "translated" : "NOT translated", x, y, z);
        const auto add = [&](const char* kind, int i, uint64_t h) {
            nvn_tex t{};
            nvn_view v{};
            bool hv = false;
            if (!h || n > 1500 || !nvn_texture_of((int)(h & 0xFFFFF), &t, &v, &hv)) return;
            n += std::snprintf(line + n, sizeof line - n, " %s%d=%llx:%ux%ux%u/f%#x", kind, i, (unsigned long long)t.gpu,
                               t.d.width, t.d.height, t.d.depth, hv && v.set_format ? v.format : t.d.format);
        };
        for (int i = 0; i < 32; i++) add("i", i, s.img[5][i]);
        n += std::snprintf(line + n, sizeof line - n, " | tex:");
        for (int i = 0; i < 128; i++) add("t", i, s.tex[5][i]);
        dump_draw(line);
    }
    if (!m) return count(nvn_upscale_stub(s) ? "cs_upscale_stub" : "cs_skipped");
    const Shader::Info& info = m->info;
    if (!info.image_buffer_descriptors.empty() || !info.texture_buffer_descriptors.empty()) {
        log_limited("nvn_cs_images", 8, "rx: an NVN compute program uses image or texture buffers: not dispatched yet");
        return count("cs_image_buffers");
    }
    // (mkw-recomp) images: the render targets of the textures their handles name (2D for now), bound as storage
    // textures, with the program translated for their formats
    std::array<uint16_t, 16> img_formats{};
    std::vector<Image*> img_targets;
    if (!info.image_descriptors.empty()) {
        NvnBanks ib;
        ib.c1 = nvn_c1(s, 5);
        for (size_t i = 0; i < info.image_descriptors.size(); i++) {
            const auto& d = info.image_descriptors[i];
            uint32_t handle = 0;
            ib.read(s, 5, d.cbuf_index, d.cbuf_offset, &handle, 4);
            nvn_tex t{};
            nvn_view v{};
            bool has_view = false;
            Image* im = nullptr;
            const bool layered = d.type == Shader::TextureType::ColorArray2D, volume = d.type == Shader::TextureType::Color3D;
            if (i < img_formats.size() && d.count == 1 && handle && nvn_texture_of((int)(handle & 0xFFFFF), &t, &v, &has_view)) {
                if (d.type == Shader::TextureType::Color2D) im = nvn_target(&t, has_view ? &v : nullptr);
                else if (layered || volume) im = nvn_storage_image(&t, has_view ? &v : nullptr, volume);
            }
            if (!im || !wgsl_storage_format(im->format)) {
                log_limited("nvn_cs_image_kind", 24, "rx: NVN compute image %zu (type %d, handle %#x, NVN format %#x %ux%ux%u "
                            "target %d): %s (WebGPU format %#x)", i, (int)d.type, handle,
                            has_view && v.set_format ? v.format : t.d.format, t.d.width, t.d.height, t.d.depth,
                            t.d.target, im ? "its format is not a storage format" : "no 2D target for it",
                            im ? (unsigned)im->format : 0u);
                return count("cs_images");
            }
            img_formats[i] = (uint16_t)im->format;
            img_targets.push_back(im);
        }
        m = nvn_compute_module(s, nullptr, nullptr, &img_formats);
        if (!m) return count("cs_skipped");
    }

    const uint64_t estimate = 64 * 1024 + info.constant_buffer_descriptors.size() * 0x10100 +
                              info.texture_descriptors.size() * 256 + info.storage_buffers_descriptors.size() * (1 << 20);
    if (stream_ro.used + estimate > stream_ro.capacity || stream_rw.used + (8u << 20) > stream_rw.capacity)
        flush_batch();

    NvnBanks banks;
    banks.c1 = nvn_c1(s, 5);
    std::vector<WGPUBindGroupLayoutEntry> layout;
    std::vector<WGPUBindGroupEntry> entries;
    std::vector<uint32_t> key = {(uint32_t)(uintptr_t)m, (uint32_t)((uintptr_t)m >> 32)};
    std::vector<BufferRef> written;
    std::vector<std::pair<uint32_t, BufferRef>> storage;   // (binding, range) of each storage descriptor
    auto buffer_entry = [&](uint32_t b, WGPUBufferBindingType type, WGPUBuffer buf, uint64_t off, uint64_t size) {
        WGPUBindGroupLayoutEntry e{};
        e.binding = b;
        e.visibility = WGPUShaderStage_Compute;
        e.buffer.type = type;
        layout.push_back(e);
        WGPUBindGroupEntry g{};
        g.binding = b;
        g.buffer = buf;
        g.offset = off;
        g.size = size;
        entries.push_back(g);
    };
    const bool indirect = info.uses_global_memory || info.uses_cbuf_indirect;
    std::array<uint32_t, 18> base{};
    std::array<bool, 18> in_ub{};
    uint32_t ub_size = 4 + 2 * (uint32_t)info.texture_descriptors.size();
    if (!indirect)
        for (const auto& d : info.constant_buffer_descriptors) {
            const uint32_t size = std::max(Common::DivCeil(info.constant_buffer_used_sizes[d.index], 16U), 1U);
            if (ub_size + size > 0x1000) continue;
            in_ub[d.index % 18] = true;
            base[d.index % 18] = ub_size;
            ub_size += size;
        }
    const uint64_t ub = stream_ro.alloc((uint64_t)ub_size * 16, dev.limits.minUniformBufferOffsetAlignment);
    if (ub == UINT64_MAX) return count("cs_space");
    uint8_t* block = stream_ro.at(ub);
    std::memset(block, 0, 64);
    const float scaling_z = 1.0f;
    std::memcpy(block + 8, &scaling_z, 4);
    for (const auto& d : info.constant_buffer_descriptors) {
        if (!in_ub[d.index % 18]) continue;
        const uint64_t size = (uint64_t)std::max(Common::DivCeil(info.constant_buffer_used_sizes[d.index], 16U), 1U) * 16;
        banks.read(s, 5, d.index, 0, block + (uint64_t)base[d.index % 18] * 16, size);
        count("up_cbuf", (int64_t)size);
    }
    buffer_entry(0, WGPUBufferBindingType_Uniform, stream_ro.buffer, ub, (uint64_t)ub_size * 16);
    uint64_t c0_at = in_ub[0] ? ub + (uint64_t)base[0] * 16 : UINT64_MAX;   // (where c0's grid goes, in stream_ro)
    uint32_t b = 1;
    for (const auto& d : info.constant_buffer_descriptors) {
        if (in_ub[d.index % 18]) continue;
        uint8_t probe[4];
        const uint64_t bank = banks.read(s, 5, d.index, 0, probe, 4);
        const uint64_t size = std::clamp<uint64_t>(bank + 15 & ~15ull, 16, 0x10000);
        const uint64_t off = stream_ro.alloc(size, dev.limits.minStorageBufferOffsetAlignment);
        if (off == UINT64_MAX) return count("cs_space");
        banks.read(s, 5, d.index, 0, stream_ro.at(off), size);
        count("up_cbuf_storage", (int64_t)size);
        if (d.index == 0) c0_at = off;
        buffer_entry(b, WGPUBufferBindingType_ReadOnlyStorage, stream_ro.buffer, off, size);
        b += d.count;
    }
    for (const auto& d : info.storage_buffers_descriptors) {
        uint64_t raw_va = 0;
        uint32_t raw_size = 0;
        banks.read(s, 5, d.cbuf_index, d.cbuf_offset, &raw_va, 8);
        banks.read(s, 5, d.cbuf_index, d.cbuf_offset + 8, &raw_size, 4);
        const uint64_t va = raw_va & ~(uint64_t)(dev.limits.minStorageBufferOffsetAlignment - 1);
        uint64_t size = (uint64_t)raw_size + (raw_va - va);
        size = std::clamp<uint64_t>((size + 3) & ~3ull, 16, 16ull << 20);
        BufferRef ref = raw_va ? guest_buffer(va, size) : BufferRef{};
        if (!ref.buffer || ref.offset % dev.limits.minStorageBufferOffsetAlignment) {
            count("cs_ssbo_copied");
            const uint64_t off = stream_rw.alloc(size, dev.limits.minStorageBufferOffsetAlignment);
            if (off == UINT64_MAX) return count("cs_space");
            if (raw_va) gpu_read(va, stream_rw.at(off), size);
            else std::memset(stream_rw.at(off), 0, size);
            ref = {stream_rw.buffer, off, size};
            if (d.is_written && raw_va) writeback_after_batch(off, size, va);
        } else if (d.is_written) {
            written.push_back(ref);
            note_dispatch_write(va, size);
        }
        storage.push_back({b, ref});
        b += d.count;
    }
    std::array<uint8_t, 64> alias{};
    std::array<uint32_t, 64> alias_words{};
    std::vector<uint64_t> end;
    if (alias_overlapping_storage(storage, end, alias, alias_words)) {
        m = nvn_compute_module(s, &alias, &alias_words, &img_formats);
        if (!m) return count("cs_skipped");
        key[0] = (uint32_t)(uintptr_t)m;
        key[1] = (uint32_t)((uintptr_t)m >> 32);
        count("cs_ssbo_aliased");
    }
    for (size_t j = 0; j < storage.size(); j++)
        if (j >= 64 || !alias[j])
            buffer_entry(storage[j].first, WGPUBufferBindingType_Storage, storage[j].second.buffer,
                         storage[j].second.offset, end[j] - storage[j].second.offset);
    uint32_t t_index = 0;
    uint32_t last_own[2] = {0, 0}, own_samplers = 0;   // (at most 16: wgsl_emit_context.cpp SetupTextures)   // (RX_SHARE_SAMPLERS: from the 17th texture on, the sampler of an earlier one, as the WGSL)
    for (const auto& d : info.texture_descriptors) {
        const bool shared_sampler = RX_SHARE_SAMPLERS && own_samplers >= 15 && last_own[d.is_depth ? 1 : 0];
        if (!shared_sampler) last_own[d.is_depth ? 1 : 0] = b + 1, own_samplers++;
        uint32_t primary = 0, secondary = 0;
        banks.read(s, 5, d.cbuf_index, d.cbuf_offset, &primary, 4);
        if (d.has_secondary) banks.read(s, 5, d.secondary_cbuf_index, d.secondary_cbuf_offset, &secondary, 4);
        const uint32_t handle = d.has_secondary ? (primary << d.shift_left) | (secondary << d.secondary_shift_left)
                                                : primary;
        const BoundTexture t = nvn_texture(s, handle, 0, d);
        std::memcpy(block + (uint64_t)(4 + 2 * t_index++) * 16, t.slots, sizeof t.slots);
        WGPUBindGroupLayoutEntry tl{};
        tl.binding = b;
        tl.visibility = WGPUShaderStage_Compute;
        tl.texture.sampleType = t.kind == TexKind::Float               ? WGPUTextureSampleType_Float
                                : t.kind == TexKind::UnfilterableFloat ? WGPUTextureSampleType_UnfilterableFloat
                                : t.kind == TexKind::Depth             ? WGPUTextureSampleType_Depth
                                : t.kind == TexKind::Uint              ? WGPUTextureSampleType_Uint
                                                                       : WGPUTextureSampleType_Sint;
        tl.texture.viewDimension = view_dimension(d.type);
        tl.texture.multisampled = d.is_multisample;
        layout.push_back(tl);
        WGPUBindGroupLayoutEntry sl{};
        sl.binding = b + 1;
        sl.visibility = WGPUShaderStage_Compute;
        sl.sampler.type = t.kind == TexKind::Depth             ? WGPUSamplerBindingType_Comparison
                          : t.kind == TexKind::UnfilterableFloat ? WGPUSamplerBindingType_NonFiltering
                                                                 : WGPUSamplerBindingType_Filtering;
        if (!shared_sampler) layout.push_back(sl);
        WGPUBindGroupEntry te{};
        te.binding = b;
        te.textureView = t.view;
        entries.push_back(te);
        WGPUBindGroupEntry se{};
        se.binding = b + 1;
        se.sampler = t.sampler;
        if (!shared_sampler) entries.push_back(se);
        key.push_back((uint32_t)t.kind);
        b += 2;
    }
    for (size_t i = 0; i < img_targets.size(); i++) {   // (mkw-recomp) the images, after the textures (as the WGSL)
        const auto& d = info.image_descriptors[i];
        WGPUBindGroupLayoutEntry il{};
        il.binding = b;
        il.visibility = WGPUShaderStage_Compute;
        il.storageTexture.access = d.is_written ? (d.is_read ? WGPUStorageTextureAccess_ReadWrite : WGPUStorageTextureAccess_WriteOnly)
                                                : WGPUStorageTextureAccess_ReadOnly;
        il.storageTexture.format = img_targets[i]->format;
        const WGPUTextureViewDimension vdim = d.type == Shader::TextureType::ColorArray2D ? WGPUTextureViewDimension_2DArray
                                             : d.type == Shader::TextureType::Color3D    ? WGPUTextureViewDimension_3D
                                                                                          : WGPUTextureViewDimension_2D;
        il.storageTexture.viewDimension = vdim;
        layout.push_back(il);
        WGPUBindGroupEntry g{};
        g.binding = b;
        g.textureView = vdim == WGPUTextureViewDimension_2D ? img_targets[i]->attachment
                                                             : img_targets[i]->view(vdim, false, 0, 0, 1, img_targets[i]->format);
        entries.push_back(g);
        key.push_back(0x1000u | img_targets[i]->format);
        b += 1;
    }
    {
        const uint64_t lh = hash_bytes(layout.data(), layout.size() * sizeof(WGPUBindGroupLayoutEntry));
        key.insert(key.end(), {(uint32_t)lh, (uint32_t)(lh >> 32)});
    }
    const uint64_t h = hash_bytes(key.data(), key.size() * 4);
    auto it = pipelines.find(h);
    if (it == pipelines.end()) {
        auto p = std::make_unique<ComputePipeline>();
        WGPUBindGroupLayoutDescriptor ld{};
        ld.entryCount = layout.size();
        ld.entries = layout.data();
        scope_push();
        p->layout = wgpuDeviceCreateBindGroupLayout(dev.device, &ld);
        WGPUPipelineLayoutDescriptor pld{};
        pld.bindGroupLayoutCount = 1;
        pld.bindGroupLayouts = &p->layout;
        WGPUPipelineLayout pl = wgpuDeviceCreatePipelineLayout(dev.device, &pld);
        WGPUComputePipelineDescriptor cd{};
        cd.layout = pl;
        cd.compute.module = m->module;
        cd.compute.entryPoint = sv("main");
        p->pipeline = wgpuDeviceCreateComputePipeline(dev.device, &cd);
        const std::string error = scope_pop();
        if (RX_WEB) pipecache_record_compute(m->source(), layout);
        wgpuPipelineLayoutRelease(pl);
        if (!error.empty()) {
            log_limited("cs_pipeline", 20, "rx: NVN compute pipeline not created: %s", error.c_str());
            p->failed = true;
        }
        count("pipelines");
        it = pipelines.emplace(h, std::move(p)).first;
    }
    if (it->second->failed) return count("cs_skipped");
    WGPUBindGroupDescriptor bd{};
    bd.layout = it->second->layout;
    bd.entryCount = entries.size();
    bd.entries = entries.data();
    // (the threaded web build: made where the pass is replayed, rx_record.cpp cp_set_bind_group_desc)
    WGPUBindGroup group = RX_WEB_MT ? nullptr : checked_bind_group(bd, "dispatch");
    if (!RX_WEB_MT && !group) return count("cs_bind_invalid");
    end_render_pass("pe_compute");
    if (args.buffer) {
        if (c0_at != UINT64_MAX) ce_copy_buffer_to_buffer(encoder(), args.buffer, args.offset, stream_ro.buffer, c0_at, 12);
        // (into a buffer of their own: the segment holding them may be bound for writing by this dispatch, and a buffer
        // cannot be both in one dispatch. Slots are reused in turn: each copy runs before its dispatch, in order)
        static WGPUBuffer ring;
        static uint64_t next;
        constexpr uint64_t kSlots = 4096;
        if (!ring) {
            WGPUBufferDescriptor bd{};
            bd.label = sv("rx indirect arguments");
            bd.size = kSlots * 16;
            bd.usage = WGPUBufferUsage_Indirect | WGPUBufferUsage_CopyDst;
            ring = wgpuDeviceCreateBuffer(dev.device, &bd);
        }
        const uint64_t slot = (next++ % kSlots) * 16;
        ce_copy_buffer_to_buffer(encoder(), args.buffer, args.offset, ring, slot, 12);
        args = {ring, slot, 12};
    }
    WGPUComputePassEncoder pass = ce_begin_compute_pass(encoder());
    cp_set_pipeline(pass, it->second->pipeline);
    if (group) cp_set_bind_group(pass, 0, group, 0, nullptr);
    else cp_set_bind_group_desc(pass, 0, bd.layout, entries.data(), entries.size());
    const uint32_t max = dev.limits.maxComputeWorkgroupsPerDimension;
    if (args.buffer) cp_dispatch_indirect(pass, args.buffer, args.offset), count("cs_indirect_gpu");
    else cp_dispatch(pass, std::min(x, max), std::min(y, max), std::min(z, max));
    cp_end(pass);
    if (group) wgpuBindGroupRelease(group);
    for (const BufferRef& w : written) buffer_cache_gpu_written(w);
    if (!written.empty()) dispatch_write_epoch++;
    for (size_t i = 0; i < img_targets.size(); i++)   // (the images it wrote: drawn on the GPU now)
        if (info.image_descriptors[i].is_written) img_targets[i]->rendered = true, img_targets[i]->version++;
    if (!img_targets.empty()) count("cs_with_images");
    count("dispatches");
    if (frame_dump_active()) {   // (which program wrote which memory: what draws later read)
        char line[640];
        int n = std::snprintf(line, sizeof line, "nvn dispatch cs %016llx grid %u,%u,%u: %zu ssbo",
                              (unsigned long long)nvn_cs_key(s), x, y, z, info.storage_buffers_descriptors.size());
        for (const auto& d : info.storage_buffers_descriptors) {
            uint64_t va = 0;
            uint32_t size = 0;
            banks.read(s, 5, d.cbuf_index, d.cbuf_offset, &va, 8);
            banks.read(s, 5, d.cbuf_index, d.cbuf_offset + 8, &size, 4);
            if (n < 600) n += std::snprintf(line + n, sizeof line - n, " %s%#llx+%#x", d.is_written ? "w" : "r",
                                            (unsigned long long)va, size);
        }
        dump_draw(line);
        watch_buffer_after_draw(line);
    }
}
} // namespace rx

using namespace rx;

extern "C" void rx_compute_launch(void* channel, const uint32_t* regs) {
    (void)channel;
    if (!rx_active()) return;
    static const bool off = getenv("RX_NO_COMPUTE") != nullptr;   // (debugging)
    if (off) return;
    std::lock_guard lk(mu);
    // the texture pools sit where the 3D engine has its own (tex_sampler 0x557, tex_header 0x55D)
    static Regs tex_regs;
    ComputeLaunch l = read_launch(regs);
    std::memcpy(&tex_regs.tex_sampler, regs + RX_REG(tex_sampler), sizeof tex_regs.tex_sampler);
    std::memcpy(&tex_regs.tex_header, regs + RX_REG(tex_header), sizeof tex_regs.tex_header);
    tex_regs.sampler_binding =
        l.linked_tsc ? Regs::SamplerBinding::ViaHeaderBinding : Regs::SamplerBinding::Independently;
    l.tex_regs = &tex_regs;
    dispatch(l);
}
