// The layer's own small shaders, assembled directly as SPIR-V 1.3 words.
//
// Vertex shader (silhouette): draws the triangles a converted mesh program wrote.
//   set S, binding 0: vec4 positions[], binding 1: uvec4 indices[] (stride 16), binding 2: uint counts[]
//   spec 0 = max vertices per group, spec 1 = max primitives per group
//   vertex i -> triangle i/3, corner i%3; slot = tri / maxP, p = tri % maxP;
//   p >= counts[slot] -> a zero position (the triangle is degenerate and dropped).
// Fragment shader: writes one flat color to `outputs` color attachments.
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace spvb {

class Builder {
public:
    std::vector<uint32_t> caps, ext, mem, entry, modes, deco, globals, body;
    uint32_t next = 1;
    uint32_t Id() { return next++; }
    static void Op(std::vector<uint32_t>& v, uint32_t op, std::initializer_list<uint32_t> w) {
        v.push_back(static_cast<uint32_t>((w.size() + 1) << 16) | op);
        v.insert(v.end(), w.begin(), w.end());
    }
    static void OpV(std::vector<uint32_t>& v, uint32_t op, const std::vector<uint32_t>& w) {
        v.push_back(static_cast<uint32_t>((w.size() + 1) << 16) | op);
        v.insert(v.end(), w.begin(), w.end());
    }
    static std::vector<uint32_t> Str(const char* s) {
        std::vector<uint32_t> w((std::strlen(s) + 4) / 4, 0);
        std::memcpy(w.data(), s, std::strlen(s));
        return w;
    }
    std::vector<uint32_t> Finish() {
        std::vector<uint32_t> out{0x07230203, 0x00010300, 0x4d4c4c4c, next, 0};
        for (auto* part : {&caps, &ext, &mem, &entry, &modes, &deco, &globals, &body}) {
            out.insert(out.end(), part->begin(), part->end());
        }
        return out;
    }
};

enum : uint32_t {
    kOpEntryPoint = 15, kOpExecutionMode = 16, kOpCapability = 17, kOpMemoryModel = 14, kOpTypeVoid = 19,
    kOpTypeBool = 20, kOpTypeInt = 21, kOpTypeFloat = 22, kOpTypeVector = 23, kOpTypeRuntimeArray = 29,
    kOpTypeStruct = 30, kOpTypePointer = 32, kOpTypeFunction = 33, kOpConstant = 43, kOpConstantComposite = 44,
    kOpSpecConstant = 50, kOpFunction = 54, kOpFunctionEnd = 56, kOpVariable = 59, kOpLoad = 61, kOpStore = 62,
    kOpAccessChain = 65, kOpDecorate = 71, kOpMemberDecorate = 72, kOpVectorExtractDynamic = 77,
    kOpCompositeConstruct = 80, kOpIAdd = 128, kOpIMul = 132, kOpUDiv = 134, kOpUMod = 137, kOpSelect = 169,
    kOpULessThan = 176, kOpLabel = 248, kOpReturn = 253,
};

// Word of binding 7 (the layer's host-written counters buffer) that holds the live depth push.
constexpr uint32_t kZpushIndex = 4095;

// position.z += zpush * position.w (zpush read live from binding 7): pushes the layer's objects a hair
// farther in depth so the game's own "depth equal" colour pass still matches them.
inline uint32_t EmitDepthPush(Builder& b, uint32_t t_f32, uint32_t t_v4f, uint32_t p_word, uint32_t buf, uint32_t c0,
                              uint32_t c_idx, uint32_t t_u32, uint32_t position) {
    enum : uint32_t { OpCompositeExtract = 81, OpCompositeInsert = 82, OpBitcast = 124, OpFAdd = 129, OpFMul = 133 };
    const uint32_t ptr = b.Id(), bits = b.Id(), zp = b.Id(), pz = b.Id(), pw = b.Id(), dz = b.Id(), nz = b.Id(), out = b.Id();
    Builder::Op(b.body, kOpAccessChain, {p_word, ptr, buf, c0, c_idx});
    Builder::Op(b.body, kOpLoad, {t_u32, bits, ptr});
    Builder::Op(b.body, OpBitcast, {t_f32, zp, bits});
    Builder::Op(b.body, OpCompositeExtract, {t_f32, pz, position, 2});
    Builder::Op(b.body, OpCompositeExtract, {t_f32, pw, position, 3});
    Builder::Op(b.body, OpFMul, {t_f32, dz, zp, pw});
    Builder::Op(b.body, OpFAdd, {t_f32, nz, pz, dz});
    Builder::Op(b.body, OpCompositeInsert, {t_v4f, out, nz, position, 2});
    return out;
}

inline std::vector<uint32_t> SilhouetteVertexShader(uint32_t set) {
    Builder b;
    Builder::Op(b.caps, kOpCapability, {1}); // Shader
    Builder::Op(b.mem, kOpMemoryModel, {0, 1}); // Logical GLSL450
    const uint32_t main = b.Id(), vid_var = b.Id(), pos_out = b.Id();
    {
        std::vector<uint32_t> w{0 /*Vertex*/, main};
        auto name = Builder::Str("main");
        w.insert(w.end(), name.begin(), name.end());
        w.push_back(vid_var);
        w.push_back(pos_out);
        Builder::OpV(b.entry, kOpEntryPoint, w);
    }
    const uint32_t t_void = b.Id(), t_fn = b.Id(), t_u32 = b.Id(), t_f32 = b.Id(), t_bool = b.Id();
    const uint32_t t_v4f = b.Id(), t_v4u = b.Id(), t_v4b = b.Id();
    Builder::Op(b.globals, kOpTypeVoid, {t_void});
    Builder::Op(b.globals, kOpTypeFunction, {t_fn, t_void});
    Builder::Op(b.globals, kOpTypeInt, {t_u32, 32, 0});
    Builder::Op(b.globals, kOpTypeFloat, {t_f32, 32});
    Builder::Op(b.globals, kOpTypeBool, {t_bool});
    Builder::Op(b.globals, kOpTypeVector, {t_v4f, t_f32, 4});
    Builder::Op(b.globals, kOpTypeVector, {t_v4u, t_u32, 4});
    Builder::Op(b.globals, kOpTypeVector, {t_v4b, t_bool, 4});
    // Buffers.
    auto buffer = [&](uint32_t elem, uint32_t stride, uint32_t binding, uint32_t* elem_ptr) {
        const uint32_t ra = b.Id(), st = b.Id(), ptr = b.Id(), var = b.Id();
        *elem_ptr = b.Id();
        Builder::Op(b.globals, kOpTypeRuntimeArray, {ra, elem});
        Builder::Op(b.globals, kOpTypeStruct, {st, ra});
        Builder::Op(b.globals, kOpTypePointer, {ptr, 12 /*StorageBuffer*/, st});
        Builder::Op(b.globals, kOpTypePointer, {*elem_ptr, 12, elem});
        Builder::Op(b.globals, kOpVariable, {ptr, var, 12});
        Builder::Op(b.deco, kOpDecorate, {ra, 6 /*ArrayStride*/, stride});
        Builder::Op(b.deco, kOpMemberDecorate, {st, 0, 35 /*Offset*/, 0});
        Builder::Op(b.deco, kOpMemberDecorate, {st, 0, 24 /*NonWritable*/});
        Builder::Op(b.deco, kOpDecorate, {st, 2 /*Block*/});
        Builder::Op(b.deco, kOpDecorate, {var, 34 /*DescriptorSet*/, set});
        Builder::Op(b.deco, kOpDecorate, {var, 33 /*Binding*/, binding});
        return var;
    };
    uint32_t p_v4f, p_v4u, p_u32;
    const uint32_t b_pos = buffer(t_v4f, 16, 0, &p_v4f);
    const uint32_t b_idx = buffer(t_v4u, 16, 1, &p_v4u);
    const uint32_t b_cnt = buffer(t_u32, 4, 2, &p_u32);
    uint32_t p_word7;
    const uint32_t b_live = buffer(t_u32, 4, 7, &p_word7);
    const uint32_t p_in_u32 = b.Id(), p_out_v4f = b.Id();
    Builder::Op(b.globals, kOpTypePointer, {p_in_u32, 1 /*Input*/, t_u32});
    Builder::Op(b.globals, kOpTypePointer, {p_out_v4f, 3 /*Output*/, t_v4f});
    Builder::Op(b.globals, kOpVariable, {p_in_u32, vid_var, 1});
    Builder::Op(b.globals, kOpVariable, {p_out_v4f, pos_out, 3});
    Builder::Op(b.deco, kOpDecorate, {vid_var, 11 /*BuiltIn*/, 42 /*VertexIndex*/});
    Builder::Op(b.deco, kOpDecorate, {pos_out, 11, 0 /*Position*/});
    const uint32_t c0 = b.Id(), c3 = b.Id(), maxv = b.Id(), maxp = b.Id(), f0 = b.Id(), vzero = b.Id(), c_zidx = b.Id();
    Builder::Op(b.globals, kOpConstant, {t_u32, c_zidx, kZpushIndex});
    Builder::Op(b.globals, kOpConstant, {t_u32, c0, 0});
    Builder::Op(b.globals, kOpConstant, {t_u32, c3, 3});
    Builder::Op(b.globals, kOpSpecConstant, {t_u32, maxv, 128});
    Builder::Op(b.globals, kOpSpecConstant, {t_u32, maxp, 64});
    Builder::Op(b.deco, kOpDecorate, {maxv, 1 /*SpecId*/, 0});
    Builder::Op(b.deco, kOpDecorate, {maxp, 1, 1});
    Builder::Op(b.globals, kOpConstant, {t_f32, f0, 0});
    Builder::Op(b.globals, kOpConstantComposite, {t_v4f, vzero, f0, f0, f0, f0});

    const uint32_t label = b.Id(), vi = b.Id(), tri = b.Id(), corner = b.Id(), slot = b.Id(), p = b.Id();
    const uint32_t cp = b.Id(), count = b.Id(), valid = b.Id(), pbase = b.Id(), pi = b.Id(), ip = b.Id();
    const uint32_t tri_idx = b.Id(), e = b.Id(), vbase = b.Id(), vi2 = b.Id(), vsel = b.Id(), pp = b.Id();
    const uint32_t position = b.Id(), valid4 = b.Id(), outp = b.Id();
    Builder::Op(b.body, kOpFunction, {t_void, main, 0, t_fn});
    Builder::Op(b.body, kOpLabel, {label});
    Builder::Op(b.body, kOpLoad, {t_u32, vi, vid_var});
    Builder::Op(b.body, kOpUDiv, {t_u32, tri, vi, c3});
    Builder::Op(b.body, kOpUMod, {t_u32, corner, vi, c3});
    Builder::Op(b.body, kOpUDiv, {t_u32, slot, tri, maxp});
    Builder::Op(b.body, kOpUMod, {t_u32, p, tri, maxp});
    Builder::Op(b.body, kOpAccessChain, {p_u32, cp, b_cnt, c0, slot});
    Builder::Op(b.body, kOpLoad, {t_u32, count, cp});
    Builder::Op(b.body, kOpULessThan, {t_bool, valid, p, count});
    Builder::Op(b.body, kOpIMul, {t_u32, pbase, slot, maxp});
    Builder::Op(b.body, kOpIAdd, {t_u32, pi, pbase, p});
    Builder::Op(b.body, kOpAccessChain, {p_v4u, ip, b_idx, c0, pi});
    Builder::Op(b.body, kOpLoad, {t_v4u, tri_idx, ip});
    Builder::Op(b.body, kOpVectorExtractDynamic, {t_u32, e, tri_idx, corner});
    // A corner index past the group's vertices (bad game data) reads vertex 0 of the group instead
    // of another group's (or past the buffer).
    const uint32_t e_ok = b.Id(), e_safe = b.Id();
    Builder::Op(b.body, kOpULessThan, {t_bool, e_ok, e, maxv});
    Builder::Op(b.body, kOpSelect, {t_u32, e_safe, e_ok, e, c0});
    Builder::Op(b.body, kOpIMul, {t_u32, vbase, slot, maxv});
    Builder::Op(b.body, kOpIAdd, {t_u32, vi2, vbase, e_safe});
    Builder::Op(b.body, kOpSelect, {t_u32, vsel, valid, vi2, vbase});
    Builder::Op(b.body, kOpAccessChain, {p_v4f, pp, b_pos, c0, vsel});
    Builder::Op(b.body, kOpLoad, {t_v4f, position, pp});
    const uint32_t pushed = EmitDepthPush(b, t_f32, t_v4f, p_word7, b_live, c0, c_zidx, t_u32, position);
    Builder::Op(b.body, kOpCompositeConstruct, {t_v4b, valid4, valid, valid, valid, valid});
    Builder::Op(b.body, kOpSelect, {t_v4f, outp, valid4, pushed, vzero});
    Builder::Op(b.body, kOpStore, {pos_out, outp});
    Builder::Op(b.body, kOpReturn, {});
    Builder::Op(b.body, kOpFunctionEnd, {});
    return b.Finish();
}

// "Real look" vertex shader: like the silhouette one, plus one output per input of the game's
// fragment program, read from the attribute buffers the converted mesh program wrote:
//   binding 5: vec4 vattr[(slot*maxV + v) * nv + k]   binding 6: vec4 pattr[(slot*maxP + p) * np + k]
struct VsOutput {
    uint32_t location = 0, component = 0, count = 4;
    uint32_t kind = 0;   // 0 float, 1 signed int, 2 unsigned int
    uint32_t source = 0; // 0 none (zeros), 1 per-vertex buffer, 2 per-primitive buffer
    uint32_t k = 0;      // index within that buffer's record
};

inline std::vector<uint32_t> RealVertexShader(uint32_t set, uint32_t max_v, uint32_t max_p, uint32_t nv, uint32_t np,
                                              const std::vector<VsOutput>& outputs) {
    enum : uint32_t { kOpVectorShuffle = 79, kOpCompositeExtract = 81, kOpBitcast = 124 };
    Builder b;
    Builder::Op(b.caps, kOpCapability, {1}); // Shader
    Builder::Op(b.mem, kOpMemoryModel, {0, 1});
    const uint32_t main = b.Id(), vid_var = b.Id(), pos_out = b.Id();
    std::vector<uint32_t> out_vars;
    for (size_t i = 0; i < outputs.size(); ++i) out_vars.push_back(b.Id());
    {
        std::vector<uint32_t> w{0 /*Vertex*/, main};
        auto name = Builder::Str("main");
        w.insert(w.end(), name.begin(), name.end());
        w.push_back(vid_var);
        w.push_back(pos_out);
        w.insert(w.end(), out_vars.begin(), out_vars.end());
        Builder::OpV(b.entry, kOpEntryPoint, w);
    }
    const uint32_t t_void = b.Id(), t_fn = b.Id(), t_u32 = b.Id(), t_i32 = b.Id(), t_f32 = b.Id(), t_bool = b.Id();
    const uint32_t t_v4f = b.Id(), t_v4u = b.Id(), t_v4b = b.Id();
    Builder::Op(b.globals, kOpTypeVoid, {t_void});
    Builder::Op(b.globals, kOpTypeFunction, {t_fn, t_void});
    Builder::Op(b.globals, kOpTypeInt, {t_u32, 32, 0});
    Builder::Op(b.globals, kOpTypeInt, {t_i32, 32, 1});
    Builder::Op(b.globals, kOpTypeFloat, {t_f32, 32});
    Builder::Op(b.globals, kOpTypeBool, {t_bool});
    Builder::Op(b.globals, kOpTypeVector, {t_v4f, t_f32, 4});
    Builder::Op(b.globals, kOpTypeVector, {t_v4u, t_u32, 4});
    Builder::Op(b.globals, kOpTypeVector, {t_v4b, t_bool, 4});
    // Types of the outputs: (kind, count) -> type id.
    uint32_t vec_types[3][5]{};
    const uint32_t scalar[3] = {t_f32, t_i32, t_u32};
    auto type_of = [&](uint32_t kind, uint32_t count) {
        if (count == 1) return scalar[kind];
        if (kind == 0 && count == 4) return t_v4f;
        if (kind == 2 && count == 4) return t_v4u;
        uint32_t& t = vec_types[kind][count];
        if (!t) { t = b.Id(); Builder::Op(b.globals, kOpTypeVector, {t, scalar[kind], count}); }
        return t;
    };
    auto buffer = [&](uint32_t elem, uint32_t stride, uint32_t binding, uint32_t* elem_ptr) {
        const uint32_t ra = b.Id(), st = b.Id(), ptr = b.Id(), var = b.Id();
        *elem_ptr = b.Id();
        Builder::Op(b.globals, kOpTypeRuntimeArray, {ra, elem});
        Builder::Op(b.globals, kOpTypeStruct, {st, ra});
        Builder::Op(b.globals, kOpTypePointer, {ptr, 12 /*StorageBuffer*/, st});
        Builder::Op(b.globals, kOpTypePointer, {*elem_ptr, 12, elem});
        Builder::Op(b.globals, kOpVariable, {ptr, var, 12});
        Builder::Op(b.deco, kOpDecorate, {ra, 6 /*ArrayStride*/, stride});
        Builder::Op(b.deco, kOpMemberDecorate, {st, 0, 35 /*Offset*/, 0});
        Builder::Op(b.deco, kOpMemberDecorate, {st, 0, 24 /*NonWritable*/});
        Builder::Op(b.deco, kOpDecorate, {st, 2 /*Block*/});
        Builder::Op(b.deco, kOpDecorate, {var, 34 /*DescriptorSet*/, set});
        Builder::Op(b.deco, kOpDecorate, {var, 33 /*Binding*/, binding});
        return var;
    };
    uint32_t p_v4f, p_v4u, p_u32, p_vat = 0, p_pat = 0;
    const uint32_t b_pos = buffer(t_v4f, 16, 0, &p_v4f);
    const uint32_t b_idx = buffer(t_v4u, 16, 1, &p_v4u);
    const uint32_t b_cnt = buffer(t_u32, 4, 2, &p_u32);
    uint32_t p_word7;
    const uint32_t b_live = buffer(t_u32, 4, 7, &p_word7);
    bool uses_v = false, uses_p = false;
    for (auto& o : outputs) { uses_v |= o.source == 1; uses_p |= o.source == 2; }
    const uint32_t b_vat = uses_v ? buffer(t_v4f, 16, 5, &p_vat) : 0;
    const uint32_t b_pat = uses_p ? buffer(t_v4f, 16, 6, &p_pat) : 0;
    const uint32_t p_in_u32 = b.Id(), p_out_v4f = b.Id();
    Builder::Op(b.globals, kOpTypePointer, {p_in_u32, 1 /*Input*/, t_u32});
    Builder::Op(b.globals, kOpTypePointer, {p_out_v4f, 3 /*Output*/, t_v4f});
    Builder::Op(b.globals, kOpVariable, {p_in_u32, vid_var, 1});
    Builder::Op(b.globals, kOpVariable, {p_out_v4f, pos_out, 3});
    Builder::Op(b.deco, kOpDecorate, {vid_var, 11 /*BuiltIn*/, 42 /*VertexIndex*/});
    Builder::Op(b.deco, kOpDecorate, {pos_out, 11, 0 /*Position*/});
    std::vector<uint32_t> out_types;
    for (size_t i = 0; i < outputs.size(); ++i) {
        const uint32_t t = type_of(outputs[i].kind, outputs[i].count), pt = b.Id();
        out_types.push_back(t);
        Builder::Op(b.globals, kOpTypePointer, {pt, 3, t});
        Builder::Op(b.globals, kOpVariable, {pt, out_vars[i], 3});
        Builder::Op(b.deco, kOpDecorate, {out_vars[i], 30 /*Location*/, outputs[i].location});
        if (outputs[i].component) Builder::Op(b.deco, kOpDecorate, {out_vars[i], 31 /*Component*/, outputs[i].component});
    }
    auto uconst = [&](uint32_t v) { const uint32_t id = b.Id(); Builder::Op(b.globals, kOpConstant, {t_u32, id, v}); return id; };
    const uint32_t c0 = uconst(0), c3 = uconst(3), maxv = uconst(max_v), maxp = uconst(max_p), c_zidx = uconst(kZpushIndex);
    const uint32_t c_nv = uconst(nv ? nv : 1), c_np = uconst(np ? np : 1);
    std::vector<uint32_t> k_const;
    for (auto& o : outputs) k_const.push_back(uconst(o.k));
    const uint32_t f0 = b.Id(), vzero = b.Id();
    Builder::Op(b.globals, kOpConstant, {t_f32, f0, 0});
    Builder::Op(b.globals, kOpConstantComposite, {t_v4f, vzero, f0, f0, f0, f0});

    const uint32_t label = b.Id(), vi = b.Id(), tri = b.Id(), corner = b.Id(), slot = b.Id(), p = b.Id();
    const uint32_t cp = b.Id(), count = b.Id(), valid = b.Id(), pbase = b.Id(), pi = b.Id(), ip = b.Id();
    const uint32_t tri_idx = b.Id(), e = b.Id(), vbase = b.Id(), vi2 = b.Id(), vsel = b.Id(), pp = b.Id();
    const uint32_t position = b.Id(), valid4 = b.Id(), outp = b.Id(), e_ok = b.Id(), e_safe = b.Id();
    Builder::Op(b.body, kOpFunction, {t_void, main, 0, t_fn});
    Builder::Op(b.body, kOpLabel, {label});
    Builder::Op(b.body, kOpLoad, {t_u32, vi, vid_var});
    Builder::Op(b.body, kOpUDiv, {t_u32, tri, vi, c3});
    Builder::Op(b.body, kOpUMod, {t_u32, corner, vi, c3});
    Builder::Op(b.body, kOpUDiv, {t_u32, slot, tri, maxp});
    Builder::Op(b.body, kOpUMod, {t_u32, p, tri, maxp});
    Builder::Op(b.body, kOpAccessChain, {p_u32, cp, b_cnt, c0, slot});
    Builder::Op(b.body, kOpLoad, {t_u32, count, cp});
    Builder::Op(b.body, kOpULessThan, {t_bool, valid, p, count});
    Builder::Op(b.body, kOpIMul, {t_u32, pbase, slot, maxp});
    Builder::Op(b.body, kOpIAdd, {t_u32, pi, pbase, p});
    Builder::Op(b.body, kOpAccessChain, {p_v4u, ip, b_idx, c0, pi});
    Builder::Op(b.body, kOpLoad, {t_v4u, tri_idx, ip});
    Builder::Op(b.body, kOpVectorExtractDynamic, {t_u32, e, tri_idx, corner});
    Builder::Op(b.body, kOpULessThan, {t_bool, e_ok, e, maxv});
    Builder::Op(b.body, kOpSelect, {t_u32, e_safe, e_ok, e, c0});
    Builder::Op(b.body, kOpIMul, {t_u32, vbase, slot, maxv});
    Builder::Op(b.body, kOpIAdd, {t_u32, vi2, vbase, e_safe});
    Builder::Op(b.body, kOpSelect, {t_u32, vsel, valid, vi2, vbase});
    Builder::Op(b.body, kOpAccessChain, {p_v4f, pp, b_pos, c0, vsel});
    Builder::Op(b.body, kOpLoad, {t_v4f, position, pp});
    const uint32_t pushed = EmitDepthPush(b, t_f32, t_v4f, p_word7, b_live, c0, c_zidx, t_u32, position);
    Builder::Op(b.body, kOpCompositeConstruct, {t_v4b, valid4, valid, valid, valid, valid});
    Builder::Op(b.body, kOpSelect, {t_v4f, outp, valid4, pushed, vzero});
    Builder::Op(b.body, kOpStore, {pos_out, outp});
    for (size_t i = 0; i < outputs.size(); ++i) {
        const VsOutput& o = outputs[i];
        uint32_t v4 = vzero;
        if (o.source == 1 || o.source == 2) {
            const uint32_t rec = b.Id(), scaled = b.Id(), idx = b.Id(), ptr = b.Id();
            v4 = b.Id();
            const bool prim = o.source == 2;
            Builder::Op(b.body, kOpIMul, {t_u32, scaled, prim ? pi : vsel, prim ? c_np : c_nv});
            Builder::Op(b.body, kOpIAdd, {t_u32, idx, scaled, k_const[i]});
            Builder::Op(b.body, kOpAccessChain, {p_v4f, ptr, prim ? b_pat : b_vat, c0, idx});
            Builder::Op(b.body, kOpLoad, {t_v4f, v4, ptr});
            (void)rec;
        }
        // Components [component, component + count) of the vec4, as the input's type.
        const uint32_t ftype = type_of(0, o.count);
        uint32_t val = v4;
        if (o.count == 1) {
            val = b.Id();
            Builder::Op(b.body, kOpCompositeExtract, {t_f32, val, v4, o.component});
        } else if (!(o.count == 4 && o.component == 0)) {
            val = b.Id();
            std::vector<uint32_t> w{ftype, val, v4, v4};
            for (uint32_t c = 0; c < o.count; ++c) w.push_back(o.component + c);
            Builder::OpV(b.body, kOpVectorShuffle, w);
        }
        if (o.kind != 0) {
            const uint32_t cast = b.Id();
            Builder::Op(b.body, kOpBitcast, {out_types[i], cast, val});
            val = cast;
        }
        Builder::Op(b.body, kOpStore, {out_vars[i], val});
    }
    Builder::Op(b.body, kOpReturn, {});
    Builder::Op(b.body, kOpFunctionEnd, {});
    return b.Finish();
}

// Indirect mesh draws: turns the game's (x, y, z) mesh-task counts (copied into the job's 64-byte
// argument block, binding 0 at its offset) into the layer's own indirect arguments:
//   in   a[0..2] = x, y, z      a[3] = draw count (IndirectCount variant)
//   out  a[4..6]  = task (or mesh-only) dispatch  (g, 1, 1)
//        a[8..10] = mesh dispatch after a task   (per_task, g, 1)
//        a[12..15]= draw (slots * verts_per_slot, 1, 0, 0)
// g = x*y*z (0 when this draw is past the count), clamped to `cap`; the unclamped value goes to
// binding 7 [counter_slot] (atomic max) so the layer can size later draws.
// Push constants (u32): per_task, cap, verts_per_slot, counter_slot, is_task, has_count, draw_index.
inline std::vector<uint32_t> IndirectArgsShader() {
    enum : uint32_t { OpTypeBoolE = 20, OpLogicalOr = 166, OpIEqual = 170, OpINotEqual = 171, OpAtomicUMax = 239 };
    Builder b;
    Builder::Op(b.caps, kOpCapability, {1});
    Builder::Op(b.mem, kOpMemoryModel, {0, 1});
    const uint32_t main = b.Id();
    {
        std::vector<uint32_t> w{5 /*GLCompute*/, main};
        auto name = Builder::Str("main");
        w.insert(w.end(), name.begin(), name.end());
        Builder::OpV(b.entry, kOpEntryPoint, w);
    }
    Builder::Op(b.modes, kOpExecutionMode, {main, 17 /*LocalSize*/, 1, 1, 1});
    const uint32_t t_void = b.Id(), t_fn = b.Id(), t_u32 = b.Id(), t_bool = b.Id();
    Builder::Op(b.globals, kOpTypeVoid, {t_void});
    Builder::Op(b.globals, kOpTypeFunction, {t_fn, t_void});
    Builder::Op(b.globals, kOpTypeInt, {t_u32, 32, 0});
    Builder::Op(b.globals, OpTypeBoolE, {t_bool});
    auto buffer = [&](uint32_t binding, uint32_t* elem_ptr) {
        const uint32_t ra = b.Id(), st = b.Id(), ptr = b.Id(), var = b.Id();
        *elem_ptr = b.Id();
        Builder::Op(b.globals, kOpTypeRuntimeArray, {ra, t_u32});
        Builder::Op(b.globals, kOpTypeStruct, {st, ra});
        Builder::Op(b.globals, kOpTypePointer, {ptr, 12, st});
        Builder::Op(b.globals, kOpTypePointer, {*elem_ptr, 12, t_u32});
        Builder::Op(b.globals, kOpVariable, {ptr, var, 12});
        Builder::Op(b.deco, kOpDecorate, {ra, 6 /*ArrayStride*/, 4});
        Builder::Op(b.deco, kOpMemberDecorate, {st, 0, 35 /*Offset*/, 0});
        Builder::Op(b.deco, kOpDecorate, {st, 2 /*Block*/});
        Builder::Op(b.deco, kOpDecorate, {var, 34 /*DescriptorSet*/, 0});
        Builder::Op(b.deco, kOpDecorate, {var, 33 /*Binding*/, binding});
        return var;
    };
    uint32_t p_args, p_cnt;
    const uint32_t b_args = buffer(0, &p_args);
    const uint32_t b_cnt = buffer(7, &p_cnt);
    // Push constants: struct of 7 u32.
    const uint32_t pc_st = b.Id(), pc_ptr = b.Id(), p_pc_u32 = b.Id(), pc_var = b.Id();
    Builder::Op(b.globals, kOpTypeStruct, {pc_st, t_u32, t_u32, t_u32, t_u32, t_u32, t_u32, t_u32});
    for (uint32_t m = 0; m < 7; ++m) Builder::Op(b.deco, kOpMemberDecorate, {pc_st, m, 35, m * 4});
    Builder::Op(b.deco, kOpDecorate, {pc_st, 2});
    Builder::Op(b.globals, kOpTypePointer, {pc_ptr, 9 /*PushConstant*/, pc_st});
    Builder::Op(b.globals, kOpTypePointer, {p_pc_u32, 9, t_u32});
    Builder::Op(b.globals, kOpVariable, {pc_ptr, pc_var, 9});
    uint32_t c[16];
    for (uint32_t i = 0; i < 16; ++i) { c[i] = b.Id(); Builder::Op(b.globals, kOpConstant, {t_u32, c[i], i}); }
    const uint32_t label = b.Id();
    Builder::Op(b.body, kOpFunction, {t_void, main, 0, t_fn});
    Builder::Op(b.body, kOpLabel, {label});
    auto load_arg = [&](uint32_t i) {
        const uint32_t ptr = b.Id(), v = b.Id();
        Builder::Op(b.body, kOpAccessChain, {p_args, ptr, b_args, c[0], c[i]});
        Builder::Op(b.body, kOpLoad, {t_u32, v, ptr});
        return v;
    };
    auto store_arg = [&](uint32_t i, uint32_t v) {
        const uint32_t ptr = b.Id();
        Builder::Op(b.body, kOpAccessChain, {p_args, ptr, b_args, c[0], c[i]});
        Builder::Op(b.body, kOpStore, {ptr, v});
    };
    auto load_pc = [&](uint32_t m) {
        const uint32_t ptr = b.Id(), v = b.Id();
        Builder::Op(b.body, kOpAccessChain, {p_pc_u32, ptr, pc_var, c[m]});
        Builder::Op(b.body, kOpLoad, {t_u32, v, ptr});
        return v;
    };
    auto bin = [&](uint32_t op, uint32_t type, uint32_t x, uint32_t y) {
        const uint32_t r = b.Id();
        Builder::Op(b.body, op, {type, r, x, y});
        return r;
    };
    auto select = [&](uint32_t cond, uint32_t x, uint32_t y) {
        const uint32_t r = b.Id();
        Builder::Op(b.body, kOpSelect, {t_u32, r, cond, x, y});
        return r;
    };
    const uint32_t x = load_arg(0), y = load_arg(1), z = load_arg(2), count = load_arg(3);
    const uint32_t per_task = load_pc(0), cap = load_pc(1), vps = load_pc(2), cslot = load_pc(3);
    const uint32_t is_task = load_pc(4), has_count = load_pc(5), di = load_pc(6);
    const uint32_t g0 = bin(kOpIMul, t_u32, bin(kOpIMul, t_u32, x, y), z);
    const uint32_t no_count = bin(OpIEqual, t_bool, has_count, c[0]);
    const uint32_t in_count = bin(kOpULessThan, t_bool, di, count);
    const uint32_t valid = bin(OpLogicalOr, t_bool, no_count, in_count);
    const uint32_t g1 = select(valid, g0, c[0]);
    {
        const uint32_t ptr = b.Id(), old = b.Id();
        Builder::Op(b.body, kOpAccessChain, {p_cnt, ptr, b_cnt, c[0], cslot});
        Builder::Op(b.body, OpAtomicUMax, {t_u32, old, ptr, c[1] /*Device scope*/, c[0] /*relaxed*/, g1});
    }
    const uint32_t g = select(bin(kOpULessThan, t_bool, g1, cap), g1, cap);
    store_arg(4, g); store_arg(5, c[1]); store_arg(6, c[1]);
    store_arg(8, per_task); store_arg(9, g); store_arg(10, c[1]);
    const uint32_t task = bin(OpINotEqual, t_bool, is_task, c[0]);
    const uint32_t slots = select(task, bin(kOpIMul, t_u32, g, per_task), g);
    store_arg(12, bin(kOpIMul, t_u32, slots, vps)); store_arg(13, c[1]); store_arg(14, c[0]); store_arg(15, c[0]);
    Builder::Op(b.body, kOpReturn, {});
    Builder::Op(b.body, kOpFunctionEnd, {});
    return b.Finish();
}

// Flat color to `outputs` attachments (float vec4 outputs at locations 0..outputs-1).
inline std::vector<uint32_t> FlatFragmentShader(uint32_t outputs, float r, float g, float bl) {
    Builder b;
    Builder::Op(b.caps, kOpCapability, {1});
    Builder::Op(b.mem, kOpMemoryModel, {0, 1});
    const uint32_t main = b.Id();
    std::vector<uint32_t> outs;
    for (uint32_t i = 0; i < outputs; ++i) outs.push_back(b.Id());
    {
        std::vector<uint32_t> w{4 /*Fragment*/, main};
        auto name = Builder::Str("main");
        w.insert(w.end(), name.begin(), name.end());
        w.insert(w.end(), outs.begin(), outs.end());
        Builder::OpV(b.entry, kOpEntryPoint, w);
    }
    Builder::Op(b.modes, kOpExecutionMode, {main, 7 /*OriginUpperLeft*/});
    const uint32_t t_void = b.Id(), t_fn = b.Id(), t_f32 = b.Id(), t_v4f = b.Id(), p_out = b.Id();
    Builder::Op(b.globals, kOpTypeVoid, {t_void});
    Builder::Op(b.globals, kOpTypeFunction, {t_fn, t_void});
    Builder::Op(b.globals, kOpTypeFloat, {t_f32, 32});
    Builder::Op(b.globals, kOpTypeVector, {t_v4f, t_f32, 4});
    Builder::Op(b.globals, kOpTypePointer, {p_out, 3, t_v4f});
    const uint32_t cr = b.Id(), cg = b.Id(), cb = b.Id(), ca = b.Id(), color = b.Id();
    uint32_t bits;
    std::memcpy(&bits, &r, 4); Builder::Op(b.globals, kOpConstant, {t_f32, cr, bits});
    std::memcpy(&bits, &g, 4); Builder::Op(b.globals, kOpConstant, {t_f32, cg, bits});
    std::memcpy(&bits, &bl, 4); Builder::Op(b.globals, kOpConstant, {t_f32, cb, bits});
    const float one = 1.0f;
    std::memcpy(&bits, &one, 4); Builder::Op(b.globals, kOpConstant, {t_f32, ca, bits});
    Builder::Op(b.globals, kOpConstantComposite, {t_v4f, color, cr, cg, cb, ca});
    for (uint32_t i = 0; i < outputs; ++i) {
        Builder::Op(b.globals, kOpVariable, {p_out, outs[i], 3});
        Builder::Op(b.deco, kOpDecorate, {outs[i], 30 /*Location*/, i});
    }
    const uint32_t label = b.Id();
    Builder::Op(b.body, kOpFunction, {t_void, main, 0, t_fn});
    Builder::Op(b.body, kOpLabel, {label});
    for (uint32_t i = 0; i < outputs; ++i) Builder::Op(b.body, kOpStore, {outs[i], color});
    Builder::Op(b.body, kOpReturn, {});
    Builder::Op(b.body, kOpFunctionEnd, {});
    return b.Finish();
}

} // namespace spvb
