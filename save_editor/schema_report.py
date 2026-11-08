#!/usr/bin/env python3
"""Report how well a names/schema table covers a save tree.

    python -I -B save_editor/schema_report.py PATH [PATH ...] [--names PATH]
        [--json] [--strict] [--by-type] [--by-save-file] [--named-only]

``PATH`` is a .sav file or a directory (searched recursively for *.sav). The
report counts entries, named/unnamed hashes, exact/semantic/unknown
provenance, schema-only hashes, type mismatches and version mismatches.
``--by-type`` and ``--by-save-file`` add named/unnamed breakdowns.
``--named-only`` lists only the named fields and hides the schema-only and
unnamed lists. ``--strict`` exits 1 on a type or version mismatch. This script
is read-only: it never writes a names table or a save.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import (  # noqa: E402
    FieldInfo,
    SaveFile,
    SaveFormatError,
    Schema,
    murmur3_32,
)
from save_editor import load_schema_for  # noqa: E402


# Fields whose presence and name matter most to a usable save editor.  The
# hashes are the validated OoT/TotK names listed in ROADMAP.md.
CRITICAL_FIELDS: dict[str, int] = {
    "PlayerStatus.Life": 0x31AB5580,
    "PlayerStatus.MaxLife": 0xFBE01DA1,
    "PlayerStatus.CurrentRupee": 0xA77921D7,
    "PlayerStatus.SavePos": 0xC884818D,
    "World_PlayerPos": 0x8773F02D,
    "CaptionData.LocationName": 0xF74E0E8E,
    "CaptionData.Pos": 0x8B26618E,
    "CaptionData.ScreenShot": 0x63696A32,
    "Sequence_CurrentBanc": 0x1D6189DA,
    "OptionData.CameraRotateSpeed": 0x128D0120,
}

PROVENANCE_ORDER = ("exact", "semantic", "unknown")


def provenance_of(entry_hash: int, info: FieldInfo | None) -> str:
    """Classify a schema field as ``exact``, ``semantic`` or ``unknown``.

    An explicit ``semantic`` provenance always wins.  Otherwise a name is
    exact only when Murmur3 validates it; a name that does not hash to the
    entry is a semantic label.  No name at all is unknown.
    """
    if info is None or not info.name:
        return "unknown"
    label = (info.provenance or "").strip().lower()
    if label == "semantic":
        return "semantic"
    if label == "exact":
        return "exact"
    return "exact" if murmur3_32(info.name) == entry_hash else "semantic"


def collect_files(paths: list[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for text in paths:
        path = Path(text)
        if path.is_file():
            found[str(path.resolve())] = path
        elif path.is_dir():
            for item in sorted(path.rglob("*.sav")):
                if item.is_file():
                    found[str(item.resolve())] = item
        else:
            raise SystemExit("error: %s is not a file or directory" % path)
    return [found[key] for key in sorted(found)]


def _empty_counts() -> dict[str, int]:
    return {"named": 0, "unnamed": 0, "total": 0}


def _group(groups: dict[str, dict[str, int]], key: str) -> dict[str, int]:
    item = groups.get(key)
    if item is None:
        item = groups[key] = _empty_counts()
    return item


def critical_checklist(all_hashes: set[int], schema: Schema) -> list[dict[str, Any]]:
    """Presence and naming status for the critical fields."""
    items: list[dict[str, Any]] = []
    for expected, entry_hash in CRITICAL_FIELDS.items():
        name = schema.name(entry_hash)
        present = entry_hash in all_hashes
        if not present:
            status = "MISSING"
        elif not name:
            status = "UNNAMED"
        elif name == expected:
            status = "OK"
        else:
            status = "WRONG"
        items.append(
            {
                "hash": "0x%08X" % entry_hash,
                "expected": expected,
                "present_in_save": present,
                "schema_name": name,
                "ok": present and name == expected,
                "status": status,
            }
        )
    return items


def inspect(
    files: list[Path], schema: Schema, limit: int | None = None
) -> dict[str, Any]:
    report = {
        "schema": str(schema.path) if schema.path else None,
        "schema_version": schema.version,
        "files": [],
        "totals": {
            "files": 0,
            "entries": 0,
            "named": 0,
            "unnamed": 0,
            "type_mismatches": 0,
            "version_mismatches": 0,
            "exact": 0,
            "semantic": 0,
            "unknown": 0,
        },
        "by_type": {},
        "by_save_file": {},
        "schema_only": [],
        "missing_names": [],
        "named_fields": [],
        "critical": [],
    }
    all_hashes: set[int] = set()
    named_hashes: set[int] = set()
    unnamed_hashes: set[int] = set()
    type_mismatches: list[dict[str, Any]] = []
    provenance_counts = {kind: 0 for kind in PROVENANCE_ORDER}

    for path in files:
        save = SaveFile.load(path)
        entries = list(save.iter_entries())
        named = 0
        unnamed = 0
        file_type_mismatches: list[dict[str, Any]] = []
        for entry in entries:
            all_hashes.add(entry.hash)
            info = schema.field(entry.hash)
            is_named = bool(info and info.name)
            if is_named:
                named += 1
                named_hashes.add(entry.hash)
            else:
                unnamed += 1
                unnamed_hashes.add(entry.hash)

            type_key = entry.type_name
            if type_key.startswith("Unknown("):
                type_key = schema.type_name(entry.hash) or type_key
            type_counts = _group(report["by_type"], type_key)
            type_counts["named" if is_named else "unnamed"] += 1
            type_counts["total"] += 1

            save_file = info.save_file if info and info.save_file else "(none)"
            file_counts = _group(report["by_save_file"], save_file)
            file_counts["named" if is_named else "unnamed"] += 1
            file_counts["total"] += 1

            kind = provenance_of(entry.hash, info)
            provenance_counts[kind] += 1
            if is_named:
                report["named_fields"].append(
                    {
                        "hash": "0x%08X" % entry.hash,
                        "name": info.name,
                        "type": info.type_name or entry.type_name,
                        "provenance": info.provenance or kind,
                    }
                )

            schema_type = schema.type_name(entry.hash)
            if schema_type and schema_type.lower() != entry.type_name.lower():
                mismatch = {
                    "hash": "0x%08X" % entry.hash,
                    "name": schema.name(entry.hash),
                    "schema_type": schema_type,
                    "save_type": entry.type_name,
                }
                file_type_mismatches.append(mismatch)
                type_mismatches.append({"file": str(path), **mismatch})

        version_mismatch = (
            schema.version is not None and save.version != schema.version
        )
        report["files"].append(
            {
                "path": str(path),
                "version": save.version,
                "entries": len(entries),
                "named": named,
                "unnamed": unnamed,
                "type_mismatches": file_type_mismatches,
                "version_mismatch": version_mismatch,
            }
        )
        report["totals"]["files"] += 1
        report["totals"]["entries"] += len(entries)
        report["totals"]["named"] += named
        report["totals"]["unnamed"] += unnamed
        report["totals"]["version_mismatches"] += 1 if version_mismatch else 0

    report["totals"]["type_mismatches"] = len(type_mismatches)
    for kind in PROVENANCE_ORDER:
        report["totals"][kind] = provenance_counts[kind]
    report["schema_only"] = [
        {"hash": "0x%08X" % entry_hash, "name": schema.name(entry_hash)}
        for entry_hash in sorted(set(schema.fields) - all_hashes)
    ]
    report["missing_names"] = [
        {"hash": "0x%08X" % entry_hash}
        for entry_hash in sorted(unnamed_hashes)
    ]
    report["critical"] = critical_checklist(all_hashes, schema)
    if limit is not None:
        report["named_fields"] = report["named_fields"][: max(0, limit)]
    return report


def print_report(
    report: dict[str, Any],
    limit: int = 20,
    by_type: bool = False,
    by_save_file: bool = False,
    named_only: bool = False,
) -> None:
    print("schema        %s" % (report["schema"] or "(none: pass --names)"))
    if report["schema_version"] is not None:
        print("schema version %d" % report["schema_version"])
    totals = report["totals"]
    unnamed_label = "not named" if named_only else "unnamed"
    if named_only:
        print(
            "files         %d, entries %d (%d named, %d not named)"
            % (totals["files"], totals["entries"], totals["named"], totals["unnamed"])
        )
    else:
        print(
            "files         %d, entries %d (%d named, %d unnamed)"
            % (totals["files"], totals["entries"], totals["named"], totals["unnamed"])
        )
    print("type mismatch %d" % totals["type_mismatches"])
    for item in report["files"]:
        if item["type_mismatches"]:
            for mismatch in item["type_mismatches"][:limit]:
                print(
                    "  %s 0x%s  %s: schema %s, save %s"
                    % (
                        Path(item["path"]).name,
                        mismatch["hash"][2:],
                        mismatch["name"] or "",
                        mismatch["schema_type"],
                        mismatch["save_type"],
                    )
                )
            if len(item["type_mismatches"]) > limit:
                print("  ... %d more" % (len(item["type_mismatches"]) - limit))
    print("version mismatch %d" % totals["version_mismatches"])
    for item in report["files"]:
        if item["version_mismatch"]:
            print("  %s: save %d, schema %s" % (item["path"], item["version"], report["schema_version"]))
    print(
        "provenance    %d exact, %d semantic, %d unknown"
        % (totals["exact"], totals["semantic"], totals["unknown"])
    )
    if by_type:
        print("by type")
        for key in sorted(report["by_type"]):
            item = report["by_type"][key]
            print(
                "  %-14s %4d entries (%d named, %d %s)"
                % (key, item["total"], item["named"], item["unnamed"], unnamed_label)
            )
    if by_save_file:
        print("by save file")
        for key in sorted(report["by_save_file"]):
            item = report["by_save_file"][key]
            print(
                "  %-14s %4d entries (%d named, %d %s)"
                % (key, item["total"], item["named"], item["unnamed"], unnamed_label)
            )
    if named_only:
        print("named fields   %d" % totals["named"])
        for item in report["named_fields"]:
            print(
                "  %s  %-40s %-12s %s"
                % (item["hash"], item["name"], item["type"], item["provenance"])
            )
        if totals["named"] > len(report["named_fields"]):
            print("  ... %d more" % (totals["named"] - len(report["named_fields"])))
    else:
        print("schema-only    %d" % len(report["schema_only"]))
        for item in report["schema_only"][:limit]:
            print("  %s  %s" % (item["hash"], item["name"] or "(unnamed)"))
        if len(report["schema_only"]) > limit:
            print("  ... %d more" % (len(report["schema_only"]) - limit))
        print("unnamed        %d" % len(report["missing_names"]))
        for item in report["missing_names"][:limit]:
            print("  %s" % item["hash"])
        if len(report["missing_names"]) > limit:
            print("  ... %d more" % (len(report["missing_names"]) - limit))
    print("critical fields")
    for item in report["critical"]:
        print("  %-7s %s  %s" % (item["status"], item["hash"], item["expected"]))
        if item["schema_name"] and item["schema_name"] != item["expected"]:
            print("          schema name: %s" % item["schema_name"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema_report.py",
        description="Report names/schema coverage and type/version mismatches (read-only).",
    )
    parser.add_argument("paths", nargs="+", help="a .sav file or a directory to search")
    parser.add_argument("--names", "--schema", dest="names", help="hash -> name table or schema")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 on a type or version mismatch")
    parser.add_argument("--by-type", action="store_true", help="count named/unnamed fields per save type")
    parser.add_argument(
        "--by-save-file",
        action="store_true",
        help="count named/unnamed fields per save file",
    )
    parser.add_argument(
        "--named-only",
        action="store_true",
        help="list only named fields (hide the schema-only and unnamed lists)",
    )
    parser.add_argument("--limit", type=int, default=20, help="list items to print (default 20)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        files = collect_files(args.paths)
        if not files:
            print("error: no .sav files found", file=sys.stderr)
            return 2
        schema = load_schema_for(args.names, files[0])
        report = inspect(files, schema, args.limit)
    except SaveFormatError as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(
            report,
            args.limit,
            by_type=args.by_type,
            by_save_file=args.by_save_file,
            named_only=args.named_only,
        )
    if args.strict and (
        report["totals"]["type_mismatches"] or report["totals"]["version_mismatches"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
