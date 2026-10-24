#!/usr/bin/env python3
"""
stp_generator.py -- Hamilton .stp (Step Parameters) File Generator

Full lifecycle management of .stp companion files for Hamilton HSL methods.
Ported from the TypeScript implementation in ``src/medGenerator.ts``.

The ``.stp`` file is a binary HxCfgFile v3 container that stores device-specific
step parameters (e.g., TipPickUp sequence names, Aspirate volumes, channel
patterns). Each device step in a method gets a section in the ``.stp`` keyed by
its instance GUID.

Architecture
============
1. Parse the ``.hsl`` AND ``.sub`` files to extract block markers
2. Identify device step GUIDs and their CLSIDs
3. Read existing ``.stp`` sections (to preserve user-configured parameters)
4. Build new ``.stp`` text with default sections for new steps
5. Convert text → binary using the pure-Python hxcfgfile_codec
6. Transactional binary write (tmp → bak → replace)

Imports
=======
This module depends on sibling modules within the ``standalone_med_tools``
package:

- ``.block_markers``   -- parsing, CLSID registry, GUID generation
- ``.hxcfgfile_codec`` -- binary ↔ text conversion
- ``.checksum``        -- CRC-32 computation and checksum footer generation

CLI Usage
=========
::

    python -m standalone_med_tools.stp_generator sync  MyMethod.hsl
    python -m standalone_med_tools.stp_generator sync  MyMethod.hsl  --stp MyMethod.stp

Requirements: Python 3.8+ (no external dependencies)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import time as _time
from datetime import datetime
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Set,
)

from .block_markers import (
    StepBlockMarker,
    parse_block_markers,
    has_step_block_markers,
    extract_device_call_from_code,
)
from .checksum import (
    generate_checksum_line,
    update_checksum_in_file,
)
from .hxcfgfile_codec import (
    text_to_binary_file,
    binary_to_text_file,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════════
# Device Step Constants
# ═════════════════════════════════════════════════════════════════════════════════

DEVICE_CLSIDS: Set[str] = {
    "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",  # Initialize
    "{54114402-7FA2-11D3-AD85-0004ACB1DCB2}",  # LoadCarrier
    "{54114400-7FA2-11D3-AD85-0004ACB1DCB2}",  # UnloadCarrier
    "{541143FA-7FA2-11D3-AD85-0004ACB1DCB2}",  # TipPickUp
    "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",  # Aspirate
    "{541143F8-7FA2-11D3-AD85-0004ACB1DCB2}",  # Dispense
    "{541143FC-7FA2-11D3-AD85-0004ACB1DCB2}",  # TipEject
    "{EA251BFB-66DE-48D1-83E5-6884B4DD8D11}",  # MoveAutoLoad
    "{9FB6DFE0-4132-4d09-B502-98C722734D4C}",  # GetLastLiquidLevel
}
"""Set of bare ML_STAR device-specific CLSIDs that require ``.stp`` entries."""

DEVICE_STEP_NAMES: Dict[str, str] = {
    "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}": "Initialize",
    "{54114402-7FA2-11D3-AD85-0004ACB1DCB2}": "LoadCarrier",
    "{54114400-7FA2-11D3-AD85-0004ACB1DCB2}": "UnloadCarrier",
    "{541143FA-7FA2-11D3-AD85-0004ACB1DCB2}": "TipPickUp",
    "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}": "Aspirate",
    "{541143F8-7FA2-11D3-AD85-0004ACB1DCB2}": "Dispense",
    "{541143FC-7FA2-11D3-AD85-0004ACB1DCB2}": "TipEject",
    "{EA251BFB-66DE-48D1-83E5-6884B4DD8D11}": "MoveAutoLoad",
    "{9FB6DFE0-4132-4d09-B502-98C722734D4C}": "GetLastLiquidLevel",
}
"""Map of bare CLSID → friendly device step name for the ``.stp`` ``StepName`` field."""

# CLSIDs grouped by category for step-type-specific field generation
_CLSID_INITIALIZE: str = "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}"
_CLSID_LOAD_CARRIER: str = "{54114402-7FA2-11D3-AD85-0004ACB1DCB2}"
_CLSID_UNLOAD_CARRIER: str = "{54114400-7FA2-11D3-AD85-0004ACB1DCB2}"
_CLSID_TIP_PICKUP: str = "{541143FA-7FA2-11D3-AD85-0004ACB1DCB2}"
_CLSID_ASPIRATE: str = "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}"
_CLSID_DISPENSE: str = "{541143F8-7FA2-11D3-AD85-0004ACB1DCB2}"
_CLSID_TIP_EJECT: str = "{541143FC-7FA2-11D3-AD85-0004ACB1DCB2}"

_CHANNEL_STEPS: Set[str] = {_CLSID_TIP_PICKUP, _CLSID_ASPIRATE, _CLSID_DISPENSE, _CLSID_TIP_EJECT}
"""Device steps that have SequenceObject/SequenceName and ChannelPattern fields."""

_LIQUID_STEPS: Set[str] = {_CLSID_ASPIRATE, _CLSID_DISPENSE}
"""Device steps that have the TipType field."""

_CARRIER_STEPS: Set[str] = {_CLSID_LOAD_CARRIER, _CLSID_UNLOAD_CARRIER}
"""Device steps that optionally have a SequenceName field."""

_TEMP_COUNTER: int = 0
"""Module-level counter for unique temp-file suffixes."""


# ═════════════════════════════════════════════════════════════════════════════════
# Utility Helpers
# ═════════════════════════════════════════════════════════════════════════════════

def bare_clsid(clsid: str) -> str:
    """Extract the bare CLSID from a potentially device-prefixed CLSID.

    Device steps carry a prefix like ``ML_STAR:`` before the actual CLSID.
    This function strips that prefix.

    Args:
        clsid: Full CLSID, possibly prefixed (e.g. ``"ML_STAR:{...}"``).

    Returns:
        Bare CLSID in ``{…}`` format.

    Examples:
        >>> bare_clsid("ML_STAR:{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}")
        '{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}'
        >>> bare_clsid("{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}")
        '{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}'
    """
    if ":" in clsid:
        return clsid.split(":")[1]
    return clsid


def is_device_step_clsid(clsid: str) -> bool:
    """Check if a CLSID represents a device step that needs an ``.stp`` entry.

    Args:
        clsid: Possibly prefixed CLSID (e.g. ``"ML_STAR:{...}"`` or ``"{...}"``).

    Returns:
        ``True`` if the bare CLSID is a known ML_STAR device step.

    Examples:
        >>> is_device_step_clsid("ML_STAR:{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}")
        True
        >>> is_device_step_clsid("{00000000-0000-0000-0000-000000000000}")
        False
    """
    return bare_clsid(clsid) in DEVICE_CLSIDS


def _unique_temp_suffix() -> str:
    """Generate a unique suffix for temporary file names.

    Returns:
        A string like ``"1234567890123"`` based on current time and a counter.
    """
    global _TEMP_COUNTER
    _TEMP_COUNTER += 1
    return f"{int(_time.time() * 1000)}_{_TEMP_COUNTER}"


def _safe_unlink(path: str) -> None:
    """Delete a file without raising if it doesn't exist.

    Args:
        path: File path to delete.
    """
    try:
        os.unlink(path)
    except OSError:
        pass


# ═════════════════════════════════════════════════════════════════════════════════
# Error Recovery Builders
# ═════════════════════════════════════════════════════════════════════════════════

def build_error_entry(
    error_number: int,
    error_desc: int,
    error_title: int,
    infinite: bool,
    recoveries: List[Dict[str, object]],
) -> List[str]:
    """Build a single error entry for the ``.stp`` ``Errors`` section.

    Each error entry contains error metadata (number, description, title,
    retry behaviour) and a list of recovery options (Retry, Abort, Cancel, etc.)

    Args:
        error_number: Error ID number (e.g. 3 for hardware, 999 for unknown).
        error_desc: Resource ID for the error description string.
        error_title: Resource ID for the error title string.
        infinite: Whether the error allows infinite retries.
        recoveries: List of recovery option dicts, each with keys:

            - ``"id"`` (int): Recovery option ID.
            - ``"desc"`` (int): Resource ID for recovery description.
            - ``"title"`` (int): Resource ID for recovery title.
            - ``"is_default"`` (bool): Whether this is the default recovery.

    Returns:
        List of token strings for this error entry.

    Examples:
        >>> entry = build_error_entry(3, 375, 374, True, [
        ...     {"id": 3, "desc": 419, "title": 418, "is_default": True},
        ... ])
        >>> entry[0]
        '"(3"'
    """
    lines: List[str] = []
    nbr_tag = str(error_number)
    if error_number == 999:
        nbr_tag = "4"
    elif error_number == 3:
        nbr_tag = "3"

    lines.append(f'"({nbr_tag}"')
    lines.extend(['"3RepeatCount"', '"0"'])
    lines.extend(['"3UseDefault"', '"1"'])
    lines.extend(['"3Timeout"', '"0"'])
    lines.extend(['"1ErrorSound"', '""'])
    lines.extend(['"3AddRecovery"', '"0"'])
    lines.extend(['"3Infinite"', f'"{1 if infinite else 0}"'])
    lines.extend(['"3ErrorDescription"', f'"{error_desc}"'])
    lines.extend(['"3ErrorNumber"', f'"{error_number}"'])
    lines.append('"(Recoveries"')
    for rec in recoveries:
        lines.append(f'"({rec["id"]}"')
        lines.extend(['"3RecoveryVisible"', '"1"'])
        lines.extend(['"3RecoveryDescription"', f'"{rec["desc"]}"'])
        lines.extend(['"3RecoveryFlag"', '"1"'])
        lines.extend(['"3RecoveryTitle"', f'"{rec["title"]}"'])
        lines.extend(['"3RecoveryDefault"', f'"{1 if rec["is_default"] else 0}"'])
        lines.append('")"')
    lines.append('")"')
    lines.extend(['"3NbrOfRecovery"', f'"{len(recoveries)}"'])
    lines.extend(['"3ErrorTitle"', f'"{error_title}"'])
    lines.append('")"')
    return lines


def get_default_error_recoveries(bare: str) -> List[str]:
    """Generate default error recovery entries for a device step.

    All device steps share a standard set of four error entries:

    - **Error 3** -- Hardware error (infinite retry, default: Retry)
    - **Error 999** -- Unknown error (no infinite retry, default: Abort)
    - **Error 10** -- Position not found (infinite retry, default: Retry)
    - **Error 2** -- Not initialized (infinite retry, default: Retry)

    Args:
        bare: Bare CLSID (without device prefix) identifying the step type.

    Returns:
        Flat list of token strings for the ``(Errors …)`` subsection.

    Examples:
        >>> tokens = get_default_error_recoveries(
        ...     "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}")
        >>> '"(3"' in tokens
        True
    """
    lines: List[str] = []

    # Error 3: Hardware error (common to all device steps)
    lines.extend(build_error_entry(3, 375, 374, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 999: Unknown (common to all device steps)
    lines.extend(build_error_entry(999, 1689, 1688, False, [
        {"id": 3, "desc": 421, "title": 420, "is_default": False},
        {"id": 4, "desc": 429, "title": 428, "is_default": False},
        {"id": 1, "desc": 371, "title": 370, "is_default": True},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 10: Position not found (common to most steps)
    lines.extend(build_error_entry(10, 391, 390, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 2: Not initialized
    lines.extend(build_error_entry(2, 373, 372, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    return lines


# ═════════════════════════════════════════════════════════════════════════════════
# STP Section Parsing
# ═════════════════════════════════════════════════════════════════════════════════

def parse_existing_stp_sections(text: str) -> Dict[str, str]:
    """Parse an existing ``.stp`` text file to extract sections by GUID.

    This is used to preserve user-configured parameters when regenerating
    the ``.stp`` file.  Sections that the user has already configured (e.g.,
    selecting specific tip types, changing channel patterns) are kept as-is.

    A special key ``"__Properties__"`` holds the
    ``DataDef,Method,1,Properties`` section if present.

    Args:
        text: Full ``.stp`` text content (decoded from binary).

    Returns:
        Dict mapping instance GUID → complete section text.  The special
        key ``"__Properties__"`` maps to the Properties section.

    Examples:
        >>> text = 'DataDef,HxPars,3,my_guid,\\n[\\n");\\n];'
        >>> sections = parse_existing_stp_sections(text)
        >>> 'my_guid' in sections
        True
    """
    # The public STP-only entry point uses the same quote-aware, fail-closed
    # preservation contract as MED sync. Keep original vendor section slices.
    from .med_generator import _parse_existing_stp_sections
    return _parse_existing_stp_sections(text)


# ═════════════════════════════════════════════════════════════════════════════════
# Default STP Section Builder
# ═════════════════════════════════════════════════════════════════════════════════

def build_default_stp_section(guid: str, clsid: str, code: str) -> str:
    """Generate a default ``.stp`` section for a device step GUID.

    The structure varies slightly by step type:

    - **Initialize** has ``AlwaysInitialize``
    - **TipPickUp / Aspirate / Dispense / TipEject** have ``SequenceObject``,
      ``SequenceName``, and ``ChannelPattern``
    - **Aspirate / Dispense** additionally have ``TipType``
    - **TipEject** additionally has ``UseDefaultWaste``
    - **LoadCarrier / UnloadCarrier** optionally have ``SequenceName``

    All step types share an error/recovery tree, channel-level defaults
    (``-534183936`` section with 8 channels), ``ParsCommandVersion``,
    and ``Timestamp``.

    For new steps, this generates a minimal valid section that allows the
    Method Editor to open the step's properties dialog for user configuration.

    Args:
        guid: Step instance GUID (Hamilton underscore format).
        clsid: Full CLSID (possibly with device prefix, e.g. ``"ML_STAR:{...}"``).
        code: HSL source code for the step (used to extract sequence names).

    Returns:
        Complete ``.stp`` section text (``DataDef,HxPars,3,<guid>,...];``).

    Examples:
        >>> section = build_default_stp_section(
        ...     "my_guid",
        ...     "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",
        ...     "ML_STAR.Initialize(...);"
        ... )
        >>> 'DataDef,HxPars,3,my_guid,' in section
        True
        >>> '"3AlwaysInitialize"' in section
        True
    """
    from .med_generator import _escape_string

    bare = bare_clsid(clsid)
    step_name = DEVICE_STEP_NAMES.get(bare, "Unknown")
    lines: List[str] = []

    lines.append(f"DataDef,HxPars,3,{guid},")
    lines.append("[")

    # CommandStepFileGuid -- usually same as instance GUID for simple steps
    lines.append('"1CommandStepFileGuid"')
    lines.append(f'"{_escape_string(guid)}"')

    # Try to extract sequence name from code for steps that use sequences
    seq_m = re.search(r'ML_STAR\.(\w+)', code)
    sequence_name = f"ML_STAR.{seq_m.group(1)}" if seq_m else ""

    # ── Step-type-specific fields ──

    if bare == _CLSID_INITIALIZE:
        # Initialize: AlwaysInitialize flag
        lines.extend(['"3AlwaysInitialize"', '"0"'])

    if bare in _CHANNEL_STEPS:
        # TipPickUp, Aspirate, Dispense, TipEject -- sequence and channel pattern
        if sequence_name:
            lines.extend(['"1SequenceObject"', f'"{sequence_name}"'])
            lines.extend(['"1SequenceName"', f'"{sequence_name}"'])
        else:
            lines.extend(['"1SequenceName"', '""'])
        lines.extend(['"1ChannelPattern"', '"11111111"'])

    if bare in _LIQUID_STEPS:
        # Aspirate/Dispense -- TipType (5 = 1000µl High Volume with Filter)
        lines.extend(['"3TipType"', '"5"'])

    if bare == _CLSID_TIP_EJECT:
        # TipEject -- UseDefaultWaste flag
        lines.extend(['"3UseDefaultWaste"', '"1"'])

    if bare in _CARRIER_STEPS:
        # LoadCarrier / UnloadCarrier -- optional SequenceName
        if sequence_name:
            lines.extend(['"1SequenceName"', f'"{sequence_name}"'])

    # ── Common fields ──

    # NbrOfErrors -- standard 4-error table
    lines.extend(['"3NbrOfErrors"', '"4"'])

    # Error recovery table
    lines.append('"(Errors"')
    lines.extend(get_default_error_recoveries(bare))
    lines.append('")"')

    # Sequence counting and optimization
    lines.extend(['"3SequenceCounting"', '"0"'])
    lines.extend(['"3Optimizing channel use"', '"1"'])

    # Step name
    lines.extend(['"1StepName"', f'"{step_name}"'])

    # Channel-level defaults (-534183936 section with 8 channels)
    lines.append('"(-534183936"')
    for ch in range(1, 9):
        lines.append(f'"({ch}"')
        lines.extend(['"3-534183876"', '"1"'])
        lines.append('")"')
    lines.append('")"')

    # ParsCommandVersion and Timestamp
    lines.extend(['"3ParsCommandVersion"', '"2"'])
    lines.append('"1Timestamp"')
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f'"{ts}"')

    lines.append('")"')
    lines.append("];")

    from .med_generator import _format_hxpars_section
    return _format_hxpars_section(lines)


# ═════════════════════════════════════════════════════════════════════════════════
# Complete STP Text Builder
# ═════════════════════════════════════════════════════════════════════════════════

def build_stp_text(
    device_steps: Dict[str, Dict[str, str]],
    existing_sections: Optional[Dict[str, str]] = None,
    author: Optional[str] = None,
) -> str:
    """Build the complete ``.stp`` text file content.

    Generates the full text representation of an ``.stp`` file including:

    - ``HxCfgFile,3;`` header and ``ConfigIsValid,Y;`` flag
    - ``DataDef,Method,1,Properties`` section (preserved or default)
    - One ``DataDef,HxPars,3,<guid>`` section per device step
    - ``AuditTrailData`` section
    - CRC-32 checksum footer

    Existing sections are preserved by GUID to retain user-configured
    parameters (e.g. custom tip types, changed channel patterns).

    Args:
        device_steps: Dict mapping instance GUID → ``{"clsid": ..., "code": ...}``
            for each device step to include.
        existing_sections: Previously parsed ``.stp`` sections to preserve.
            If ``None``, all sections are generated fresh.
        author: Author name for the checksum footer.  Defaults to the
            ``USERNAME`` environment variable.

    Returns:
        Complete ``.stp`` text content ready for binary conversion.

    Examples:
        >>> text = build_stp_text({
        ...     "my_guid": {
        ...         "clsid": "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",
        ...         "code": "ML_STAR.Initialize(...);",
        ...     },
        ... })
        >>> 'HxCfgFile,3;' in text
        True
    """
    if author is None:
        author = os.environ.get("USERNAME", "admin")
    if existing_sections is None:
        existing_sections = {}
    if existing_sections:
        validated = parse_existing_stp_sections("\n".join(existing_sections.values()))
        if validated != existing_sections:
            raise ValueError("Existing STP mapping does not match its section keys")

    sections: List[str] = []

    # Header
    sections.append("HxCfgFile,3;")
    sections.append("")
    sections.append("ConfigIsValid,Y;")
    sections.append("")

    # Properties section
    existing_props = existing_sections.get("__Properties__")
    if existing_props:
        sections.append(existing_props)
    else:
        sections.append("DataDef,Method,1,Properties,")
        sections.append("{")
        sections.append('ReadOnly, "0"')
        sections.append("};")
    sections.append("")

    # Device step sections -- sorted alphabetically by GUID for determinism
    for guid in sorted(device_steps.keys()):
        step = device_steps[guid]
        existing = existing_sections.get(guid)
        if existing:
            # Preserve user-configured section
            sections.append(existing)
        else:
            # Generate a new default section
            sections.append(build_default_stp_section(guid, step["clsid"], step["code"]))
        sections.append("")

    # AuditTrailData section
    sections.append("DataDef,HxPars,3,AuditTrailData,")
    sections.append("[")
    sections.append('")"')
    sections.append("];")
    sections.append("")

    # Join and add checksum footer
    content = "\r\n".join(sections) + "\r\n"
    footer = generate_checksum_line(content, author=author, valid=0, prefix_char="*")
    return content + footer + "\r\n"


# ═════════════════════════════════════════════════════════════════════════════════
# Transactional Binary Write
# ═════════════════════════════════════════════════════════════════════════════════

def _transactional_binary_write(final_path: str, text_content: str) -> None:
    """Transactional binary write: temp → convert → copy → cleanup.

    Writes the text content to a temporary file, converts it to binary
    using the HxCfgFile v3 codec, then atomically replaces the target file.
    A backup (``.bak``) is created before replacement and removed on success.

    Args:
        final_path: Destination path for the binary ``.stp`` file.
        text_content: Complete ``.stp`` text representation to convert.

    Raises:
        OSError: If conversion or file operations fail.
    """
    dir_name = os.path.dirname(final_path) or "."
    ext = os.path.splitext(final_path)[1]
    tmp_name = f"~hxsync_{int(_time.time() * 1000)}{ext}"
    tmp_path = os.path.join(dir_name, tmp_name)
    bak_path = final_path + ".bak"

    try:
        # Step 1: Write text to temp file
        Path(tmp_path).write_text(text_content, encoding="latin1", newline="")

        # Step 2: Convert text → binary in-place
        text_to_binary_file(Path(tmp_path), Path(tmp_path))

        # Step 3: Verify temp file after conversion
        if not os.path.exists(tmp_path):
            raise OSError(
                f"Binary conversion removed temp file unexpectedly: {tmp_name}"
            )

        # Step 4: Backup existing target
        if os.path.exists(final_path):
            _safe_unlink(bak_path)
            try:
                shutil.copy2(final_path, bak_path)
            except OSError:
                pass  # Non-fatal

        # Step 5: Copy temp → target
        shutil.copy2(tmp_path, final_path)

        # Step 6: Clean up
        _safe_unlink(tmp_path)
        _safe_unlink(bak_path)

    except Exception:
        _safe_unlink(tmp_path)
        # Rollback from backup if target was lost
        if os.path.exists(bak_path) and not os.path.exists(final_path):
            try:
                shutil.copy2(bak_path, final_path)
            except OSError:
                pass
        _safe_unlink(bak_path)
        raise


# ═════════════════════════════════════════════════════════════════════════════════
# Full STP Sync Pipeline
# ═════════════════════════════════════════════════════════════════════════════════

def sync_stp_from_hsl(
    hsl_path: str,
    stp_path: Optional[str] = None,
) -> None:
    """Synchronize the ``.stp`` file from ``.hsl`` + ``.sub`` block markers.

    Full sync pipeline:

    1. Parse ``.hsl`` and ``.sub`` files for block markers
    2. Collect device step GUIDs and their CLSIDs/code
    3. Read existing ``.stp`` sections (to preserve user-configured parameters)
    4. Build new ``.stp`` text with default sections for new steps
    5. Transactional binary write (tmp → bak → replace)

    For each device step GUID found in the code:

    - If a section already exists in the ``.stp``, preserve it (user may have
      configured non-default parameters via the Method Editor dialog)
    - If not, generate a default section with minimal valid parameters

    Device step GUIDs not found in code are removed (orphan cleanup).

    The function also performs a belt-and-suspenders check: if the actual code
    inside a block references a DIFFERENT instance GUID than the block marker
    comment (e.g. user edited the code but reconciliation hasn't run yet),
    both GUIDs get ``.stp`` entries.

    Args:
        hsl_path: Path to the ``.hsl`` source file.
        stp_path: Path for the output ``.stp`` file.  Defaults to the same
            base name with ``.stp`` extension.

    Raises:
        OSError: If the ``.hsl`` file cannot be read or the ``.stp`` file
            cannot be written.
    """
    if not stp_path:
        stp_path = re.sub(r'\.hsl$', '.stp', hsl_path, flags=re.IGNORECASE)

    hsl_content = Path(hsl_path).read_text(encoding="utf-8")
    all_markers = list(parse_block_markers(hsl_content))

    # Also parse companion .sub file
    sub_path = re.sub(r'\.hsl$', '.sub', hsl_path, flags=re.IGNORECASE)
    if os.path.exists(sub_path):
        sub_content = Path(sub_path).read_text(encoding="utf-8")
        all_markers.extend(parse_block_markers(sub_content))

    # Collect all device step GUIDs and their code/CLSID
    device_steps: Dict[str, Dict[str, str]] = {}
    for marker in all_markers:
        if not isinstance(marker, StepBlockMarker):
            continue
        if not is_device_step_clsid(marker.step_clsid):
            continue

        if marker.instance_guid not in device_steps:
            device_steps[marker.instance_guid] = {
                "clsid": marker.step_clsid,
                "code": "\n".join(marker.code_lines),
            }

        # Belt-and-suspenders: check the actual code inside the block.
        # If the code references a DIFFERENT instance GUID than the block
        # marker comment (e.g. user edited code, reconciliation pending),
        # ensure the code's actual GUID also gets an .stp entry.
        actual_call = extract_device_call_from_code(marker.code_lines)
        if actual_call and actual_call.instance_guid not in device_steps:
            device_steps[actual_call.instance_guid] = {
                "clsid": actual_call.clsid,
                "code": "\n".join(marker.code_lines),
            }
            logger.info(
                "Found code-level GUID %s (differs from comment GUID %s) -- "
                "adding to .stp",
                actual_call.instance_guid,
                marker.instance_guid,
            )

    # If there are no device steps and no existing .stp, nothing to do
    if not device_steps and not os.path.exists(stp_path):
        return

    # Read existing .stp sections (if .stp exists) to preserve user config
    existing_sections: Dict[str, str] = {}
    if os.path.exists(stp_path):
        tmp_text_path = stp_path + f".sync_{_unique_temp_suffix()}.txt"
        try:
            binary_to_text_file(Path(stp_path), Path(tmp_text_path))
            existing_text = Path(tmp_text_path).read_text(encoding="latin1")
            existing_sections = parse_existing_stp_sections(existing_text)
        finally:
            _safe_unlink(tmp_text_path)

    # Build the new .stp text
    stp_text = build_stp_text(device_steps, existing_sections)

    # Transactional write: text → binary → atomic swap
    _transactional_binary_write(stp_path, stp_text)
    logger.info("Synced .stp: %s", os.path.basename(stp_path))


# ═════════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """CLI entry point for standalone ``.stp`` sync.

    Subcommands:

    ``sync``
        Synchronize a ``.stp`` file from an ``.hsl`` file::

            python -m standalone_med_tools.stp_generator sync MyMethod.hsl

        With explicit output path::

            python -m standalone_med_tools.stp_generator sync MyMethod.hsl --stp MyMethod.stp

    Returns:
        Exit code (0 = success, 1 = error).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="stp_generator",
        description="Hamilton .stp file generator and sync pipeline",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # sync command
    p_sync = sub.add_parser("sync", help="Sync a .stp from an .hsl file")
    p_sync.add_argument("hsl", type=str, help="Path to the .hsl file")
    p_sync.add_argument("--stp", type=str, default=None, help="Output .stp path")

    args = parser.parse_args()

    try:
        if args.cmd == "sync":
            sync_stp_from_hsl(args.hsl, stp_path=args.stp)
            print(f"Synced .stp for {args.hsl}")
            return 0

    except Exception as e:
        logger.error("Error: %s", e)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
