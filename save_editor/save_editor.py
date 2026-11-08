#!/usr/bin/env python3
"""Command-line save editor for Hookshot (Ocarina of Time, Switch 2).

The tool is deliberately conservative: it reads the documented GameData
container, edits scalar values and fixed-size strings in place, and writes
atomically after making a backup.  It never re-serialises the whole file, so
unknown sections, padding and stale data survive an edit.

    python save_editor/save_editor.py list --save-dir "I:\\Hookshot\\user\\save"
    python save_editor/save_editor.py check <progress.sav>
    python save_editor/save_editor.py dump <progress.sav> --nonzero --limit 80
    python save_editor/save_editor.py get <progress.sav> 0xA3DB7114
    python save_editor/save_editor.py set <progress.sav> 0xA3DB7114 1
    python save_editor/save_editor.py hash Game
    python save_editor/save_editor.py scan --game <main> --save <progress.sav> --out names.json

See README.md and ROADMAP.md in this folder.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Keep the tool working under `python -I` / `python -P`, where the script's
# own directory is not added to sys.path automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import (
    BOOL,
    ENUM,
    FLOAT,
    FieldInfo,
    INT,
    SaveFile,
    SaveFormatError,
    Schema,
    STRING_TYPES,
    TYPE_BY_NAME,
    TYPE_NAMES,
    UINT,
    WSTRING_TYPES,
    describe_value,
    load_schema,
    murmur3_32,
    resolve_hash,
    scan_for_names,
)

VERSION = "0.1.0"
DEFAULT_LIMIT = 200


# ---------------------------------------------------------------------------
# save discovery


def looks_like_account(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "account.sav").exists() or (path / "option.sav").exists():
        return True
    try:
        return any(child.is_dir() and child.name.startswith("slot_") for child in path.iterdir())
    except OSError:
        return False


def candidate_roots() -> list[Path]:
    roots: list[Path] = []
    value = os.environ.get("HOOKSHOT_SAVE_DIR")
    if value:
        roots.append(Path(value))
    cwd = Path.cwd()
    roots.extend((cwd / "user" / "save", cwd.parent / "user" / "save"))
    roots.append(Path(__file__).resolve().parents[1] / "user" / "save")
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.append(Path(xdg) / "hookshot" / "save")
    roots.append(Path.home() / ".local" / "share" / "hookshot" / "save")
    return roots


def discover_accounts(root: Path) -> list[Path]:
    if looks_like_account(root):
        return [root]
    try:
        return [path for path in sorted(root.iterdir()) if looks_like_account(path)]
    except OSError:
        return []


def find_save_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit)
        if not root.exists():
            raise SystemExit("error: %s does not exist" % root)
        if (root / "user" / "save").is_dir():
            root = root / "user" / "save"
        elif (root / "save").is_dir():
            root = root / "save"
        if not discover_accounts(root):
            raise SystemExit(
                "error: %s does not contain a Hookshot save account "
                "(expected account.sav/option.sav or slot_* folders)" % root
            )
        return root

    for root in candidate_roots():
        if root.exists() and discover_accounts(root):
            return root
    raise SystemExit(
        "error: no save folder found. Pass --save-dir, for example:\n"
        '  --save-dir "I:\\Hookshot\\user\\save"\n'
        "or set HOOKSHOT_SAVE_DIR."
    )


def account_for_file(path: Path) -> Path:
    """The account folder that holds a progress.sav/caption.sav/option.sav."""
    if path.parent.name.startswith("slot_"):
        return path.parent.parent
    return path.parent


def caption_preview(path: Path) -> dict[str, Any] | None:
    try:
        save = SaveFile.load(path)
    except (OSError, SaveFormatError):
        return None

    strings: list[str] = []
    vectors: list[tuple[float, ...]] = []
    for entry in save.iter_entries():
        value = entry.value
        if isinstance(value, str) and value.strip():
            strings.append(value)
        elif isinstance(value, tuple) and len(value) in (2, 3):
            vectors.append(value)
    plain = next((s for s in strings if "/" not in s), None)
    spawn = next((s for s in strings if "/" in s), None)
    return {"scene": plain, "spawn": spawn, "position": vectors[0] if vectors else None}


# ---------------------------------------------------------------------------
# formatting helpers


SCHEMA_FILENAMES = ("save_editor_names.json", "names.json", "schema.json")


def find_schema(save_path: Path | None) -> Path | None:
    """Look for a hand-curated table beside the save or in this folder."""
    if save_path is None:
        return None
    if save_path.parent.name.startswith("slot_"):
        parents = [save_path.parent.parent]
    else:
        parents = [save_path.parent]
    parents.append(Path(__file__).resolve().parent)
    for parent in parents:
        for name in SCHEMA_FILENAMES:
            candidate = parent / name
            if candidate.is_file():
                return candidate
    return None


def load_schema_for(explicit: str | None, save_path: Path | None = None) -> Schema:
    path = Path(explicit) if explicit else find_schema(save_path)
    return load_schema(path) if path else Schema()


def name_of(schema: Schema, entry_hash: int) -> str:
    return schema.name(entry_hash) or ""


def section_filter(text: str | None) -> int | None:
    if not text:
        return None
    lowered = text.strip().lower()
    if lowered in TYPE_BY_NAME:
        return TYPE_BY_NAME[lowered]
    try:
        value = int(text, 0)
    except ValueError:
        raise SystemExit(
            "error: unknown section %r. Known names: %s"
            % (text, ", ".join(sorted(name.lower() for name in TYPE_NAMES.values())))
        )
    if value not in TYPE_NAMES:
        raise SystemExit("error: section type 0x%X is not known" % value)
    return value


def truthy(entry: Any) -> bool:
    value = entry.value
    if value is None:
        return False
    if isinstance(value, (bool, int, float)):
        return bool(value)
    if isinstance(value, (str, bytes, list, tuple, dict)):
        return len(value) > 0
    return True


def json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def entry_json(entry: Any, schema: Schema) -> dict[str, Any]:
    enum_label = schema.enum_name(entry.hash, entry.raw) if entry.section_type == ENUM else None
    return {
        "hash": "0x%08X" % entry.hash,
        "name": schema.name(entry.hash),
        "schema_type": schema.type_name(entry.hash),
        "type": entry.type_name,
        "type_id": entry.section_type,
        "offset": entry.offset,
        "raw": "0x%08X" % entry.raw,
        "value": json_value(entry.value),
        "display": describe_value(entry, schema=schema),
        "enum": enum_label,
    }


def print_summary(save: SaveFile) -> None:
    print("magic        0x%08X" % save.magic)
    print("version      %d" % save.version)
    print("data offset  0x%X" % save.data_offset)
    print("file size    %d bytes" % len(save.data))
    total = sum(len(section.entries) for section in save.sections)
    print("sections     %d (%d entries)" % (len(save.sections), total))
    for section in save.sections:
        print(
            "  0x%02X  %-13s  %5d entries  at 0x%X"
            % (section.type_id, section.type_name, len(section.entries), section.marker_offset)
        )


# ---------------------------------------------------------------------------
# commands


def cmd_list(args: argparse.Namespace) -> int:
    root = find_save_root(args.save_dir)
    accounts = discover_accounts(root)
    report: list[dict[str, Any]] = []
    for account in accounts:
        slots = []
        try:
            children = sorted(account.iterdir())
        except OSError:
            continue
        for slot in children:
            if not slot.is_dir() or not slot.name.startswith("slot_"):
                continue
            progress = slot / "progress.sav"
            caption = slot / "caption.sav"
            item: dict[str, Any] = {
                "slot": slot.name,
                "progress": None,
                "caption": None,
            }
            if progress.exists():
                stat = progress.stat()
                item["progress"] = {
                    "size": stat.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                }
            if caption.exists():
                item["caption"] = caption_preview(caption)
            slots.append(item)
        report.append({"account": str(account), "slots": slots})

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    for account in report:
        print(account["account"])
        if not account["slots"]:
            print("  (no slot_* folders)")
            continue
        for item in account["slots"]:
            progress = item["progress"]
            caption = item["caption"] or {}
            bits = [item["slot"]]
            if progress:
                bits.append("%s B" % format(progress["size"], ","))
                bits.append(progress["modified"])
            else:
                bits.append("no progress.sav")
            scene = caption.get("scene")
            spawn = caption.get("spawn")
            position = caption.get("position")
            if scene:
                bits.append("scene=%s" % scene)
            if spawn:
                bits.append("spawn=%s" % spawn)
            if position:
                bits.append("pos=(%s)" % ", ".join("%.2f" % value for value in position))
            print("  " + "  ".join(bits))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    save = SaveFile.load(args.file)
    if args.json:
        print(json.dumps(save.summary(), indent=2))
    else:
        print("%s: OK" % args.file)
        print_summary(save)
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    save = SaveFile.load(args.file)
    schema = load_schema_for(args.names, args.file)
    wanted = section_filter(args.section)
    limit = 0 if args.all else max(0, args.limit)

    rows = []
    printed = 0
    truncated = False
    for section in save.sections:
        if wanted is not None and section.type_id != wanted:
            continue
        rows.append(("section", section))
        for entry in section.entries:
            if args.nonzero and not truthy(entry):
                continue
            rows.append(("entry", entry))

    if args.json:
        data = {
            "file": str(args.file),
            "summary": save.summary(),
            "entries": [
                entry_json(entry, schema)
                for kind, entry in rows
                if kind == "entry"
                and (wanted is None or entry.section_type == wanted)
            ],
        }
        print(json.dumps(data, indent=2))
        return 0

    print("file         %s" % args.file)
    if schema.path:
        print("names        %s" % schema.path)
    print_summary(save)
    if wanted is not None:
        print("filter       section %s" % TYPE_NAMES[wanted])
    if args.nonzero:
        print("filter       nonzero only")
    print()

    current_section = None
    for kind, item in rows:
        if kind == "section":
            current_section = item
            print("== %s (0x%02X) @ 0x%X: %d entries ==" % (
                item.type_name, item.type_id, item.marker_offset, len(item.entries)
            ))
            continue
        if limit and printed >= limit:
            truncated = True
            break
        entry = item
        name = name_of(schema, entry.hash)
        print(
            "0x%08X  %-13s  %-48s  %s"
            % (entry.hash, entry.type_name, name, describe_value(entry, schema=schema))
        )
        printed += 1

    if truncated:
        remaining = sum(
            1 for kind, entry in rows
            if kind == "entry" and (wanted is None or entry.section_type == wanted)
        ) - printed
        print("\n... %d more; use --all or raise --limit" % max(0, remaining))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    save = SaveFile.load(args.file)
    schema = load_schema_for(args.names, args.file)
    wanted_section = section_filter(args.section)
    try:
        matches = save.find(args.field)
    except (TypeError, ValueError) as error:
        raise SystemExit("error: %s" % error)
    if wanted_section is not None:
        matches = [entry for entry in matches if entry.section_type == wanted_section]
    if not matches:
        wanted = resolve_hash(args.field)
        print(
            "error: no entry for 0x%08X%s"
            % (wanted, "" if _looks_numeric(args.field) else " (%s)" % args.field),
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps([entry_json(entry, schema) for entry in matches], indent=2))
        return 0

    for entry in matches:
        name = name_of(schema, entry.hash)
        print(
            "0x%08X  %-13s  %-48s  %s  (raw 0x%08X)"
            % (entry.hash, entry.type_name, name, describe_value(entry, schema=schema), entry.raw)
        )
    return 0


def _looks_numeric(text: str) -> bool:
    value = text.strip().lower()
    if value.startswith(("0x", "+0x", "-0x")):
        return True
    return value.lstrip("+-").isdigit()


def parse_bool(text: str) -> bool:
    value = text.strip().lower()
    if value in ("1", "true", "yes", "on", "y"):
        return True
    if value in ("0", "false", "no", "off", "n"):
        return False
    raise ValueError("expected true/false, yes/no, on/off or 1/0")


def parse_input(entry: Any, text: str, schema: Schema | None = None) -> Any:
    kind = entry.section_type
    if kind == BOOL:
        return parse_bool(text)
    if kind == INT:
        value = int(text, 0)
        if not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError("value %d does not fit in a signed 32-bit integer" % value)
        return value
    if kind == FLOAT:
        return float(text)
    if kind == UINT:
        value = int(text, 0)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("value %d does not fit in an unsigned 32-bit integer" % value)
        return value
    if kind == ENUM:
        if _looks_numeric(text):
            value = int(text, 0)
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("value %d does not fit in a u32" % value)
            return value
        # A schema can name enum members; otherwise the value is the Murmur3
        # hash of the member's name.
        if schema:
            info = schema.field(entry.hash)
            pairs = list(info.enum.items()) if info else []
            pairs += list(schema.enums.items())
            wanted = text.strip().lower()
            for number, label in pairs:
                if label.lower() == wanted:
                    return number
        return murmur3_32(text.strip())
    if kind in STRING_TYPES or kind in WSTRING_TYPES:
        return text
    raise ValueError("%s is read-only in this version" % entry.type_name)


def game_running() -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq hookshot.exe", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            return b"hookshot.exe" in result.stdout.lower()
        result = subprocess.run(
            ["pgrep", "-x", "hookshot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def backup_file(path: Path, account: Path | None = None) -> Path:
    account = account or account_for_file(path)
    # Microseconds plus a uniquifier: two edits in the same second must not
    # overwrite the first backup with the once-edited file.
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%06d" % (time.time_ns() % 1_000_000)
    # A real account sits under .../save/<account>; keep its backups beside
    # the account folders so the game never sees them inside a slot.  For a
    # bare file passed to the CLI, keep the backup beside that file.
    is_account = (
        (account / "account.sav").exists()
        or (account / "option.sav").exists()
        or any(child.is_dir() and child.name.startswith("slot_") for child in account.iterdir())
    )
    base_root = (account.parent if is_account else account) / "save-backups" / stamp
    destination_root = base_root
    counter = 0
    while destination_root.exists():
        counter += 1
        destination_root = base_root.with_name("%s-%d" % (base_root.name, counter))
    try:
        relative = path.relative_to(account)
    except ValueError:
        relative = Path(path.name)
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def cmd_set(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if game_running() and not args.force:
        print(
            "error: hookshot.exe is running. Close the game first (its next "
            "save would overwrite this edit), or pass --force.",
            file=sys.stderr,
        )
        return 1

    save = SaveFile.load(path)
    schema = load_schema_for(args.names, path)
    wanted_section = section_filter(args.section)
    try:
        matches = save.find(args.field)
    except (TypeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    if wanted_section is not None:
        matches = [item for item in matches if item.section_type == wanted_section]
    if not matches:
        print("error: no entry for %s" % args.field, file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(
            "error: %s matches %d entries; use --section to choose one"
            % (args.field, len(matches)),
            file=sys.stderr,
        )
        return 1
    entry = matches[0]

    try:
        value = parse_input(entry, args.value, schema)
    except (TypeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1

    old = describe_value(entry, schema=schema)
    if entry.section_type == ENUM and not _looks_numeric(args.value):
        print("enum name %r -> 0x%08X" % (args.value, value))
    try:
        save.set_value(entry, value)
    except (TypeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1

    if args.dry_run:
        print("would set 0x%08X: %s -> %s" % (
            entry.hash, old, describe_value(entry, schema=schema)
        ))
        return 0

    if not args.no_backup:
        try:
            backup = backup_file(path)
            print("backup       %s" % backup)
        except OSError as error:
            print("error: could not back up the save: %s" % error, file=sys.stderr)
            return 1

    save.save(path)
    name = name_of(schema, entry.hash)
    print(
        "set 0x%08X%s %s: %s -> %s"
        % (entry.hash, (" (%s)" % name) if name else "", entry.type_name, old,
           describe_value(entry, schema=schema))
    )
    return 0


def cmd_hash(args: argparse.Namespace) -> int:
    value = murmur3_32(args.name)
    print("0x%08X\t%d" % (value, value))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    save = SaveFile.load(args.save)
    wanted = {entry.hash for entry in save.iter_entries()}
    found = scan_for_names(args.game, wanted, args.min_len, args.max_len)
    if args.out:
        data = {"0x%08X" % key: value for key, value in sorted(found.items())}
        Path(args.out).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("wrote %d names to %s" % (len(found), args.out))
    for key, value in sorted(found.items()):
        print("0x%08X  %s" % (key, value))
    if not found:
        print(
            "no matches. The game's names may be built at runtime; use a "
            "GameDataList schema or a hand-written name table.",
            file=sys.stderr,
        )
    return 0


def cmd_names(args: argparse.Namespace) -> int:
    """Create or update a hand-curated schema from a save's hashes."""
    save = SaveFile.load(args.file)
    schema = load_schema(args.merge) if args.merge else Schema()
    if schema.version is None:
        schema.version = save.version

    for entry in save.iter_entries():
        info = schema.fields.get(entry.hash)
        if info is None:
            info = FieldInfo()
            schema.fields[entry.hash] = info
        if info.type_name is None:
            info.type_name = entry.type_name

    if args.game:
        found = scan_for_names(
            args.game, set(schema.fields), args.min_len, args.max_len
        )
        for entry_hash, name in found.items():
            info = schema.fields.setdefault(entry_hash, FieldInfo())
            if not info.name:
                info.name = name

    fields: dict[str, dict[str, Any]] = {}
    named = 0
    for entry_hash in sorted(schema.fields):
        info = schema.fields[entry_hash]
        item: dict[str, Any] = {}
        if info.name:
            item["name"] = info.name
            named += 1
        if info.type_name:
            item["type"] = info.type_name
        if info.enum:
            item["enum"] = {
                "0x%08X" % value: label
                for value, label in sorted(info.enum.items())
            }
        if info.default is not None:
            item["default"] = info.default
        if info.reset is not None:
            item["reset"] = info.reset
        if info.save_file is not None:
            item["save_file"] = info.save_file
        if info.save_file_index is not None:
            item["save_file_index"] = info.save_file_index
        if info.size is not None:
            item["size"] = info.size
        fields["0x%08X" % entry_hash] = item

    payload: dict[str, Any] = {"version": schema.version, "fields": fields}
    if schema.enums:
        payload["enums"] = {
            "0x%08X" % value: label
            for value, label in sorted(schema.enums.items())
        }
    if args.out:
        out = Path(args.out)
    elif args.file.parent.name.startswith("slot_"):
        out = args.file.parent.parent / "names.json"
    else:
        out = args.file.parent / "names.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote %d fields (%d named) to %s" % (len(fields), named, out))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    root = find_save_root(args.save_dir)
    accounts = discover_accounts(root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = Path(args.out) if args.out else root.parent / "save-backups" / stamp
    for account in accounts:
        target = destination / account.name
        if target.exists():
            print("error: %s already exists" % target, file=sys.stderr)
            return 1
        shutil.copytree(account, target)
        print("%s -> %s" % (account, target))
    print("backup root  %s" % destination)
    return 0


# ---------------------------------------------------------------------------
# parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="save_editor.py",
        description="Inspect and edit Hookshot GameData .sav files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The game must be closed while editing. Every write makes a backup "
            "under <save>/save-backups/ unless --no-backup is given.\n\n"
            "Examples:\n"
            '  python save_editor/save_editor.py list --save-dir "I:\\Hookshot\\user\\save"\n'
            "  python save_editor/save_editor.py check slot_00/progress.sav\n"
            "  python save_editor/save_editor.py dump slot_00/progress.sav --nonzero --limit 80\n"
            "  python save_editor/save_editor.py names slot_00/progress.sav --game .hookshot_exefs/main\n"
            "  python save_editor/save_editor.py hash Game\n"
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list accounts and slots under a save folder")
    listing.add_argument("--save-dir", help="user/save folder, account folder, or game folder")
    listing.add_argument("--json", action="store_true", help="machine-readable output")
    listing.set_defaults(func=cmd_list)

    check = sub.add_parser("check", help="validate a .sav header and entry array")
    check.add_argument("file", type=Path)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    dump = sub.add_parser("dump", help="show sections and entries")
    dump.add_argument("file", type=Path)
    dump.add_argument(
        "--names", "--schema", dest="names",
        help="hash -> name table or schema JSON/TSV (auto-detected if omitted)",
    )
    dump.add_argument("--section", help="only this type name or id")
    dump.add_argument("--nonzero", action="store_true", help="only non-zero/non-empty values")
    dump.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="entries to print (default %d)" % DEFAULT_LIMIT)
    dump.add_argument("--all", action="store_true", help="print every entry")
    dump.add_argument("--json", action="store_true")
    dump.set_defaults(func=cmd_dump)

    get = sub.add_parser("get", help="read one entry by hash or GameData name")
    get.add_argument("file", type=Path)
    get.add_argument("field", help="0x... hash, decimal hash, or name")
    get.add_argument(
        "--names", "--schema", dest="names",
        help="hash -> name table or schema JSON/TSV (auto-detected if omitted)",
    )
    get.add_argument("--section", help="only this type name or id")
    get.add_argument("--json", action="store_true")
    get.set_defaults(func=cmd_get)

    set_cmd = sub.add_parser("set", help="edit one scalar or fixed string")
    set_cmd.add_argument("file", type=Path)
    set_cmd.add_argument("field", help="0x... hash, decimal hash, or name")
    set_cmd.add_argument("value", help="new value; enum labels from a schema are accepted")
    set_cmd.add_argument(
        "--names", "--schema", dest="names",
        help="hash -> name table or schema JSON/TSV (auto-detected if omitted)",
    )
    set_cmd.add_argument("--section", help="only this type name or id")
    set_cmd.add_argument("--no-backup", action="store_true", help="do not copy the save first")
    set_cmd.add_argument("--force", action="store_true", help="allow writing while hookshot.exe runs")
    set_cmd.add_argument("--dry-run", action="store_true", help="show the change without writing")
    set_cmd.set_defaults(func=cmd_set)

    hash_cmd = sub.add_parser("hash", help="print the Murmur3 hash of a GameData name")
    hash_cmd.add_argument("name")
    hash_cmd.set_defaults(func=cmd_hash)

    scan = sub.add_parser("scan", help="find GameData names in a game binary (best effort)")
    scan.add_argument("--game", required=True, help="the game's main executable (unpacked)")
    scan.add_argument("--save", required=True, help="a .sav whose hashes should be matched")
    scan.add_argument("--out", help="write matches as a JSON name table")
    scan.add_argument("--min-len", type=int, default=2)
    scan.add_argument("--max-len", type=int, default=200)
    scan.set_defaults(func=cmd_scan)

    names = sub.add_parser(
        "names",
        aliases=["schema"],
        help="create/update a names or schema table from a save",
    )
    names.add_argument("file", type=Path)
    names.add_argument(
        "--out",
        help="output JSON (default: names.json beside the save, or in the account folder)",
    )
    names.add_argument("--game", help="scan a game binary for matching names")
    names.add_argument("--merge", help="existing names/schema to keep")
    names.add_argument("--min-len", type=int, default=2)
    names.add_argument("--max-len", type=int, default=200)
    names.set_defaults(func=cmd_names)

    backup = sub.add_parser("backup", help="copy every account under a save folder")
    backup.add_argument("--save-dir")
    backup.add_argument("--out", help="destination folder")
    backup.set_defaults(func=cmd_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except SaveFormatError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
