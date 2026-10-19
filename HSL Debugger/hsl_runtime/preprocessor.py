"""
HSL Preprocessor
================
Handles #include, #define, #ifdef/#ifndef/#endif, #pragma once directives.
Resolves file includes relative to Hamilton's Library and Methods directories.

SIMULATION ONLY - No hardware interaction.
"""

import os
import re
from pathlib import Path
from typing import Optional


class PreprocessorError(Exception):
    """Error during preprocessing."""
    def __init__(self, message: str, file: str = "", line: int = 0):
        self.file = file
        self.line = line
        super().__init__(f"{file}({line}): Preprocessor error: {message}")


class Preprocessor:
    """HSL Preprocessor - resolves includes, defines, and conditional compilation."""

    # Default Hamilton installation paths
    DEFAULT_HAMILTON_DIR = r"C:\Program Files (x86)\Hamilton"

    def __init__(self, hamilton_dir: Optional[str] = None, strict: bool = False):
        self.hamilton_dir = hamilton_dir or self.DEFAULT_HAMILTON_DIR
        self.strict = strict
        self.search_paths = [
            os.path.join(self.hamilton_dir, "Library"),
            os.path.join(self.hamilton_dir, "Methods"),
            os.path.join(self.hamilton_dir, "Methods", "Library Demo Methods"),
            os.path.join(self.hamilton_dir, "Bin"),
        ]
        self.defines: dict[str, str] = {}
        self.pragma_once_files: set[str] = set()
        self.included_files: set[str] = set()
        self.include_stack: list[str] = []
        self.source_map: list[tuple[str, int]] = []  # (original_file, original_line)
        self._current_file = ""
        self._max_include_depth = 50
        self._basename_cache: dict[str, Optional[str]] = {}

        # Always define HSL_RUNTIME for simulation mode
        self.defines["HSL_RUNTIME"] = "1"

    def add_search_path(self, path: str):
        """Add a directory to the include search path."""
        if path not in self.search_paths:
            self.search_paths.insert(0, path)

    def resolve_include(self, include_path: str, relative_to: str = "") -> Optional[str]:
        """Resolve an include path to an absolute file path."""
        # Handle __filename__ token
        if "__filename__" in include_path:
            if relative_to:
                base = os.path.splitext(relative_to)[0]
                include_path = include_path.replace('__filename__', f'"{base}"')
                # Clean up double quotes from the replacement
                include_path = include_path.replace('""', '')
                include_path = include_path.strip('"')
                # Handle concatenation like __filename__ ".sub"
                parts = include_path.split('" "')
                if len(parts) == 1:
                    include_path = include_path.strip('"')

        # Clean path
        include_path = include_path.strip('"').strip("'").strip()

        # Try relative to current file first
        if relative_to:
            rel_dir = os.path.dirname(relative_to)
            candidate = os.path.normpath(os.path.join(rel_dir, include_path))
            if os.path.isfile(candidate):
                return candidate

        # Try each search path
        for search_dir in self.search_paths:
            candidate = os.path.normpath(os.path.join(search_dir, include_path))
            if os.path.isfile(candidate):
                return candidate

        # Try the include path as absolute
        if os.path.isabs(include_path) and os.path.isfile(include_path):
            return include_path

        # Exported methods often retain an absolute path from another VENUS
        # workstation. If exactly one file with the same basename exists in
        # the active Hamilton Library/Methods trees, use that unambiguous
        # local equivalent. Never guess when multiple candidates exist.
        basename = os.path.basename(include_path.replace('/', os.sep))
        key = basename.lower()
        if basename:
            if key not in self._basename_cache:
                matches = []
                for root_name in ("Library", "Methods"):
                    root = os.path.join(self.hamilton_dir, root_name)
                    if not os.path.isdir(root):
                        continue
                    for directory, _, filenames in os.walk(root):
                        for filename in filenames:
                            if filename.lower() == key:
                                matches.append(os.path.join(directory, filename))
                                if len(matches) > 1:
                                    break
                        if len(matches) > 1:
                            break
                    if len(matches) > 1:
                        break
                self._basename_cache[key] = matches[0] if len(matches) == 1 else None
            fallback = self._basename_cache[key]
            if fallback is not None:
                return fallback

        return None

    def preprocess_file(self, filepath: str) -> str:
        """Preprocess an HSL file, resolving all includes and directives."""
        filepath = os.path.normpath(os.path.abspath(filepath))
        if not os.path.isfile(filepath):
            raise PreprocessorError(f"File not found: {filepath}")

        # Add the file's directory to search paths
        file_dir = os.path.dirname(filepath)
        self.add_search_path(file_dir)

        content = self._read_source_file(filepath)

        return self.preprocess(content, filepath)

    def preprocess(self, source: str, filepath: str = "<string>") -> str:
        """Preprocess HSL source code."""
        self._current_file = filepath
        filepath_norm = os.path.normpath(filepath)

        # Check pragma once
        if filepath_norm in self.pragma_once_files:
            return ""

        # Check include depth
        if len(self.include_stack) >= self._max_include_depth:
            raise PreprocessorError(
                f"Maximum include depth ({self._max_include_depth}) exceeded",
                filepath, 0
            )

        self.include_stack.append(filepath_norm)
        self.included_files.add(filepath_norm)

        try:
            return self._preprocess_content(source, filepath)
        finally:
            # A failed nested include must not poison later files processed by
            # the same Preprocessor instance. Remove this exact frame even if
            # an inner include raised before it could return normally.
            for index in range(len(self.include_stack) - 1, -1, -1):
                if self.include_stack[index] == filepath_norm:
                    del self.include_stack[index]
                    break

    def _preprocess_content(self, source: str, filepath: str) -> str:
        """Process one source unit after its include frame has been installed."""
        lines = source.split('\n')
        output_lines = []
        condition_stack: list[bool] = []  # Stack of if/ifdef conditions: True = active
        in_block_comment = False

        for line_num, line in enumerate(lines, 1):
            directive_line, in_block_comment = self._without_comments(
                line, in_block_comment
            )
            stripped = directive_line.strip()

            # Handle preprocessor directives
            if stripped.startswith('#'):
                directive_result = self._handle_directive(
                    stripped, filepath, line_num, condition_stack
                )
                if directive_result is not None:
                    output_lines.append(directive_result)
                    self.source_map.append((filepath, line_num))
                else:
                    # Add empty line to preserve line numbering
                    output_lines.append("")
                    self.source_map.append((filepath, line_num))
                continue

            # If we're in a false conditional block, skip
            if condition_stack and not all(condition_stack):
                output_lines.append("")
                self.source_map.append((filepath, line_num))
                continue

            # Handle inline includes: namespace _Method { #include "file.hsl" }
            inline_include = re.match(
                r'(.*?)\s*#include\s+(".*?"|<.*?>|__filename__\s*"[^"]*")\s*(.*)',
                directive_line
            )
            if inline_include:
                prefix = inline_include.group(1).strip()
                inc_path = inline_include.group(2)
                suffix = inline_include.group(3).strip()
                
                resolved = self._resolve_and_read_include(inc_path, filepath, line_num)

                if prefix:
                    output_lines.append(prefix)
                    self.source_map.append((filepath, line_num))
                if resolved:
                    for res_line in resolved.split('\n'):
                        output_lines.append(res_line)
                        self.source_map.append((filepath, line_num))
                if suffix and suffix != '}':
                    output_lines.append(suffix)
                    self.source_map.append((filepath, line_num))
                elif suffix == '}':
                    output_lines.append(suffix)
                    self.source_map.append((filepath, line_num))
                continue

            # Perform macro substitution
            processed_line = self._substitute_defines(line)
            output_lines.append(processed_line)
            self.source_map.append((filepath, line_num))

        return '\n'.join(output_lines)

    @staticmethod
    def _read_source_file(filepath: str) -> str:
        """Read UTF-8 exports and legacy ANSI/Windows-1252 Hamilton files."""
        data = Path(filepath).read_bytes()
        try:
            return data.decode('utf-8-sig')
        except UnicodeDecodeError:
            return data.decode('cp1252')

    @staticmethod
    def _without_comments(line: str, in_block_comment: bool) -> tuple[str, bool]:
        """Mask comments so sample `#include` text is never executed."""
        output: list[str] = []
        index = 0
        quote = ""
        while index < len(line):
            if in_block_comment:
                end = line.find('*/', index)
                if end < 0:
                    return ''.join(output), True
                index = end + 2
                in_block_comment = False
                continue

            char = line[index]
            if quote:
                output.append(char)
                if char == '\\' and index + 1 < len(line):
                    output.append(line[index + 1])
                    index += 2
                    continue
                if char == quote:
                    quote = ""
                index += 1
                continue

            if char in ('"', "'"):
                quote = char
                output.append(char)
                index += 1
                continue
            if line.startswith('//', index):
                break
            if line.startswith('/*', index):
                in_block_comment = True
                index += 2
                continue

            output.append(char)
            index += 1

        return ''.join(output), in_block_comment

    @classmethod
    def _include_guard_symbol(cls, source: str) -> Optional[str]:
        """Return a conventional leading #ifndef/#define include guard."""
        meaningful: list[str] = []
        in_block_comment = False
        for line in source.splitlines():
            code, in_block_comment = cls._without_comments(line, in_block_comment)
            stripped = code.strip()
            if stripped:
                meaningful.append(stripped)
                if len(meaningful) == 2:
                    break
        if len(meaningful) < 2:
            return None
        guard = re.fullmatch(r'#ifndef\s+(\w+)', meaningful[0])
        if not guard:
            return None
        symbol = guard.group(1)
        definition = re.match(r'#define\s+' + re.escape(symbol) + r'(?:\s|$)', meaningful[1])
        return symbol if definition else None

    def _handle_directive(self, line: str, filepath: str, line_num: int,
                          condition_stack: list[bool]) -> Optional[str]:
        """Handle a preprocessor directive. Returns replacement text or None."""

        # #pragma once
        if line.startswith('#pragma once') or line.startswith('#pragma\tonce'):
            self.pragma_once_files.add(os.path.normpath(filepath))
            return None

        # #pragma warning (ignore)
        if line.startswith('#pragma'):
            return None

        # #define
        m = re.match(r'#define\s+(\w+)\s*(.*)', line)
        if m:
            if condition_stack and not all(condition_stack):
                return None
            name = m.group(1)
            value = m.group(2).strip()
            self.defines[name] = value
            return None

        # #undef
        m = re.match(r'#undef\s+(\w+)', line)
        if m:
            if condition_stack and not all(condition_stack):
                return None
            self.defines.pop(m.group(1), None)
            return None

        # #ifdef
        m = re.match(r'#ifdef\s+(\w+)', line)
        if m:
            symbol = m.group(1)
            condition_stack.append(symbol in self.defines)
            return None

        # #ifndef
        m = re.match(r'#ifndef\s+(\w+)', line)
        if m:
            symbol = m.group(1)
            condition_stack.append(symbol not in self.defines)
            return None

        # #else
        if line.strip() == '#else':
            if condition_stack:
                condition_stack[-1] = not condition_stack[-1]
            return None

        # #endif
        if line.strip().startswith('#endif'):
            if condition_stack:
                condition_stack.pop()
            return None

        # #if (simplified - treat as ifdef for common patterns)
        m = re.match(r'#if\s+defined\s*\(\s*(\w+)\s*\)', line)
        if m:
            symbol = m.group(1)
            condition_stack.append(symbol in self.defines)
            return None

        # #include (standalone)
        m = re.match(r'#include\s+(".*?"|<.*?>|__filename__\s*"[^"]*")', line)
        if m:
            if condition_stack and not all(condition_stack):
                return None
            inc_path = m.group(1)
            return self._resolve_and_read_include(inc_path, filepath, line_num)

        # Unknown directive - pass through as comment
        return f"/* preprocessor: {line} */"

    def _resolve_and_read_include(self, inc_path: str, filepath: str,
                                   line_num: int) -> str:
        """Resolve an include path and return preprocessed contents."""
        # Handle __filename__ ".sub" pattern
        if '__filename__' in inc_path:
            base_name = os.path.splitext(os.path.basename(filepath))[0]
            # Extract the suffix (e.g., ".sub")
            suffix_match = re.search(r'"([^"]*)"', inc_path.replace('__filename__', ''))
            if suffix_match:
                resolved_name = base_name + suffix_match.group(1)
            else:
                resolved_name = base_name
            resolved = self.resolve_include(resolved_name, filepath)
        else:
            cleaned = inc_path.strip('"').strip('<').strip('>')
            resolved = self.resolve_include(cleaned, filepath)

        if resolved is None:
            cleaned = inc_path.strip('"').strip('<').strip('>')
            if self.strict:
                raise PreprocessorError(
                    f"Include not found: {cleaned}", filepath, line_num
                )
            # Non-fatal in permissive simulation mode.
            return f"/* [PREPROCESSOR] Include not found: {cleaned} */"

        resolved_norm = os.path.normpath(resolved)

        # Check pragma once
        if resolved_norm in self.pragma_once_files:
            return ""

        try:
            content = self._read_source_file(resolved)
            guard_symbol = self._include_guard_symbol(content)
            if guard_symbol and guard_symbol in self.defines:
                return ""
            if resolved_norm in self.include_stack:
                # Hamilton's installed VM libraries contain an intentional
                # unguarded mutual include. The translator treats active-file
                # re-entry as already included, so mirror that behavior while
                # still rejecting paths that cannot be resolved at all.
                return ""
            return self.preprocess(content, resolved)
        except Exception as e:
            if self.strict:
                if isinstance(e, PreprocessorError):
                    raise
                raise PreprocessorError(
                    f"Error including {resolved}: {e}", filepath, line_num
                ) from e
            return f"/* [PREPROCESSOR] Error including {resolved}: {e} */"

    def _substitute_defines(self, line: str) -> str:
        """Substitute defined macros in a line (simple text replacement)."""
        # Only do simple whole-word substitution for non-empty defines
        for name, value in self.defines.items():
            if value and name in line:
                # Only replace whole words
                line = re.sub(r'\b' + re.escape(name) + r'\b', value, line)
        return line
