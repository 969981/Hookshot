#!/usr/bin/env python3
"""Read-only acceptance report for a Hookshot save tree.

    python -I -B save_editor/acceptance.py [--save-dir PATH] [--json] [--pair]

Checks every .sav: parse, byte-identical round-trip, size, mtime, version.
Checks every slot: progress/caption previews and, with ``--pair``, that the
two agree on scene, spawn and position. Exit 1 on a parse/round-trip/pair
failure, 2 when no save tree is found. It never writes or launches the game.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savefile import SaveFile, SaveFormatError, STRING_TYPES, VECTOR3  # noqa: E402
from save_editor import discover_accounts, find_save_root  # noqa: E402


def _value(save: SaveFile, entry_hash: int) -> Any:
    matches = save.find(entry_hash)
    return matches[0].value if matches else None


def _first_string(save: SaveFile, containing: str | None = None) -> str | None:
    for entry in save.iter_entries():
        if entry.section_type in STRING_TYPES and isinstance(entry.value, str):
            value = entry.value.strip()
            if value and (containing is None or containing in value):
                return value
    return None


def _first_vector(save: SaveFile) -> tuple[float, ...] | None:
    for entry in save.iter_entries():
        if entry.section_type == VECTOR3 and isinstance(entry.value, tuple):
            if any(abs(component) > 1e-6 for component in entry.value):
                return entry.value
    return None


def progress_preview(save: SaveFile) -> dict[str, Any]:
    spawn = _value(save, 0x1D6189DA)
    if not isinstance(spawn, str):
        spawn = _first_string(save, "_Child") or _first_string(save, "_Adult") or _first_string(save, "/")
    scene = None
    if isinstance(spawn, str):
        base = spawn.split("/")[-1]
        scene = base.split("_")[0]
    position = _value(save, 0xC884818D)
    if not isinstance(position, tuple):
        position = _first_vector(save)
    return {"scene": scene, "spawn": spawn, "position": position}


def caption_preview_checked(save: SaveFile) -> dict[str, Any]:
    scene = _value(save, 0xF74E0E8E)
    spawn = _value(save, 0x0CCCC6CD)
    position = _value(save, 0x8B26618E)
    if not isinstance(scene, str) or not isinstance(spawn, str) or not isinstance(position, tuple):
        fallback = caption_preview_from_entries(save)
        scene = scene if isinstance(scene, str) else fallback.get("scene")
        spawn = spawn if isinstance(spawn, str) else fallback.get("spawn")
        position = position if isinstance(position, tuple) else fallback.get("position")
    return {"scene": scene, "spawn": spawn, "position": position}


def caption_preview_from_entries(save: SaveFile) -> dict[str, Any]:
    strings: list[str] = []
    vectors: list[tuple[float, ...]] = []
    for entry in save.iter_entries():
        if isinstance(entry.value, str) and entry.value.strip():
            strings.append(entry.value)
        elif isinstance(entry.value, tuple) and len(entry.value) == 3:
            vectors.append(entry.value)
    plain = next((value for value in strings if "/" not in value), None)
    spawn = next((value for value in strings if "/" in value), None)
    return {"scene": plain, "spawn": spawn, "position": vectors[0] if vectors else None}


def pair_mismatches(progress: dict[str, Any], caption: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    p_spawn = progress.get("spawn")
    c_spawn = caption.get("spawn")
    expected = c_spawn.split("/")[-1] if isinstance(c_spawn, str) and "/" in c_spawn else c_spawn
    if p_spawn and expected and p_spawn != expected:
        problems.append("spawn: progress %r, caption %r" % (p_spawn, expected))
    p_scene = progress.get("scene")
    c_scene = caption.get("scene")
    if p_scene and c_scene and p_scene != c_scene:
        problems.append("scene: progress %r, caption %r" % (p_scene, c_scene))
    p_pos = progress.get("position")
    c_pos = caption.get("position")
    if p_pos and c_pos:
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(p_pos, c_pos)))
        if distance > 1e-3:
            problems.append("position: distance %.6f" % distance)
    return problems


def run_report(root: Path, pair: bool) -> dict[str, Any]:
    files = sorted(root.rglob("*.sav"))
    file_reports: list[dict[str, Any]] = []
    failures = 0

    for path in files:
        item: dict[str, Any] = {
            "path": str(path),
            "size": path.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
            "version": None,
            "roundtrip": False,
            "error": None,
        }
        try:
            save = SaveFile.load(path)
            item["version"] = save.version
            item["roundtrip"] = save.to_bytes() == path.read_bytes()
            if not item["roundtrip"]:
                item["error"] = "round-trip differs"
                failures += 1
        except (OSError, SaveFormatError) as error:
            item["error"] = str(error)
            failures += 1
        file_reports.append(item)

    slot_reports: list[dict[str, Any]] = []
    newest: dict[str, Any] | None = None
    for account in discover_accounts(root):
        try:
            slots = sorted(item for item in account.iterdir() if item.is_dir() and item.name.startswith("slot_"))
        except OSError:
            continue
        for slot in slots:
            progress_path = slot / "progress.sav"
            caption_path = slot / "caption.sav"
            item: dict[str, Any] = {
                "slot": str(slot),
                "progress": str(progress_path) if progress_path.exists() else None,
                "caption": str(caption_path) if caption_path.exists() else None,
                "progress_preview": None,
                "caption_preview": None,
                "mismatches": [],
            }
            if progress_path.exists():
                try:
                    item["progress_preview"] = progress_preview(SaveFile.load(progress_path))
                except (OSError, SaveFormatError) as error:
                    item["mismatches"].append("progress: %s" % error)
            if caption_path.exists():
                try:
                    item["caption_preview"] = caption_preview_checked(SaveFile.load(caption_path))
                except (OSError, SaveFormatError) as error:
                    item["mismatches"].append("caption: %s" % error)

            if pair:
                if not progress_path.exists() or not caption_path.exists():
                    item["mismatches"].append("pair: progress.sav and caption.sav are both required")
                elif item["progress_preview"] and item["caption_preview"]:
                    item["mismatches"].extend(
                        pair_mismatches(item["progress_preview"], item["caption_preview"])
                    )
                if item["mismatches"]:
                    failures += 1

            stamp = max(
                (path.stat().st_mtime for path in (progress_path, caption_path) if path.exists()),
                default=0,
            )
            item["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)) if stamp else None
            slot_reports.append(item)
            if newest is None or (stamp and stamp > newest.get("_stamp", 0)):
                newest = {**item, "_stamp": stamp}

    if newest is not None:
        newest.pop("_stamp", None)
    return {
        "root": str(root),
        "files": file_reports,
        "slots": slot_reports,
        "newest": newest,
        "failures": failures,
    }


def print_report(report: dict[str, Any]) -> None:
    print("save root    %s" % report["root"])
    bad = [item for item in report["files"] if item["error"]]
    print("files        %d (%d ok, %d bad)" % (len(report["files"]), len(report["files"]) - len(bad), len(bad)))
    for item in bad:
        print("  BAD %s: %s" % (item["path"], item["error"]))
    newest = report["newest"]
    if newest:
        print("newest slot  %s  %s" % (Path(newest["slot"]).name, newest.get("mtime") or ""))
    for item in report["slots"]:
        progress = item["progress_preview"] or {}
        caption = item["caption_preview"] or {}
        bits = [Path(item["slot"]).name]
        bits.append("progress" if item["progress"] else "no progress")
        bits.append("caption" if item["caption"] else "no caption")
        if progress:
            bits.append("scene=%s" % progress.get("scene"))
            bits.append("spawn=%s" % progress.get("spawn"))
            if progress.get("position"):
                bits.append("pos=(%s)" % ", ".join("%.2f" % value for value in progress["position"]))
        if caption and caption.get("scene") != progress.get("scene"):
            bits.append("caption=%s" % caption.get("scene"))
        print("  " + "  ".join(bits))
        for problem in item["mismatches"]:
            print("    MISMATCH %s" % problem)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acceptance.py",
        description="Read-only parse/round-trip/preview report for a save tree.",
    )
    parser.add_argument("--save-dir", help="account folder, user/save folder, or game folder")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pair", action="store_true", help="fail when progress and caption disagree")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = find_save_root(args.save_dir)
    except SystemExit:
        return 2
    try:
        report = run_report(root, args.pair)
    except (OSError, SaveFormatError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
