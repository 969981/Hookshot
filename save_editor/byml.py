"""Small, stdlib-only reader for Nintendo BYML v2-v7 documents.

The game's ``GameDataList`` is a version 7 BYML.  The same layout covers the
uncompressed ``.bgyml`` files in the romfs, which are useful as parser tests.
Only reading is needed here; the save editor never writes BYML.

Header (16 bytes):

    0x00  char[2]  "YB" little-endian, "BY" big-endian
    0x02  u16      version (2, 3, 4 or 7 are accepted)
    0x04  u32      hash-key table offset (0 = none)
    0x08  u32      string table offset (0 = none)
    0x0C  u32      root node offset (0 = empty document)

String table at an offset:

    0xC2 + u24 count + u32 offset[count + 1] + NUL-terminated UTF-8 strings

Node types used here:

    0x20 HashMap32       u24 count, (u32 key, u32 slot)[count], u8 type[count]
    0x21 HashMap64       u24 count, (u64 key, u32 slot)[count], u8 type[count]
    0xA0 String          slot = index into the string table
    0xA1 Binary          slot = absolute offset of u32 size + bytes
    0xA2 BinaryAligned   slot = absolute offset of u32 size, u32 align, bytes
    0xC0 Array           u24 count, u8 type[count], pad4, u32 slot[count]
    0xC1 Map             u24 count, (u24 key index, u8 type, u32 slot)[count]
    0xD0 Bool, 0xD1 Int32, 0xD2 Float, 0xD3 UInt32 (inline in the slot)
    0xD4 Int64, 0xD5 UInt64, 0xD6 Double (slot = absolute offset of 8 bytes)
    0xFF Null
"""

from __future__ import annotations

import struct
from typing import Any

MAGIC_LE = b"YB"
MAGIC_BE = b"BY"
SUPPORTED_VERSIONS = {2, 3, 4, 7}

HASH32 = 0x20
HASH64 = 0x21
STRING = 0xA0
BINARY = 0xA1
BINARY_ALIGNED = 0xA2
ARRAY = 0xC0
MAP = 0xC1
STRING_TABLE = 0xC2
BOOL = 0xD0
INT = 0xD1
FLOAT = 0xD2
UINT = 0xD3
INT64 = 0xD4
UINT64 = 0xD5
DOUBLE = 0xD6
NULL = 0xFF

MAX_DEPTH = 128


class BymlError(ValueError):
    """The bytes are not a valid BYML document."""


class Byml:
    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = bytes(data)
        if len(self.data) < 0x10:
            raise BymlError("document is shorter than the 16-byte header")
        magic = self.data[:2]
        if magic == MAGIC_LE:
            self.endian = "<"
        elif magic == MAGIC_BE:
            self.endian = ">"
        else:
            raise BymlError("bad magic %r (expected b'YB' or b'BY')" % magic)
        self.version = self._u16(2)
        if self.version not in SUPPORTED_VERSIONS:
            raise BymlError("unsupported BYML version %d" % self.version)
        key_offset, string_offset, root_offset = struct.unpack_from(
            self.endian + "III", self.data, 4
        )
        self.keys = self._string_table(key_offset) if key_offset else []
        self.strings = self._string_table(string_offset) if string_offset else []
        self.root = self._node(root_offset, 0) if root_offset else None

    # ---- primitive reads -------------------------------------------------
    def _need(self, offset: int, size: int, what: str = "data") -> None:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise BymlError(
                "%s at 0x%X (%d bytes) is outside the %d-byte document"
                % (what, offset, size, len(self.data))
            )

    def _u16(self, offset: int) -> int:
        self._need(offset, 2)
        return struct.unpack_from(self.endian + "H", self.data, offset)[0]

    def _u24(self, offset: int) -> int:
        self._need(offset, 3)
        raw = self.data[offset:offset + 3]
        if self.endian == "<":
            return raw[0] | (raw[1] << 8) | (raw[2] << 16)
        return (raw[0] << 16) | (raw[1] << 8) | raw[2]

    def _u32(self, offset: int) -> int:
        self._need(offset, 4)
        return struct.unpack_from(self.endian + "I", self.data, offset)[0]

    # ---- tables and nodes ------------------------------------------------
    def _string_table(self, offset: int) -> list[str]:
        self._need(offset, 4, "string table")
        if self.data[offset] != STRING_TABLE:
            raise BymlError(
                "string table at 0x%X has type 0x%02X" % (offset, self.data[offset])
            )
        count = self._u24(offset + 1)
        self._need(offset + 4, 4 * (count + 1), "string table offsets")
        result: list[str] = []
        offsets = [
            self._u32(offset + 4 + 4 * index) for index in range(count + 1)
        ]
        for start, end in zip(offsets, offsets[1:]):
            begin = offset + start
            stop = offset + end
            self._need(begin, max(0, stop - begin), "string")
            raw = self.data[begin:stop]
            result.append(raw.split(b"\x00", 1)[0].decode("utf-8", "replace"))
        return result

    def _slot(self, offset: int, kind: int, depth: int) -> Any:
        if kind == STRING:
            index = self._u32(offset)
            if index >= len(self.strings):
                raise BymlError("string index %d is out of range" % index)
            return self.strings[index]
        if kind == BINARY or kind == BINARY_ALIGNED:
            address = self._u32(offset)
            size = self._u32(address)
            header = 8 if kind == BINARY_ALIGNED else 4
            self._need(address + header, size, "binary")
            return self.data[address + header:address + header + size]
        if kind in (MAP, ARRAY, HASH32, HASH64):
            return self._node(self._u32(offset), depth + 1)
        if kind in (INT64, UINT64, DOUBLE):
            address = self._u32(offset)
            self._need(address, 8, "64-bit value")
            fmt = {INT64: "q", UINT64: "Q", DOUBLE: "d"}[kind]
            return struct.unpack_from(self.endian + fmt, self.data, address)[0]
        return self._inline(offset, kind)

    def _inline(self, offset: int, kind: int) -> Any:
        if kind == BOOL:
            return self._u32(offset) != 0
        if kind == INT:
            self._need(offset, 4)
            return struct.unpack_from(self.endian + "i", self.data, offset)[0]
        if kind == FLOAT:
            self._need(offset, 4)
            return struct.unpack_from(self.endian + "f", self.data, offset)[0]
        if kind == UINT:
            return self._u32(offset)
        if kind == NULL:
            return None
        raise BymlError("unsupported value type 0x%02X at 0x%X" % (kind, offset))

    def _node(self, offset: int, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise BymlError("BYML nesting is deeper than %d" % MAX_DEPTH)
        self._need(offset, 4, "node")
        kind = self.data[offset]
        if kind == MAP:
            count = self._u24(offset + 1)
            self._need(offset + 4, 8 * count, "map entries")
            result: dict[str, Any] = {}
            for index in range(count):
                entry = offset + 4 + 8 * index
                key_index = self._u24(entry)
                if key_index >= len(self.keys):
                    raise BymlError("map key index %d is out of range" % key_index)
                value_kind = self.data[entry + 3]
                result[self.keys[key_index]] = self._slot(entry + 4, value_kind, depth)
            return result
        if kind == ARRAY:
            count = self._u24(offset + 1)
            self._need(offset + 4, count, "array types")
            types_at = offset + 4
            values_at = types_at + ((count + 3) // 4) * 4
            self._need(values_at, 4 * count, "array values")
            return [
                self._slot(values_at + 4 * index, self.data[types_at + index], depth)
                for index in range(count)
            ]
        if kind == HASH32:
            count = self._u24(offset + 1)
            self._need(offset + 4, 8 * count + count, "hash map")
            entries = offset + 4
            types_at = entries + 8 * count
            result: dict[int, Any] = {}
            for index in range(count):
                entry = entries + 8 * index
                key = self._u32(entry)
                result[key] = self._slot(entry + 4, self.data[types_at + index], depth)
            return result
        if kind == HASH64:
            count = self._u24(offset + 1)
            self._need(offset + 4, 12 * count + count, "hash map")
            entries = offset + 4
            types_at = entries + 12 * count
            result: dict[int, Any] = {}
            for index in range(count):
                entry = entries + 12 * index
                key = struct.unpack_from(self.endian + "Q", self.data, entry)[0]
                result[key] = self._slot(entry + 8, self.data[types_at + index], depth)
            return result
        raise BymlError("unsupported node type 0x%02X at 0x%X" % (kind, offset))


def parse(data: bytes | bytearray | memoryview) -> Any:
    return Byml(data).root


def load(path: str) -> Any:
    with open(path, "rb") as handle:
        return parse(handle.read())
