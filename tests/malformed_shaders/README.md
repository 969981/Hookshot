# Malformed shader input is refused

A broken or malicious mod, or a game bug, can hand the shader recompiler instruction fields and runtime data the hardware
never encodes. This game-free fixture authors them, calls the production lowering, translators, passes and SPIR-V back end
(`gpu/vk/eden/shader_recompiler`), and expects each to end in `Shader::NotImplementedException`, which the translation's
callers catch (`gpu/vk/vk_shader.cpp`: the program is left out and logged), while the well-formed neighbour of each still
translates. Groups (`--only <group>` runs one):

- **cbuf**: LDC's 5-bit constant buffer index past the 18 buffers (18, 31, 31 in IS mode); LDC .IS from RZ at a negative
  offset, which folds to buffer 0xFFFF + bank (past LDC's and the lowering's checks); a global load whose address is read
  from buffer 0xFFFF (the storage buffer pass tracked it, and the back end looked it up among the 18); a constant buffer
  read at an immediate index 18, 31, 40 (the info pass wrote `constant_buffer_used_sizes[index]` past its end and shifted
  `1U << index`).
- **isberd**: ISBERD's 2-bit shift at 3 and 4-bit size at 4..15, which reached `UNREACHABLE` (`__builtin_unreachable`).
- **folds**: a constant arithmetic shift by 32 or more folded (undefined; now saturated to the sign) and `Common::DivCeil`
  near the type's top (it wrapped to 0, and sized an array by a malformed program's memory size).
- **xfb**: a transform feedback varying of 5 or 255 components at a location's first element, which wrote past
  `output_generics[index]`'s 4 elements.
- **lowering**: 32,767 branches back to the start, one forward and 32,767 exits: the native lowering's per-instruction
  checks scanned every relocation and call, quadratic in the program's size. The check is how the time grows from a quarter
  of the program to all of it (about 4 times kept as they come, about 16 when scanned), not a time a slow machine could miss.

No game, dump, GPU or driver is used. On main before the fix: cbuf crashes (SIGSEGV) at the .IS fold, isberd dies at the
UNREACHABLE, folds gives 0x20000000 for `0x40000000 >> 33` and 0 for `DivCeil(0xFFFFFFFF, 4)`, xfb accepts 5 components,
and lowering grows 14 times.

```sh
CC=clang CXX=clang++ cmake -S tests/malformed_shaders -B build-malformed \
  -DREPO_ROOT="$PWD" -DSPIRV_HEADERS_ROOT=/path/to/SPIRV-Headers
cmake --build build-malformed --parallel 2
build-malformed/malformed_shaders
```
