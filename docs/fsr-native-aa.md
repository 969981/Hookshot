# Experimental FSR 4 Native AA

This opt-in Windows/Vulkan path replaces the final neural antialiasing resolve
in the supported OoT v131072 build with AMD FidelityFX FSR 4 Native AA. It keeps
the scene's existing render dimensions. It does not increase internal resolution
or enable frame generation. The default renderer is unchanged.

This is an experiment for review and further testing, not a general-purpose FSR
integration. In particular, motion vectors currently describe camera movement
only. Moving characters, foliage, particles and disocclusions need more testing
and may leave temporal trails.

## Scene-resolution prerequisites

Native AA requires matching scene color, depth and resolve-output dimensions.
Fixed Resolution 100% on current main does not reliably establish that contract:
in the isolated contribution test, the game's TAAU path still received 1920x1080
and wrote 2560x1440, and this implementation did not initialize FSR.
The player liked that image, but that observation is not FSR validation.

The previously validated 2560x1440 setup also carried the fixed-scale cache
refresh from [PR #329](https://github.com/NotRexed/Hookshot/pull/329) and the native
base-scale option in [PR #578](https://github.com/NotRexed/Hookshot/pull/578).
Reproducing that setup requires both separate changes, with `HOS_FIX_RES=1`,
`HOS_SCENE_NATIVE=1` and fixed Resolution 100%, until upstream provides equivalent
behavior. They are not bundled into this PR. The #329 proposal documents
unresolved issues at lower resolution percentages; this is not validation of
those modes. A plain build of this branch on current main is not a verified
standalone Native AA configuration.

## Trying it

Build this branch normally. It vendors only the unmodified MIT-licensed API
headers from [FidelityFX SDK 2.4.0](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/releases/tag/v2.4.0).
No AMD runtime or game files are included, and no SDK installation is needed to
build the default renderer.

For the optional runtime, obtain `amd_fidelityfx_loader_vk.dll` and
`amd_fidelityfx_upscaler_vk.dll` from the official SDK's Vulkan binaries and put
them next to `hookshot.exe`, following AMD's license terms. The loader is opened
by absolute executable-relative path with restricted DLL search flags.

Add these lines to the local `hookshot_env.txt`, then restart:

```text
RX_VK_FSR_NATIVE_AA=1
RX_VK_FSR_SHARPNESS=0.4
```

Sharpening is optional: its default is zero, and finite values are clamped to
0..1. Start with the game's fixed Resolution 100% and a single replay worker
(leave `RX_VK_PARALLEL` unset). Parallel replay with more than one worker disables
this experiment. Restart after changing these settings or the scene resolution.

Check `hookshot_log.txt` for `fsr-native: FSR 4.` followed by `context ready` and
successful `fsr-native: dispatch` lines. The logged dimensions are the actual
color, depth and Native AA dimensions, not the window dimensions. Merely enabling
the environment variable does not prove FSR is running.

The SDK must expose an FSR 4 provider for the GPU and driver. This has been tested
on a Radeon RX 9070 XT. It deliberately does not silently select FSR 3 or FSR 2.
A missing runtime, unavailable provider or unsupported input retains the game's
resolve. Remove `RX_VK_FSR_NATIVE_AA` (or set it to zero) and restart to return to
the default path. Other GPUs and Linux are not validated; Linux always takes
the default path.

## Integration details

- Camera matrices are read through `ub_read` during ordered replay, including
  submitted inline uniform updates. There is no read of live guest memory.
- Full-size, single-sample D32 scene depth and a perspective camera are matched
  to the color input. Unsupported layouts are rejected.
- An eight-phase viewport jitter is part of the dynamic-state cache. Camera
  reprojection produces current-to-previous pixel motion excluding that jitter.
- The input is linear B10G11R11 color before the game's neural AA; the final
  B10G11R11 resolve is replaced before the HUD. The intervening native compute
  passes still execute. Removing them is outside this experiment.
- Two authored compute shaders prepare RGBA16F color/RG16F motion and convert
  the FSR result back to the game's target. Their embedded SPIR-V is built from
  the checked-in GLSL, not extracted from the game.
- History resets on initial use, frame gaps, long pauses, projection changes and
  large camera discontinuities. A failed SDK dispatch disables subsequent FSR
  work and restores the renderer's binding-cache contract for the fallback.

## Limits and review targets

Only camera motion is available; there are no per-object motion vectors,
reactive masks or transparency/composition masks. The first context's dimensions
are fixed for the process lifetime. Other sizes fall back rather than destroying
resources that may still be in flight. Context and resources are retained until
process/device teardown; explicit fence-safe cleanup and resizing remain work
for a broader implementation.

The camera layout and resolve endpoints are specific to the supported game build.
The initial tests cover Kokiri Forest near Saria on Windows, RX 9070 XT, driver
32.0.32015.7029. The player reported a substantial reduction in edge aliasing and
shimmer with comfortable performance. This is a qualitative result, not a
controlled benchmark. Menus, cutscenes, area transitions, independent character
motion, resizing and other hardware need wider validation before enabling the
feature by default. The best local image also used a separate native scene-scale
experiment; that resolution change is not part of this feature.

## Game-free checks

```sh
python -I -B tests/test_fsr_camera.py --cxx g++
python -I -B tests/test_fsr_options.py --cxx g++
```

These exercise projection/inversion, both depth conventions, jitter and motion
direction, invalid matrices, opt-in defaults, platform/parallel gating and
sharpening bounds. They do not substitute for Vulkan validation or gameplay.

With glslang 16.6.0, rebuild the authored shader headers from the repository root:

```sh
glslang -V --target-env vulkan1.2 --spirv-val --vn FSR_PREPARE_COMP_SPV -o gpu/vk/shaders/fsr_prepare_comp_spv.h gpu/vk/shaders/fsr_prepare.comp
glslang -V --target-env vulkan1.2 --spirv-val --vn FSR_RESOLVE_COMP_SPV -o gpu/vk/shaders/fsr_resolve_comp_spv.h gpu/vk/shaders/fsr_resolve.comp
```
