# DLSS Super Resolution

Optional, and Windows-only. With DLSS off, or without the bridge DLL, or on a GPU that is not NVIDIA's, the Vulkan
renderer records the game's own TAAU network exactly as before.

The game draws its 3D smaller than the output and resolves it with one temporal network per ratio of those sizes.
DLSS takes that dispatch (`vk_draw.cpp`, `taau_dlss`). The networks Hookshot already knows, at the 2048x1152 and
2560x1440 outputs, are:

| Ratio of the output | Program key |
|---|---|
| 12/12 (DLAA, and the Resolution row's 100%) | `9dc98871eb7fc101` |
| 9/12 (the Resolution row's 75%) | `fd029a317a9a4b94` |
| 8/12 (DLSS Quality) | `6676619641320ba7` |
| 7/12 (Balanced) | `f10782f9e58b6a7c` |
| 6/12 (Performance, and the Resolution row's 50%) | `72ba090c70fe2614` |
| 4/12 (Ultra Performance) | `0775215f8da720e6` |

Any other network of the same shape is taken too: cooperative-matrix code, one colour texture in, one B10G11R11 image
out, the input no larger than the output and the same aspect. That is how the 3840x2160 output is covered. The keys
are logged the first time each one is taken (`dlss: TAAU network ... taken`).

The network has no motion vectors, depth or jitter of its own. It is given the scene colour only, stored as the square
root of the linear colour (the same thing `shaders/taau_upscale.comp` reads). `shaders/dlss_prep.comp` makes that
linear, builds motion by reprojecting the scene's depth with the camera found in the network's constants, and writes
depth. NGX evaluates DLSS into an RGBA16F image, and `shaders/dlss_out.comp` stores that into the network's B10G11R11
output. The rest of the frame is unchanged.

NGX's quality mode is the DLSS quality row's, not a guess from one ratio:

| DLSS quality | 3D size | NGX mode |
|---|---|---|
| Quality | 8/12 | 2 (MaxQuality) |
| Balanced | 7/12 | 1 (Balanced) |
| Performance | 6/12 | 0 (MaxPerf) |
| Ultra Performance | 4/12 | 3 (UltraPerformance) |
| DLAA | 12/12 | 5 (DLAA) |
| Auto | the Resolution row | from the ratio of the sizes (0.99 and up is DLAA, then 0.62, 0.54, 0.42) |

Ultra Performance at 2560x1440 draws 854x480, and mode 3 allows only 853x480. Create snaps into the mode's range and
evaluate reads that many pixels. It does not switch to another mode. A create that still fails is logged and, after
three failures, DLSS stays off until the next start.

Motion is the camera's only. A character that moves on its own gets the motion of what is behind it, so a faint trail
is possible. The projection has no jitter, so the anti-aliasing is weaker than DLSS in a PC game that jitters. The
default motion row flips NDC Y, which is the one that is clean in this game. `RX_DLSS_DEPTH_INVERTED=1` treats the
depth as reversed-Z.

The DLSS row's model is an NGX render-preset hint: CNN is preset E, Transformer is K, Transformer 2 is M, and NVIDIA
default is 0 except DLAA, which asks for K. If NGX refuses the preset, the bridge tries 0. Preset E is deprecated in
DLSS 310.x and may take that path.

## What a player needs

Both files sit beside `hookshot_vk.dll` (the game's `bin` folder):

- `hookshot_ngx.dll`, built from `gpu/vk/ngx` as below
- `nvngx_dlss.dll`, from NVIDIA's DLSS SDK, `lib/Windows_x86_64/rel/nvngx_dlss.dll`

NGX is told that folder. An NVIDIA driver new enough for the DLL is required. An RTX GPU is required (the bridge
checks vendor `0x10DE`). Linux builds the renderer without calling NGX. The menu row is there, and turning it on
leaves the game's upscaler in place.

`RX_DLSS=0..4` and `RX_DLSS_MOTION=0..2` in `hookshot_env.txt` set the rows at start. `RX_DLSS_OFF=1` does not load
the bridge, and keeps DLSS off for that run even if the menu asks.

On an 8 GB card the game is already close to the driver's video-memory allowance in a busy town. DLAA draws the 3D at
the full output and DLSS adds its own images, so the allowance can be passed and the frame rate falls. Quality or
Balanced is the better choice there. The same pressure is what
[#993](https://github.com/NotRexed/Hookshot/issues/993) and
[#995](https://github.com/NotRexed/Hookshot/issues/995) collect, and what
[#961](https://github.com/NotRexed/Hookshot/pull/961) and
[#963](https://github.com/NotRexed/Hookshot/pull/963) reduce on the renderer's side. DLSS does not raise those
budgets. While it is running, the log records a VRAM line every 300 presents, including `OVER` when the driver's
allowance is passed.

## Building the bridge

NVIDIA's [DLSS SDK](https://github.com/NVIDIA/DLSS) is under NVIDIA's own license, which forbids placing the SDK under
an open-source license. None of it is in this repository: not `nvngx_dlss.dll`, not the headers, not `nvsdk_ngx_s.lib`.
Do not add them. Check the SDK out somewhere else.

The bridge is a small MSVC DLL because the SDK ships only as MSVC static libraries and the renderer is built with
llvm-mingw. On Linux, with [llvm-mingw](https://github.com/mstorsjo/llvm-mingw) and an [xwin](https://github.com/Jake-Shadle/xwin)
splat of the Windows SDK and the UCRT:

```sh
MINGW=<llvm-mingw> XWIN=<xwin splat> DLSS_SDK=<NVIDIA/DLSS checkout> VK_HEADERS=<Vulkan-Headers> \
  gpu/vk/ngx/build_ngx.sh
```

The script writes `hookshot_ngx.dll` under `gpu/vk/ngx/out` (gitignored) and does not copy `nvngx_dlss.dll`. Copy both
into the game's `bin` folder yourself. The game build (`nx2/build.py`) does not run this script and does not need the
SDK.

Diagnostic switches, off unless set: `RX_DLSS_NGX_LOG=1`, `RX_DLSS_LOG_CANDIDATES=1`, `RX_DLSS_LOG_CB=<frames>`,
`RX_DLSS_JITTER=<x>,<y>`, `RX_DLSS_NO_SQRT=1`, `RX_DLSS_MV=<slot>`, `RX_DLSS_COLOR=<slot>`, `RX_DLSS_DEPTH=<slot>`,
`RX_DLSS_KEYS=<key prefixes>`.

DLSS Frame Generation is not included. Hookshot's own frame generation still runs on top of the picture DLSS wrote.
