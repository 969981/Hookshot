# Hookshot save editor

A small, standalone editor for Hookshot's save files. It reads the GameData
container used by *The Legend of Zelda: Ocarina of Time* on Switch 2 (the same
format Nintendo uses in *Tears of the Kingdom*), shows its sections and
entries, and edits scalar values and fixed-size strings in place.

It is **not** part of the game build and does not need a compiler, .NET, or any
third-party Python package. Python 3.10+ is enough.

> Back up your save before using it. The editor makes its own backup before a
> write, but a copy you control is still the safest thing to have.

## Where the saves are

Windows, in the game folder:

```
user/save/<account-id>/
  account.sav
  option.sav
  slot_00/{progress.sav,caption.sav}
  ...
  slot_05/{progress.sav,caption.sav}
```

Linux, from `host/hos_main.c`:

```
$XDG_DATA_HOME/hookshot/save/<account-id>/
# or ~/.local/share/hookshot/save/<account-id>/
```

`progress.sav` holds the game flags. `caption.sav` holds the file-select
preview (scene, spawn, position). `option.sav` holds settings.

## Usage

Run from the repository root:

```powershell
python save_editor/save_editor.py list --save-dir "I:\Hookshot\user\save"
python save_editor/save_editor.py check "I:\Hookshot\user\save\<id>\slot_00\progress.sav"
python save_editor/save_editor.py dump  "I:\Hookshot\user\save\<id>\slot_00\progress.sav" --nonzero --limit 80
python save_editor/save_editor.py get   "...\progress.sav" 0xA3DB7114
python save_editor/save_editor.py set   "...\progress.sav" 0xA3DB7114 1
```

`list` also works with the game folder itself (it looks for `user/save`) or
with `HOOKSHOT_SAVE_DIR`.

If `python` is not on PATH (the Microsoft Store alias is not a real Python),
use the interpreter bundled with Hookshot Builder:
`%LOCALAPPDATA%\HookshotBuilder\versions\<version>\HookshotBuilder\tools\python\python.exe`.

### Finding a field

The save stores MurmurHash3 hashes, not names. You can compute a name's hash:

```powershell
python save_editor/save_editor.py hash "Game"
# 0xA3DB7114        2749067540
```

The game's binary can be scanned for printable strings whose hash matches the
save. This is best-effort and can produce false positives:

```powershell
python save_editor/save_editor.py scan --game "I:\Hookshot\.hookshot_exefs\main" `
  --save "...\slot_00\progress.sav" --out names.json
python save_editor/save_editor.py dump "...\progress.sav" --names names.json --limit 80
```

`--names` accepts JSON (`{"0xA3DB7114": "Game"}`) or a text table with
`hash<TAB>name` lines. `--schema` is an alias.

### Names and schemas

`names` (also `schema`) writes a starter table from a save, optionally filling
matches from the game binary:

```powershell
python save_editor/save_editor.py names "...\slot_00\progress.sav" `
  --game "I:\Hookshot\.hookshot_exefs\main" --out names.json
```

Without `--out`, it writes `names.json` beside the save (in the account folder
for a slot). The output is a schema JSON. Fill in `name`, optional `type`, and optional
per-field `enum` labels:

```json
{
  "version": 2982123,
  "fields": {
    "0xA3DB7114": {"name": "Game", "type": "Bool64bitKey"},
    "0x8E9816FE": {
      "name": "Example.Mode",
      "type": "Enum",
      "enum": {"0xDEADBEEF": "Mode A", "0x11111111": "Mode B"}
    }
  }
}
```

If you omit `--names`/`--schema`, the tool looks for `save_editor_names.json`,
`names.json` or `schema.json` in the account folder (or beside a standalone
save), or in this folder. It does not read them from a slot folder, so a
schema never becomes part of a save slot. A table is never bundled; generate
it from your own game.

With a schema, `get`/`dump` show names and enum labels, and `set` accepts an
enum label instead of a hash.

### Full schema from the game

The game's own `GameDataList` supplies every saved field's type, default,
reset bits, save-file index, array size and enum labels. Its zstd frame uses
dictionary id 1, which ships in `romfs/Pack/ZsDic.pack.zs`. Extract both and
build the schema:

```powershell
python save_editor/extract_gamedata.py --romfs "I:\Hookshot\romfs" `
  --out "$env:TEMP\GameDataList.Product.110.byml"

python save_editor/gamedata.py "$env:TEMP\GameDataList.Product.110.byml" `
  --revision 2982123 `
  --scan-game "I:\Hookshot\.hookshot_exefs\main" `
  --out "$env:TEMP\GameDataList.schema.json"
```

`extract_gamedata.py` needs Python 3.14's `compression.zstd` (the bundled
builder Python has it) or the `zstandard` package. `gamedata.py` is
stdlib-only.

The result has ~12,000 saved fields and ~123 enum tables. Use it with
`--names`/`--schema` on `dump`, `get`, `set`, `schema_report` and `acceptance`.
The GameDataList does **not** carry most field names: `--scan-game` finds the
few printable ones (12 in the current build), and `--merge` keeps names you
curate later. Enum labels work without names, for example:

```
0xB80C70A8  Enum  PlayerStatus.AssignableItem.BottleInfo.BottleC.BottleItem
             0xBE69DE49 (Empty)
```

`--all-fields` includes runtime-only GameData (`SaveFileIndex -1`); the default
writes only fields that can appear in a save. `--no-defaults` omits
`DefaultValue` for a smaller table.

### Editing

`set` accepts:

- bools: `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0`;
- ints and uints: decimal or `0x...`;
- floats: normal Python float syntax;
- enums: a number/hex, a label from the schema, or a name (hashed with Murmur3);
- fixed strings (`String16/32/64`, `WString*`): text that fits the field.

It refuses to write while `hookshot.exe` is running unless `--force` is given.
By default it copies the save to `save-backups/<timestamp>/` first; use
`--dry-run` to see the change without writing.

## Format

```
0x00  u32  magic             0x01020304
0x04  u32  format version    GameDataList revision
0x08  u32  save_data_offset  start of the referenced-data area
0x0C        padding to 0x20

0x20        entry array, 8 bytes each: u32 hash, u32 value
              hash == 0 -> section header; value = type id
```

Scalar sections store the value inline. Reference sections store an offset;
arrays and strings start with a count or a fixed-size block. `Bool64bitKey` is
a list of 64-bit keys terminated by zero. Hashes are MurmurHash3 x86 32-bit,
seed 0, of the GameData path. The version matches `GameDataList.Product.<n>`
`revision.txt`.

The `progress.sav` "Game" value is a `Bool64bitKey` set, not a checksum. No
per-file checksum or signature has been found in the container; a controlled
edit should still be tested in game before relying on that.

## What works now

- Full read of the header, sections, entry array, arrays, strings, vectors,
  binaries and `Bool64bitKey` sets.
- In-place editing of bool/int/float/enum/uint and fixed strings.
- `list`, `check`, `dump`, `get`, `set`, `hash`, `scan`, `names`, `backup`.
- Name/schema tables (JSON or TSV), auto-discovery, and schema enum labels.
- Full GameDataList schema: `extract_gamedata.py` (dictionary + BYML) and
  `gamedata.py` (types, defaults, reset bits, save-file index, enums).
- Read-only diagnostics: `compare`, `schema_report`, `acceptance`.
- Atomic writes and automatic backups.

## Read-only diagnostics

These three helpers never write a save:

```powershell
# Diff two files, two slots, or two accounts.
python save_editor/compare.py "I:\Hookshot\user\save\<id>\slot_00" `
  "I:\Hookshot\user\save\<id>\slot_05" --limit 40

# How complete is the names table, and does it agree on types/version?
python save_editor/schema_report.py "I:\Hookshot\user\save" --names names.json --strict

# Parse/round-trip every save, show the newest slot, check progress/caption agree.
python save_editor/acceptance.py --save-dir "I:\Hookshot\user\save" --pair
```

`compare` exits 0 when equal, 1 on differences, 2 on a bad path.
`schema_report --strict` exits 1 on a type or version mismatch.
`acceptance --pair` exits 1 when a slot's `progress.sav` and `caption.sav`
disagree on scene, spawn, or position.

## Not yet

- Field names for most hashes. The GameDataList has types/defaults/enums but
  no `HashText`; `--scan-game` finds only a few printable paths. Curate the
  rest with `--merge` or save-diff experiments.
- Editing arrays, binaries, or growing the data area.
- A GUI. The parser and CLI are intended to be the stable core.
- Updating `caption.sav` together with `progress.sav` after a position/scene
  change.

## Limits and safety

- Close the game before editing. While a slot is loaded the game saves into a
  slot of its own choosing, so files changed underneath it can be replaced or
  ignored.
- Do not commit saves, `GameDataList` files, or anything extracted from the
  game. Read them from the user's own installation.
- This is a single-player save tool. Do not use it to attack, cheat in online
  services, or redistribute game data.
- If a write fails, the temp file is removed and the original is untouched.

## Manual acceptance test

The automated tests never touch a real save. The final Phase 1 check is done
by hand:

1. Close Hookshot and copy the whole `user/save` folder somewhere safe.
2. Copy one `slot_NN` to a scratch folder.
3. Change one harmless field in the copy, for example a bool with `--dry-run`
   first and then without it.
4. Put the edited slot back into a **copy** of the save tree, launch the game,
   load that slot, and confirm the change is visible.
5. Save in game. The game does **not** overwrite the slot you loaded: it
   writes a new save into a slot of its own choosing (observed: it reused the
   oldest existing slot). Quit and confirm the value in the **newest**
   `progress.sav`, not the one you edited.
6. Restore your original save tree.

If the game rejects the edited slot, stop and keep the original; the format
has no observed checksum, but version/schema assumptions still need that
confirmation.

### Result (2026-11-08)

A copied `slot_00` was edited:

- `progress.sav` `0x1D6189DA` (`String64`) `HyruleField_Child` ->
  `KakarikoVillage_Child`;
- the three position vectors (`0xC884818D`, `0x960AD56A`, `0x8773F02D`) ->
  the Kakariko position;
- `caption.sav` `0xF74E0E8E` / `0x0CCCC6CD` / `0x8B26618E` -> the same scene,
  spawn and position.

Hookshot's Load screen showed the slot as **Kakariko Village** (with the old
Hyrule Field thumbnail), and loading it spawned in Kakariko Village. The
edited format is accepted.

A later in-game save (after moving around) was produced correctly. It went to
**`slot_05`**, not the edited `slot_00`:

- `slot_05/progress.sav`, 123,988 B, latest 12:34:28 (an earlier save at 12:25 had the same state)
- `slot_05/caption.sav`, 42,217 B, latest 12:34:28
- scene/spawn: `KakarikoVillage` / `KakarikoVillage_Child`
- position: `(-80.14, 96.76, 39.20)` in both files

This is expected: **the game does not let the player overwrite a chosen save
in-game.** A save creates/rotates a slot; here it reused the oldest one,
`slot_05`, and left the loaded `slot_00` untouched. Both files parse,
round-trip byte-for-byte, and agree with each other. `account.sav` and
`option.sav` also parse.

## Development

```powershell
python -I -B save_editor/tests/test_savefile.py
python -I -B save_editor/tests/test_cli.py
python -I -B save_editor/tests/test_compare.py
python -I -B save_editor/tests/test_schema_report.py
python -I -B save_editor/tests/test_acceptance.py
python -I -B save_editor/tests/test_byml.py
python -I -B save_editor/tests/test_gamedata.py
python -I -B tests/run_all.py save_editor
```

The tests use a synthesized save (`save_editor/tests/fixture.py`); no game
save is stored in the repository. Real-save round-trip checks run only when
`HOOKSHOT_SAVE_DIR` (or `I:\Hookshot\user\save`) is present.
