// Malformed shader input is refused, not undefined behaviour. Authored instruction bits and IR only: no dump, game, GPU or driver.
// A broken or malicious mod, or a game bug, can hand the recompiler fields the hardware never encodes. Each case below must end in
// Shader::NotImplementedException (the translation's callers catch it, leave the program out and log it), and the well-formed
// neighbour of each must still translate:
//   - LDC's 5-bit constant buffer index past the 18 buffers (18, 31): at LDC, and as an immediate the info pass meets (constant
//     folding makes LDC's IS-mode index one) where it wrote constant_buffer_used_sizes[index] past its end and shifted 1U << index;
//   - ISBERD's 2-bit shift at 3 and 4-bit size at 4..15, which reached UNREACHABLE (__builtin_unreachable: undefined);
//   - a transform feedback varying wider than what is left of its location (5 components at element 0), which wrote past
//     output_generics[index]'s 4 elements and asked for F32[5].
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <string>
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/environment.h"
#include "common/div_ceil.h"
#include "shader_recompiler/exception.h"
#include "shader_recompiler/frontend/ampere/lower_to_maxwell.h"
#include "shader_recompiler/frontend/maxwell/translate/impl/impl.h"
#include "shader_recompiler/host_translate_info.h"
#include "shader_recompiler/ir_opt/passes.h"
#include "shader_recompiler/object_pool.h"
#include "shader_recompiler/runtime_info.h"

using namespace Shader;

struct FixtureEnvironment final : Shader::Environment {
    explicit FixtureEnvironment(Stage s) { stage = s; }
    u64 ReadInstruction(u32) override { std::abort(); }
    u32 ReadCbufValue(u32, u32) override { return 0; }
    TextureType ReadTextureType(u32) override { return TextureType::Color2D; }
    TexturePixelFormat ReadTexturePixelFormat(u32) override { return TexturePixelFormat::R8_UNORM; }
    bool IsTexturePixelFormatInteger(u32) override { return false; }
    u32 ReadViewportTransformState() override { return 0; }
    u32 TextureBoundBuffer() const override { return 0; }
    u32 LocalMemorySize() const override { return 0; }
    u32 SharedMemorySize() const override { return 0; }
    std::array<u32, 3> WorkgroupSize() const override { return {1, 1, 1}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<ReplaceConstant> GetReplaceConstBuffer(u32, u32) override { return std::nullopt; }
    void Dump(u64, u64) override { std::abort(); }
};

static int failures, passes;

/// f must end in NotImplementedException (rejected) or not throw at all (accepted)
template <class F> static void expect(bool rejected, const char* what, F&& f) {
    std::string why;
    bool threw = false;
    try {
        f();
    } catch (const NotImplementedException& e) {
        threw = true, why = e.what();
    } catch (const std::exception& e) {
        std::printf("FAIL: %s: %s (not a NotImplementedException)\n", what, e.what());
        failures++;
        return;
    }
    if (threw != rejected) {
        std::printf("FAIL: %s: %s\n", what, rejected ? "accepted" : ("refused: " + why).c_str());
        failures++;
        return;
    }
    std::printf("ok: %s%s%s\n", what, rejected ? ": " : "", why.c_str());
    passes++;
}

/// One block, run through the passes the translation runs (the info pass among them)
struct OneBlock {
    ObjectPool<IR::Inst> pool;
    IR::Block block{pool};
    IR::Program program;
    FixtureEnvironment env;
    explicit OneBlock(Stage stage) : env(stage) {
        program.stage = stage;
        program.workgroup_size = {1, 1, 1};
        program.shared_memory_size = 4;   // (the results are written there: WriteShared)
        program.blocks.push_back(&block);
        program.post_order_blocks.push_back(&block);
        IR::AbstractSyntaxNode node{};
        node.type = IR::AbstractSyntaxNode::Type::Block;
        node.data.block = &block;
        program.syntax_list.push_back(node);
        IR::AbstractSyntaxNode ret{};
        ret.type = IR::AbstractSyntaxNode::Type::Return;
        program.syntax_list.push_back(ret);
    }
    void passes() {   // (translate_program.cpp's order: the storage buffer and texture passes before the info pass)
        HostTranslateInfo host{};
        Optimization::SsaRewritePass(program);
        Optimization::ConstantPropagationPass(env, program);
        Optimization::GlobalMemoryToStorageBufferPass(env, program, host);
        Optimization::TexturePass(env, program, host);
        Optimization::ConstantPropagationPass(env, program);
        Optimization::DeadCodeEliminationPass(program);
        Optimization::IdentityRemovalPass(program);
        Optimization::CollectShaderInfoPass(env, program);
    }
    void emit() {
        Profile profile{};
        profile.supported_spirv = 0x00010600;
        const auto spv = Backend::SPIRV::EmitSPIRV(profile, program);
        if (spv.size() <= 5) throw std::runtime_error("no module");
    }
};

// ---- LDC: index bits 36..40, mode 44..45, size 48..50 (B32 = 4), source register 8..15 (RZ = 255), destination 0..7 ----
static u64 ldc(u64 index, u64 mode = 0, u64 offset = 0) {
    return (index << 36) | (mode << 44) | (u64{4} << 48) | ((offset & 0xffff) << 20) | (u64{255} << 8) | 1;
}
static void translate_ldc(u64 index, u64 mode = 0, u64 offset = 0) {
    OneBlock b(Stage::Compute);
    Maxwell::TranslatorVisitor visitor{b.env, b.block};
    visitor.LDC(ldc(index, mode, offset));
    visitor.ir.WriteShared(32, visitor.ir.Imm32(0), visitor.X(static_cast<IR::Reg>(1)));
    visitor.ir.Epilogue();
    b.passes();
    b.emit();
}
/// A global load whose address is read from constant buffer `index`, as LDC .IS from RZ leaves one: the storage buffer pass
/// tracks the address to that buffer (its descriptor's cbuf_index), and the back end looks the buffer up among the 18
static void global_load_from_cbuf(u32 index) {
    OneBlock b(Stage::Compute);
    IR::IREmitter ir{b.block};
    const IR::Value address{ir.GetCbuf(ir.Imm32(index), ir.Imm32(0x110), 64, false)};
    const IR::U32 value{ir.LoadGlobal32(IR::U64{ir.PackUint2x32(address)})};
    ir.WriteShared(32, ir.Imm32(0), value);
    ir.Epilogue();
    b.passes();
    b.emit();
}
/// A constant buffer read whose index is an immediate, as LDC's IS mode leaves it once constant folding is done
static void info_pass_cbuf(u32 index) {
    OneBlock b(Stage::Compute);
    IR::IREmitter ir{b.block};
    const IR::U32 value{ir.GetCbuf(ir.Imm32(index), ir.Imm32(0), 32, false)};
    ir.WriteShared(32, ir.Imm32(0), value);
    ir.Epilogue();
    b.passes();
}

// ---- ISBERD: source register 8..15 (RZ: the immediate alone), o 32, skew 31, size 36..39, shift 47..48 ----
static u64 isberd(u64 src, u64 o, u64 skew, u64 sz, u64 shift) {
    return 1 | (src << 8) | (skew << 31) | (o << 32) | (sz << 36) | (shift << 47);
}
static void translate_isberd(u64 insn) {
    OneBlock b(Stage::Compute);
    Maxwell::TranslatorVisitor visitor{b.env, b.block};
    visitor.ISBERD(insn);
}

// ---- transform feedback: generic 0 stored by a vertex stage, its varying `components` wide ----
static void emit_xfb(u32 components) {
    OneBlock b(Stage::VertexB);
    IR::IREmitter ir{b.block};
    for (int c = 0; c < 4; c++)
        ir.SetAttribute(static_cast<IR::Attribute>(static_cast<size_t>(IR::Attribute::Generic0X) + c), ir.Imm32(1.0f), ir.Imm32(0));
    ir.Epilogue();
    b.passes();
    RuntimeInfo info{};
    const size_t at{static_cast<size_t>(IR::Attribute::Generic0X)};
    info.xfb_varyings[at] = TransformFeedbackVarying{.buffer = 0, .stream = 0, .stride = 16, .offset = 0, .components = components};
    info.xfb_count = static_cast<u32>(at + 4);
    Profile profile{};
    profile.supported_spirv = 0x00010600;
    Backend::Bindings bindings{};
    const auto spv = Backend::SPIRV::EmitSPIRV(profile, info, b.program, bindings);
    if (spv.size() <= 5) throw std::runtime_error("no module");
}

// ---- the native lowering's cost: 32,767 branches back to the start, one forward to the end, then 32,766 unconditional EXITs and
// the last one. Each EXIT asked every relocation in turn whether a branch was still pending, and the backward ones all said no
// before the forward one said yes (and the other per-instruction checks scanned every call and relocation the same way):
// quadratic, seconds a program; a few milliseconds kept as they come ----
static Ampere::Instruction native(u32 op, u32 word1, u32 word2) { return {{{op | (7U << 12), word1, word2, 0}}}; }
static Ampere::Instruction branch(u32 from, u32 to) {
    const u64 relative = static_cast<u64>(s64{to} * 16 - (s64{from} * 16 + 16));   // (50 bits, two's complement)
    return native(0x947, static_cast<u32>(relative), (7U << 23) | static_cast<u32>((relative >> 32) & 0x3ffff));
}
static double lower_branches_and_exits(bool* lowered, u32 back, u32 exits) {
    const u32 last = back + exits;   // (the forward branch at `back`, then the EXITs; the last of them closes)
    std::vector<Ampere::Instruction> code;
    for (u32 i = 0; i < back; i++) code.push_back(branch(i, 0));
    code.push_back(branch(back, last));
    for (u32 i = 0; i < exits; i++) code.push_back(native(0x94d, 0, 0x03800000));
    const auto t0 = std::chrono::steady_clock::now();
    const auto result = Ampere::LowerToMaxwell(code);
    *lowered = static_cast<bool>(result);
    if (!result) std::printf("  (not lowered: %.*s at +%u)\n", int(result.error.size()), result.error.data(), result.error_offset);
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

// ---- a constant arithmetic shift by 32 or more, folded (constant_propagation_pass.cpp): saturated to the sign, not shifted ----
static u32 fold_sar(u32 a, u32 b) {
    OneBlock blk(Stage::Compute);
    IR::IREmitter ir{blk.block};
    const IR::U32 value{ir.ShiftRightArithmetic(ir.Imm32(a), ir.Imm32(b))};
    ir.WriteShared(32, ir.Imm32(0), value);
    ir.Epilogue();
    Optimization::ConstantPropagationPass(blk.env, blk.program);
    for (const IR::Inst& inst : blk.block.Instructions())
        if (inst.GetOpcode() == IR::Opcode::WriteSharedU32) {
            const IR::Value v{inst.Arg(1)};
            if (v.IsImmediate()) return v.U32();
            if (v.InstRecursive()->GetOpcode() == IR::Opcode::Identity && v.InstRecursive()->Arg(0).IsImmediate())
                return v.InstRecursive()->Arg(0).U32();
        }
    throw std::runtime_error("the shift was not folded");
}

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);   // (each line out at once: a build that dies on a case still shows the ones before)
    // (--only <group>: cbuf, isberd, folds, xfb or lowering, so a group can be run where an earlier one dies, as on main before
    // this fixture; all of them without it)
    const std::string only = argc > 2 && std::string(argv[1]) == "--only" ? argv[2] : "";
    const auto group = [&](const char* name) { return only.empty() || only == name; };
    if (group("cbuf")) {
    expect(false, "LDC index 17, the last buffer", [] { translate_ldc(17); });
    expect(true, "LDC index 18", [] { translate_ldc(18); });
    expect(true, "LDC index 31", [] { translate_ldc(31); });
    expect(true, "LDC index 31 in IS mode", [] { translate_ldc(31, 2); });
    // (F1: LDC .IS from RZ with a negative offset: the address's top half is 0xFFFF, the buffer 0xFFFF + 2, past the lowering's
    // bank check and LDC's 5-bit one)
    expect(true, "LDC .IS from RZ at offset -16 of bank 2 (folds to buffer 0x10001)", [] { translate_ldc(2, 2, 0xfff0); });
    expect(false, "LDC .IS from RZ at offset 16 of bank 2", [] { translate_ldc(2, 2, 0x10); });
    expect(false, "a global load from an address in constant buffer 3", [] { global_load_from_cbuf(3); });
    expect(true, "a global load from an address in constant buffer 0xFFFF (the storage buffer pass)", [] { global_load_from_cbuf(0xffff); });
    expect(false, "a constant buffer read at immediate index 17", [] { info_pass_cbuf(17); });
    expect(true, "a constant buffer read at immediate index 18 (the info pass)", [] { info_pass_cbuf(18); });
    expect(true, "a constant buffer read at immediate index 31 (the info pass)", [] { info_pass_cbuf(31); });
    expect(true, "a constant buffer read at immediate index 40 (1U << index past 32)", [] { info_pass_cbuf(40); });
    }

    if (group("isberd")) {
    expect(false, "ISBERD shift 2 (B32)", [] { translate_isberd(isberd(2, 0, 0, 2, 2)); });
    expect(true, "ISBERD shift 3", [] { translate_isberd(isberd(2, 0, 0, 2, 3)); });
    expect(false, "ISBERD.O size 2 (U32)", [] { translate_isberd(isberd(255, 1, 0, 2, 0)); });
    for (u64 sz = 4; sz < 16; sz++) {
        const std::string what = "ISBERD.O size " + std::to_string(sz);
        expect(true, what.c_str(), [sz] { translate_isberd(isberd(255, 1, 0, sz, 0)); });
        const std::string skew = "ISBERD.O.SKEW size " + std::to_string(sz);
        expect(true, skew.c_str(), [sz] { translate_isberd(isberd(255, 1, 1, sz, 0)); });
    }
    }

    if (group("folds")) {
    const auto equal = [](const char* what, u64 got, u64 want) {
        if (got == want) std::printf("ok: %s: %#llx\n", what, (unsigned long long)got), passes++;
        else std::printf("FAIL: %s: %#llx, not %#llx\n", what, (unsigned long long)got, (unsigned long long)want), failures++;
    };
    equal("0x40000000 >> 1, folded", fold_sar(0x40000000u, 1), 0x20000000u);
    equal("0x40000000 >> 33, folded (saturates to the sign)", fold_sar(0x40000000u, 33), 0u);
    equal("0x80000000 >> 40, folded", fold_sar(0x80000000u, 40), 0xFFFFFFFFu);
    equal("0x80000000 >> -1 (as unsigned), folded", fold_sar(0x80000000u, 0xFFFFFFFFu), 0xFFFFFFFFu);
    // (Common::DivCeil near the type's top: a malformed program's memory size sized its array by it, and it wrapped to 0)
    equal("DivCeil(0xFFFFFFFF, 4)", Common::DivCeil(0xFFFFFFFFu, 4U), 0x40000000u);
    equal("DivCeil(0xFFFFFFFD, 4)", Common::DivCeil(0xFFFFFFFDu, 4U), 0x40000000u);
    equal("DivCeil(9, 4)", Common::DivCeil(9U, 4U), 3u);
    equal("DivCeil(8, 4)", Common::DivCeil(8U, 4U), 2u);
    }

    if (group("xfb")) {
    expect(false, "a 4-component transform feedback varying", [] { emit_xfb(4); });
    expect(true, "a 5-component transform feedback varying", [] { emit_xfb(5); });
    expect(true, "a 255-component transform feedback varying", [] { emit_xfb(255); });
    }

    if (group("lowering")) {
        // (how it grows, not a time a slow machine could miss: four times the program, about four times the time when the
        // checks are kept as they come, sixteen when they scan; the fastest of three runs of each)
        bool lowered = true;
        const auto fastest = [&](u32 n) {
            double best = 1e9;
            for (int run = 0; run < 3; run++) {
                bool ok = false;
                best = (std::min)(best, lower_branches_and_exits(&ok, n, n));
                lowered = lowered && ok;
            }
            return best;
        };
        const double small = fastest(8191), large = fastest(32767);
        const double growth = large / (std::max)(small, 1e-6);
        if (!lowered || growth > 8.0 || large > 1.0) {
            std::printf("FAIL: branches and exits: %s, 16,383 in %.4f s, 65,535 in %.4f s: %.1f times (linear about 4, quadratic "
                        "about 16; at most 8)\n", lowered ? "lowered" : "not lowered", small, large, growth);
            failures++;
        } else {
            std::printf("ok: branches and exits: 16,383 in %.4f s, 65,535 in %.4f s, %.1f times\n", small, large, growth);
            passes++;
        }
    }

    std::printf("%d passed, %d failed\n", passes, failures);
    return failures ? 1 : 0;
}
