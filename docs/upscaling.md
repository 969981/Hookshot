# Upscaling and frame generation

This is a plan, not a description of what the renderer does today. It names the integration point,
the input contract, the licensing rules each vendor runtime imposes, and the order the work can land
in. Nothing here is implemented yet; each section below is one pull request.

## What exists today

| | Where | What it is |
|---|---|---|
| `resolution` | `gpu/vk/vk_menu.h`, `nx2/oot_patches.py` | The 3D scale: Auto (the game's dynamic resolution), or 100/75/67/50% of full |
| `upscale` | `gpu/vk/vk_menu.h`, `framegen_scale` | A spatial filter on the finished window image: off, Catmull-Rom, Lanczos |
| Frame generation | `gpu/vk/vk_framegen.cpp` | Image-space interpolation: quarter-size luma, 16x16 block matching within +-32 px, a 3x3 median, then a forward/backward blend per made frame |
| The game's own upscaler | `taau_stand_in`, `gpu/vk/vk_draw.cpp` | OoT evaluates a neural TAAU network (program `fd029a317a9a4b94`). Where the device has no hardware f16 matrix multiply (or with `RX_VK_NO_F16`), a compute stand-in replaces it, because the translated network loses the blue channel and casts the scene yellow; elsewhere the network itself runs. `RX_VK_TAAU_KEYS` overrides either way |
| The per-network log | `gpu/vk/vk_draw.cpp`, `gpu/vk/upscale_contract.h` | Once per network key: its dispatch size, its sampled textures, what each one is in the input contract below, and whether a temporal upscaler could stand in |

Two consequences follow from that table, and they decide the whole design.

**The game already upscales temporally.** It renders the 3D at a dynamic internal resolution and
resolves it with its own network. A vendor upscaler does not need a new low-resolution path invented
for it; it needs to take that network's place. Note that on a card with hardware f16 matrix multiply
the network runs as itself, so a replacement cannot be built inside the stand-in alone: the dispatch
has to be recognized whether or not the stand-in is the thing currently handling it.

**The finished window image is the wrong place.** `upscale` and the frame interpolator both work on
the composited frame, after the HUD. A temporal upscaler there has no depth, no motion and a HUD
baked into its history. That is why the existing interpolator block-matches: it has nothing better to
work from.

## What the baseline actually is

Measured before any of this changes anything, because it decides whether an upscaler is worth
having here at all. Linux, RTX 5060 Ti, driver 615.71.09, headless under Xvfb, a warmed shader
cache, the opening and Kokiri Forest:

| | |
|---|---|
| Frame rate | **57-60 fps**, held over 33,600 presents in one run and 9,000 in another |
| A frame | 1106 draws (0 skipped), 99 dispatches, 148 passes |
| Scene | 1536x864 internal, resolved by the game's own network into a 2048x1152 scene |
| Present | 2560x1440 |

**60 fps is the floor here, not the target.** The game's own limiter lives at `mod + 0xfc` and the
engine's adaptive controller writes any rate from 40 up when variable refresh is on (`+0x231`), so
`HOS_FPS_TARGET=<fps>` can ask for more; the real-time frame step at `main+0x6e6cc4` already keeps
game speed honest at whatever rate comes out. What an upscaler buys against that is the two things
a floor does not give you: a higher internal resolution at the same rate, and the headroom to hold
a higher rate at all. The scene is 1536x864 for a 2560x1440 present — there is a lot of resolution
being left behind, and that is the gap FSR or DLSS closes.

So the argument for this work is image quality and headroom at a given cost. It is not "getting to
60", and this document should not be read as saying the job is done at 60.

## Correction: the game's network is not a temporal upscaler

**A run settles this, and it contradicts the section above.** With the per-network log reading the
contract, the gameplay network reports:

```
vk: network fd029a317a9a4b94: dispatch 32x36x1, 1 textures 1 images: 0:1536x864; no upscaler inputs
```

**One sampled texture, one written image, and nothing else.** No motion, no depth. Every one of the
eleven networks in the game is the same shape: at most two sampled textures, never a motion or depth
target among them.

So OoT's upscaler is a *single-image* neural upscaler, not a motion-vector temporal one, and the
premise this document started from — that a vendor upscaler takes that dispatch's place — is wrong.
FSR 3.1 and DLSS Super Resolution are temporal: they need motion and depth, and this dispatch is
never handed either. `temporal_possible()` therefore refuses at it, correctly, and will keep
refusing however the backend is wired.

What the run does confirm is that the opportunity is real: the input is **1536x864** against a
2048x1152 scene, so the game already renders the 3D at 75% and upscales. There is a genuine upscale
to improve on.

What it means is that the inputs have to be **assembled** rather than taken:

- **Depth** exists — the census found D32 depth attached to the scene passes — but it has to be
  carried to the resolve rather than read off this dispatch.
- **Motion** does not exist anywhere in the frame as a target. It has to be derived, and the only
  cheap source is the camera, which gives camera-only motion: correct for a still world, ghosting
  on anything that moves by itself.

That is precisely what #577 does, at the final resolve rather than at this dispatch, and this
finding is the argument for its integration point over the one below. The contract, the census and
the FSR passes stand; where they are plugged in has to change.

## The integration point (superseded by the correction above)

`taau_stand_in()` in `gpu/vk/vk_draw.cpp` already recognizes the network's dispatch by program key
and substitutes its own resolve. A vendor upscaler replaces the same dispatch, under the same
recognition, and falls back to the existing stand-in whenever it cannot run. That keeps one code path
for "the network did not run as itself" instead of two.

The replay rules in [design notes, section 4](design.md#4-the-lessons-from-mkw-as-rules-here) apply
unchanged. In particular the inputs are read from the submitted dispatch's own descriptors during
ordered replay, never from live guest memory.

## The camera, read out of the game rather than guessed

FSR's `device_to_view_depth` and `tan_half_fov` want the near plane and the vertical field of view,
and camera-derived motion wants the view-projection. Both are available: sead's perspective
projection object carries them, and its matrix update is already a patch site (`nx2/oot_wide.py`,
main+`0x3485cc0`, which is the whole function `f_834a5cc0`).

Reading that function's lifted C gives the layout, each offset from what the code does with it
rather than from a guess:

| offset | what it is | how the code shows it |
|---|---|---|
| `+0x98`, `+0x9C` | the near and far planes | loaded as a pair (`ldp s4, s3, [x0, #0x98]`), then **swapped by the byte at `+0x0A`** — a reversed-depth flag — before their difference is taken |
| `+0x0A` | which way round the depth runs | selects between the two above |
| `+0xAC` | `tan(fovy / 2)` | `1.0f / it` becomes the matrix's m11, which is `1 / tan(fovy/2)`. So the field of view is `2 * atan(it)` |
| `+0xB0` | the aspect ratio | divided into m00, and the ultrawide patch stores its own ratio back here |
| `+0xB4`, `+0xB8` | the projection's offsets | each doubled into m02 and m12 |

`x0` is the projection object throughout that function and `x1` is the 4x4 it writes, so one
injected block there can publish the near plane, the far plane and the field of view to host globals
for the renderer to read, the way `hos_wide_ratio` already travels the other way. Note that the path
which actually runs is one of the **inlined** copies, not the out-of-line function, and each holds
the projection in a different register; patching only the out-of-line one published nothing.

**A run confirms the layout and the values.** The object is built on the stack, so a published
pointer read a frame later is all zeros; snapshotting its first 48 floats at publish time gives:

```
+0x80: 0 0 0 0 1 0 1 20000
+0xa0: 0.7854 0.38268 0.92388 0.41421 0.5625 0 0 0
```

- `+0x98` = **1.0**, the near plane, and `+0x9c` = **20000**, the far plane
- `+0xac` = **0.41421** = tan(22.5 deg), so the vertical field of view is **45 degrees** — and
  `+0xa0` holds that same angle in radians, 0.7854
- `+0xb0` = **0.5625** = 1080/1920, so it is height over width, not width over height
- `+0xa4` and `+0xa8` are sin and cos of the half angle

That 45 degrees is also the needle for the rest of the matrix: a projection built from it has
m11 = 1/tan(22.5 deg) = **2.41421**, so the view projection the draws use can be found by looking
for that value in the uniform buffer a scene draw reads, rather than by guessing at another object's
offsets.

That removes the approximation the reference implementation settles for — it documents using "a
typical near plane and vertical field of view" because DLSS hands it no camera — and it is the same
data camera-derived motion needs.

## The input contract

A temporal upscaler needs the network's own inputs, and it must only run when they are all present and
of the expected shape:

- low-resolution color, HDR, matching the output's format
- motion, two channels, normalized toward the previous frame, in render pixels
- depth, reversed, at the render extent
- a fresh jitter sample for this frame, in render pixels

and it must reset its history on a resolution change, on first use, and after a gap longer than about
250 ms (a pause, a menu, a load). A camera cut is a separate reset that this game does not hand us
directly.

`gpu/vk/upscale_contract.h` decides which sampled binding is which, and `gpu/vk/upscale_format.h`
decides what each Vulkan format counts as. Both are driven by `tests/upscale_contract_test.cpp`
without a game, a GPU or a dispatch. Motion and depth fix the render extent, because they are the two
a network cannot sample at another size; the colour at that extent is the input. A colour at a larger
extent is reported as full-size and nothing more: the renderer records what a dispatch's descriptors
sample *or store* in one list, so the history and the output cannot be told apart there, and the seam
in row 2 is where that is resolved.

Where any of that does not hold, the native dispatch or the stand-in stays. An upscaler that runs on
inputs it has guessed produces ghosting that looks like a renderer bug and is reported as one.

> The same contract, derived for a different Switch 2 title in a sibling GPL-3.0 project, is written up
> in Eglantine's `docs/native-dlss-replacement.md`. The formats there (B10G11R11 color, R16G16 motion,
> D32 depth) are that game's, not necessarily this one's: the shapes have to be read off OoT's own
> dispatch before anything is wired to them.

## The seam

`gpu/vk/upscale_seam.h` is where a backend is chosen. The renderer resolves a recognized dispatch once into an
`upscale::Request` — what the sampled bindings provide, the image the dispatch writes, and which binding the renderer
is going to hand over as colour — and `choose()` says which backend takes it. A backend never re-derives the dispatch
for itself.

`temporal_possible()` is the gate a temporal backend has to pass, and every clause in it is a wrong picture rather
than a missing feature: the inputs must be complete, the output must be resolved, writable and no smaller than the
render extent, and the colour the renderer hands over must be the colour the contract identified.

The bindings the contract reads are in **descriptor order**, resolved the way `build_descriptors` resolves them, so
index `i` is `dw.imgs[i]` — the descriptor a backend is handed. That matters because the obvious shortcut is wrong:
`draw_sampled` records a sampled texture only where the GPU has written it and records every storage image as well,
so binding 0 of that list is not `dw.imgs[0]`. A backend wired to that correspondence would read whichever texture
happened to be bound first and ghost, and the ghosting would be reported as a renderer bug rather than as a wrong
binding. The stand-in's own `dw.imgs[0]` is the colour only when the contract agrees that slot 0 is the colour.

The stand-in needs none of this. It is a spatial resolve, and it runs wherever `RX_VK_TAAU_KEYS` asks for it exactly
as it did before the seam existed, including where the contract is not met. That property is what makes introducing
the seam a no-op, and `tests/upscale_seam_test.cpp` pins it.

## Order of work

Each row is one pull request, and each one is useful on its own.

| # | Change | Why it is first |
|---|---|---|
| 1 | Name each network binding's role in the existing per-network log, and say whether the inputs are complete | Nothing can be built until the contract above is read off the real dispatch rather than assumed, and the log that reports a network's textures was already there |
| 2 | A vendor-neutral seam at `taau_stand_in`, with the stand-in as its only backend | No behaviour change, and it is the review that decides the shape |
| 3 | FSR 3.1 temporal upscaling as the first backend | MIT sources, no external runtime, works on every vendor, so it is the backend that can be the default |
| 4 | DLSS Super Resolution: Streamline on Windows, NGX on Linux | Needs a user-supplied runtime, so it comes after a working fallback exists |
| 4b | FSR 4 through AMD's runtime, behind the same seam | The same API as row 3, so it is a backend rather than a rewrite; AMD hardware only |
| 5 | Presets, quality modes and DLAA in the Insert menu and `hookshot_options.ini` | Settings for backends that exist, rather than settings-first |
| 6 | Frame generation from real motion and depth | Replaces the block matcher's guess where the inputs are available |

## What each vendor runtime allows

**FSR.** AMD's FidelityFX sources are MIT and can be vendored under `third_party/` with a `PROVENANCE.md`,
unmodified. There are two integration routes, and they are not interchangeable:

- *AMD's FidelityFX API*, loading AMD's own Vulkan runtime library at run time. This is what #577 does, and it has
  already vendored the five MIT API headers under `third_party/fidelityfx/` with provenance. The runtime libraries
  are AMD's and are not committed. FSR 4's newer models arrive this way, on Windows.
- *The FidelityFX SDK sources*, where the upscaler's passes are recorded from AMD's MIT shader sources and no runtime
  library is needed at all. This is the route that works on Linux as well as Windows.

This tree takes the **SDK-source route**, under `third_party/fidelityfx-sdk/` with its provenance note: AMD's GPU
headers and the eight upscaler passes from SDK v1.1.4, unmodified, MIT. No runtime library is needed and none is
included, and the same code path serves Windows and Linux.

The eleven variants (the eight passes, plus wave-free pyramids and a sharpening accumulate) are compiled by
`scripts/fsr3_shaders.py` into `gpu/vk/shaders/fsr3_*_comp_spv.h` and **checked in**, like every other shader here, so
a clone still builds with no glslang. `tests/test_fsr3_shaders.py` recompiles them all and compares, so the headers
and their sources cannot drift apart, and validates each module. That costs about 1.3 MB of generated hex in the
repository; generating them at build time instead would remove that at the price of a glslang dependency for every
build, and is the maintainer's call rather than something to decide in a patch.

Note that #577 vendors AMD's *API* headers under `third_party/fidelityfx/` for the other route. The two folders are
different things — API headers against a runtime library, versus GPU sources compiled here — but if both land, one of
them should go.

**DLSS.** The Streamline headers are MIT and can be vendored the same way. **The runtime libraries are
not**, must not be committed, and must not be bundled in a release. The user supplies them, or the
build fetches them only after the user has accepted NVIDIA's license. Streamline has no Linux build,
so Linux uses NGX directly — `libnvidia-ngx.so` ships with the NVIDIA driver, and the model library
sits beside the binary. The same two backends therefore need two loaders behind one interface.

Neither backend may be a build requirement: a tree with no SDK present builds, and a machine with no
runtime present runs, with the stand-in.

## Denoising upscalers, and how to find out

NVIDIA calls it Ray Reconstruction, AMD calls it Ray Regeneration (RDNA 4 and newer only). Both are the same kind of
thing and neither takes colour, motion and depth: they replace a ray-traced renderer's denoiser, and they want guide
buffers — albedo, normals, roughness, specular hit distance. Nothing in `gpu/` names such a target, and this game is
rasterized, so the working assumption has been that they cannot be given what they need here.

That assumption is now testable rather than repeated. A deferred renderer writes its guides as several colour
attachments of one extent in one pass, so `RX_VK_MRT_CENSUS=1` logs each distinct render target set once — how many
colour attachments the game binds together, their formats and the extent — from `targets()`, which resolves once per
`NOP_SET_RT` rather than per draw. Off by default and free while off.

Two outcomes, both useful:

- **Never more than one colour attachment.** There is no G-buffer, there are no guides, and a denoising upscaler is
  settled as not applicable to this title. Say so in the docs and stop proposing it.
- **Three or four at one extent.** Guides may exist, and identifying which attachment is which becomes the same kind
  of work the input contract above already is.

**The log has now been read, and the naive reading of it is the opposite of what was expected.** A
240 s run of the opening (`RX_VK_MRT_CENSUS=1`, 136 distinct target sets, 491 presents) found that
the game *does* bind multi-attachment passes:

| sets | colour attachments | what they are |
|---|---|---|
| 103 | 1 | the main scene at 2048x1152 B10G11R11, the 2560x1440 RGBA8 sRGB present, and the UI |
| 18 | 0 | depth-only passes |
| 7 | **6** | 1x1 through 64x64, every attachment B10G11R11: six faces of a cubemap down a mip chain, so an environment or irradiance probe |
| 6 | 2 | 170x96 and 384x216 R16F + R32F with D32; 341x192 and 768x432 RGBA16F + RGBA16F with D32; 480x270 two B10G11R11; 960x540 B10G11R11 + R8 |
| 2 | 3 | 341x192 and 768x432, R32F + *undefined* + R16F with D32 (slot 1 unbound) |

So the count alone does not settle it — the formats and extents do. A deferred G-buffer is three or
four attachments **at the main render resolution** carrying albedo (an 8-bit or sRGB colour),
packed normals (RGB10A2 or RG16) and roughness/metallic. Nothing here is that. The main scene is a
*single* HDR target; every multi-attachment pass is either a cubemap face chain or a small
post-process at a quarter resolution or less, in single-channel float or HDR pairs.

**Conclusion: there are no albedo, normal or roughness guides to give a denoising upscaler, so Ray
Reconstruction and Ray Regeneration remain inapplicable to this title** — but on the evidence above
rather than on the absence of a target named "normal" in `gpu/`, which was never a sound argument.

What *is* deliverable, and is what "the newest upscaling" means in practice on each vendor:

| | |
|---|---|
| NVIDIA | DLSS Super Resolution with the transformer presets, and DLAA at native resolution. A neural network upscaler, through NGX on Linux and Streamline on Windows |
| AMD | FSR 4's machine-learning upscaler, reached through the FSR 3.1 API that row 4 below targets. Radeon RX 9000/7000/6000; AMD's own API falls back to FSR 3.1.6 elsewhere |
| Everyone | FSR 3.1 compiled from the SDK sources in this tree, which needs no runtime and no particular vendor |

## FSR 3 and FSR 4 are two backends, not two versions

FSR 4 is not source-available: it ships as prebuilt signed libraries inside AMD's SDK releases, so it cannot come out
of `third_party/fidelityfx-sdk/`. It is reached by loading AMD's runtime (`amd_fidelityfx_loader_vk.dll` and
`amd_fidelityfx_upscaler_vk.dll`, which the user supplies) and asking it for a provider. That makes FSR 4 a *second
backend* behind the same seam, not an upgrade of the first:

- **FSR 3.1, from the SDK sources here.** No runtime, no downloads, every vendor and both platforms. This is the one
  that can be a default.
- **FSR 4, through AMD's runtime.** AMD hardware, where the installed runtime exposes a provider; it must not
  silently become FSR 3 or FSR 2, because then nobody can tell which ran.

The API that reaches FSR 4 is the FSR 3.1 API, which AMD kept stable for exactly this, so integrating 3.1 properly is
the path to 4 rather than a detour away from it.

## Frame generation, honestly

The existing interpolator shows made frames about one frame behind the game, and skips interpolation
across hitches over 100 ms. Real motion and depth improve what it makes, but they do not change
either property. DLSS frame generation is a Streamline feature and so would be Windows-only; FSR's
frame interpolation is available on both. Neither removes the latency a frame-interpolating renderer
has by construction, and #755 should be read before any of it is wired to the Insert menu.
