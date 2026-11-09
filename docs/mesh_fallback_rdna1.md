# The mesh shader fallback on GPUs without mesh shaders (RDNA1 and older)

Ocarina of Time on Switch 2 draws its foliage, grass, ivy, some doors and many effects with
mesh shaders. A GPU without `VK_EXT_mesh_shader` (AMD RX 5000 and older, NVIDIA GTX 10 and
older) used to skip those draws. This branch makes the fallback from
[#640](https://github.com/NotRexed/Hookshot/issues/640) work: task and mesh programs run as
compute shaders, and a generated vertex shader draws what they wrote.

This page explains how it behaves, why it was broken, and how each problem was fixed.

## Status

| Part | Status |
|---|---|
| Foliage, floor grass, ivy, vines, the door of Link's house | Working on the test laptop |
| Lighting and bloom on AMD | Working on the test laptop |
| GPUs **with** mesh shaders (the #640 device loss) | Fixed in the code, **not tested** on such a GPU |
| Texture noise after alt-tab on low-VRAM cards | **Working, but further testing needed** |

### The test laptop: low VRAM

Everything here was tested on a single laptop, an **AMD Radeon RX 5500M (RDNA1) with 4 GB of
VRAM**, on Windows 11 with AMD's driver. Vulkan lets Hookshot use only about 3.1 to 3.4 GB of
that, and less while other programs run.

That is well under the 8 GB the readme recommends, and several of the problems below only
showed because of it: the lock-up, the white distant foliage, and the texture noise. On a
card like this:

- Use the **Quality preset: Minimum (4GB)**. It lowers the resolution, turns on the handheld
  profile, and caps the texture and mirror memory. Frame generation is better left Off: the
  GPU is the limit, and it costs GPU time.
- Changing any other graphics setting switches the preset to Custom and drops those memory
  caps. Select Minimum again afterwards.

Frame rates seen in the logs, in Link's house and Kokiri Forest (not a controlled benchmark):

| State | Frame rate | GPU time a frame |
|---|---|---|
| Docked settings, before the grass fix | ~15 fps | ~54 ms |
| Minimum (4GB) preset | ~19 fps | ~41 ms |
| Minimum preset, after the fallback's later fixes (shared buffers, no empty vertices) | ~34-38 fps | ~17 ms |

## How it behaves

### Picked at every start

At each start Hookshot asks the driver whether the GPU has mesh shaders:

- **Mesh shaders:** the game draws as on the console. The fallback is off, and nothing of it
  is built or cached.
- **No mesh shaders:** the fallback turns on by itself. The log says
  `vk: no mesh shaders: task and mesh programs run as compute shaders`.
- **No mesh shaders, and no buffer device addresses** (Vulkan 1.2, needed by the fallback):
  a warning at launch says so, and that updating the driver should help.

The shader cache records which path it was built for, so switching cards (the **GPU
device** setting) rebuilds it rather than using the other path's shaders.

### The toggle

Insert menu, Graphics: **Mesh shader fallback**.

| Choice | Behaviour |
|---|---|
| **Auto** (default) | The fallback only on a GPU without mesh shaders |
| **Off** | Never. On a GPU without mesh shaders the foliage, grass and effects are left out |
| **On** | On every GPU. A GPU's mesh shaders are not turned on, and the fallback draws instead |

- A change applies after a restart, and that start prepares the shaders again.
- It is not part of the Quality preset: changing it keeps your preset and its memory caps.
- `RX_VK_NO_MESH_FALLBACK=1` turns it off for one run, as Off does.

**On is meant for AMD cards that have mesh shaders but hang with them.** Lichii, another
Switch 2 emulator, found that native task and mesh draws hang AMD's RX 6000 and newer cards:
a driver timeout (TDR) on Windows, GPU resets with RADV on Linux. It made the fallback its
default on AMD ([Lichii #814](https://github.com/zortos293/Lichii/pull/814)). Hookshot keeps
Auto, but a player with such a card who sees hangs can set On. This is Lichii's finding, not
yet confirmed in Hookshot.

### Starting the game: shader preparation

The first start after an update shows a "Preparing shaders" count that runs three times in a
row, each with its own total:

1. **Translating, about 116,000:** the game's 38,809 shader programs, three times each (once
   for each kind of bone input). The mesh programs are among them, translated to their
   fallback form on a GPU without mesh shaders.
2. **Prediction:** a short step that works out which pipelines will be needed.
3. **Precompile, about 11,000 on the test laptop:** the GPU driver compiles the programs. That
   is about 10,000 ordinary compute programs, **about 1,350 mesh fallback programs** (one or
   two compute pipelines each), and the graphics pipelines earlier play used.

The fallback programs are compiled here, before play, so that nothing pops in white during
play (see "The door white at spawn" below). On AMD a fallback pipeline can take 10 to 12
seconds to compile (one took over 7 minutes), so the first start after a change takes a
few minutes. Later starts take them from the driver's own cache (`mkw_vk_pipelines.bin`) in
seconds. The full compile comes back only after a change to the shader translator, a GPU
driver update, or a deleted cache. If nothing finishes for 30 seconds, the game starts
anyway and the rest compile behind it.

## Why it was broken, and how it was fixed

In the order the problems were found, on the RX 5500M.

### 1. #640 had been taken out

The fallback ([#640](https://github.com/NotRexed/Hookshot/issues/640)) was merged upstream,
then reverted (`806c566`) after an RTX 4060 Ti lost the device. This branch re-applies it,
together with the reader of the program cache that went with it (`449382e` undone).

With it back, the fallback ran but drew no foliage, and the game froze at the first room
with foliage.

### 2. Invalid shader code

Checked offline with `scripts/validate_spirv.py`, almost every one of the 1,350 fallback
programs failed the Vulkan validator:

- **The payload's address** (the data a task program hands its mesh programs) was read from
  the push constants by its byte offset, 64, used as a member index into a 5-member block.
  Every task-driven program read and wrote its payload at a garbage address.
- **Device-address loads and stores had no `Aligned`**, which Vulkan requires.

Both fixed. Later runs of the validator found two more: a loop counter declared in the
wrong place, and a `bool` stored into a 32-bit word. Fixed too. Now **all 1,347 fallback
programs pass**.

### 3. The freeze

- A compute pipeline the driver refused was asked for again on every draw, and the stages
  made before it leaked.
- AMD took seconds to compile each fallback pipeline, and it did so inside the draw.

The fallback's pipelines now compile on a worker thread, as ordinary compute programs
already did, and a draw is skipped until its pipelines are ready.

### 4. The door and the ivy appeared, the floor grass did not

Two more bugs broke every task-driven draw:

- **A task thread copied 1 word in 32 of its payload** into its slot (the copy loop strode by
  thread), so a mesh workgroup read mostly zeros.
- **A mesh workgroup's index within its task was its global slot** (up to 13,855) instead of
  0 to 31, so it picked a meshlet past the end of its task's list.

With those fixed, together with the freeze fix in the same build, the **door of Link's
house, the ivy and the vines** drew. Which single fix the door needed was not isolated.

The **floor grass** stayed missing, for a different reason, found later (item 7): it is drawn
with an indirect draw, which the fallback never ran.

### 5. Dark lighting and white-glowing objects (AMD)

Not part of the fallback, but on the same card:

- **Warp size.** NVN's shaders are written for 32-lane warps. AMD lets only compute shaders
  be held to 32 lanes. Fragment shaders run 64 lanes wide, and their warp operations
  (ballots, shuffles) ran across both halves. The translator now handles 64-lane warps for
  graphics stages on such GPUs. Compute shaders, already held to 32, keep their old code.
  Giving them the 64-lane path too made shader preparation take hours.
- **[Lichii #273](https://github.com/zortos293/Lichii/pull/273):** fragment-shader shuffles
  are broken on AMD's Windows driver (a basket in Link's house glowed white), and now go
  through a broadcast workaround. A depth-compare texture is never treated as an integer
  image (the lighting's shadow samples).

### 6. The lock-up, and the white distant foliage

- **The lock-up** was the 4 GB of VRAM running out: textures alone took about 2.2 GB a frame
  at docked settings. When VRAM is full, Windows moves memory over PCIe mid-frame and frames
  take seconds. The Minimum (4GB) preset avoids it.
- **The fallback's buffers took 2.2 GB:** each of 95 programs had its own pair, on each
  recording thread. When VRAM ran out, new pairs could not be made, and those programs
  stopped drawing for good. For two-pass foliage that left the depth pass without its colour
  pass: **white foliage in the distance.** All of a thread's fallback draws now share one pair
  (72 MB), with a barrier so a draw waits for the previous one to finish reading.

### 7. The floor grass

- **Too few slots.** A draw had 1,024 slots, each sized for its program's maximum. The grass
  needs 433 tasks of 32 slots, so about 93% of it was dropped. Now there are 16,384 slots. A
  mesh workgroup claims records only for the triangles it outputs, and the draw's size comes
  from that count, which also stopped the GPU running hundreds of thousands of empty vertices.
- **Still no grass at spawn.** The grass is drawn with `DrawMeshTasksIndirect`: its task count
  lives in GPU memory. The fallback ran the task stage from that count, but launched the mesh
  stage with the CPU's copy, which is 0. So **no mesh workgroup ever ran for an indirect task
  draw**, and that had been true since #640. Now each task thread that starts a mesh workgroup
  counts it on the GPU, and the mesh stage is launched from that count. **The floor grass
  drew.**

### 8. The door white at spawn

With the grass drawing, the door was white for a few seconds after spawning, and normal once
you got near it. Its colour pass was waiting for its fallback pipelines, which AMD took 10 to
12 seconds to compile the first time, while its depth pass had already drawn. Getting close
probably switched it to a version already compiled.

Fixed by compiling **every** fallback program at start (see "Starting the game" above),
rather than only those an earlier session had drawn.

### 9. GPUs with mesh shaders, and the RTX 4060 Ti

Auditing everything #640 changed for GPUs that **have** mesh shaders found the likely cause
of the RTX 4060 Ti's device loss that had #640 reverted:

- **#640 made the pipeline builder leave out every task and mesh stage**, for every program.
  The fallback's stages are compute stages, so that only ever matched **native** mesh
  programs: on a GPU with mesh shaders every native mesh pipeline was built with only a
  fragment stage, which is invalid. That GPU had mesh shaders, so the fallback was not even
  running.
- #640 also made every pipeline layout 16 bytes bigger on every GPU, and changed which stage
  vertex input was read from.

All three now behave as on `main` when the fallback is not in use. Native task and mesh
shader code is byte for byte `main`'s. **Not tested on an RTX card or any GPU with mesh
shaders.** Before this goes upstream, someone with one should check that the device loss is
gone.

### 10. Texture noise after alt-tab (working, but further testing needed)

On the test laptop, after alt-tabbing (or a scene change), textures turned to colourful block
noise: the path, the trees, Link's and the Kokiri's clothes, the minimap. **This is a bug in
Hookshot's texture code, not the fallback's**, and it is most likely on cards with little VRAM.

1. With the game in the background, other programs took VRAM, the driver lowered Hookshot's
   allowance, and about a thousand textures were evicted in seconds.
2. On this card, ASTC textures are re-encoded to BC3 on the GPU when made again. That needs a
   scratch area, which Hookshot gives back after 20 seconds unused.
3. Its output buffer could only be made in VRAM, and VRAM was full: every BC3 re-make failed
   ("no memory for the BC3 scratch" in the log).
4. A failed upload was marked as done anyway. A BC3 texture that was never written shows
   whatever its memory held, which is the noise. It was never made again, because static
   textures never change.

Now the scratch's buffer goes to system memory when VRAM is full, and a failed BC3 texture is
tried again 30 frames later, with the cause logged. **It worked on the test laptop after
alt-tabbing, but was only tested briefly.** It needs more play, and testing on other low-VRAM
cards. A texture can still show noise for a moment between a failed upload and its retry.

## Known limits

- **Draw order.** Records land in the order the GPU claims them, so semi-transparent mesh
  geometry can occasionally draw out of order. Opaque foliage is not affected, including the
  grass's depth pass and the colour pass that draws only where its depth matches. Lichii's
  fallback has the same limit.
- **One GPU tested.** Only the RX 5500M. Other cards without mesh shaders (RX 5000, RX 400/500,
  GTX 10 series) should behave the same, but have not been tried.
- **No GPU with mesh shaders tested** since the fix in item 9.
- **The texture fix** (item 10) needs more testing.

## Testing and tools

- **Offline shader check.** `python -I scripts/validate_spirv.py <game folder>\hookshot_programs.bin`
  checks every fallback program with the Vulkan SDK's `spirv-val`, and writes failing ones
  out as `.spv`, `.spvasm` and `.txt`. `--all` checks every program, and `--key <hex>` checks
  one.
- **Tests:**
  - `tests/test_mesh_fallback.py`
  - `tests/test_amd_fragment_warp.py`
  - `tests/test_bc_upload_retry.py`
  - `tests/test_validate_spirv.py`
  - the existing `test_mesh_*`, `test_program_cache_reader` and `test_bc_levels`
- **Useful log lines:**
  - `vk: fallback: ... SLOW compile` (compile time)
  - `vk: precompile: ... mesh fallback programs` (the startup count)
  - `vk: fallback: N buffer pairs, X MB` (fallback memory)
  - `vk: BC3 texture ... its upload failed (...)` (the texture fix)
- **`RX_VK_PASS_TIMING=2`** logs GPU time per render pass, to see where frame time goes.

## Credits

- [#640](https://github.com/NotRexed/Hookshot/issues/640): the fallback this builds on.
- [Lichii](https://github.com/zortos293/Lichii) (zortos293 and contributors): its fallback
  design (#569), the AMD fixes ported here (#273), and the finding that native mesh draws
  hang newer AMD cards (#814). The notes its developers shared on how the game draws its
  foliage pointed to most of the fixes above.
