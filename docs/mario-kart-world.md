# Mario Kart World on this engine

*Mario Kart World* (Switch 2) builds and plays from this repository with its own game profile (`mkw`, its game
pack `packs/mkw/pack.json`, chosen by main's build id). It is in the release: RecompLauncher shows it as a
tile of its own, which asks for its own copy (`recomplauncher.json` `games[]` and `inputs[]`, written by
`nx2/game_folders.py`), and the builder checks that copy against the profile (`make_game.py --game-mkw=<copy>`). The
AppImage's builder, `HookshotBuilder.exe` and `nx2/make_game.py` build it too. Game data never enters this repository: the dump is read where it lies,
and the extraction, the lift and the builds stay outside it. Where a value comes from the game, this page says how it
was found. Most of the work was done in the Lakitu fork (notquiteog/Lakitu) and brought here one pull request at a time.

## 1. Where it stands (2026-11-14)

| | State |
|---|---|
| The dump | `Mario Kart World [0400C3F00006E000][v0].nup`: base v0 (**1.0.0**), no update, 205,660 RomFS files |
| Identity | program `0400C3F00006E000`, main build `9e244438c3d8ef1c703a6a85ef99c4f0`, SDK 21.4 |
| Game profile | `mkw`: identity, main at `0x80020000`, the parallel replay on 4 threads (`vk_parallel`), saves in `hookshot-mkw`, not released |
| Lift | 234,094 functions, 12,961,823 instructions, **100.0000 %** translated, 3,383 jump tables (3,015 bounded); a full build from the `.nup` took 29 minutes (4 compile jobs) |
| Plays | from `main` alone: the title, the menus, Grand Prix, the Mushroom Cup's opening movie and its races. Driven headless with accelerate held (no steering), one 25-minute run finished three of the cup's four races to their results and was on the fourth's second lap |
| Frame rate | races at 43-60 fps once a course's pipelines are made (RTX 5060 Ti, Linux, NVIDIA 615.71.09); a course's first visit is slower while they compile |

## 2. What stopped it, in play order, and what fixed it

Each is a pull request to this repository, with Mario Kart World and Ocarina runs.

1. **#10:** the game profile itself (chosen by main's build id), with the saves in a folder per game. Before it, a
   Mario Kart World build wrote its save (`Play/account.sav`) into Ocarina's folder.
2. **#21** (with #16): the cup's opening movie calls `nn::codec` by the older SDK's names. Most of them are SDK 20's
   forms (#16, for Donkey Kong Bananza): `InitializeVp9Decoder` with and without work memory, the out-pointer ready
   event, the `CodecMode` preference and pool, the work buffer's size. #21 adds what 1.0.0 needs beyond them:
   `MakeDecodeImagePreferenceForCompat`, `InitializeVp8Decoder`, and `GetVp9DecoderOutputBufferProperties` with a
   `ColorSpace` pointer (its index on the guest stack). Without them the movie thread stopped at svcBreak when a Grand
   Prix started.
3. **#31:** the first race stopped about 75 s in, at `indirect jump to an address with no function at 0x81e0f200`.
   The lifter had bounded a jump table by a compare of unrelated code above a `b` (2 of its 5 cases). Every game's
   lift: 16 of Mario Kart World's tables and 1 of Ocarina's gained cases.
4. **#39:** course loads stalled for seconds and races stuttered: the ASTC upload's BC1/BC3 choice read the blocks back
   out of write-combined staging memory. Frames with 50 ms or more of uploads went from 219 to 11 on the same route;
   the worst from 26 s to 0.22 s.
5. **#55:** the parallel replay's default is the game profile's. This game records most of a race frame in one command
   buffer, and its races run faster on 4 threads (Ocarina's do not, so its profile leaves it off).

Also run in Mario Kart World on the way, as its movies take the same code: #41 (an early acquire reserved, the end's
own picture) and #29 (a decoder refuses a submission while 16 wait: the opening movie's 1424 submissions, 630 put off,
none lost). #19 gives a game other than Ocarina its own game and work folders.

## 3. Open

1. **A whole cup in one run.** Driven with accelerate held and no steering, the kart finishes last, and the fourth race
   did not end inside 25 minutes. In that run the third and fourth races fell to 2-35 fps: the GPU took 4-5 ms a frame
   and the replay 10-20 ms, while the game's threads waited 1-5 ms on average for a virtual core. A second run did not
   repeat it (31-61 fps), so it looks like load from elsewhere on a shared PC. A person playing is the next test.
2. **Where a race frame goes** (the all-thread sampler, `scripts/crit.py`, a 38-fps stretch): the presenting thread runs
   73% of the time (the replay 44%: draws 31.5% with ~12% in NVIDIA's driver, descriptors 16%; memory capture ~15%)
   and waits 27% for the game's next frame; the game's frame thread (BaseProcExecutor0) runs 63%. They alternate more
   than they overlap.
3. **Nothing game-specific is measured yet beyond the identity:** no widescreen or field-of-view patch, no frame-limiter
   offsets (`gfx`), no present-poll hooks, no upscaler program keys, and what the game makes of
   `oe::GetCurrentGraphicsScale` is not read (Bananza reads it as an enum; the host answers Ocarina's 1).
4. **SDK calls the generic stub answers, none fatal so far:** `nn::movie` Initialize / IsInitialized / Finalize, audio
   capture, the software keyboard, `prepo`, `friends`, `nifm`, the time zone rule.
5. **1.8.0** (main `d00e4f39...`) is another executable with other addresses: no profile, no dump here.
6. **Not tried:** sound (every run had `HOS_AUDIO=0`), a window, a real controller, Windows, AMD, other modes (VS Race,
   Time Trials, Free Roam, online), split screen.

## 4. mkw-recomp: what was taken

[mkw-recomp](https://github.com/adriadam10/mkw-recomp) (adriadam10, `5ff4fbb`, MIT, Copyright (c) 2026 Adria) plays this
game on a different architecture. No code was copied: each answer was written here. What was taken are facts it found,
credited where they are used:

| Finding | Taken? |
|---|---|
| The 1.0.0 row: main's build id and the RomFS count | yes (the profile) |
| The older SDK's `nn::codec` signatures and answers (`ov_codec.c`) | yes (#21) |
| Offline answers for `nifm`, `friends`, `ldn` (`ov_net.c`) | not yet: nothing has needed them |
| Patch tables for 1.0.0 and 1.8.0 (`recomp/patches-mkw.txt`): output size, aspect, LAN | not yet |

## 5. Running it

```sh
python -I nx2/make_game.py --nup "<Mario Kart World [0400C3F00006E000][v0].nup>" --out ~/Games/mkw --work ~/.cache/mkw-work
python -I scripts/hookshot_ctl.py launch ~/Games/mkw --data ~/mkw-data --headless --env HOS_AUDIO=0
```

The profile is chosen by the dump's build id; saves go to `hookshot-mkw` under `--data` (or `$XDG_DATA_HOME`). The
route to a race through the control bridge, each press after the screen settles: L+R at the title, then A (Single
Player), A (Grand Prix), A, A, A (the cup), A (50cc), A (OK, "Start Grand Prix?"). The cup's opening movie plays, then
the race; hold accelerate (`press ZR+A --frames 300`, again).
