// Encoding fields and values authored from public register-role documentation, not game programs.
#include <array>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"

using Shader::Ampere::Instruction;
using Shader::Ampere::LoweredProgram;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL:%d %s\n",__LINE__,#x); std::exit(1); } } while (0)

static void field(Instruction& i, unsigned at, unsigned count, u32 value) {
    CHECK(count <= 32 && (count == 32 || value < (u64{1} << count)));
    for (unsigned bit = 0; bit < count; ++bit) {
        auto& word = i.words[(at+bit)/32];
        const u32 mask = u32{1} << ((at+bit)%32);
        word = (word & ~mask) | ((value >> bit & 1) ? mask : 0);
    }
}
static Instruction op(u32 code) {
    Instruction i{}; field(i,0,12,code); field(i,12,4,7); return i;
}
static Instruction exit_op() { auto i=op(0x94d); field(i,87,3,7); return i; }
static Instruction move(u32 reg,u32 value,bool uniform=false) {
    auto i=op(uniform ? 0x882 : 0x802); field(i,16,8,reg); field(i,32,32,value);
    if (!uniform) field(i,72,4,15);
    return i;
}
static Instruction r2ur(u32 uniform,u32 source) {
    auto i=op(0x3c2); field(i,16,8,uniform); field(i,24,8,source); field(i,81,3,7); return i;
}
static Instruction lop(u32 d,u32 a,u32 operand,u32 c,bool immediate=true) {
    auto i=op(immediate ? 0x812 : 0x212);
    field(i,16,8,d); field(i,24,8,a); field(i,32,immediate?32:8,operand); field(i,64,8,c);
    field(i,72,8,0x96); field(i,81,3,7); field(i,87,4,15); // XOR3, no predicate result
    return i;
}
static LoweredProgram lower(std::vector<Instruction> code) {
    code.push_back(exit_op()); return Shader::Ampere::LowerToMaxwell(code);
}
static void lowering_checks() {
    for (u32 source : {19U,217U,238U,239U,250U,254U,255U}) {
        const auto result=lower({r2ur(0,source)}); CHECK(result);
        if (source >= 216 && source < 255) CHECK(result.register_map_words > 0);
    }
    CHECK(lower({r2ur(1,217)})); // native R217 and emulated UR1 are different registers
    CHECK(lower({r2ur(28,214)})); // high uniform destination has its existing mirror below R216
    CHECK(!lower({r2ur(63,19)})); // preserve rejection of discarded uniform destinations
    for (u32 immediate : {0x314159faU,0x2468acefU,0x800000feU,0xdeadbeffU,0x12345611U}) {
        CHECK(lower({lop(13,9,immediate,17)})); // low operands: an immediate alone is not a high GPR
        const auto result=lower({move(5,42,true),lop(224,220,immediate,255)});
        CHECK(result && result.register_map_words > 0);
    }
    const auto reg=lower({move(5,42,true),lop(224,220,250,255,false)});
    CHECK(reg && reg.register_map_words > 0); // a real Rb in the register form still participates
    std::puts("PASS: authored R2UR scalar/uniform roles and LOP3 immediate/register controls");
}

#ifndef REGISTER_ROLES_LOWERING_ONLY
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <spirv/unified1/spirv.hpp>
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/frontend/maxwell/translate_program.h"
#include "shader_recompiler/host_translate_info.h"

struct Environment final : Shader::Environment {
    std::vector<u64> code;
    explicit Environment(const LoweredProgram& lowered) {
        stage=Shader::Stage::Compute; code.resize(0x50/8);
        for (u64 word:lowered.instructions) {
            if (code.size()%4==0) code.push_back(0);
            code.push_back(word);
        }
    }
    u64 ReadInstruction(u32 at) override { CHECK(at/8 < code.size()); return code[at/8]; }
    u32 ReadCbufValue(u32,u32) override { CHECK(false); return 0; }
    Shader::TextureType ReadTextureType(u32) override { CHECK(false); return {}; }
    Shader::TexturePixelFormat ReadTexturePixelFormat(u32) override { CHECK(false); return {}; }
    bool IsTexturePixelFormatInteger(u32) override { CHECK(false); return false; }
    u32 ReadViewportTransformState() override { return 0; }
    u32 TextureBoundBuffer() const override { return 0; }
    u32 LocalMemorySize() const override { return 0; }
    u32 SharedMemorySize() const override { return 4; }
    std::array<u32,3> WorkgroupSize() const override { return {32,1,1}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<Shader::ReplaceConstant> GetReplaceConstBuffer(u32,u32) override { return std::nullopt; }
    void Dump(u64,u64) override {}
};

static bool consumes_shuffle(Shader::IR::Value value,u32 expected,std::set<const Shader::IR::Inst*>& seen) {
    if (value.IsImmediate() || value.IsEmpty()) return false;
    const auto* inst=value.InstRecursive();
    if (!seen.insert(inst).second) return false;
    if (inst->GetOpcode()==Shader::IR::Opcode::ShuffleIndex) {
        const auto source=inst->Arg(0).Resolve();
        CHECK(source.IsImmediate() && source.U32()==expected);
        return true;
    }
    bool found=false;
    for (size_t n=0;n<inst->NumArgs();++n) found |= consumes_shuffle(inst->Arg(n),expected,seen);
    return found;
}

static void translate_check(std::vector<Instruction> source,const std::filesystem::path& directory,
                            const std::string& name,u32 result_reg,u32 expected,bool broadcast) {
    auto store=op(0x388); field(store,24,8,255); field(store,32,8,result_reg); field(store,73,3,4);
    source.push_back(store);
    const auto lowered=lower(source); CHECK(lowered);
    Environment env(lowered);
    Shader::ObjectPool<Shader::IR::Inst> insts;
    Shader::ObjectPool<Shader::IR::Block> blocks;
    Shader::ObjectPool<Shader::Maxwell::Flow::Block> flow_blocks;
    Shader::Maxwell::Flow::CFG cfg{env,flow_blocks,0x50};
    Shader::HostTranslateInfo host{}; host.support_float64=host.support_float16=host.support_int64=true;
    host.support_conditional_barrier=true;
    auto program=Shader::Maxwell::TranslateProgram(insts,blocks,env,cfg,host);
    u32 stores=0;
    for (const auto* block:program.blocks) for (const auto& inst:*block) {
        if (inst.GetOpcode()!=Shader::IR::Opcode::WriteSharedU32) continue;
        ++stores;
        const auto value=inst.Arg(1).Resolve();
        if (broadcast) {
            std::set<const Shader::IR::Inst*> seen;
            CHECK(consumes_shuffle(value,expected,seen));
        } else CHECK(value.IsImmediate() && value.U32()==expected);
    }
    CHECK(stores==1);
    Shader::Profile profile{}; profile.supported_spirv=0x00010600; profile.support_int64=true;
    profile.support_vote=true; profile.support_shuffle_relative=true;
    profile.supported_subgroup_stages=1U<<static_cast<u32>(Shader::Stage::Compute);
    const auto spirv=Shader::Backend::SPIRV::EmitSPIRV(profile,program);
    std::map<u32,u32> constants;
    std::set<u32> broadcast_results;
    std::map<u32,std::vector<u32>> result_inputs;
    std::vector<u32> stores_spirv;
    bool observed=false;
    for (size_t at=5;at<spirv.size();) {
        const u32 count=spirv[at]>>16; CHECK(count && at+count<=spirv.size());
        const auto opcode=static_cast<spv::Op>(spirv[at]&0xffff); const auto* w=&spirv[at+1];
        if (opcode==spv::OpConstant && count==4) constants[w[1]]=w[2];
        if (opcode==spv::OpGroupNonUniformShuffle) {
            CHECK(constants.at(w[3])==expected); broadcast_results.insert(w[1]);
        }
        if (opcode==spv::OpSelect) result_inputs[w[1]]={w[3],w[4]};
        if (opcode==spv::OpCopyObject || opcode==spv::OpBitcast) result_inputs[w[1]]={w[2]};
        if (opcode==spv::OpPhi)
            for (u32 n=2;n+1<count-1;n+=2) result_inputs[w[1]].push_back(w[n]);
        if (opcode==spv::OpStore) {
            stores_spirv.push_back(w[1]);
            if (!broadcast) observed |= constants.contains(w[1]) && constants.at(w[1])==expected;
        }
        at+=count;
    }
    if (broadcast) {
        bool changed;
        do {
            changed=false;
            for (const auto& [result,inputs]:result_inputs)
                for (u32 input:inputs) if (broadcast_results.contains(input)) changed |= broadcast_results.insert(result).second;
        } while (changed);
        for (u32 stored:stores_spirv) observed |= broadcast_results.contains(stored);
    }
    CHECK(observed);
    std::filesystem::create_directories(directory);
    std::ofstream file(directory/(name+".spv"),std::ios::binary);
    file.write(reinterpret_cast<const char*>(spirv.data()),static_cast<std::streamsize>(spirv.size()*4)); CHECK(file.good());
    std::printf("PASS: %s actual REGMAP/Maxwell/IR value and SPIR-V store\n",name.c_str());
}

static void r2ur_check(const std::filesystem::path& dir,u32 uniform,u32 source) {
    constexpr u32 value=0x13579bdf;
    auto read=op(0xc02); field(read,16,8,8); field(read,32,8,uniform); field(read,72,4,15); field(read,91,1,1);
    translate_check({move(uniform,0x52525252,true),move(source,value),r2ur(uniform,source),read},dir,
                    "r2ur-ur"+std::to_string(uniform)+"-r"+std::to_string(source),8,source==255?0:value,true);
}
static void lop_check(const std::filesystem::path& dir,u32 d,u32 a,u32 b,u32 c,bool immediate,
                      const std::string& name) {
    constexpr u32 av=0x12345678,cv=0x76543210,bv=0xabcdef01;
    std::vector<Instruction> source{move(5,42,true),move(a,av)};
    if (!immediate) source.push_back(move(b,bv));
    if (c!=255) source.push_back(move(c,cv));
    source.push_back(lop(d,a,b,c,immediate));
    const u32 expected=av^(immediate?b:bv)^(c==255?0:cv);
    translate_check(source,dir,name,d,expected,false);
}
#endif

int main(int argc,char** argv) {
    if (argc==2 && std::string(argv[1])=="--expect-original-rejections") {
        const auto r=lower({r2ur(0,238)});
        const auto l=lower({move(5,42,true),lop(224,220,0x314159fa,255)});
        CHECK(!r && r.error=="native program uses lowering scratch registers");
        CHECK(!l && l.error=="native program uses lowering scratch registers");
        std::puts("PASS: original header rejects both independently authored register-role counterexamples"); return 0;
    }
    lowering_checks();
#ifndef REGISTER_ROLES_LOWERING_ONLY
    CHECK(argc==2); const std::filesystem::path directory{argv[1]};
    for (u32 source:{19U,217U,238U,239U,250U,254U,255U}) r2ur_check(directory,0,source);
    r2ur_check(directory,1,217); r2ur_check(directory,28,214);
    lop_check(directory,13,9,0x314159fa,17,true,"lop-low-scratch-looking-immediate");
    lop_check(directory,224,220,0x314159fa,255,true,"lop-high-scratch-looking-immediate");
    lop_check(directory,224,220,0x2468acef,255,true,"lop-predicate-looking-immediate");
    lop_check(directory,224,220,0x800000fe,255,true,"lop-high-bit-immediate");
    lop_check(directory,224,220,0xdeadbeff,255,true,"lop-rz-looking-immediate");
    lop_check(directory,224,220,0x12345611,255,true,"lop-ordinary-immediate");
    lop_check(directory,224,220,250,255,false,"lop-real-register-rb-control");
#endif
}
