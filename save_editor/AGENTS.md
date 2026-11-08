# AGENTS.md — save_editor

This folder is a **standalone save-file editor** for Hookshot's GameData
saves. It is not part of the game build. Do not wire it into `nx2/build.py`,
the CMake graph, or the runtime unless the task explicitly asks for that.

The single source of truth for the format is `savefile.py`. If a change
touches the file layout, update `savefile.py`, its tests, and `README.md`
together.

## Hard rules

1. **Never write to a real save in tests.** Use `tests/fixture.py`, a temp
   copy, or a directory under `tempfile.mkdtemp()`. The only exception is a
   manual integration run the user explicitly asks for.
2. **Never commit game data.** No `.sav` files, no `GameDataList` files, no
   extracted Nintendo schemas, no save backups. The editor may read them from
   the user's own installation at runtime, but they must not enter the repo.
3. **Preserve unknown bytes.** The container can contain sections, padding,
   stale data past a terminator, and future types. Edits patch the existing
   bytearray in place; do not rebuild the whole file unless the feature
   explicitly needs to grow the data area.
4. **Write atomically.** Temp file in the same directory, `fsync`, then
   `os.replace`. Make a backup first unless the user passed `--no-backup`.
5. **Refuse to edit a running game by default.** `hookshot.exe` saves into a
   slot of its own choosing; a loaded slot's files may be replaced or ignored.
   `--force` is the explicit escape hatch.
6. **Keep it stdlib-only.** No `pip install` for the editor. The Python
   bundled by Hookshot Builder has no `tkinter`, so do not make the CLI
   depend on it either.
7. **Don't guess field semantics.** A hash is not a name. If a feature needs
   a GameData name or enum, get it from the game's own resources or a
   user-supplied table; otherwise expose the hash and the raw value. The
   GameDataList gives types/defaults/enums but almost no field names; do not
   invent names from values.
8. **Names are user-owned data.** A `names.json`/schema (including the output
   of `gamedata.py`) is generated from the user's own game or hand-curated; it
   is gitignored and must not be bundled. `load_schema` accepts a plain names
   map, the richer `{version, fields, enums}` shape, a list of
   `{hash, name}`, and a text table. Keep all of those working;
   `load_name_table` is a thin wrapper over it. `FieldInfo` also carries
   `default`, `reset`, `save_file`, `save_file_index` and `size`; preserve
   them through `names --merge`.
9. **Auto-discovery stays predictable.** `find_schema` checks only
   `save_editor_names.json`, `names.json`, or `schema.json` in the account
   folder (or beside a standalone save), and in this folder. It deliberately
   does not look inside a `slot_*` folder. Do not search the whole disk or read
   a table from an untrusted save directory.
10. **Saving in game does not overwrite the loaded slot.** The game writes a
    new save into a slot of its own choosing (observed: it reused the oldest,
    `slot_05`) and leaves the loaded slot untouched. Do not tell a user to
    "save over" a slot in game; to put a state into a specific slot, do it
    offline with the files and a backup.

## Format invariants

- Magic `0x01020304`, little-endian.
- Header: `u32 magic`, `u32 version`, `u32 save_data_offset`; pad to `0x20`.
- Entry array starts at `0x20`, 8 bytes per entry: `u32 hash`, `u32 value`.
- `hash == 0` starts a section; its `value` is the section type id.
- Scalars (`Bool`, `Int`, `Float`, `Enum`, `UInt`) are inline.
- Arrays, strings, vectors, binaries and `Bool64bitKey` store an offset into
  the data area. Array/count data starts with a `u32` count.
- `Bool64bitKey` is a list of `u64` keys terminated by eight zero bytes.
- Hashes are MurmurHash3 x86 32-bit, seed 0, over the UTF-8 GameData path.
- The version is the `GameDataList.Product.<ver>.revision.txt` value. Do not
  change it unless the schema really changed.

## Testing

From the repo root:

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

The read-only helpers are `compare.py`, `schema_report.py`, `acceptance.py`,
`byml.py`, `gamedata.py` and `extract_gamedata.py`. They must stay read-only:
no `SaveFile.save`, no `backup_file`, no generated schema committed, and no
game launch. `extract_gamedata.py` may use Python 3.14's `compression.zstd` or
the `zstandard` package; the core editor remains stdlib-only.

The last command also picks up `save_editor/tests/test_*.py` through
`tests/run_all.py`. Real-save coverage is opt-in via `HOOKSHOT_SAVE_DIR`
(or the default `I:\Hookshot\user\save` if it exists).

## Style

- Python 3, four spaces, type hints where they help.
- `argparse` subcommands; `--json` on read-only commands.
- Prefer small, testable functions in `savefile.py`; keep the CLI thin.
- Errors go to stderr and return `1`; do not print a traceback for a bad file.

## When adding a field or feature

1. Add or extend a test in `save_editor/tests/`.
2. Decide whether it is a scalar edit, a fixed-string edit, or a structural
   edit (arrays/binary/new data). Structural edits need a writer that adjusts
   `save_data_offset` and every affected offset; do not fake it.
3. Update `ROADMAP.md` if the milestone moved.
4. Update `README.md` if the command line changed.
