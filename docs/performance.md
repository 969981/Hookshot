# Performance notes

Where the time goes, what has been tried, and how to measure on your own PC. The numbers come from
[design.md](design.md) (sections 9 to 11); none were taken for this page. Every switch named here is a test switch:
put it in `hookshot_env.txt` beside the game, one `NAME=VALUE` a line ([env-vars.md](env-vars.md) lists them all).

## What is known

- Gameplay is CPU-bound. In Kokiri Forest the replay thread took about 9 ms a frame and the GPU about 10 ms;
  `RX_VK_NO_WRITEBACK` cut the GPU to 3.6 ms and the frame rate did not move.
- Tried without a gain: `HOS_PIN_BY_CORE`, `HOS_SPIN_US=200` (slower), narrower barriers, `RX_VK_WAIT_GPU`.
- Already in: a pipeline cache on disk (`mkw_vk_pipelines.bin`), shaders precompiled before the game in a release
  build (`RX_VK_PRECOMPILE`), 2 to 4 background workers for shader translation and pipelines, big command buffers
  recorded on several threads.
- On RADV (Linux, AMD) each `vkQueueSubmit2` cost ~13 ms, so the replay's submits are batched there by default
  (`RX_VK_BATCH=0/1` chooses). On an RTX 4060 Ti forcing it on lost fps (25 to 16), so it stays off on NVIDIA.
- A long NVIDIA session (issue 384) split by scene: ~630 draws a frame took ~6 ms of replay, ~1630 draws ~13 ms, and
  bursts of hundreds of new pipelines in new areas took the frame rate to 8 to 14 fps even with a warm cache.
- Stutter on new areas is a shader or pipeline built for the first time; it is smaller when you pass again.

## Measure first

Take the same short walk each time (for example the same path in Kokiri Forest, 60 s), change one thing, compare.

| Want | How |
|---|---|
| Which threads are busy | `HOS_PROFILE=10`: every 10 s, the busiest threads and their share of a core (Windows and Linux) |
| One thread's hot code | `HOS_PROFILE=60 HOS_SAMPLE=<thread name> HOS_SAMPLE_FILE=stacks.txt`, then `python -I scripts/prof.py stacks.txt` |
| What every thread does at once | `HOS_PROFILE=60 HOS_SAMPLE_ALL=<path>`, create `<path>.go` to start, then `python -I scripts/crit.py <path>` |
| Hot lifted functions | `python -I scripts/hotlist.py <path> <out>` (feeds `A64_HOT_FUNCS` for the next build) |
| Slow frames | `RX_VK_SLOW_LOG=<ms>`: a frame that long gets its replay times logged |
| Every frame shown | `RX_VK_FRAMELOG=<file>` and `scripts/framelog.py` |

On Linux the same variables work (`host/posix/prof.c`); stacks are `hookshot` and `hookshot_vk.so` offsets.

## Things to try, cheapest first

1. **More background workers.** `RX_VK_JOBS=<n>` (1 to 16) sets the worker count; the default is a third of the
   hardware threads, 2 to 4. If the stutter on a new area gets shorter with 6 or 8, the default can follow.
   Check that the frame rate does not drop: the workers share cores with the game's threads.
2. **Pre-warm the shader list.** Play through the places you care about once, with a release build, so
   `hookshot_programs.bin` and `hookshot_pipelines.bin` hold them. The second time through should not stutter.
3. **Host code generation.** The host is built for `x86-64-v3`; `RX_MARCH=native` at build time is one line to try.
   The lifted code is `-O1` with `-O2` for the hot chunk; a PGO profile (`RX_PGO=gen`, then `RX_PGO=<file>`) fitted to
   more of the game than the first area is the larger lever.
4. **Locks and scans on the replay thread.** Two of the recent wins were of this kind (the pool-end lookup,
   the storage-table scan, about 8% each). A profile of the replay thread (`HOS_SAMPLE=` its name) shows the next one.
5. **Pipelines ahead of the draw.** A draw that needs a pipeline still being built waits for it. Building them from
   the shader list at load (item 2) is safe; skipping the draw for a frame is not, as it shows as flashes.

## Reporting a result

Say the card and driver, the build (release or test), the switch, the walk, and the frame rate or frame times before
and after. Never attach game files.
