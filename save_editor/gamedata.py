#!/usr/bin/env python3
"""Build a save-editor schema from the game's GameDataList BYML.

    python -I -B save_editor/gamedata.py GameDataList.Product.110.byml \
        --out GameDataList.schema.json [--revision 2982123] \
        [--scan-game .hookshot_exefs/main] [--merge old-schema.json]

The GameDataList carries each field's 32-bit Murmur3 hash, its save type, its
default, reset bits, save-file index, array size and enum values. It does not
carry most field names; ``--scan-game`` fills the ones it can find as printable
strings, and ``--merge`` keeps a table you already curated.

This tool is read-only with respect to the game and the saves. It reads a
decompressed BYML and writes only the ``--out`` JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import byml  # noqa: E402
from savefile import FieldInfo, Schema, load_schema, murmur3_32, scan_for_names  # noqa: E402


def _revision_from_sidecar(path: Path) -> int | None:
    for candidate in (
        path.with_name(path.stem + ".revision.txt"),
        path.with_name(path.name + ".revision.txt"),
        path.parent / "GameDataList.Product.110.revision.txt",
    ):
        try:
            return int(candidate.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return None


def _size(entry: dict[str, Any]) -> int | None:
    for key in ("ArraySize", "Size", "TotalSize", "OriginalSize"):
        if key in entry and isinstance(entry[key], int):
            return int(entry[key])
    return None


def _enums(entry: dict[str, Any]) -> dict[int, str]:
    values = entry.get("Values")
    labels = entry.get("RawValues")
    if not isinstance(values, list) or not isinstance(labels, list):
        return {}
    result: dict[int, str] = {}
    for value, label in zip(values, labels):
        if isinstance(value, int) and 0 <= value <= 0xFFFFFFFF and isinstance(label, str):
            result[value] = label
    return result


def build_schema(
    document: dict[str, Any],
    *,
    revision: int | None,
    saved_only: bool,
    defaults: bool,
    merge: Schema | None,
) -> tuple[Schema, dict[str, Any]]:
    root = document.get("RootNode", document)
    if not isinstance(root, dict):
        raise ValueError("BYML root has no RootNode/Data map")
    data = root.get("Data")
    meta = root.get("MetaData", {})
    if not isinstance(data, dict):
        raise ValueError("GameDataList has no RootNode.Data map")
    if not isinstance(meta, dict):
        meta = {}

    save_dirs = meta.get("SaveDirectory", [])
    if not isinstance(save_dirs, list):
        save_dirs = []

    schema = merge or Schema()
    schema.version = revision if revision is not None else schema.version
    stats = {
        "fields": 0,
        "saved": 0,
        "runtime": 0,
        "skipped_64bit": 0,
        "enums": 0,
        "named": 0,
    }

    for type_name in sorted(data):
        entries = data[type_name]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("Hash"), int):
                continue
            entry_hash = int(entry["Hash"])
            if entry_hash > 0xFFFFFFFF:
                # Bool64bitKey's entries are 64-bit object keys, not a
                # 32-bit save field; the save section stores the set.
                stats["skipped_64bit"] += 1
                continue
            save_index = int(entry.get("SaveFileIndex", -1))
            if saved_only and save_index < 0:
                stats["runtime"] += 1
                continue

            info = schema.fields.get(entry_hash) or FieldInfo()
            info.type_name = type_name
            info.save_file_index = save_index
            info.save_file = (
                save_dirs[save_index]
                if 0 <= save_index < len(save_dirs) and isinstance(save_dirs[save_index], str)
                else None
            )
            if entry.get("ResetTypeValue") is not None:
                info.reset = int(entry["ResetTypeValue"])
            size = _size(entry)
            if size is not None:
                info.size = size
            if defaults and "DefaultValue" in entry:
                info.default = entry["DefaultValue"]
            # Struct/BoolExp are runtime-only in this game; their Values are
            # not a save enum and their defaults are large member tables.
            if type_name in ("Enum", "EnumArray"):
                info.enum = _enums(entry)
                if info.enum:
                    stats["enums"] += 1
            schema.fields[entry_hash] = info
            stats["fields"] += 1
            stats["saved" if save_index >= 0 else "runtime"] += 1

    return schema, stats


def write_schema(schema: Schema, source: str, save_dirs: list[Any], out: Path) -> None:
    fields: dict[str, dict[str, Any]] = {}
    named = 0
    for entry_hash in sorted(schema.fields):
        info = schema.fields[entry_hash]
        item: dict[str, Any] = {"type": info.type_name}
        if info.name:
            item["name"] = info.name
            named += 1
        if info.enum:
            item["enum"] = {
                "0x%08X" % value: label for value, label in sorted(info.enum.items())
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
        if info.provenance is not None:
            item["provenance"] = info.provenance
        if info.note is not None:
            item["note"] = info.note
        fields["0x%08X" % entry_hash] = item

    payload = {
        "version": schema.version,
        "source": source,
        "save_directory": save_dirs,
        "fields": fields,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote %d fields (%d named) to %s" % (len(fields), named, out))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamedata.py",
        description="Build a save-editor schema from a decompressed GameDataList BYML.",
    )
    parser.add_argument("byml", type=Path)
    parser.add_argument("--out", type=Path, help="output schema JSON")
    parser.add_argument("--revision", type=int, help="save format revision (2982123 for Product.110)")
    parser.add_argument("--revision-file", type=Path, help="read the revision from this file")
    parser.add_argument("--merge", type=Path, help="existing schema/names table to keep")
    parser.add_argument("--scan-game", type=Path, help="scan this executable for field names")
    parser.add_argument("--all-fields", action="store_true", help="include SaveFileIndex -1 runtime fields")
    parser.add_argument("--no-defaults", action="store_true", help="omit DefaultValue")
    parser.add_argument("--min-len", type=int, default=2)
    parser.add_argument("--max-len", type=int, default=200)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        document = byml.load(args.byml)
        if not isinstance(document, dict):
            raise ValueError("GameDataList root is not a map")
        revision = args.revision
        if revision is None and args.revision_file:
            revision = int(args.revision_file.read_text(encoding="utf-8").strip())
        if revision is None:
            revision = _revision_from_sidecar(args.byml)
        merge = load_schema(args.merge) if args.merge else None
        schema, stats = build_schema(
            document,
            revision=revision,
            saved_only=not args.all_fields,
            defaults=not args.no_defaults,
            merge=merge,
        )
        if args.scan_game:
            found = scan_for_names(
                args.scan_game, set(schema.fields), args.min_len, args.max_len
            )
            for entry_hash, name in found.items():
                info = schema.fields.get(entry_hash)
                if info is not None and not info.name:
                    info.name = name
        out = args.out or args.byml.with_name(args.byml.name + ".schema.json")
        root = document.get("RootNode", document)
        save_dirs = root.get("MetaData", {}).get("SaveDirectory", []) if isinstance(root, dict) else []
        if not isinstance(save_dirs, list):
            save_dirs = []
        write_schema(schema, str(args.byml), save_dirs, out)
        print(
            "fields %d, saved %d, runtime %d, 64-bit keys skipped %d, enums %d"
            % (
                stats["fields"],
                stats["saved"],
                stats["runtime"],
                stats["skipped_64bit"],
                stats["enums"],
            )
        )
    except (OSError, ValueError, byml.BymlError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
