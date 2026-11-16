"""Malformed input is refused, not undefined behaviour: the renderer's side, and the copies tests/malformed_shaders does not build.

tests/malformed_shaders (built and run in CI's SPIR-V fixtures job) feeds the Vulkan recompiler malformed instructions and
varyings. This checks, game-free:
  - common/assert.h (both copies: gpu/vk/compat for the Vulkan renderer, suyu's for the WebGPU one): UNREACHABLE throws, compiled
    and run, never __builtin_unreachable; and every UNREACHABLE left in gpu/ is one a throw is safe at (none in the texture
    formats' noexcept helpers, which a game's format reaches: they return a defined block instead);
  - the GLSL backend's transform feedback varying and suyu's ISBERD refuse what the Vulkan side refuses (source);
  - the program cache refuses a record with no stages (a translation never saves one; its program was published ready and
    stages[0] read past an empty vector), and the predicted pipelines' sample passes each note through note_sane;
  - a texture descriptor's shifts, which a program cache record carries as the file has them, are taken mod 32 where shifted.

    python -I -B tests/test_malformed_inputs.py [--cxx g++]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import block  # noqa: E402

failures = []


def check(ok, what):
    if not ok:
        failures.append(what)
        print("FAIL: " + what)


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


ASSERTS = ("gpu/vk/compat/common/assert.h", "gpu/third_party/suyu/common/assert.h")

RUN = r"""
#include "common/assert.h"
#include <cstdio>
#include <stdexcept>
static int pick(int v) {
    switch (v) {
    case 0: return 10;
    case 1: return 11;
    default: UNREACHABLE();
    }
}
int main() {
    if (pick(1) != 11) return 2;
    try {
        pick(7);
    } catch (const std::logic_error& e) {
        std::printf("%s\n", e.what());
        return 0;
    }
    return 3;
}
"""


def check_asserts(cxx):
    for rel in ASSERTS:
        src = text(rel)
        check("__builtin_unreachable();" not in src.replace("__builtin_unreachable let", ""), rel + " still ends in __builtin_unreachable")
        with tempfile.TemporaryDirectory(prefix="unreachable-") as tmp:
            (Path(tmp) / "common").mkdir()
            shutil.copyfile(ROOT / rel, Path(tmp) / "common" / "assert.h")
            cpp, exe = Path(tmp) / "t.cpp", Path(tmp) / "t"
            cpp.write_text(RUN, encoding="utf-8")
            subprocess.run([cxx, "-std=c++20", "-O2", "-I", tmp, str(cpp), "-o", str(exe)], check=True)
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check(r.returncode == 0 and "unreachable code reached at" in r.stdout,
                  "%s: UNREACHABLE did not throw (exit %d)" % (rel, r.returncode))
    # (every UNREACHABLE left in gpu/: in shader translation, where the throw is caught; none in a noexcept texture helper)
    uses = subprocess.run(["git", "-C", str(ROOT), "grep", "-n", "UNREACHABLE", "--", "gpu"], capture_output=True, text=True).stdout
    for line in uses.splitlines():
        path, _, code = line.partition(":")
        code = code.partition(":")[2].split("//")[0]
        if path in ASSERTS or not re.search(r"\bUNREACHABLE(_MSG)?\(", code):
            continue   # (the macros themselves, and a comment naming one)
        check("/shader_recompiler/" in path, "UNREACHABLE outside the shader recompiler, where nothing catches it: " + line[:160])
    surface = text("gpu/third_party/suyu/video_core/surface.h")
    for fn, val in (("DefaultBlockWidth", "1"), ("DefaultBlockHeight", "1"), ("BitsPerBlock", "32")):
        body = block(surface, "constexpr u32 %s(PixelFormat format) noexcept {" % fn)
        check("default: return %s;" % val in body, "surface.h %s gives a format outside the list a defined block" % fn)


def check_sources():
    glsl = block(text("gpu/third_party/suyu/shader_recompiler/backend/glsl/glsl_emit_context.cpp"), "void EmitContext::DefineGenericOutput(")
    spirv = block(text("gpu/vk/eden/shader_recompiler/backend/spirv/spirv_emit_context.cpp"), "void DefineGenericOutput(")
    for name, body in (("GLSL", glsl), ("SPIR-V", spirv)):
        refuse = "if (xfb_varying && xfb_varying->components > remainder) {"
        check(refuse in body and body.index(refuse) < body.index("std::fill_n("),
              name + "'s DefineGenericOutput fills output_generics before refusing a varying wider than its location")
    for rel in ("gpu/vk/eden", "gpu/third_party/suyu"):
        isberd = text(rel + "/shader_recompiler/frontend/maxwell/translate/impl/internal_stage_buffer_entry_read.cpp")
        check(isberd.count('throw NotImplementedException("ISBERD') == 3, rel + "'s ISBERD does not refuse its shift and sizes")
    # (the audit's F1/F2/F4: every consumer of an immediate constant buffer index refuses one past the 18; a handle's shift of 32
    # or more is not tracked; a program's memory sizes and mesh output counts past any GPU's are refused)
    gm = text("gpu/vk/eden/shader_recompiler/ir_opt/global_memory_to_storage_buffer_pass.cpp")
    track = block(gm, "std::optional<StorageBufferAddr> Track(const IR::Value& value, const Bias* bias, bool skip_c1 = false) {")
    check("if (index.U32() >= Info::MAX_CBUFS) {" in track, "the storage buffer pass tracks a constant buffer index past the 18")
    tex = text("gpu/vk/eden/shader_recompiler/ir_opt/texture_pass.cpp")
    get = block(tex, "std::optional<ConstBufferAddr> TryGetConstBuffer(const IR::Inst* inst, Environment& env, const HostTranslateInfo& host_info, const IR::Block* block) {")
    check("if (index.U32() >= Info::MAX_CBUFS) {" in get, "the texture pass tracks a constant buffer index past the 18")
    check("if (shift.U32() >= 32) {" in get and "if (op2.U32() == 0) {" in get, "the texture pass keeps a handle shift of 32 or more")
    emit = text("gpu/vk/eden/shader_recompiler/backend/spirv/spirv_emit_context.cpp")
    check("cbufs[" not in emit.replace("cbufs[i].*member_ptr", ""), "the SPIR-V back end indexes cbufs[] unchecked")
    check("ctx.cbufs.at(binding.U32())" in text("gpu/vk/eden/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"),
          "GetCbuf indexes cbufs[] unchecked")
    tp = text("gpu/vk/eden/shader_recompiler/frontend/maxwell/translate_program.cpp")
    check('throw NotImplementedException("Local memory of {} bytes"' in tp and 'throw NotImplementedException("Shared memory of {} bytes"' in tp,
          "translate_program takes any local or shared memory size")
    check("if (program.mesh.max_vertices == 0 || program.mesh.max_primitives == 0) {" in tp, "translate_program takes a mesh of no outputs")
    check("want > 0xFFFFFFFFull ? 0xFFFFFFFFu" in text("gpu/vk/vk_shader.cpp"), "vk_shader.cpp truncates the header's local memory size")
    for rel in ("gpu/vk/compat/common/div_ceil.h", "gpu/third_party/suyu/common/div_ceil.h"):
        check("(static_cast<D>(number) + divisor - 1) / divisor" not in text(rel), rel + " wraps near the type's top")
    prog = text("gpu/vk/vk_progcache.cpp")
    check("bool ok = !r.bad && n >= 1 && n <= 8;" in prog, "vk_progcache.cpp takes a program record with no stages")
    draw = text("gpu/vk/vk_draw.cpp")
    pred = draw[draw.index("predicted_sample_path().c_str(), \"rb\")"):]
    pred = pred[:pred.index("std::thread([sample]")]
    check("std::erase_if(sample, [](PipeNote& n) { return !note_sane(n); });" in pred,
          "the predicted pipelines' sample goes to the driver without note_sane")
    handle = block(draw, "uint32_t handle_of(const NvnState& s, const Stage& st, const D& d, uint32_t k) {")
    for shift in ("d.size_shift & 31", "d.shift_left & 31", "d.secondary_shift_left & 31"):
        check(shift in handle, "handle_of shifts by a record's " + shift.split()[0] + " unbounded")
    check(not re.search(r"<< d\.(size_shift|shift_left|secondary_shift_left)\b(?! & 31)", draw.replace("(d.", "d.")),
          "vk_draw.cpp shifts by a descriptor's shift unbounded somewhere")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cxx", default=shutil.which("clang++") or shutil.which("g++") or "c++")
    a = p.parse_args()
    check_asserts(a.cxx)
    check_sources()
    if failures:
        sys.exit(1)
    print("PASS: malformed input is refused or defined, in both recompilers and the renderer's readers")


if __name__ == "__main__":
    main()
