# Game packs: one engine in NX2PC, each game in its own repository

**Status: a design; its open questions were decided on 2026-11-22 (section 6), except two checks. No code has moved.**
Written 2026-11-22 on `main` 0826b396 (rc.40 + #130).

The decision this design serves:

- The engine and the program that installs, updates and manages every game move to **`notquiteog/NX2PC`** (created,
  public, with Hookshot's full history at 2a440fd0).
- Each game lives in **its own repository as a game pack**: `notquiteog/Hookshot` becomes Ocarina of Time's,
  `notquiteog/Lakitu` Mario Kart World's, `notquiteog/Dragoon` Kirby Air Riders', and the new, empty
  `notquiteog/Bananza` Donkey Kong Bananza's. A pack repository holds only that game's profile, patches, docs and tests,
  and publishes a small, versioned pack. NX2PC lists the packs it supports, with pinned versions and hashes, and fetches
  them. A game repository never copies the engine.
- **`notquiteog/Hookshot` is not renamed.** Old Builders and AppImages check Hookshot's releases: a bridge release there
  installs NX2PC.

Already done or in progress elsewhere, and not redone here: #130 (merged: `game_folders.library()`, `make_game.py
--update-all` and `--base`), PR 2 (a game library in the three Builders), and the rename PR (Hookshot → NX2PC names,
with a migration: the Lakitu session's product-rename plan and rename inventory). Section 5(e) says how they line up.

"Pack" here always means a **game pack**. `scripts/dev_packs.py` already uses "pack" for a development runtime over a
base; that tool keeps its name, and the docs always say "game pack" or "dev pack".

## 1. What is one game's today

Counted on 0826b396 with `wc -l` and `grep`. "Game's" means it acts on one game's code or data and is wrong for any
other. "Mis-named" means general behaviour that only carries a game's name, or is only implemented by one game's
patches.

### Ocarina of Time (`oot`)

**Python, run by the lifter or the build**

| Path | Lines | What | Verdict |
|---|---:|---|---|
| `nx2/game_profile.py` `OOT` | ~57 | identity, `main_base`, `patch_modules`, `code_patch_module`, `Gfx` offsets, `Upscale` keys, `Hooks` return addresses, `romfs_needed`, npad and user counts | game's (data) |
| `nx2/game_profile.py` `DEFAULT = "oot"`, `saves_dir()`'s `"hookshot"` | 3 | the default game: no folder suffix, the legacy saves folder | release policy, not game data (goes to the lock file, section 3) |
| `nx2/oot_patches.py` | 1,200 | the code patch module (`patch_file`, `output_size_file`, `report`, `LIFE_BUILD_ID`): C edits at OoT's functions | game's |
| `nx2/oot_wide.py`, `oot_fov.py`, `oot_jitter.py`, `oot_occlusion.py` | 112 + 24 + 66 + 37 | lift patch modules (`install(lifter, img[, build_id])`) | game's |
| `nx2/prompt_assets.py`, `prompt_draw.py`, `prompt_keys.py`, `prompt_faces.py`, `prompt_faces/` | 1,347 + 342 + 71 + 110 | button prompts per controller, made from OoT's `External.bfotf`, `Common.bntx`, the Talk ring and ocarina hint layouts | game's file names and layouts over a general idea; moves to the pack whole first, a general part later |
| `nx2/make_game.py` `OOT_` env prefix in `lift_key`, the prompts step (2918), the "Ocarina's e0f3ccfa" messages | ~15 | game names in shared code | mis-named; the prefix and the prompts step come from the pack |
| `nx2/lift.py` 185, 222; `nx2/chunks.py` 523, 537, 637 | comments | already go through `game_profile.load_module` | general |
| `scripts/oot_saves.py` | 410 | OoT save conversion | game's |
| `scripts/hookshot_ctl.py` (map, teleport, age commands) | ~60 of 663 | the control bridge's OoT verbs | game's verbs over a general client |
| `assets/pipeline_states/` (tour + 40 contributed lists) | 1.1 MB | OoT's pipeline hashes; `make_game.py ship_state_list` copies them into **every** game's folder today | game's (data), wrongly shipped to MKW and DKB |
| `assets/profiles/hookshot_vk.profdata.xz` | 166 KB | the renderer's play profile, recorded in OoT | engine (the renderer is shared); stays in NX2PC |

**C compiled into the game's runtime** (every `host/*.c` is compiled into every game's runtime today: `nx2/build.py`
1304)

| Path | Lines | What | Verdict |
|---|---:|---|---|
| `host/oot_damage.c`, `oot_fov.c`, `oot_heap_retry.c`, `oot_high_fps.c/.h`, `oot_menu.c`, `oot_text_size.c` | 22 + 43 + 210 + 194 + 825 + 76 | OoT's damage scale, lens, heap retry, high-fps fixes, the game menus' mouse, text size | game's |
| `host/hos_main.c` 666–1836 | ~1,170 of 2,360 | the debug tools (hearts, flight, free camera, speed, inventory, clock, age), cross-map teleport and `oot_destinations[]`, Skip opening, the ultrawide cover (`hos_wide_cover`), occlusion switch | game's, inside the shared `main` file |
| `host/control.c` 2318–2331, `control.h` `oot_state` | ~30 | the control bridge's `map`, `teleport`, `age`, `oot_state` filled with `hos_oot_*` | game's hooks in a general table |
| `host/nn_hid.c` 370, 537–600, 872–877, 1132 | ~80 | `!cheat` live action, the ocarina's five note controls, flight shortcut, auto run's "player free" | game's |
| `host/nn_vi.c` 291–376, `nn_applet.c` 129, 172, `mouse_native.c`, `prompts.c`, `hos_zstd.c`, `frame_whole.h`, `rx_backend.c` 416 | — | `hos_text_scale`, `hos_camera_scale`, `hos_fixed_resolution`, `hos_mouse_camera`, the prompt font server, the zstd stream: functions and globals the OoT patches call | general mechanisms; they stay in NX2PC as the pack ABI (section 2.4) |
| `host/hos_main.c` `"RX_VK_OOT_FOG"` and `HOS_OOT_*` env | — | retired-name list; OoT's switches | game's switches; the list stays |

**Compiled into the shared renderer** (`hookshot_vk`, built once for every game: this is what must leave)

| Path | Lines | What | Verdict |
|---|---:|---|---|
| `gpu/vk/oot_cheat_items.h` | 109 | the Inventory page's items, sections, probes; `OOT_CHEAT_ITEM_BASE`, `OOT_CHEAT_AGE_ID`, `OOT_CHEAT_ITEM_ACTION` (also included by `host/hos_main.c` 24) | game's |
| `gpu/vk/vk_menu.cpp` | ~1,000 of 5,452 (estimate) | the Debug tab's 12 tools and groups (1469–1500, 1590–1760, 2991–3140), `teleport_names[]` and sections (1609–1690, a second copy of `oot_destinations`), `draw_inventory` 3559, `draw_teleport` 3698, their mouse and press handlers (4782–4830), `TAB_DEBUG` | game's |
| `gpu/vk/vk_menu.cpp` rows | ~25 | texts that name OoT: "Link", "Navi", "the ocarina", "Deku Tree" in Text size, Skip opening, Microphone, Auto run, Damage taken; `"Ocarina note (L)"` etc. in the bindings (2604) | mis-named: general rows with OoT's words |
| `gpu/vk/vk_menu.cpp` rows Field of view, Text speed, Text size, Camera speed, Disable camera centering, Mouse camera, Game speed, Skip opening, Auto run, Microphone, ultrawide | — | general settings that **only OoT's patches implement**: MKW and DKB show them today and they do nothing | mis-named: the row is general, the implementation is the game's; the pack says which it implements |
| `gpu/vk/menu_i18n.h` | ~40 English texts x 12 languages of 6,960 lines (estimate) | translations of the texts above | game's strings |
| `gpu/vk/vk_api.h` 19–60 | ~10 | `HOS_OPT_CHEAT_*` (32–36), `HOS_OPT_SKIP_OPENING` (52), `HOS_OPT_DAMAGE_TAKEN` (56) option ids | game's ids in the shared option space |
| `game/game_profile.h` (generated, OoT's), `-I game` for the renderer (`nx2/build.py` 1472, 1493) | 81 | the renderer's built-in fallback keys are OoT's; the runtime overrides them through `HOOKSHOT_PROFILE_*` (`host/hos_main.c` `game_profile_env`) | engine should build with the empty profile |

**Launcher, Builders, docs, tests**

| Path | What | Verdict |
|---|---|---|
| `recomplauncher.json` `inputs[0]`, `games[0]`, the top-level `saves` (`{local}/hookshot`, `{home}/Games/Hookshot/user`) | written by `nx2/game_folders.py --write-launcher` from the released profiles | generated from packs |
| `recomplauncher.json` `settings` `ultrawide`, `speed`, `text`, `mousecam` | release-wide settings only OoT implements | mis-named. Decided (2026-11-22): the settings stay release-wide; the engine hides or ignores what a game does not implement (`features`, 2.4) |
| `builder/HookshotBuilder/Games.cs` `Profiles` | key, name, build id of oot, dkb, mkw, hard-coded (tests/test_builder_games.py holds it to the profiles) | duplicate of pack data; PR 2 should read `--library --json` instead |
| `builder/setup/setup.c` 1060 `HOOKSHOT_DEFAULT_GAME "oot"` | the default game's folder | release policy (the lock's `default`) |
| `builder/*/Strings.cs`, `strings.py` (12 languages), `hookshot_builder.py` 332, 1219, `core.py` 572, `Finder.cs` 105, `window.py` 843 | "Turns your own copy of Ocarina of Time...", "Hookshot requires build e0f3ccfa", shortcut descriptions | mis-named; PR 2's library replaces these headings with the game list |
| `readme.md` (1,234 lines) | the player guide is mostly OoT's (controls, debug tools, known issues), the rest engine | split: NX2PC readme, pack readme |
| `docs/control.md` (41 OoT mentions), `docs/saves.md`, `docs/longshot.md` | teleport/age verbs, OoT saves, the Longshot (OoT upstream) pull process | split per verb; `saves.md` and `longshot.md`'s OoT half to the pack |
| `docs/tested-configurations.md` | ~266 rows, no Game column: ~13 MKW, ~18 DKB, ~3 KAR (Dragoon), the rest OoT | Decided (2026-11-22): one table in NX2PC with a Game column |
| `tests/` | 70 of 855 files touch OoT code: `test_oot_*` (7 files, 1,696 lines), `test_ocarina_notes.py`, `test_opening_skip.py`, `test_teleport_minimap_flag.py`, `test_wide_cover.py`, `test_debug_tools_linux.py`, `test_text_size.py`, `test_auto_run.py`, ...; 17 checks cut functions out of `vk_menu.cpp` | the pure-OoT ones move with the pack; mixed ones split |

### Mario Kart World (`mkw`)

| Path | What | Verdict |
|---|---|---|
| `nx2/game_profile.py` `MKW` (~30 lines) | identity of 1.0.0 (9e244438), `vk_parallel=4`, no patches, hooks or keys yet | game's (data) |
| `host/hos_main.c` 99 `hos_game_is_mkw` (build **d00e4f39**, i.e. 1.8.0), `host/nvn_native.c` 2645 `0x806bce28` → `hos_mkw_pacer`, `host/nn_vi.c` 245, 622–647 (`HOS_MKW_INTERVAL`, `0x837de940`), `host/hos_thread.c` 437 `0x825c6844` | mkw-recomp's 1.8.0 pacer and idle loop: one-engine.md rule 3's exceptions. The profile is 1.0.0, so **this code never runs** today | game's, dead: delete, or move to the mkw pack keyed to 1.8.0 if Lakitu wants 1.8.0 |
| `host/mkw_glue.c`, `nx2/rt/mkw_host.h`, `mkwvk`, `MkwVk*`, `mkw_vk_pipelines.bin` | engine names from MKW's origin | mis-named: the rename PR's (a)/(b) tables |
| `docs/mario-kart-world.md` (89), Games.cs row, `recomplauncher.json` rows, `tests/test_profile_vk_parallel.py` (126) | | to the pack (the test's MKW half) |
| Lakitu `fork/mario-kart-world` (c7803248, merged main at rc.28) | `docs/mario-kart-world.md`, the fork's harnesses; no game files of its own beyond main's | becomes the pack repo (section 5c) |

### Donkey Kong Bananza (`dkb`)

| Path | What | Verdict |
|---|---|---|
| `nx2/game_profile.py` `DKB` (~30 lines) | identity of 3.2.0, `video_catchup=False`, `graphics_scale=0` | game's (data) |
| `host/nn_applet.c` 406–440, `nn_video.c` 76–285, 553, 675, `nn_fs.c` 327, `nn_hid.c` 1383, `gpu/vk/vk_bank.cpp` 255, 274 | SDK 20 HLE (account, streamplay, video decoder forms), tagged `(dkb)` with dkb-pc-recomp credit | **general**: any game on SDK 20 calls them. Stays in NX2PC |
| `docs/donkey-kong-bananza.md` (118), Games.cs row, `recomplauncher.json` rows | | to the pack |
| `~/Projects/hookshot-dkb` (`fork/donkey-kong-bananza`, local only) | keeps only what is in review | nothing to carry; Bananza starts from the pack |

### Kirby Air Riders (`kar`, only in Dragoon)

| Path | What | Verdict |
|---|---|---|
| Dragoon `fork/kirby-air-riders` (78f22774) `nx2/game_profile.py` `KAR` (~40 lines, 217–258) | identity of 1.3.3, no patches, `npad_players="auto"`; `released` stays False until it plays | game's (data) |
| Dragoon's multi-pad layer (up to 8 npads, per-pad rumble), `ldn` refusal | general | to NX2PC as engine PRs before the cutover (one-engine.md rule 1) |
| `docs/kirby-air-riders.md` | | to the pack |

### What this says about the design

- The Python side is already nearly a pack: a profile is pure data, and patch modules are loaded by name in one place.
- The runtime is already compiled per game, so the C that is a game's can be compiled into that game's runtime only.
- The real work is **the shared renderer's menu** (OoT's Debug tab, Teleport and Inventory pages, items, texts) and
  **`host/hos_main.c`'s 1,170 OoT lines**. Both have to become pack content before any pack can leave the tree.
- Today MKW and DKB players see OoT's Debug tab, OoT's general rows that do nothing in their game, and get OoT's
  pipeline list copied into their folders. The pack split fixes these as a side effect (section 5a).

## 2. The pack format

### 2.1 Layout

In NX2PC's tree during the migration (`packs/<key>/`), and at the root of the pack's own repository afterwards:

```
pack.json                 the manifest (2.2): identity, profile data, what the pack holds
lift/                     Python the lifter imports: oot_patches.py, oot_wide.py, oot_fov.py, oot_jitter.py, oot_occlusion.py
host/                     C compiled into this game's runtime only: oot_*.c, oot_cheats.c (from hos_main.c), oot_control.c
menu/                     C compiled into this game's runtime: menu.c (the menu tables, 2.4), menu_i18n.h (their translations)
tools/                    Python the build or a person runs: prompt_assets.py and its faces, oot_saves.py
assets/                   data the build ships: pipeline_states/ (the tour and contributed lists), prompt_faces/
docs/                     the game's pages: readme.md (the player guide), saves.md, control.md (its verbs)
tests/                    game-free checks of this pack, run against an NX2PC checkout (section 3.5)
LICENSE                   GPL-3.0 (the same text as NX2PC's)
CREDITS.md                where the pack's code and measurements come from
```

A pack for a game with no patches yet (DKB, MKW, KAR) is just `pack.json`, `docs/`, `LICENSE`, `CREDITS.md`.

### 2.2 The manifest: `pack.json`

JSON, so the Builders, the launcher writer and `--library` read a pack without running any of its code.

```json
{
  "format": 1,
  "key": "oot",
  "name": "The Legend of Zelda: Ocarina of Time",
  "short_name": "Ocarina of Time",
  "version": "1.0.0",
  "api": {"major": 1, "minor": 0},
  "nx2pc": ">=3.0.0",
  "released": true,
  "program_id": "0400486024DEC000",
  "builds": [
    {"build_id": "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348", "version": 131072, "version_name": "version 131072",
     "main_base": "0x80020000"}
  ],
  "profile": {
    "gfx": {"frame_limiter": "0xfc", "nominal_fps": "0x2c8", "vrr_mode": "0x231", "vrr_gate": "0x232",
            "controller": "0x270", "controller_words": ["0x58", "0x28", "0x2c", "0x30"],
            "controller_rate_words": ["0x58", "0x28", "0x2c"]},
    "upscale": {"taau": ["fd029a317a9a4b94"], "gameplay": ["0xfd029a317a9a4b94", "0x9dc98871eb7fc101"],
                "network_keep": ["fd029a317a9a4b94", "9dc98871eb7fc101"]},
    "hooks": {"gfx_clock": "0x8348c770", "present_poll": "0x80590520", "present_events": ["0x805904a0", "0x80590530"]},
    "npad_players": 1, "account_users": 1, "video_catchup": true, "vk_parallel": null, "graphics_scale": null,
    "romfs_needed": [[["Pack/ZsDic.pack.zs"], "file"], [["System/Resource"], "dir"],
                     [["System/HeapSize/Product.Nin_Ounce_NVN2.HeapDef.byml.zs",
                       "System/HeapSize/Default.Nin_Ounce_NVN2.HeapDef.byml.zs"], "file"]]
  },
  "lift": {"patch_modules": ["oot_wide", "oot_fov", "oot_jitter", "oot_occlusion"], "code_patch_module": "oot_patches",
           "env_prefixes": ["OOT_"]},
  "host": ["host/*.c", "menu/menu.c"],
  "features": ["fov", "text_speed", "text_size", "camera_speed", "camera_centering", "mouse_camera", "game_speed",
               "skip_opening", "auto_run", "microphone", "ultrawide", "prompts", "damage_taken", "debug_tools"],
  "build_steps": ["prompts", "pipeline_states"],
  "saves_dir": "hookshot",
  "license": "GPL-3.0-or-later"
}
```

- `profile` holds every field of today's `Profile` except identity, as hex strings for addresses so a reviewer reads
  what the docs quote. `nx2/game_profile.py` keeps the `Profile` class and builds `GAMES` from the loaded packs, plus
  `NONE`. `emit_header` is unchanged, so `game_profile.h` is the same bytes.
- `builds` is a list because a pack may support more than one build (MKW 1.0.0 and 1.8.0 one day). Each build may
  later carry its own `profile` override; format 1 has one profile for all of them.
- `features` says which of the shared menu's general rows this game implements (2.4). A row whose feature no pack
  of the running game lists is hidden.
- `build_steps` names the optional steps of `make_game.py` the pack takes part in, each run from the pack's `tools/`
  or `assets/` (the prompts, the shipped pipeline list). A game without them skips the step, as MKW and DKB already do.
- There is no per-pack issues link. Decided (2026-11-22): the in-game bug report points to NX2PC's issues, and the
  report names the game (the pack's `key`, `name`, version and build id).
- `saves_dir` exists for Ocarina's legacy `hookshot` folder; without it the saves folder is `hookshot-<key>` (and
  the rename PR's `nx2pc-<key>`). The rename's migration keys on the same field.

### 2.3 What a pack may carry, and what it may not

| May carry | Run or read by | Notes |
|---|---|---|
| The profile data (`pack.json`) | everything | the only part read without running code |
| Python lift patch and code patch modules (`lift/`) | the lifter, inside the build | `install(lifter, img[, build_id])`, `patch_file`, `output_size_file`, `report`, as today |
| Python build tools (`tools/`) | `make_game.py`'s named `build_steps`, or a person | they may import NX2PC's `nx2/` modules |
| C sources (`host/`, `menu/`) | compiled into **that game's runtime only** (`hookshot` / `bin\hookshot.exe` and `hookshot_game`) | they include only `host/nx2_pack.h` and the headers it names (2.4) |
| Data the shared renderer reads at run time | handed over by the runtime (2.4) | menu rows, item and teleport tables, strings with translations |
| Data the build ships into the game folder (`assets/`) | `make_game.py` | pipeline lists, prompt layers; never anything made from a dump |
| Docs and tests | people, CI | not in the players' download (3.2) |

May **not** carry:

- **Anything compiled into the shared renderer or the engine**: no file under `gpu/`, no header the renderer
  includes, no change to `host/` files outside the pack. The renderer is built once for every game and the runtime's
  shared files once per engine release; a pack that needed them changed is an NX2PC pull request.
- **Game files or anything made from them**: CONTRIBUTING.md's rule applies to packs as to the engine. A pipeline list
  of hashes and draw state is allowed (as `assets/pipeline_states/README.md` already says); a texture, a shader or a
  lifted function is not.
- **Copies of engine code.** A pack that needs engine behaviour asks for it in NX2PC.
- **Downloads of its own.** A pack's code never fetches anything; NX2PC fetches packs (section 3).

### 2.4 How the shared renderer gets a game's data

Three channels exist today. The design keeps two and adds one C table:

1. **Environment set by the runtime** (`HOOKSHOT_PROFILE_*`, `host/hos_main.c` `game_profile_env`, read by
   `gpu/vk/vk_draw.cpp` `profile_keys`). Kept for the few scalars it carries (program keys, parallel replay). Not grown:
   strings and tables do not belong in the environment.
2. **Option ids** (`rx_option`, `rx_option_set`, `HOS_OPT_*`): the menu sends actions, the game side reports values.
   Kept. Game-owned ids move into a pack range, `HOS_OPT_GAME_BASE = 0x10000` and up, so `HOS_OPT_CHEAT_*` and
   `OOT_CHEAT_*` stop being shared numbers.
3. **New: a menu table the per-game runtime exports to the renderer.** `MkwVkHost` (`gpu/vk/vk_api.h`) gets one
   member, which bumps `MKWVK_API_VERSION`:

   ```c
   const NX2GameMenu *(*game_menu)(void);   /* the running game's menu content, or NULL: none */
   ```

   `NX2GameMenu` (in `vk_api.h`) starts with `uint32_t version, size` and holds: the `features` bit mask (2.2);
   help-text overrides for general rows by option id (so "Auto run" says "Link sprints" only in OoT); the game's
   tools tab (its title, groups, and rows of kind toggle / choice / action / list, each with its option id or action
   id); and lists for list pages, each with sections and cards (Teleport and Inventory are both sectioned card lists:
   names, notes, kinds, counts by option id); and translations, the same `{en, text}` entries per language code that
   `menu_i18n.h` holds, looked up before the renderer's own. The pack defines it in `menu/menu.c`, compiled into the
   runtime; `host/hos_main.c` passes `nx2_game_menu()` into the host table, and a game without one links
   `host/game_none.c`, which answers NULL.

Decided (2026-11-22): the menu content is this C table, compiled into each game's runtime and handed to the shared
renderer through the versioned interface (`MKWVK_API_VERSION`, `NX2GameMenu.version`). The renderer hides or ignores
every general row, and every RecompLauncher setting's effect, that the table's `features` does not list.
Why a C table and not a data file in the game folder: the runtime is rebuilt with every pack change anyway, so a data
file gains nothing and adds a parser, a file to lose, and a second copy of the teleport list (the runtime needs the
destinations too: today `oot_destinations[]` in `hos_main.c` and `teleport_names[]` in `vk_menu.cpp` are two copies of
one generated list; the table makes them one). The renderer only draws what the table says: its generic pages stay in
`vk_menu.cpp`, the OoT content leaves it.

The pack's C talks to the engine through one header, **`host/nx2_pack.h`**, which names the engine functions a pack
may call and their versions: guest memory (`recomp_load*`, `recomp_store*`, `hos_guest_range`), `hos_log`, options
(`rx_option`, `rx_option_set`), the control bridge's registration (the game members of `HosControlGame`), the
per-frame hooks the lifted patches call (`hos_text_scale`, `hos_camera_scale`, `hos_fixed_resolution`,
`hos_mouse_camera`, the prompt font server, `hos_zstd_stream`), and `NX2_PACK_API_MAJOR` / `_MINOR`. Code the OoT
patches emit calls only names in that header or in the pack's own C. A check in NX2PC holds the header to what the
pinned packs use.

## 3. How NX2PC fetches and trusts packs

### 3.1 The pin list

`packs.lock.json` at NX2PC's root, changed only by a reviewed pull request:

```json
{
  "format": 1,
  "default": "oot",
  "packs": [
    {"key": "oot", "repo": "notquiteog/Hookshot", "tag": "oot-v1.0.0", "asset": "oot-pack-1.0.0.zip",
     "size": 412345, "sha256": "<64 hex>"},
    {"key": "dkb", "repo": "notquiteog/Bananza", "tag": "dkb-v1.0.0", "asset": "dkb-pack-1.0.0.zip",
     "size": 9876, "sha256": "<64 hex>"},
    {"key": "mkw", "repo": "notquiteog/Lakitu", "tag": "mkw-v1.0.0", "asset": "mkw-pack-1.0.0.zip",
     "size": 9876, "sha256": "<64 hex>"}
  ]
}
```

- The order is the profile order (`--library`, the launcher's tiles). `default` is the game whose folder takes no
  suffix (today's `DEFAULT`, `setup.c`'s `HOOKSHOT_DEFAULT_GAME`, `Games.cs`'s `DefaultKey`, all read from here).
- Pack tags are `<key>-v<semver>`. This matters on `notquiteog/Hookshot`: old Builders pick the newest tag matching
  `^v\d+\.\d+\.\d+(-rc\.\d+)?$` (`Updater.cs` `ReleaseName`, `linux_update.py` `newest_release`), so a pack tag must
  never look like that, and a pack release is published with `--latest=false` (section 5d).
- During the migration an entry may say `"source": "tree"` instead of a release: the pack is NX2PC's own `packs/<key>/`.

### 3.2 Fetch, verify, cache: `nx2/packs.py`

One module, used by every path:

```
python -I nx2/packs.py sync [--key oot]       fetch every pinned pack missing from the cache, verify, unpack
python -I nx2/packs.py path oot               the unpacked pack's folder (what make_game and the lifter use)
python -I nx2/packs.py verify                 every cached pack against the lock (CI, the release)
python -I nx2/packs.py build --dir . --out dist/   (in a pack repo) the release zip: sorted names, fixed times, no tests/docs
python -I nx2/packs.py pin oot oot-v1.1.0     (in NX2PC) fetch that release, check it, write the lock entry
```

- **Download** from `https://github.com/<repo>/releases/download/<tag>/<asset>` only (the same https-only, API-fallback
  downloader as `builder/linux/linux_update.py` and `builder/setup/setup.c`). Never a URL from anywhere else.
- **Verify** the size, then the SHA-256 against the lock, **before** unpacking. The lock is the trust: GitHub, the
  network and the cache are not. Unpack refuses absolute paths, `..`, links and anything outside the pack's listed
  folders, then checks that `pack.json`'s `key` and `version` match the lock entry.
- **Cache** at `<Builder home>/packs/<key>/<version>-<sha8>/` (the source checkout's is `work/packs/`), never changed in
  place: a new pin is a new folder. The last three versions of each key are kept.
- **Developers** use a pack checkout instead with `NX2PC_PACK_DIR_<KEY>=<path>` (read by `nx2/packs.py path`), which
  every build log and `hookshot_version.txt` then name as "pack from a local folder, not pinned", so such a build is
  never mistaken for a release one.

### 3.3 What players' installs do

Players never run `nx2/packs.py sync` themselves. **The release carries the pinned packs as one more update part.**
NX2PC's release workflow syncs and verifies the lock, checks each pack release's build attestation
(`gh attestation verify <zip> -R <pack repo>`), and puts the unpacked packs, without `tests/` and `docs/`, into a
`packs` part beside the tools part (`HookshotBuilder-packs-<sha>.zip` in `hookshot-setup.txt`; the same in
`hookshot-linux.txt`; inside the AppImage; inside the RecompLauncher zip). So:

- **Fetch at install and update** goes through the existing parts mechanism: each part is checked by its SHA-256, and
  a part that did not change is not downloaded again. A pin change downloads the small `packs` part only.
- **Old setups understand it**: it is an ordinary part, so the bridge's list (5d) needs nothing new.
- **Offline**: everything needed to build is in the installed version; nothing is fetched at build time.
- **Rollback**: the Builders keep earlier versions (`versions/<tag>`); each carries its own `packs` part, so going
  back to a Builder version goes back to the packs it was released with. A pack can never be newer than its engine.
- **A pack-only update** (OoT's patches fixed, engine unchanged) is a pin PR in NX2PC and a patch release:
  `fix(packs): oot 1.0.1 (...)`. Decided (2026-11-22): pins change only with an NX2PC release, never between releases. Pack releases and
  pin changes are covered by the standing OK to merge and release.

What each command does with packs (all of them resolve the game's pack with `nx2/packs.py path <key>`, from the
installed `packs` part, or the source checkout's cache):

| Command | With packs |
|---|---|
| `make_game.py --prepare` (RecompLauncher) | each `--game-<key>` copy is checked against that pack's `builds`; a copy no pinned pack knows is refused as today |
| `make_game.py --update <folder>` | the folder's game's pack; the folder records the pack it was built with (below) |
| `make_game.py --update-all [<base>]` (#130) | per game, as #130 does; a game whose pack is refused (5.4) is reported `failed` and the others go on |
| `make_game.py --base <base>` (#130) | the copy's build id picks the pack, the pack's key picks the folder |
| `game_folders.py --library --json` (#130) | adds `pack: {version, repo, sha256}` per game and `pack_built` (what the folder was built with); `update_due` also when they differ |

`hookshot_version.txt` (and its rename) gains one line, `pack <key> <version> <sha256>`, so `--library` and a bug
report say which pack a folder was built from.

**The lift cache key** (`make_game.py` `lift_key` / `lift_sources`) takes **the active game's** pack only (its key,
version and file hashes), not every profile's modules as now. An MKW pack update then no longer translates Ocarina
again. Each pack file is hashed under the name it had in `nx2/` (`nx2/oot_patches.py`), so moving the files into
`packs/oot/lift/` does not change Ocarina's key, and no player relifts for the move itself.

### 3.4 CI in NX2PC

- **Engine checks** (`python -I -B tests/run_all.py`) run on a synthetic fixture pack in `tests/fixtures/packs/example/`
  (a made-up build id, one lift module, one host file, one menu table) wherever a check needs "a game", so the engine
  suite does not depend on any real pack. `tests/test_game_profile.py`'s header check uses the fixture.
- **Pinned-pack checks**: a job runs `nx2/packs.py sync && nx2/packs.py verify`, then each pinned pack's own `tests/`
  against this checkout (`run_all.py --pack <key>`, which puts the pack's folder where `nx2/packs.py path` finds it).
  An engine PR that breaks a pinned pack fails here, before release.
- **A pin PR** (`chore(packs): pin oot oot-v1.1.0`) runs the same, plus the attestation check, plus
  `nx2/packs.py pin --check` (the lock entry is what the release holds). Only notquiteog pack repositories can be
  pinned (a list in `nx2/packs.py`, held by a test).
- No game in CI, as now: lifting and running stay local, reported in the PR.

### 3.5 CI in a pack repository

- On every PR: check out NX2PC at the pack's lowest supported release (the `nx2pc` range's floor tag) and at NX2PC
  `main`, set `NX2PC_PACK_DIR_<KEY>=$GITHUB_WORKSPACE`, run `run_all.py --pack <key>` in each. The floor must pass;
  `main` failing is reported as "NX2PC main breaks this pack" (it tells the pack what the next engine release needs).
- On a tag `<key>-v*`: `nx2/packs.py build`, upload the zip and its `.sha256` to that tag's release with
  `--latest=false`, and attest it (`actions/attest-build-provenance`, as NX2PC's releases do).
- release-please per pack repo (`release-type: simple`, tag prefix `<key>-`), so a pack's version moves with its
  commits as the engine's does.
- Then a pin PR in NX2PC (by hand or by an agent) brings the release in.

### 3.6 Licensing and credits

Both sides are GPL-3.0-or-later (NX2PC's `LICENSE`, the same text in each pack). A pack's C is linked into the game's
runtime and its Python runs inside the GPL lifter, so a pack must be GPL-3.0-compatible; CONTRIBUTING.md's licensing
section applies to pack repositories unchanged. Each pack keeps a `CREDITS.md` with what its code and measurements came
from, moved from the comments that say so today: OoT: Longshot (MagicBeanDev) and EvilOOT; MKW: mkw-recomp
(adriadam10, MIT; the 1.0.0 identity row); DKB: dkb-pc-recomp (Lucas-blip-png, GPL-3.0-or-later). NX2PC keeps the
engine's credits (yuzu, suyu, Eden, a64-kit, TOTKPort, mkw-recomp) and the rename inventory's `docs/credits.md`. A
pack zip carries `LICENSE` and `CREDITS.md`.

## 4. The engine/pack interface and its versions

Two numbers, for two kinds of need:

- **`api`: the contract.** NX2PC defines `NX2PC_PACK_API = (major, minor)` in `nx2/packs.py` and the same as
  `NX2_PACK_API_MAJOR` / `_MINOR` in `host/nx2_pack.h`. The contract is: `pack.json`'s format and fields; the lift
  hooks' signatures and what they receive (`install(lifter, img[, build_id])`, `patch_file`, `output_size_file`,
  `report`); `host/nx2_pack.h`; `NX2GameMenu`'s layout; the option id range; `build_steps`' calling convention. Adding
  to any of these is a minor bump; removing, renaming or changing one is a major bump. A pack says
  `"api": {"major": M, "minor": n}`: it works with an engine whose major is M and whose minor is n or more.
- **`nx2pc`: a release range**, for a pack that needs an engine fix rather than a new interface (`">=3.2.0"`). Optional.

The lifter's `lifter` and `img` objects are wide (the OoT modules reach into them freely), so the contract is pinned
in practice by the pinned-pack job (3.4), not by the header alone. During the migration the API is `0.x`, where any
minor may break: it becomes `1.0` with the cutover.

On a mismatch:

| Where | What happens |
|---|---|
| A pin PR in NX2PC | fails: "oot-v1.1.0 needs pack API 1.3; this engine has 1.2" (or a release range that excludes the next release) |
| `make_game.py` (any mode) | that game is refused before anything in its folder is touched: "This NX2PC version cannot build Ocarina of Time's pack 1.1.0 (it needs NX2PC 3.2 or later): update the Builder." With `--update-all`, that game is `failed` and the others go on (#130) |
| The Builders and `--library` | the game shows "needs a newer NX2PC" from the JSON (`pack_ok: false`, `pack_needs`), and Install/Update is off for it |
| Compiling the pack's C | `host/nx2_pack.h` has `#if` checks against the pack's declared major, so a mismatched source tree stops with a line naming both |
| The running game | the renderer ignores an `NX2GameMenu` of another major or a smaller `size` than it needs (the game's rows hidden, a log line), and keeps running; `MKWVK_API_VERSION` already refuses a renderer of another version |

## 5. Migration and order

Each step is one pull request unless it says otherwise, tested as AGENTS.md asks (checks, then a run in the game where
the change shows). Until step (c3), `notquiteog/Hookshot` `main` stays the trunk and NX2PC `main` is a fast-forward
copy of it (`git push nx2pc origin/main:main` after each merge), so there is never a second trunk.

### (a) In-tree packs, no behaviour change for Ocarina

"No behaviour change" is measured: an Ocarina build from `scripts/dev_packs.py base` at `main` and at the PR gives the
same lift key (no relift), the same lifted C (one hash over `work/sa/gen`), the same runtime objects but for embedded
source paths (compared with `-ffile-prefix-map`), and for a1 to a3 the same renderer. a4 changes the renderer by
design and is checked by menu screenshots instead.

| PR | What | Needs |
|---|---|---|
| a1 | `nx2/packs.py` (load only: `packs/*/pack.json` in the tree), `packs/{oot,dkb,mkw}/pack.json`, `nx2/game_profile.py` builds `GAMES` from them; `emit_header` unchanged; `game/game_profile.h` unchanged; `Games.cs` and `setup.c` defaults tested against the packs | — |
| a2 | OoT's Python into `packs/oot/lift/` and `packs/oot/tools/` (`oot_*.py`, `prompt_*`, `prompt_faces/`, `scripts/oot_saves.py`); `load_module` looks in the active pack's `lift/`; `lift_sources` hashes pack files under their old `nx2/` names; `OOT_` env prefix from `lift.env_prefixes`; the prompts step and `ship_state_list` run only as the pack's `build_steps`; `assets/pipeline_states/` → `packs/oot/assets/` (MKW and DKB stop getting OoT's list: a fix) | a1 |
| a3 | OoT's runtime C into `packs/oot/host/`: the `host/oot_*` files, `hos_main.c` 666–1836 split into `oot_cheats.c`, `oot_teleport.c`, `oot_opening.c`, `oot_wide_cover.c`; the control bridge's OoT members registered by the pack; `host/nx2_pack.h`; `host/game_none.c`; `nx2/build.py` compiles a pack's `host` list into that game's runtime only. Also rule 3's MKW 1.8.0 literals (`hos_game_is_mkw`, `0x806bce28`, `0x825c6844`, `0x837de940`, `HOS_MKW_INTERVAL`): decided (2026-11-22), moved into `packs/mkw/host/` behind the mkw profile's build check, kept for a later 1.8.0 profile; shared code then holds none | a2 |
| a4 | The renderer's menu reads `NX2GameMenu`: `oot_cheat_items.h`, the teleport list, the Debug tab's tools, the OoT texts and their translations into `packs/oot/menu/`; general rows hidden unless the game lists their feature; option ids into the pack range; `MKWVK_API_VERSION` +1; the renderer compiled with the empty profile's header. The 17 checks that cut `vk_menu.cpp` move to the pack's tests or follow the new functions in the same PR | a3 |
| a5 | Tests and docs follow: the pure-OoT tests into `packs/oot/tests/`, `run_all.py --pack`; `docs/mario-kart-world.md`, `donkey-kong-bananza.md` into their packs; readme, `control.md`, `saves.md` split; a Game column in `docs/tested-configurations.md` (existing rows filled from their text); the bug report names the game; `tests/fixtures/packs/example/` | a4 |

After a4, Ocarina's menu must look and act the same (each tab and page captured with `RX_VK_MENU_SCRIPT` before and
after, OoT in the game), and MKW's and DKB's must lose exactly the Debug tab and the rows their game does not
implement (a run of each to the first menu; the GPU lock's schedule applies to every run).

KAR joins here too: the Dragoon session adds `packs/kar/pack.json` (`released: false`) in a PR to Hookshot `main`,
after a1, and its multi-pad layer as separate engine PRs.

### (b) The fetcher and the pin list

| PR | What | Needs |
|---|---|---|
| b1 | `packs.lock.json` with `"source": "tree"` entries; `nx2/packs.py sync / path / verify / build / pin`; the cache; `NX2PC_PACK_DIR_<KEY>`; the downloader shared with `linux_update.py`; tests with a fixture pack served from a local folder (`file://` only in tests) | a5 |
| b2 | `make_game.py` and `game_folders.py` resolve packs through `nx2/packs.py path`; the folder's `pack` line; `--library`'s `pack`, `pack_built`, `pack_ok`; refusals of 5.4 | b1 |
| b3 | The release workflows build the `packs` part (Windows setup list, Linux list, AppImage, RecompLauncher zip, full setup) from the lock; `scripts/release_files.sh check` holds it there | b2 |

### (c) The cutover and the packs moving out

| Step | What | Needs, and the user's OK |
|---|---|---|
| c0 | Engine changes still only in forks go to `main` first: `git diff <main>...fork/mario-kart-world -- . ':!packs' ':!docs/mario-kart-world.md'` and the same for Dragoon list them; each becomes an engine PR or is dropped. Open engine PRs (#123) merged or moved (5f) | a5 |
| c1 | **NX2PC becomes the trunk.** One PR in NX2PC: the default repository `notquiteog/NX2PC` in `nx2/version.py`, `builder/setup/setup.c` `HOOKSHOT_REPOSITORY`, `Repository.cs`, `builder/py/hookshot_builder/__init__.py`, `linux_update.py`, `scripts/linux/appimage.sh`, the MSI, `tests/test_repository_default.py`; release-please moved to NX2PC; the bridge job (5d). Hookshot `main` frozen (branch protection) | b3; **OK**: freezing Hookshot `main`, a token in NX2PC that can write Hookshot's releases. The first tag is decided: NX2PC `v3.0.0` |
| c2 | NX2PC's first release and the Hookshot bridge release (5d), tested from rc.40 clients on Windows and Linux before it is marked latest | c1 |
| c3 | **DKB → `notquiteog/Bananza`** (empty, so the simplest; it proves the flow): `git clone NX2PC`, `git filter-repo --subdirectory-filter packs/dkb` (keeps the history of those files), push as `main`; CI and release-please; release `dkb-v1.0.0`; pin PR in NX2PC that removes `packs/dkb/` and pins the release; the lift key and the lifted C the same as from the tree | c2; **OK**: Bananza's settings (issues, default branch `main`) |
| c4 | **MKW → `notquiteog/Lakitu`**, keeping its history: on a new branch `pack` from `fork/mario-kart-world`, merge NX2PC `main` (which has `packs/mkw/`), then one commit that removes everything but `packs/mkw/` and moves it to the root ("Lakitu becomes Mario Kart World's game pack"). Then `git merge -s ours origin/main` on `pack`, so `main` fast-forwards to it: `git push origin pack:main`. No force-push, no default-branch change; `fork/mario-kart-world` and the old `main` stay in the history and as a tag `engine-fork-final`. Release `mkw-v1.0.0`, pin PR | c2; decided: the removal commit on `main` (history kept, no force-push); **the user is told before it lands**; deleting or archiving its `fork/*` branch later is asked separately |
| c5 | **KAR → `notquiteog/Dragoon`**, the same as c4 from `fork/kirby-air-riders` | c2; as c4: **the user is told before it lands** |
| c6 | **OoT → `notquiteog/Hookshot`**, last, because Hookshot is the repository old clients read. Hookshot `main` is NX2PC `main` up to c1, so one commit on Hookshot `main` removes everything but `packs/oot/`, moves it to the root, and keeps `recomplauncher.json` at the root (5d) and a readme that says where NX2PC is. History, blame and old links stay. Release `oot-v1.0.0` with `--latest=false`; pin PR in NX2PC | c2; as c4: **the user is told before it lands** |

Decided (2026-11-22): Hookshot, Lakitu and Dragoon each get a removal commit on `main`, history kept, no force-push,
no default-branch change, and the user is told before each one lands. (Rejected: a new orphan default branch per
repository, which would change default branches and leave old links to `main` pointing at the engine.)

After c6, a fork's own branches are history. Issues: game bugs stay where they are (Hookshot's issues are mostly
Ocarina's); engine issues are transferred to NX2PC with `gh issue transfer` (with the user's OK, one batch).

### (d) The bridge on `notquiteog/Hookshot`, and RecompLauncher

What old clients ask Hookshot for (`docs/versioning.md`, `Updater.cs`, `setup.c`, `linux_update.py`, `appimage.sh`):

| Old client | Asks for |
|---|---|
| Windows Builder (`Updater.cs`) | `releases/latest` (today v2.2.0) or, on the rc track, the newest `v*` in `/releases`; assets `HookshotBuilder.exe` and `hookshot-setup.txt`; the nightly track `releases/download/nightly/hookshot-setup.txt` |
| A kept `HookshotBuilder.exe` (setup) | `releases/latest/download/hookshot-setup.txt` (or `.../download/<tag>/`), then the parts that list names |
| AppImage self-update (`linux_update.py`) | `hookshot-linux.txt` on latest, the newest rc, or `nightly` |
| AppImageUpdate, Gear Lever | `gh-releases-zsync|notquiteog|Hookshot|latest|Hookshot-*-x86_64.AppImage.zsync` (`rc` and `nightly` in place of `latest`) |
| A game folder's own update check (`nx2/version.py`) | the latest release of the repository in its `repository.txt` |
| RecompLauncher | the repository in its catalog, `recomplauncher.json`, the `Hookshot-v<ver>-Windows-x64.zip` asset |

The bridge is **NX2PC's first release's files under their old names, published on Hookshot** by NX2PC's release job
(c1). Because the setup lists and Linux lists name each part by its full URL, the bridge files point at NX2PC's
`builder-parts` and `linux-parts` releases and copy nothing large:

- A release **`v3.0.0`** on Hookshot (decided 2026-11-22: NX2PC's first tag; newer in semver than every v2.3.0-rc.N
  and v2.2.0, so every track offers it), marked **latest**, not a prerelease, with: `HookshotBuilder.exe` (NX2PC's setup,
  built with `HOOKSHOT_REPOSITORY "notquiteog/NX2PC"`), `hookshot-setup.txt` and `hookshot-linux.txt` (NX2PC's own
  lists, whose parts are on NX2PC), `Hookshot-v<ver>-x86_64.AppImage.zsync` whose URL is NX2PC's AppImage,
  `Hookshot-v<ver>-Windows-x64.zip` (for RecompLauncher), `SHA256SUMS`, and notes that say "Hookshot is now NX2PC;
  this release installs it; your games, saves and settings stay where they are".
- The rolling **`rc`** and **`nightly`** releases on Hookshot get the same bridge files once, so clients on those tracks
  move too.
- Once installed, the new Builder's `repository.txt` names NX2PC and it never asks Hookshot again. The bridge is
  refreshed only if NX2PC ever prunes a part it names (a check in `release_files.sh`), and later by the rename PR
  (5e). It is not a mirror of every release.
- **Open check 2, before c2**: that AppImageUpdate follows a `.zsync` whose URL is on another repository (else the bridge
  also carries the 250 MB AppImage, once); and the whole matrix by installing from rc.40 and updating: the Windows
  Builder on each track, a kept setup, the AppImage's own updater, AppImageUpdate, Gear Lever, RecompLauncher.

**RecompLauncher (open check 1, before c2)**: how it keys an install (the catalog's repository, the entry's `name`, or an id) is not known: the
rename plan already lists it as "to check in its source before starting", and no note on this PC answers it. Until it
is known:

- RecompLauncher keeps reading **`notquiteog/Hookshot`**: Hookshot's `main` keeps `recomplauncher.json` at the root
  after c6 (copied from NX2PC by the bridge job), and its `repo` field stays `notquiteog/Hookshot`. NX2PC's release job
  also publishes the `Hookshot-v<ver>-Windows-x64.zip` on Hookshot **for every release** for as long as this lasts
  (the one asset that is mirrored; small).
- Then, with RecompLauncher's maintainers, move the catalog entry to `notquiteog/NX2PC` in a way that keeps existing
  installs as the same entry (same id), switch `repo`, and stop the mirror. The `games[]` ids `oot`, `dkb`, `mkw` (and
  later `kar`) never change.

### (e) Lining up with PR 2 and the rename PR

- **#130** (merged) is the core the packs build on: b2 adds fields to its JSON and its refusals; nothing in #130 is
  replaced.
- **PR 2 (the Builders' game library)** touches `builder/` only. To not do it twice: its game list should come from
  `game_folders.py --library --json` rather than `Games.cs`'s `Profiles` table or new hard-coded rows, and its game
  headings should replace the "Ocarina of Time" strings; b2 then only adds a pack version to the row. The packs work
  does not touch the Builders' UI.
- **The rename PR** (Hookshot → NX2PC names): its steps 1–3 (readers, migrations, writers) are independent of the
  packs and can land before or after (a). Its token script (`scripts/rename_nx2.py`) must include `packs/` and run
  again on each pack repository; packs use only `host/nx2_pack.h`'s names, so they are barely touched. Its plan
  assumed a GitHub rename with a redirect, which is no longer the plan, so its **step 4 (release assets under both
  names) and the repository default move into c1/c2 here, done once**: NX2PC releases publish the old names for the
  rename's transition, and the Hookshot bridge is those same old-named files. Its `saves_dir` migration keys on the
  pack's `saves_dir`.
- Recommended order: a1–a5 → rename steps 1–3 (any time) → b1–b3 → c0–c2 (with the rename's step 4) → c3–c6.

### (f) Open PRs and the sessions after the cutover

- **#123** (B2, Longshot's renderer performance picks): an engine change. Merge it on Hookshot before c1 if its A/B is
  done; if it is still open at c1, push its branch to NX2PC (`git push nx2pc perf/longshot-vk-b2`), open the same PR
  there (`gh pr create -R notquiteog/NX2PC --head perf/longshot-vk-b2`, the body carried over), and close #123 with a
  link. **#131** (release-please rc.41) is merged or closed before c1; NX2PC's release-please takes over from there.
- **After c1**: engine work (renderer, runtime, HLE, lifter, builder, CI) is a PR to NX2PC `main`. A game's work is a PR
  to its pack repository. A game that needs a new engine hook is two PRs: NX2PC (the hook, an API minor bump), then the
  pack (its `api.minor` raised), pinned after the engine release.
- **The sessions**: Ocarina's → `notquiteog/Hookshot` (pack) and NX2PC; Lakitu's → Lakitu (pack) and NX2PC; Dragoon's →
  Dragoon (pack) and NX2PC (the multi-pad layer first); Bananza's → Bananza (pack) and NX2PC. The local fork checkouts
  (`~/Projects/hookshot-mkw`, `hookshot-kar`, `hookshot-dkb`) are retired after c3–c5.
- **Longshot pulls** (`docs/longshot.md`): each Longshot commit is split by what it touches: engine files → an NX2PC PR,
  Ocarina's patches and menu content → a Hookshot pack PR, both crediting the commit.
- **Team tools**: `scripts/test_requests.py` and the relay assume `notquiteog/Hookshot` PR numbers; they get a `--repo`
  (default NX2PC) and the relay watches all five repositories' `testing wanted` labels. `AGENTS.md`, `CONTRIBUTING.md`,
  `docs/one-engine.md` and `docs/workflow.md` are rewritten for NX2PC in c1, and each pack repository gets a short
  `AGENTS.md` that points at NX2PC's.

## 6. Risks, decisions and open checks

Risks:

- **Packs are code.** A pack's Python runs in the build and its C runs in the game, with the player's rights. The pin
  PR is the review; only notquiteog repositories can be pinned, and there are no third-party packs.
- **The lift interface is wide.** The OoT modules use the lifter's internals freely. The pinned-pack CI job catches
  breaks; expect a few major bumps early (the API stays `0.x` until c1).
- **a3 and a4 are large** (`hos_main.c` and `vk_menu.cpp`) and conflict with anything in flight there, the rename's
  token pass included. Land them in quiet windows, and land the rename's mechanical pass before or after them, never
  in between.
- **Old-client paths are tested once and then trusted.** If the zsync or RecompLauncher checks fail, the bridge carries
  more (the AppImage, a per-release zip mirror).
- A pack move that changed the lift key would cost every player a full relift (about 45 minutes); 3.3's name mapping
  avoids it, and a1–a5 check it.

Decided (2026-11-22):

1. **The first tag** is NX2PC `v3.0.0`, and so the Hookshot bridge's (5d).
2. **History:** a removal commit on `main` for Hookshot, Lakitu and Dragoon, history kept, no force-push, no
   default-branch change. The user is told before each one lands (c4, c5, c6).
3. **Menu content:** a C table compiled into each game's runtime, handed to the shared renderer through the versioned
   interface (2.4).
4. **Pack releases and pin changes** are covered by the standing OK to merge and release. Pins change only with an
   NX2PC release, never between releases (3.3).
5. **The bug report** points to NX2PC's issues, with the game named in the report (2.2).
6. **RecompLauncher's release-wide settings** stay. The engine hides or ignores the rows a game does not implement,
   from the menu table's implemented-rows list (`features`, 2.4).
7. **Tested configurations:** one table in NX2PC with a Game column (a5 adds the column).
8. **The dead MKW 1.8.0 code** moves to the mkw pack, behind its profile, for a later 1.8.0 profile (a3).

Open checks (both before c2, the bridge release):

1. **RecompLauncher's install key**: whether it keys an install by the catalog's repository, the entry's `name`, or an
   id. Until it is known, RecompLauncher keeps reading `notquiteog/Hookshot` (5d).
2. **AppImageUpdate and the `.zsync`**: whether AppImageUpdate (and Gear Lever) follow a `.zsync` whose URL is on
   another repository, and what file name they save. If not, the bridge also carries the AppImage, once (5d).
