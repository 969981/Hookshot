"""Synthetic GameData saves used by the tests.

These are not copies of any game file.  They exercise the shapes the editor
needs: scalar sections, fixed strings, vectors, a Bool64bitKey set, arrays,
and the empty-section layout used by account.sav/option.sav.
"""

from __future__ import annotations

import struct

import savefile

# section type -> payload builder.  A value for one of these is a Python
# object (str/list/tuple/bytes), not an inline u32.
REFERENCE_TYPES = frozenset((
    savefile.STRING16, savefile.STRING32, savefile.STRING64,
    savefile.WSTRING16, savefile.WSTRING32, savefile.WSTRING64,
    savefile.VECTOR2, savefile.VECTOR3,
    savefile.BOOL64BIT_KEY,
    savefile.BOOL_ARRAY, savefile.INT_ARRAY, savefile.FLOAT_ARRAY,
    savefile.ENUM_ARRAY, savefile.VECTOR2_ARRAY, savefile.VECTOR3_ARRAY,
    savefile.STRING16_ARRAY, savefile.STRING32_ARRAY, savefile.STRING64_ARRAY,
    savefile.BINARY, savefile.BINARY_ARRAY,
    savefile.UINT_ARRAY, savefile.INT64, savefile.INT64_ARRAY,
    savefile.UINT64, savefile.UINT64_ARRAY,
    savefile.WSTRING16_ARRAY, savefile.WSTRING32_ARRAY, savefile.WSTRING64_ARRAY,
))


def _string(value: str, size: int, wide: bool = False) -> bytes:
    encoded = value.encode("utf-16-le" if wide else "utf-8")
    if len(encoded) > size:
        raise ValueError("%r is longer than %d bytes" % (value, size))
    return encoded + b"\x00" * (size - len(encoded))


def _payload(kind: int, value: object) -> bytes:
    if kind == savefile.STRING16:
        return _string(value, 16)
    if kind == savefile.STRING32:
        return _string(value, 32)
    if kind == savefile.STRING64:
        return _string(value, 64)
    if kind == savefile.WSTRING16:
        return _string(value, 32, True)
    if kind == savefile.WSTRING32:
        return _string(value, 64, True)
    if kind == savefile.WSTRING64:
        return _string(value, 128, True)
    if kind == savefile.VECTOR2:
        return struct.pack("<ff", *value)
    if kind == savefile.VECTOR3:
        return struct.pack("<fff", *value)
    if kind == savefile.BOOL64BIT_KEY:
        return b"".join(struct.pack("<Q", key) for key in value) + b"\x00" * 8
    if kind == savefile.BOOL_ARRAY:
        size = max(4, (len(value) + 7) // 8)
        bits = sum((1 << index) for index, item in enumerate(value) if item)
        return struct.pack("<I", len(value)) + bits.to_bytes(size, "little")
    if kind == savefile.INT_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<i", item) for item in value)
    if kind == savefile.FLOAT_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<f", item) for item in value)
    if kind in (savefile.ENUM_ARRAY, savefile.UINT_ARRAY):
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<I", item) for item in value)
    if kind == savefile.VECTOR2_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<ff", *item) for item in value)
    if kind == savefile.VECTOR3_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<fff", *item) for item in value)
    if kind in (savefile.STRING16_ARRAY, savefile.STRING32_ARRAY, savefile.STRING64_ARRAY):
        size = {savefile.STRING16_ARRAY: 16, savefile.STRING32_ARRAY: 32, savefile.STRING64_ARRAY: 64}[kind]
        return struct.pack("<I", len(value)) + b"".join(_string(item, size) for item in value)
    if kind in (savefile.WSTRING16_ARRAY, savefile.WSTRING32_ARRAY, savefile.WSTRING64_ARRAY):
        units = {savefile.WSTRING16_ARRAY: 16, savefile.WSTRING32_ARRAY: 32, savefile.WSTRING64_ARRAY: 64}[kind]
        return struct.pack("<I", len(value)) + b"".join(_string(item, units * 2, True) for item in value)
    if kind == savefile.BINARY:
        data = bytes(value)
        return struct.pack("<I", len(data)) + data
    if kind == savefile.BINARY_ARRAY:
        out = bytearray(struct.pack("<I", len(value)))
        for item in value:
            data = bytes(item)
            out += struct.pack("<I", len(data)) + data
        return bytes(out)
    if kind == savefile.INT64:
        return struct.pack("<q", value)
    if kind == savefile.UINT64:
        return struct.pack("<Q", value)
    if kind == savefile.INT64_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<q", item) for item in value)
    if kind == savefile.UINT64_ARRAY:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<Q", item) for item in value)
    raise ValueError("fixture does not build type 0x%X" % kind)


def _scalar(kind: int, value: object) -> int:
    if kind == savefile.BOOL:
        return 1 if value else 0
    if kind == savefile.INT:
        return struct.unpack("<I", struct.pack("<i", value))[0]
    if kind == savefile.FLOAT:
        return struct.unpack("<I", struct.pack("<f", value))[0]
    if kind in (savefile.ENUM, savefile.UINT):
        return int(value)
    raise ValueError("fixture does not encode scalar type 0x%X" % kind)


def build_save(sections: list[tuple[int, list[tuple[str, object]]]]) -> tuple[bytes, dict[str, int]]:
    """Build a save from ``[(type_id, [(name, value), ...]), ...]``."""
    entry_count = sum(1 + len(entries) for _, entries in sections)
    data_start = 0x20 + entry_count * 8

    data = bytearray()
    offsets: dict[str, int] = {}

    def add_blob(name: str, blob: bytes) -> int:
        offsets[name] = data_start + len(data)
        data.extend(blob)
        return offsets[name]

    for kind, entries in sections:
        for name, value in entries:
            if kind in REFERENCE_TYPES:
                add_blob(name, _payload(kind, value))

    entries_blob = bytearray()
    for kind, section_entries in sections:
        entries_blob += struct.pack("<II", 0, kind)
        for name, value in section_entries:
            entry_hash = savefile.murmur3_32(name)
            raw = offsets[name] if kind in REFERENCE_TYPES else _scalar(kind, value)
            entries_blob += struct.pack("<II", entry_hash, raw)

    header = struct.pack("<III", savefile.MAGIC, 2_982_123, data_start)
    header += b"\x00" * (0x20 - len(header))
    blob = header + bytes(entries_blob) + bytes(data)
    assert len(blob) == data_start + len(data)
    return blob, offsets


def build_fixture() -> tuple[bytes, dict[str, int]]:
    return build_save([
        (savefile.BOOL, [("Test.Bool", True)]),
        (savefile.INT, [("Test.Int", -7)]),
        (savefile.FLOAT, [("Test.Float", 1.25)]),
        (savefile.ENUM, [("Test.Enum", 0xDEADBEEF)]),
        (savefile.STRING16, [("Test.String", "HyruleField")]),
        (savefile.VECTOR3, [("Test.Vector", (1.5, 2.5, 3.5))]),
        (savefile.BOOL64BIT_KEY, [("Test.Keys", [0x1111222233334444, 0x5555666677778888])]),
        (savefile.BOOL_ARRAY, [("Test.Bools", [True, False, True])]),
        (savefile.INT_ARRAY, [("Test.Ints", [1, -2, 3])]),
    ])


def build_empty_sections() -> tuple[bytes, dict[str, int]]:
    """The account.sav shape: every type marker, no entries."""
    return build_save([(kind, []) for kind in range(0x21)])


def build_account_like() -> tuple[bytes, dict[str, int]]:
    """A small account.sav-shaped file: Bool values and one key set."""
    return build_save([
        (savefile.BOOL, [("Account.Slot0", True), ("Account.Slot1", False)]),
        (savefile.BOOL64BIT_KEY, [("Account.Keys", [0x0123456789ABCDEF])]),
    ])


def build_option_like() -> tuple[bytes, dict[str, int]]:
    """A small option.sav-shaped file with binary, 64-bit and array data."""
    return build_save([
        (savefile.BOOL, [("Option.Flag", True)]),
        (savefile.INT, [("Option.Value", 42)]),
        (savefile.ENUM, [("Option.Mode", 0xAABBCCDD)]),
        (savefile.BINARY, [("Option.Blob", b"hello")]),
        (savefile.INT64, [("Option.Big", 0x1122334455667788)]),
        (savefile.INT64_ARRAY, [("Option.Bigs", [1, 2, -3])]),
        (savefile.BOOL64BIT_KEY, [("Option.Keys", [0xDEADBEEFCAFEBABE])]),
    ])
