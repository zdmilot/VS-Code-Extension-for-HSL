#!/usr/bin/env python3
"""
med_generator.py -- Hamilton .med File Generator & Sync Pipeline (Pure Python)

Full lifecycle management of .med companion files for Hamilton HSL methods.
Ported from the TypeScript implementation in ``src/medGenerator.ts``.

Architecture
============
1. Parse the .hsl AND .sub files to extract block markers and step data
2. Build the .med text format with all required sections
3. Convert text → binary using the pure-Python hxcfgfile_codec
4. Update checksums on .hsl / .sub / .med files

Key Capabilities
=================
- .sub block marker parsing for complete .med sync
- .stp sync for device steps (TipPickUp, Aspirate, Dispense, etc.)
- Cross-file row numbering (.hsl rows 1..N, .sub rows N+1..M)
- Transactional binary writes (tmp → bak → replace)
- Orphan cleanup (removes .med sections for deleted steps)
- ActivityData preservation from existing .med files
- Checksum updates for .hsl, .sub, .med, .stp on save
- Save triggers for both .hsl and .sub files

Imports
=======
This module depends on sibling modules within the ``standalone_med_tools``
package:

- ``.hxcfgfile_codec`` -- binary ↔ text conversion
- ``.block_markers``   -- parsing, CLSID registry, GUID generation, reconciliation
- ``.checksum``        -- CRC-32 computation and checksum footer generation

CLI Usage
=========
::

    python -m standalone_med_tools.med_generator sync  MyMethod.hsl
    python -m standalone_med_tools.med_generator sync  MyMethod.hsl  --med MyMethod.med
    python -m standalone_med_tools.med_generator on-save MyMethod.hsl

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
    NamedTuple,
    Optional,
    Set,
    Tuple,
)

from .block_markers import (
    STEP_CLSID,
    ML_STAR_CLSID,
    TRIPLE_BRACE_CLSIDS,
    StepBlockMarker,
    StructuralBlockMarker,
    parse_block_markers,
    has_step_block_markers,
    renumber_block_markers,
    reconcile_block_marker_headers,
    extract_device_call_from_code,
    generate_instance_guid,
)
from .checksum import (
    RE_CHECKSUM_LINE,
    compute_hsl_checksum,
    generate_checksum_line,
    update_checksum_in_file,
)
from .hxcfgfile_codec import (
    parse_text_med,
    build_binary_med,
    parse_binary_med,
    build_text_med,
    text_to_binary_file,
    binary_to_text_file,
)

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════════

FIELD_HSL_CODE: str = "-533921779"
"""Field ID for the HSL source code in a .med BlockData entry."""

FIELD_DISPLAY_TEXT: str = "-533921780"
"""Field ID for the display text in a .med BlockData entry."""

FIELD_STEP_TYPE_NAME: str = "-533921781"
"""Field ID for the step type name label in a .med BlockData entry."""

FIELD_ICON: str = "-533921782"
"""Field ID for the icon filename in a .med BlockData entry."""

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
"""Set of bare ML_STAR device-specific CLSIDs that need .stp entries."""

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
"""Map of bare CLSID → friendly device step name for the .stp ``StepName`` field."""

_TEMP_COUNTER: int = 0
"""Module-level counter for unique temp-file suffixes."""


# ═════════════════════════════════════════════════════════════════════════════════
# Data Structures
# ═════════════════════════════════════════════════════════════════════════════════


class HslStepBlock(NamedTuple):
    """One block (opening/closing pair) inside an HSL step."""

    block_index: int
    """1-based block index within the step (If/Then/Else has 3)."""

    code: str
    """HSL source code lines concatenated with ``\\n``."""

    row: int
    """Row number from the block marker header."""


class HslStepRecord(NamedTuple):
    """Aggregated record for a single compound step (may have multiple blocks)."""

    instance_guid: str
    """Hamilton underscore-format GUID that uniquely identifies the step instance."""

    clsid: str
    """COM CLSID for the step type, potentially with a device prefix (e.g. ``ML_STAR:...``)."""

    blocks: List[HslStepBlock]
    """Ordered list of blocks belonging to this step."""


class SubmethodInfo(NamedTuple):
    """Information about a submethod parsed from a .sub file."""

    name: str
    """Submethod function name."""

    params: List[Dict[str, object]]
    """List of parameter dicts with keys ``name`` (str), ``type`` (int), ``direction`` (int)."""

    builtin: bool
    """Whether this is a built-in submethod (e.g. ``OnAbort``)."""


class DeviceInfo(NamedTuple):
    """Device declaration parsed from an .hsl file."""

    name: str
    """Device variable name."""

    layout_file: str
    """Deck layout filename referenced in the declaration."""


class ComponentFlags(NamedTuple):
    """Flags for enabled Hamilton components detected via template includes."""

    sched_comp_cmd: bool
    """Scheduler component command enabled."""

    custom_dialog_comp_cmd: bool
    """Custom dialog component command enabled."""

    multi_pip_comp_cmd: bool
    """Multi-pipetting component command enabled."""

    gru_comp_cmd: bool
    """GRU (Grip & Release Unit) component command enabled."""


class StepTypeInfo(NamedTuple):
    """Name and icon for a step type, resolved from its CLSID."""

    name: str
    icon: str


class FunctionCallInfo(NamedTuple):
    """Parsed result of a ``SingleLibFunction`` or ``SubmethodCall`` code line."""

    return_var: str
    """The variable receiving the return value, or ``""``."""

    function_name: str
    """Fully qualified function name (e.g. ``Namespace::FunctionName``)."""

    args: List[str]
    """Ordered list of argument expressions."""


class ArrayMethodCallInfo(NamedTuple):
    """Parsed result of an array method call code line."""

    array_name: str
    """Name of the array variable."""

    method: str
    """Method name (``SetSize``, ``AddAsLast``, ``SetAt``, ``GetAt``, ``GetSize``)."""

    value: str
    """Value argument (or size for ``SetSize``)."""

    index: str
    """Index argument for ``SetAt`` / ``GetAt``."""

    is_add_as_last: bool
    """True when the method is ``AddAsLast``."""

    return_var: str
    """Variable receiving the return value (``GetAt`` / ``GetSize``)."""


class VariableRef(NamedTuple):
    """A variable reference extracted from step code for the Variables section."""

    name: str
    """Variable identifier."""

    role: str
    """Semantic role (``Result``, ``LoopCounter``, ``ReturnValue``, etc.)."""


# ═════════════════════════════════════════════════════════════════════════════════
# Utility Helpers
# ═════════════════════════════════════════════════════════════════════════════════


def _unique_temp_suffix() -> str:
    """Generate a unique suffix for temp files to avoid collisions.

    Returns:
        A string like ``1709471234567_1``.
    """
    global _TEMP_COUNTER
    _TEMP_COUNTER += 1
    return f"{int(_time.time() * 1000)}_{_TEMP_COUNTER}"


def _safe_unlink(file_path: str) -> None:
    """Delete *file_path* if it exists, silently ignoring errors.

    Args:
        file_path: Absolute or relative path to delete.
    """
    try:
        if os.path.exists(file_path):
            os.unlink(file_path)
    except OSError:
        pass


def _escape_string(s: str) -> str:
    """Escape a string for inclusion inside a quoted .med/.stp token.

    Backslashes, double quotes, and newlines are escaped.

    Args:
        s: Raw string value.

    Returns:
        Escaped string safe for embedding between ``"…"`` in .med text.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\r\\n")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _format_timestamp(dt: Optional[datetime] = None) -> str:
    """Format a datetime as Hamilton's ``YYYY-MM-DD HH:MM`` timestamp.

    Args:
        dt: Datetime to format.  Defaults to ``datetime.now()``.

    Returns:
        Formatted timestamp string.
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M")


def _bare_clsid(clsid: str) -> str:
    """Strip a device prefix (e.g. ``ML_STAR:``) from a CLSID.

    Args:
        clsid: Full CLSID, possibly prefixed.

    Returns:
        Bare CLSID in ``{…}`` format.
    """
    if ":" in clsid:
        return clsid.split(":")[1]
    return clsid


def _is_device_step(clsid: str) -> bool:
    """Check whether *clsid* belongs to a device-specific step.

    Device steps carry a prefix like ``ML_STAR:`` before the CLSID.

    Args:
        clsid: The CLSID to test.

    Returns:
        True if the CLSID contains a device prefix.
    """
    return ":" in clsid


def _is_device_step_clsid(clsid: str) -> bool:
    """Check whether *clsid* represents a device step that needs an .stp entry.

    Args:
        clsid: Possibly prefixed CLSID.

    Returns:
        True if the bare CLSID is a known device step.
    """
    return _bare_clsid(clsid) in DEVICE_CLSIDS


def _hsl_type_to_number(type_kw: str) -> int:
    """Convert an HSL type keyword to its numeric type code.

    Args:
        type_kw: HSL type keyword (``variable``, ``sequence``, ``device``, …).

    Returns:
        Integer type code used in .med/.stp parameter declarations.
    """
    mapping: Dict[str, int] = {
        "variable": 1,
        "sequence": 4,
        "device": 5,
        "object": 6,
        "timer": 7,
        "event": 8,
        "file": 9,
    }
    return mapping.get(type_kw.lower(), 1)


# ═════════════════════════════════════════════════════════════════════════════════
# Step-Type Metadata
# ═════════════════════════════════════════════════════════════════════════════════

_STEP_INFO_MAP: Dict[str, StepTypeInfo] = {
    STEP_CLSID["Comment"]:           StepTypeInfo("Comment",                     "Comment.bmp"),
    STEP_CLSID["Assignment"]:        StepTypeInfo("Assignment",                  "Assignment.bmp"),
    STEP_CLSID["Loop"]:              StepTypeInfo("Loop",                        "Loop.bmp"),
    STEP_CLSID["IfThenElse"]:        StepTypeInfo("If",                          "If_Then.bmp"),
    STEP_CLSID["Break"]:             StepTypeInfo("Break",                       "Break.bmp"),
    STEP_CLSID["Return"]:            StepTypeInfo("Return",                      "MECCStepReturn.bmp"),
    STEP_CLSID["Abort"]:             StepTypeInfo("Abort",                       "Abort.bmp"),
    STEP_CLSID["Shell"]:             StepTypeInfo("Shell",                       "Shell.bmp"),
    STEP_CLSID["UserInput"]:         StepTypeInfo("User Input",                  "User_Input.bmp"),
    STEP_CLSID["UserOutput"]:        StepTypeInfo("User Output",                 "User_Output.bmp"),
    STEP_CLSID["SingleLibFunction"]: StepTypeInfo("Single Library Function",     "SingleLibFunction.bmp"),
    STEP_CLSID["SubmethodCall"]:     StepTypeInfo("Submethod Call",              "MECCStepSubmethodCall.bmp"),
    STEP_CLSID["ArrayDeclare"]:      StepTypeInfo("Array Declare/SetSize",       "MECCArrayDeclare.bmp"),
    STEP_CLSID["ArraySetAt"]:        StepTypeInfo("Array SetAt/AddAsLast",       "MECCArraySetAt.bmp"),
    STEP_CLSID["ArrayGetAt"]:        StepTypeInfo("Array GetAt",                 "MECCArrayGetAt.bmp"),
    STEP_CLSID["ArrayGetSize"]:      StepTypeInfo("Array GetSize",               "MECCArrayGetSize.bmp"),
    STEP_CLSID["MathExpression"]:    StepTypeInfo("Math Expression",             "MathExpression.bmp"),
    STEP_CLSID["FileOpen"]:          StepTypeInfo("File Open",                   "File_Open.bmp"),
    STEP_CLSID["FileFind"]:          StepTypeInfo("File Find",                   "File_Find.bmp"),
    STEP_CLSID["FileRead"]:          StepTypeInfo("File Read",                   "File_Read.bmp"),
    STEP_CLSID["FileWrite"]:         StepTypeInfo("File Write",                  "File_Write.bmp"),
    STEP_CLSID["FileClose"]:         StepTypeInfo("File Close",                  "File_Close.bmp"),
    STEP_CLSID["SetCurrentSeqPos"]:  StepTypeInfo("Set Current Position",        "Set_Current_Position.bmp"),
    STEP_CLSID["GetCurrentSeqPos"]:  StepTypeInfo("Get Current Position",        "Get_Current_Position.bmp"),
    STEP_CLSID["SetTotalSeqCount"]:  StepTypeInfo("Set Count",                   "Set_Count.bmp"),
    STEP_CLSID["GetTotalSeqCount"]:  StepTypeInfo("Get Count",                   "Get_Count.bmp"),
    STEP_CLSID["AlignSequences"]:    StepTypeInfo("Align Sequences",             "AlignSequences.bmp"),
    STEP_CLSID["StartTimer"]:        StepTypeInfo("Start Timer",                 "Start_Timer.bmp"),
    STEP_CLSID["WaitTimer"]:         StepTypeInfo("Wait Timer",                  "Wait_Timer.bmp"),
    STEP_CLSID["ReadElapsedTime"]:   StepTypeInfo("Read Elapsed Time",           "ReadElapsed_Time.bmp"),
    STEP_CLSID["ResetTimer"]:        StepTypeInfo("Reset Timer",                 "Reset_Timer.bmp"),
    STEP_CLSID["StopTimer"]:         StepTypeInfo("Stop Timer",                  "StopTimer.bmp"),
    STEP_CLSID["WaitForEvent"]:      StepTypeInfo("Wait for Event",              "WaitForEvent.bmp"),
    STEP_CLSID["SetEvent"]:          StepTypeInfo("Set Event",                   "SetEvent.bmp"),
    STEP_CLSID["LibraryFunction"]:   StepTypeInfo("Library Function",            "LibraryFunction.bmp"),
    STEP_CLSID["ArrayCopy"]:         StepTypeInfo("Array Copy",                  "MECCArrayCopy.bmp"),
    STEP_CLSID["UserErrorHandling"]: StepTypeInfo("User Error Handling",         "UserErrorHandler.bmp"),
    STEP_CLSID["ComPortOpen"]:       StepTypeInfo("COM Port Open",               "ComPortOpen.bmp"),
    STEP_CLSID["ComPortRead"]:       StepTypeInfo("COM Port Read",               "ComPortRead.bmp"),
    STEP_CLSID["ComPortWrite"]:      StepTypeInfo("COM Port Write",              "ComPortWrite.bmp"),
    STEP_CLSID["ComPortClose"]:      StepTypeInfo("COM Port Close",              "ComPortClose.bmp"),
    STEP_CLSID["ThreadBegin"]:       StepTypeInfo("Begin Thread",                "ThreadBegin.bmp"),
    STEP_CLSID["ThreadWaitFor"]:     StepTypeInfo("Wait for Thread",             "ThreadWaitFor.bmp"),
}
"""Map of bare CLSID → (friendly name, icon filename)."""


def get_step_info_from_clsid(clsid: str) -> StepTypeInfo:
    """Look up step-type metadata by CLSID.

    Args:
        clsid: Full or bare CLSID.

    Returns:
        :class:`StepTypeInfo` with ``name`` and ``icon`` fields.
    """
    return _STEP_INFO_MAP.get(_bare_clsid(clsid), StepTypeInfo("Unknown", "Comment.bmp"))


def get_block_type_name(clsid: str, block_index: int) -> str:
    """Return the display label for a specific block within a step.

    For most steps this is the step name itself.  Multi-block steps
    (Loop, If/Then/Else) have different labels for their closing blocks.

    Args:
        clsid: Full or bare CLSID.
        block_index: 1-based block index.

    Returns:
        Display label string (e.g. ``"End Loop"``, ``"Else"``).
    """
    info = get_step_info_from_clsid(clsid)
    bare = _bare_clsid(clsid)

    if bare == STEP_CLSID["Loop"] and block_index == 2:
        return "End Loop"
    if bare == STEP_CLSID["IfThenElse"]:
        if block_index == 2:
            return "Else"
        if block_index == 3:
            return "End If"
    return info.name


def get_block_icon(clsid: str, block_index: int) -> str:
    """Return the icon filename for a specific block within a step.

    Args:
        clsid: Full or bare CLSID.
        block_index: 1-based block index.

    Returns:
        Icon filename string (e.g. ``"End_Loop.bmp"``).
    """
    info = get_step_info_from_clsid(clsid)
    bare = _bare_clsid(clsid)

    if bare == STEP_CLSID["Loop"] and block_index == 2:
        return "End_Loop.bmp"
    if bare == STEP_CLSID["IfThenElse"] and block_index == 3:
        return "End_If.bmp"
    return info.icon


# ═════════════════════════════════════════════════════════════════════════════════
# Code Parsing Helpers
# ═════════════════════════════════════════════════════════════════════════════════


def extract_comment_text(code: str) -> str:
    """Extract the human-readable comment text from a Comment step's HSL code.

    The Comment step wraps its text in::

        MECC::TraceComment(Translate("..."));

    Args:
        code: Full HSL source code of the Comment block.

    Returns:
        Extracted text, or ``""`` if no match.
    """
    m = re.search(r'MECC::TraceComment\(Translate\("(.*)"\)\);', code, re.DOTALL)
    return m.group(1) if m else ""


def parse_assignment(code: str) -> Dict[str, str]:
    """Parse an Assignment step's HSL code into variable and value parts.

    Expected format::

        varName = expression;

    Args:
        code: HSL source code of the Assignment block.

    Returns:
        Dict with keys ``variable`` and ``value``, both strings.
    """
    m = re.match(r'^\s*(\w+)\s*=\s*(.*?)\s*;\s*$', code)
    if m:
        return {"variable": m.group(1), "value": m.group(2)}
    return {"variable": "", "value": ""}


def parse_loop_code(code: str) -> Dict[str, object]:
    """Parse a Loop step's HSL code to extract counter, iteration count, etc.

    Handles both for-loops::

        for(counter = 0; counter < N;)

    and while-loops::

        counter = 0; while (condition)

    Args:
        code: HSL source code of the Loop block (first block).

    Returns:
        Dict with keys ``counter`` (str), ``iterations`` (str),
        ``is_while`` (bool), ``condition`` (str).
    """
    # For-loop pattern
    for_m = re.search(r'for\((\w+)\s*=\s*0;\s*\w+\s*<\s*(\d+)', code)
    if for_m:
        return {
            "counter": for_m.group(1),
            "iterations": for_m.group(2),
            "is_while": False,
            "condition": "",
        }

    # While-loop pattern
    while_m = re.search(r'(\w+)\s*=\s*0;\s*while\s*\((.+?)\)', code, re.DOTALL)
    if while_m:
        return {
            "counter": while_m.group(1),
            "iterations": "0",
            "is_while": True,
            "condition": while_m.group(2),
        }

    return {"counter": "loopCounter1", "iterations": "0", "is_while": False, "condition": ""}


def split_function_args(arg_str: str) -> List[str]:
    """Split function arguments respecting quoted strings and nested parentheses.

    Args:
        arg_str: The argument portion of a function call (between outer parentheses).

    Returns:
        List of individual argument expression strings.
    """
    args: List[str] = []
    depth = 0
    in_string = False
    current = ""

    for i, ch in enumerate(arg_str):
        if ch == '"' and (i == 0 or arg_str[i - 1] != "\\"):
            in_string = not in_string

        if not in_string:
            if ch in ("(", "["):
                depth += 1
            elif ch in (")", "]"):
                depth -= 1
            elif ch == "," and depth == 0:
                args.append(current.strip())
                current = ""
                continue

        current += ch

    if current.strip():
        args.append(current.strip())

    return args


def parse_function_call_code(code: str) -> FunctionCallInfo:
    """Parse a ``SingleLibFunction`` or ``SubmethodCall`` HSL code line.

    Handles formats::

        Namespace::Function(arg1, arg2, ...);
        retVar = Namespace::Function(arg1, arg2, ...);
        SubmethodName(arg1, arg2, ...);
        retVar = SubmethodName(arg1, arg2, ...);

    Args:
        code: HSL source code of the function call.

    Returns:
        :class:`FunctionCallInfo` with ``return_var``, ``function_name``, ``args``.
    """
    inner = code.strip()
    # Strip outer braces if present
    if inner.startswith("{"):
        inner = re.sub(r'^\{\s*', '', inner)
        inner = re.sub(r'\s*\}$', '', inner)

    m = re.match(r'^\s*(?:(\w+)\s*=\s*)?([A-Za-z_][\w:]*)\s*\(([\s\S]*)\)\s*;\s*$', inner)
    if not m:
        return FunctionCallInfo(return_var="", function_name="", args=[])

    return FunctionCallInfo(
        return_var=m.group(1) or "",
        function_name=m.group(2),
        args=split_function_args(m.group(3).strip()) if m.group(3).strip() else [],
    )


def parse_array_method_call(code: str) -> ArrayMethodCallInfo:
    """Parse an array method call from HSL code.

    Handles formats like::

        arrName.SetSize(0);
        arrName.AddAsLast(value);
        arrName.SetAt(index, value);
        result = arrName.GetAt(index);
        result = arrName.GetSize();

    Args:
        code: HSL source code for the array step.

    Returns:
        :class:`ArrayMethodCallInfo` with parsed fields.
    """
    array_name = ""
    method = ""
    value = ""
    index = ""
    is_add_as_last = False
    return_var = ""

    inner = code.strip()

    # Check for return var: result = arrName.Method(...)
    ret_m = re.match(r'^\s*(\w+)\s*=\s*(.*)\s*$', inner)
    if ret_m:
        return_var = ret_m.group(1)
        inner = ret_m.group(2).strip()

    # Match arrName.Method(args);
    call_m = re.match(r'^(\w+)\.(\w+)\((.*)\)\s*;\s*$', inner)
    if not call_m:
        return ArrayMethodCallInfo(array_name, method, value, index, is_add_as_last, return_var)

    array_name = call_m.group(1)
    method = call_m.group(2)
    arg_str = call_m.group(3).strip()

    if method == "SetSize":
        value = arg_str or "0"
    elif method == "AddAsLast":
        is_add_as_last = True
        value = arg_str
    elif method == "SetAt":
        parts = split_function_args(arg_str)
        index = parts[0] if len(parts) > 0 else ""
        value = parts[1] if len(parts) > 1 else ""
    elif method == "GetAt":
        index = arg_str
    # GetSize has no args

    return ArrayMethodCallInfo(array_name, method, value, index, is_add_as_last, return_var)


# ═════════════════════════════════════════════════════════════════════════════════
# Display-Text Generation
# ═════════════════════════════════════════════════════════════════════════════════


def generate_display_text(clsid: str, code: str, block_index: int) -> Optional[str]:
    """Generate the human-readable display text for a .med BlockData entry.

    Args:
        clsid: Step CLSID (possibly device-prefixed).
        code: HSL source code for the block.
        block_index: 1-based block index.

    Returns:
        Display text string, or ``None`` if the step type does not use one.
    """
    bare = _bare_clsid(clsid)

    if bare == STEP_CLSID["Comment"]:
        text = extract_comment_text(code)
        return f"<{text.replace(chr(92) + 'n', chr(10))}>"

    if bare == STEP_CLSID["Assignment"]:
        asn = parse_assignment(code)
        return f"'{asn['variable']}' = '{asn['value']}'"

    if bare == STEP_CLSID["Loop"]:
        if block_index == 2:
            return ""
        loop_info = parse_loop_code(code)
        if loop_info["is_while"]:
            return f"while '{loop_info['condition']}'\n'{loop_info['counter']}' used as loop counter variable"
        return f"'{loop_info['iterations']}' times\n'{loop_info['counter']}' used as loop counter variable"

    return None


# ═════════════════════════════════════════════════════════════════════════════════
# Variable Extraction
# ═════════════════════════════════════════════════════════════════════════════════


def extract_variables_from_code(clsid: str, code: str) -> List[VariableRef]:
    """Extract variable references from step code for the ``Variables`` section.

    Only relevant for non-function-call, non-array steps.

    Args:
        clsid: Step CLSID.
        code: HSL source code of the step.

    Returns:
        List of :class:`VariableRef` named tuples.
    """
    bare = _bare_clsid(clsid)
    variables: List[VariableRef] = []

    if bare == STEP_CLSID["Assignment"]:
        asn = parse_assignment(code)
        if asn["variable"]:
            variables.append(VariableRef(name=asn["variable"], role="Result"))
    elif bare == STEP_CLSID["Loop"]:
        loop_info = parse_loop_code(code)
        if loop_info["counter"]:
            variables.append(VariableRef(name=str(loop_info["counter"]), role="LoopCounter"))
    elif bare in (STEP_CLSID["ArrayGetAt"], STEP_CLSID["ArrayGetSize"]):
        arr_info = parse_array_method_call(code)
        if arr_info.return_var:
            variables.append(VariableRef(name=arr_info.return_var, role="Result"))

    return variables


# ═════════════════════════════════════════════════════════════════════════════════
# Extraction from .hsl / .sub
# ═════════════════════════════════════════════════════════════════════════════════


def extract_submethods_from_sub(sub_path: str) -> List[SubmethodInfo]:
    """Parse submethod forward declarations from a .sub file.

    If the .sub file does not exist, a minimal list containing only
    ``OnAbort`` is returned.

    Args:
        sub_path: Path to the .sub file.

    Returns:
        List of :class:`SubmethodInfo` records.
    """
    result: List[SubmethodInfo] = []

    if not os.path.exists(sub_path):
        return [SubmethodInfo(name="OnAbort", params=[], builtin=True)]

    content = Path(sub_path).read_text(encoding="utf-8")
    decl_re = re.compile(r'function\s+(\w+)\s*\(\s*(.*?)\s*\)\s*void\s*;', re.DOTALL)

    for m in decl_re.finditer(content):
        name = m.group(1)
        param_str = m.group(2).strip()
        params: List[Dict[str, object]] = []

        if param_str:
            param_parts = [s.strip() for s in param_str.split(",")]
            for part in param_parts:
                pm = re.match(r'(\w+)\s*(&\s*)?(\w+)', part)
                if pm:
                    type_kw = pm.group(1)
                    is_ref = bool(pm.group(2))
                    p_name = pm.group(3)
                    params.append({
                        "name": p_name,
                        "type": _hsl_type_to_number(type_kw),
                        "direction": 2 if is_ref else 1,
                    })

        result.append(SubmethodInfo(
            name=name,
            params=params,
            builtin=(name == "OnAbort"),
        ))

    # Ensure OnAbort is always present
    if not any(s.name == "OnAbort" for s in result):
        result.append(SubmethodInfo(name="OnAbort", params=[], builtin=True))

    return result


def extract_devices_from_hsl(content: str) -> List[DeviceInfo]:
    """Extract device declarations from .hsl file content.

    Matches patterns like::

        device myDevice ("layout.lay", "...", hslTrue);

    Args:
        content: Full .hsl file text.

    Returns:
        List of :class:`DeviceInfo` records.
    """
    devices: List[DeviceInfo] = []
    regex = re.compile(
        r'(?:global\s+)?device\s+(\w+)\s*\(\s*"([^"]+)"\s*,\s*"[^"]*"\s*,\s*hsl\w+\s*\)'
    )
    for m in regex.finditer(content):
        devices.append(DeviceInfo(name=m.group(1), layout_file=m.group(2)))
    return devices


def detect_enabled_components(content: str) -> ComponentFlags:
    """Detect which Hamilton components are enabled from template includes.

    Args:
        content: Full .hsl file text.

    Returns:
        :class:`ComponentFlags` with boolean flags for each component.
    """
    return ComponentFlags(
        sched_comp_cmd="HSLSchedCCLib" in content,
        custom_dialog_comp_cmd=True,  # usually enabled by default
        multi_pip_comp_cmd=True,
        gru_comp_cmd="HSLPTLLib" in content,
    )


# ═════════════════════════════════════════════════════════════════════════════════
# ActivityData Template
# ═════════════════════════════════════════════════════════════════════════════════


def get_default_activity_data(template_path: Optional[str] = None) -> str:
    """Load the default ActivityData base64 blob.

    Tries to load from *template_path*, then from the bundled
    ``activityDataTemplate.b64`` file next to the ``src/`` directory.

    Args:
        template_path: Explicit path to a ``.b64`` template file.  If ``None``,
            the function searches relative to this module.

    Returns:
        Base64-encoded ActivityData string, or ``""`` if not found.
    """
    candidates: List[str] = []
    if template_path:
        candidates.append(template_path)

    # Relative to this module: ../src/activityDataTemplate.b64
    module_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(module_dir, "..", "src", "activityDataTemplate.b64"))
    candidates.append(os.path.join(module_dir, "activityDataTemplate.b64"))

    for p in candidates:
        if os.path.isfile(p):
            try:
                return Path(p).read_text(encoding="utf-8").strip()
            except OSError:
                continue

    return ""


def extract_activity_data_from_med(med_path: str) -> str:
    """Extract the ActivityData base64 blob from an existing binary .med file.

    Converts the binary .med to text, extracts the ``ActivityDocument`` value,
    then cleans up the temp file.

    Args:
        med_path: Path to the .med binary file.

    Returns:
        Base64-encoded ActivityData string.

    Raises:
        ValueError: If the ActivityData cannot be extracted.
    """
    text_path = med_path + f".extract_{_unique_temp_suffix()}.txt"
    try:
        binary_to_text_file(Path(med_path), Path(text_path))
        text_content = Path(text_path).read_text(encoding="latin1")

        m = re.search(
            r'DataDef,ActivityData,1,ActivityData,\s*\{([^}]+)\}', text_content, re.DOTALL
        )
        if not m:
            raise ValueError("Could not extract ActivityData from template .med file")

        activity_section = m.group(1).strip()
        b64_m = re.search(r'ActivityDocument,\s*"([^"]+)"', activity_section, re.DOTALL)
        if not b64_m:
            raise ValueError("Could not extract ActivityDocument base64 data")

        return b64_m.group(1)
    finally:
        _safe_unlink(text_path)


# ═════════════════════════════════════════════════════════════════════════════════
# Step-Type-Specific Field Builders
# ═════════════════════════════════════════════════════════════════════════════════


def _append_step_specific_fields(lines: List[str], clsid: str, code: str) -> None:
    """Append step-type-specific data fields to the token list.

    These fields come at the beginning of the step section, before
    ``ParsCommandVersion`` and ``BlockData``.

    Args:
        lines: Mutable list of token strings being assembled.
        clsid: Full (possibly prefixed) CLSID.
        code: HSL source code of the first block.
    """
    bare = _bare_clsid(clsid)

    if bare == STEP_CLSID["Comment"]:
        lines.append('"3TraceSwitch"')
        has_trace = "MECC::TraceComment" in code
        lines.append(f'"{1 if has_trace else 0}"')
        lines.append('"1Comment"')
        comment_text = extract_comment_text(code)
        lines.append(f'"{_escape_string(comment_text.replace(chr(92) + "n", "\r\n"))}"')

    elif bare == STEP_CLSID["Assignment"]:
        lines.append('"3Expression"')
        lines.append('"1"')
        lines.append('"1Result"')
        asn = parse_assignment(code)
        lines.append(f'"{_escape_string(asn["variable"])}"')

    elif bare == STEP_CLSID["Loop"]:
        loop_info = parse_loop_code(code)
        lines.append('"3ComparisonOperator"')
        lines.append('"11102"')
        lines.append('"1LeftComparisonValue"')
        lines.append('""')
        lines.append('"1LoopCounter"')
        lines.append(f'"{_escape_string(loop_info["counter"])}"')
        lines.append('"3NbrOfIterations"')
        lines.append(f'"{_escape_string(loop_info["iterations"])}"')

    elif bare in (STEP_CLSID["SingleLibFunction"], STEP_CLSID["SubmethodCall"]):
        call_info = parse_function_call_code(code)
        is_submethod = bare == STEP_CLSID["SubmethodCall"]

        lines.append('"1ReturnValue"')
        lines.append(f'"{_escape_string(call_info.return_var)}"')
        lines.append('"1FunctionName"')
        lines.append(f'"{_escape_string(call_info.function_name)}"')
        lines.append('"3FieldCount"')
        lines.append(f'"{len(call_info.args)}"')

        # FunctionPars section
        lines.append('"(FunctionPars"')
        lines.append('"3-534642658"')
        lines.append(f'"{"16" if is_submethod else "0"}"')
        lines.append('"(-533921770"')
        for i in range(len(call_info.args)):
            lines.append(f'"({i}"')
            lines.append('"1-534642683"')
            lines.append(f'"param{i}"')
            lines.append('"1-533921767"')
            lines.append('""')
            lines.append('"3-533921768"')
            lines.append('"0"')
            lines.append('"3-534642677"')
            lines.append('"1"')
            lines.append('"1-533921769"')
            lines.append('""')
            lines.append('")"')
        lines.append('")"')  # close -533921770
        lines.append('"1-533921771"')
        lines.append('""')
        lines.append('"1-534642685"')
        lines.append('""')
        lines.append('"3-534642677"')
        lines.append(f'"{"10" if is_submethod else "1"}"')
        lines.append('")"')  # close FunctionPars

    elif bare == STEP_CLSID["ArraySetAt"]:
        arr_info = parse_array_method_call(code)
        lines.append('"3AddAsLastFlag"')
        lines.append(f'"{1 if arr_info.is_add_as_last else 0}"')
        value_prefix = "5" if re.match(r'^-?\d+(\.\d+)?$', arr_info.value) else "1"
        lines.append(f'"{value_prefix}ValueToSet"')
        lines.append(f'"{_escape_string(arr_info.value)}"')
        lines.append('"1ArrayName"')
        lines.append(f'"{_escape_string(arr_info.array_name)}"')

    elif bare == STEP_CLSID["ArrayDeclare"]:
        arr_info = parse_array_method_call(code)
        lines.append('"1NewSize"')
        lines.append(f'"{_escape_string(arr_info.value or "")}"')
        lines.append('"1ArrayName"')
        lines.append(f'"{_escape_string(arr_info.array_name)}"')

    elif bare == STEP_CLSID["ArrayGetAt"]:
        arr_info = parse_array_method_call(code)
        lines.append('"1ArrayName"')
        lines.append(f'"{_escape_string(arr_info.array_name)}"')
        lines.append('"1Result"')
        lines.append(f'"{_escape_string(arr_info.return_var)}"')

    elif bare == STEP_CLSID["ArrayGetSize"]:
        arr_info = parse_array_method_call(code)
        lines.append('"1ArrayName"')
        lines.append(f'"{_escape_string(arr_info.array_name)}"')
        lines.append('"1Result"')
        lines.append(f'"{_escape_string(arr_info.return_var)}"')


# ═════════════════════════════════════════════════════════════════════════════════
# Per-Step Section Builder
# ═════════════════════════════════════════════════════════════════════════════════


def build_device_step_stub(step: HslStepRecord) -> str:
    """Generate the minimal .med stub for a device step.

    Device steps store their full parameters in the .stp file, not in .med.
    The .med only contains a minimal placeholder with empty display fields.

    Standard format (3 field count):
        ``"33","3","(1","10","","11","","12","",")",")"``

    LoadCarrier format (6 field count -- includes barcode fields):
        ``"33","6","(1","13","","14","","15","","10","","11","","12","",")",")"``

    Args:
        step: The :class:`HslStepRecord` for the device step.

    Returns:
        Complete .med section text for the device step stub.
    """
    bare = _bare_clsid(step.clsid)
    lines: List[str] = []

    lines.append(f"DataDef,HxPars,3,{step.instance_guid},")
    lines.append("[")

    is_load_carrier = bare == "{54114402-7FA2-11D3-AD85-0004ACB1DCB2}"
    field_count = 6 if is_load_carrier else 3

    lines.append('"33"')
    lines.append(f'"{field_count}"')
    lines.append('"(1"')

    if is_load_carrier:
        lines.append('"13"')
        lines.append('""')
        lines.append('"14"')
        lines.append('""')
        lines.append('"15"')
        lines.append('""')

    lines.append('"10"')
    lines.append('""')
    lines.append('"11"')
    lines.append('""')
    lines.append('"12"')
    lines.append('""')
    lines.append('")"')
    lines.append('")"')
    lines.append("];")

    return _format_hxpars_section(lines)


def _format_hxpars_section(lines: List[str]) -> str:
    """Vendor text grammar requires a comma between every HxPars token."""
    if (not isinstance(lines, list) or len(lines) < 4 or lines[1] != "[" or lines[-1] != "];"
            or not isinstance(lines[0], str)
            or not re.fullmatch(r'DataDef,HxPars,3,[^,\r\n\[\]"]+,', lines[0])):
        raise ValueError("Expected one complete generated HxPars section")
    if any(not isinstance(token, str) or not re.fullmatch(r'"(?:[^"\\\r\n]|\\["\\rnt])*"', token)
           for token in lines[2:-1]):
        raise ValueError("Expected individually quoted, escaped HxPars tokens without separators")
    return "\r\n".join(lines[:2]) + "\r\n" + ",\r\n".join(lines[2:-1]) + "\r\n];"


def build_step_section(step: HslStepRecord) -> str:
    """Generate the full .med ``DataDef,HxPars,3,<guid>,`` section for a step.

    Non-device (general) steps get full sections in .med with:
    - Step-type-specific fields
    - ``ParsCommandVersion``
    - ``BlockData`` with display text, icon, HSL code
    - ``ParamTranslateValue`` / ``ParamValue`` for function calls
    - ``Variables`` section
    - ``Timestamp``

    Device steps are delegated to :func:`build_device_step_stub`.

    Args:
        step: The :class:`HslStepRecord` to serialize.

    Returns:
        Complete .med section text.
    """
    # Device steps use a minimal stub
    if _is_device_step(step.clsid):
        return build_device_step_stub(step)

    lines: List[str] = []
    lines.append(f"DataDef,HxPars,3,{step.instance_guid},")
    lines.append("[")

    bare = _bare_clsid(step.clsid)
    is_func_call_step = bare in (STEP_CLSID["SingleLibFunction"], STEP_CLSID["SubmethodCall"])

    # Step-type-specific data fields
    first_code = step.blocks[0].code if step.blocks else ""
    _append_step_specific_fields(lines, step.clsid, first_code)

    # ParsCommandVersion (2 for SingleLibFunction, 1 for all others)
    lines.append('"3ParsCommandVersion"')
    lines.append(f'"{2 if bare == STEP_CLSID["SingleLibFunction"] else 1}"')

    # BlockData
    lines.append('"(BlockData"')
    for block in step.blocks:
        lines.append(f'"({block.block_index}"')

        display_text = generate_display_text(step.clsid, block.code, block.block_index)
        if display_text is not None:
            lines.append(f'"1{FIELD_DISPLAY_TEXT}"')
            lines.append(f'"{_escape_string(display_text)}"')

        type_name = get_block_type_name(step.clsid, block.block_index)
        if type_name:
            lines.append(f'"1{FIELD_STEP_TYPE_NAME}"')
            lines.append(f'"{type_name}"')

        lines.append(f'"1{FIELD_ICON}"')
        lines.append(f'"{get_block_icon(step.clsid, block.block_index)}"')

        lines.append(f'"1{FIELD_HSL_CODE}"')
        lines.append(f'"{_escape_string(block.code)}"')

        lines.append('")"')
    lines.append('")"')

    # ParamTranslateValue -- for function call steps
    if is_func_call_step:
        call_info = parse_function_call_code(first_code)
        string_arg_indices = [
            i for i, a in enumerate(call_info.args) if a.startswith('"')
        ]
        if string_arg_indices:
            lines.append('"(ParamTranslateValue"')
            for idx in string_arg_indices:
                lines.append(f'"3Value.{idx}"')
                lines.append('"0"')
            lines.append('")"')

    # Timestamp
    lines.append('"1Timestamp"')
    lines.append(f'"{_format_timestamp()}"')

    # ParamValue -- for function call steps
    if is_func_call_step:
        call_info = parse_function_call_code(first_code)
        if call_info.args:
            lines.append('"(ParamValue"')
            for i, arg in enumerate(call_info.args):
                lines.append(f'"1Value.{i}"')
                lines.append(f'"{_escape_string(arg)}"')
            lines.append('")"')

    # Variables section
    if is_func_call_step:
        call_info = parse_function_call_code(first_code)
        lines.append('"(Variables"')

        # Container -533921792: FunctionName
        lines.append('"(-533921792"')
        lines.append(f'"({_escape_string(call_info.function_name)}"')
        lines.append('"(0"')
        lines.append('"10"')
        lines.append('"FunctionName"')
        lines.append('")"')
        lines.append('")"')
        lines.append('")"')

        # Container -534118398: ParamValue and ReturnValue references
        has_variable_args = any(
            not a.startswith('"') and not re.match(r'^\d+(\.\d+)?$', a)
            for a in call_info.args
        )
        has_return = call_info.return_var != ""

        if has_variable_args or has_return:
            lines.append('"(-534118398"')
            for i, arg in enumerate(call_info.args):
                if not arg.startswith('"') and not re.match(r'^\d+(\.\d+)?$', arg):
                    lines.append(f'"({_escape_string(arg)}"')
                    lines.append('"(0"')
                    lines.append('"10"')
                    lines.append('"ParamValue"')
                    lines.append('"11"')
                    lines.append(f'"Value.{i}"')
                    lines.append('")"')
                    lines.append('")"')
            if has_return:
                lines.append(f'"({_escape_string(call_info.return_var)}"')
                lines.append('"(0"')
                lines.append('"10"')
                lines.append('"ReturnValue"')
                lines.append('")"')
                lines.append('")"')
            lines.append('")"')

        lines.append('")"')  # close Variables
    else:
        is_array_set_at = bare == STEP_CLSID["ArraySetAt"]
        is_array_declare = bare == STEP_CLSID["ArrayDeclare"]

        if is_array_set_at or is_array_declare:
            arr_info = parse_array_method_call(first_code)

            # ArraySetAt: Index field comes after Timestamp
            if is_array_set_at:
                lines.append('"1Index"')
                lines.append(f'"{_escape_string(arr_info.index)}"')

            # ArrayDeclare: ArrayTypeCommandKey comes BEFORE Variables
            if is_array_declare:
                lines.append('"3ArrayTypeCommandKey"')
                lines.append('"-534118349"')

            lines.append('"(Variables"')

            # ValueToSet container (only for ArraySetAt with variable values)
            if (
                is_array_set_at
                and arr_info.value
                and not arr_info.value.startswith('"')
                and not re.match(r'^-?\d+(\.\d+)?$', arr_info.value)
            ):
                lines.append('"(-534118398"')
                lines.append(f'"({_escape_string(arr_info.value)}"')
                lines.append('"(0"')
                lines.append('"10"')
                lines.append('"ValueToSet"')
                lines.append('")"')
                lines.append('")"')
                lines.append('")"')

            # ArrayName container
            lines.append('"(-534118349"')
            lines.append(f'"({_escape_string(arr_info.array_name)}"')
            lines.append('"(0"')
            lines.append('"10"')
            lines.append('"ArrayName"')
            lines.append('")"')
            lines.append('")"')
            lines.append('")"')

            lines.append('")"')  # close Variables

            # ArrayDeclare: EmptyArray comes AFTER Variables
            if is_array_declare:
                lines.append('"3EmptyArray"')
                lines.append('"1"')
        else:
            # Non-function-call, non-array steps: simple flat variable list
            var_refs = extract_variables_from_code(step.clsid, first_code)
            if var_refs:
                lines.append('"(Variables"')
                lines.append('"(-534118398"')
                for v in var_refs:
                    lines.append(f'"({_escape_string(v.name)}"')
                    lines.append('"(0"')
                    lines.append('"10"')
                    lines.append(f'"{_escape_string(v.role)}"')
                    lines.append('")"')
                    lines.append('")"')
                lines.append('")"')
                lines.append('")"')

    lines.append('")"')
    lines.append("];")

    return _format_hxpars_section(lines)


# ═════════════════════════════════════════════════════════════════════════════════
# .med Section Builders
# ═════════════════════════════════════════════════════════════════════════════════


def _build_hx_met_ed_data(
    devices: List[DeviceInfo],
    components: ComponentFlags,
) -> str:
    """Build the ``HxMetEdData`` section of the .med text.

    Contains version info, component flags, and device declarations.

    Args:
        devices: Device declarations parsed from .hsl.
        components: Enabled component flags.

    Returns:
        Complete ``HxMetEdData`` section text.
    """
    lines: List[str] = []
    lines.append("DataDef,HxPars,3,HxMetEdData,")
    lines.append("[")
    lines.append('"1Version"')
    lines.append('"6.2.2.4006"')
    lines.append('"3-533725180"')
    lines.append('"1"')
    lines.append('"3-533725181"')
    lines.append('"1045"')
    lines.append('"(-533725182"')
    lines.append('"3SchedCompCmd"')
    lines.append(f'"{1 if components.sched_comp_cmd else 0}"')
    lines.append('"3CustomDialogCompCmd"')
    lines.append(f'"{1 if components.custom_dialog_comp_cmd else 0}"')
    lines.append('"3MultiPipCompCmd"')
    lines.append(f'"{1 if components.multi_pip_comp_cmd else 0}"')
    lines.append('"3GRUCompCmd"')
    lines.append(f'"{1 if components.gru_comp_cmd else 0}"')
    lines.append('")"')

    if devices:
        lines.append('"(-533725183"')
        for dev in devices:
            lines.append(f'"3{_escape_string(dev.name)}"')
            lines.append('"1"')
        lines.append('")"')

    lines.append('")"')
    lines.append("];")
    return _format_hxpars_section(lines)


def _build_main_definition() -> str:
    """Build the ``HxMetEd_MainDefinition`` section.

    Returns:
        Complete ``HxMetEd_MainDefinition`` section text.
    """
    return _format_hxpars_section([
        "DataDef,HxPars,3,HxMetEd_MainDefinition,",
        "[",
        '"3-533725173"',
        '"3"',
        '"(-533725157"',
        '"(-533725169"',
        '")"',
        '"1-533725170"',
        '""',
        '"3-533725171"',
        '"0"',
        '"1-533725161"',
        '"main"',
        '"3-533725172"',
        '"1"',
        '")"',
        '")"',
        "];",
    ])


def _build_submethods_section(submethods: List[SubmethodInfo]) -> str:
    """Build the ``HxMetEd_Submethods`` section.

    Args:
        submethods: Submethod declarations parsed from .sub.

    Returns:
        Complete ``HxMetEd_Submethods`` section text.
    """
    lines: List[str] = []
    lines.append("DataDef,HxPars,3,HxMetEd_Submethods,")
    lines.append("[")
    lines.append('"(-533725162"')

    for i, sm in enumerate(submethods):
        lines.append(f'"({i}"')
        lines.append('"(-533725169"')

        for j, p in enumerate(sm.params):
            lines.append(f'"({j}"')
            lines.append('"1-533725163"')
            lines.append('""')
            lines.append('"1-533725164"')
            lines.append('""')
            lines.append('"3-533725165"')
            lines.append(f'"{p["direction"]}"')
            lines.append('"3-533725166"')
            lines.append(f'"{p["type"]}"')
            lines.append('"1-533725167"')
            lines.append('""')
            lines.append('"1-533725168"')
            lines.append(f'"{_escape_string(p["name"])}"')
            lines.append('")"')

        lines.append('")"')
        lines.append('"1-533725170"')
        lines.append('""')
        lines.append('"3-533725171"')
        lines.append('"0"')
        lines.append('"1-533725161"')
        lines.append(f'"{_escape_string(sm.name)}"')
        lines.append('"3-533725172"')
        lines.append(f'"{1 if sm.builtin else 0}"')
        lines.append('")"')

    lines.append('")"')
    lines.append('"3-533725173"')
    lines.append('"4"')
    lines.append('"6-533725154"')
    lines.append('"0"')
    lines.append('"6-533725155"')
    lines.append('"0"')
    lines.append('"6-533725156"')
    lines.append('"0"')
    lines.append('"1-533725158"')
    lines.append('""')
    lines.append('"1-533725160"')
    lines.append('""')
    lines.append('")"')
    lines.append("];")

    return _format_hxpars_section(lines)


# ═════════════════════════════════════════════════════════════════════════════════
# .med Text Builder
# ═════════════════════════════════════════════════════════════════════════════════


def build_med_text(
    activity_data_base64: str,
    steps: Dict[str, HslStepRecord],
    submethods: List[SubmethodInfo],
    devices: List[DeviceInfo],
    enabled_components: ComponentFlags,
    author: Optional[str] = None,
) -> str:
    """Build the complete .med text representation.

    The output follows the HxCfgFile text format with sections:
    - ``HxCfgFile,3;`` header
    - ``ActivityData`` section
    - Step sections (``DataDef,HxPars,3,<guid>,``)
    - ``HxMetEdData`` section
    - ``HxMetEd_MainDefinition`` section
    - ``HxMetEd_Outlining`` section
    - ``HxMetEd_Submethods`` section
    - Checksum footer

    Args:
        activity_data_base64: Base64-encoded activity flowchart blob.
        steps: Dict of instance-GUID → :class:`HslStepRecord`.
        submethods: Submethod declarations.
        devices: Device declarations.
        enabled_components: Component flags.
        author: Author name for the checksum footer (defaults to ``USERNAME`` env var).

    Returns:
        Complete .med text content ready for binary conversion.
    """
    if author is None:
        author = os.environ.get("USERNAME", "admin")

    sections: List[str] = []

    # Header
    sections.append("HxCfgFile,3;")
    sections.append("")
    sections.append("ConfigIsValid,Y;")
    sections.append("")

    # ActivityData
    sections.append("DataDef,ActivityData,1,ActivityData,")
    sections.append("{")
    sections.append(f'ActivityDocument, "{activity_data_base64}"')
    sections.append("};")
    sections.append("")

    # Step sections (sorted alphabetically by GUID)
    for guid in sorted(steps.keys()):
        step = steps[guid]
        sections.append(build_step_section(step))
        sections.append("")

    # HxMetEdData
    sections.append(_build_hx_met_ed_data(devices, enabled_components))
    sections.append("")

    # HxMetEd_MainDefinition
    sections.append(_build_main_definition())
    sections.append("")

    # HxMetEd_Outlining
    sections.append("DataDef,HxPars,3,HxMetEd_Outlining,")
    sections.append("[")
    sections.append('")"')
    sections.append("];")
    sections.append("")

    # HxMetEd_Submethods
    sections.append(_build_submethods_section(submethods))
    sections.append("")

    # Join and add checksum
    content = "\r\n".join(sections) + "\r\n"

    footer = generate_checksum_line(
        content, author=author, valid=0, prefix_char="*"
    )

    return content + footer + "\r\n"


# ═════════════════════════════════════════════════════════════════════════════════
# .stp Section Builders
# ═════════════════════════════════════════════════════════════════════════════════


def _build_error_entry(
    error_number: int,
    error_desc: int,
    error_title: int,
    infinite: bool,
    recoveries: List[Dict[str, object]],
) -> List[str]:
    """Build a single error entry for the .stp ``Errors`` section.

    Args:
        error_number: Error ID number.
        error_desc: Resource ID for error description.
        error_title: Resource ID for error title.
        infinite: Whether retry is infinite.
        recoveries: List of recovery option dicts.

    Returns:
        List of token strings for this error entry.
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


def _get_default_error_recoveries(bare_clsid: str) -> List[str]:
    """Generate default error recovery entries for a device step.

    Args:
        bare_clsid: Bare CLSID (without device prefix).

    Returns:
        Flat list of token strings for the ``(Errors …)`` section.
    """
    lines: List[str] = []

    # Error 3: Hardware error
    lines.extend(_build_error_entry(3, 375, 374, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 999: Unknown
    lines.extend(_build_error_entry(999, 1689, 1688, False, [
        {"id": 3, "desc": 421, "title": 420, "is_default": False},
        {"id": 4, "desc": 429, "title": 428, "is_default": False},
        {"id": 1, "desc": 371, "title": 370, "is_default": True},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 10: Position not found
    lines.extend(_build_error_entry(10, 391, 390, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    # Error 2: Not initialized
    lines.extend(_build_error_entry(2, 373, 372, True, [
        {"id": 3, "desc": 419, "title": 418, "is_default": True},
        {"id": 1, "desc": 371, "title": 370, "is_default": False},
        {"id": 2, "desc": 437, "title": 436, "is_default": False},
    ]))

    return lines


def build_default_stp_section(guid: str, clsid: str, code: str) -> str:
    """Generate a default .stp section for a device step GUID.

    For new steps, generates a minimal valid section that allows the Method
    Editor to open the step's properties dialog for user configuration.

    Args:
        guid: Step instance GUID (Hamilton format).
        clsid: Full CLSID (possibly with device prefix).
        code: HSL source code for the step.

    Returns:
        Complete .stp section text.
    """
    bare = _bare_clsid(clsid)
    step_name = DEVICE_STEP_NAMES.get(bare, "Unknown")
    lines: List[str] = []

    lines.append(f"DataDef,HxPars,3,{guid},")
    lines.append("[")

    lines.append('"1CommandStepFileGuid"')
    lines.append(f'"{_escape_string(guid)}"')

    # Try to extract sequence name from code
    seq_m = re.search(r'ML_STAR\.(\w+)', code)
    sequence_name = f"ML_STAR.{seq_m.group(1)}" if seq_m else ""

    # Step-type-specific fields
    if bare == "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}":
        # Initialize
        lines.extend(['"3AlwaysInitialize"', '"0"'])

    if bare in (
        "{541143FA-7FA2-11D3-AD85-0004ACB1DCB2}",  # TipPickUp
        "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",  # Aspirate
        "{541143F8-7FA2-11D3-AD85-0004ACB1DCB2}",  # Dispense
        "{541143FC-7FA2-11D3-AD85-0004ACB1DCB2}",  # TipEject
    ):
        if sequence_name:
            lines.extend(['"1SequenceObject"', f'"{sequence_name}"'])
            lines.extend(['"1SequenceName"', f'"{sequence_name}"'])
        else:
            lines.extend(['"1SequenceName"', '""'])
        lines.extend(['"1ChannelPattern"', '"11111111"'])

    if bare in (
        "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",  # Aspirate
        "{541143F8-7FA2-11D3-AD85-0004ACB1DCB2}",  # Dispense
    ):
        lines.extend(['"3TipType"', '"5"'])

    if bare == "{541143FC-7FA2-11D3-AD85-0004ACB1DCB2}":
        # TipEject
        lines.extend(['"3UseDefaultWaste"', '"1"'])

    if bare in (
        "{54114402-7FA2-11D3-AD85-0004ACB1DCB2}",  # LoadCarrier
        "{54114400-7FA2-11D3-AD85-0004ACB1DCB2}",  # UnloadCarrier
    ):
        if sequence_name:
            lines.extend(['"1SequenceName"', f'"{sequence_name}"'])

    # Error table
    lines.extend(['"3NbrOfErrors"', '"4"'])
    lines.append('"(Errors"')
    lines.extend(_get_default_error_recoveries(bare))
    lines.append('")"')

    # Sequence counting and optimization
    lines.extend(['"3SequenceCounting"', '"0"'])
    lines.extend(['"3Optimizing channel use"', '"1"'])

    lines.extend(['"1StepName"', f'"{step_name}"'])

    # Channel-level defaults
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

    return _format_hxpars_section(lines)


def _parse_existing_stp_sections(stp_text: str) -> Dict[str, str]:
    """Parse an existing .stp text file to extract sections by GUID.

    Args:
        stp_text: Full .stp text content.

    Returns:
        Dict mapping GUID → complete section text.  A special key
        ``__Properties__`` holds the ``DataDef,Method,1,Properties`` section.
    """
    # Keep original slices: re-emitting could change vendor escaping or native
    # parameter tokens. Delimiters inside quoted values are data.
    sections: Dict[str, str] = {}
    cursor = 0

    def whitespace(position: int) -> int:
        while position < len(stp_text) and stp_text[position].isspace():
            position += 1
        return position

    def quoted(position: int) -> int:
        if position >= len(stp_text) or stp_text[position] != '"':
            raise ValueError("Expected quoted token in existing STP section")
        position += 1
        while position < len(stp_text):
            if stp_text[position] == "\\":
                position += 2
            elif stp_text[position] == '"':
                return position + 1
            else:
                position += 1
        raise ValueError("Unterminated quoted token in existing STP section")

    cursor = whitespace(cursor)
    if stp_text.startswith("HxCfgFile,3;", cursor):
        cursor = whitespace(cursor + len("HxCfgFile,3;"))
        if not stp_text.startswith("ConfigIsValid,Y;", cursor):
            raise ValueError("Unsupported existing STP configuration header")
        cursor = whitespace(cursor + len("ConfigIsValid,Y;"))
    while True:
        cursor = whitespace(cursor)
        if cursor == len(stp_text):
            break
        if stp_text.startswith('*', cursor) and RE_CHECKSUM_LINE.fullmatch(stp_text[cursor:].strip()):
            break
        # Do not search past damaged text to a later header: that silently drops
        # content. Only whitespace or the terminal checksum may separate blocks.
        start = cursor
        header = re.match(r'DataDef,HxPars,3,([^,\s\[\]"{}]+),\s*\[', stp_text[start:])
        properties = re.match(r'DataDef,Method,1,Properties,\s*\{', stp_text[start:])
        if header:
            key, closing = header.group(1), "];"
            if key == "__Properties__":
                raise ValueError("Reserved existing STP section key")
        elif properties:
            header, key, closing = properties, "__Properties__", "};"
        else:
            raise ValueError("Malformed or unsupported existing STP section header")
        if key in sections:
            raise ValueError("Duplicate existing STP section: " + key)
        cursor = whitespace(start + header.end())
        if properties:
            field = re.match(r'[^,\s{}"]+\s*,\s*', stp_text[cursor:])
            if not field:
                raise ValueError("Malformed existing STP Properties field")
            cursor += field.end()
            cursor = whitespace(quoted(cursor))
            if not stp_text.startswith(closing, cursor):
                raise ValueError("Malformed existing STP Properties terminator")
        if not stp_text.startswith(closing, cursor):
            while True:
                cursor = whitespace(quoted(cursor))
                if stp_text.startswith(closing, cursor):
                    break
                if properties or cursor >= len(stp_text) or stp_text[cursor] != ',':
                    raise ValueError("Missing separator or terminator in existing STP section: " + key)
                cursor = whitespace(cursor + 1)
        cursor += len(closing)
        sections[key] = stp_text[start:cursor]
    if not sections:
        raise ValueError("No existing STP sections found")
    return sections


def build_stp_text(
    device_steps: Dict[str, Dict[str, str]],
    existing_sections: Optional[Dict[str, str]] = None,
    author: Optional[str] = None,
) -> str:
    """Build the complete .stp text file content.

    Args:
        device_steps: Dict mapping GUID → ``{"clsid": ..., "code": ...}``.
        existing_sections: Previously parsed .stp sections to preserve.
        author: Author name for checksum footer.

    Returns:
        Complete .stp text content ready for binary conversion.
    """
    if author is None:
        author = os.environ.get("USERNAME", "admin")
    if existing_sections is None:
        existing_sections = {}
    if existing_sections:
        validated = _parse_existing_stp_sections("\n".join(existing_sections.values()))
        if validated != existing_sections:
            raise ValueError("Existing STP section keys do not match their content")

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

    # Device step sections -- sorted by GUID
    for guid in sorted(device_steps.keys()):
        step = device_steps[guid]
        existing = existing_sections.get(guid)
        if existing is not None:
            sections.append(existing)
        else:
            sections.append(build_default_stp_section(guid, step["clsid"], step["code"]))
        sections.append("")

    # AuditTrailData
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


def transactional_binary_write(final_path: str, text_content: str) -> None:
    """Transactional binary write: temp → convert → copy → cleanup.

    Uses the target file's own extension for the temp file.
    Uses copy+delete instead of rename for Windows compatibility.

    Args:
        final_path: Destination path for the binary .med/.stp file.
        text_content: Complete .med/.stp text representation to convert.

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
            raise OSError(f"Binary conversion removed temp file unexpectedly: {tmp_name}")

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
# .hsl → .med Sync
# ═════════════════════════════════════════════════════════════════════════════════


def sync_med_from_hsl(
    hsl_path: str,
    med_path: Optional[str] = None,
    activity_data_base64: Optional[str] = None,
    activity_data_template: Optional[str] = None,
) -> None:
    """Synchronize a .med file from an .hsl file and its companion .sub file.

    Reads both the .hsl and .sub files, parses all block markers,
    applies cross-file row numbering, and generates a matching .med file.

    Args:
        hsl_path: Path to the .hsl source file.
        med_path: Path for the output .med file.  Defaults to same base name
            with ``.med`` extension.
        activity_data_base64: Pre-loaded ActivityData blob.  If ``None``,
            extracted from existing .med or loaded from the template.
        activity_data_template: Path to ``activityDataTemplate.b64`` file.

    Raises:
        OSError: If the .hsl file cannot be read or the .med file cannot
            be written.
    """
    if not med_path:
        med_path = re.sub(r'\.hsl$', '.med', hsl_path, flags=re.IGNORECASE)

    hsl_content = Path(hsl_path).read_text(encoding="utf-8")
    hsl_markers = parse_block_markers(hsl_content)

    # Group step markers by instance GUID
    step_map: Dict[str, HslStepRecord] = {}

    for marker in hsl_markers:
        if not isinstance(marker, StepBlockMarker):
            continue

        code = "\n".join(marker.code_lines)
        guid = marker.instance_guid

        if guid in step_map:
            existing = step_map[guid]
            new_blocks = list(existing.blocks)
            new_blocks.append(HslStepBlock(
                block_index=len(new_blocks) + 1,
                code=code,
                row=marker.row,
            ))
            step_map[guid] = HslStepRecord(
                instance_guid=existing.instance_guid,
                clsid=existing.clsid,
                blocks=new_blocks,
            )
        else:
            step_map[guid] = HslStepRecord(
                instance_guid=guid,
                clsid=marker.step_clsid,
                blocks=[HslStepBlock(block_index=1, code=code, row=marker.row)],
            )

    # Parse .sub markers
    sub_path = re.sub(r'\.hsl$', '.sub', hsl_path, flags=re.IGNORECASE)
    if os.path.exists(sub_path):
        sub_content = Path(sub_path).read_text(encoding="utf-8")
        sub_markers = parse_block_markers(sub_content)

        for marker in sub_markers:
            if not isinstance(marker, StepBlockMarker):
                continue

            code = "\n".join(marker.code_lines)
            guid = marker.instance_guid

            if guid in step_map:
                existing = step_map[guid]
                new_blocks = list(existing.blocks)
                new_blocks.append(HslStepBlock(
                    block_index=len(new_blocks) + 1,
                    code=code,
                    row=marker.row,
                ))
                step_map[guid] = HslStepRecord(
                    instance_guid=existing.instance_guid,
                    clsid=existing.clsid,
                    blocks=new_blocks,
                )
            else:
                step_map[guid] = HslStepRecord(
                    instance_guid=guid,
                    clsid=marker.step_clsid,
                    blocks=[HslStepBlock(block_index=1, code=code, row=marker.row)],
                )

    # Extract submethod info from .sub
    submethods = extract_submethods_from_sub(sub_path)

    # Extract device declarations from .hsl
    devices = extract_devices_from_hsl(hsl_content)

    # Detect enabled components
    enabled_components = detect_enabled_components(hsl_content)

    # Get or generate ActivityData
    if not activity_data_base64:
        if os.path.exists(med_path):
            try:
                activity_data_base64 = extract_activity_data_from_med(med_path)
            except (ValueError, OSError):
                pass
        if not activity_data_base64:
            activity_data_base64 = get_default_activity_data(activity_data_template)

    # Generate .med text
    med_text = build_med_text(
        activity_data_base64,
        step_map,
        submethods,
        devices,
        enabled_components,
    )

    # Transactional write
    transactional_binary_write(med_path, med_text)
    logger.info("Synced .med: %s", os.path.basename(med_path))


# ═════════════════════════════════════════════════════════════════════════════════
# .stp Sync
# ═════════════════════════════════════════════════════════════════════════════════


def sync_stp_from_hsl(
    hsl_path: str,
    stp_path: Optional[str] = None,
) -> None:
    """Synchronize the .stp file from .hsl + .sub block markers.

    For each device step GUID found in the code:
    - If a section already exists in the .stp, preserve it
    - If not, generate a default section with minimal valid parameters

    Device step GUIDs not found in code are removed (orphan cleanup).

    Args:
        hsl_path: Path to the .hsl source file.
        stp_path: Path for the output .stp file.  Defaults to same base name
            with ``.stp`` extension.
    """
    if not stp_path:
        stp_path = re.sub(r'\.hsl$', '.stp', hsl_path, flags=re.IGNORECASE)

    hsl_content = Path(hsl_path).read_text(encoding="utf-8")
    all_markers = list(parse_block_markers(hsl_content))

    sub_path = re.sub(r'\.hsl$', '.sub', hsl_path, flags=re.IGNORECASE)
    if os.path.exists(sub_path):
        sub_content = Path(sub_path).read_text(encoding="utf-8")
        all_markers.extend(parse_block_markers(sub_content))

    # Collect device step GUIDs and their code
    device_steps: Dict[str, Dict[str, str]] = {}
    for marker in all_markers:
        if not isinstance(marker, StepBlockMarker):
            continue
        if not _is_device_step_clsid(marker.step_clsid):
            continue

        if marker.instance_guid not in device_steps:
            device_steps[marker.instance_guid] = {
                "clsid": marker.step_clsid,
                "code": "\n".join(marker.code_lines),
            }

        # Also check actual code for differing GUIDs
        actual_call = extract_device_call_from_code(marker.code_lines)
        if actual_call and actual_call.instance_guid not in device_steps:
            device_steps[actual_call.instance_guid] = {
                "clsid": actual_call.clsid,
                "code": "\n".join(marker.code_lines),
            }
            logger.info(
                "Found code-level GUID %s (differs from comment GUID %s)",
                actual_call.instance_guid,
                marker.instance_guid,
            )

    if not device_steps and not os.path.exists(stp_path):
        return

    # Read existing .stp sections
    existing_sections: Dict[str, str] = {}
    if os.path.exists(stp_path):
        tmp_text_path = stp_path + f".sync_{_unique_temp_suffix()}.txt"
        try:
            binary_to_text_file(Path(stp_path), Path(tmp_text_path))
            existing_text = Path(tmp_text_path).read_text(encoding="latin1")
            existing_sections = _parse_existing_stp_sections(existing_text)
        finally:
            _safe_unlink(tmp_text_path)

    # Build and write
    stp_text = build_stp_text(device_steps, existing_sections)
    transactional_binary_write(stp_path, stp_text)
    logger.info("Synced .stp: %s", os.path.basename(stp_path))


# ═════════════════════════════════════════════════════════════════════════════════
# On-Save Handler
# ═════════════════════════════════════════════════════════════════════════════════


def correct_block_markers_on_save(file_path: str) -> None:
    """On-save handler: correct block markers and sync companion files.

    Handles BOTH ``.hsl`` AND ``.sub`` files:
    - For .hsl files: renumber rows (starting at 1), then sync .med
    - For .sub files: renumber rows (starting after .hsl's last row), then sync .med

    **Guards** -- this function is a no-op (returns immediately) when ANY of:
    1. The file is not a .hsl or .sub file
    2. The .hsl file has no existing step block markers (i.e. it's a library)
    3. No companion .med or .smt file exists with the same base name

    When all guards pass, the function:
    1. Reconciles block marker headers with actual code
    2. Applies cross-file row numbering
    3. Writes corrected content back to disk (only if changed)
    4. Re-syncs the companion .med from the corrected .hsl + .sub
    5. Re-syncs the companion .stp for device steps
    6. Updates checksums on .hsl and .sub text files

    Args:
        file_path: Path to the saved .hsl or .sub file.

    Raises:
        OSError: If .med or .stp sync fails (propagated for caller to report).
            The .med may already have been updated when a later .stp sync fails.
    """
    # Guard 1: must be .hsl or .sub
    is_hsl = file_path.lower().endswith(".hsl")
    is_sub = file_path.lower().endswith(".sub")
    if not is_hsl and not is_sub:
        logger.info("Skipping %s -- not .hsl or .sub", os.path.basename(file_path))
        return

    # Resolve paths
    base_path = re.sub(r'\.(hsl|sub)$', '', file_path, flags=re.IGNORECASE)
    hsl_path = base_path + ".hsl"
    sub_path = base_path + ".sub"
    med_path = base_path + ".med"
    smt_path = base_path + ".smt"
    stp_path = base_path + ".stp"

    # Guard 2: .hsl must exist and contain step block markers
    if not os.path.exists(hsl_path):
        logger.info("Skipping -- .hsl not found: %s", hsl_path)
        return

    try:
        hsl_content = Path(hsl_path).read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read .hsl: %s", e)
        return

    if not has_step_block_markers(hsl_content):
        logger.info("Skipping %s -- no step block markers (library file)", os.path.basename(hsl_path))
        return

    # Guard 3: companion .med or .smt must exist
    has_med = os.path.exists(med_path)
    has_smt = os.path.exists(smt_path)
    if not has_med and not has_smt:
        logger.info(
            "Skipping %s -- no companion .med or .smt found", os.path.basename(hsl_path)
        )
        return

    logger.info(
        "Starting sync for %s (med=%s, smt=%s)",
        os.path.basename(hsl_path), has_med, has_smt,
    )

    # Step 0: Reconcile block marker headers
    reconciled_hsl = reconcile_block_marker_headers(hsl_content)
    if reconciled_hsl != hsl_content:
        hsl_content = reconciled_hsl
        try:
            Path(hsl_path).write_text(reconciled_hsl, encoding="utf-8")
            logger.info("Reconciled block marker headers in %s", os.path.basename(hsl_path))
        except OSError:
            return

    # Step 1: Renumber .hsl block markers (rows starting at 1)
    corrected_hsl = renumber_block_markers(hsl_content)
    if corrected_hsl != hsl_content:
        try:
            Path(hsl_path).write_text(corrected_hsl, encoding="utf-8")
        except OSError:
            return

    # Count the last row number in the .hsl
    last_hsl_row = 0
    row_re = re.compile(r'^//\s*\{\{\{?\s+(\d+)\s+\d+\s+\d+\s+"[^"]+"\s+"[^"]+"', re.MULTILINE)
    for m in row_re.finditer(corrected_hsl):
        row = int(m.group(1))
        if row > last_hsl_row:
            last_hsl_row = row

    # Step 2: Renumber .sub block markers (rows starting at lastHslRow + 1)
    if os.path.exists(sub_path):
        try:
            sub_content = Path(sub_path).read_text(encoding="utf-8")
        except OSError:
            sub_content = ""

        if sub_content and has_step_block_markers(sub_content):
            # Reconcile .sub headers
            reconciled_sub = reconcile_block_marker_headers(sub_content)
            if reconciled_sub != sub_content:
                sub_content = reconciled_sub
                try:
                    Path(sub_path).write_text(reconciled_sub, encoding="utf-8")
                    logger.info(
                        "Reconciled block marker headers in %s", os.path.basename(sub_path)
                    )
                except OSError:
                    pass

            # Renumber .sub rows continuing from .hsl
            sub_counter_state = {"value": last_hsl_row}

            def _sub_renumber(m: re.Match) -> str:
                sub_counter_state["value"] += 1
                return f"{m.group(1)}{sub_counter_state['value']}{m.group(2)}"

            corrected_sub = re.sub(
                r'^(//\s*\{\{\{?\s+)\d+(\s+\d+\s+\d+\s+"[^"]+"\s+"[^"]+")',
                _sub_renumber,
                sub_content,
                flags=re.MULTILINE,
            )
            if corrected_sub != sub_content:
                try:
                    Path(sub_path).write_text(corrected_sub, encoding="utf-8")
                except OSError:
                    pass

    # Step 3: Sync .med
    if has_med:
        try:
            sync_med_from_hsl(hsl_path, med_path)
        except Exception as e:
            logger.error(
                ".med sync failed for %s: %s", os.path.basename(hsl_path), e
            )
            raise OSError(f".med sync failed: {e}") from e

    # Step 4: Sync .stp
    try:
        sync_stp_from_hsl(hsl_path, stp_path)
    except Exception as e:
        logger.error(
            ".stp sync failed for %s: %s", os.path.basename(hsl_path), e
        )
        raise OSError(f".stp sync failed: {e}") from e

    # Step 5: Update checksums on text companion files (.hsl, .sub only)
    for f in (hsl_path, sub_path):
        if os.path.exists(f):
            try:
                update_checksum_in_file(f)
            except (ValueError, OSError) as e:
                logger.error(
                    "Checksum update failed for %s: %s", os.path.basename(f), e
                )


# ═════════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════════════════════════════════════


def main() -> int:
    """CLI entry point for .med generation and sync operations.

    Subcommands:

    ``sync``
        Synchronize a .med file from an .hsl file::

            python -m standalone_med_tools.med_generator sync MyMethod.hsl

    ``on-save``
        Run the full on-save pipeline (reconcile, renumber, sync, checksum)::

            python -m standalone_med_tools.med_generator on-save MyMethod.hsl

    ``build-text``
        Build .med text (without binary conversion) and print to stdout::

            python -m standalone_med_tools.med_generator build-text MyMethod.hsl

    Returns:
        Exit code (0 = success, 1 = error).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="med_generator",
        description="Hamilton .med file generator and sync pipeline",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # sync command
    p_sync = sub.add_parser("sync", help="Sync a .med from an .hsl file")
    p_sync.add_argument("hsl", type=str, help="Path to the .hsl file")
    p_sync.add_argument("--med", type=str, default=None, help="Output .med path")
    p_sync.add_argument(
        "--activity-data", type=str, default=None,
        help="Path to activityDataTemplate.b64",
    )

    # on-save command
    p_save = sub.add_parser("on-save", help="Run the on-save sync pipeline")
    p_save.add_argument("file", type=str, help="Path to the .hsl or .sub file")

    # build-text command
    p_text = sub.add_parser(
        "build-text", help="Build .med text and print to stdout"
    )
    p_text.add_argument("hsl", type=str, help="Path to the .hsl file")
    p_text.add_argument(
        "--activity-data", type=str, default=None,
        help="Path to activityDataTemplate.b64",
    )

    # stp-sync command
    p_stp = sub.add_parser("stp-sync", help="Sync a .stp from an .hsl file")
    p_stp.add_argument("hsl", type=str, help="Path to the .hsl file")
    p_stp.add_argument("--stp", type=str, default=None, help="Output .stp path")

    args = parser.parse_args()

    try:
        if args.cmd == "sync":
            sync_med_from_hsl(
                args.hsl,
                med_path=args.med,
                activity_data_template=args.activity_data,
            )
            print(f"Synced .med for {args.hsl}")
            return 0

        elif args.cmd == "on-save":
            correct_block_markers_on_save(args.file)
            print(f"On-save sync complete for {args.file}")
            return 0

        elif args.cmd == "build-text":
            hsl_path = args.hsl
            hsl_content = Path(hsl_path).read_text(encoding="utf-8")
            markers = parse_block_markers(hsl_content)

            step_map: Dict[str, HslStepRecord] = {}
            for marker in markers:
                if not isinstance(marker, StepBlockMarker):
                    continue
                code = "\n".join(marker.code_lines)
                guid = marker.instance_guid
                if guid in step_map:
                    existing = step_map[guid]
                    new_blocks = list(existing.blocks)
                    new_blocks.append(HslStepBlock(
                        block_index=len(new_blocks) + 1, code=code, row=marker.row,
                    ))
                    step_map[guid] = HslStepRecord(
                        instance_guid=existing.instance_guid,
                        clsid=existing.clsid,
                        blocks=new_blocks,
                    )
                else:
                    step_map[guid] = HslStepRecord(
                        instance_guid=guid,
                        clsid=marker.step_clsid,
                        blocks=[HslStepBlock(block_index=1, code=code, row=marker.row)],
                    )

            sub_path = re.sub(r'\.hsl$', '.sub', hsl_path, flags=re.IGNORECASE)
            submethods = extract_submethods_from_sub(sub_path)
            devices = extract_devices_from_hsl(hsl_content)
            components = detect_enabled_components(hsl_content)

            activity_data = get_default_activity_data(args.activity_data)

            med_text = build_med_text(
                activity_data, step_map, submethods, devices, components,
            )
            sys.stdout.write(med_text)
            return 0

        elif args.cmd == "stp-sync":
            sync_stp_from_hsl(args.hsl, stp_path=args.stp)
            print(f"Synced .stp for {args.hsl}")
            return 0

    except Exception as e:
        logger.error("Error: %s", e)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
