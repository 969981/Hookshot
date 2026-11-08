# ROADMAP — save editor

Status: **Phase 1 complete; Phase 2 schema complete, field names partial.**
The container is decoded, the raw reader/writer and CLI work, an edited copy
was loaded and saved in the current build, and the game's own GameDataList is
decoded into types/defaults/enums (~12,019 saved fields, 123 enum tables).
Most field names are not in the GameDataList, so naming remains a curation
task. A GUI and the optional in-game page are not done yet.

## Phase 0 — decode the container (done)

- Located the saves: `user/save/<account>/slot_NN/{progress,caption}.sav`,
  plus `account.sav` and `option.sav`.
- Confirmed the GameData container: magic `0x01020304`, revision from
  `GameDataList.Product.110.revision.txt`, typed sections, offsets.
- Confirmed the hash: MurmurHash3 x86 32-bit, seed 0, of the dotted path.
- Confirmed the format is the same publicly documented one used by TOTK
  (`TotkMods/Research/Formats/Sav` and the `sav.hexpat` viewer).
- Found no per-file checksum/signature. The trailing `Game` value is a
  `Bool64bitKey` list.

## Phase 1 — raw parser and CLI (this folder)

- [x] `savefile.py`: header, sections, all section types, `Bool64bitKey`.
- [x] In-place scalar and fixed-string edits; atomic save.
- [x] CLI: `list`, `check`, `dump`, `get`, `set`, `hash`, `scan`, `names`,
  `backup`.
- [x] Synthetic fixture and end-to-end tests.
- [x] Optional real-save round-trip test.
- [x] Controlled edit against a copy of a real save (automated; the real file
  is checked unchanged afterwards).
- [x] Test `option.sav` and `account.sav` layouts specifically, including the
  all-empty-section shape.
- [x] Add `--schema`/`--names` for a hand-curated table, plus a `names`/
  `schema` command that generates a starter table, merges an existing one,
  and can fill names from a game-binary scan. Auto-discovered beside the save,
  beside the account, or in this folder.
- [x] Read-only diagnostics: `compare.py` (file/slot/account diff),
  `schema_report.py` (name/type/version coverage), and `acceptance.py`
  (parse/round-trip/preview/pair check), each with tests.
- [x] **Manual in-game load (2026-11-08)**: a copy of `slot_00` in a fresh
  `user` tree had `progress.sav` `0x1D6189DA` changed from `HyruleField_Child`
  to `KakarikoVillage_Child` and the matching `caption.sav` strings/vector
  changed to Kakariko. Hookshot's Load screen showed the edited slot as
  `Kakariko Village`, and loading it spawned in Kakariko Village. The game
  accepts the edited files.
- [x] **In-game save persistence (2026-11-08)**: after loading the edited
  slot, moving and saving, the game wrote **`slot_05`** (not `slot_00`):
  `progress.sav` 123,988 B and `caption.sav` 42,217 B, latest save at 12:34:28
  (the 12:25 save had the same state). Both parse, round-trip byte-for-byte,
  and agree: scene
  `KakarikoVillage_Child` / `KakarikoVillage`, position
  `(-80.14, 96.76, 39.20)`. This is expected: **the game cannot overwrite a
  chosen save in-game.** It writes a new save into a slot of its own choosing
  (observed: the oldest, `slot_05`) and leaves the loaded slot untouched.
  Save writing works.

## Phase 2 — names and schema

- [x] Decode `romfs/GameData/GameDataList.Product.110.byml.zs`. The zstd
  dictionary id 1 ships in `romfs/Pack/ZsDic.pack.zs` (a plain zstd frame
  around a SARC with four 128 KiB dictionaries). `extract_gamedata.py`
  extracts dictionary 1 and writes the 1,462,888-byte BYML.
- [x] Parse BYML v7 in stdlib code (`byml.py`, both `YB`/`BY` byte orders;
  64-bit values, maps, arrays, hash maps, strings, binaries, nulls).
- [x] Build the save schema (`gamedata.py`) from `RootNode.Data` and
  `MetaData`: hash, type, `DefaultValue`, `ResetTypeValue`, `SaveFileIndex`,
  `SaveFileDirectory`, array size, and `Values`/`RawValues` enum labels.
  Product.110 yields ~12,019 saved fields and 123 enum tables; a save has
  **0 type and 0 version mismatches** against it.
- [x] Preserve the richer fields in `FieldInfo`/`load_schema` and in the
  `names` output, so `--merge` keeps them.
- [x] Merge `scan` results: `gamedata.py --scan-game main` names the printable
  paths; the current build yields 12. `--merge` keeps a curated table.
- [x] Enum labels are populated from `RawValues`; `dump`/`get` show them, e.g.
  `0xB80C70A8 ... 0xBE69DE49 (Empty)`.

### Phase 2 remaining — field names

Status: **schema complete; names blocked on a source**. The GameDataList has
12,019 saved hashes with types/defaults/enums, but no `HashText` or name
strings. Only about 12 full OoT paths are printable in the build. The public
TotK `GameDataListFull.csv` gives 214 OoT hash matches, of which **207 validate**
with `murmur3_32(name) == hash` (about 1.8% of the schema). That includes:

```
PlayerStatus.Life                 0x31AB5580
PlayerStatus.MaxLife              0xFBE01DA1
PlayerStatus.CurrentRupee         0xA77921D7
PlayerStatus.SavePos              0xC884818D
World_PlayerPos                   0x8773F02D
Sequence_CurrentBanc              0x1D6189DA
CaptionData.LocationName          0xF74E0E8E
CaptionData.Pos                   0x8B26618E
CaptionData.ScreenShot            0x63696A32
OptionData.CameraRotateSpeed      0x128D0120
```

Exact names for *most* hashes cannot be recovered from the retail build. Pick
one of two completion criteria:

- **A. Practical core coverage (recommended):** merge the 207 validated TotK
  paths and the 12 OoT paths, then add semantic labels for OoT-specific
  player, caption, option, sequence and inventory/equipment fields through
  controlled save diffs. Leave the remaining event flags hash-addressed but
  fully typed.
- **B. Literal “most hashes” (>50%):** requires an external exact-name source:
  the `.nup` package (check its TOC for `.nss`/symbols/debug metadata), a
  debug/development build, a symbol dump, or a community OoT Switch 2 name
  table. Without one, do not claim >50%.

**Chosen: Option A.** The import, coverage and diff tooling is in place; the
remaining work is OoT-specific curation and in-game verification.

#### Option A work

- [x] Add `import_names.py`: read `schema.json`, TotK CSV/WordList, the OoT
  binary scan and a curated overlay; validate every exact name with Murmur3;
  record provenance (`totk`, `oot-scan`, `curated`, `semantic`); reject the
  seven bad CSV rows. Priority: curated > OoT scan > TotK.
  Validation against Product.110: **219 exact names** (207 TotK + 12 OoT),
  plus semantic overlay entries.
- [x] Extend `schema_report.py` with `--by-type`, `--by-save-file`,
  `--named-only`, provenance counts and a critical-field checklist
  (`PlayerStatus.Life/MaxLife/CurrentRupee/SavePos`, `World_PlayerPos`,
  `CaptionData.LocationName/Pos/ScreenShot`, `Sequence_CurrentBanc`,
  `OptionData.*`). The current real-tree report has 0 type/version mismatches
  and every critical field `OK`.
- [x] Add `label_diff.py BEFORE.sav AFTER.sav --label "..." --names names.json`:
  use the `compare` machinery, list changed hashes and append semantic labels
  with provenance and a note; `--hash`, `--name`, `--force`, `--dry-run`.
- [ ] Curate OoT-specific inventory, equipment, song, medallion, stone,
  skulltula and trial fields from controlled saves: rupee, heart, item pickup,
  equipment, song, chest and scene/save-point changes. `0x0CCCC6CD` is the first
  semantic entry (`CaptionData.SpawnName`).
- [ ] Verify the curated fields in game: edit, load, observe the HUD/Load
  screen, save and reload.
- [x] Update `README.md` and `AGENTS.md` with the import/labelling workflow.
  Generated names and schemas stay in `%TEMP%` or the user's save folder and
  are never committed.

Acceptance for A: 100% of the core `PlayerStatus`, `CaptionData`, `OptionData`
and `Sequence` fields that exist have an exact or semantic name; every exact
name satisfies `murmur3_32(name) == hash`; `schema_report --strict` reports
0 type/version mismatches; `acceptance --pair` passes; `dump`/`get` show names
and enum labels; `set` accepts them. The coverage report states exact,
semantic and unnamed counts.

Estimated effort: 2–4 days for `import_names`/coverage/`label_diff` tooling,
plus 1–2 weeks for careful in-game curation.

#### Option B work

- [ ] Reconnect/provide the `.nup` and inspect its TOC for a `.nss`/symbol
  file, debug metadata or a `GameDataList` JSON with `HashText`.
- [ ] If none, scan all romfs printable strings in `.ainb`, `.bgyml`, `.pack`
  and `.byml.zs` payloads; hash and match the 12,019 OoT hashes, parallelised
  by directory.
- [ ] If coverage is still low, run a bounded component search: components
  from TotK CSV/WordList, OoT strings and `main` identifiers; prefixes such as
  `PlayerStatus`, `CaptionData`, `OptionData`, `Sequence`, `World`,
  `HavePlayedEvent`, `GameData`; combinations up to depth 4, every candidate
  validated by Murmur3.
- [ ] Only after an exact source exists, set a >50% coverage target and add
  the resulting names to the schema.

Estimated effort: 1–3 days if an exact source exists; open-ended otherwise.

## Phase 3 — useful fields

- [ ] Identify and validate the OoT remake's player fields: hearts, magic,
  rupees, ammo, equipment, inventory, songs, medallions/stones, warp songs,
  skulltulas, trials.
- [ ] Identify scene/event flags and the `Bool64bitKey` sets.
- [ ] Keep `caption.sav` in sync when position/scene changes.
- [ ] Add friendly commands (e.g. `set-hearts`, `set-rupees`) backed by the
  schema, not hard-coded offsets.
- [ ] Validate ranges and cross-field invariants (e.g. current health <= max
  health) where the game is known to care.

## Phase 4 — GUI

- [ ] Choose a front end:
  - WPF window in `builder/HookshotBuilder` (self-contained Windows app, but
    duplicates the format logic in C#), or
  - local web UI served by the Python tool (cross-platform, no extra deps).
- [ ] Slot picker, search, grouped fields, undo/redo, diff view.
- [ ] Keep the CLI as the testable core; the GUI should call the same
  operations.

## Phase 5 — optional in-game page

- [ ] Decide whether it is worth it. The game caches its save in memory;
  editing files while a slot is loaded is unsafe.
- [ ] If pursued, use `gpu/vk/vk_menu.cpp` and only expose it before a slot
  loads, or hook the save manager and edit through the game.
- [ ] Needs a host-side save API and a rebuild of `hookshot.exe`.

## Open questions

- Where is the zstd dictionary with id 1? It may be a raw dictionary embedded
  in the game or derivable from the dump. Until it is found, the GameDataList
  schema is the main blocker for named fields.
- Does the game validate anything beyond the magic/version on load? No
  checksum has been observed; the controlled edit test will settle it.
- How does a new build's GameDataList revision interact with an old save?
  Keep the revision unchanged; the game upgrades saves itself.
- Which GameData flags are actually written by the OoT remake? Its
  GameDataList is game-specific even though the container is shared.

## Non-goals

- Online cheating or modifying another player's data.
- Bundling Nintendo assets, schemas, or extracted game data.
- Replacing the game's own save UI.
