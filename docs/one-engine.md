# One engine, four games

Hookshot is the engine for four recompilations. All four are to end up in **one release that the launcher shows as
four games**, and a general improvement made for one game reaches the other three. The forks are:

| Game | Repository | Profile key | State (2026-11-13) |
|---|---|---|---|
| The Legend of Zelda: Ocarina of Time | `notquiteog/Hookshot` (this one) | `oot` | released (v2.3.0-rc.1) |
| Mario Kart World | this one ([its page](mario-kart-world.md)); `notquiteog/Lakitu` keeps only what is in review | `mkw` | 1.0.0 plays from `main`: the menus, Grand Prix, the Mushroom Cup's races; in the release |
| Kirby Air Riders | `notquiteog/Dragoon`, branch `fork/kirby-air-riders` | `kar` | lifts, boots to NVN, crashes in a PhysX worker |
| Donkey Kong Bananza | this one ([its page](donkey-kong-bananza.md)); `~/Projects/hookshot-dkb` keeps only what is in review | `dkb` | 3.2.0 plays from `main`: the opening, the mining tunnel, the guide movies; in the release |

This page has the target, the rules that keep it reachable, and the order of the work. It is for the people and the
agents who work on any of the four.

## The target

- **One build of the engine.** The lifter and build (`nx2/`), the Horizon runtime and its HLE (`host/`), the Vulkan
  back end (`gpu/vk/`), the builder, the control bridge and the tests are shared. The renderer `hookshot_vk` is already
  built once and shipped for every game. Only the runtime is compiled per game.
- **A game is a profile.** Its game pack's `pack.json` (`packs/<key>/`, built into `nx2/game_profile.py`'s profiles;
  [game packs](game-packs.md)) gives a game's identity (program id, version, main's build id), its
  patch modules, the offsets and return addresses the runtime recognises, its shader program keys and its defaults. Its
  game patches live in their own files. **The dump decides the game:** the profile is chosen by main's build id, and
  `HOOKSHOT_GAME=<key>` only overrides it. A build no profile knows gets an empty profile, so no game's patches are
  applied to another game.
- **One launcher entry.** `recomplauncher.json` lists the four in `games[]`, which the launcher's schema has for
  several games in one release: each game has its own tile, input (its `.nup`, checked by `nup_build_id`), `--game`
  args and saves, and there is one install and one update. Ocarina's game folder, saves and settings stay where they
  are today, so existing players lose nothing. The builder builds the game it is given into that game's own folder,
  with that game's saves, settings and pipeline caches.

## Rules

1. **Hookshot `main` is the trunk.** A change that is not one game's belongs here, as its own pull request cut from
   `main`. That covers the renderer, performance, the runtime and HLE, NVN, the lifter, the builder or launcher, CI and
   tests. If a fork made the change first, it says so in its pull request, and the change is carried here. The fork
   drops its copy at its next merge of `main`.
2. **Every fork merges `main`** after each Hookshot release, and sooner when a fix it needs lands. That is how a
   rendering or performance fix made for one game reaches the others.
3. **One game's code goes behind the profile, never in place of another game's.** Shared code keeps no game's
   addresses, offsets, keys or build ids without a profile check. A game's runtime code is its pack's
   (`packs/<key>/host/`, compiled into that game's runtime only, through `host/nx2_pack.h`): Mario Kart World 1.8.0's
   pacer and idle loop (0x806bce28, 0x825c6844, 0x837de940, `HOS_MKW_INTERVAL`) are in `packs/mkw/host/` behind
   its build check, kept for a later 1.8.0 profile ([game packs](game-packs.md) a3).
4. **Keep merges cheap.** In a fork, don't rename, move or reformat shared files: add new files for game code. Name
   things by the profile (`PROJECT` in `nx2/game_profile.py`), not by a literal "Hookshot", so that a fork's name is
   one line: `scripts/product_name.sh` is the pattern.
5. **One seam, not one per fork.** The profile design below is the one to build on. A new fork copies it rather
   than inventing its own.
6. **Fixes in the shared engine are measured in more than one game where they can be.** At least, say which games a
   change was run in and which not.

## The converged profile

Lakitu and Dragoon each built a profile seam. Dragoon's took Lakitu's shape (`nx2/game_profile.py`, `Profile`, `Gfx`,
`Upscale`, the committed `game/game_profile.h` and its `GAME_*` macros) and added what one engine for several games
needs. The seam that comes to `main` keeps:

- **The choice by build id** (`for_build()`), with `HOOKSHOT_GAME` as an override and an empty `none` profile for
  unknown builds (Dragoon c0812113).
- **The renderer's keys at run time.** The runtime hands the shipped renderer the game's keys through
  `HOOKSHOT_PROFILE_*` (`host/hos_main.c` `game_profile_env`, `gpu/vk/vk_draw.cpp` `profile_keys`), because a
  compile-time header would give every game Ocarina's (Dragoon).
- **Return-address hooks gated at run time**, on the image's build id matching the profile's
  (`hos_game_profile_live()`, `host/game_profile_check.h`) (Dragoon).
- **A per-dump header** ahead of `game/` for the runtime. Lakitu (`work/game-<key>/`) and Dragoon
  (`<work>/sa/game/`) each wrote one; the pull request picks one path.
- **Ocarina's patch modules unchanged** (Dragoon). Each module already checks its words, and leaving them alone keeps
  their later fixes merging. `game_profile` imports a module by name in one place, and the lift cache takes every
  profile's modules as roots (Lakitu fafd5cde).
- **`game/` carried by the AppImage, the Windows package and the source stamp**, with
  `tests/test_game_profile.py` checking it (Lakitu fafd5cde, 029287fa).
- **The product name from the profile** (Lakitu 61ce02d5).
- Per-game defaults: `npad_players` and `account_users` (Dragoon).

With Ocarina's dump, a build with the seam must lift the same code and run the same as one without it.

## Order of the work

1. **The seam into `main`**, with Ocarina as its first and only profile, as above (Dragoon's session).
2. **The engine-wide fixes the forks made, into `main`**, one pull request each:
   - from Dragoon: R_AARCH64_TLSDESC relocations bound to a host stop (`nx2/image.py`, in c0812113),
     `nn::hid::GetNpadState` (3b6d6b81), `nn::crypto::GenerateCryptographicallyRandomBytes` and
     `nn::oe::GetDefaultDisplayResolution` (62bf9058), and `nn::account` with 1 to 8 users (1711f59e);
   - from the NX2navi port, already against `main`: the fast-linked pipeline leak, menus with VRAM full and the
     memory pool initialised again, plus Ocarina's moving floors and water above 60 fps.
3. **The forks move onto the seam in `main`**, keeping only their profile and game patches, and remove the runtime
   literals in rule 3.
4. **The launcher and one program for every game.** Done: `games[]` in `recomplauncher.json` (each released game a
   tile with its own input and saves, `nx2/game_folders.py --write-launcher`), a folder per game
   (`<base>`, `<base>-<key>`), and the repository: the entry, `nx2/version.py` and the update checks all name
   `notquiteog/Hookshot`. Then, in three pull requests, one program that installs, updates and manages every game:
   - **The shared core** (this step's first): `nx2/game_folders.py --library <base> --json` lists every game,
     installed or not and wherever under the base it is, with its version and whether an update is due;
     `nx2/make_game.py --update-all` updates every built game where it is, one failure not stopping the others
     (`--dry-run` only says what it would do); `--base` builds a copy into its own game's folder; and
     a copy is refused over a folder that holds another game ([development guide](development.md#every-game-under-one-base)).
   - **A game library in the three Builders** (the WPF Builder, the Qt Builder and the Linux Tk Builder), built on
     those calls: every game with its state, install, update all, play.
   - **The rename to NX2PC**, with a migration that leaves Ocarina's folders, saves and settings where they are.
5. **A game joins the release** once it is folded into `main`: its profile, its patches, its row in `games[]`. That
   happens when it boots to gameplay on the engine in `main`. Until then it stays in its fork. In the release each game
   is a RecompLauncher tile (`games[]`) with its own copy (a named input in `inputs[]`, role game, which only its
   tile asks for); `prepare.args` passes each copy as `--game-<key>={input:<key>}` in one argument, and
   `make_game.py` checks it against that game's profile, since an input carries no build check. `python -I
   nx2/game_folders.py --write-launcher recomplauncher.json` writes all of it from the released profiles. Ocarina's
   copy moved from the legacy `game` to the named input `oot` with it: RecompLauncher asks for it once more after
   that update (`.github/release-notice.md`).

## Telling the others

A pull request for the engine names the forks that need it. An agent's finding that another game would hit (a
missing HLE, an SDK difference, a crash in shared code) goes in the pull request or issue where the others look. With
the person's OK, it also goes in the team room (`/team-updates`, see [AGENTS.md](../AGENTS.md)).
