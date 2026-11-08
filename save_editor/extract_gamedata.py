#!/usr/bin/env python3
"""Decompress the game's GameDataList BYML with its own zstd dictionary.

The romfs ships the dictionaries in ``Pack/ZsDic.pack.zs``: an ordinary zstd
frame (no dictionary needed) around a SARC with four formatted dictionaries.
The GameDataList frame uses dictionary id 1, so this extracts the pack, picks
that dictionary and writes the decompressed ``GameDataList.Product.110.byml``.

    python -I -B save_editor/extract_gamedata.py --romfs "I:\\Hookshot\\romfs" \
        --out "%TEMP%\\GameDataList.Product.110.byml"

Uses Python 3.14's stdlib ``compression.zstd``; on older Python it falls back
to the ``zstandard`` package when that is installed. It only reads the romfs.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_zstd = None
_zstd_kind = None

try:  # Python 3.14+
    from compression import zstd as _zstd
    _zstd_kind = "stdlib"
except ImportError:  # older Python with the third-party package
    try:
        import zstandard as _zstd  # type: ignore[no-redef]
        _zstd_kind = "zstandard"
    except ImportError:  # pragma: no cover - depends on the host
        _zstd = None


def _require_zstd() -> None:
    if _zstd is None:
        raise RuntimeError(
            "needs Python 3.14's compression.zstd or the zstandard package"
        )


def _decompress(data: bytes, dictionary: bytes | None = None) -> bytes:
    _require_zstd()
    assert _zstd is not None
    if _zstd_kind == "stdlib":
        if dictionary is None:
            return _zstd.decompress(data)
        return _zstd.ZstdDecompressor(
            zstd_dict=_zstd.ZstdDict(dictionary, is_raw=False)
        ).decompress(data)
    if dictionary is None:
        return _zstd.ZstdDecompressor().decompress(data)
    return _zstd.ZstdDecompressor(
        dict_data=_zstd.ZstdCompressionDict(dictionary)
    ).decompress(data)


def _dict_id(blob: bytes) -> int:
    _require_zstd()
    assert _zstd is not None
    if _zstd_kind == "stdlib":
        return _zstd.ZstdDict(blob, is_raw=False).dict_id
    return int(_zstd.ZstdCompressionDict(blob).dict_id())


def read_sarc(data: bytes) -> list[bytes]:
    """The entries of a Nintendo SARC (little-endian)."""
    if len(data) < 0x20 or data[:4] != b"SARC":
        raise ValueError("not a SARC")
    data_start = struct.unpack_from("<I", data, 0x0C)[0]
    count = struct.unpack_from("<H", data, 0x1A)[0]
    if count > 10000:
        raise ValueError("implausible SARC entry count %d" % count)
    entries: list[bytes] = []
    for index in range(count):
        offset = 0x20 + 16 * index
        if offset + 16 > len(data):
            raise ValueError("SARC entry table is truncated")
        _, _, start, end = struct.unpack_from("<IIII", data, offset)
        begin = data_start + start
        stop = data_start + end
        if begin > stop or stop > len(data):
            raise ValueError("SARC entry %d is out of bounds" % index)
        entries.append(data[begin:stop])
    return entries


def find_game_data(romfs: Path) -> Path:
    candidates = sorted((romfs / "GameData").glob("GameDataList.Product.*.byml.zs"))
    if not candidates:
        raise FileNotFoundError("no GameData/GameDataList.Product.*.byml.zs under %s" % romfs)
    return candidates[-1]


def extract(romfs: Path) -> tuple[bytes, int | None, Path]:
    pack_path = romfs / "Pack" / "ZsDic.pack.zs"
    if not pack_path.is_file():
        raise FileNotFoundError("missing %s" % pack_path)
    packed = _decompress(pack_path.read_bytes())
    dictionaries = read_sarc(packed)
    dictionary = next((blob for blob in dictionaries if _dict_id(blob) == 1), None)
    if dictionary is None:
        raise ValueError("ZsDic.pack.zs has no dictionary id 1")
    target = find_game_data(romfs)
    output = _decompress(target.read_bytes(), dictionary)
    revision = None
    sidecar = target.with_suffix("").with_suffix(".revision.txt")
    try:
        revision = int(sidecar.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    return output, revision, target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_gamedata.py",
        description="Decompress GameDataList with the game's ZsDic.pack.zs dictionary.",
    )
    parser.add_argument("--romfs", type=Path, required=True, help="the game's romfs folder")
    parser.add_argument("--out", type=Path, help="output .byml (default: %%TEMP%%/GameDataList.Product.110.byml)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        data, revision, target = extract(args.romfs)
    except (OSError, ValueError, RuntimeError, struct.error) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    out = args.out or Path(os.environ.get("TEMP", ".")) / target.name[:-3]  # drop .zs
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("wrote %s (%d bytes)" % (out, len(data)))
    if revision is not None:
        print("revision %d" % revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
