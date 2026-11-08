# Hookshot Roadmap

**A plan for the 133 open issues as of 8 November 2026.**

This roadmap is based on a full read of the open issue tracker, the comments on the
highest-traffic reports, and the open pull requests. It does not replace the issue
tracker: every item below points back to an issue number so progress is visible.

The short version: Hookshot does not have 133 unrelated problems. It has about
**twenty root causes**, and most of the tracker is duplicate reports of them. Fixing
those root causes, closing duplicates with a link, and adding a small amount of release
discipline will retire the majority of the open issues without needing 133 separate
changes.

How to use it: section 2 is the next-release list, section 4 is the sequencing and
exit criteria, and the appendix gives every issue a disposition so a maintainer can work
through the tracker without re-reading it.

---

## 1. What the tracker says

| Measure | Value |
|---|---:|
| Open issues analysed | **133** |
| Open pull requests at the same time | 59 |
| Bugs | 56 |
| Enhancements | 18 |
| Unlabelled issues | 59 |

The five largest clusters:

| Cluster | Approx. issues | Root symptom |
|---|---:|---|
| Build time and builder reliability | 22 | Builds that took ~1 hour now take 7-10 hours, stop at 98%, or restart themselves |
| Black screen, device lost, null crash | 35 | Picture disappears while audio continues; crashes in `OptimizeChangeBanc` and texture-builder code |
| Performance and stutter | 22 | 3-15 fps on AMD/RADV, first-area shader hitches, movie upscaler cost, VRAM pressure |
| Rendering correctness | 40 | Exposure flicker, striped shadows, AMD foliage artifacts, missing grass/textures |
| Features and verification | 14 | macOS, controller options, ultrawide, graphics settings, retests |

The most useful fact in the tracker is the duplicate ratio: **61 of 133 open issues are
duplicates or sub-reports of another issue**. The appendix at the end maps every issue
to a disposition, so no report is lost.

---

## 2. The most pressing, easy-to-understand issues

These are ordered by user impact first, then by how quickly a fix can be explained and
verified. They are the issues to talk about in release notes and the first ones to put
on a board.

| Rank | Issue(s) | What the user sees | Why it is pressing | First action | Effort |
|---:|---|---|---|---|---|
| 1 | **#503, #502, #511, #418, #495** | A build that used to take ~1 hour now quotes 7-10 hours and uses only 2 compiler jobs | It blocks every new player and every retest; the regression is recent and well measured | Ship the worker-count fix (one compiler per thread, capped by free RAM), then the chunk-cost scheduler. `nx2/make_game.py:default_jobs()` already implements the policy; make every builder/CLI/AppImage entry point use it. PR #505 covers the Linux override. | S |
| 2 | **#493** | Launching the builder re-downloads and re-extracts the game, leaving several copies of the ROM on disk | Disk exhaustion and repeated multi-hour rebuilds; users cannot trust the launcher | Keep versioned installs, never delete a payload another process may use (PR #514), skip extraction when the same `.nup`/hash is already staged, show where the copies are. | M |
| 3 | **#501, #507, #457, #478** | Build stops at 98%, clang dies "due to signal", or lift stops on Steam Deck | A failed build at 98% wastes hours and produces no actionable message | Reproduce each with the exact file/log; keep child exit status (PR #499); retry a failed file alone (already added); make the ADR diagnostic non-fatal on expected Steam Deck input; add one clear failure summary. | M |
| 4 | **#287, #509, #500** | Crash reading guest address `0x4` while a scene loads; crash in `nvnTextureBuilderSetSwizzle` when opening the ocarina | Null-pointer bugs with small, local fixes; they turn a loading transition into a lost session | Merge PR #510 (memory-pool guard) and apply the same null guard to every `nvnTextureBuilder*` setter in `host/nvn_native.c`; log the caller once. | S |
| 5 | **#150, #309, #195** | Black screen with audio after the intro, the Deku Tree cutscene, or on the second start | The most common "game is broken" report; some cases are already fixed, the rest need one canonical tracker | Keep #150 as the device-lost canonical, #309 as the black-screen canonical, #195 as the cache-corruption canonical. Require the log signature before routing a new report, then close duplicates. Add a cache-validation/reset path and an actionable device-lost dialog. | L |
| 6 | **#145, #319, #323, #382** | Brightness pumping, one-frame black/white flashes, doubled image with frame pacing | Photosensitivity risk and the most visible visual regression; a fix is already in flight | Land PR #513 (frame wait and queue ordering), re-run the flash detector for 60 s on NVIDIA/AMD/Intel, then retest the moving-ground-lines half of #319 separately. | M |
| 7 | **#413, #312, #384** | 3-10 fps on AMD/RADV while GPU time is ~10 ms; queue submit takes 250-300 ms | AMD/Linux is a large, vocal part of the audience, and the root cause is understood | The batching patch is already merged and on by default for RADV. Follow up with `RX_VK_BATCH_POLL_US=2000` as the RADV default, the import/udmabuf work (PR #448), import give-back (PR #446/#479), and the second THP/madvise cause in #312. | M-L |
| 8 | **#515, #468, #480** | Moving striped shadows over Hyrule Field, later Castle Town | Very obvious, reproducible, and reported across NVIDIA and AMD | Treat #468/#515 as one shadow/culling tracker; test with the tree-shadow fixes in PR #477 and the culling work from #252; add a fixed save and 30 s camera sweep to the hardware matrix. | M |
| 9 | **#36, #508** | Entire scene overexposed, bloom blown out, plants glowing | Obvious to every player and easy to compare against a reference build | Keep #36 as canonical; compare tonemap/bloom/exposure against the known-good Lichii frame; recheck after the flash fix because exposure and flash share the post-process path. | M |
| 10 | **#302, #224, #197, #57** | AMD foliage and lighting artifacts, double image, missing surfaces | Makes AMD cards look broken even when the frame rate is acceptable | Land the AMD foliage PRs (#482, #475, #477), then retest with the #302 save and log on RX 6000/7000/9000. | M |
| 11 | **#17, #366, #367, #460, #496, #498, #374** | Intel Arc/Iris: intro plays, then black screen or hard crash | An entire vendor is effectively unsupported; no maintainer has the hardware | One Intel tracker (#17); recruit two testers (one Windows Arc, one Linux ANV); land the bounded `CasLoop`/EQUAL-depth fixes proposed in #17; add Intel to the release matrix or state the limitation clearly. | L |
| 12 | **#105, #452, #96, #151** | VRAM fills, textures are dropped, 4 GB cards go black | Current texture cap helps, but the remaining budget is still confusing and causes crashes | Keep #105 canonical; show a VRAM breakdown in the log/menu; lower the GPU-copy share on large cards; make the 4 GB preset select conservative defaults automatically. | M |

**Quick wins (ideally one day each):**

- #287/#509 with PR #510, and #500 with the texture-builder guards.
- #325/#481: show the release name/version in the Insert menu and in the first log line.
- #494: document `HOOKSHOT_JOBS` for the AppImage (and point at PR #505).
- #393/#433: publish the antivirus false-positive note, submit the binary to Microsoft, and keep the "Repair" path.
- #195: add a visible "Reset shader cache" action and validate cache files before use.
- #297: land PR #450 for the ultrawide camera; PR #205 already added the letterbox fallback.
- #396: land PR #440 for the GPU selector.
- #420/#125: land the controller-preset work in PR #372.
- #68: the Insert menu already has resolution choices; retest and close if the request is satisfied.
- #35: fixed by the `new-installer` release (leaf files were read from offset `0x304`); ask reporters to retest and close.

**Fixes already in flight.** These pull requests should be reviewed, landed and verified
before new work starts on the same area:

| PR | Covers | Action |
|---|---|---|
| #510 | #287, #509 | Memory-pool null guard; merge and add the texture-builder equivalent |
| #513 | #145, #319, #323, #382 | One-frame exposure flashes; land, then run the flash detector |
| #505 | #494, #503 | `HOOKSHOT_JOBS` in the Linux builder |
| #514 | #493 | Retain older builder files instead of deleting them |
| #499 | #501, #507, #457 | Keep the child exit status in stopped-step errors |
| #453 | #267 | Optional CLI build log |
| #429, #428 | #503 | Compile-cost chunk split; parallel lift and skip unchanged lift |
| #482, #475, #477 | #302, #319, #515 | AMD grass shadow, stage buffer, tree-shadow flicker |
| #252 | #35, #302, #385 | Foliage culling: three bugs that zero the tree counts |
| #448, #446, #479 | #312, #413 | RADV imports: udmabuf, give-back, pressure handling |
| #487 | #361, #197 | RADV colour noise and one-frame sunlight flicker |
| #440 | #396 | In-game GPU selector |
| #450 | #297 | Ultrawide camera |
| #451 | #391 | Relative-folder launch |
| #459 | #512 | Experimental macOS support |
| #372 | #420, #125 | Controller presets and camera inversion |

Already merged and worth naming in release notes: the RADV submit batching (#413), the
exposure ordering (#415), the cache/descriptor fixes, the leaf-file offset fix (#436),
the mobile-frame-pacing work (#296), and the ultrawide letterbox fallback (#205).

---

## 3. How the work will run

### Priority definitions

| Priority | Meaning | Response |
|---|---|---|
| P0 | Data loss, build blocker, crash, black screen, photosensitivity | Fix or mitigate in the next release; release notes must name it |
| P1 | Major visual corruption or performance below playable on common hardware | Scheduled in the current milestone |
| P2 | Usability, feature request, single-hardware issue | Backlog, picked up when P0/P1 are clear |
| P3 | Nice to have, unsupported platform, duplicate data point | Keep as reference or close as duplicate |

### Working rules

1. **One canonical issue per root cause.** New reports are linked to it and closed as
   duplicates only after the reporter has been told which fix to test.
2. **No forced update during a build.** A build in progress must not be invalidated by a
   release published minutes later. Offer the update at start-up and after completion
   (this also answers #421 and part of #493).
3. **No issue without a next action.** Every issue gets one of: `fix`, `duplicate`,
   `needs-log`, `verify-fixed`, `feature`, `process`.
4. **Do not close on a code commit alone.** Close when the reporter confirms, or when a
   maintainer reproduces the fixed behaviour on the same hardware class.
5. **Small, single-topic pull requests.** Do not mix a renderer fix, a builder change and
   a refactor; that is what made #421 and #495 hostile and hard to review.
6. **Every fix gets a game-free test where possible.** Add it to
   `.github/workflows/source-regressions.yml` and to the paths lists, as CONTRIBUTING.md
   requires.

### Hardware test matrix

No single maintainer can own every report. The matrix below is the minimum for closing
P0/P1 work. Community testers are explicitly credited.

| Platform | Minimum devices | What to run |
|---|---|---|
| Windows NVIDIA | RTX 4060 Ti (reference), RTX 3060/3080, RTX 50-series | Build, 3 cold starts, new game through Hyrule Field, save/reload |
| Windows AMD | RX 6600/6750 XT, RX 7000, RX 9000 | Same, plus `RX_VK_BATCH` on/off A/B |
| Windows Intel | Arc A750/B580, Iris Xe / Core Ultra iGPU | Intro, title, first gameplay frame, no-device-lost run |
| Linux RADV | Steam Deck, ROG Ally, RX 6700 XT/7600, 9000 series | Same, plus queue-submit timing |
| Low VRAM | 4 GB and 6 GB cards, 16 GB RAM | Minimum preset, Deku Tree, Castle Town |
| CPU | 4, 8 and 16 thread builds | Build time and worker selection |

---

## 4. Phased plan

### Phase 0 - Triage and release hygiene (2-3 days)

Goal: make the tracker usable before writing more fixes.

- [ ] Add labels: `area/build`, `area/runtime`, `area/vulkan`, `area/audio`, `vendor/amd`,
      `vendor/intel`, `vendor/nvidia`, `type/crash`, `type/regression`, `type/duplicate`,
      `needs-log`, `needs-retest`, `good-first-fix`.
- [ ] Pin the canonical trackers: #503 (build), #150 (device lost), #309 (black screen),
      #195 (cache), #287 (null pool), #145 (flashes), #319 (ground lines), #302 (AMD
      foliage), #36 (overexposure), #105 (VRAM), #17 (Intel), #11 (foliage/textures).
- [ ] Apply the Appendix dispositions: link and close duplicates, ask for the missing log
      on `Info` issues, and mark `Retest` issues with the release to try.
- [ ] Rewrite the issue chooser text from #159: version, GPU/driver, log, where in the
      game, one topic per issue, latest release first. State that reports without a log
      may be closed as `needs-log`.
- [ ] Show the release name/version in the Insert menu and keep the first log line
      authoritative (#325, #481).
- [ ] Publish a "Known issues / first 30 minutes" page: shader warm-up, AV1 extension,
      cache reset, AMD driver minimum, Intel status, how to set `hookshot_env.txt`.
- [ ] Freeze forced updates while a build is running; batch releases (answer #421/#493).

Exit criteria: every open issue has a label and a disposition; the top twelve trackers
are pinned; a new reporter can find the right tracker in under a minute.

---

### Phase 1 - Build unblockers (week 1, P0)

Goal: a clean build is predictable again, and a failed build tells the user why.

- [x] **Worker count.** `nx2/make_game.py:default_jobs()` uses one compiler per thread,
      capped by free memory at ~3.5 GB per job. PR #505 makes the Linux builder honour
      `HOOKSHOT_JOBS`; PR #517 adds the Windows **Build workers** choice. `--jobs` and
      `HOOKSHOT_JOBS` remain the CLI overrides (#494, #503, #502, #511, #418).
- [x] **Scheduling.** PR #429 splits giant chunks by compile cost and compiles the
      longest first; PR #428 emits the lift in parallel and skips it when its inputs are
      unchanged (#503).
- [x] **Installer.** PR #514 retains older installed and partial builder versions so a
      build started by another version can finish; PR #331 tracks tag plus source
      revision, so a corrected tag is not mistaken for the same sources (#493). The
      downloads already use a checksum cache; staging reuse and a visible staging path
      remain follow-ups.
- [x] **Build failures.** PR #499 keeps the child exit status and signal in the stopped
      step; PR #434 reports a missing compiler or Python in the builder protocol instead
      of "the game files could not be read"; PR #453 adds `--log`; PR #469 explains
      staging permission failures. #501, #507, #457 and #478 still need the reporter's
      exact file/log before a code change.
- [x] **Command-line discovery.** #404 already made `cpm_dirs()` find the packaged
      `tools/cpm`; the readme documents `RX_CPM` and `EMSDK` for a clone. The optional
      CLI build log is PR #453 (#387, #267).
- [x] **Honest progress.** `nx2/build.py` now emits `@@progress` weighted by each file's
      estimated compile cost instead of raw file count, so a 20-minute file no longer
      leaves the ring at 95% for minutes. The file count remains beside it. Persisting
      per-stage timings for a previous-build ETA is still open (#491).
- [x] **Portable paths.** PR #451 launches a game from a relative folder; #391 remains
      the feature for relocatable builds and #497 is its duplicate.
- [ ] **Antivirus noise.** The readme explains the false positive, restoring a removed
      tool and Repair. Submitting the current binary to Microsoft and signing the
      installer still need the owner's signing identity (#433, #393).

Merged in this phase: #505, #517, #429, #428, #514, #499, #453, #434, #469, #451,
#414, #331. The exit criteria still need a full game build on the reference hardware:
16-thread/32 GB under 60 minutes, 8-thread/16 GB under 90 minutes, no forced rebuild
without consent, no build failure without a named file and a log, and `HOOKSHOT_JOBS`
working in all three entry points.

---

### Phase 2 - Stability: no silent black screens (weeks 1-2, P0)

Goal: a crash or a lost device produces a visible, actionable message, and the known null
crashes are gone.

- [ ] **NVN null guards.** Merge PR #510 for the memory-pool accessors (#287, #509).
      Add the same early-return guard to every `nvnTextureBuilder*` setter in
      `host/nvn_native.c`; `nvnTextureBuilderSetSwizzle` currently dereferences `P(0)`
      without a check (#500). Log the caller once and return a safe default.
- [ ] **Device lost.** Keep #150 canonical. Confirm the `9bb52a9` fix is in the tested
      build; then route #309, #483, #486, #461, #338, #332, #317, #216, #192, #160,
      #426, #484 and #120 through it. On a lost device, show a dialog with GPU, driver,
      last checkpoint and a "restart cleanly" action; do not leave a black window.
- [ ] **Cache corruption on second start.** Keep #195 canonical. Write
      `hookshot_prepared.bin`, `hookshot_programs.bin` and `mkw_vk_pipelines.bin`
      atomically; stamp them with version and input hash; discard a cache that fails
      validation; add a visible "Reset shader cache" action. This also addresses #130
      and #190.
- [ ] **First-run shader warm-up.** Save the driver cache incrementally while preparing,
      show "preparing shaders (N remaining)" and warn before closing; if the process is
      interrupted, restart from the last good chunk instead of invalidating everything
      (#388, #334).
- [ ] **No mesh-shader hardware.** Keep #148 canonical. Implement the compute fallback
      requested in #454 (Pascal / GTX 10-series and RDNA1); until then, state the
      limitation and fail with a clear message instead of a black screen.
- [ ] **Intel.** Keep #17 canonical; fold #366, #367, #460, #496, #498, #374 and #383
      into it. Recruit Windows Arc and Linux ANV testers. Land the bounded `CasLoop` and
      EQUAL-depth fixes proposed in #17's comments. If Intel remains unsupported after
      the investigation, say so in the README and the in-game menu.
- [ ] **Local crashes.** #504 (freeze when saving), #401 (menu crash on low-spec laptop),
      #164 (crash before opening), #379 (Lake Hylia), #285 (green screen) and #280 need
      logs before code changes; use the `needs-log` label and a short reproduction form.
- [ ] **Audio.** #444 (no audio at all) is a separate path from the visual issues; add an
      audio-device fallback and a clear log line when no device opens.

Exit criteria: three consecutive New Game runs and a save/reload on each matrix device
without a black screen; every crash has a signature in the log; a lost device is always
explained to the player.

---

### Phase 3 - Performance to a steady frame rate (weeks 2-3, P0/P1)

Goal: remove the remaining 3-10 fps cases on AMD/Linux and make first-run stutter
predictable.

- [ ] **RADV submit cost.** #413 is the canonical success story: batching is merged and
      on by default for RADV. Follow up with the reporter's data: default
      `RX_VK_BATCH_POLL_US=2000` on RADV, keep `RX_VK_BATCH=0/1` overrides. Land PR #448
      (udmabuf instead of host pointers) and PR #446/#479 (give imports back under
      pressure). Then re-measure #312's second cause: on `madvise` systems, transparent
      huge pages are not the only cost; the `mmap/munmap` traffic and per-submit
      validation must be handled too.
- [ ] **Submit scaling.** #384 shows CPU replay cost growing with draw count (5.9 ms at
      632 draws, 12.7 ms at 1627). Profile `vk_replay.cpp` on Hyrule Field; reduce draw
      walk/descriptor churn; reuse vertex/index binds when unchanged (PR #423) and
      descriptors after another program (PR #435).
- [ ] **Pipeline bursts.** A warm cache makes the first area smooth; the remaining bursts
      are new pipelines. Prewarm known pipelines on a loading screen or in the
      background (shareable manifest #334), and make `RX_VK_PIPE_SKIP=0` available for
      players who prefer a freeze to a missing frame.
- [ ] **Movies.** #109 and #38: the upscaler networks are 30-90 ms on some cards while the
      same work is 12 ms on an RTX 3050. Use a display-period threshold rather than a
      fixed 40 ms (#336), and make the fallback visible in the log. #52 needs the
      cooperative-matrix fallback described there (16x16x16 tiles through shared memory)
      before AMD's movie upscaler is correct and fast.
- [ ] **VRAM and RAM.** Keep #105 canonical; fold #452, #96, #394 and #151 into it. Show
      a breakdown (textures, render targets, GPU copies, allocator slack) in the log and
      menu, lower GPU-copy share on large cards, and make low-VRAM presets automatic
      (#96).
- [ ] **Utilization.** #470 (CPU high, GPU idle, memory growth), #365 (RX 580 at 0% GPU)
      and #222/#169 need a profile with `HOS_SAMPLE`; add the profile instructions to the
      issue template. #424 and #490 are reference data, not separate fixes.

Exit criteria: Kokiri Forest at 60 fps on the reference RTX 4060 Ti and an RX 6700 XT-class
card; Hyrule Field at 45+ fps; queue submit under 8 ms at 1,600 draws; first-run hitches
covered by a visible warm-up or a prefetch manifest.

---

### Phase 4 - Rendering correctness (weeks 3-4, P1)

Goal: the picture matches the reference build in the tested areas and does not flicker.

- [ ] **Flashes and exposure.** Land PR #513; keep #145 canonical for one-frame
      black/white flashes and brightness pumping, and fold in #458, #386, #382,
      #323, #307 and the flicker half of #305/#419. Keep #36 canonical for overexposure
      and fold in #508. Run the existing flash detector in `scripts/run-vk.ps1` for 60 s
      on each matrix device before closing.
- [ ] **Ground lines and shadows.** Keep #319 canonical for the moving ground lines and
      the Skull Kid vertex explosion; fold in #316 and the ground-line half of #419. Keep
      #468/#515 canonical for
      the Hyrule Field striped shadows; fold in #480. Land the tree-shadow/culling work
      (PR #477 and the #252 family) and verify with a fixed save and a 30 s camera sweep.
- [ ] **AMD foliage.** Keep #302 canonical; fold in #224, #197 and #57. Land PR #482
      (point topology for the grass shadow mesh), #475 (stage buffer starts at zero) and
      #477. Keep #361 canonical for RADV colour noise and fold in #380 (RX 580 noise and
      wave64 subgroup handling).
- [ ] **Missing foliage and textures.** Keep #11 canonical; #35 is fixed in
      `new-installer` and needs a retest; #489 is a grass duplicate; #107 is the
      GPU-last-wrote-memory upload hypothesis and needs its own test; #376 is a missing
      ground texture in Lost Woods. #64 (cutscene effects) and #385 (held torch) belong
      to the same culling/indirect-dispatch family.
- [ ] **Floating trees.** Keep #352 canonical; fold in #422 (mountains not rendering and
      trees in the sky). Check the scene-graph transform and the missing mountain draw,
      not only the tree culling.
- [ ] **Ultrawide.** Land PR #450 for the wider camera; the letterbox fallback from #205
      already prevents stretching. Keep the HUD stretch documented until a HUD layout
      fix exists (#297).
- [ ] **Misc visuals.** #455 (black spider webs), #203 (grey planter), #285 (green
      screen), #280 (texture corruption) and #379 (Lake Hylia) need a fixed save and a
      frame dump; treat them as individual investigations after the shared causes above.

Exit criteria: zero flashes in the 60 s detector; stable shadows in Hyrule Field;
no duplicated Link; foliage density comparable to the emulator reference on NVIDIA and
AMD; no stretched 16:9 image on an ultrawide monitor.

---

### Phase 5 - Features, platforms and quality of life (week 4+)

Goal: only after P0/P1 are clear, spend time on requests.

- [ ] **macOS.** #512: PR #459 exists. Treat it as an experimental port with its own
      build documentation and support boundary; do not promise parity.
- [ ] **Controller.** #420 is canonical; fold in #125. Land PR #372 (presets and camera
      inversion), then #473 (Steam Controller on Linux), #112 (gyro) and #189
      (microphone) as separate features.
- [ ] **Display and graphics options.** #396 (GPU selector) with PR #440, #156
      (RCAS/AA), #68 (resolution, already partly present), #12 (in-game graphics page)
      and #19 (DLSS) as backlog. #305's supersampling/AA suggestions belong here.
- [ ] **Test automation.** #389: allow scripted input without foreground focus so a
      maintainer can run the game and a test harness on one screen.
- [ ] **Portable builds.** #391 with PR #451; #497 is the same request.
- [ ] **Project process.** #421 and #495 are symptoms of release cadence and moderation,
      not code. Keep the weekly release train, publish what changed, and moderate
      abusive reports without engaging.

Exit criteria: feature work does not delay a P0 fix; every accepted feature has a
tracking issue, a test plan and a documented support boundary.

---

## 5. Release gates

A release is ready when all of these are true:

1. `source-regressions.yml` is green.
2. A clean build has completed on Windows and Linux, on at least one 8-thread and one
   16-thread machine, with the worker count and wall time recorded in the release notes.
3. The smoke run passes on the hardware matrix: new game -> Kokiri Forest -> Deku Tree
   -> Hyrule Field -> save -> reload -> second start.
4. The 60 s flash detector reports zero one-frame black/white flashes.
5. No known P0 issue is left without either a fix or a named workaround in the release
   notes.
6. The version is visible in the Insert menu and in the first line of `hookshot_log.txt`.
7. Every closed issue in the release links to the commit/PR and says what to retest.

## 6. Measures of success

| Measure | Now | Target (30 days) |
|---|---:|---:|
| Open issues | 133 | < 70, with the remainder labelled and assigned |
| Duplicate/unactionable open issues | 61 | < 10 |
| Unlabelled issues | 59 | 0 |
| Build time, 16-thread / 32 GB | 7-10 h reported | < 60 min |
| P0 issues without a workaround | 12+ | 0 |
| Flash detector, 60 s | 10+ flashes | 0 |
| First response with a label | not tracked | < 48 h |

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| No AMD/Intel hardware in the core team | Recruit named testers per vendor; make the hardware matrix explicit; do not mark vendor issues fixed without a vendor log |
| Forced updates keep invalidating builds | Release train plus "update after build" policy; PR #514 retention |
| Renderer fixes regress performance | A/B with `RX_VK_BATCH`, `RX_VK_PIPE_SKIP` and the 60 s profile before merge |
| Upstream Eden/mkw-recomp sync conflicts | Keep OoT-specific code in new files; name the upstream base in each sync commit |
| Scope creep from 15+ feature requests | Features wait behind P0/P1; one topic per PR; close duplicate feature asks |
| Reporter cannot retest | Maintainer verifies on the same hardware class; keep a community test log |

---

## Appendix: disposition of all 133 open issues

Legend: **Fix** = root-cause work; **Dup** = close as duplicate after the canonical fix;
**Info** = needs a log or reproduction; **Retest** = likely fixed, verify and close;
**Feature** = accepted backlog; **Process** = project/release work; **Reference** = keep
the data, no code change.

The 133 issues break down as **37 fix**, **61 duplicate**, **14 needs-information**,
**15 feature**, **2 process**, **2 reference** and **2 retest**. In other words, 37 code
changes (plus two process changes) cover the whole tracker once the duplicates are linked
and the information requests are answered.

### Build, installer and release process (22)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 503 | Build performance: previous vs updated compiler | Fix | Canonical build tracker; worker count, chunk scheduling, progress calibration |
| 502 | 600 min build vs 70, wants worker choice | Dup -> #503 | Close after worker fix; keep the request for a visible worker control |
| 511 | Install time far too long | Dup -> #503 | Same; also capture installer timings |
| 418 | Worse FPS and only 4 workers | Dup -> #503 | Worker half is fixed; keep the FPS data under #319/#145 |
| 495 | Abusive "10x faster before" | Dup -> #503 | Close as duplicate; moderate, do not engage |
| 493 | Launcher leaves several extracted ROM copies, re-downloads | Fix | PR #514 + staging reuse + visible paths |
| 501 | Builder crashes with `OSError` on `a64_fpx.h` | Info | Ask for the full log and output path; add path fixture |
| 507 | `clang-23` frontend signal at 98% | Info | Ask for the file and log; retry alone; capture signal |
| 457 | Build stopped during a build | Info | Ask for the last log lines and stage |
| 478 | Steam Deck AppImage build error (ADR lines) | Fix | Make the ADR diagnostic non-fatal or actionable; test on SteamOS |
| 494 | How to use `HOOKSHOT_JOBS` with AppImage | Fix | Document it; PR #505 makes it work |
| 491 | Adaptive build progress meter | Feature | ETA from previous build, keep file count secondary |
| 481 | Add semver | Dup -> #325 | Same work as version display |
| 325 | Versions are names, latest at the bottom | Fix | Version in menu/log/release title; semver from semantic-release |
| 433 | Windows Defender flags the builder | Fix | Submit false positive, sign, document exclusion, keep Repair |
| 393 | Defender flags `HookshotBuilder.zip` | Dup -> #433 | Close after the new installer note |
| 387 | CLI cannot find bundled FFmpeg/CPM | Fix | Extend `cpm_dirs()` and document `RX_CPM`/`EMSDK` |
| 267 | Improve dev building with scripts and a log | Fix | Optional CLI log (PR #453), clearer env discovery |
| 497 | Portable version (no AppData) | Dup -> #391 | Close; portable install is #391 |
| 391 | Portable game builds and relocatable paths | Feature | PR #451 covers relative launch; design the rest |
| 421 | "You merge everything" release complaint | Process | Release cadence and one-topic PR rule |
| 159 | Issue guidelines | Process | Pin the rewritten guidance from Phase 0 |

### Stability, black screen and crashes (35)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 287 | Crash in `OptimizeChangeBanc`, guest `0x4` | Fix | Canonical null-pool crash; PR #510 |
| 509 | Kakariko day/night null memory pool | Dup -> #287 | Close with PR #510 |
| 500 | Crash in `nvnTextureBuilderSetSwizzle` | Fix | Guard every texture-builder setter; add regression test |
| 150 | `VK_ERROR_DEVICE_LOST` on RX 6900 XT | Fix | Canonical device-lost tracker; verify `9bb52a9`; actionable dialog |
| 309 | Black screen at Deku Tree cinematic, audio continues | Fix | Canonical black-screen tracker; route reports here |
| 195 | Black screen on second start / loading a save | Fix | Canonical cache corruption; validate/reset caches |
| 130 | Stuck on loading screen between areas | Dup -> #195 | Close after cache validation |
| 190 | Kakariko not loading | Dup -> #195 | Retest with clean cache |
| 504 | Freezes and visual glitches when saving | Info | Ask for log and save slot; check #150 and audio path |
| 484 | Crash entering the Deku Tree | Dup -> #150 | Verify device-lost signature in log |
| 483 | Black screen and crash on RX 6600 | Dup -> #150 | Same |
| 486 | Black screen after cutscenes on RX 6700 XT | Dup -> #150 | Same |
| 461 | Drivers crash after a few minutes | Dup -> #150 | Same |
| 338 | Black screen, GTX 1660 Super | Dup -> #150 | Same |
| 332 | Black screen after scenes | Dup -> #150 | Same |
| 317 | Crash in Hyrule Castle Town | Dup -> #150 | Same |
| 216 | Black screen with sound | Dup -> #150 | Same |
| 192 | Crash after cutscene on RX 580 | Dup -> #150 | Same |
| 160 | Cannot pass title screen on AMD | Dup -> #150 | Check driver 0x800161 pipeline rejection too |
| 426 | Black screen with audio on Legion Go | Info | Needs log; likely #150 or shader warm-up |
| 401 | Menu crash on low-spec laptop | Info | Needs log and GPU driver |
| 360 | Blank screen entering the Deku Tree | Info | Needs log; differentiate driver pipeline rejection |
| 120 | Black screen on RX 9070 XT | Info | Needs log; check #150/#361 |
| 164 | Crash before it opens | Info | Needs log and release name |
| 26 | Shaders stuck, black screen on AMD | Dup -> #388 | Close after warm-up/cache work |
| 96 | Black screen on 4 GB card | Dup -> #105 | Low-VRAM preset and texture fallback |
| 452 | VRAM maxed entering the Deku Tree | Dup -> #105 | Same |
| 17 | Intel Arc B580 crash / black screen | Fix | Canonical Intel tracker; recruit testers; CasLoop/EQUAL-depth |
| 366 | Intel Core Ultra black screen | Dup -> #17 | Same |
| 367 | Intel Arc B580 Linux freeze | Dup -> #17 | Same |
| 460 | Intel Arc 140V black screen | Dup -> #17 | Same |
| 496 | Intel Arc A750 black screen | Dup -> #17 | Same |
| 498 | Intel GPU black screen after intro | Dup -> #17 | Same |
| 444 | No audio at all | Fix | Separate audio-device/init investigation |
| 148 | Black screen, ~100 mesh programs fail on RDNA1 | Fix | Canonical no-mesh-shader case; #454 fallback |

### Performance and memory (22)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 413 | RADV 10 fps, `vkQueueSubmit2` 13 ms | Fix | Canonical RADV submit tracker; batching merged; poll default follow-up |
| 312 | RADV 3-5 fps, submit 250-295 ms | Fix | Canonical RADV import/submit case; PR #448 and THP/madvise follow-up |
| 384 | Submit cost scales with draw count | Fix | Profile replay draw walk; bind/descriptor reuse |
| 388 | Cold cache: 20 fps, interrupted prepare breaks next start | Fix | Canonical shader warm-up tracker; incremental cache |
| 105 | VRAM use/leak on RX 9000 | Fix | Canonical VRAM tracker; 6 GB texture cap, copy budget |
| 470 | CPU high, GPU idle, memory growth | Fix | Profile with `HOS_SAMPLE`; add to issue template |
| 427 | Shader stuttering on AMD | Dup -> #388 | Close after warm-up/prefetch |
| 394 | 15 fps and 95% RAM | Dup -> #105 | Check memory budget and page file |
| 365 | RX 580 at 0% GPU, 7 fps | Dup -> #312 | Check RADV submit and wave64 |
| 424 | RX 5700 XT fixed by Lichii | Reference | Use as comparison data, not a target |
| 490 | CachyOS/RX 9070 XT report | Reference | Add to hardware matrix |
| 336 | Movie upscale switch for small GPUs | Feature | Display-period threshold instead of fixed 40 ms |
| 334 | Shareable pipeline manifest | Feature | Prewarm known pipelines; no game content |
| 254 | Deku Tree intro stutter | Dup -> #388 | Close after warm-up |
| 227 | Bad fps on RTX 3050 | Dup -> #388 | Check VRAM and movie path |
| 222 | RTX 5060 below 30 fps | Info | Needs log; check movie/upscaler |
| 211 | AMD setup freezes/artifacts | Dup -> #150/#105 | Route by log |
| 169 | RTX 2050 8-14 fps | Dup -> #388 | Same |
| 121 | AMD blurry text, 20-30 min shader wait | Dup -> #145/#388 | Split flicker and warm-up |
| 109 | Stuck at 10 fps | Fix | Movie upscaler threshold and network fallback |
| 58 | Big hangs on cutscenes | Dup -> #388 | Close after warm-up |
| 38 | 6 fps after update, green screen fixed | Dup -> #109 | Movie upscaler path |

### Rendering correctness (40)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 145 | Dark bars and brightness flicker | Fix | Canonical flash/exposure tracker; PR #513 |
| 319 | Ground textures/lighting "crazy", Skull Kid explosion | Fix | Canonical moving-ground-lines tracker; split light flicker to #145 |
| 468 | Shadows going crazy in Hyrule Field/Castle Town | Fix | Canonical shadow tracker; PR #477 |
| 515 | Lighting and shadow streaks in Hyrule Field | Fix | Merge into #468/#515 tracker |
| 36 | Severe overexposure/bloom | Fix | Canonical tonemap/bloom tracker |
| 508 | Deku Tree overexposed after Gohma | Dup -> #36 | Same post-process path |
| 302 | AMD foliage/lighting artifacting | Fix | Canonical AMD foliage tracker; PRs #482/#475/#477 |
| 361 | RADV noise/psychedelic colours | Fix | Canonical RADV noise tracker; resolution workaround |
| 352 | Floating trees in Kakariko | Fix | Canonical transform/culling tracker |
| 107 | Grass texture reuse/upload bug | Fix | Add upload causality test; fixed save |
| 11 | Missing Hyrule Field/Kokiri/water textures | Fix | Canonical missing-texture/foliage tracker |
| 455 | Black spider webs in the Deku Tree | Fix | Investigate texture/lighting; fixed save |
| 385 | Held torch fire does not render | Fix | Check the #252 indirect-dispatch family |
| 52 | RX 9070 XT software matrices, 56 ms | Fix | Cooperative-matrix path (16x16x16 via shared memory) |
| 297 | Ultrawide stretches the image | Fix | PR #450 camera; #205 already letterboxes |
| 458 | Light bug and audio out of sync | Dup -> #145 | Close after flash fix; audio sync separate |
| 422 | Mountains not rendering, trees in the sky | Dup -> #352 | Same transform/culling fix |
| 419 | Graphics issues in release 18 | Dup -> #319 | Retest current build |
| 386 | Texture corruption, fog flicker, green autosave flash | Dup -> #145 | Split transient corruption into #107 |
| 383 | Intel Iris Xe bloom/performance | Dup -> #17 | Intel tracker |
| 382 | Flashing picture and frame-generation judder | Dup -> #145 | Flash fix first, then pacing |
| 380 | RX 580 colour noise, wave64 | Dup -> #361 | Use subgroup properties; RADV noise tracker |
| 379 | Graphical glitch at Lake Hylia | Info | Needs fixed save and frame dump |
| 376 | Missing ground texture in Lost Woods | Dup -> #107 | Upload causality |
| 374 | Intel Arc 140V missing walls | Dup -> #17 | Intel tracker |
| 323 | Flashing/doubling with frame pacing | Dup -> #145 | Retest after PR #513 |
| 316 | Black lines on Zelda's first appearance | Dup -> #319 | Moving-lines tracker |
| 307 | Flicker and duplicate Link | Dup -> #145 | Flash fix |
| 305 | AMD feedback: too bright, shimmer, text fixed | Dup -> #145/#36 | Split by symptom |
| 224 | AMD graphical bugs, missing meshes | Dup -> #302 | AMD foliage tracker |
| 203 | Grey planter in Castle courtyard | Info | Needs fixed save; check detail/normal map binding |
| 197 | RX 9070 XT textures/colours wrong | Dup -> #302 | AMD tracker |
| 151 | Water flashes at VRAM limit | Dup -> #105 | Memory budget and flash fix |
| 64 | Deku Tree cutscene effects missing | Dup -> #385 | Same culling family |
| 57 | AMD missing textures and blurry text | Dup -> #302 | AMD tracker |
| 50 | CachyOS RX 6700 XT 9-10 fps | Dup -> #312 | RADV submit tracker |
| 489 | Rendering the grass | Dup -> #11 | Foliage tracker |
| 480 | NVIDIA 5090 Linux: striped shadows, missing fog/leaves | Dup -> #468 | Shadow/foliage tracker |
| 285 | Green screen | Info | Needs log and driver |
| 280 | Faulty graphics/textures/effects | Info | Needs fixed save and log |

### Features and quality of life (12)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 512 | macOS support | Feature | PR #459; experimental boundary |
| 473 | Steam Controller on Linux | Feature | SDL3/gamepad mapping; separate from #420 |
| 420 | Controller settings | Feature | Canonical controller request; PR #372 |
| 125 | Xbox controller layout swap | Dup -> #420 | Close after presets land |
| 396 | Choose another GPU | Feature | PR #440 |
| 389 | Scripted input blocked by focus | Feature | Test-automation switch |
| 189 | Microphone input for ocarina | Feature | Low priority; document if declined |
| 156 | Replace sharpen/AA with RCAS/SMAA | Feature | Graphics-options page |
| 112 | Gyro aiming | Feature | Depends on controller abstraction |
| 454 | Compute fallback for GTX 1070 Ti / Pascal | Feature | Required for #148 and no-mesh-shader GPUs |
| 19 | DLSS | Feature | Backlog; requires an upscaler integration |
| 12 | In-game graphics settings page | Feature | Consolidate with #68/#156/#396 |

### Verification-only issues (2)

| # | Issue | Disposition | Next action |
|---:|---|---|---|
| 35 | Missing leaves | Retest | Fixed in `new-installer` (offset `0x304`); ask reporters to confirm |
| 68 | Resolution selection | Retest | Confirm the Insert menu covers the request and close |

---

## What "addressed" means here

An issue is addressed when one of these is true:

1. It is fixed on `main`, has a game-free test where possible, is verified on at least one
   device in the hardware matrix, and the reporter is told what to retest.
2. It is a duplicate of a canonical issue, is linked to it, and is closed with the
   specific release that contains the fix.
3. It is a feature that has an explicit decision (accepted with a milestone, or declined
   with a reason) and is no longer counted as an unexplained bug.
4. It is a hardware limitation that is documented in the README/menu, with a clear
   message instead of a silent failure.

That is how 133 reports become a short list of engineering problems, and how the tracker
gets back to representing what actually needs work.
