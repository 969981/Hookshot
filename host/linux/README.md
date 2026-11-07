# Linux host layer

This directory is the Linux platform layer adapted from the Linux host layer in
[SkyboundTotk](https://github.com/NotRexed/SkyboundTotk/tree/main/a64-kit/hos/linux).

The shared Hookshot host code continues to use the Win32-shaped API surface. On
Linux, the compatibility headers and implementation map that surface to
POSIX/pthreads/futexes, `dlopen`, `mmap`, and SDL3. The Linux display and movie
implementations replace the Windows display, SDL2 loader, and Media Foundation
files selected by `nx2/build.py`.

Hookshot-specific runtime differences still need validation, especially the
NVN memory/write-watch code and the Vulkan backend.
