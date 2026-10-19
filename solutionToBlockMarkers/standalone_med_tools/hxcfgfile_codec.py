#!/usr/bin/env python3
"""
Pure-Python HxCfgFile v3 Codec for Hamilton .med, .stp, and .lay Files
======================================================================

This module provides a fully self-contained, zero-dependency codec for reading
and writing the HxCfgFile version 3 binary container format used by Hamilton
STAR / STARlet / Vantage liquid-handling robots.

Binary Format Overview
----------------------

The HxCfgFile v3 binary container stores instrument method (.med), step
(.stp), and layout (.lay) files.  The on-disk layout is:

    ┌──────────────────────────────────────────────────────────────┐
    │  File Header (4 bytes)                                       │
    │    [u16-LE]  version          -- always 3                     │
    │    [u16-LE]  type_marker      -- always 1                     │
    ├──────────────────────────────────────────────────────────────┤
    │  Named-Section Count (4 bytes)                               │
    │    [u32-LE]  count            -- 0, 1, or N                   │
    ├──────────────────────────────────────────────────────────────┤
    │  Named Section(s) (repeated count times)                      │
    │    [short-string]  section_name                              │
    │        .med → "ActivityData,ActivityData"                    │
    │        .stp → "Method,Properties"                            │
    │        .lay → "DECKLAY,ML_STAR", "DEVICE,...", etc.           │
    │    [u16-LE]  field_type       -- 1, 2, or 5                   │
    │    [u32-LE]  field_count      -- 1 or more                    │
    │    Repeated field_count times:                                │
    │      [short-string]  field_key                               │
    │      [var-string]  field_value                                │
    ├──────────────────────────────────────────────────────────────┤
    │  HxPars Count (1 byte) + 3-byte zero pad                    │
    │    [u8]     hxpars_count                                     │
    │    [3×0x00] padding                                          │
    ├──────────────────────────────────────────────────────────────┤
    │  HxPars Sections (repeated hxpars_count times)               │
    │    [short-string]  section_header  -- "<prefix>,<key>"        │
    │    [u16-LE]  pars_version     -- typically 3, may vary        │
    │    [u32-LE]  token_count                                     │
    │    [var-string × token_count]  tokens                        │
    ├──────────────────────────────────────────────────────────────┤
    │  Footer                                                      │
    │    \\r\\n                                                       │
    │    ASCII metadata line ($$author=… $$)                        │
    └──────────────────────────────────────────────────────────────┘

String Encoding
~~~~~~~~~~~~~~~

Two string encodings are used throughout:

* **short-string**: 1-byte length prefix (max 255 bytes) + raw Latin-1 payload.
* **var-string**: 1-byte marker + payload.
    - If the marker byte is 0x00-0xFE the marker *is* the length
      and the payload follows immediately.
    - If the marker byte is 0xFF the next 2 bytes are a u16-LE length,
      then the payload follows.  This allows strings up to 65 535 bytes.

All string payloads are encoded in Latin-1 (ISO 8859-1).

Text Representation
~~~~~~~~~~~~~~~~~~~

The HxCfgFilConverter.exe utility (``/t`` flag) converts between this binary
container and a human-readable text format.  This module reproduces that
conversion entirely in Python so that no COM objects, Windows executables,
or third-party packages are required.

CLI Usage
~~~~~~~~~

::

    python hxcfgfile_codec.py to-text   input.med  output.med.txt
    python hxcfgfile_codec.py to-binary input.med.txt  output.med
    python hxcfgfile_codec.py roundtrip input.med  [output.med]
    python hxcfgfile_codec.py dump      input.med

All commands work with .med, .stp, and .lay files interchangeably.

Subcommands:

* ``to-text``   -- convert a binary .med/.stp to its text representation.
* ``to-binary`` -- convert a text representation back to a binary container.
* ``roundtrip`` -- binary → text → binary and verify byte-for-byte equality.
* ``dump``      -- print a human-readable structural summary of the binary file.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Constants -- magic numbers & well-known values
# ---------------------------------------------------------------------------

HXCFG_VERSION: int = 3
"""HxCfgFile container version handled by this codec."""

HXCFG_TYPE_MARKER: int = 1
"""Type marker that immediately follows the version word.  Always 1."""

HXPARS_VERSION: int = 3
"""Default version marker inside each HxPars section (may vary in .lay files)."""

NAMED_FIELD_TYPE: int = 1
"""Default field type inside a named section (1 for .med/.stp; varies in .lay)."""

NAMED_FIELD_COUNT: int = 1
"""Default field count inside a named section (1 for .med/.stp; varies in .lay)."""

HXPARS_PAD_BYTES: bytes = b"\x00\x00\x00"
"""Three-byte zero pad observed between the HxPars count byte and the first
HxPars section in every known binary file."""

VAR_STRING_LONG_MARKER: int = 0xFF
"""If the first byte of a var-string is 0xFF, the length is stored in the
following two bytes as a u16-LE value."""

SHORT_STRING_MAX_LENGTH: int = 255
"""Maximum payload length for a short-string (1-byte length prefix)."""

VAR_STRING_MAX_LENGTH: int = 0xFFFF
"""Maximum payload length for a var-string when using the 0xFF + u16-LE
encoding (65 535 bytes)."""

HEADER_TEXT: str = "HxCfgFile,3;\r\n\r\nConfigIsValid,Y;\r\n\r\n"
"""Fixed text header emitted at the top of every text-format file."""

ACTIVITY_SECTION_NAME: str = "ActivityData,ActivityData"
"""Named-section name for .med files (contains the flowchart blob)."""

ACTIVITY_KEY: str = "ActivityDocument"
"""Field key inside the ActivityData named section."""

PROPERTIES_SECTION_NAME: str = "Method,Properties"
"""Named-section name for .stp files."""

PROPERTIES_KEY: str = "ReadOnly"
"""Field key inside the Method,Properties named section."""

FOOTER_PATTERN: re.Pattern[str] = re.compile(r"\* \$\$author=.*\$\$")
"""Regex matching the metadata footer line found at the end of every file."""

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NamedSection:
    """A named section in an HxCfgFile v3 container.

    For **.med** files this section carries the base-64-encoded flowchart
    blob (``ActivityDocument``).  For **.stp** files it carries a simple
    property (``ReadOnly``).  For **.lay** files there are multiple sections
    (DECKLAY, DEVICE, RESOURCES, SYSTEM) with varying field types and counts.

    The ``key`` and ``value`` attributes hold the first field for backward
    compatibility.  Additional fields are stored in ``extra_fields``.
    Use ``all_fields`` to iterate over every (key, value) pair.

    Attributes:
        name:         The compound section name, e.g. ``"ActivityData,ActivityData"``.
        key:          The first field key, e.g. ``"ActivityDocument"`` or ``"ReadOnly"``.
        value:        The first field value.
        field_type:   The binary field type (1 for .med/.stp, varies in .lay).
        extra_fields: Additional (key, value) pairs beyond the first.
    """

    name: str
    key: str
    value: str
    field_type: int = 1
    extra_fields: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def all_fields(self) -> List[Tuple[str, str]]:
        """Return all (key, value) pairs -- the primary field plus extras."""
        result = [(self.key, self.value)]
        result.extend(self.extra_fields)
        return result


@dataclass
class HxParsSection:
    """One HxPars-like parameter section.

    Each section has a *key* (the part after the prefix comma in the binary
    header) and an ordered list of *tokens* (free-form Latin-1 strings).

    Attributes:
        key:     Section key, e.g. ``"Method"``, ``"Instrument"``, etc.
        tokens:  Ordered list of parameter tokens.
        prefix:  Header prefix (``"HxPars"`` for .med/.stp; may vary in .lay).
        version: Section version (3 for .med/.stp; may vary in .lay).
    """

    key: str
    tokens: List[str]
    prefix: str = "HxPars"
    version: int = 3


class HxCfgTextModel:
    """In-memory representation of the full HxCfgFile content.

    This model is the intermediate form used by both the binary parser and
    the text parser / emitter.  It captures:

    * Zero or more :class:`NamedSection` entries (one for .med/.stp files;
      multiple for .lay files).
    * Zero or more :class:`HxParsSection` entries with their tokens.
    * The footer metadata line (``* $$author=... $$``).

    Accepts either ``named_section`` (singular, for backward compat) or
    ``named_sections`` (plural, for .lay files with multiple sections).

    Attributes:
        named_sections:  Ordered list of named sections.
        hxpars_sections: Ordered list of HxPars parameter sections.
        footer_line:     The metadata footer string (without trailing newline).
    """

    def __init__(
        self,
        *,
        named_sections: List[NamedSection] | None = None,
        named_section: NamedSection | None = None,
        hxpars_sections: List[HxParsSection],
        footer_line: str,
    ):
        if named_sections is not None:
            self.named_sections: List[NamedSection] = named_sections
        elif named_section is not None:
            self.named_sections = [named_section]
        else:
            self.named_sections = []
        self.hxpars_sections = hxpars_sections
        self.footer_line = footer_line

    @property
    def named_section(self) -> NamedSection | None:
        """Backward-compat: return the first named section, or None."""
        return self.named_sections[0] if self.named_sections else None

    @property
    def activity_document_b64(self) -> str:
        """Return the ActivityDocument base-64 value, or ``""`` if absent.

        This is a convenience accessor for .med files.  If the named section
        does not exist, or is not an ActivityData section, an empty string is
        returned.
        """
        ns = self.named_section
        if ns and ns.name == ACTIVITY_SECTION_NAME:
            return ns.value
        return ""


# ---------------------------------------------------------------------------
# Text escaping / unescaping helpers
# ---------------------------------------------------------------------------


def _escape_token_for_text(token: str) -> str:
    """Escape a raw Latin-1 token string for inclusion in the text format.

    The text format wraps each token in double quotes and uses backslash
    escapes for special characters:

    * ``\\\\`` for a literal backslash (``0x5C``)
    * ``\\"`` for a literal double-quote (``0x22``)
    * ``\\n`` for line-feed (``0x0A``)
    * ``\\r`` for carriage-return (``0x0D``)
    * ``\\0xHH`` for any other non-printable or non-ASCII byte

    Args:
        token: The raw token string (Latin-1).

    Returns:
        The token wrapped in double quotes with escapes applied.
    """
    out: List[str] = ['"']
    for byte in token.encode("latin1"):
        if byte == 0x5C:          # backslash
            out.append("\\\\")
        elif byte == 0x22:        # double-quote
            out.append('\\"')
        elif byte == 0x0A:        # LF
            out.append("\\n")
        elif byte == 0x0D:        # CR
            out.append("\\r")
        elif byte < 0x20 or byte > 0x7E:  # non-printable / non-ASCII
            out.append(f"\\0x{byte:02x}")
        else:
            out.append(chr(byte))
    out.append('"')
    return "".join(out)


def _unescape_token_from_text(line: str) -> str:
    """Unescape a quoted token from the text format back to a raw Latin-1 string.

    This reverses the escaping applied by :func:`_escape_token_for_text`.

    Recognised escape sequences:

    * ``\\\\`` → ``0x5C``
    * ``\\"``  → ``0x22``
    * ``\\n``  → ``0x0A``
    * ``\\r``  → ``0x0D``
    * ``\\0xHH`` → byte with hex value ``HH``
    * A lone trailing backslash is preserved literally.

    Args:
        line: The full quoted token string (including surrounding quotes).

    Returns:
        The unescaped raw token string (Latin-1).

    Raises:
        ValueError: If the string is not properly double-quoted.
    """
    s = line.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError(f"Invalid quoted token line: {line}")

    inner = s[1:-1]  # strip surrounding quotes
    out = bytearray()
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch != "\\":
            # Ordinary character -- encode as Latin-1 byte
            out.extend(ch.encode("latin1"))
            i += 1
            continue

        # Backslash -- look ahead for escape sequence
        if i + 1 >= len(inner):
            # Trailing backslash at end of string -- keep literal
            out.append(0x5C)
            i += 1
            continue

        nxt = inner[i + 1]
        if nxt == "\\":
            out.append(0x5C)  # literal backslash
            i += 2
        elif nxt == '"':
            out.append(0x22)  # literal double-quote
            i += 2
        elif nxt == "n":
            out.append(0x0A)  # LF
            i += 2
        elif nxt == "r":
            out.append(0x0D)  # CR
            i += 2
        elif nxt == "0" and i + 4 < len(inner) and inner[i + 2] in {"x", "X"}:
            # Hex escape: \0xHH
            hh = inner[i + 3 : i + 5]
            try:
                out.append(int(hh, 16))
                i += 5
            except ValueError:
                # Malformed hex -- treat backslash literally
                out.append(0x5C)
                i += 1
        else:
            # Unrecognised escape -- treat backslash literally
            out.append(0x5C)
            i += 1

    return out.decode("latin1")


# ---------------------------------------------------------------------------
# Binary I/O primitives
# ---------------------------------------------------------------------------


def _read_u16_le(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a little-endian unsigned 16-bit integer from *data* at *pos*.

    Returns:
        A tuple of ``(value, new_pos)`` where *new_pos* is advanced by 2.
    """
    return struct.unpack_from("<H", data, pos)[0], pos + 2


def _read_u32_le(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a little-endian unsigned 32-bit integer from *data* at *pos*.

    Returns:
        A tuple of ``(value, new_pos)`` where *new_pos* is advanced by 4.
    """
    return struct.unpack_from("<I", data, pos)[0], pos + 4


def _write_u16_le(value: int) -> bytes:
    """Pack *value* as a little-endian unsigned 16-bit integer (2 bytes)."""
    return struct.pack("<H", value)


def _write_u32_le(value: int) -> bytes:
    """Pack *value* as a little-endian unsigned 32-bit integer (4 bytes)."""
    return struct.pack("<I", value)


def _read_short_string(data: bytes, pos: int) -> Tuple[str, int]:
    """Read a short-string (1-byte length prefix) from *data* at *pos*.

    Layout::

        [u8 length] [length bytes of Latin-1 payload]

    Args:
        data: The source byte buffer.
        pos:  Current read offset.

    Returns:
        A tuple of ``(decoded_string, new_pos)``.
    """
    length: int = data[pos]
    pos += 1
    value: str = data[pos : pos + length].decode("latin1")
    return value, pos + length


def _write_short_string(value: str) -> bytes:
    """Encode *value* as a short-string (1-byte length prefix + Latin-1 payload).

    Args:
        value: The string to encode (must be ≤ 255 bytes in Latin-1).

    Returns:
        The encoded bytes.

    Raises:
        ValueError: If the encoded payload exceeds 255 bytes.
    """
    raw: bytes = value.encode("latin1")
    if len(raw) > SHORT_STRING_MAX_LENGTH:
        raise ValueError(
            f"short-string exceeds {SHORT_STRING_MAX_LENGTH} bytes "
            f"(got {len(raw)}): {value!r}"
        )
    return bytes([len(raw)]) + raw


def _read_var_string(data: bytes, pos: int) -> Tuple[str, int]:
    """Read a variable-length string from *data* at *pos*.

    Layout (short form, marker 0x00-0xFE)::

        [u8 length] [length bytes of Latin-1 payload]

    Layout (long form, marker 0xFF)::

        [0xFF] [u16-LE length] [length bytes of Latin-1 payload]

    Args:
        data: The source byte buffer.
        pos:  Current read offset.

    Returns:
        A tuple of ``(decoded_string, new_pos)``.
    """
    marker: int = data[pos]
    pos += 1
    if marker == VAR_STRING_LONG_MARKER:
        # Long form: next 2 bytes are u16-LE length
        length, pos = _read_u16_le(data, pos)
    else:
        # Short form: marker byte *is* the length
        length = marker
    value: str = data[pos : pos + length].decode("latin1")
    return value, pos + length


def _write_var_string(value: str) -> bytes:
    """Encode *value* as a variable-length string.

    Strings of 254 bytes or fewer use the compact 1-byte length prefix.
    Strings of 255-65 535 bytes use the ``0xFF`` marker followed by a
    u16-LE length.

    Args:
        value: The string to encode (Latin-1, max 65 535 bytes).

    Returns:
        The encoded bytes.

    Raises:
        ValueError: If the encoded payload exceeds 65 535 bytes.
    """
    raw: bytes = value.encode("latin1")
    n: int = len(raw)
    if n <= 0xFE:
        # Short form -- marker byte is the length itself
        return bytes([n]) + raw
    if n <= VAR_STRING_MAX_LENGTH:
        # Long form -- 0xFF prefix + u16-LE length
        return b"\xFF" + _write_u16_le(n) + raw
    raise ValueError(
        f"var-string too long for current encoding "
        f"(max {VAR_STRING_MAX_LENGTH}, got {n})"
    )


# ---------------------------------------------------------------------------
# Binary → Model parser
# ---------------------------------------------------------------------------


def parse_binary_med(binary_data: bytes) -> HxCfgTextModel:
    """Parse a binary HxCfgFile v3 container (.med or .stp) into a model.

    The parser walks the byte buffer sequentially, validating each field
    against expected constants, and extracts all named sections, HxPars
    sections, and the footer metadata line.

    Args:
        binary_data: The raw bytes of the .med or .stp file.

    Returns:
        An :class:`HxCfgTextModel` representing the file content.

    Raises:
        ValueError: If any structural field has an unexpected value or the
                    footer metadata line cannot be located.
    """
    pos: int = 0

    # --- File header (4 bytes) ---
    # u16-LE version -- must be 3
    version: int
    version, pos = _read_u16_le(binary_data, pos)
    if version != HXCFG_VERSION:
        raise ValueError(
            f"Unsupported HxCfgFile version: {version} (expected {HXCFG_VERSION})"
        )

    # u16-LE type marker -- must be 1
    section_type: int
    section_type, pos = _read_u16_le(binary_data, pos)
    if section_type != HXCFG_TYPE_MARKER:
        raise ValueError(
            f"Unexpected type marker: {section_type} (expected {HXCFG_TYPE_MARKER})"
        )

    # --- Named-section count (4 bytes) ---
    named_section_count: int
    named_section_count, pos = _read_u32_le(binary_data, pos)

    named_section: NamedSection | None = None

    if named_section_count == 1:
        # --- Named section ---
        # Has a named section:
        #   .med → name="ActivityData,ActivityData", key="ActivityDocument"
        #   .stp → name="Method,Properties",        key="ReadOnly"
        section_name: str
        section_name, pos = _read_short_string(binary_data, pos)

        # Field type -- must be 1
        field_type: int
        field_type, pos = _read_u16_le(binary_data, pos)
        if field_type != NAMED_FIELD_TYPE:
            raise ValueError(
                f"Unexpected field type in {section_name}: {field_type} "
                f"(expected {NAMED_FIELD_TYPE})"
            )

        # Field count -- must be 1
        field_count: int
        field_count, pos = _read_u32_le(binary_data, pos)
        if field_count != NAMED_FIELD_COUNT:
            raise ValueError(
                f"Unexpected field count in {section_name}: {field_count} "
                f"(expected {NAMED_FIELD_COUNT})"
            )

        # Field key (short-string) and value (var-string)
        field_key: str
        field_key, pos = _read_short_string(binary_data, pos)
        field_value: str
        field_value, pos = _read_var_string(binary_data, pos)

        named_section = NamedSection(
            name=section_name, key=field_key, value=field_value
        )
    elif named_section_count != 0:
        raise ValueError(
            f"Unexpected named-section count: {named_section_count} "
            f"(expected 0 or 1)"
        )
    # else: count == 0 → no named section (some minimal .stp files)

    # --- HxPars count (1 byte) + 3-byte zero pad ---
    hxpars_count: int = binary_data[pos]
    pos += 1

    # Skip the 3-byte zero pad observed on all known files
    pos += len(HXPARS_PAD_BYTES)

    # --- HxPars sections ---
    hxpars_sections: List[HxParsSection] = []

    for _ in range(hxpars_count):
        # Section header is a short-string like "HxPars,Method"
        raw_name: str
        raw_name, pos = _read_short_string(binary_data, pos)

        if not raw_name.startswith("HxPars,"):
            raise ValueError(f"Unexpected section header: {raw_name!r}")

        section_key: str = raw_name.split(",", 1)[1]

        # HxPars version -- must be 3
        pars_version: int
        pars_version, pos = _read_u16_le(binary_data, pos)
        if pars_version != HXPARS_VERSION:
            raise ValueError(
                f"Unexpected HxPars version in section {section_key}: "
                f"{pars_version} (expected {HXPARS_VERSION})"
            )

        # Token count + tokens
        token_count: int
        token_count, pos = _read_u32_le(binary_data, pos)
        tokens: List[str] = []

        for _ in range(token_count):
            token: str
            token, pos = _read_var_string(binary_data, pos)
            tokens.append(token)

        hxpars_sections.append(HxParsSection(key=section_key, tokens=tokens))

    # --- Footer ---
    # The remainder of the file contains \r\n followed by the footer metadata
    remainder: str = binary_data[pos:].decode("latin1")
    footer_match = FOOTER_PATTERN.search(remainder)
    if not footer_match:
        raise ValueError("Could not locate footer metadata line in binary remainder")
    footer_line: str = footer_match.group(0)

    return HxCfgTextModel(
        named_section=named_section,
        hxpars_sections=hxpars_sections,
        footer_line=footer_line,
    )


# ---------------------------------------------------------------------------
# Model → Text emitter
# ---------------------------------------------------------------------------


def build_text_med(model: HxCfgTextModel) -> str:
    """Build the textual HxCfgFile representation from a model.

    The output mirrors what ``HxCfgFilConverter.exe /t`` produces:

    1. A fixed header (``HxCfgFile,3;`` + ``ConfigIsValid,Y;``).
    2. An optional ``DataDef`` block for the named section.
    3. One ``DataDef,HxPars,3,<key>,`` block per HxPars section,
       each containing its tokens in escaped/quoted form.
    4. The footer metadata line.

    Args:
        model: The in-memory model to serialise.

    Returns:
        The complete text representation as a string (with ``\\r\\n`` line
        endings).
    """
    parts: List[str] = [HEADER_TEXT]

    if model.named_section:
        # Emit the named section as a DataDef block.
        # The section name contains a comma (e.g. "ActivityData,ActivityData")
        # which maps to:  DataDef,ActivityData,1,ActivityData,
        ns: NamedSection = model.named_section
        name_part1, name_part2 = ns.name.split(",", 1)
        parts.append(f"DataDef,{name_part1},1,{name_part2},\r\n")
        parts.append("{\r\n")
        parts.append(f'{ns.key}, "{ns.value}"\r\n')
        parts.append("};\r\n\r\n")

    for section in model.hxpars_sections:
        # Emit each HxPars section as:
        #   DataDef,HxPars,3,<key>,
        #   [
        #   "token1",
        #   "token2",
        #   ...
        #   "tokenN"
        #   ];
        parts.append(f"DataDef,HxPars,3,{section.key},\r\n")
        parts.append("[\r\n")
        for index, token in enumerate(section.tokens):
            parts.append(_escape_token_for_text(token))
            if index < len(section.tokens) - 1:
                parts.append(",\r\n")  # comma after every token except the last
            else:
                parts.append("\r\n")   # last token has no trailing comma
        parts.append("];\r\n\r\n")

    # Footer metadata line + final CRLF
    parts.append(model.footer_line)
    parts.append("\r\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Text → Model parser
# ---------------------------------------------------------------------------


def parse_text_med(text: str) -> HxCfgTextModel:
    """Parse a textual HxCfgFile representation into a model.

    The parser recognises:

    * The fixed ``HxCfgFile,3;`` header.
    * An optional ``DataDef,<Type>,1,<Name>,`` block enclosed in ``{ };``
      (the named section).
    * Any number of ``DataDef,HxPars,3,<Key>,`` blocks enclosed in ``[ ];``
      (HxPars sections), each containing comma-separated quoted tokens.
    * The ``* $$author=…$$`` footer line.

    Args:
        text: The full text content (typically read with ``encoding="latin1"``).

    Returns:
        An :class:`HxCfgTextModel` representing the file content.

    Raises:
        ValueError: If the header is wrong, tokens are malformed, or the
                    footer cannot be found.
    """
    if not text.startswith("HxCfgFile,3;"):
        raise ValueError("Input is not HxCfgFile,3 text")

    # --- Named section ---
    # Pattern: DataDef,<Type>,1,<Name>,\s*{\s*<Key>,\s*"<Value>"\s*};
    named_section: NamedSection | None = None
    named_pattern = re.compile(
        r'DataDef,(\w+),1,(\w+),\s*\{\s*(\w+),\s*"([^"]*?)"\s*\};',
        flags=re.S,
    )
    named_match = named_pattern.search(text)
    if named_match:
        # Reconstruct the composite section name (e.g. "ActivityData,ActivityData")
        section_name: str = f"{named_match.group(1)},{named_match.group(2)}"
        named_section = NamedSection(
            name=section_name,
            key=named_match.group(3),
            value=named_match.group(4),
        )

    # --- HxPars sections ---
    hxpars_sections: List[HxParsSection] = []
    header_pattern = re.compile(r"DataDef,HxPars,3,([^,]+),\s*\[", flags=re.S)
    search_pos: int = 0

    while True:
        section_match = header_pattern.search(text, search_pos)
        if not section_match:
            break

        key: str = section_match.group(1)
        cursor: int = section_match.end()
        tokens: List[str] = []

        while cursor < len(text):
            # Skip whitespace between tokens
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1

            # End-of-section marker
            if text.startswith("];", cursor):
                cursor += 2
                break

            # Each token must start with a double-quote
            if cursor >= len(text) or text[cursor] != '"':
                snippet: str = text[cursor : cursor + 80]
                raise ValueError(
                    f"Unexpected token start in section {key}: {snippet!r}"
                )

            # Walk through the quoted token, handling backslash escapes
            token_start: int = cursor
            cursor += 1  # skip opening quote
            escaped: bool = False
            while cursor < len(text):
                ch: str = text[cursor]
                if escaped:
                    escaped = False
                    cursor += 1
                    continue
                if ch == "\\":
                    escaped = True
                    cursor += 1
                    continue
                if ch == '"':
                    cursor += 1  # skip closing quote
                    break
                cursor += 1

            token_literal: str = text[token_start:cursor]
            tokens.append(_unescape_token_from_text(token_literal))

            # Skip trailing whitespace and comma separator
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor] == ",":
                cursor += 1

        hxpars_sections.append(HxParsSection(key=key, tokens=tokens))
        search_pos = cursor

    # --- Footer ---
    footer_match = FOOTER_PATTERN.search(text)
    if not footer_match:
        raise ValueError("Could not parse footer metadata line")
    footer_line: str = footer_match.group(0)

    return HxCfgTextModel(
        named_section=named_section,
        hxpars_sections=hxpars_sections,
        footer_line=footer_line,
    )


# ---------------------------------------------------------------------------
# Model → Binary builder
# ---------------------------------------------------------------------------


def build_binary_med(model: HxCfgTextModel) -> bytes:
    """Build a binary HxCfgFile v3 container from a model.

    The output is byte-for-byte compatible with the original Hamilton
    binary format (assuming the model was parsed from such a file).

    Args:
        model: The in-memory model to serialise.

    Returns:
        The complete binary content for a .med or .stp file.

    Raises:
        ValueError: If any string exceeds the allowed encoding limits or
                    there are more than 255 HxPars sections.
    """
    out = bytearray()

    # --- File header ---
    out += _write_u16_le(HXCFG_VERSION)       # version = 3
    out += _write_u16_le(HXCFG_TYPE_MARKER)    # type marker = 1

    # --- Named section ---
    if model.named_section:
        ns: NamedSection = model.named_section
        out += _write_u32_le(1)                # named-section count = 1
        out += _write_short_string(ns.name)    # section name
        out += _write_u16_le(NAMED_FIELD_TYPE) # field type = 1
        out += _write_u32_le(NAMED_FIELD_COUNT)  # field count = 1
        out += _write_short_string(ns.key)     # field key
        out += _write_var_string(ns.value)     # field value
    else:
        out += _write_u32_le(0)                # named-section count = 0

    # --- HxPars count + padding ---
    if len(model.hxpars_sections) > SHORT_STRING_MAX_LENGTH:
        raise ValueError(
            f"HxPars section count ({len(model.hxpars_sections)}) exceeds "
            f"single-byte capacity ({SHORT_STRING_MAX_LENGTH})"
        )
    out.append(len(model.hxpars_sections))     # u8 count
    out += HXPARS_PAD_BYTES                     # 3-byte zero pad

    # --- HxPars sections ---
    for section in model.hxpars_sections:
        name: str = f"HxPars,{section.key}"
        out += _write_short_string(name)        # section header
        out += _write_u16_le(HXPARS_VERSION)    # HxPars version = 3
        out += _write_u32_le(len(section.tokens))  # token count
        for token in section.tokens:
            out += _write_var_string(token)      # each token

    # --- Footer ---
    out += b"\r\n"                              # CRLF before footer
    out += model.footer_line.encode("latin1")   # metadata line

    return bytes(out)


# ---------------------------------------------------------------------------
# File-level convenience functions
# ---------------------------------------------------------------------------


def binary_to_text_file(input_path: Path, output_path: Path) -> None:
    """Convert a binary .med/.stp file to its textual representation using the CLI converter.

    Args:
        input_path:  Path to the source binary file.
        output_path: Path where the text output will be written.
    """
    import subprocess
    converter = Path(r"C:\Program Files (x86)\Hamilton\7 - Created Utilities and Scripts\Programatic Method and Venus File Generation Playground\tooling\Hamilton-Binary-ASCII-Converter\Hamilton-Binary-ASCII-Converter.exe")
    if not converter.exists():
        raise FileNotFoundError(f"Converter not found at {converter}")
    
    subprocess.run([str(converter), "--ascii", str(input_path), "--output", str(output_path), "--no-backup"], check=True)


def text_to_binary_file(input_path: Path, output_path: Path) -> None:
    """Convert a textual .med/.stp representation back to binary using the CLI converter.

    Args:
        input_path:  Path to the source text file.
        output_path: Path where the binary output will be written.
    """
    import subprocess
    converter = Path(r"C:\Program Files (x86)\Hamilton\7 - Created Utilities and Scripts\Programatic Method and Venus File Generation Playground\tooling\Hamilton-Binary-ASCII-Converter\Hamilton-Binary-ASCII-Converter.exe")
    if not converter.exists():
        raise FileNotFoundError(f"Converter not found at {converter}")
    
    subprocess.run([str(converter), "--binary", str(input_path), "--output", str(output_path), "--no-backup"], check=True)


# ---------------------------------------------------------------------------
# Dump -- human-readable structural summary
# ---------------------------------------------------------------------------


def dump_binary_structure(binary_data: bytes) -> str:
    """Return a human-readable summary of the binary HxCfgFile v3 structure.

    This is useful for debugging and inspection.  It walks the binary data
    and reports every field with its offset, type, and value (or a truncated
    preview for long values).

    Args:
        binary_data: The raw bytes of the .med or .stp file.

    Returns:
        A multi-line string suitable for printing to the console.
    """
    lines: List[str] = []
    total_len: int = len(binary_data)
    lines.append(f"File size: {total_len:,} bytes ({total_len:#x})")
    lines.append("")

    pos: int = 0

    # --- Header ---
    version, pos = _read_u16_le(binary_data, pos)
    lines.append(f"[0x{pos - 2:04X}] Version:      {version}")

    type_marker, pos = _read_u16_le(binary_data, pos)
    lines.append(f"[0x{pos - 2:04X}] Type marker:  {type_marker}")

    # --- Named-section count ---
    named_count, pos = _read_u32_le(binary_data, pos)
    lines.append(f"[0x{pos - 4:04X}] Named-section count: {named_count}")
    lines.append("")

    if named_count == 1:
        sec_start: int = pos
        section_name, pos = _read_short_string(binary_data, pos)
        lines.append(f"[0x{sec_start:04X}] Named section name: {section_name!r}")

        field_type, pos = _read_u16_le(binary_data, pos)
        lines.append(f"[0x{pos - 2:04X}]   Field type:  {field_type}")

        field_count, pos = _read_u32_le(binary_data, pos)
        lines.append(f"[0x{pos - 4:04X}]   Field count: {field_count}")

        key_start: int = pos
        field_key, pos = _read_short_string(binary_data, pos)
        lines.append(f"[0x{key_start:04X}]   Field key:   {field_key!r}")

        val_start: int = pos
        field_value, pos = _read_var_string(binary_data, pos)
        preview: str = field_value[:80] + ("…" if len(field_value) > 80 else "")
        lines.append(
            f"[0x{val_start:04X}]   Field value:  ({len(field_value):,} chars) "
            f"{preview!r}"
        )
        lines.append("")
    elif named_count > 1:
        lines.append(f"  WARNING: unexpected named-section count {named_count}")
        lines.append("")

    # --- HxPars count ---
    hxpars_count: int = binary_data[pos]
    lines.append(f"[0x{pos:04X}] HxPars section count: {hxpars_count}")
    pos += 1

    # 3-byte pad
    pad: bytes = binary_data[pos : pos + 3]
    lines.append(f"[0x{pos:04X}] Padding: {pad.hex()}")
    pos += 3
    lines.append("")

    # --- HxPars sections ---
    for idx in range(hxpars_count):
        sec_off: int = pos
        raw_name, pos = _read_short_string(binary_data, pos)
        lines.append(f"[0x{sec_off:04X}] HxPars section #{idx + 1}: {raw_name!r}")

        pars_version, pos = _read_u16_le(binary_data, pos)
        lines.append(f"         Version: {pars_version}")

        token_count, pos = _read_u32_le(binary_data, pos)
        lines.append(f"         Tokens:  {token_count:,}")

        # Summarise tokens -- show byte sizes
        total_token_bytes: int = 0
        for t_idx in range(token_count):
            t_start: int = pos
            _token, pos = _read_var_string(binary_data, pos)
            total_token_bytes += pos - t_start

        lines.append(f"         Token data: {total_token_bytes:,} bytes")
        lines.append("")

    # --- Footer remainder ---
    remainder_start: int = pos
    remainder: str = binary_data[pos:].decode("latin1")
    footer_match = FOOTER_PATTERN.search(remainder)
    if footer_match:
        lines.append(f"[0x{remainder_start:04X}] Footer ({len(remainder)} bytes):")
        # Show the footer line (may be long -- truncate for display)
        footer_preview: str = footer_match.group(0)
        if len(footer_preview) > 120:
            footer_preview = footer_preview[:120] + "…"
        lines.append(f"         {footer_preview}")
    else:
        lines.append(f"[0x{remainder_start:04X}] Remainder ({len(remainder)} bytes, no footer found)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Roundtrip verification
# ---------------------------------------------------------------------------


def roundtrip_verify(
    input_path: Path, output_path: Path | None = None
) -> Tuple[bool, str]:
    """Perform a binary → text → binary roundtrip and compare results.

    This reads a binary file, converts it to text and back, then checks
    whether the re-encoded binary is byte-for-byte identical to the
    original.

    Args:
        input_path:  Path to the original binary .med/.stp file.
        output_path: Optional path to write the re-encoded binary.
                     If ``None``, a temporary comparison is done in memory.

    Returns:
        A tuple of ``(success, message)`` where *success* is ``True`` when
        the roundtrip produces identical bytes, and *message* contains a
        human-readable summary.
    """
    original_bytes: bytes = input_path.read_bytes()

    # Binary → model → text
    model: HxCfgTextModel = parse_binary_med(original_bytes)
    text: str = build_text_med(model)

    # Text → model → binary
    model2: HxCfgTextModel = parse_text_med(text)
    rebuilt_bytes: bytes = build_binary_med(model2)

    # Optionally write the re-encoded file
    if output_path is not None:
        output_path.write_bytes(rebuilt_bytes)

    # Compare
    if original_bytes == rebuilt_bytes:
        msg: str = (
            f"PASS: roundtrip OK -- {len(original_bytes):,} bytes identical.\n"
            f"  Named section: {'yes' if model.named_section else 'no'}\n"
            f"  HxPars sections: {len(model.hxpars_sections)}\n"
            f"  Total tokens: "
            f"{sum(len(s.tokens) for s in model.hxpars_sections):,}"
        )
        return True, msg
    else:
        # Find first differing byte for diagnostics
        min_len: int = min(len(original_bytes), len(rebuilt_bytes))
        first_diff: int = min_len  # default if one is a prefix of the other
        for i in range(min_len):
            if original_bytes[i] != rebuilt_bytes[i]:
                first_diff = i
                break
        msg = (
            f"FAIL: roundtrip mismatch.\n"
            f"  Original size:  {len(original_bytes):,} bytes\n"
            f"  Rebuilt size:   {len(rebuilt_bytes):,} bytes\n"
            f"  First diff at:  offset 0x{first_diff:04X} ({first_diff})\n"
            f"    original: 0x{original_bytes[first_diff]:02X}\n"
            f"    rebuilt:  0x{rebuilt_bytes[first_diff]:02X}"
        )
        return False, msg


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Parse CLI arguments and dispatch to the requested subcommand.

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Pure-Python HxCfgFile v3 codec for Hamilton .med and .stp files.\n\n"
            "Converts between the binary HxCfgFile v3 container format and\n"
            "the human-readable text representation used by HxCfgFilConverter /t."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # -- to-text --
    p_to_text = sub.add_parser(
        "to-text",
        help="Convert a binary .med/.stp to its text representation.",
    )
    p_to_text.add_argument(
        "input", type=Path, help="Path to the binary .med or .stp file."
    )
    p_to_text.add_argument(
        "output", type=Path, help="Path for the output text file."
    )

    # -- to-binary --
    p_to_bin = sub.add_parser(
        "to-binary",
        help="Convert a text representation back to a binary .med/.stp.",
    )
    p_to_bin.add_argument(
        "input", type=Path, help="Path to the text file."
    )
    p_to_bin.add_argument(
        "output", type=Path, help="Path for the output binary file."
    )

    # -- roundtrip --
    p_roundtrip = sub.add_parser(
        "roundtrip",
        help="Binary → text → binary roundtrip verification.",
    )
    p_roundtrip.add_argument(
        "input", type=Path, help="Path to the original binary .med or .stp file."
    )
    p_roundtrip.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Optional path to write the re-encoded binary.",
    )

    # -- dump --
    p_dump = sub.add_parser(
        "dump",
        help="Print a human-readable structural summary of a binary file.",
    )
    p_dump.add_argument(
        "input", type=Path, help="Path to the binary .med or .stp file."
    )

    args = parser.parse_args()

    if args.cmd == "to-text":
        binary_to_text_file(args.input, args.output)
        print(f"Wrote text to {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "to-binary":
        text_to_binary_file(args.input, args.output)
        print(f"Wrote binary to {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "roundtrip":
        success, message = roundtrip_verify(args.input, args.output)
        print(message)
        if args.output and success:
            print(f"Re-encoded binary written to {args.output}", file=sys.stderr)
        return 0 if success else 1

    if args.cmd == "dump":
        data: bytes = args.input.read_bytes()
        print(dump_binary_structure(data))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
