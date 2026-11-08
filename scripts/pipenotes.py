"""Merge or inspect hookshot_pipelines.bin, the list of pipelines the game asked for (gpu/vk/vk_draw.cpp, PipeNote).

    python -I scripts/pipenotes.py info  <notes.bin> [...]
    python -I scripts/pipenotes.py merge <out.bin> <notes.bin> [<notes.bin> ...]

The file is two u64 (the translator's stamp, a record's size), then records; each record starts with its pipeline's key
(u64). merge keeps the first record of every key, in the order given, so a list can be grown from several players' runs.
Only files with the same stamp and record size can be merged: another build's pipelines are not this build's. A last
record that was cut short (the game closed while writing) is left out. <out.bin> is written whole, then renamed over.

The records hold the draw state as the game had it, not the game's files, but nothing here proves they are safe to give
to another player's build: see docs/pipeline-notes.md before sharing one.
"""
import argparse
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Sequence

HEADER = struct.Struct("<QQ")
KEY = struct.Struct("<Q")
MAX_RECORD = 1 << 20


class NotesError(Exception):
    pass


@dataclass(frozen=True)
class Notes:
    path: str
    stamp: int
    size: int
    records: Dict[int, bytes]   # key -> whole record, in file order
    cut: int                    # bytes of a record cut short at the end


def read_notes(path: str) -> Notes:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < HEADER.size:
        raise NotesError("%s: shorter than the header" % path)
    stamp, size = HEADER.unpack_from(data, 0)
    if not KEY.size <= size <= MAX_RECORD:
        raise NotesError("%s: record size %d is not a pipeline notes file" % (path, size))
    body = len(data) - HEADER.size
    records: Dict[int, bytes] = {}
    for at in range(HEADER.size, HEADER.size + body - body % size, size):
        record = data[at:at + size]
        records.setdefault(KEY.unpack_from(record)[0], record)
    return Notes(path, stamp, size, records, body % size)


def merge(paths: Sequence[str]) -> Notes:
    if not paths:
        raise NotesError("nothing to merge")
    first = read_notes(paths[0])
    merged: Dict[int, bytes] = dict(first.records)
    for path in paths[1:]:
        other = read_notes(path)
        if (other.stamp, other.size) != (first.stamp, first.size):
            raise NotesError("%s: stamp %016x, record size %d; %s has %016x, %d: from another build"
                             % (path, other.stamp, other.size, first.path, first.stamp, first.size))
        for key, record in other.records.items():
            merged.setdefault(key, record)
    return Notes("<merged>", first.stamp, first.size, merged, 0)


def write_notes(path: str, notes: Notes) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".pipenotes-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(HEADER.pack(notes.stamp, notes.size))
            for record in notes.records.values():
                f.write(record)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    info = sub.add_parser("info", help="stamp, record size and count of each file")
    info.add_argument("files", nargs="+")
    mg = sub.add_parser("merge", help="union of the files by key, written to <out>")
    mg.add_argument("out")
    mg.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            for path in a.files:
                n = read_notes(path)
                print("%s: stamp %016x, record %d bytes, %d pipelines%s"
                      % (path, n.stamp, n.size, len(n.records), ", %d bytes cut short" % n.cut if n.cut else ""))
            return 0
        if os.path.abspath(a.out) in {os.path.abspath(p) for p in a.files}:
            raise NotesError("<out> is also an input: give a new file name")
        merged = merge(a.files)
        write_notes(a.out, merged)
        print("wrote %s: %d pipelines from %d files" % (a.out, len(merged.records), len(a.files)))
        return 0
    except (NotesError, OSError) as e:
        print("pipenotes: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
