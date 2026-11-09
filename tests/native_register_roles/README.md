# Native register-role regression

The instructions and values in `../native_register_roles.cpp` are independently
authored from public encoding fields and the lowering's documented reserved
register boundaries. No game words, programs, caches or SDK material are used.

Two separate role corrections are exercised:

- R2UR (`0x3c2`) has one scalar GPR source at bits 24–31. Its destination at bit
  16 is uniform, not a native GPR. Scalar sources near R239 must not be widened
  into pairs/quads, and sources aliasing an emulated uniform mirror must remain
  separate from that uniform destination.
- LOP3 immediate (`0x812`) has GPRs at bits 16, 24 and 64. Bits 32–63 are one
  immediate, even when its low byte resembles a scratch register. The exclusion
  is specific to this known form; the register form's real Rb remains tracked.

The small Python fixture runs actual production lowering. The full CMake target
uses the actual Maxwell CFG/REGMAP translator, SSA/constant propagation passes
and SPIR-V backend. R2UR stores must depend on a shuffle of the authored source
value, while LOP3 stores must equal the authored XOR result after real IR
constant propagation. All emitted modules are validated without executing a GPU.

This branch includes the original-operand marker survey from
[PR #759](https://github.com/NotRexed/Hookshot/pull/759) by pelicanux through a
normal merge of exact published `add0937`, retaining its author/history. The
register-role corrections remain separate, and the conflict guard stays intact.
Two additional LOP3 cases strictly check the stored XOR value when an early
destination marker could collide with a later Ra or Rc. The original sixteen
pipeline cases remain. Original 232 and the standalone published PR759 header
are useful negative controls for the two role counterexamples selected by
`--expect-original-rejections`. An authored pre-dependency collision probe
produced wrong stored values even though its SPIR-V modules were valid.

Primary encoding reference:
[Mesa NAK](https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa/+/b1fe47e94409d81924c8663f58fac97e66ab4527/src/nouveau/compiler/nak/sm70_encode.rs),
`OpR2UR`, `OpLop3`, `encode_alu_base` form4 and `encode_alu_imm`.

Configure with Clang C++20, `-DREPO_ROOT=<repo>` and
`-DSPIRV_HEADERS_ROOT=<SPIRV-Headers>`. Run `native_register_roles` with an output
directory and validate its modules using `spirv-val --target-env vulkan1.3`.
These checks establish field roles and translation, not live rendering or the
cause of other shader failures.
