// SPIR-V rewriting for the OoT mesh fallback layer.
//
// MeshToCompute: a Lichii task+mesh / mesh program (EXT mesh shading) becomes a GLCompute program
// that writes what the mesh stage would hand to the rasterizer into storage buffers:
//   binding 0  positions   vec4  [slot * max_vertices + v]
//   binding 1  indices     uvec3 [slot * max_primitives + p]   (stride 16)
//   binding 2  prim count  uint  [slot]
//   binding 3  payload     uint  [task * payload_words + w]     (read)
//   binding 4  mesh count  uint  [task]                         (read; task pipelines)
// slot = WorkgroupId.y * NumWorkgroups.x + WorkgroupId.x. For a task pipeline the mesh program
// is dispatched as (mesh groups per task, tasks, 1): WorkgroupId.x is then the index within the task,
// as Lichii's mesh program expects, and groups past the task's count store 0 primitives and return.
// Every other mesh output becomes a Private variable (unused in silhouette mode).
//
// TaskToCompute: the task payload moves to workgroup memory; OpEmitMeshTasksEXT becomes "store the
// mesh count and copy the payload to binding 3/4 at WorkgroupId.x, then return".
#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

namespace spvrw {

enum : uint32_t {
    OpName = 5, OpMemberName = 6, OpExtension = 10, OpEntryPoint = 15, OpExecutionMode = 16,
    OpCapability = 17, OpTypeVoid = 19, OpTypeBool = 20, OpTypeInt = 21, OpTypeFloat = 22,
    OpTypeVector = 23, OpTypeArray = 28, OpTypeRuntimeArray = 29, OpTypeStruct = 30,
    OpTypePointer = 32, OpConstant = 43, OpFunction = 54, OpVariable = 59, OpLoad = 61, OpStore = 62,
    OpAccessChain = 65, OpInBoundsAccessChain = 66, OpDecorate = 71, OpMemberDecorate = 72,
    OpCompositeExtract = 81, OpIAdd = 128, OpIMul = 132, OpUGreaterThanEqual = 174, OpPhi = 245,
    OpSelectionMerge = 247, OpLabel = 248, OpBranchConditional = 250, OpReturn = 253,
    OpEmitMeshTasksEXT = 5294, OpSetMeshOutputsEXT = 5295,
};
enum : uint32_t { SC_Input = 1, SC_Output = 3, SC_Workgroup = 4, SC_Private = 6, SC_StorageBuffer = 12,
                  SC_TaskPayload = 5402 };
enum : uint32_t { Dec_Block = 2, Dec_ArrayStride = 6, Dec_BuiltIn = 11, Dec_Location = 30, Dec_Binding = 33,
                  Dec_DescriptorSet = 34, Dec_Offset = 35, Dec_PerPrimitive = 5271 };
enum : uint32_t { BI_Position = 0, BI_WorkgroupId = 26, BI_PrimitiveTriangleIndices = 5296 };

struct Inst {
    uint32_t op = 0;
    std::vector<uint32_t> w; // operands (everything after the first word)
};

struct Module {
    uint32_t header[5]{};
    std::vector<Inst> code;

    bool Parse(const uint32_t* words, size_t count) {
        if (count < 5 || words[0] != 0x07230203) return false;
        for (int i = 0; i < 5; ++i) header[i] = words[i];
        for (size_t i = 5; i < count;) {
            const uint32_t n = words[i] >> 16;
            if (n == 0 || i + n > count) return false;
            Inst in;
            in.op = words[i] & 0xffff;
            in.w.assign(words + i + 1, words + i + n);
            code.push_back(std::move(in));
            i += n;
        }
        return true;
    }
    std::vector<uint32_t> Serialize() const {
        std::vector<uint32_t> out(header, header + 5);
        for (const Inst& in : code) {
            out.push_back((static_cast<uint32_t>(in.w.size() + 1) << 16) | in.op);
            out.insert(out.end(), in.w.begin(), in.w.end());
        }
        return out;
    }
    uint32_t NewId() { return header[3]++; }
};

// Result id position of an instruction, or -1 (only the opcodes this file looks at by id).
inline int ResultIndex(uint32_t op) {
    switch (op) {
    case OpTypeVoid: case OpTypeBool: case OpTypeInt: case OpTypeFloat: case OpTypeVector: case OpTypeArray:
    case OpTypeRuntimeArray: case OpTypeStruct: case OpTypePointer: case OpLabel: return 0;
    default: return 1;
    }
}

struct Options {
    uint32_t set = 7;                 // descriptor set of the layer's buffers
    uint32_t max_mesh_per_task = 32;  // (unused: the dispatch size gives the mesh groups per task)
    uint32_t debug_slot = ~0u;        // task programs: atomicMax(binding 7 [debug_slot], mesh groups emitted)
    bool task_pipeline = false;       // mesh program that follows a task program
    // Generic outputs (by Location) to keep for the game's fragment program ("real look"):
    // per-vertex ones go to binding 5, per-primitive ones to binding 6 (vec4 each).
    std::vector<uint32_t> keep_locations;
};

struct Result {
    bool ok = false;
    std::string error;
    std::vector<uint32_t> spirv;
    uint32_t max_vertices = 0, max_primitives = 0, payload_words = 0, local_x = 0;
    // Kept outputs in buffer order: binding 5 holds vec4 [(slot*maxV + v) * vertex_locations.size() + k],
    // binding 6 holds vec4 [(slot*maxP + p) * prim_locations.size() + k].
    std::vector<uint32_t> vertex_locations, prim_locations;
};

// ------------------------------------------------------------------ helpers over a module
struct Rewriter {
    Module& m;
    std::map<uint32_t, size_t> def;                 // id -> defining instruction index (types/consts/vars)
    std::map<uint32_t, std::vector<uint32_t>> dec;  // id -> list of decoration kinds
    std::vector<Inst> new_decorations, new_globals;
    explicit Rewriter(Module& mod) : m(mod) { Index(); }

    void Index() {
        def.clear();
        dec.clear();
        for (size_t i = 0; i < m.code.size(); ++i) {
            const Inst& in = m.code[i];
            if (in.op == OpDecorate) dec[in.w[0]].push_back(in.w[1]);
            if (in.op == OpFunction) break;
            switch (in.op) {
            case OpTypeVoid: case OpTypeBool: case OpTypeInt: case OpTypeFloat: case OpTypeVector:
            case OpTypeArray: case OpTypeRuntimeArray: case OpTypeStruct: case OpTypePointer:
                def[in.w[0]] = i; break;
            case OpConstant: case OpVariable: def[in.w[1]] = i; break;
            default: break;
            }
        }
    }
    const Inst* Def(uint32_t id) const {
        auto it = def.find(id);
        return it == def.end() ? nullptr : &m.code[it->second];
    }
    uint32_t FindType(uint32_t op, std::vector<uint32_t> operands) {
        for (auto& [id, idx] : def) {
            const Inst& in = m.code[idx];
            if (in.op == op && std::vector<uint32_t>(in.w.begin() + 1, in.w.end()) == operands) return id;
        }
        for (const Inst& in : new_globals) {
            if (in.op == op && std::vector<uint32_t>(in.w.begin() + 1, in.w.end()) == operands) return in.w[0];
        }
        const uint32_t id = m.NewId();
        Inst t{op, {id}};
        t.w.insert(t.w.end(), operands.begin(), operands.end());
        new_globals.push_back(t);
        return id;
    }
    uint32_t U32() { return FindType(OpTypeInt, {32, 0}); }
    uint32_t Const(uint32_t v) {
        const uint32_t u32 = U32();
        for (auto& [id, idx] : def) {
            const Inst& in = m.code[idx];
            if (in.op == OpConstant && in.w[0] == u32 && in.w.size() == 3 && in.w[2] == v) return id;
        }
        for (const Inst& in : new_globals) {
            if (in.op == OpConstant && in.w[0] == u32 && in.w[2] == v) return in.w[1];
        }
        const uint32_t id = m.NewId();
        new_globals.push_back({OpConstant, {u32, id, v}});
        return id;
    }
    uint32_t ConstValue(uint32_t id) const {
        const Inst* in = Def(id);
        return in && in->op == OpConstant ? in->w[2] : 0;
    }
    void Decorate(uint32_t id, std::vector<uint32_t> d) {
        Inst in{OpDecorate, {id}};
        in.w.insert(in.w.end(), d.begin(), d.end());
        new_decorations.push_back(in);
    }
    // A storage buffer "struct { elem[] }" variable at (set, binding); returns the variable.
    uint32_t Buffer(uint32_t elem, uint32_t stride, uint32_t set, uint32_t binding) {
        const uint32_t ra = m.NewId();
        new_globals.push_back({OpTypeRuntimeArray, {ra, elem}});
        Decorate(ra, {Dec_ArrayStride, stride});
        const uint32_t st = m.NewId();
        new_globals.push_back({OpTypeStruct, {st, ra}});
        new_decorations.push_back({OpMemberDecorate, {st, 0, Dec_Offset, 0}});
        Decorate(st, {Dec_Block});
        const uint32_t ptr = m.NewId();
        new_globals.push_back({OpTypePointer, {ptr, SC_StorageBuffer, st}});
        const uint32_t var = m.NewId();
        new_globals.push_back({OpVariable, {ptr, var, SC_StorageBuffer}});
        Decorate(var, {Dec_DescriptorSet, set});
        Decorate(var, {Dec_Binding, binding});
        return var;
    }
    uint32_t Pointer(uint32_t sc, uint32_t pointee) {
        for (const Inst& in : new_globals) {
            if (in.op == OpTypePointer && in.w[1] == sc && in.w[2] == pointee) return in.w[0];
        }
        const uint32_t id = m.NewId();
        new_globals.push_back({OpTypePointer, {id, sc, pointee}});
        return id;
    }
    // Inserts new decorations before the first type/const/var, new globals before the first function.
    void Commit() {
        size_t first_global = m.code.size(), first_function = m.code.size();
        for (size_t i = 0; i < m.code.size(); ++i) {
            const uint32_t op = m.code[i].op;
            if (first_global == m.code.size() && (op == OpTypeVoid || op == OpTypeBool || op == OpTypeInt ||
                                                  op == OpTypeFloat || op == OpTypeVector || op == OpTypePointer ||
                                                  op == OpTypeStruct || op == OpTypeArray || op == OpConstant ||
                                                  op == OpVariable || (op >= 19 && op <= 39))) {
                first_global = i;
            }
            if (op == OpFunction) { first_function = i; break; }
        }
        m.code.insert(m.code.begin() + first_function, new_globals.begin(), new_globals.end());
        m.code.insert(m.code.begin() + first_global, new_decorations.begin(), new_decorations.end());
        new_globals.clear();
        new_decorations.clear();
    }
};

inline bool IsMeshMode(uint32_t mode) {
    return mode == 26 /*OutputVertices*/ || mode == 27 /*OutputPoints*/ || mode == 5269 /*OutputLinesEXT*/ ||
           mode == 5270 /*OutputPrimitivesEXT*/ || mode == 5298 /*OutputTrianglesEXT*/;
}

// Removes the mesh capability/extension, turns the entry point into GLCompute and returns the
// local size x (0 if unknown). `drop` lists interface ids to remove, `add` ids to add.
inline uint32_t RetargetEntry(Module& m, const std::set<uint32_t>& drop, const std::vector<uint32_t>& add) {
    uint32_t local_x = 0;
    std::vector<Inst> out;
    for (Inst& in : m.code) {
        if (in.op == OpCapability && in.w[0] == 5283) continue;
        if (in.op == OpExtension) {
            const std::string name(reinterpret_cast<const char*>(in.w.data()));
            if (name == "SPV_EXT_mesh_shader") continue;
        }
        if (in.op == OpExecutionMode) {
            if (IsMeshMode(in.w[1])) continue;
            if (in.w[1] == 17) local_x = in.w[2];
        }
        if (in.op == OpEntryPoint) {
            in.w[0] = 5; // GLCompute
            const std::string name(reinterpret_cast<const char*>(&in.w[2]));
            const size_t name_words = name.size() / 4 + 1;
            std::vector<uint32_t> w(in.w.begin(), in.w.begin() + 2 + name_words);
            std::set<uint32_t> listed;
            for (size_t k = 2 + name_words; k < in.w.size(); ++k) {
                if (!drop.count(in.w[k]) && listed.insert(in.w[k]).second) w.push_back(in.w[k]);
            }
            for (uint32_t id : add) {
                if (listed.insert(id).second) w.push_back(id);
            }
            in.w = std::move(w);
        }
        out.push_back(std::move(in));
    }
    m.code = std::move(out);
    return local_x;
}

// ------------------------------------------------------------------ mesh -> compute
inline Result MeshToCompute(const uint32_t* words, size_t count, const Options& opt) {
    Result r;
    Module m;
    if (!m.Parse(words, count)) { r.error = "not SPIR-V"; return r; }
    Rewriter rw(m);

    // Find the interesting variables.
    uint32_t pos_var = 0, idx_var = 0, payload_var = 0, wg_var = 0, nwg_var = 0;
    std::set<uint32_t> other_outputs;
    for (auto& [id, i] : rw.def) {
        const Inst& in = m.code[i];
        if (in.op != OpVariable) continue;
        const uint32_t sc = in.w[2];
        uint32_t builtin = ~0u;
        for (const Inst& d : m.code) {
            if (d.op == OpDecorate && d.w[0] == id && d.w[1] == Dec_BuiltIn) builtin = d.w[2];
        }
        if (sc == SC_Output) {
            if (builtin == BI_Position) pos_var = id;
            else if (builtin == BI_PrimitiveTriangleIndices) idx_var = id;
            else other_outputs.insert(id);
        } else if (sc == SC_TaskPayload) {
            payload_var = id;
        } else if (sc == SC_Input && builtin == BI_WorkgroupId) {
            wg_var = id;
        } else if (sc == SC_Input && builtin == 24 /*NumWorkgroups*/) {
            nwg_var = id;
        }
    }
    if (!pos_var || !idx_var) { r.error = "no position or triangle index output"; return r; }

    // Output array lengths.
    auto array_len = [&](uint32_t var) -> uint32_t {
        const Inst* v = rw.Def(var);
        const Inst* p = v ? rw.Def(v->w[0]) : nullptr;
        const Inst* a = p ? rw.Def(p->w[2]) : nullptr;
        return a && a->op == OpTypeArray ? rw.ConstValue(a->w[2]) : 0;
    };
    r.max_vertices = array_len(pos_var);
    r.max_primitives = array_len(idx_var);
    if (payload_var) r.payload_words = array_len(payload_var);
    if (!r.max_vertices || !r.max_primitives) { r.error = "unknown output sizes"; return r; }

    const uint32_t u32 = rw.U32();
    const uint32_t f32 = rw.FindType(OpTypeFloat, {32});
    const uint32_t vec4 = rw.FindType(OpTypeVector, {f32, 4});

    // Generic outputs the fragment program reads: kept in buffers instead of becoming Private.
    // kept[var] = (per-primitive, index in its buffer).
    std::map<uint32_t, std::pair<bool, uint32_t>> kept;
    if (!opt.keep_locations.empty()) {
        std::vector<std::pair<uint32_t, uint32_t>> vtx, prim; // (location, var)
        for (uint32_t v : other_outputs) {
            uint32_t loc = ~0u;
            bool per_prim = false, other_deco = false;
            for (const Inst& d : m.code) {
                if (d.op != OpDecorate || d.w[0] != v) continue;
                if (d.w[1] == Dec_Location) loc = d.w[2];
                else if (d.w[1] == Dec_PerPrimitive) per_prim = true;
                else if (d.w[1] != 5 /*RelaxedPrecision*/) other_deco = true; // Component, interpolation...
            }
            if (loc == ~0u || other_deco) continue;
            if (std::find(opt.keep_locations.begin(), opt.keep_locations.end(), loc) == opt.keep_locations.end()) continue;
            // Only arrays of vec4 sized like the stage's vertices/primitives.
            const Inst* var = rw.Def(v);
            const Inst* ptr = var ? rw.Def(var->w[0]) : nullptr;
            const Inst* arr = ptr ? rw.Def(ptr->w[2]) : nullptr;
            if (!arr || arr->op != OpTypeArray || arr->w[1] != vec4) continue;
            if (rw.ConstValue(arr->w[2]) != (per_prim ? r.max_primitives : r.max_vertices)) continue;
            (per_prim ? prim : vtx).push_back({loc, v});
        }
        std::sort(vtx.begin(), vtx.end());
        std::sort(prim.begin(), prim.end());
        for (auto& [loc, v] : vtx) { kept[v] = {false, static_cast<uint32_t>(r.vertex_locations.size())}; r.vertex_locations.push_back(loc); }
        for (auto& [loc, v] : prim) { kept[v] = {true, static_cast<uint32_t>(r.prim_locations.size())}; r.prim_locations.push_back(loc); }
        for (auto& [v, info] : kept) other_outputs.erase(v);
    }
    const uint32_t uvec3 = rw.FindType(OpTypeVector, {u32, 3});
    const uint32_t boolean = rw.FindType(OpTypeBool, {});
    if (!wg_var) {
        wg_var = m.NewId();
        rw.new_globals.push_back({OpVariable, {rw.Pointer(SC_Input, uvec3), wg_var, SC_Input}});
        rw.Decorate(wg_var, {Dec_BuiltIn, BI_WorkgroupId});
    }
    if (!nwg_var) {
        nwg_var = m.NewId();
        rw.new_globals.push_back({OpVariable, {rw.Pointer(SC_Input, uvec3), nwg_var, SC_Input}});
        rw.Decorate(nwg_var, {Dec_BuiltIn, 24 /*NumWorkgroups*/});
    }
    const uint32_t b_pos = rw.Buffer(vec4, 16, opt.set, 0);
    const uint32_t b_idx = rw.Buffer(uvec3, 16, opt.set, 1);
    const uint32_t b_cnt = rw.Buffer(u32, 4, opt.set, 2);
    const uint32_t b_pay = rw.Buffer(u32, 4, opt.set, 3);
    const uint32_t b_tsk = rw.Buffer(u32, 4, opt.set, 4);
    const uint32_t b_vat = r.vertex_locations.empty() ? 0 : rw.Buffer(vec4, 16, opt.set, 5);
    const uint32_t b_pat = r.prim_locations.empty() ? 0 : rw.Buffer(vec4, 16, opt.set, 6);
    const uint32_t p_vec4 = rw.Pointer(SC_StorageBuffer, vec4);
    const uint32_t p_f32 = rw.Pointer(SC_StorageBuffer, f32);
    const uint32_t p_uvec3 = rw.Pointer(SC_StorageBuffer, uvec3);
    const uint32_t p_u32 = rw.Pointer(SC_StorageBuffer, u32);
    const uint32_t c0 = rw.Const(0), c1 = rw.Const(1);
    const uint32_t c_maxv = rw.Const(r.max_vertices), c_maxp = rw.Const(r.max_primitives);
    const uint32_t c_payw = rw.Const(r.payload_words ? r.payload_words : 1);
    const uint32_t c_nv = rw.Const(static_cast<uint32_t>(r.vertex_locations.size()));
    const uint32_t c_np = rw.Const(static_cast<uint32_t>(r.prim_locations.size()));
    std::map<uint32_t, uint32_t> kept_index_const; // buffer index k -> constant
    for (auto& [v, info] : kept) kept_index_const[info.second] = rw.Const(info.second);

    // Output pointer types used by the other outputs get Private twins.
    std::map<uint32_t, uint32_t> private_twin; // Output pointer type -> Private pointer type
    auto twin = [&](uint32_t ptr_type) {
        auto it = private_twin.find(ptr_type);
        if (it != private_twin.end()) return it->second;
        const Inst* p = rw.Def(ptr_type);
        const uint32_t t = rw.Pointer(SC_Private, p ? p->w[2] : 0);
        private_twin[ptr_type] = t;
        return t;
    };
    std::map<uint32_t, uint32_t> output_ptr_of; // other output var -> its Output pointer type
    for (uint32_t v : other_outputs) output_ptr_of[v] = rw.Def(v)->w[0];
    for (auto& [v, p] : output_ptr_of) twin(p);
    // Every Output pointer type (for access chains into the other outputs).
    std::vector<uint32_t> output_ptr_types;
    for (auto& [id, i] : rw.def) {
        const Inst& in = m.code[i];
        if (in.op == OpTypePointer && in.w[1] == SC_Output) output_ptr_types.push_back(id);
    }
    for (uint32_t p : output_ptr_types) twin(p);
    rw.Commit();
    rw.Index();

    // Globals: other outputs -> Private, payload variable removed, decorations of converted outputs dropped.
    std::set<uint32_t> removed{pos_var, idx_var};
    if (payload_var) removed.insert(payload_var);
    for (auto& [v, info] : kept) removed.insert(v);
    {
        // The Private versions of the other outputs move after the new pointer types they use
        // (types must be declared before use), keeping their ids.
        std::vector<Inst> out, moved;
        bool in_function = false;
        for (Inst& in : m.code) {
            if (in.op == OpFunction && !in_function) {
                in_function = true;
                out.insert(out.end(), moved.begin(), moved.end());
            }
            if (!in_function) {
                if (in.op == OpVariable && removed.count(in.w[1])) continue;
                if (in.op == OpVariable && other_outputs.count(in.w[1])) {
                    in.w[0] = private_twin[in.w[0]];
                    in.w[2] = SC_Private;
                    moved.push_back(std::move(in));
                    continue;
                }
                if ((in.op == OpDecorate || in.op == OpName) && (removed.count(in.w[0]) || other_outputs.count(in.w[0]))) {
                    if (in.op == OpName) { if (removed.count(in.w[0])) continue; }
                    else continue;
                }
            }
            out.push_back(std::move(in));
        }
        m.code = std::move(out);
    }
    // The other outputs stay in the interface as Private variables (SPIR-V 1.4+ lists them all).
    std::vector<uint32_t> interface_add{wg_var, nwg_var, b_pos, b_idx, b_cnt, b_pay, b_tsk};
    if (b_vat) interface_add.push_back(b_vat);
    if (b_pat) interface_add.push_back(b_pat);
    r.local_x = RetargetEntry(m, removed, interface_add);

    // Function bodies. The setup goes into the entry point's function (programs can have helper
    // functions before it), and only that function may touch the converted outputs.
    uint32_t main_id = 0;
    for (const Inst& in : m.code) {
        if (in.op == OpEntryPoint) { main_id = in.w[1]; break; }
    }
    std::vector<Inst> out;
    size_t i = 0;
    for (; i < m.code.size() && m.code[i].op != OpFunction; ++i) out.push_back(m.code[i]);
    uint32_t slot = 0, vbase = 0, pbase = 0, paybase = 0;
    uint32_t entry_label = 0, cont_label = 0;
    bool prologue_done = false, in_main = false;
    for (; i < m.code.size(); ++i) {
        Inst in = m.code[i];
        if (in.op == OpFunction) in_main = in.w[1] == main_id;
        if (in.op == OpLabel && in_main && !entry_label) {
            entry_label = in.w[0];
            out.push_back(in);
            continue;
        }
        if (!in_main && (in.op == OpAccessChain || in.op == OpInBoundsAccessChain) &&
            (in.w[2] == pos_var || in.w[2] == idx_var || (payload_var && in.w[2] == payload_var) || kept.count(in.w[2]))) {
            r.error = "mesh output used outside the entry function";
            return r;
        }
        if (!in_main && in.op == OpSetMeshOutputsEXT) { r.error = "SetMeshOutputs outside the entry function"; return r; }
        if (in_main && entry_label && !prologue_done && in.op != OpVariable) {
            // Slot of this workgroup, bases into the buffers, and the early return past the task's count.
            prologue_done = true;
            const uint32_t wg = m.NewId(), wx = m.NewId(), wy = m.NewId(), row = m.NewId();
            const uint32_t nwg = m.NewId(), per_task = m.NewId();
            slot = m.NewId(); vbase = m.NewId(); pbase = m.NewId(); paybase = m.NewId();
            out.push_back({OpLoad, {uvec3, wg, wg_var}});
            out.push_back({OpCompositeExtract, {u32, wx, wg, 0}});
            out.push_back({OpCompositeExtract, {u32, wy, wg, 1}});
            // Mesh groups per task = the dispatch's x size (chosen per draw by the layer).
            out.push_back({OpLoad, {uvec3, nwg, nwg_var}});
            out.push_back({OpCompositeExtract, {u32, per_task, nwg, 0}});
            out.push_back({OpIMul, {u32, row, wy, per_task}});
            out.push_back({OpIAdd, {u32, slot, row, wx}});
            out.push_back({OpIMul, {u32, vbase, slot, c_maxv}});
            out.push_back({OpIMul, {u32, pbase, slot, c_maxp}});
            out.push_back({OpIMul, {u32, paybase, wy, c_payw}});
            if (opt.task_pipeline) {
                const uint32_t tp = m.NewId(), tc = m.NewId(), oob = m.NewId();
                const uint32_t exit_label = m.NewId();
                cont_label = m.NewId();
                out.push_back({OpAccessChain, {p_u32, tp, b_tsk, c0, wy}});
                out.push_back({OpLoad, {u32, tc, tp}});
                out.push_back({OpUGreaterThanEqual, {boolean, oob, wx, tc}});
                out.push_back({OpSelectionMerge, {cont_label, 0}});
                out.push_back({OpBranchConditional, {oob, exit_label, cont_label}});
                out.push_back({OpLabel, {exit_label}});
                const uint32_t cp = m.NewId();
                out.push_back({OpAccessChain, {p_u32, cp, b_cnt, c0, slot}});
                out.push_back({OpStore, {cp, c0}});
                out.push_back({OpReturn, {}});
                out.push_back({OpLabel, {cont_label}});
            }
        }
        switch (in.op) {
        case OpAccessChain:
        case OpInBoundsAccessChain: {
            const uint32_t base = in.w[2];
            if (base == pos_var || base == idx_var || base == payload_var) {
                const uint32_t idx = m.NewId();
                const uint32_t base_off = base == pos_var ? vbase : base == idx_var ? pbase : paybase;
                out.push_back({OpIAdd, {u32, idx, base_off, in.w[3]}});
                Inst chain{in.op, {0, in.w[1], 0, c0, idx}};
                for (size_t k = 4; k < in.w.size(); ++k) chain.w.push_back(in.w[k]);
                if (base == pos_var) { chain.w[0] = in.w.size() > 4 ? p_f32 : p_vec4; chain.w[2] = b_pos; }
                else if (base == idx_var) { chain.w[0] = p_uvec3; chain.w[2] = b_idx; }
                else { chain.w[0] = p_u32; chain.w[2] = b_pay; }
                out.push_back(chain);
                continue;
            }
            if (auto k = kept.find(base); k != kept.end()) {
                // out[i] -> buffer[(base + i) * n + k]
                const bool per_prim = k->second.first;
                const uint32_t sum = m.NewId(), scaled = m.NewId(), idx = m.NewId();
                out.push_back({OpIAdd, {u32, sum, per_prim ? pbase : vbase, in.w[3]}});
                out.push_back({OpIMul, {u32, scaled, sum, per_prim ? c_np : c_nv}});
                out.push_back({OpIAdd, {u32, idx, scaled, kept_index_const[k->second.second]}});
                Inst chain{in.op, {in.w.size() > 4 ? p_f32 : p_vec4, in.w[1], per_prim ? b_pat : b_vat, c0, idx}};
                for (size_t q = 4; q < in.w.size(); ++q) chain.w.push_back(in.w[q]);
                out.push_back(chain);
                continue;
            }
            if (other_outputs.count(base) && private_twin.count(in.w[0])) in.w[0] = private_twin[in.w[0]];
            break;
        }
        case OpSetMeshOutputsEXT: {
            const uint32_t cp = m.NewId();
            out.push_back({OpAccessChain, {p_u32, cp, b_cnt, c0, slot}});
            out.push_back({OpStore, {cp, in.w[1]}});
            continue;
        }
        case OpPhi:
            // The entry block now ends at the continue label for its successors.
            if (cont_label && in_main) {
                for (size_t k = 3; k < in.w.size(); k += 2) {
                    if (in.w[k] == entry_label) in.w[k] = cont_label;
                }
            }
            break;
        default: break;
        }
        out.push_back(std::move(in));
    }
    m.code = std::move(out);
    for (const Inst& in : m.code) {
        if (in.op == OpEmitMeshTasksEXT) { r.error = "emit in a mesh program"; return r; }
    }
    r.spirv = m.Serialize();
    r.ok = true;
    return r;
}

// ------------------------------------------------------------------ task -> compute
inline Result TaskToCompute(const uint32_t* words, size_t count, const Options& opt) {
    Result r;
    Module m;
    if (!m.Parse(words, count)) { r.error = "not SPIR-V"; return r; }
    Rewriter rw(m);
    uint32_t payload_var = 0, wg_var = 0, lid_var = 0;
    for (auto& [id, i] : rw.def) {
        const Inst& in = m.code[i];
        if (in.op != OpVariable) continue;
        uint32_t builtin = ~0u;
        for (const Inst& d : m.code) {
            if (d.op == OpDecorate && d.w[0] == id && d.w[1] == Dec_BuiltIn) builtin = d.w[2];
        }
        if (in.w[2] == SC_TaskPayload) payload_var = id;
        if (in.w[2] == SC_Input && builtin == BI_WorkgroupId) wg_var = id;
        if (in.w[2] == SC_Input && builtin == 27 /*LocalInvocationId*/) lid_var = id;
    }
    if (!payload_var) { r.error = "no payload"; return r; }
    const Inst* pv = rw.Def(payload_var);
    const Inst* pp = rw.Def(pv->w[0]);
    const Inst* pa = rw.Def(pp->w[2]);
    if (!pa || pa->op != OpTypeArray) { r.error = "payload is not an array"; return r; }
    r.payload_words = rw.ConstValue(pa->w[2]);
    const uint32_t payload_array = pp->w[2];

    const uint32_t u32 = rw.U32();
    const uint32_t uvec3 = rw.FindType(OpTypeVector, {u32, 3});
    if (!wg_var) {
        wg_var = m.NewId();
        rw.new_globals.push_back({OpVariable, {rw.Pointer(SC_Input, uvec3), wg_var, SC_Input}});
        rw.Decorate(wg_var, {Dec_BuiltIn, BI_WorkgroupId});
    }
    if (!lid_var) {
        lid_var = m.NewId();
        rw.new_globals.push_back({OpVariable, {rw.Pointer(SC_Input, uvec3), lid_var, SC_Input}});
        rw.Decorate(lid_var, {Dec_BuiltIn, 27});
    }
    const uint32_t b_pay = rw.Buffer(u32, 4, opt.set, 3);
    const uint32_t b_tsk = rw.Buffer(u32, 4, opt.set, 4);
    const uint32_t b_dbg = opt.debug_slot != ~0u ? rw.Buffer(u32, 4, opt.set, 7) : 0;
    const uint32_t c_dbg = opt.debug_slot != ~0u ? rw.Const(opt.debug_slot) : 0;
    const uint32_t c_device_scope = rw.Const(1);
    const uint32_t p_u32 = rw.Pointer(SC_StorageBuffer, u32);
    const uint32_t wg_array_ptr = rw.Pointer(SC_Workgroup, payload_array);
    const uint32_t wg_u32_ptr = rw.Pointer(SC_Workgroup, u32);
    const uint32_t c0 = rw.Const(0);
    const uint32_t c_payw = rw.Const(r.payload_words);
    // TaskPayload pointer types -> Workgroup twins.
    std::map<uint32_t, uint32_t> wg_twin;
    std::vector<uint32_t> payload_ptr_types;
    for (auto& [id, i] : rw.def) {
        const Inst& in = m.code[i];
        if (in.op == OpTypePointer && in.w[1] == SC_TaskPayload) payload_ptr_types.push_back(id);
    }
    for (uint32_t p : payload_ptr_types) wg_twin[p] = rw.Pointer(SC_Workgroup, rw.Def(p)->w[2]);
    std::vector<uint32_t> lanes; // constants k*32 for the copy
    uint32_t local_x = 32;
    for (const Inst& in : m.code) {
        if (in.op == OpExecutionMode && in.w[1] == 17) local_x = in.w[2];
    }
    for (uint32_t k = 0; k < r.payload_words; k += local_x) lanes.push_back(rw.Const(k));
    rw.Commit();
    rw.Index();

    // Payload variable -> Workgroup storage, moved after the new pointer type it uses.
    {
        std::vector<Inst> out;
        Inst moved;
        bool in_function = false;
        for (Inst& in : m.code) {
            if (in.op == OpFunction && !in_function) {
                in_function = true;
                out.push_back(moved);
            }
            if (!in_function && in.op == OpVariable && in.w[1] == payload_var) {
                moved = in;
                moved.w[0] = wg_array_ptr;
                moved.w[2] = SC_Workgroup;
                continue;
            }
            out.push_back(std::move(in));
        }
        m.code = std::move(out);
    }
    std::vector<uint32_t> task_interface{wg_var, lid_var, b_pay, b_tsk};
    if (b_dbg) task_interface.push_back(b_dbg);
    r.local_x = RetargetEntry(m, {}, task_interface);
    // In SPIR-V 1.4+ the Workgroup payload stays in the interface (it was already there).

    std::vector<Inst> out;
    for (Inst& in : m.code) {
        if ((in.op == OpAccessChain || in.op == OpInBoundsAccessChain) && wg_twin.count(in.w[0])) {
            in.w[0] = wg_twin[in.w[0]];
        }
        if (in.op == OpEmitMeshTasksEXT) {
            // count = x * y * z; mesh count[task] = count; payload[task * words + i] = shared[i].
            const uint32_t gy = m.NewId(), n = m.NewId(), wg = m.NewId(), task = m.NewId();
            const uint32_t lid = m.NewId(), lane = m.NewId(), base = m.NewId();
            out.push_back({OpIMul, {u32, gy, in.w[1], in.w[2]}});
            out.push_back({OpIMul, {u32, n, in.w[0], gy}});
            out.push_back({OpLoad, {uvec3, wg, wg_var}});
            out.push_back({OpCompositeExtract, {u32, task, wg, 0}});
            const uint32_t tp = m.NewId();
            out.push_back({OpAccessChain, {p_u32, tp, b_tsk, c0, task}});
            out.push_back({OpStore, {tp, n}});
            if (b_dbg) {
                // The largest number of mesh groups this program sends, for the layer to size batches.
                const uint32_t dp = m.NewId(), old = m.NewId();
                out.push_back({OpAccessChain, {p_u32, dp, b_dbg, c0, c_dbg}});
                out.push_back({239 /*OpAtomicUMax*/, {u32, old, dp, c_device_scope, c0, n}});
            }
            out.push_back({OpLoad, {uvec3, lid, lid_var}});
            out.push_back({OpCompositeExtract, {u32, lane, lid, 0}});
            out.push_back({OpIMul, {u32, base, task, c_payw}});
            for (size_t k = 0; k < lanes.size(); ++k) {
                // lane + k * local_x stays inside the payload: its size is a multiple of the
                // local size (checked below).
                const uint32_t w = m.NewId(), src = m.NewId(), val = m.NewId();
                const uint32_t dst_i = m.NewId(), dst = m.NewId();
                out.push_back({OpIAdd, {u32, w, lane, lanes[k]}});
                out.push_back({OpAccessChain, {wg_u32_ptr, src, payload_var, w}});
                out.push_back({OpLoad, {u32, val, src}});
                out.push_back({OpIAdd, {u32, dst_i, base, w}});
                out.push_back({OpAccessChain, {p_u32, dst, b_pay, c0, dst_i}});
                out.push_back({OpStore, {dst, val}});
            }
            out.push_back({OpReturn, {}});
            continue;
        }
        out.push_back(std::move(in));
    }
    m.code = std::move(out);
    if (r.payload_words % local_x != 0) { r.error = "payload size is not a multiple of the local size"; return r; }
    r.spirv = m.Serialize();
    r.ok = true;
    return r;
}

// ------------------------------------------------------------------ fragment program of a mesh pipeline
// The game's fragment program, made usable after a vertex shader: the mesh capability/extension go,
// per-primitive inputs become flat inputs (the layer's vertex shader gives all three corners the
// primitive's value). Also lists the user-defined inputs so the vertex shader can feed each one.
struct FragInput {
    uint32_t location = 0, component = 0, count = 4; // components
    uint32_t kind = 0;                               // 0 float, 1 signed int, 2 unsigned int
    bool per_primitive = false;
};
struct FragResult {
    bool ok = false;
    std::string error;
    std::vector<uint32_t> spirv;
    std::vector<FragInput> inputs;
};

inline FragResult FragmentForVertexPipeline(const uint32_t* words, size_t count) {
    FragResult r;
    Module m;
    if (!m.Parse(words, count)) { r.error = "not SPIR-V"; return r; }
    Rewriter rw(m);
    bool fragment = false, has_geometry_cap = false, needs_geometry_cap = false;
    for (const Inst& in : m.code) {
        if (in.op == OpEntryPoint && in.w[0] == 4) fragment = true;
        if (in.op == OpCapability && in.w[0] == 2) has_geometry_cap = true;
    }
    if (!fragment) { r.error = "not a fragment program"; return r; }
    std::map<uint32_t, std::vector<std::vector<uint32_t>>> deco; // id -> decorations (kind, operands)
    std::set<uint32_t> flat_ids;
    for (const Inst& in : m.code) {
        if (in.op == OpDecorate) deco[in.w[0]].push_back(std::vector<uint32_t>(in.w.begin() + 1, in.w.end()));
        if (in.op == OpDecorate && in.w[1] == 14 /*Flat*/) flat_ids.insert(in.w[0]);
    }
    for (auto& [id, i] : rw.def) {
        const Inst& v = m.code[i];
        if (v.op != OpVariable || v.w[2] != SC_Input) continue;
        uint32_t builtin = ~0u, loc = ~0u, comp = 0;
        bool per_prim = false;
        for (auto& d : deco[id]) {
            if (d[0] == Dec_BuiltIn) builtin = d[1];
            if (d[0] == Dec_Location) loc = d[1];
            if (d[0] == 31 /*Component*/) comp = d[1];
            if (d[0] == Dec_PerPrimitive) per_prim = true;
        }
        if (builtin != ~0u) {
            // Primitive id / layer / viewport index read in a fragment program need Geometry here.
            if (builtin == 7 || builtin == 9 || builtin == 10) needs_geometry_cap = true;
            continue;
        }
        if (loc == ~0u) { r.error = "input without a location"; return r; }
        const Inst* ptr = rw.Def(v.w[0]);
        const Inst* t = ptr ? rw.Def(ptr->w[2]) : nullptr;
        if (!t) { r.error = "unknown input type"; return r; }
        FragInput fi;
        fi.location = loc;
        fi.component = comp;
        fi.per_primitive = per_prim;
        fi.count = 1;
        if (t->op == OpTypeVector) { fi.count = t->w[2]; t = rw.Def(t->w[1]); }
        if (!t) { r.error = "unknown input type"; return r; }
        if (t->op == OpTypeFloat && t->w[1] == 32) fi.kind = 0;
        else if (t->op == OpTypeInt && t->w[1] == 32) fi.kind = t->w[2] ? 1 : 2;
        else { r.error = "input type is not a 32-bit scalar or vector"; return r; }
        if (fi.component + fi.count > 4) { r.error = "input spans locations"; return r; }
        r.inputs.push_back(fi);
    }
    std::vector<Inst> out;
    for (Inst& in : m.code) {
        if (in.op == OpCapability && in.w[0] == 5283) continue;
        if (in.op == OpExtension) {
            const std::string name(reinterpret_cast<const char*>(in.w.data()));
            if (name == "SPV_EXT_mesh_shader") continue;
        }
        if (in.op == OpDecorate && in.w[1] == Dec_PerPrimitive) {
            if (flat_ids.count(in.w[0])) continue;
            in.w[1] = 14; // Flat
            flat_ids.insert(in.w[0]);
        }
        if (in.op == OpMemberDecorate && in.w[2] == Dec_PerPrimitive) in.w[2] = 14;
        out.push_back(std::move(in));
        if (needs_geometry_cap && !has_geometry_cap && out.back().op == OpCapability) {
            out.push_back({OpCapability, {2}});
            has_geometry_cap = true;
        }
    }
    m.code = std::move(out);
    r.spirv = m.Serialize();
    r.ok = true;
    return r;
}

} // namespace spvrw
