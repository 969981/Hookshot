# Fixes from Longshot

[Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot) is the magicbean.dev team's continuation of upstream
Hookshot (GPL-3.0, like this tree). It forked from upstream at `4dd972a8` (upstream #2888, 2026-11-12). Its fixes are
taken here as cherry-picks (`git cherry-pick -x`), each with its Longshot PR number and author. Its history is not merged:
release-please reads every commit a merge brings in, so a merge would put Longshot's rename, its Forgejo tooling and its
`feat(release)!:` in this changelog and version.

**Taken through:** Longshot `main` at `514591a3` (2026-11-13). The next pull starts at:

```bash
git fetch longshot
git log --reverse --oneline 514591a3..longshot/main
```

When a pull is done, the hash above moves to the new tip, in the same pull request.

## Adapting a pick

Longshot renamed the project, so its later commits carry its names. A pick turns them back:

- comment tags `(longshot)` and `(longshot: ...)` become `(hookshot)` and `(hookshot: ...)`;
- file names `longshot_*` (`longshot_env.h`, `longshot_options.ini`, `longshot_load_start.txt`, ...) become `hookshot_*`;
- environment names `LONGSHOT_*` become `HOOKSHOT_*`;
- "Longshot" in a message to the player becomes "Hookshot".

Watch for option ids too: two trees can number a new row alike (Longshot's Damage taken is 55; here it is 56, after Auto
run). `tests/test_option_ids.py` catches it.

## Up to 514591a3

Taken in [#29](https://github.com/notquiteog/Hookshot/pull/29), one squash commit (44a8a29a), so each pick's author is
named here: a squash keeps a commit's author only where GitHub knows its email.

| Longshot | Author | What |
|---|---|---|
| [#7](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/7) | hckrman101 | feat(menu): the Game frame rate cap, Descriptor reuse and Sampler cache apply in play |
| [#8](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/8) | hckrman101 | feat(fps): a Dev level of the FPS counter, past Breakdown, with the renderer's own numbers |
| [#39](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/39) | llun | fix(dlss): the DLSS model row's preset is taken once it holds it, not a feature for each one stepped past |
| [#54](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/54) | mooki | fix(linux): warn about low physical RAM even with ample swap |
| [#55](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/55) | mooki | fix(menu): keep render scale when switching upscalers |
| [#78](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/78) | mooki | fix(oot): preserve Scarecrow's Song recording and playback timing |
| [#94](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/94) | HumanBean | fix(vk): one slow load no longer takes an install back to loading before the title |
| [#108](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/108) | futondestroyer | fix(vk): an opt-in RGBA16F route for PsychoV35 HDR with DLSS and NVIDIA frame generation |
| [#110](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/110) | HumanBean | fix(video): a decoder refuses submissions while 16 wait, so a movie's first seconds are not skipped |
| [#138](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/138) | simonwjackson | feat(gameplay): a Damage taken row (Half to Quadruple) |
| [#176](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/176) | d4nkn3ss | fix(vk): Auto (this PC) reads the primary monitor when there's no window, so an update no longer lowers the resolution |
| [#177](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/177) | HumanBean | feat(log): a log line can be debug or trace, and the movie networks a card leaves out are trace lines |
| [#178](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/178) | HumanBean | feat(input): Mouse camera Off, for playing without the mouse |
| [#187](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/187) | d4nkn3ss | test(input): the display-pointer fixture defines rx_option, which mouse_capture now asks (#178) |
| [#202](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/202) | dyst | test(vk): the texture staging check declares LogLevel and logf_at again, failing on main since #177 |
| [#233](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/233) | TrashPanda | fix(vk): RX_VK_JOB_STEAL=true no longer turns off a draw's pipeline build where it waits |
| [#247](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/247) | MustardTiger | fix(vk): opt-in RGBA16F route for PsychoV35 HDR with AMD FSR 4 |
| [#265](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/265) | jarynclouatre | fix(input): a mouse button released outside the game is not a click on return |
| [#266](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/266) | jarynclouatre | fix(menu): a layout preset is applied whole after a face button was cleared |

Also, in a pull request of their own because each needs its own measurement: #41, #197, #198, #260.

Already here from upstream, under other hashes: #49 (= upstream #2929), #77 (= #2972), #131 (= #3038), #147 (= #2732),
#4 (`third_party/zbic` is carried whole), #63 (`host/rx_backend.c` already steps over 256 clear marks at a time), #174
(this tree compiles the Linux back end itself, and the AppImage carries `third_party/fidelityfx`).

Not taken yet, each worth its own pull request:

| Longshot | What | Why not yet |
|---|---|---|
| #234 | Quick pipelines for draws whose full build is not done | A feature with its own performance measurement |
| #107 | OoT's clustered lights sample their own shadow maps | Changes the shader translator, so every cache is prepared again; needs a visual check |
| #15 | The segment heap, so the driver's compiles run side by side (Windows) | Windows only: measure on Windows |
| #158, #164, #167 | Compile shaders All / Most / None | In the Builders, which differ here |
| #172 | A new install starts at its screen's size | Overlaps upstream #2566's choice of the output by tier, which this tree has |
| #159 | Fog and exposure docs | Check against this tree's renderer first |

Left out on purpose: the rename (`e2a4d453`), Discord as Longshot's (#10, #194), its README, website and docs (#9,
#11, #104, #114, #168, #192, #193, #210, #214, #257, #258, #274), its nightly version display (#170), its Forgejo forge
tools, templates, CI and releases (#3, #5, #6, #13, #18, #21, #48, #72, #76, #79, #99, #105, #106, #113, #115, #116,
#117, #121, #126, #128, #129, #132, #133, #135, #140, #143, #146, #148, #149, #154, #160, #166, #254), the removal of
builders this tree still ships (#161, #163, #171, #175, #179, #181), and importing Hookshot saves into Longshot (#20).

## 514591a3 to 9dd41326

Taken in pull requests by area, each with its run in the game. The list of what was taken, what waits and what is left
out is the triage of this pull (587 commits); each batch adds its rows here.

### Renderer fixes (A1)

| Longshot | Author | What |
|---|---|---|
| [#216](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/216) | TrashPanda | perf(vk): walk the captured ranges once a scan, not a binary search a page |
| [#323](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/323) | Amateurwizrd | fix(vk): HUD labels no longer turn to noise when a render target over their memory is evicted |
| [#325](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/325) | proci | fix(vk): HUD labels the GPU drew no longer go blank after a zone change |
| [#427](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/427) | mooki | fix(vk): shader programs over 1 MiB, or with a stray branch after their end, are no longer left out |
| [#428](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/428) | mooki | fix(vk): closing the game while its shaders are prepared no longer counts as a crash |
| [#442](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/442) | mooki | fix(vk): samplers past the driver's allocation limit are reused, not lost |
| [#498](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/498) | mooki | fix(vk): a game ended while it closes, after a quit during the precompile, no longer counts as a crash |
| [#552](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/552) | Zelda Ootr | fix(vk): a recording without a draw stream is submitted instead of crashing |
| [#670](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/670) | mooki | fix(vk): a timeline wait that comes back device-lost is said as a lost device, not as a timeout |
| [#902](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/902) | Zelda Ootr | fix(vk): the start names the driver's shader cache and says when the pipelines were built in another environment |
| [#910](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/910) | bvtterbrot | fix(vk): the Anisotropic filtering row leaves images of one level alone, so no bright lines along edges |
| [#975](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/975) | HumanBean | fix(vk): GPUs on the compute fallback load programs when the game makes them, not all 32,645 pipelines before the title |
| [#1083](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1083) | nyannoying | fix(vk): a submit's capture no longer misses the pages the replay's collection is marking again |
| [#1220](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1220) | Joey Magnus | fix(vk): the write-back diff pass has a checkpoint of its own in device-lost reports |
| [#1418](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1418) | Joey Magnus | fix(vk): an update that changes no shader no longer rebuilds every pipeline |
| [#1447](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1447) | pmwork-personal | fix(vk): leave out the depth draws of a mesh the game no longer shows: no ghosting with the camera inside Link |
| [#1450](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1450) | HumanBean | fix(vk): a load-at-create start whose mesh fallback builds stall in the driver goes on to the title, with progress on the way (without its save before the title: precompile_save.h is not here) |
| [#1458](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1458) | HumanBean | fix(vk): the swapchain's image is waited for on the CPU, so a window behind another one no longer stops the GPU's work |
| [#1615](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1615) | SonnikBoom | fix(vk): precompile the Hyrule drawbridge scene pipelines |
| [#1631](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1631) | Guep | fix(vk): no EQUAL depth margin for draws that blend (Death Mountain Trail's khaki veil on Intel) |
| [#1839](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1839) | gmourao | fix(vk): recognize RDNA 4 GPUs by PCI device ID (ported: the ids only) |

#1659 is taken as its commit, but its line stays as #1631 had it: this tree's `dynamic_state` reads `cur_program`.

### Host fixes (C1)

| Longshot | Author | What |
|---|---|---|
| [#102](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/102) | mooki | fix(runtime): handle windowless Windows stop requests (without its crash record: not here) |
| [#341](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/341) | llun | fix(hos): wait at the commit limit for the host's own allocations, then say out of memory (host API 23 here) |
| [#488](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/488) | omgitsgela | fix(vi): pace at 240 while a movie plays, so its player stops losing pictures |
| [#496](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/496) | Zelda Ootr | fix(host): a thread lowered right after it is created runs lowered on Linux |
| [#558](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/558) | Zelda Ootr | fix: a log file that cannot be opened no longer closes stderr |
| [#594](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/594) | mooki | fix(vcores): a thread starved of its core gets it after 1 s, not an escape after 2 s |
| [#911](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/911) | dyst | fix(host): hookshot_env.txt can't set HOS_CONTROL_PEER_SID |
| [#1291](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1291) | HumanBean | fix(vk): at Windows' commit limit the renderer's C++ allocations wait for memory, then say out of memory (host API 23 here) |
| [#1322](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1322) | omgitsgela | fix(linux): a Gaming-mode start still writes hookshot_log.txt (the game log follows the game, not Steam's pipe) |
| [#1346](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1346) | cuecuemoar | fix(fs): a long path with a doubled separator opens: the cutscenes' voices in a deep game folder |
| [#1422](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1422) | Joey Magnus | fix(host): closing the game waits for a save it is writing |
| [#1459](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1459) | dyst | fix(host): the saved logs leave out the player's user name |
| [#1683](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1683) | rainy | fix(svc): svcQueryMemory answers a free range as the whole gap, Inaccessible past the address space |
| [#1827](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1827) | SadTransGirl | fix(host): a CPU read or write of texels gives the thread's virtual core up while it waits for the GPU (no ~1 s freeze at area changes) |
| [#2007](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/2007) | SadTransGirl | fix(host): the game's memory stays in RAM when the PC runs short: a soft working-set minimum (HOS_WS_FLOOR_MB) |
| [#2012](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/2012) | siantm | fix(os): spin_begin reads HOS_SPIN_ALWAYS once, under a lock, and its cache atomically |

Longshot's crash records (#75 and the fixes on them: #593, #626, #628, #1375, #1416, #1714) are not here; #102 and #558
are taken without their crash-record parts, and #1459 without its crash window.
