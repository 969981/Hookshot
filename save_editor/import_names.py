#!/usr/bin/env python3
"""Merge field names from several read-only sources into a save schema.

    python -I -B save_editor/import_names.py \
        --schema PATH [--save PATH] \
        [--totk-csv PATH] [--totk-wordlist PATH] \
        [--oot-scan PATH] [--curated PATH] \
        [--out PATH] [--min-len N] [--max-len N]

Sources are applied from lowest to highest priority:

    TotK CSV / WordList  <  OoT binary scan  <  curated overlay

A CSV row and a WordList line are accepted only when the name's Murmur3 hash
matches the hash in the row/line (for a WordList the hash is computed from the
path, so a line is exact by construction).  ``--oot-scan`` calls
``savefile.scan_for_names``; matches are exact as well.  The curated overlay is
allowed to supply a semantic label whose hash does not match the field: that
label is marked ``semantic`` instead of ``curated``.

The tool never writes to a save or a game file.  It writes only ``--out`` (or
``%TEMP%/names.json`` when there is no obvious sibling).  Output is the same
JSON shape as ``gamedata.py`` writes, so ``savefile.load_schema`` reads it.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import (  # noqa: E402
    FieldInfo,
    SaveFile,
    Schema,
    load_schema,
    murmur3_32,
    scan_for_names,
)

# Larger numbers win.  A name already present in the base schema without a
# known provenance is a manual edit and is never replaced.
PRIORITY = {
    "totk": 1,
    "oot-scan": 2,
    "semantic": 3,
    "curated": 4,
}
MANUAL_PRIORITY = 5

CSV_HEADER = ("index", "hash", "decimal", "type", "name")


class BadInput(ValueError):
    """A source file cannot be used (bad input, not a crash)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_names.py",
        description="Merge TotK/OoT/curated field names into a save schema (read-only).",
    )
    parser.add_argument("--schema", type=Path, help="base schema/names table")
    parser.add_argument("--save", type=Path, help="build the base schema from this .sav")
    parser.add_argument("--totk-csv", type=Path, help="TotK CSV: index,hash,decimal,type,name")
    parser.add_argument("--totk-wordlist", type=Path, help="TotK WordList: one GameData path per line")
    parser.add_argument("--oot-scan", type=Path, help="binary to scan for field names (OoT executable)")
    parser.add_argument("--curated", type=Path, help="hand-curated schema/names table overlay")
    parser.add_argument("--out", type=Path, help="output schema JSON")
    parser.add_argument("--min-len", type=int, default=2, help="shortest scanned name (default 2)")
    parser.add_argument("--max-len", type=int, default=200, help="longest scanned name (default 200)")
    return parser


def _schema_from_save(path: Path) -> Schema:
    save = SaveFile.load(path)
    schema = Schema()
    schema.version = save.version
    for entry in save.iter_entries():
        info = schema.fields.get(entry.hash)
        if info is None:
            info = FieldInfo()
            schema.fields[entry.hash] = info
        if info.type_name is None:
            info.type_name = entry.type_name
    return schema


def _json_metadata(path: Path | None) -> dict[str, Any]:
    """Read ``source``/``save_directory`` from a JSON schema, if it has them."""
    if path is None or path.suffix.lower() != ".json":
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    meta: dict[str, Any] = {}
    if isinstance(data.get("source"), str):
        meta["source"] = data["source"]
    if isinstance(data.get("save_directory"), list):
        meta["save_directory"] = data["save_directory"]
    return meta


def _load_base(args: argparse.Namespace) -> tuple[Schema, dict[str, Any]]:
    if args.schema is not None:
        schema = load_schema(args.schema)
        meta = _json_metadata(args.schema)
        meta.setdefault("source", str(args.schema))
        return schema, meta
    if args.save is not None:
        schema = _schema_from_save(args.save)
        return schema, {"source": str(args.save), "save_directory": []}
    raise BadInput("at least one of --schema or --save is required")


def _apply_totk_csv(path: Path, schema: Schema, offer) -> tuple[int, int]:
    """Return (invalid rows, valid names whose hash is not in the schema)."""
    invalid = 0
    not_in_schema = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if not row or not any(cell.strip() for cell in row):
                continue
            # The header is optional; a data row always has a numeric hash.
            if tuple(cell.strip().lower() for cell in row[:5]) == CSV_HEADER:
                continue
            if len(row) < 5:
                invalid += 1
                continue
            try:
                entry_hash = int(row[1].strip(), 16)
            except ValueError:
                invalid += 1
                continue
            name = row[4].strip()
            if not name or murmur3_32(name) != entry_hash:
                invalid += 1
                continue
            if entry_hash not in schema.fields:
                not_in_schema += 1
                continue
            offer(entry_hash, name, "totk", None)
    return invalid, not_in_schema


def _apply_wordlist(path: Path, schema: Schema, offer) -> int:
    """Return the number of names whose hash is not in the schema."""
    not_in_schema = 0
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        entry_hash = murmur3_32(name)
        if entry_hash not in schema.fields:
            not_in_schema += 1
            continue
        offer(entry_hash, name, "totk", None)
    return not_in_schema


def _apply_oot_scan(path: Path, schema: Schema, offer, min_len: int, max_len: int) -> None:
    found = scan_for_names(path, set(schema.fields), min_len, max_len)
    for entry_hash, name in sorted(found.items()):
        offer(entry_hash, name, "oot-scan", None)


def _apply_curated(path: Path, schema: Schema, offer) -> int:
    """Return the number of curated names whose hash is not in the schema."""
    curated = load_schema(path)
    not_in_schema = 0
    for entry_hash in sorted(curated.fields):
        info = curated.fields[entry_hash]
        if not info.name:
            continue
        if entry_hash not in schema.fields:
            not_in_schema += 1
            continue
        provenance = "curated" if murmur3_32(info.name) == entry_hash else "semantic"
        offer(entry_hash, info.name, provenance, info.note)
    return not_in_schema


def _make_offer(schema: Schema):
    """Return offer(hash, name, provenance, note) -> bool.

    A source may replace a name only when its priority is strictly higher than
    the one that supplied the current name.
    """

    def offer(entry_hash: int, name: str, provenance: str, note: str | None) -> bool:
        info = schema.fields.get(entry_hash)
        if info is None:
            return False
        rank = PRIORITY[provenance]
        if info.name:
            current = PRIORITY.get(info.provenance, MANUAL_PRIORITY)
            if current >= rank:
                return False
        info.name = name
        info.provenance = provenance
        if note is not None:
            info.note = note
        return True

    return offer


def _in_slot_folder(path: Path) -> bool:
    return any(part.lower().startswith("slot_") for part in path.resolve().parts[:-1])


def default_output_path(schema_path: Path | None) -> Path:
    """The ``--schema`` path with ``.names.json``, else ``%TEMP%/names.json``."""
    if schema_path is not None:
        candidate = (
            schema_path.with_suffix(".names.json")
            if schema_path.suffix
            else schema_path.with_name(schema_path.name + ".names.json")
        )
        if not _in_slot_folder(candidate):
            return candidate
    return Path(tempfile.gettempdir()) / "names.json"


def _resolve_output(args: argparse.Namespace) -> Path:
    if args.out is not None:
        if _in_slot_folder(args.out):
            raise BadInput(
                "refusing to write %s inside a slot_* folder; pass an --out outside it"
                % args.out
            )
        return args.out
    return default_output_path(args.schema)


def write_schema(schema: Schema, meta: dict[str, Any], out: Path) -> int:
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

    payload: dict[str, Any] = {
        "version": schema.version,
        "source": meta.get("source"),
        "save_directory": meta.get("save_directory", []),
        "fields": fields,
    }
    if schema.enums:
        payload["enums"] = {
            "0x%08X" % value: label for value, label in sorted(schema.enums.items())
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return named


def count_names(schema: Schema) -> tuple[int, int, int]:
    """Return (named, exact, semantic) over the schema's current names."""
    named = exact = semantic = 0
    for entry_hash, info in schema.fields.items():
        if not info.name:
            continue
        named += 1
        if murmur3_32(info.name) == entry_hash:
            exact += 1
        else:
            semantic += 1
    return named, exact, semantic


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.min_len < 1 or args.max_len < args.min_len:
            raise BadInput("--min-len/--max-len must satisfy 1 <= min-len <= max-len")
        schema, meta = _load_base(args)
        offer = _make_offer(schema)

        invalid = 0
        not_in_schema = 0
        if args.totk_csv is not None:
            bad, missing = _apply_totk_csv(args.totk_csv, schema, offer)
            invalid += bad
            not_in_schema += missing
        if args.totk_wordlist is not None:
            not_in_schema += _apply_wordlist(args.totk_wordlist, schema, offer)
        if args.oot_scan is not None:
            _apply_oot_scan(args.oot_scan, schema, offer, args.min_len, args.max_len)
        if args.curated is not None:
            not_in_schema += _apply_curated(args.curated, schema, offer)

        out = _resolve_output(args)
        write_schema(schema, meta, out)
        named, exact, semantic = count_names(schema)
        print(
            "fields %d, named %d, exact %d, semantic %d, "
            "invalid names skipped %d, names not in schema %d"
            % (len(schema.fields), named, exact, semantic, invalid, not_in_schema)
        )
        print("wrote %d fields (%d named) to %s" % (len(schema.fields), named, out))
    except (OSError, ValueError, csv.Error) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
