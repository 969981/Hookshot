"""Reader and writer for Hookshot's GameData ``.sav`` files.

The format is the same one used by *Tears of the Kingdom* (the game and the
remake share Nintendo's GameData system):

    0x00  u32  magic             0x01020304
    0x04  u32  format version    GameDataList revision (e.g. 2_982_123)
    0x08  u32  save_data_offset  first byte of the referenced-data area
    0x0C        padding to 0x20

    0x20        entry array, 8 bytes per entry:
                  u32 hash             MurmurHash3_x86_32(name, seed 0)
                  u32 value_or_offset

A section starts with an entry whose hash is zero; that entry's value is the
type id for every following entry until the next zero-hash entry.  Scalars
store their value inline; arrays, strings, vectors, binaries and the
``Bool64bitKey`` set store an offset into the data area.

Only little-endian files exist on the Switch.  This module is deliberately
stdlib-only so it can run with the Python bundled by Hookshot Builder.
"""

from __future__ import annotations

import os
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

MAGIC = 0x01020304
HEADER_SIZE = 0x20
ENTRY_SIZE = 8

# Section type ids, in the order the game writes them.
BOOL = 0x00
BOOL_ARRAY = 0x01
INT = 0x02
INT_ARRAY = 0x03
FLOAT = 0x04
FLOAT_ARRAY = 0x05
ENUM = 0x06
ENUM_ARRAY = 0x07
VECTOR2 = 0x08
VECTOR2_ARRAY = 0x09
VECTOR3 = 0x0A
VECTOR3_ARRAY = 0x0B
STRING16 = 0x0C
STRING16_ARRAY = 0x0D
STRING32 = 0x0E
STRING32_ARRAY = 0x0F
STRING64 = 0x10
STRING64_ARRAY = 0x11
BINARY = 0x12
BINARY_ARRAY = 0x13
UINT = 0x14
UINT_ARRAY = 0x15
INT64 = 0x16
INT64_ARRAY = 0x17
UINT64 = 0x18
UINT64_ARRAY = 0x19
WSTRING16 = 0x1A
WSTRING16_ARRAY = 0x1B
WSTRING32 = 0x1C
WSTRING32_ARRAY = 0x1D
WSTRING64 = 0x1E
WSTRING64_ARRAY = 0x1F
BOOL64BIT_KEY = 0x20

TYPE_NAMES = {
    BOOL: "Bool",
    BOOL_ARRAY: "BoolArray",
    INT: "Int",
    INT_ARRAY: "IntArray",
    FLOAT: "Float",
    FLOAT_ARRAY: "FloatArray",
    ENUM: "Enum",
    ENUM_ARRAY: "EnumArray",
    VECTOR2: "Vector2",
    VECTOR2_ARRAY: "Vector2Array",
    VECTOR3: "Vector3",
    VECTOR3_ARRAY: "Vector3Array",
    STRING16: "String16",
    STRING16_ARRAY: "String16Array",
    STRING32: "String32",
    STRING32_ARRAY: "String32Array",
    STRING64: "String64",
    STRING64_ARRAY: "String64Array",
    BINARY: "Binary",
    BINARY_ARRAY: "BinaryArray",
    UINT: "UInt",
    UINT_ARRAY: "UIntArray",
    INT64: "Int64",
    INT64_ARRAY: "Int64Array",
    UINT64: "UInt64",
    UINT64_ARRAY: "UInt64Array",
    WSTRING16: "WString16",
    WSTRING16_ARRAY: "WString16Array",
    WSTRING32: "WString32",
    WSTRING32_ARRAY: "WString32Array",
    WSTRING64: "WString64",
    WSTRING64_ARRAY: "WString64Array",
    BOOL64BIT_KEY: "Bool64bitKey",
}

TYPE_BY_NAME = {name.lower(): value for value, name in TYPE_NAMES.items()}

SCALAR_TYPES = frozenset((BOOL, INT, FLOAT, ENUM, UINT))
STRING_TYPES = {
    STRING16: 16,
    STRING32: 32,
    STRING64: 64,
}
WSTRING_TYPES = {
    WSTRING16: 16,
    WSTRING32: 32,
    WSTRING64: 64,
}
ARRAY_TYPES = frozenset((
    BOOL_ARRAY, INT_ARRAY, FLOAT_ARRAY, ENUM_ARRAY, VECTOR2_ARRAY,
    VECTOR3_ARRAY, STRING16_ARRAY, STRING32_ARRAY, STRING64_ARRAY,
    BINARY_ARRAY, UINT_ARRAY, INT64_ARRAY, UINT64_ARRAY,
    WSTRING16_ARRAY, WSTRING32_ARRAY, WSTRING64_ARRAY,
))


class SaveFormatError(ValueError):
    """The bytes are not a valid GameData save file."""


def murmur3_32(text: str, seed: int = 0) -> int:
    """MurmurHash3 x86 32-bit, seed 0 by default.

    The game hashes a GameData path such as ``"Game"`` or
    ``"PlayerStatus.AssignableItem.BottleInfo.BottleA.BottleItem"`` with this
    function.  The UTF-8 bytes are hashed, not the Python string's code units.
    """
    data = text.encode("utf-8")
    length = len(data)
    h = seed & 0xFFFFFFFF
    nblocks = length // 4

    for i in range(nblocks):
        k = int.from_bytes(data[i * 4:i * 4 + 4], "little")
        k = (k * 0xCC9E2D51) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * 0x1B873593) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF

    tail = data[nblocks * 4:]
    k1 = 0
    if len(tail) >= 3:
        k1 ^= tail[2] << 16
    if len(tail) >= 2:
        k1 ^= tail[1] << 8
    if len(tail) >= 1:
        k1 ^= tail[0]
        k1 = (k1 * 0xCC9E2D51) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * 0x1B873593) & 0xFFFFFFFF
        h ^= k1

    h ^= length
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def resolve_hash(value: int | str) -> int:
    """A hash given as an int, ``0x...``, decimal, or a GameData name."""
    if isinstance(value, bool):
        raise TypeError("a bool is not a hash")
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if not isinstance(value, str):
        raise TypeError("hash must be an int or a string")
    text = value.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if text.isdigit():
        return int(text, 10)
    return murmur3_32(text)


@dataclass
class FieldInfo:
    """A hand-curated or generated entry for one GameData hash."""

    name: str | None = None
    type_name: str | None = None
    enum: dict[int, str] = field(default_factory=dict)
    default: Any = None
    reset: int | None = None
    save_file: str | None = None
    save_file_index: int | None = None
    size: int | None = None
    provenance: str | None = None
    note: str | None = None


@dataclass
class Schema:
    """Hash -> name/type/enum labels, loaded from a user-owned table.

    The file is optional.  A plain names table works too; a schema just adds
    ``type`` and per-field ``enum`` labels.  Nothing here is bundled with the
    repository.
    """

    version: int | None = None
    fields: dict[int, FieldInfo] = field(default_factory=dict)
    enums: dict[int, str] = field(default_factory=dict)
    path: Path | None = None

    def field(self, entry_hash: int) -> FieldInfo | None:
        return self.fields.get(entry_hash)

    def name(self, entry_hash: int) -> str | None:
        info = self.field(entry_hash)
        return info.name if info and info.name else None

    def type_name(self, entry_hash: int) -> str | None:
        info = self.field(entry_hash)
        return info.type_name if info and info.type_name else None

    def enum_name(self, entry_hash: int, value: int) -> str | None:
        info = self.field(entry_hash)
        if info and value in info.enum:
            return info.enum[value]
        return self.enums.get(value)


@dataclass
class Entry:
    """One 8-byte entry in the entry array."""

    hash: int
    offset: int                 # absolute file offset of the hash
    section_type: int
    raw: int                    # the u32 in the entry: value or data offset
    value: Any = None           # decoded value

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.section_type, "Unknown(%d)" % self.section_type)

    @property
    def value_offset(self) -> int:
        """Absolute offset of the inline u32 (the entry's second word)."""
        return self.offset + 4

    def __str__(self) -> str:
        return "0x%08X  %-13s  0x%X" % (self.hash, self.type_name, self.raw)


@dataclass
class Section:
    """A run of entries sharing one type, started by a zero-hash marker."""

    type_id: int
    marker_offset: int
    entries: list[Entry] = field(default_factory=list)

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type_id, "Unknown(%d)" % self.type_id)


class SaveFile:
    """A parsed save file.

    The original bytes are kept in ``data``.  Scalar and fixed-string edits
    patch that bytearray in place, so unknown sections, padding and trailing
    bytes survive a write unchanged.
    """

    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = bytearray(data)
        self.magic = 0
        self.version = 0
        self.data_offset = 0
        self.sections: list[Section] = []
        self._parse()

    # ---- loading / saving -------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "SaveFile":
        with open(path, "rb") as handle:
            return cls(handle.read())

    def to_bytes(self) -> bytes:
        return bytes(self.data)

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write atomically: a temporary file in the same folder, then replace."""
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        mode = None
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            pass
        try:
            with open(tmp, "wb") as handle:
                handle.write(self.data)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                try:
                    os.chmod(tmp, mode)
                except OSError:
                    pass
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    # ---- parsing ----------------------------------------------------------
    def _need(self, offset: int, size: int, what: str = "data") -> None:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise SaveFormatError(
                "%s at 0x%X (%d bytes) is outside the %d-byte file"
                % (what, offset, size, len(self.data))
            )

    def _u32(self, offset: int, what: str = "u32") -> int:
        self._need(offset, 4, what)
        return struct.unpack_from("<I", self.data, offset)[0]

    def _parse(self) -> None:
        if len(self.data) < HEADER_SIZE:
            raise SaveFormatError("file is shorter than the 0x20-byte header")
        self.magic, self.version, self.data_offset = struct.unpack_from("<III", self.data, 0)
        if self.magic != MAGIC:
            raise SaveFormatError(
                "bad magic 0x%08X (expected 0x%08X)" % (self.magic, MAGIC)
            )
        if self.data_offset < HEADER_SIZE:
            raise SaveFormatError(
                "save_data_offset 0x%X is inside the header" % self.data_offset
            )
        if self.data_offset > len(self.data):
            raise SaveFormatError(
                "save_data_offset 0x%X is past the end of the %d-byte file"
                % (self.data_offset, len(self.data))
            )
        if (self.data_offset - HEADER_SIZE) % ENTRY_SIZE:
            raise SaveFormatError(
                "entry array ends at 0x%X, not on an 8-byte boundary"
                % self.data_offset
            )

        current: Section | None = None
        offset = HEADER_SIZE
        while offset < self.data_offset:
            entry_hash, raw = struct.unpack_from("<II", self.data, offset)
            if entry_hash == 0:
                if raw not in TYPE_NAMES:
                    raise SaveFormatError(
                        "section marker at 0x%X has unknown type 0x%X" % (offset, raw)
                    )
                current = Section(raw, offset)
                self.sections.append(current)
            else:
                if current is None:
                    raise SaveFormatError(
                        "entry at 0x%X appears before any section marker" % offset
                    )
                entry = Entry(entry_hash, offset, current.type_id, raw)
                entry.value = self._decode(entry)
                current.entries.append(entry)
            offset += ENTRY_SIZE

    def _decode(self, entry: Entry) -> Any:
        kind = entry.section_type
        raw = entry.raw

        if kind == BOOL:
            return raw != 0
        if kind == INT:
            return struct.unpack("<i", struct.pack("<I", raw))[0]
        if kind == FLOAT:
            return struct.unpack("<f", struct.pack("<I", raw))[0]
        if kind in (ENUM, UINT):
            return raw
        if kind == BOOL64BIT_KEY:
            if raw == 0:
                return []
            if raw < self.data_offset:
                raise SaveFormatError(
                    "Bool64bitKey at 0x%X points to 0x%X, inside the entry array"
                    % (entry.offset, raw)
                )
            values: list[int] = []
            offset = raw
            while True:
                self._need(offset, 8, "Bool64bitKey key")
                key = struct.unpack_from("<Q", self.data, offset)[0]
                offset += 8
                if key == 0:
                    break
                values.append(key)
            return values

        # Everything below is an offset into the data area.
        if raw == 0:
            return [] if kind in ARRAY_TYPES else None
        if raw < self.data_offset:
            raise SaveFormatError(
                "entry at 0x%X points to 0x%X, inside the entry array"
                % (entry.offset, raw)
            )

        if kind == INT64:
            self._need(raw, 8, "Int64")
            return struct.unpack_from("<q", self.data, raw)[0]
        if kind == UINT64:
            self._need(raw, 8, "UInt64")
            return struct.unpack_from("<Q", self.data, raw)[0]
        if kind == VECTOR2:
            self._need(raw, 8, "Vector2")
            return tuple(struct.unpack_from("<ff", self.data, raw))
        if kind == VECTOR3:
            self._need(raw, 12, "Vector3")
            return tuple(struct.unpack_from("<fff", self.data, raw))
        if kind in STRING_TYPES:
            return self._read_fixed_string(raw, STRING_TYPES[kind], False)
        if kind in WSTRING_TYPES:
            return self._read_fixed_string(raw, WSTRING_TYPES[kind], True)
        if kind == BINARY:
            size = self._u32(raw, "Binary size")
            self._need(raw + 4, size, "Binary data")
            return bytes(self.data[raw + 4:raw + 4 + size])

        if kind in ARRAY_TYPES:
            return self._decode_array(kind, raw)

        raise SaveFormatError(
            "entry at 0x%X has unsupported type 0x%X" % (entry.offset, kind)
        )

    def _read_fixed_string(self, offset: int, units: int, wide: bool) -> str:
        size = units * 2 if wide else units
        self._need(offset, size, "string")
        raw = bytes(self.data[offset:offset + size])
        if wide:
            return raw.decode("utf-16-le", "replace").split("\x00", 1)[0]
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")

    def _count(self, offset: int) -> int:
        count = self._u32(offset, "array count")
        if count > 100_000_000:
            raise SaveFormatError("array count %d at 0x%X is implausible" % (count, offset))
        return count

    def _decode_array(self, kind: int, offset: int) -> Any:
        count = self._count(offset)
        body = offset + 4

        if kind == BOOL_ARRAY:
            size = max(4, (count + 7) // 8)
            self._need(body, size, "BoolArray")
            bits = int.from_bytes(self.data[body:body + size], "little")
            return [bool((bits >> i) & 1) for i in range(count)]

        if kind == INT_ARRAY:
            self._need(body, count * 4, "IntArray")
            return list(struct.unpack_from("<%di" % count, self.data, body)) if count else []

        if kind == FLOAT_ARRAY:
            self._need(body, count * 4, "FloatArray")
            return list(struct.unpack_from("<%df" % count, self.data, body)) if count else []

        if kind == ENUM_ARRAY or kind == UINT_ARRAY:
            self._need(body, count * 4, "UIntArray")
            return list(struct.unpack_from("<%dI" % count, self.data, body)) if count else []

        if kind == VECTOR2_ARRAY:
            self._need(body, count * 8, "Vector2Array")
            return [tuple(struct.unpack_from("<ff", self.data, body + i * 8)) for i in range(count)]

        if kind == VECTOR3_ARRAY:
            self._need(body, count * 12, "Vector3Array")
            return [tuple(struct.unpack_from("<fff", self.data, body + i * 12)) for i in range(count)]

        if kind in (STRING16_ARRAY, STRING32_ARRAY, STRING64_ARRAY):
            units = {
                STRING16_ARRAY: 16,
                STRING32_ARRAY: 32,
                STRING64_ARRAY: 64,
            }[kind]
            self._need(body, count * units, "StringArray")
            return [
                bytes(self.data[body + i * units:body + (i + 1) * units]).split(b"\x00", 1)[0].decode("utf-8", "replace")
                for i in range(count)
            ]

        if kind in (WSTRING16_ARRAY, WSTRING32_ARRAY, WSTRING64_ARRAY):
            units = {
                WSTRING16_ARRAY: 16,
                WSTRING32_ARRAY: 32,
                WSTRING64_ARRAY: 64,
            }[kind]
            size = units * 2
            self._need(body, count * size, "WStringArray")
            return [
                bytes(self.data[body + i * size:body + (i + 1) * size]).decode("utf-16-le", "replace").split("\x00", 1)[0]
                for i in range(count)
            ]

        if kind == INT64_ARRAY:
            self._need(body, count * 8, "Int64Array")
            return list(struct.unpack_from("<%dq" % count, self.data, body)) if count else []

        if kind == UINT64_ARRAY:
            self._need(body, count * 8, "UInt64Array")
            return list(struct.unpack_from("<%dQ" % count, self.data, body)) if count else []

        if kind == BINARY_ARRAY:
            values = []
            cursor = body
            for _ in range(count):
                size = self._u32(cursor, "BinaryArray size")
                cursor += 4
                self._need(cursor, size, "BinaryArray data")
                values.append(bytes(self.data[cursor:cursor + size]))
                cursor += size
            return values

        raise SaveFormatError("unsupported array type 0x%X" % kind)

    # ---- lookups ----------------------------------------------------------
    @property
    def entries(self) -> list[Entry]:
        return [entry for section in self.sections for entry in section.entries]

    def iter_entries(self) -> Iterator[Entry]:
        for section in self.sections:
            yield from section.entries

    def find(self, key: int | str) -> list[Entry]:
        wanted = resolve_hash(key)
        return [entry for entry in self.iter_entries() if entry.hash == wanted]

    def find_one(self, key: int | str) -> Entry:
        matches = self.find(key)
        if not matches:
            raise KeyError("no entry for 0x%08X%s" % (
                resolve_hash(key),
                "" if isinstance(key, int) else " (%s)" % key,
            ))
        if len(matches) > 1:
            raise KeyError(
                "0x%08X is stored %d times; use --section to choose one"
                % (resolve_hash(key), len(matches))
            )
        return matches[0]

    def section(self, type_id: int) -> Section | None:
        for section in self.sections:
            if section.type_id == type_id:
                return section
        return None

    # ---- editing ----------------------------------------------------------
    def set_scalar(self, entry: Entry, value: Any) -> None:
        """Set a Bool/Int/Float/Enum/UInt entry in place."""
        kind = entry.section_type
        if kind == BOOL:
            raw = 1 if value else 0
        elif kind == INT:
            raw = struct.unpack("<I", struct.pack("<i", int(value)))[0]
        elif kind == FLOAT:
            raw = struct.unpack("<I", struct.pack("<f", float(value)))[0]
        elif kind in (ENUM, UINT):
            number = int(value)
            if not 0 <= number <= 0xFFFFFFFF:
                raise ValueError("value %d does not fit in a u32" % number)
            raw = number
        else:
            raise ValueError(
                "%s is not a scalar type; editing it is not supported yet"
                % entry.type_name
            )
        struct.pack_into("<I", self.data, entry.offset + 4, raw)
        entry.raw = raw
        entry.value = self._decode(entry)

    def set_string(self, entry: Entry, value: str) -> None:
        """Set a fixed-size String16/32/64 or WString entry in place."""
        kind = entry.section_type
        if kind in STRING_TYPES:
            size = STRING_TYPES[kind]
            encoded = value.encode("utf-8")
            if len(encoded) > size:
                raise ValueError(
                    "%s holds at most %d UTF-8 bytes; %d given"
                    % (entry.type_name, size, len(encoded))
                )
            if entry.raw == 0:
                raise ValueError("this string has no data area; allocating one is not supported")
            self._need(entry.raw, size, "string")
            self.data[entry.raw:entry.raw + size] = encoded + b"\x00" * (size - len(encoded))
        elif kind in WSTRING_TYPES:
            units = WSTRING_TYPES[kind]
            encoded = value.encode("utf-16-le")
            if len(encoded) > units * 2:
                raise ValueError(
                    "%s holds at most %d UTF-16 code units; %d given"
                    % (entry.type_name, units, len(encoded) // 2)
                )
            if entry.raw == 0:
                raise ValueError("this string has no data area; allocating one is not supported")
            size = units * 2
            self._need(entry.raw, size, "string")
            self.data[entry.raw:entry.raw + size] = encoded + b"\x00" * (size - len(encoded))
        else:
            raise ValueError(
                "%s is not a fixed string type; editing it is not supported yet"
                % entry.type_name
            )
        entry.value = self._decode(entry)

    def set_value(self, entry: Entry, value: Any) -> None:
        if entry.section_type in SCALAR_TYPES:
            self.set_scalar(entry, value)
        elif entry.section_type in STRING_TYPES or entry.section_type in WSTRING_TYPES:
            self.set_string(entry, str(value))
        else:
            raise ValueError(
                "%s is read-only in this version" % entry.type_name
            )

    # ---- reports ----------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        return {
            "magic": "0x%08X" % self.magic,
            "version": self.version,
            "data_offset": self.data_offset,
            "file_size": len(self.data),
            "sections": [
                {
                    "type": section.type_id,
                    "name": section.type_name,
                    "marker_offset": section.marker_offset,
                    "entries": len(section.entries),
                }
                for section in self.sections
            ],
        }


def describe_value(entry: Entry, limit: int = 8, schema: Schema | None = None) -> str:
    """A short, human-readable rendering of a decoded value."""
    value = entry.value
    kind = entry.section_type
    if kind == BOOL:
        return "true" if value else "false"
    if kind == FLOAT:
        return ("%.6g" % value) if value is not None else "null"
    if kind == ENUM:
        label = schema.enum_name(entry.hash, value) if schema else None
        return "0x%08X%s" % (value, (" (%s)" % label) if label else "")
    if kind in (INT, UINT):
        return str(value)
    if kind in STRING_TYPES or kind in WSTRING_TYPES:
        return repr(value)
    if kind == BOOL64BIT_KEY:
        keys = value or []
        shown = " ".join("%016X" % key for key in keys[:limit])
        more = "" if len(keys) <= limit else " ... (+%d)" % (len(keys) - limit)
        return "%d keys%s%s" % (len(keys), (": " + shown) if shown else "", more)
    if kind == BINARY:
        if value is None:
            return "null"
        return "%d bytes: %s" % (len(value), value[:limit].hex())
    if isinstance(value, list):
        shown = ", ".join(_short(item) for item in value[:limit])
        more = "" if len(value) <= limit else ", ... (+%d)" % (len(value) - limit)
        return "[%s%s]" % (shown, more)
    if isinstance(value, tuple):
        return "(" + ", ".join("%.6g" % item for item in value) + ")"
    return repr(value)


def _short(value: Any) -> str:
    if isinstance(value, float):
        return "%.6g" % value
    if isinstance(value, bytes):
        return value[:8].hex()
    return str(value)


def _enum_map(data: object) -> dict[int, str]:
    result: dict[int, str] = {}
    if isinstance(data, dict):
        for key, label in data.items():
            result[resolve_hash(key)] = str(label)
        return result
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                result[murmur3_32(item)] = item
            elif isinstance(item, dict):
                key = item.get("hash", item.get("value"))
                if key is None:
                    raise ValueError("an enum list item needs a hash/value")
                result[resolve_hash(key)] = str(item.get("name", item.get("label", "")))
            else:
                raise ValueError("an enum list must contain strings or objects")
        return result
    raise ValueError("an enum table must be an object or a list")


def load_schema(path: str | os.PathLike[str]) -> Schema:
    """Load a hash -> name table or a richer schema.

    Accepted JSON shapes:

        {"0xA3DB7114": "Game"}
        [{"hash": "0xA3DB7114", "name": "Game"}]
        {
          "version": 2982123,
          "fields": {
            "0xA3DB7114": "Game",
            "0x...": {"name": "Player.Rupees", "type": "Int"},
            "0x...": {"name": "Song", "type": "Enum",
                       "enum": {"0x...": "Song of Storms"}}
          },
          "enums": {"0x...": "Shared label"}
        }

    A non-JSON file is a text table: ``hash<TAB>name[<TAB>type]`` per line, or
    a bare name (its Murmur3 hash is computed).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    schema = Schema(path=path)

    if path.suffix.lower() == ".json":
        import json

        data = json.loads(text)

        def add_field(key: object, value: object) -> None:
            entry_hash = resolve_hash(key)  # type: ignore[arg-type]
            if isinstance(value, str):
                schema.fields[entry_hash] = FieldInfo(name=value)
            elif isinstance(value, dict):
                info = FieldInfo()
                if value.get("name") is not None:
                    info.name = str(value["name"])
                if value.get("type") is not None:
                    info.type_name = str(value["type"])
                if "enum" in value:
                    info.enum = _enum_map(value["enum"])
                if "default" in value:
                    info.default = value["default"]
                if value.get("reset") is not None:
                    info.reset = int(value["reset"])
                if "save_file" in value:
                    info.save_file = str(value["save_file"]) if value["save_file"] is not None else None
                if value.get("save_file_index") is not None:
                    info.save_file_index = int(value["save_file_index"])
                if value.get("size") is not None:
                    info.size = int(value["size"])
                if value.get("provenance") is not None:
                    info.provenance = str(value["provenance"])
                if value.get("note") is not None:
                    info.note = str(value["note"])
                schema.fields[entry_hash] = info
            else:
                raise ValueError(
                    "field 0x%08X must be a name or an object" % entry_hash
                )

        if isinstance(data, dict) and "fields" in data:
            if data.get("version") is not None:
                schema.version = int(data["version"])
            if "enums" in data:
                schema.enums = _enum_map(data["enums"])
            fields = data["fields"]
            if not isinstance(fields, dict):
                raise ValueError("schema 'fields' must be an object")
            for key, value in fields.items():
                add_field(key, value)
            return schema

        if isinstance(data, dict):
            for key, value in data.items():
                if key in ("version", "enums"):
                    continue
                add_field(key, value)
            if data.get("version") is not None:
                schema.version = int(data["version"])
            if "enums" in data:
                schema.enums = _enum_map(data["enums"])
            return schema

        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    schema.fields[murmur3_32(item)] = FieldInfo(name=item)
                elif isinstance(item, dict) and "hash" in item:
                    info = FieldInfo(name=str(item.get("name", "")) or None)
                    if item.get("type") is not None:
                        info.type_name = str(item["type"])
                    if "enum" in item:
                        info.enum = _enum_map(item["enum"])
                    schema.fields[resolve_hash(item["hash"])] = info
                else:
                    raise ValueError("name table list items must be strings or objects")
            return schema

        raise ValueError("name table JSON must be an object or a list")

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            key, name = parts[0], parts[1].strip()
            type_name = parts[2].strip() if len(parts) > 2 else None
        elif " " in line:
            key, name = line.split(None, 1)
            type_name = None
        else:
            key, name, type_name = line, line, None
        schema.fields[resolve_hash(key)] = FieldInfo(
            name=name or None,
            type_name=type_name or None,
        )
    return schema


def load_name_table(path: str | os.PathLike[str]) -> dict[int, str]:
    """Load just the hash -> name part of a names table or schema."""
    schema = load_schema(path)
    return {
        entry_hash: info.name
        for entry_hash, info in schema.fields.items()
        if info.name
    }


def scan_for_names(
    game_path: str | os.PathLike[str],
    wanted: Iterable[int],
    min_length: int = 2,
    max_length: int = 200,
) -> dict[int, str]:
    """Scan a binary for printable strings whose Murmur3 matches ``wanted``.

    This is a best-effort helper for finding GameData names in the game's
    executable.  It can produce incidental matches; treat the result as a
    starting point, not as a schema.
    """
    import re

    wanted_set = set(wanted)
    pattern = re.compile(rb"[\x20-\x7e]{%d,%d}" % (min_length, max_length))
    found: dict[int, str] = {}
    carry = b""
    chunk_size = 8 * 1024 * 1024
    with open(game_path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            data = carry + chunk
            for match in pattern.finditer(data):
                try:
                    text = match.group().decode("latin-1")
                    value = murmur3_32(text)
                except (UnicodeError, ValueError):
                    continue
                if value in wanted_set and value not in found:
                    found[value] = text
            carry = data[-max_length:]
    return found
