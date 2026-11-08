#!/usr/bin/env python3
"""Label the fields that changed between two saves.

    python -I -B save_editor/label_diff.py BEFORE.sav AFTER.sav \
        --label "collected heart piece" [--note TEXT] \
        [--names PATH] [--out PATH] [--hash HASH ...] [--name NAME] \
        [--force] [--dry-run] [--json]

The two saves are parsed with :class:`savefile.SaveFile`; ``compare`` lists
what changed, and each selected hash gets a name in a schema/names table.
A generated name has the form ``<label>.0x<HASH>`` and is recorded with
``provenance = "semantic"``.  A ``--name`` that really hashes to the field is
recorded with ``provenance = "curated"``.  An existing name is kept unless
``--force`` is given.

This tool is read-only with respect to the saves: it only writes the schema
JSON named by ``--out`` (default: ``names.json`` beside the AFTER save, or in
the account folder when AFTER lives in a ``slot_*`` folder).  It never
touches the network.

Exit status: 0 success, 2 bad input.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any

# Keep the tool working under `python -I` / `python -P`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare  # noqa: E402
import gamedata  # noqa: E402
from savefile import (  # noqa: E402
    FieldInfo,
    SaveFile,
    SaveFormatError,
    Schema,
    load_schema,
    murmur3_32,
    resolve_hash,
)

CHANGE_KINDS = ("changed", "left_only", "right_only")


# ---------------------------------------------------------------------------
# schema / output helpers


def default_out(after: Path) -> Path:
    """`names.json` beside AFTER, or in the account folder when it is a slot."""
    if after.parent.name.startswith("slot_"):
        return after.parent.parent / "names.json"
    return after.parent / "names.json"


def schema_from_save(save: SaveFile, schema: Schema | None = None) -> Schema:
    """Add every entry of ``save`` to ``schema``, filling in missing types."""
    if schema is None:
        schema = Schema()
    if schema.version is None:
        schema.version = save.version
    for entry in save.iter_entries():
        info = schema.fields.get(entry.hash)
        if info is None:
            info = FieldInfo()
            schema.fields[entry.hash] = info
        if info.type_name is None:
            info.type_name = entry.type_name
    return schema


def _field(schema: Schema, change: dict[str, Any]) -> FieldInfo:
    entry_hash = int(change["hash"], 16)
    info = schema.fields.get(entry_hash)
    if info is None:
        info = FieldInfo()
        schema.fields[entry_hash] = info
    if info.type_name is None and change.get("type"):
        info.type_name = str(change["type"])
    return info


def _hashes(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    return {resolve_hash(value) for value in values}


def select_changes(
    changes: list[dict[str, Any]], wanted_hashes: set[int] | None
) -> list[dict[str, Any]]:
    """Keep real changes, optionally restricted to a set of hashes."""
    selected = [change for change in changes if change["kind"] in CHANGE_KINDS]
    if wanted_hashes is not None:
        selected = [
            change for change in selected
            if int(change["hash"], 16) in wanted_hashes
        ]
    return selected


def build_proposals(
    schema: Schema,
    changes: list[dict[str, Any]],
    *,
    label: str,
    note: str,
    exact_name: str | None,
    force: bool,
) -> list[dict[str, Any]]:
    """Give every selected change a name and update ``schema`` in place."""
    proposals: list[dict[str, Any]] = []
    use_exact_name = exact_name is not None and len(changes) == 1

    for change in changes:
        entry_hash = int(change["hash"], 16)
        info = _field(schema, change)
        current_name = info.name or None
        proposal: dict[str, Any] = {
            "hash": "0x%08X" % entry_hash,
            "kind": change["kind"],
            "type": change.get("type"),
            "type_id": change.get("type_id"),
            "left": change.get("left"),
            "right": change.get("right"),
            "current_name": current_name,
            "name": current_name,
            "provenance": info.provenance,
            "note": info.note,
            "applied": False,
            "kept": False,
        }

        if current_name is not None and not force:
            # Names are user-owned data: do not overwrite one silently.
            proposal["kept"] = True
            proposals.append(proposal)
            continue

        if use_exact_name:
            name = exact_name  # type: ignore[assignment]
        else:
            name = "%s.0x%08X" % (label, entry_hash)
        provenance = "curated" if murmur3_32(name) == entry_hash else "semantic"

        info.name = name
        info.provenance = provenance
        info.note = note
        proposal.update(
            {
                "name": name,
                "provenance": provenance,
                "note": note,
                "applied": True,
            }
        )
        proposals.append(proposal)

    return proposals


# ---------------------------------------------------------------------------
# output


def _print_proposals(proposals: list[dict[str, Any]]) -> None:
    for item in proposals:
        mark = {"changed": "~", "left_only": "-", "right_only": "+"}.get(
            item["kind"], "?"
        )
        left = "" if item["left"] is None else item["left"]
        right = "" if item["right"] is None else item["right"]
        print(
            "  %s %s  %-13s  %s -> %s"
            % (mark, item["hash"], item["type"] or "?", left, right)
        )
        if item["kept"]:
            print(
                "      name %s [kept; use --force to overwrite]"
                % item["name"]
            )
        else:
            print(
                "      name %s [%s]"
                % (item["name"], item["provenance"])
            )


def _report(
    args: argparse.Namespace,
    after_path: Path,
    out: Path,
    changes: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    written: bool,
) -> dict[str, Any]:
    return {
        "before": str(args.before),
        "after": str(after_path),
        "schema": str(args.names) if args.names else None,
        "label": args.label,
        "note": args.note if args.note is not None else args.label,
        "out": str(out),
        "dry_run": bool(args.dry_run),
        "written": written,
        "changes": changes,
        "proposals": proposals,
    }


# ---------------------------------------------------------------------------
# command


def cmd_label(args: argparse.Namespace) -> int:
    before_path = Path(args.before)
    after_path = Path(args.after)

    if not args.label.strip():
        print("error: --label must not be empty", file=sys.stderr)
        return 2
    if args.name is not None and not args.name.strip():
        print("error: --name must not be empty", file=sys.stderr)
        return 2

    before = SaveFile.load(before_path)
    after = SaveFile.load(after_path)

    if args.names:
        schema = load_schema(args.names)
    else:
        schema = Schema()
    schema = schema_from_save(after, schema)

    wanted = _hashes(args.hash)
    changes = select_changes(
        compare.compare_saves(before, after, schema, show_equal=False),
        wanted,
    )

    notes = args.note if args.note is not None else args.label
    proposals = build_proposals(
        schema,
        changes,
        label=args.label,
        note=notes,
        exact_name=args.name,
        force=bool(args.force),
    )

    out = Path(args.out) if args.out else default_out(after_path)
    written = False

    if args.dry_run:
        report = _report(args, after_path, out, changes, proposals, written=False)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("before %s" % before_path)
            print("after  %s" % after_path)
            print("%d change(s); label %r" % (len(changes), args.label))
            _print_proposals(proposals)
            print("dry run: no files written (%s)" % out)
        return 0

    if changes:
        source = str(args.names) if args.names else str(after_path)
        if args.json:
            # write_schema prints a human line; keep stdout valid JSON.
            with contextlib.redirect_stdout(io.StringIO()):
                gamedata.write_schema(schema, source, [], out)
        else:
            print("before %s" % before_path)
            print("after  %s" % after_path)
            print("%d change(s); label %r" % (len(changes), args.label))
            _print_proposals(proposals)
            gamedata.write_schema(schema, source, [], out)
        written = True

    report = _report(args, after_path, out, changes, proposals, written=written)
    if args.json:
        print(json.dumps(report, indent=2))
    elif not changes:
        print("no changes selected; nothing written")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="label_diff.py",
        description=(
            "Label the fields that changed between two saves and write a "
            "names/schema JSON (read-only for the saves)."
        ),
    )
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--label", required=True, help='semantic label, e.g. "collected heart piece"')
    parser.add_argument("--note", help="note stored with each label (default: the label)")
    parser.add_argument("--names", "--schema", dest="names", help="existing hash -> name table or schema")
    parser.add_argument("--out", help="output schema JSON (default: names.json beside AFTER)")
    parser.add_argument(
        "--hash",
        action="append",
        dest="hash",
        metavar="HASH",
        help="only this hash (0x..., decimal, or GameData name); repeatable",
    )
    parser.add_argument("--name", help="exact GameData name when exactly one change is selected")
    parser.add_argument("--force", action="store_true", help="overwrite an existing name")
    parser.add_argument("--dry-run", action="store_true", help="print the changes and proposed names; write nothing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return cmd_label(args)
    except SaveFormatError as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
