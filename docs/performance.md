# Where the frame goes

Measured on Linux, RTX 5060 Ti, driver 615.71.09, headless under Xvfb, Ocarina in Kokiri Forest with
a warmed shader cache, driven there by a frame-clocked `HOS_INPUT_SCRIPT` so that two arms measure
the same place in the game. A frame of ~1800-2800 draws is gameplay; ~5 draws a frame is the title
screen, and comparing one against the other is how this project once blamed FSR for a collapse it
had nothing to do with (`docs/upscaling.md`).

## The frame is CPU-bound in recording, not on the GPU

A representative gameplay frame, from `vk: a frame's replay time (ms)`:

| | ms |
|---|---|
| `submit` (the whole of `run_submit`) | **30.6-35.0** |
| of which `draw` | 7.9-11.0 |
| `programs` | 3.3-4.7 |
| `descriptors` | 2.2-3.1 |
| `commands` | 2.0-2.6 |
| everything else named | < 1 each |
| **GPU time** | **~12** |

The named sub-counters are subsets of `submit`, and they account for roughly two thirds of it; the
rest is inside `replay_handle` walking the game's command stream. At ~30 fps the frame is ~33 ms, so
the GPU is idle for over half of it. **Recording the commands is the limit, not drawing them.**

## What the CPU is actually doing

`perf` on this kernel cannot open events with `-g`, so there are no call graphs; `-e cpu-clock`
(the software event) and a flat profile work. 30 s of a gameplay scene:

| share | symbol |
|---|---|
| **~30%** | one libc routine, disassembled to `rep movsb` -- ERMS `memmove` |
| 3.5% | `spin_until_set` |
| 2.1% | `__sched_yield` |
| ~1% each | guest code (`f_*`), `renderer_main`, `obj_lookup`, `build_descriptors`, `ub_try_gap` |

**The memcpy is not attributed.** Without call graphs there is no honest way to say whether it is
guest-memory reads, mirror syncing, staging uploads or the game's own work, and guessing is how the
upscaling work wasted a day. One guess was checked and was wrong: `ub_read` copies a whole 4 KB page
on a read that lands in a captured page, which looks wasteful until you find `submitted_page_reuse`
-- the copy is an investment that pays off because uniform reads cluster on a page.

## Parallel replay exists, and today it makes things worse

`RX_VK_PARALLEL=<threads>` (default 0, off) walks the command stream once on the replay thread to
find segment boundaries, then records segments of ~160 draws across worker threads, submitted in
order. It was written for Mario Kart World, where one command buffer held nearly a frame's draws.

Measured against the serial path, same scene:

| arm | fps | `submit` |
|---|---|---|
| serial | 29.9 / 30.5 / 30.5 | 30.55 ms |
| `RX_VK_PARALLEL=4` | 23.2 / 23.2 / 24.4 | 38.02 ms |

It costs about a fifth of the frame rate. The reason is in its own log line, and it is not the dry
pass:

```
parallel replay, a command buffer: dry pass 0.34 ms, segments 11.22 ms (4.7 of them),
                                   lock taken 739 times, waited for 3.65 ms (all threads)
```

The dry pass is 0.34 ms. The workers spend **3.65 ms waiting on a lock they take 739 times** per
command buffer, across 4.7 segments. `ParLock` (gpu/vk/vk_internal.h) is **one global recursive
mutex** over everything the threads share -- textures and their uploads, programs, pipelines,
samplers, pools and memory stamps. Three of its seventeen sites in the draw path are inside
`build_descriptors`' per-binding loop, so the count rises with every texture every draw binds.

So the parallel path is not slow because parallelism does not suit this game. It is serialised on
one lock, and then pays the coordination on top. Splitting that lock by what it protects is the
work that would make it pay, and it is delicate: the mutex is recursive and taken reentrantly, so
the split has to preserve every nesting order that exists today.

Until then `RX_VK_PARALLEL` should stay off, which it is.
