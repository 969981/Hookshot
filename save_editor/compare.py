#!/usr/bin/env python3
"""Compare two GameData saves, slots, or accounts without writing anything.

    python -I -B save_editor/compare.py LEFT RIGHT [--names PATH] [--json] [--all] [--limit N]

Each side is a .sav file or a directory (a slot or an account). Directories are
compared by relative path, so ``slot_00`` against ``slot_05`` compares
``progress.sav`` with ``progress.sav`` and ``caption.sav`` with
``caption.sav``. Exit status: 0 equal, 1 differences, 2 error.

Only ``SaveFile.load`` is used; this script never writes a save.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

# Keep the tool working under `python -I` / `python -P`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import SaveFile, SaveFormatError, describe_value  # noqa: E402
from save_editor import load_schema_for, name_of, section_filter  # noqa: E402

DEFAULT_LIMIT = 200


def _files(path: Path) -> dict[str, Path] | None:
    if path.is_file():
        return {path.name: path}
    if path.is_dir():
        found = {
            item.relative_to(path).as_posix(): item
            for item in sorted(path.rglob("*.sav"))
            if item.is_file()
        }
        return found
    return None


def _load(paths: dict[str, Path]) -> dict[str, SaveFile]:
    return {name: SaveFile.load(path) for name, path in paths.items()}


def _entries(save: SaveFile) -> dict[tuple[int, int, int], Any]:
    result: dict[tuple[int, int, int], Any] = {}
    counts: dict[tuple[int, int], int] = {}
    for entry in save.iter_entries():
        base = (entry.section_type, entry.hash)
        index = counts.get(base, 0)
        counts[base] = index + 1
        result[(entry.section_type, entry.hash, index)] = entry
    return result


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return True
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _change(kind: str, entry: Any, schema: Any, other: Any = None) -> dict[str, Any]:
    item = {
        "kind": kind,
        "hash": "0x%08X" % entry.hash,
        "type": entry.type_name,
        "type_id": entry.section_type,
        "name": name_of(schema, entry.hash) or None,
        "left": describe_value(entry, schema=schema) if kind != "right_only" else None,
        "right": None,
    }
    if kind == "changed" and other is not None:
        item["right"] = describe_value(other, schema=schema)
    elif kind == "right_only":
        item["right"] = describe_value(entry, schema=schema)
    return item


def compare_saves(left: SaveFile, right: SaveFile, schema: Any, show_equal: bool = False) -> list[dict[str, Any]]:
    left_entries = _entries(left)
    right_entries = _entries(right)
    changes: list[dict[str, Any]] = []

    for key in sorted(set(left_entries) | set(right_entries)):
        a = left_entries.get(key)
        b = right_entries.get(key)
        if a is None:
            changes.append(_change("right_only", b, schema))
        elif b is None:
            changes.append(_change("left_only", a, schema))
        elif not _same(a.value, b.value):
            changes.append(_change("changed", a, schema, b))
        elif show_equal:
            changes.append(_change("equal", a, schema, b))
    return changes


def _print_text(path_name: str, changes: list[dict[str, Any]], limit: int, show_equal: bool) -> None:
    print("== %s ==" % path_name)
    if not any(change["kind"] != "equal" for change in changes):
        if not show_equal:
            print("  equal")
            return
    shown = 0
    for change in changes:
        if change["kind"] == "equal" and not show_equal:
            continue
        if limit and shown >= limit:
            print("  ... %d more; use --all or raise --limit" % (len(changes) - shown))
            break
        mark = {"changed": "~", "left_only": "-", "right_only": "+", "equal": "="}[change["kind"]]
        name = change["name"] or ""
        if change["kind"] == "equal":
            print("  = 0x%s  %-13s  %-40s  %s" % (change["hash"][2:], change["type"], name, change["left"]))
        elif change["kind"] == "changed":
            print(
                "  %s 0x%s  %-13s  %-40s  %s -> %s"
                % (mark, change["hash"][2:], change["type"], name, change["left"], change["right"])
            )
        elif change["kind"] == "left_only":
            print("  %s 0x%s  %-13s  %-40s  %s" % (mark, change["hash"][2:], change["type"], name, change["left"]))
        else:
            print("  %s 0x%s  %-13s  %-40s  %s" % (mark, change["hash"][2:], change["type"], name, change["right"]))
        shown += 1


def cmd_compare(args: argparse.Namespace) -> int:
    left_path = Path(args.left)
    right_path = Path(args.right)
    left_files = _files(left_path)
    right_files = _files(right_path)
    if left_files is None or right_files is None:
        print("error: each side must be a .sav file or a directory", file=sys.stderr)
        return 2
    if (left_path.is_file() != right_path.is_file()):
        print("error: compare a file with a file, or a directory with a directory", file=sys.stderr)
        return 2
    if left_path.is_file():
        # Two files are one comparison even when their names differ.
        key = left_path.name
        left_files = {key: left_path}
        right_files = {key: right_path}

    names_path = args.names
    first = next(iter(left_files.values()), None) or next(iter(right_files.values()), None)
    schema = load_schema_for(names_path, first)

    left = _load(left_files)
    right = _load(right_files)
    names = sorted(set(left) | set(right))
    report: list[dict[str, Any]] = []
    differences = 0

    for name in names:
        if name not in left:
            report.append({"file": name, "missing": "left", "changes": []})
            differences += 1
            continue
        if name not in right:
            report.append({"file": name, "missing": "right", "changes": []})
            differences += 1
            continue
        changes = compare_saves(left[name], right[name], schema, show_equal=args.all)
        diff = sum(1 for change in changes if change["kind"] != "equal")
        if diff:
            differences += diff
        report.append({"file": name, "missing": None, "changes": changes})

    if args.json:
        print(json.dumps(
            {
                "left": str(left_path),
                "right": str(right_path),
                "schema": str(schema.path) if schema.path else None,
                "equal": differences == 0,
                "files": report,
            },
            indent=2,
        ))
        return 0 if differences == 0 else 1

    print("left  %s" % left_path)
    print("right %s" % right_path)
    if schema.path:
        print("names %s" % schema.path)
    print()
    for item in report:
        if item["missing"]:
            print("== %s ==" % item["file"])
            print("  only in %s" % item["missing"])
            continue
        _print_text(item["file"], item["changes"], 0 if args.all else args.limit, args.all)
    print()
    if differences:
        print("%d difference(s)" % differences)
        return 1
    print("no differences")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare.py",
        description="Compare two GameData saves, slots, or accounts (read-only).",
    )
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--names", "--schema", dest="names", help="hash -> name table or schema")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--all", action="store_true", help="show equal entries too and do not truncate")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="changes to print (default %d)" % DEFAULT_LIMIT)
    parser.add_argument("--section", help="only this type name or id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # section_filter validates the value even though the comparison keeps all
    # sections; a filtered comparison is not useful, so reject it explicitly.
    if args.section:
        section_filter(args.section)
        print("error: compare does not filter by section; remove --section", file=sys.stderr)
        return 2
    try:
        return cmd_compare(args)
    except SaveFormatError as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
