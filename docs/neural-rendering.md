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

**GPU**: NVIDIA's shipped `nvngx_dlssnr.dll` appears to be **Blackwell-only** (SM120). A measured
probe on an RTX 4070 SUPER with the official signed 2.13 plugin got `AdapterUnsupported` and
Streamline then ignored the plugin outright; claims of RTX 20-series support trace to openNR's
*recompiled* DLL on its NGX-direct path, which does not claim Vulkan support. So: an RTX 50-series
card is the expectation here, and anything older reporting the feature unavailable is the honest
answer rather than a bug.

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
