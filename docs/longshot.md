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

### Lifter, build and script fixes (D1)

| Longshot | Author | What |
|---|---|---|
| [#35](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/35) | dyst | fix(build): the hash record is written under its build's start, and keeps only the keys used |
| [#255](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/255) | Yeetbot90 | fix(tests): the game-free checks pass on Windows |
| [#356](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/356) | madebyjordan | fix(build): a game image past 2 GB fails the link instead of running broken |
| [#486](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/486) | jarynclouatre | fix(prompts): a failed rebuild of the button pictures keeps the last good file |
| [#487](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/487) | jarynclouatre | fix(build): the object store's clean-up skips a folder it cannot read |
| [#529](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/529) | jarynclouatre | fix(prompts): the Keyboard prompts show the movement keys as they are bound |
| [#531](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/531) | Zelda Ootr | fix(scripts): hardware_report.py counts a Steam Deck as one, by whole words |
| [#534](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/534) | jarynclouatre | fix(build): a romfs folder without the game's files is refused before the build |
| [#557](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/557) | Zelda Ootr | fix(linux): a saves move that fails part way brings back what it moved |
| [#630](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/630) | HumanBean | fix(build): one build at a time in a work folder, so a second build doesn't compile again what the first is compiling |
| [#655](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/655) | NitnekLeBorgne | fix(linux): follow deep slave graphs for the slow-disk warning |
| [#804](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/804) | d4nkn3ss | test(build): the make_game checks let go of the work folder's lock, so Windows can delete their temporary folders (with #630) |
| [#863](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/863) | llun | fix(build): a romfs without the game's heap definition is refused too, also from a .nup and for an update |
| [#900](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/900) | Zelda Ootr | fix(linux): a start from the Steam Deck's Game Mode no longer compiles every pipeline the preparation built |
| [#948](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/948) | HumanBean | fix(scripts): dev_packs.py makes a folder only when it isn't there, for a Windows sandbox |
| [#1032](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1032) | Jeff Aigner | fix(tests): run_all uses the clone's llvm-mingw folder, as make_game does |
| [#1080](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1080) | HumanBean | fix(build): a failure after the game linked keeps the translation and the game's objects; only the renderer's go |
| [#1125](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1125) | Gabriel Moisan Matte | fix(prompts): Auto picks the face by the pad's SDL type, not its name alone |
| [#1132](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1132) | enum | fix(build): the build runs from, in and into folders past 260 characters |
| [#1155](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1155) | pepe999 | fix(linux): guest memory as dma-bufs under Steam and Flatpak, where udmabuf's size_limit_mb can't be read |
| [#1238](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1238) | HumanBean | fix(build): the compiler check names a folder Windows lets no program list, and never says "no message" |
| [#1246](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1246) | Amateurwizrd | fix(tests): the GPU hang watch's check times its fixture from main(), not from outside |
| [#1423](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1423) | Victor | fix(lift): SDIV of the most negative number by -1 gives the dividend, not a trap |
| [#1495](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1495) | Zelda Ootr | fix(tests): run_all ends every process a check started, on Linux |
| [#1686](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/1686) | rainy | fix(lift): SCVTF / UCVTF (scalar, fixed-point) |

#1132 is taken for the build's own scripts and `nx2/version.py`; Longshot's `nx2/translator_files.py` (its shader stamp
in one file, which this tree keeps in `nx2/build.py`) comes with the perf batch. Not taken: #602 and #1267 (Longshot's
play profiles, not here), #1602 (no `.def` or `.inl` files here), and the setup and Builder fixes (#151, #465, #540,
#641, #690, #1453), which go to the Builders with their rename.

### Renderer fixes (A2)

| Longshot | Author | What |
|---|---|---|
| [#50](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/50) | mooki | test(vk): execute startup pool placement and fallback checks |
| [#189](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/189) | Guep | fix(vk): on Intel iGPUs a split submission's uniform updates are written by its last part |
| [#191](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/191) | Guep | fix(vk): Intel's Windows driver no longer loses the device on float adds to storage buffers: they are the GPU's own atomic |
| [#219](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/219) | TrashPanda | fix(vk): task + mesh compute fallback by default for Navi 23 on Windows, one rule for the IDs |
| [#252](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/252) | Yeetbot90 | fix(vk): a slow movie shows its pictures, and a fault in Intel's Windows compiler leaves that shader out |
| [#307](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/307) | Amateurwizrd | fix(vk): a picture larger than the window is filtered over all its pixels, not sampled |
| [#329](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/329) | klimovich008 | fix(vk): order overlapping inline buffer updates |
| [#346](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/346) | jarynclouatre | fix(vk): the page files' room to grow counts before the commit rule cuts the textures |
| [#350](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/350) | mooki | test(vk): restore the framegen resource and module identifier checks |
| [#403](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/403) | jcabarca | fix(vk): give image descriptors a dummy view when nullDescriptor is off |
| [#440](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/440) | mooki | fix(vk): the replay's own buffer writes stay in command order when unaligned or split |
| [#443](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/443) | nyannoying | fix(vk): Arc B580 no longer draws white shards while moving: a part ends only right before a plain draw |
| [#444](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/444) | nyannoying | fix(vk): Arc B-series no longer loses the device in Kokiri Forest: float adds are the GPU's own atomic |
| [#450](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/450) | mooki | fix(vk): image, image-buffer and texture-buffer arrays are arrays in the SPIR-V |
| [#468](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/468) | Zelda Ootr | fix(vk): retain guest pages during import handoff and retirement |
| [#469](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/469) | Zelda Ootr | fix(vk): release pages ending at a GPU import boundary |
| [#489](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/489) | Yeetbot90 | fix(vk): the graphics in AMD's desktop CPUs decode ASTC on the CPU, so Kokiri Forest no longer resets the GPU |
| [#499](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/499) | jarynclouatre | fix(vk): on Linux the texture helpers started inside a replay run on every core again |
| [#502](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/502) | mooki | fix(vk): storage image and texel buffer arrays are indexed nonuniformly where the GPU can |
| [#551](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/551) | Zelda Ootr | fix(vk): a mesh fallback program with bigger payloads reuses the record arena |
| [#553](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/553) | Zelda Ootr | fix(vk): a read-back after a timed-out one waits for the GPU, and its textures are kept |
| [#554](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/554) | Zelda Ootr | fix(vk): a timed-out read-back's source images are kept until the GPU is past the copy |
| [#556](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/556) | Zelda Ootr | fix(vk): FSR 3.1 frees its images once the chain lets it go, not at its next upscale |
| [#559](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/559) | Zelda Ootr | fix(vk): Auto settings take SteamDeck=1 as a handheld only on an integrated GPU |
| [#621](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/621) | jcabarca | fix(vk): read and publish a compute program's pipeline atomically |
| [#661](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/661) | jcabarca | fix(vk): read RX_VK_SHADOW_BIAS once, safely across recording threads |
| [#672](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/672) | jcabarca | fix(vk): keep a copy into BC3 to whole blocks or the level's edge |
| [#713](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/713) | klimovich008 | fix(pipelines): run All prediction after preparing on Most |
| [#721](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/721) | HumanBean | fix(vk): the start-up screen counts the compute pipelines and says when it writes the pipeline cache |

Fitted to this tree: #252's Intel compiler gate goes on this tree's `guarded_graphics` and `guarded_compute`, without Longshot's fetch lock (#190, below); #307's filter reads through this tree's `pixel()` and `bilinear()`, its SPIR-V regenerated; #553's poll of a timed-out read-back replaces this tree's own wait, and also notes a lost device; #554 keeps a timed-out read-back's textures, while a render target's image goes to the pool at once here (upstream Hookshot #2766), ordered on the one queue after the copy; #721's saving flag sits beside this tree's own count of the compute pipelines (Hookshot #73). Already here: #60 (upstream Hookshot #2897, which it ported) and #101 (this tree's #32). Moved: #190, #404 and #415 to the pipeline cache batch with #416; #223 and the evicted pools' check of #324 to the memory batch; #59 to frame generation; #769 and #928 to the render-thread series; #342 and #526 to B2, with #162 and #248. #500 (B) and #781 come after this batch, on #252's gate.
