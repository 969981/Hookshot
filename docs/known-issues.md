# Known issues and the first 30 minutes

This page is the short version of the issue tracker. It covers the problems most reports
hit, what to try first, and which issue tracks each one. If your problem is listed here,
add your log and hardware to the linked issue instead of opening another report.

The full triage plan is in [ROADMAP.md](../ROADMAP.md).

## Before you report

1. **Use the newest release.** Every release includes the fixes before it. The release
   name is in the Insert menu, beside "Hookshot", and in the first line of
   `hookshot_log.txt`.
2. **Attach `hookshot_log.txt`.** It is beside `hookshot.exe` and holds the last run only,
   so copy it right after the problem. It contains no game files.
3. **Say where you were in the game** and what you were doing. A save that reproduces the
   problem is useful if you can share it without game files.
4. **One problem per issue.** Split unrelated graphics, crash and performance reports.
5. **Never attach game files, keys, `hookshot.exe`, the `.bin` caches or saves that contain
   game data.** The log and a screenshot or video are enough.
6. **Test without ReShade, shader-cache tools, overlays and frame-generation injectors.**
   They change what Hookshot does and make the report hard to compare.

## First start: shaders and caches

The first walk through an area builds shaders and pipelines. On a cold cache this can
take minutes and can look like a freeze or a black frame. Later starts are faster.

- Keep the game open while it says it is preparing shaders. Closing early can leave the
  next start without a complete cache.
- The caches are `hookshot_prepared.bin`, `hookshot_programs.bin` and
  `mkw_vk_pipelines.bin` in the game folder.
- If the picture is black on a later start while the audio still works, close the game and
  move those three files out of the folder, then start again. This is the workaround for
  the second-start case tracked in #195. Changing the resolution in the Insert menu back
  and forth also restores the picture for some users.
- A visible "Reset shader cache" action and cache validation are planned; until then the
  three files are the manual reset.

## Movies are black but the sound works

The game's movies are AV1. On Windows, Hookshot decodes them through Windows itself, which
needs Microsoft's free **AV1 Video Extension** from the Store. Without it the movies are
black with sound. The newest release offers to open the extension's page the first time
this happens. This is not the same problem as a black gameplay screen.

## Black screen with audio

This is the most common "the game is broken" report. It has several causes, and the log
tells them apart:

| What the log says | What it is | What to try |
|---|---|---|
| `submit ... failed` / `device lost` | The GPU stopped, usually while a scene loads (#150) | Update the graphics driver, restart the game, and attach the log to #150 |
| No device lost, long submits | First-time shader compilation (#388) | Wait for the area to finish; do not close the game |
| Black only on a second start or after loading a save | A cache problem (#195) | Move the three cache files out of the folder and start again |
| Intel Arc or Iris | A known Intel issue (#17) | Attach the log to #17; Intel testers are needed |

If the menu is also unresponsive, the game is not simply compiling: attach the log and
say whether the window recovers.

## AMD and Linux

- Update Adrenalin. Older logs show driver `0x800161` rejecting two of the game's
  pipelines; newer drivers build them. If the window hangs right after a
  `not made (-13)` line, the driver is the first thing to change (#360).
- On Linux/RADV, batched queue submits are on by default. `RX_VK_BATCH=0` disables them
  and `RX_VK_BATCH=1` forces them. `RX_VK_BATCH_POLL_US=2000` is being evaluated as the
  default because it reduces progress-poll flushes (#413).
- If `vkQueueSubmit2` takes hundreds of milliseconds while GPU time is ~10 ms, add your
  system and log to #312. The current work is the udmabuf/import path (PR #448) and the
  second `madvise` cause described in that issue.
- On a 4 GB or 6 GB card, start with the Minimum or Low quality preset and Resolution
  67% or 50%. The texture cache is capped, but render targets and GPU-side copies still
  use memory (#105).

## Exposure flicker and photosensitivity

Some builds show brightness pumping or one-frame black/white flashes, especially around
lighting changes. This is tracked in #145. The fix in PR #513 is in flight. If you are
sensitive to flashing images, cap the frame rate or wait for the release that includes
the fix; do not keep playing through a flashing section to capture a video.

## Building

- The builder chooses one compiler per CPU thread, capped by free memory at about 3.5 GB
  per compiler. `HOOKSHOT_JOBS` (or `--jobs`) overrides it. If the build is slower than it
  used to be, include the worker line from the build output in #503.
- Do not update the builder while a build is running. Start the update after the build
  finishes; a build that is interrupted by a new release has to start again.
- If the build stops, copy the **whole** stopped-step text, not a screenshot of it. The
  exit status and the failing file are the useful part (#501, #507, #457).
- A command-line build from a clone needs `RX_CPM` for FFmpeg/Vulkan headers and
  `EMSDK`/`HOOKSHOT_MINGW` for the compiler. The builder sets these itself; the command
  line does not always find the builder's packaged copies (#387).
- On the Linux AppImage, `HOOKSHOT_JOBS=<n>` sets the worker count. Put it in the
  environment before starting the AppImage, not in `hookshot_env.txt` (#494).

## Ultrawide and aspect ratio

A window or screen wider than 16:9 shows the game at its own 16:9 shape with black bars
at the sides. The Ultrawide option is for a wider camera; on some setups the game ignores
it and the 3D image still stretches (#297). The HUD remains stretched until a HUD layout
fix exists.

## Canonical trackers

Add your report to the matching issue rather than opening a new one. These are the
trackers used by the roadmap:

| Area | Issue |
|---|---|
| Build time, workers, progress | #503 |
| Installer copies, re-downloads | #493 |
| Null-memory-pool crash | #287 |
| Texture-builder crash | #500 |
| Device lost / black screen with audio | #150 |
| Black screen at the Deku Tree cinematic | #309 |
| Black screen on a second start / cache | #195 |
| Exposure flashes and brightness flicker | #145 |
| Moving ground lines / Skull Kid | #319 |
| Hyrule Field shadows | #468 |
| Overexposure and bloom | #36 |
| AMD foliage artifacts | #302 |
| RADV colour noise | #361 |
| Floating trees | #352 |
| Missing textures and foliage | #11 |
| VRAM pressure and 4 GB cards | #105 |
| Intel Arc and Iris | #17 |
| No mesh-shader fallback (Pascal, RDNA1) | #148, #454 |
| Cold cache and shader warm-up | #388 |
| RADV queue-submit performance | #413, #312 |
| Version display | #325 |
| Issue guidelines | [docs/issue-guidelines.md](issue-guidelines.md) (replaces #159) |

## If your problem is not listed

Open the matching issue form:

- [Game problem](https://github.com/NotRexed/Hookshot/issues/new?template=1-game-problem.yml)
- [Build problem](https://github.com/NotRexed/Hookshot/issues/new?template=2-build-problem.yml)
- [Idea or feature request](https://github.com/NotRexed/Hookshot/issues/new?template=3-idea.yml)

Reports without a log may be labelled `needs-log` and closed if the information never
arrives. That is not a judgement on the report: without the log there is usually nothing
to reproduce.
