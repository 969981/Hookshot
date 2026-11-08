#!/usr/bin/env python3
"""Report how well a names/schema table covers a save tree.

    python -I -B save_editor/schema_report.py PATH [PATH ...] [--names PATH] [--json] [--strict]

``PATH`` is a .sav file or a directory (searched recursively for *.sav). The
report counts entries, named/unnamed hashes, schema-only hashes, type
mismatches and version mismatches. ``--strict`` exits 1 on a type or version
mismatch. This script is read-only: it never writes a names table or a save.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import SaveFile, SaveFormatError, Schema  # noqa: E402
from save_editor import load_schema_for  # noqa: E402


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


def inspect(files: list[Path], schema: Schema) -> dict[str, Any]:
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
        },
        "schema_only": [],
        "missing_names": [],
    }
    all_hashes: set[int] = set()
    named_hashes: set[int] = set()
    unnamed_hashes: set[int] = set()
    type_mismatches: list[dict[str, Any]] = []

    for path in files:
        save = SaveFile.load(path)
        entries = list(save.iter_entries())
        named = 0
        unnamed = 0
        file_type_mismatches: list[dict[str, Any]] = []
        for entry in entries:
            all_hashes.add(entry.hash)
            info = schema.field(entry.hash)
            if info and info.name:
                named += 1
                named_hashes.add(entry.hash)
            else:
                unnamed += 1
                unnamed_hashes.add(entry.hash)
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
    report["schema_only"] = [
        {"hash": "0x%08X" % entry_hash, "name": schema.name(entry_hash)}
        for entry_hash in sorted(set(schema.fields) - all_hashes)
    ]
    report["missing_names"] = [
        {"hash": "0x%08X" % entry_hash}
        for entry_hash in sorted(unnamed_hashes)
    ]
    return report


def print_report(report: dict[str, Any], limit: int = 20) -> None:
    print("schema        %s" % (report["schema"] or "(none: pass --names)"))
    if report["schema_version"] is not None:
        print("schema version %d" % report["schema_version"])
    totals = report["totals"]
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema_report.py",
        description="Report names/schema coverage and type/version mismatches (read-only).",
    )
    parser.add_argument("paths", nargs="+", help="a .sav file or a directory to search")
    parser.add_argument("--names", "--schema", dest="names", help="hash -> name table or schema")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 on a type or version mismatch")
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
        report = inspect(files, schema)
    except SaveFormatError as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, args.limit)
    if args.strict and (
        report["totals"]["type_mismatches"] or report["totals"]["version_mismatches"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
