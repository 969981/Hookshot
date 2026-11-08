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

Requirements: Python 3 with `lz4`, `numpy`, `capstone`, `unicorn` (2.1), `zstandard`; emsdk's clang at `C:\emsdk` (or `EMSDK`);
for the Vulkan back end, Eden's CPM package cache (found in mkw-recomp's `work/eg` or `..\Eglantine\.cache\cpm`, or
`RX_CPM`).

Run everything with `python -I` from this folder.

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

Scripted test input (`HOS_INPUT_SCRIPT` and `HOS_INPUT_LIVE`) is applied before the window-focus check. A log saying
the game window is not in front does not mean those presses were discarded. `HOS_HEADLESS` and
`HOS_WINDOW_NOACTIVATE` change window creation, not this input routing. The game must still reach a state that reads
and responds to the supplied controller input.

Physical keyboard and mouse input stays with the foreground window. Physical controllers can also play in the
background when the existing "Controller in background" option or `HOS_INPUT_BACKGROUND=1` enables them;
`HOS_INPUT_BACKGROUND=0` overrides that option. `HOS_INPUT_EXCLUSIVE=1` controls merging the player's devices while
the script's existing exclusive interval is active; it is not a focus override.

## macOS (Apple Silicon)

The macOS builder and generated game are native arm64, with no Rosetta dependency. It uses SDL2 for the window and
input, and the existing WebGPU renderer through `wgpu-native`'s Metal backend; the Vulkan backend is not built on
macOS. The executable targets macOS 13.3 or newer because Apple's C++20 floating-point formatting support requires it.

The `macOS platform` workflow uploads a `HookshotBuilder-macos-arm64` artifact containing a command-line builder,
the Hookshot source, a native arm64 Python runtime and modules, SDL2, static FFmpeg, and Metal-enabled `wgpu-native`.
It does not contain game data or a built game. Download and unpack it on the target Mac; Xcode Command Line Tools
must be installed there. Double-click `HookshotBuilder.app` to choose your own `.nup` dump and output folder, or
use the command-line launcher:

```sh
./HookshotBuilder/HookshotBuilder.command --nup /path/to/game.nup --out /path/to/game-folder --jobs 4
```

For development outside the artifact, use arm64 builds of SDL2 2.26 or newer and `wgpu-native`. `wgpu-native` must
include its matching `include/webgpu/webgpu.h` and `lib/libwgpu_native.dylib`, built with Metal support. Set
`HOOKSHOT_FFMPEG` to the static FFmpeg prefix (or provide FFmpeg through `pkg-config`) and build natively:

```sh
export WGPU_NATIVE=/path/to/arm64-wgpu-native
export HOOKSHOT_SDL2=/path/to/arm64/libSDL2-2.0.0.dylib
export HOOKSHOT_FFMPEG=/path/to/arm64/ffmpeg
python3 -I nx2/make_game.py --nup /path/to/game.nup --out /path/to/game-folder --jobs 4
```

The normal builder command validates the arm64 compiler, SDL2, FFmpeg and `wgpu-native` before lifting the game,
then places `hookshot`, `libSDL2-2.0.0.dylib` and `libwgpu_native.dylib` beside `image.bin` in the output folder;
FFmpeg is statically linked when `HOOKSHOT_FFMPEG` points to the packaged static libraries. The macOS FFmpeg build
uses its native AV1 decoder to avoid compiling an additional dav1d dependency in CI.
For lower-level development, `nx2/lift.py --standalone --write` followed by `nx2/build.py --standalone --jobs 4`
writes the executable and libraries under `work/sa/build/`. Set `RX_BACKEND=metal` to make the renderer choice
explicit (Metal is the macOS default). As on other platforms, the local game dump is required to generate and build
the game. The workflow packages and validates builder dependencies without running a game build; it is not a
notarized app release.

## Layout

| Path | What |
|---|---|
| `DESIGN.md` | why mkw-recomp's pipeline, the architecture, MKW's lessons as rules, stages, testing |
| `nx2/` | the lift pipeline and its tests (mkw-recomp's, made game-neutral; `kit.py` has the paths) |
| `host/` | the standalone Horizon runtime (TOTKPort's `hos/` as adapted by mkw-recomp), with the native NVN2 layer `nvn_native.c` |
| `gpu/vk/` | the Vulkan back end (`hookshot_vk.dll`), with Eden's shader recompiler and sirit |
| `gpu/render/`, `gpu/third_party/` | the WebGPU renderer (Metal on macOS), its op stream (`rx_nvn.h`), suyu's decoders |
| `scripts/` | test runs and the profile reader |
| `third_party/` | a64-kit (unmodified), libopus, zbic (Atmosphere's zstd with ZBIC, for unpacking only) |

License: GPL-3.0-or-later, as mkw-recomp: a64-kit's translator is suyu's (GPL-2.0-or-later) and Eden's code is
GPL-3.0.
