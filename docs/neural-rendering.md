# DLSS 5 neural rendering

## What it is, and the mistake that cost a week

DLSS 5 neural rendering (DLSS-NR; NVIDIA's own name for it is *3D-Guided Neural Rendering*) was
refused in this project three times on the grounds that it needs ray tracing. That was wrong, and
the record is kept here because the reasoning was plausible and still wrong.

The refusal conflated DLSS-NR with **DLSS Ray Reconstruction** (`sl.dlss_d`, NGX's `DLSSD`). Ray
reconstruction denoises a ray-traced image and needs a G-buffer -- albedo, normals, roughness --
which a recompilation of a 1998 rasterized game does not have and never will. DLSS-NR is a different
feature with a different plugin and different inputs. Streamline's own public headers settle it:

```
sl_core_types.h:210  kBufferTypeUpliftInputColor  = 70
sl_core_types.h:213  kBufferTypeUpliftOutputColor = 71   // may alias the input (in-place uplift)
sl_core_types.h:215  kBufferTypeUpliftControlMask = 72   // optional, 4-channel
sl_core_types.h:253  kFeatureDLSS_NR              = 1004
```

Colour in, colour out, plus depth and motion vectors. It is an image-enhancement pass, not a
denoiser, and it is why the feature can be put over rasterized content at all.

## Where it runs here

In the present, over the frame the game finished, before the menu and the FPS counter are drawn on
top (`gpu/vk/vk_replay.cpp`, `present_image`). It is not an upscaler and is not part of the
upscaling path: input and output are both at display resolution, and `Upscaling` / `Frame
generation` are untouched by it. It runs on every presented frame, so with frame generation at 4x it
runs four times per game frame -- each shown frame gets enhanced, which is the point, but it is four
times the cost.

The model is handed a flat depth and no motion at all, with an identity camera and no jitter
(`scene_constants`). That is deliberate, not laziness: by this point in the frame the picture is
already upscaled and already has the HUD on it, so the scene's real depth buffer does not describe
the image being handed over. Giving it the real camera would have it reproject detail that is not
where it thinks it is. The renderer *does* have real scene depth and camera motion (`vk_camera_motion.cpp`),
and feeding them is the obvious next experiment -- it would mean moving the pass before the HUD.

Dagor's shipping integration also runs NR on a **tonemapped, display-referred SDR image** and
inverse-tonemaps afterwards. Here the window image already is that, which is a second reason this is
the right place in the frame.

## The options struct

NVIDIA ships no `sl_dlss_nr.h`. The struct that configures the model is NDA-only, so
`gpu/vk/vk_dlssnr_options.h` declares it, and that file is the riskiest thing in this feature: it is
passed to code we did not compile, so a field at the wrong offset is not a crash but the model
reading one setting as another. Ten `static_assert`s on the offsets are there to make a future
Streamline that moves a field a build error.

Two sources are genuinely independent of each other and agree exactly on field order, count and
types: **DagorEngine** (BSD-3-Clause), which compiles against NVIDIA's real header, and **openNR**
(MIT), which recovered the layout from a prerelease Streamline 2.13 `sl.dlss_nr.dll`.

Three things this project previously had wrong, all inherited from openNR's defaults and all
corrected here:

| | was | is | why |
|---|---|---|---|
| `preset` | 1 | **0** | 0 is the default model; 1-7 pin presets A-G. A default of 1 silently pins preset A. |
| `skinStructureStrength` | -1 ("leave it to the model") | **1.0** | No source states a negative sentinel. openNR defaults -1 with no comment; Dagor's real header and two others default 1.0, and the documented range is 0-1. The "-1" meaning was read into openNR's default and then repeated -- including by me, in Elysium. |
| `useAutoMask` | `sl::Boolean` | **`uint32_t`** | `sl::Boolean` is `enum Boolean : char`: one byte, leaving three bytes of *uninitialized padding* inside a struct handed to NVIDIA's code. Same offsets either way. |

`globalToneStrength` and `performanceMode` are in the ABI and **ignored by Streamline 2.14's
plugin**. They are sent correctly anyway and the menu rows say so, because a row that looks broken
is worse than a row that explains itself.

### Not corroborated

The struct **GUID** `{29DFDFE0-...}` and `kStructVersion3` rest on openNR alone -- Dagor includes the
real header and so never restates them. If `slDLSSNRSetOptions` answers `eErrorInvalidParameter`,
that GUID is the first suspect, and `vk_dlssnr.cpp` says so in the log rather than leaving the next
person to rediscover it.

## Why a second NVIDIA path

Super resolution and frame generation go through NGX directly (`gpu/vk/vk_ngx.h`), which is measured
and works. DLSS-NR cannot: the public NGX SDK does not name the feature, and the only entry point
that configures it, `slDLSSNRSetOptions`, lives inside `sl.dlss_nr` and comes out through
`slGetFeatureFunction`. So Streamline sits beside the NGX session rather than replacing it, and it is
asked to load **only** `kFeatureDLSS_NR` -- not Streamline's own DLSS or DLSS-G plugins -- so that two
NGX sessions never come up against the same device.

`eAllowOTA` and `eLoadDownloadedPlugins` are deliberately not set: they let Streamline fetch plugins
over the network and bootstrap `nvngx_update.exe`, and a game has no business starting an updater.

## NGX direct: the second backend

`gpu/vk/vk_dlssnr_ngx.{h,cpp}` creates the same model as NGX feature 18 (`NVSDK_NGX_Feature_Reserved18`), over the one NGX
session DLSS super resolution owns (`dlss_ngx_session`), and needs no Streamline at all. The Neural tab's **Backend** row
(`opts.nr_backend`, saved as `nr_backend`; `RX_VK_DLSS_NR_BACKEND=auto|streamline|ngx` over it) chooses: Auto is Streamline
where `streamline::nr_available()`, NGX direct otherwise. `nr::apply` is the present's one call (`vk_replay.cpp`
`present_image`), and every other row applies to whichever runs.

Why it exists: Streamline's own plugin may gate RTX 20-40 even with a patched `nvngx_dlssnr.dll` (the "not measured here"
below), and on Linux there is no Streamline at all while NGX is there.

**The parameter set is openNR's** ([clshortfuse/openNR](https://github.com/clshortfuse/openNR), MIT), from its native
benchmark (`benchmarks/native/d3d11/main.cpp`, `benchmarks/native/include/neural_rendering_direct.hpp`). The public NGX
SDK names the feature id and nothing else. Names and types were taken as facts; no code was copied:

| when | keys | type |
|---|---|---|
| create | `Width`/`Height`/`OutWidth`/`OutHeight`, `DLSSNR.Width`/`Height`, `DLSSNR.InputWidth`/`InputHeight`, `DLSSNR.OutputWidth`/`OutputHeight`, `DLSSNR.Output.Width`/`Output.Height` | uint |
| create | `DLSSNR.Hint.Render.Preset` (0 default, 1-7 = A-G) | uint |
| create | `PerfQualityValue` = sl::DLSSMode - 1 (the Model row: 0 perf, 1 balanced, 2 quality, 5 DLAA) | int |
| create | `DLSSNRComputeScalingRatioCallback`: NGX calls it with a parameter block and it answers `DLSSNR.ScalingRatio` = 1, i.e. no upscaling | void* |
| create | `DLSSNR.ScalingRatio` 1, `DLSSNR.Scale` 1 / `DLSSNR.Upscaling` 0 | float / int |
| evaluate | `DLSSNR.Color`, `Output`, `MVec`, `Depth` (`NVSDK_NGX_Resource_VK`, images in `GENERAL`), `UI`/`UIAlpha` null | void* |
| evaluate | `DLSSNR.<Color/Output/MVec/Depth>Subrect<BaseX/BaseY/Width/Height>` | uint |
| evaluate | `Jitter.Offset.X/Y`, `DLSSNR.JitterOffsetX/Y` 0, `DLSSNR.MVecScaleX/Y` 1 | float |
| evaluate | `DLSSNR.DepthInverted` 0, `Enabled` 1, `Reset` (first frame of a feature), `UseAutoMask`, `UICorrection` 0 | int |
| evaluate | `DLSSNR.Intensity`, `LocalToneStrength`, `LocalStructureStrength`, `GlobalToneStrength`, `SkinStructureStrength` | float |
| evaluate | `DLSSNR.Style` | uint |

All of them are set with NGX's C functions (`NVSDK_NGX_Parameter_SetUI/SetI/SetF/SetVoidPointer`), not the
`NVSDK_NGX_Parameter` virtuals: NGX's Windows object is MSVC-built and llvm-mingw's vtable does not line up with it
(`vk_dlss.cpp`). Preset and Model are creation parameters, so changing either remakes the feature; the rest go with every
evaluation. A refusal is held against those exact settings (any row change tries again), and six refusals turn it off
for the run. The device is created with whatever extensions NGX names for feature 18
(`NVSDK_NGX_VULKAN_GetFeatureDeviceExtensionRequirements`, `vk_main.cpp`), asked whatever the switch says because the
device exists before a player can turn it on.

Its inputs are the Streamline path's: the window image copied into an `R8G8B8A8_UNORM` image of its own, a flat 0.5 `R32`
depth, zero `R16G16` motion, no jitter. openNR's benchmark feeds `RGBA16F`; whether 8-bit colour is accepted by feature
18 on Vulkan is not known yet.

**Availability.** It reads `DLSSNR.Available` and `DLSSNR.FeatureInitResult` from NGX's capability parameters (and
`DLSSNR.NeedsUpdatedDriver` / `MinDriverVersionMajor/Minor` by the naming every NGX feature's keys follow; a key NGX does
not answer is reported as such) and logs them. Where NGX does not answer for the feature at all, the first frame's
`CreateFeature` is the answer. When it is refused:

- **Linux**: NGX loads features as `libnvidia-ngx-<name>.so*` from the folders `vk_dlss.cpp ngx_init` gives it, and then
  the game folder itself (`dlss_ngx_runtime` lists them all), and NVIDIA ships no `libnvidia-ngx-dlssnr.so` -- SDK 310.9.16's Linux libraries are
  dlss, dlssd and dlssg only. The log and the Neural tab say exactly that, and that the Windows build under Proton is the
  way to the feature today. The day NVIDIA ships the library, putting it in the game folder's `ngx` folder should be all
  it takes. That is a prediction, not a measurement.
- **Windows**: the same reasoning as `vk_streamline.cpp explain_refusal` -- the GPU's generation, the `nvngx_dlssnr.dll`
  found in NGX's folders and whether Windows trusts its signature (only in a build that links wintrust, i.e. one with
  Streamline's headers), and NGX's signature override, read and never written.

### What is measured for NGX direct, and what is not

- Measured, 2026-11-09, Linux, RTX 5060 Ti (Blackwell), driver 615.71.09, NGX SDK 310.9.16, windowed on Xvfb (a
  `HOS_HEADLESS=1` run never reaches `present_image`, so it never reaches either backend):
  - NGX asks for `VK_KHR_buffer_device_address` for feature 18 (`vk: dlss-nr wants ...: enabled`).
  - With Backend Auto, the run chose NGX direct (no Streamline on Linux); with NGX direct, the same.
  - NGX's own log (`RX_VK_DLSS_LOG=1`) shows it looking for `libnvidia-ngx-dlssnr.so` in the game folder's `ngx`, then
    `~/.local/share/hookshot/ngx`, then the game folder itself, and finding none.
  - NGX answers `DLSSNR.Available = 0`, `DLSSNR.FeatureInitResult = 0xbad00004` (`FAIL_FeatureNotFound`): the core
    knows the feature and has no library for it. The Linux message is logged once; the game went on to gameplay
    (~2,185 draws a frame, ~36 fps) for 10,000+ presents.
  - Nothing on Linux has created or evaluated feature 18, because there is nothing to create it from.
- Not measured: anything on Windows. No run has created feature 18 through NGX on Vulkan, evaluated it, or seen a frame
  from it; whether NGX's Windows core answers `DLSSNR.Available` for a patched DLL on RTX 20-40; whether
  `PerfQualityValue` and `Hint.Render.Preset` are taken at creation as openNR's D3D11/D3D12 benchmark takes them; and
  whether openNR's D3D parameter set is the whole Vulkan set (its benchmark loads the snippet itself, not through NGX's
  core, so the core's own checks are not exercised by it).

## What a player needs

Windows x64 only. Streamline's interposer and plugins are DLLs; on Linux, run the Windows build under
Proton, which is where this was measured for Elysium. These go beside the game, or in a `streamline`
folder beside it, or wherever `RX_VK_STREAMLINE_DIR` points:

```
sl.interposer.dll     the loader; its signature is checked before it is loaded
sl.common.dll         the interposer needs it whichever feature loads on top
sl.pcl.dll            likewise
sl.dlss_nr.dll        the Streamline plugin
nvngx_dlssnr.dll      the model -- must sit in the same folder as sl.dlss_nr.dll
```

Two lessons from other people's measurements, both worth not rediscovering: the plugin and the model
**must be colocated** (in separate folders the support query returned `FeatureNotSupported`), and the
`sl.*` trio **must be version-matched** -- a 2.13 plugin with 2.14 common/interposer is not a
supported combination.

Floor: **Streamline 2.14.1**, the first public tag carrying `kFeatureDLSS_NR` at all (2.14.0
introduced the plugin; v2.12.0 and earlier have neither the feature nor the uplift tags). CI builds
against v2.14.16.

**GPU**: NVIDIA's shipped `nvngx_dlssnr.dll` is **Blackwell-only** (its kernels are SM120). On an RTX 20, 30 or 40
NGX refuses it with `0xbad00001` (`FAIL_FeatureNotSupported`), and a probe on an RTX 4070 SUPER got
`AdapterUnsupported` from Streamline. The arch gate and the kernels are both inside that one file, which is why the
community route works: [dlssnr-patcher](https://github.com/dev-camo/dlssnr-patcher) (GPL-2.0) recompiles its PTX for
`sm_75`/`sm_86`/`sm_89`, rewrites the FP8 instructions older cards lack and widens the architecture the file declares,
and [openNR](https://github.com/clshortfuse/openNR) (MIT) reconstructs a pinned 310.9.16 compatibility DLL, an FP16 one
reported at about 3.5 ms on an RTX 4070 against 6 ms for the FP8 port. Both work from the player's own copy and ship
nothing of NVIDIA's.

Hookshot needs nothing special for them -- it loads whatever `nvngx_dlssnr.dll` sits beside `sl.dlss_nr.dll`, and
signature-checks only `sl.interposer.dll` -- but **a patched file has no NVIDIA signature, and NGX loads an unsigned
feature only with its signature override** (`HKLM\SOFTWARE\NVIDIA Corporation\Global\NGXCore`, DWORD
`EnableSignatureOverride` = 1), the same switch DLSS wrappers have always needed. That is the player's to set:
`vk_streamline.cpp explain_refusal` reads it for the log and never writes it. When Streamline refuses the feature, that
function logs the GPU's generation, whether the `nvngx_dlssnr.dll` there is NVIDIA's (signed) or a patched one, and the
override's state, says which of them is missing, and gives the Neural tab a one-line reason.

Not measured here: the patched DLLs through *Streamline* on *Vulkan*. Every report found used NGX directly from D3D12
(RenoDX's add-on, OptiScaler). If Streamline's own plugin turns out to gate on the architecture as well, the log line will
say `AdapterUnsupported` with a signed-off DLL and the override on, and the answer is the NGX-direct path for feature 18
(above, the Backend row's NGX direct) -- which is also the one that would bring NR to Linux.

## On AMD

What exists for neural rendering on an AMD card, as of November 2026, and what of it can be used here:

- **[DLSS-NR on AMD](https://github.com/danielblnc/DLSS-NR-on-AMD)** (NeuralHub) reimplements the model in HIP and hooks
  D3D12 games. Its licence forbids redistribution, bundling and reverse engineering, and it is D3D12 only -- Vulkan is
  "planned". Hookshot is Vulkan, so it cannot hook it, and may not ship it.
- **[OpenDLSS-NR](https://github.com/maanHimself/OpenDLSS-NR)** (MIT) is a Vulkan reimplementation of the 310.8.0
  network, bit-exact block by block. Its kernels need `VK_NV_cooperative_matrix2` and `VK_NV_cuda_kernel_launch` (the
  fast path is PTX), so it is NVIDIA-only as it stands, and it reads the weights from a model directory that "nothing in
  this repository produces". An AMD port means rewriting its GEMMs for `VK_KHR_cooperative_matrix` with
  `VK_EXT_shader_float8` (RDNA 4) or an FP16 fallback (RDNA 3), and a way for the player to get the weights out of their
  own `nvngx_dlssnr.dll`. That is a project of its own, and it is the most promising one.
- **AMD's own machine-learning upscaler is now here**: FSR 4, on the Upscaler row (`docs/upscaling.md`).

## What is still unproven

Being plain about this, because the rest of this file reads confident:

- **Nobody has been found shipping or measuring DLSS-NR on Vulkan.** Dagor's NR path is written to be
  API-agnostic and does handle Vulkan resources, but every measurement found was D3D. This is the
  least-trodden configuration of the feature.
- Elysium reached start-up and feature support but **never drove `slEvaluateFeature` over real
  images**, so nothing in this project has yet seen the model produce a frame.
- Whether a flat depth and zero motion are *acceptable* rather than merely *accepted*. The four tags
  are declared required and are all supplied; whether the model wants physically meaningful depth is
  untested. The ReShade addon's present-time hook strongly suggests dummies are tolerated.
- In-place aliasing of tags 70 and 71. NVIDIA's header says it may alias; no integration found does
  it, and this one uses two images.
- The upper bound of `style`. Three values are documented (Default, Natural, Cinematic); the row
  offers exactly those three.
