# Hookshot (oot-recomp)

**Hookshot** is the port's name (as Skybound is TOTKPort's and BlueShell is mkw-recomp's); the repository folder
stays `oot-recomp`. The game builds as `hookshot.exe` + `hookshot_vk.dll`.

A static recompilation of the Switch 2 *The Legend of Zelda: Ocarina of Time* remake to native x86-64 Windows:
the game's code lifted to C (NotRexed's a64-kit, Rexcomp's AArch64 kit), the Horizon OS and NVN2 implemented
natively, the GPU work replayed on Vulkan. It is a fork of mkw-recomp (`..\mkw-recomp`, Mario Kart World), whose
paths it keeps so MKW's fixes apply here.

**No game files are in this repository.** The dump is read where it lies (`work/dump.txt`); everything made from it
(modules, image, lifted C, builds, runs) goes to `work/`, which git ignores.

Status: the dump is read and lifted (100 % translated, the instruction test passes); the standalone build is next. Plan, stages and the lessons carried over from MKW: [DESIGN.md](DESIGN.md).

## Use

Requirements: Python 3 with `lz4`, `numpy`, `capstone`, `zstandard`, `unicorn` (2.1); emsdk's clang at `C:\emsdk` (or `EMSDK`);
for the Vulkan back end, Eden's CPM package cache (found in mkw-recomp's `work/eg` or `..\Eglantine\.cache\cpm`, or
`RX_CPM`).

Run everything with `python -I` from this folder.

Targets (`nx2/target.py`): `build.py` and `make_game.py` take `--target windows` (the default; `RX_TARGET`) or `linux`.
From a Linux PC, `windows` is a cross-build with the system's clang and lld-link over a /winsysroot-style copy of
Microsoft's C library and Windows SDK (`scripts/setup-winsysroot.sh`, xwin; `RX_WINSYSROOT`, default
`~/.local/share/hookshot/winsysroot`), and the game runs with Proton or Wine. `linux` is native: `hookshot_rt` +
`libhookshot_vk.so`. The host's Win32 calls are answered by `host/linux` (`windows.h` and `win32.c`: Win32 over
POSIX), the window, keyboard and mouse are SDL3's (`host/linux/display_sdl.c`, Wayland first, else X11), waveOut plays
through SDL3's audio, and the controllers are SDL3's on both systems (`host/pad_sdl.c`). The tools that run during the
build (zbic, the tests) are always built for the PC they run on.

### Dependencies and release folders

`scripts/fetch-deps.sh [linux|windows|all]` fetches what the repository does not hold, at pinned versions with their
sha256: the Vulkan and SPIR-V headers and VMA (`RX_CPM=work/deps/cpm`), FFmpeg's LGPL shared builds, SDL3 (Windows'
DLL; for Linux built from its source into `work/deps/linux-sdl3`, which needs cmake, make and Wayland's, X11's,
PipeWire's, PulseAudio's, ALSA's and udev's headers), LLVM's libc++ (`work/deps/linux-libcxx`), and the release
folders' Python and LLVM.

A Linux release is built on Ubuntu 22.04 (glibc 2.35, so it runs on Debian 12 and later) with
`RX_SDL3=work/deps/linux-sdl3` (libSDL3.so.0 bundled beside the runtime, rpath `$ORIGIN`) and
`RX_LIBCXX=work/deps/linux-libcxx` (libc++ linked statically into the renderer and the C++ helper: 22.04's libstdc++
has no `<format>`, and the release then asks for no libstdc++). `ci/github/release.yml` does this and tests both
folders in bare systems; `ci/github/cross-windows.yml` cross-builds the Windows one on Linux. Both are inactive (kept
out of `.github/workflows/`).

The split build (`nx2/module.py`): `build.py --part runtime` makes the game-independent runtime once
(`work/runtime-<target>`: `hookshot_rt[.exe]`, the Vulkan back end, and the files a module links against), and
`build.py --part game` makes the game module from a lift against it (`hookshot_game.dll` / `libhookshot_game.so`,
in `work/sa/game-<target>`). The module compiles against `nx2/rt/crt` instead of an SDK, so the player's PC needs no
Windows SDK, Visual Studio or libc headers. `scripts/package.py` puts a runtime, the launcher (`launcher/hookshot.c`)
and the build scripts in a release folder; the launcher builds the game module from the player's `.nup` the first time,
and again when the runtime changes (`runtime.stamp` against `game.stamp`). The one-executable build (no `--part`) is
unchanged and stays the development build.

| Command | What |
|---|---|
| `python -I nx2/nup.py <file.nup> --extract work/dump` | the `.nup` dump package to `work/dump/exefs` + `romfs` (newest title; ZBIC NSOs made plain by `nx2/unpack.py`) |
| `python -I nx2/dump.py --dump <folder with exefs/ and romfs/> --set` | what the dump is (Switch 1 or 2, ids, modules, SDK, NVN); `--set` records it in `work/dump.txt` |
| `python -I nx2/lift.py [--write]` | image + discovery + coverage (`work/image/coverage.txt`); `--write` emits the C to `work/gen` |
| `python -I nx2/census.py --nx2` | instruction census per module, after the Switch 2 forms |
| `python -I nx2/test_insn.py [--per 6 --states 12]` | every instruction form the game uses, Unicorn vs lifted C |
| `python -I nx2/test_leaf.py [--count 3000 --states 4] [--calls]` | random whole functions (or call trees), Unicorn vs recompiled native code on the real image |
| `python -I nx2/lift.py --standalone --write` then `python -I nx2/build.py --standalone --jobs 4` | the standalone game: `work/sa/build/hookshot.exe` + `hookshot_vk.dll` |
| `powershell -File scripts/run-vk.ps1 [-Window] [-Seconds 120] [-Name x]` | a muted test run on Vulkan, with screenshots in `work/runs/<name>/shots` |
| `python -I scripts/prof.py <stacks file>` | a profile sampled with `HOS_SAMPLE=<thread>` + `HOS_SAMPLE_FILE=<stacks file>`, named from the linker maps |

The dump can also come from `OOT_DUMP` (its folder) or `OOT_EXEFS`; `OOT_WORK` moves the work folder.

For test runs:

| Variable | What |
|---|---|
| `HOS_HEADLESS=1` | no window; with `RX_VK_SHOTS=<dir>` (and `RX_VK_SHOT_EVERY=<frames>`) the frames are written out |
| `HOS_INPUT_SCRIPT` | timed presses, `"<seconds>:<button>[+...][/<ms>],..."` (`host/nn_hid.c`); `15:A,20:A,...` loads a save |
| `RX_VK_MENU_OPEN=1` or `controls` | the Insert menu open from the first frame, on its Controls page with `controls` |
| `RX_VK_PRECOMPILE=0` | no shader preparation: each shader is translated when first drawn |
| `HOOKSHOT_JOBS` | make_game.py's default `--jobs`, the launcher's builds included |

`hookshot_rt --prepare-shaders` prepares every shader without starting the game (the launcher's "Preparing shaders"
step). A runtime built with libc++ keeps its own program cache, since the records hold C++ library types as they lie.

## Layout

| Path | What |
|---|---|
| `DESIGN.md` | why mkw-recomp's pipeline, the architecture, MKW's lessons as rules, stages, testing |
| `nx2/` | the lift pipeline and its tests (mkw-recomp's, made game-neutral; `kit.py` has the paths) |
| `host/` | the standalone Horizon runtime (TOTKPort's `hos/` as adapted by mkw-recomp), with the native NVN2 layer `nvn_native.c` |
| `gpu/vk/` | the Vulkan back end (`hookshot_vk.dll`), with Eden's shader recompiler and sirit |
| `gpu/render/`, `gpu/third_party/` | the WebGPU renderer the exe falls back to, its op stream (`rx_nvn.h`), suyu's decoders |
| `scripts/` | test runs and the profile reader |
| `third_party/` | a64-kit (unmodified), libopus, zbic (Atmosphere's zstd with ZBIC, for unpacking only) |

License: GPL-3.0-or-later, as mkw-recomp: a64-kit's translator is suyu's (GPL-2.0-or-later) and Eden's code is
GPL-3.0.
