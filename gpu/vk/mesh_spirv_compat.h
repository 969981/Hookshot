// Hookshot submits each indirect record separately in the compute fallback.
// DrawIndex is therefore zero, and is not a legal compute-stage input.
#pragma once
#include "mesh_spirv_rewrite.h"
inline std::vector<uint32_t> MeshSingleDrawSpirv(const std::vector<uint32_t>& code) {
    spvrw::Module m;
    if (!m.Parse(code.data(), code.size())) return {};
    std::set<uint32_t> ids;
    for (const auto& i : m.code)
        if (i.op == spvrw::OpDecorate && i.w.size() >= 3 && i.w[1] == spvrw::Dec_BuiltIn && i.w[2] == 4426)
            ids.insert(i.w[0]);
    if (ids.empty()) return code;
    spvrw::Rewriter rw(m);
    const uint32_t zero = rw.Const(0);
    rw.Commit();
    std::vector<spvrw::Inst> out;
    for (auto& i : m.code) {
        if ((i.op == spvrw::OpVariable && ids.count(i.w[1])) ||
            ((i.op == spvrw::OpDecorate || i.op == spvrw::OpName) && ids.count(i.w[0]))) continue;
        if (i.op == spvrw::OpLoad && ids.count(i.w[2])) {
            i.op = 83; // OpCopyObject, preserving the original result ID and type
            i.w.resize(3); i.w[2] = zero;
        }
        if (i.op == spvrw::OpEntryPoint) {
            const std::string name(reinterpret_cast<const char*>(&i.w[2]));
            const size_t begin = 2 + name.size() / 4 + 1;
            i.w.erase(std::remove_if(i.w.begin() + begin, i.w.end(), [&](uint32_t id) { return ids.count(id); }), i.w.end());
        }
        out.push_back(std::move(i));
    }
    m.code = std::move(out);
    return m.Serialize();
}

// Only original task instructions get the base workgroup. The converter's new
// loads keep local indices into its payload/count buffers. The base is copied
// from the NVN record into counters[2] before dispatch.
inline std::vector<uint32_t> MeshTaskFirstSpirv(const std::vector<uint32_t>& converted,
                                             const std::vector<uint32_t>& original) {
    spvrw::Module input, m;
    if (!input.Parse(original.data(), original.size()) || !m.Parse(converted.data(), converted.size())) return {};
    for (const auto& i : input.code)
        if (i.op == spvrw::OpDecorate && i.w.size() >= 3 && i.w[1] == spvrw::Dec_BuiltIn && i.w[2] == 4426)
            return converted; // translator already added first through its pushed address
    uint32_t wg=0, counter=0;
    std::map<uint32_t,uint32_t> sets, bindings;
    for (const auto& i : m.code) if (i.op == spvrw::OpDecorate && i.w.size() >= 3) {
        if (i.w[1] == spvrw::Dec_BuiltIn && i.w[2] == 26) wg=i.w[0];
        if (i.w[1] == spvrw::Dec_DescriptorSet) sets[i.w[0]]=i.w[2];
        if (i.w[1] == spvrw::Dec_Binding) bindings[i.w[0]]=i.w[2];
    }
    for (const auto& [id,binding] : bindings) if (binding==7 && sets.count(id) && sets[id]==1) counter=id;
    if (!wg || !counter) return converted; // no workgroup input: nothing to offset
    spvrw::Rewriter rw(m);
    const uint32_t u32=rw.U32(), zero=rw.Const(0), two=rw.Const(2);
    const uint32_t word_ptr=rw.FindType(spvrw::OpTypePointer,{spvrw::SC_StorageBuffer,u32});
    rw.Commit();
    std::vector<spvrw::Inst> out;
    for (auto& i : m.code) {
        if (i.op == spvrw::OpLoad && i.w.size() >= 3 && i.w[2]==wg && i.w[1]<input.header[3]) {
            const uint32_t type=i.w[0], result=i.w[1], raw=m.NewId(), ptr=m.NewId(), first=m.NewId(), offset=m.NewId();
            i.w[1]=raw;out.push_back(std::move(i));
            out.push_back({spvrw::OpAccessChain,{word_ptr,ptr,counter,zero,two}});
            out.push_back({spvrw::OpLoad,{u32,first,ptr}});
            out.push_back({80,{type,offset,first,zero,zero}}); // OpCompositeConstruct
            out.push_back({spvrw::OpIAdd,{type,result,raw,offset}});
        } else out.push_back(std::move(i));
    }
    m.code=std::move(out);
    return m.Serialize();
}
