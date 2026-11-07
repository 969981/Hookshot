# The game module's C library headers

`build.py --part game` compiles the lifted game with `-nostdlibinc -I nx2/rt/crt`: these headers stand in for
Microsoft's or glibc's, so the player's PC needs no Windows SDK, Visual Studio or libc headers to build the game.
The compiler's own headers (stdint.h, stddef.h, float.h, the intrinsics) still come from clang.

What the module calls (a64-kit's rt/a64_rt.c and the generated a64_suyu.c: logging, getenv, calloc, a clock) goes to
the prebuilt runtime as `hookshot_crt_*` (host/module_crt.c). memcpy and the rest of string.h keep their names, so
clang still inlines them: on Linux the leftover calls bind to the process's libc, on Windows to
nx2/rt/module_crt.c's forwarders.
