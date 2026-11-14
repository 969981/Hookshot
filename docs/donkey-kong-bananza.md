# Donkey Kong Bananza on this engine

*Donkey Kong Bananza* (Switch 2) builds and plays from this repository with its own game profile (`dkb` in
`nx2/game_profile.py`, chosen by main's build id). It is not offered by the release yet (`released=False`: see
[one-engine.md](one-engine.md) step 5). Game data never enters this repository: the dump is read where it lies, and the
extraction, the lift and the builds stay in the gitignored `work/`. Where a value comes from the game, this page says
how it was found.

## 1. Where it stands (2026-11-14)

| | State |
|---|---|
| The dump | `Donkey Kong Bananza.nup`: base v0, update **v458752 (3.2.0)**, add-on content `040074a01bf13001` (romfs only, 7,270 files) |
| Identity | program `040074A01BF12000`, main build `e25c90c1c136638885659fd55bf8a03b3403489d`, sdk `18265dae...`, SDK and NVN 20.5.19, no subsdk modules |
| Game profile | `dkb`: identity only, saves in `hookshot-dkb`, the movie catch-up off (`video_catchup=False`), not released |
| Lift | 188,676 functions, 9,102,448 instructions, **100.0000 %** translated, 2,175 jump tables (1,986 bounded) |
| Plays | from `main` alone: the first-start menu (Normal / Assist Mode), the opening cutscene, the mining tunnel with the HUD, punching, walking, climbing, gold, the guide movies (72 loops in one run, no abort); median 60.0 fps at 380-4,800 draws a frame on an RTX 5060 Ti (Linux, NVIDIA 615.71.09) |
| Where it stops | nowhere found yet: 7 minutes of scripted play past the tunnel's guide with no abort. Scripted input does not get through the tutorial; a person playing is the next test |

## 2. What stopped it, in boot order, and what fixed it

Each is a pull request to this repository, with Bananza and Ocarina runs.

1. **#12:** `nup.py` crashed on the package's add-on content title (no exefs, directory `0xffffffff`).
2. **#14:** offline answers where the generic stub said a thing was there, or the lifted SDK reached IPC and aborted:
   `streamplay::GetRequiredMemorySize` 0 (no GameShare), `bcat::MountDeliveryCacheStorage` 0x47a,
   `account::LoadCachedNintendoAccountInfo` 0x1907c, `account::GetLastOpenedUser` the one user. With these, the game
   opens no service through `sm:` (no `ipc:` line in a whole run).
3. **#15:** `hid::GetTouchScreenState<1>`, the single-state read, aborted in the lifted SDK. After this the game
   reached its first-start menu.
4. **#16:** the guide movies' SDK 20 `nn::codec` forms (`InitializeVp9Decoder` without an output mode, the out-pointer
   ready event, `CodecMode` preferences and pools, the work-buffer size, the native image handle) go to the VP9
   decoder already here. The pool's fields after the SDK's 24 bytes are the game's own; the runtime leaves them alone.
5. **#40:** `fs::EnsureSaveData` answered. Since #20 (null guest reads fault on Linux too), the lifted SDK's read
   through a null fs proxy crashed the boot.
6. **#41 and #42:** the guide movies' player aborted after one to sixteen loops. It counts every picture it submits.
   The runtime finished more buffers than that: an acquire before any submission was reached at the next one, while
   its picture went elsewhere, and the end filled no buffer while acquires after it got one again. Its catch-up also
   skipped pictures. Now an early acquire is reserved for the next picture and the end has its own picture (#41), and
   Bananza's profile turns the catch-up off (#42).

Also fixed on the way, because they would have bitten any second game: saves in a folder per game
(`GAME_SAVES_DIR`, part of #10), and the build's shader census without Ocarina's `Pack/ZsDic.pack.zs` (#17).

## 3. Open

1. **`nvnCommandBufferPushGraphicsConstants`** is not implemented (logged once, answered 0). The game pushes
   `{1/w, 1/h, w, h}` into graphics root table 0, which its full-screen blit shaders read as constant bank 24
   (dkb-pc-recomp). Nothing has looked wrong in the tunnel yet; a frame capture of a blit would tell.
2. **Virtual-core waits** of 200-850 ms appeared once, with the PC loaded (load average 79). dkb-pc-recomp lists 23
   recursive spin-lock loops in 3.2.0 main that stall heap and RomFS loads under virtual cores. Measure on an idle PC
   before acting.
3. **Add-on content**: `aoc::CountAddOnContent` is the generic stub's 0, so the DLC (mounted as "EmeraldRush" on a
   console) is not offered. Not needed to play the base game.
4. **`oe::GetCurrentGraphicsScale`: fixed, Bananza renders at its full size.** The host answered Ocarina's enum 1
   (x0.8). Bananza reads it as an enum: main 0x8033e9b4 and 0x80569324 keep a mode byte, and 0x803ad180 and 0x805695d0
   index a float table `{3, 12, 16}` with it. With 1 it drew at 80% of its 2880x1620 (2304x1296; scenes 1536x864). Its
   profile now answers 0 (`graphics_scale`), so it draws at 2880x1620 (scenes 1920x1080). Measured in a quiet window on an
   RTX 5060 Ti (2026-11-14, alternating 1, 0, 0, 1): GPU time 1.70 → 2.20 ms a frame (median; p90 2.40 → 2.90), 60.0 fps
   either way. A card about 9 times slower would go from about 15 to about 20 ms, under 60 fps:
   `HOS_GRAPHICS_SCALE_ENUM=1` gives the old answer.
   **Open:** that variable is the only way back to 80% for a slow card. The scale wants to be a choice in the game's
   menu (full size or 80%), not an environment variable.
5. **`oe::GetDisplayVersion`** answers "1.8.0" (the game logs `ReleaseVersion : 320 (nmeta=1.8.0)`). The update's real
   string is not in the dump.
6. **Not tried:** sound (every run had `HOS_AUDIO=0`), Windows, AMD, the add-on content, play past the tutorial.

## 4. dkb-pc-recomp: what was taken

[dkb-pc-recomp](https://github.com/Lucas-blip-png/dkb-pc-recomp) (`bananza` at `fac48bc`) already plays this game, on a
different architecture (TotK Recomp's). It is GPL-3.0-or-later as a whole, with the files from TotK Recomp under MIT.
Our dump's ExeFS is byte-identical to its `dkb458752` target (SHA-256 of main, sdk, rtld and npdm all match), so its
3.2.0 findings apply unchanged.

No code was copied: each answer was written here. What was taken are facts it found by running the game, credited
where they are used:

| Finding | Taken? |
|---|---|
| Offline answers on 3.2.0: streamplay memory 0, BCAT 0x47a, cached account 0x1907c, last opened user | yes (#14) |
| SDK 20 `nn::codec` VP9 names, the preference's +8 / +10 | yes (#16) |
| `fs::EnsureSaveData` answered above IPC | yes (#40) |
| The movie decoder's model: a reserved buffer filled by the next picture, an end packet filling a buffer | yes (#41) |
| NVN2 procs Hookshot lacks (`DeviceGetFlags`, `RegisterFastClear*`, `DeviceMessageBuffer*`, `PushGraphicsConstants`, HDR window metadata, ...) | not needed so far: only `PushGraphicsConstants` was called (section 3.1) |
| NVN API minor 30 gate at main+0x5c61ac | not needed: Hookshot's 231.40 passes |
| Memory: below 8 GiB the game crashes at start; it asks ~9 GiB | already met (9,809 MB) |
| Spin-lock loop heads in 3.2.0 main | not yet: measure first (section 3.2) |
| `GetCurrentGraphicsScale` enum, the dynamic-resolution controller (main+0x38db98), the 2880x1620 virtual display | not yet (quality, not boot) |
| Add-on content mount ("EmeraldRush") | not yet |

## 5. Running it

```sh
python -I nx2/make_game.py --nup "<Donkey Kong Bananza.nup>" --out work/game-dkb --work work/build --no-prepare-shaders
python -I scripts/hookshot_ctl.py launch work/game-dkb --data work/data-dkb --env HOS_AUDIO=0 --env RX_VK_PRECOMPILE=0
```

The profile is chosen by the dump's build id; saves go to `hookshot-dkb` under `--data` (or `$XDG_DATA_HOME`). At the
first start, press A once the menu takes input (about 40 s in): Normal Mode loads the opening, and Y punches.
