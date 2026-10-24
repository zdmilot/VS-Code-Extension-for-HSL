"""Comprehensive unit tests for standalone_med_tools.med_generator."""

from __future__ import annotations

import os
import sys
import unittest

try:
    from standalone_med_tools.med_generator import (
        split_function_args,
        parse_function_call_code,
        parse_array_method_call,
        build_step_section,
        build_med_text,
        extract_activity_data_from_med,
        sync_med_from_hsl,
        FunctionCallInfo,
        ArrayMethodCallInfo,
        HslStepBlock,
        HslStepRecord,
        ComponentFlags,
        DeviceInfo,
        SubmethodInfo,
        STEP_CLSID,
    )
    from standalone_med_tools.block_markers import STEP_CLSID as BM_STEP_CLSID

    _IMPORT_OK = True
    _IMPORT_ERROR = None
except ImportError as exc:
    _IMPORT_OK = False
    _IMPORT_ERROR = str(exc)


def _skip_if_not_importable(cls):
    """Class decorator: skip the entire TestCase when the module cannot be imported."""
    if not _IMPORT_OK:
        return unittest.skip(f"Cannot import med_generator: {_IMPORT_ERROR}")(cls)
    return cls


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_step(clsid_key: str, code: str, guid: str = "test_guid_0001") -> "HslStepRecord":
    """Build a minimal ``HslStepRecord`` for testing."""
    return HslStepRecord(
        instance_guid=guid,
        clsid=STEP_CLSID[clsid_key],
        blocks=[HslStepBlock(block_index=1, code=code, row=1)],
    )


# ═════════════════════════════════════════════════════════════════════════════
# split_function_args
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestSplitFunctionArgs(unittest.TestCase):
    """Tests for ``split_function_args``."""

    def test_split_function_args_simple(self):
        result = split_function_args("a, b, c")
        self.assertEqual(result, ["a", "b", "c"])

    def test_split_function_args_nested_parens(self):
        result = split_function_args("f(a,b), c")
        self.assertEqual(result, ["f(a,b)", "c"])

    def test_split_function_args_quoted_commas(self):
        result = split_function_args('"hello, world", 42')
        self.assertEqual(result, ['"hello, world"', "42"])

    def test_split_function_args_empty(self):
        result = split_function_args("")
        self.assertEqual(result, [])

    def test_split_function_args_brackets(self):
        result = split_function_args("a[1,2], b")
        self.assertEqual(result, ["a[1,2]", "b"])

    def test_split_function_args_single(self):
        result = split_function_args("x")
        self.assertEqual(result, ["x"])

    def test_split_function_args_deeply_nested(self):
        result = split_function_args("f(g(a,b),c), d")
        self.assertEqual(result, ["f(g(a,b),c)", "d"])

    def test_split_function_args_whitespace_only(self):
        result = split_function_args("   ")
        self.assertEqual(result, [])

    def test_split_function_args_mixed_nesting(self):
        result = split_function_args('f(a[1,2]), "x,y", z')
        self.assertEqual(result, ['f(a[1,2])', '"x,y"', "z"])


# ═════════════════════════════════════════════════════════════════════════════
# parse_function_call_code
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestParseFunctionCallCode(unittest.TestCase):
    """Tests for ``parse_function_call_code``."""

    def test_parse_function_call_simple(self):
        info = parse_function_call_code("MyFunc(a, b);")
        self.assertEqual(info.function_name, "MyFunc")
        self.assertEqual(info.args, ["a", "b"])
        self.assertEqual(info.return_var, "")

    def test_parse_function_call_namespaced(self):
        info = parse_function_call_code("Lib::Func(x);")
        self.assertEqual(info.function_name, "Lib::Func")
        self.assertEqual(info.args, ["x"])

    def test_parse_function_call_no_args(self):
        info = parse_function_call_code("NoArgs();")
        self.assertEqual(info.function_name, "NoArgs")
        self.assertEqual(info.args, [])

    def test_parse_function_call_not_a_call(self):
        info = parse_function_call_code("x = 5")
        # Returns an empty FunctionCallInfo when there's no match
        self.assertEqual(info.function_name, "")
        self.assertEqual(info.args, [])

    def test_parse_function_call_with_return(self):
        info = parse_function_call_code("result = Namespace::DoThing(a, b);")
        self.assertEqual(info.return_var, "result")
        self.assertEqual(info.function_name, "Namespace::DoThing")
        self.assertEqual(info.args, ["a", "b"])

    def test_parse_function_call_with_braces(self):
        info = parse_function_call_code("{ MyFunc(a); }")
        self.assertEqual(info.function_name, "MyFunc")
        self.assertEqual(info.args, ["a"])

    def test_parse_function_call_string_arg(self):
        info = parse_function_call_code('Trace("hello, world");')
        self.assertEqual(info.function_name, "Trace")
        self.assertEqual(info.args, ['"hello, world"'])

    def test_parse_function_call_multiple_namespaces(self):
        info = parse_function_call_code("HSLExtensions::File::Open(path, mode);")
        self.assertEqual(info.function_name, "HSLExtensions::File::Open")
        self.assertEqual(info.args, ["path", "mode"])


# ═════════════════════════════════════════════════════════════════════════════
# parse_array_method_call
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestParseArrayMethodCall(unittest.TestCase):
    """Tests for ``parse_array_method_call``."""

    def test_parse_array_method_add(self):
        info = parse_array_method_call('arr.AddAsLast("item");')
        self.assertEqual(info.array_name, "arr")
        self.assertEqual(info.method, "AddAsLast")
        self.assertTrue(info.is_add_as_last)
        self.assertEqual(info.value, '"item"')

    def test_parse_array_method_set_at(self):
        info = parse_array_method_call("arr.SetAt(0, val);")
        self.assertEqual(info.array_name, "arr")
        self.assertEqual(info.method, "SetAt")
        self.assertEqual(info.index, "0")
        self.assertEqual(info.value, "val")

    def test_parse_array_method_set_size(self):
        info = parse_array_method_call("myArr.SetSize(10);")
        self.assertEqual(info.array_name, "myArr")
        self.assertEqual(info.method, "SetSize")
        self.assertEqual(info.value, "10")

    def test_parse_array_method_get_at(self):
        info = parse_array_method_call("result = data.GetAt(3);")
        self.assertEqual(info.return_var, "result")
        self.assertEqual(info.array_name, "data")
        self.assertEqual(info.method, "GetAt")
        self.assertEqual(info.index, "3")

    def test_parse_array_method_get_size(self):
        info = parse_array_method_call("n = data.GetSize();")
        self.assertEqual(info.return_var, "n")
        self.assertEqual(info.array_name, "data")
        self.assertEqual(info.method, "GetSize")

    def test_parse_array_method_invalid(self):
        info = parse_array_method_call("not a call")
        self.assertEqual(info.array_name, "")
        self.assertEqual(info.method, "")


# ═════════════════════════════════════════════════════════════════════════════
# build_step_section
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestBuildStepSection(unittest.TestCase):
    """Tests for ``build_step_section``."""

    def test_build_step_section_basic(self):
        step = _make_step("Comment", "// My comment")
        section = build_step_section(step)
        # Must contain the DataDef header with our GUID
        self.assertIn("DataDef,HxPars,3,test_guid_0001,", section)
        # Must contain the code
        self.assertIn("// My comment", section)

    def test_build_step_section_with_label(self):
        step = _make_step("Assignment", "x = 5;")
        section = build_step_section(step)
        # Section header present
        self.assertIn("DataDef,HxPars,3,test_guid_0001,", section)
        # Code is embedded
        self.assertIn("x = 5;", section)

    def test_build_step_section_function_call(self):
        step = _make_step("SingleLibFunction", 'Lib::DoThing(a, "hello");')
        section = build_step_section(step)
        self.assertIn("DataDef,HxPars,3,", section)
        self.assertIn("BlockData", section)
        self.assertIn("ParamValue", section)

    def test_build_step_section_contains_timestamp(self):
        step = _make_step("Comment", "// test")
        section = build_step_section(step)
        self.assertIn("Timestamp", section)

    def test_build_step_section_loop(self):
        step = _make_step("Loop", "loopCounter = 0; loopCounter < 10; loopCounter++")
        section = build_step_section(step)
        self.assertIn("DataDef,HxPars,3,", section)
        self.assertIn("BlockData", section)

    def test_build_step_section_if_then_else(self):
        """IfThenElse steps may have multiple blocks."""
        blocks = [
            HslStepBlock(block_index=1, code="x > 0", row=1),
            HslStepBlock(block_index=2, code="{ y = 1; }", row=2),
            HslStepBlock(block_index=3, code="{ y = 0; }", row=3),
        ]
        step = HslStepRecord(
            instance_guid="guid_ite_001",
            clsid=STEP_CLSID["IfThenElse"],
            blocks=blocks,
        )
        section = build_step_section(step)
        self.assertIn("DataDef,HxPars,3,guid_ite_001,", section)
        # All block indices should appear
        self.assertIn('"(1"', section)
        self.assertIn('"(2"', section)
        self.assertIn('"(3"', section)

    def test_build_step_section_device_step(self):
        """Device steps (prefixed CLSID) use a minimal stub."""
        step = HslStepRecord(
            instance_guid="guid_dev_001",
            clsid="ML_STAR:{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",
            blocks=[HslStepBlock(block_index=1, code="Aspirate(seq);", row=1)],
        )
        section = build_step_section(step)
        self.assertIn("DataDef,HxPars,3,guid_dev_001,", section)


# ═════════════════════════════════════════════════════════════════════════════
# build_med_text
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestBuildMedText(unittest.TestCase):
    """Tests for ``build_med_text``."""

    def _default_components(self) -> "ComponentFlags":
        return ComponentFlags(
            sched_comp_cmd=False,
            custom_dialog_comp_cmd=False,
            multi_pip_comp_cmd=False,
            gru_comp_cmd=False,
        )

    def test_build_med_text_empty(self):
        """Empty steps dict produces valid structure."""
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={},
            submethods=[],
            devices=[],
            enabled_components=self._default_components(),
            author="testuser",
        )
        self.assertIn("HxCfgFile,3;", text)
        self.assertIn("ConfigIsValid,Y;", text)
        self.assertIn("ActivityData", text)

    def test_build_med_text_structure(self):
        """Output contains required structural headers."""
        step = _make_step("Comment", "// hello")
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={step.instance_guid: step},
            submethods=[],
            devices=[],
            enabled_components=self._default_components(),
            author="testuser",
        )
        self.assertIn("HxCfgFile,3;", text)
        self.assertIn("ActivityData", text)
        self.assertIn("DataDef,HxPars,3,test_guid_0001,", text)
        self.assertIn("HxMetEdData", text)
        self.assertIn("HxMetEd_MainDefinition", text)
        self.assertIn("HxMetEd_Outlining", text)
        self.assertIn("HxMetEd_Submethods", text)

    def test_build_med_text_with_devices(self):
        """Devices are included in HxMetEdData."""
        device = DeviceInfo(name="ML_STAR", layout_file="MyLayout.lay")
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={},
            submethods=[],
            devices=[device],
            enabled_components=self._default_components(),
            author="testuser",
        )
        self.assertIn("HxMetEdData", text)

    def test_build_med_text_with_submethods(self):
        """Submethods section is populated."""
        sub = SubmethodInfo(name="OnAbort", params=[], builtin=True)
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={},
            submethods=[sub],
            devices=[],
            enabled_components=self._default_components(),
            author="testuser",
        )
        self.assertIn("HxMetEd_Submethods", text)

    def test_build_med_text_contains_checksum(self):
        """Output ends with a checksum line."""
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={},
            submethods=[],
            devices=[],
            enabled_components=self._default_components(),
            author="testuser",
        )
        # Checksum lines start with * in .med files
        lines = text.strip().split("\n")
        last_line = lines[-1].strip()
        self.assertTrue(last_line.startswith("*"), f"Expected checksum line, got: {last_line!r}")

    def test_build_med_text_multiple_steps(self):
        """Multiple steps are each emitted as separate sections."""
        step1 = _make_step("Comment", "// step 1", guid="guid_a")
        step2 = _make_step("Assignment", "x = 1;", guid="guid_b")
        text = build_med_text(
            activity_data_base64="AAAA",
            steps={step1.instance_guid: step1, step2.instance_guid: step2},
            submethods=[],
            devices=[],
            enabled_components=self._default_components(),
            author="testuser",
        )
        self.assertIn("DataDef,HxPars,3,guid_a,", text)
        self.assertIn("DataDef,HxPars,3,guid_b,", text)


# ═════════════════════════════════════════════════════════════════════════════
# extract_activity_data_from_med  (file-based -- needs mocking or skipping)
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestExtractActivityDataRoundtrip(unittest.TestCase):
    """Round-trip test: build → write → extract preserves step count.

    These tests touch the filesystem and require the codec to be functional.
    They are skipped only when the required converter executable is absent.
    """

    def test_extract_activity_data_roundtrip(self):
        """Build .med text → binary → extract ActivityData works."""
        import tempfile

        components = ComponentFlags(
            sched_comp_cmd=False,
            custom_dialog_comp_cmd=False,
            multi_pip_comp_cmd=False,
            gru_comp_cmd=False,
        )
        step = _make_step("Comment", "// round-trip", guid="guid_rt_001")
        med_text = build_med_text(
            activity_data_base64="AAAA",
            steps={step.instance_guid: step},
            submethods=[],
            devices=[],
            enabled_components=components,
            author="testuser",
        )
        # Verify the text contains our marker
        self.assertIn("AAAA", med_text)
        self.assertIn("guid_rt_001", med_text)

        # Conversion and assertion failures must remain visible as test failures.
        try:
            from standalone_med_tools.hxcfgfile_codec import text_to_binary_file

            with tempfile.TemporaryDirectory() as tmpdir:
                text_path = os.path.join(tmpdir, "test.med.txt")
                bin_path = os.path.join(tmpdir, "test.med")

                with open(text_path, "w", encoding="latin1") as f:
                    f.write(med_text)

                from pathlib import Path as P
                text_to_binary_file(P(text_path), P(bin_path))

                activity = extract_activity_data_from_med(bin_path)
                self.assertEqual(activity, "AAAA")
        except FileNotFoundError as exc:
            if str(exc).startswith("Converter not found at "):
                self.skipTest(f"Codec round-trip not available: {exc}")
            raise


# ═════════════════════════════════════════════════════════════════════════════
# sync_med_from_hsl  (file-based)
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestSyncMedFromHsl(unittest.TestCase):
    """Tests for ``sync_med_from_hsl`` with minimal .hsl content."""

    def test_sync_med_from_hsl_empty(self):
        """HSL with no block markers produces a .med file (minimal/empty steps)."""
        import tempfile

        minimal_hsl = (
            '// {49C1B901-1244-4152-8F24-1B8CF0731B29}\r\n'
            'method main()\r\n'
            '{\r\n'
            '}\r\n'
        )

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                hsl_path = os.path.join(tmpdir, "Test.hsl")
                med_path = os.path.join(tmpdir, "Test.med")

                with open(hsl_path, "w", encoding="utf-8") as f:
                    f.write(minimal_hsl)

                sync_med_from_hsl(
                    hsl_path=hsl_path,
                    med_path=med_path,
                    activity_data_base64="AAAA",
                )

                self.assertTrue(
                    os.path.exists(med_path),
                    "sync_med_from_hsl should create a .med file",
                )
                # The generated file should have non-trivial size
                self.assertGreater(os.path.getsize(med_path), 0)
        except FileNotFoundError as exc:
            if str(exc).startswith("Converter not found at "):
                self.skipTest(f"sync_med_from_hsl not runnable in test env: {exc}")
            raise


# ═════════════════════════════════════════════════════════════════════════════
# Additional edge-case tests
# ═════════════════════════════════════════════════════════════════════════════

@_skip_if_not_importable
class TestEdgeCases(unittest.TestCase):
    """Miscellaneous edge-case tests."""

    def test_function_call_info_is_namedtuple(self):
        info = parse_function_call_code("F(a);")
        self.assertIsInstance(info, FunctionCallInfo)

    def test_array_method_call_info_is_namedtuple(self):
        info = parse_array_method_call("a.SetSize(0);")
        self.assertIsInstance(info, ArrayMethodCallInfo)

    def test_build_step_section_returns_string(self):
        step = _make_step("Comment", "// test")
        self.assertIsInstance(build_step_section(step), str)

    def test_split_args_escaped_quote(self):
        """An escaped quote inside a string should not toggle in_string."""
        result = split_function_args(r'"a\"b", c')
        # The escaped quote should keep everything in one token
        self.assertEqual(len(result), 2)

    def test_parse_function_call_deeply_nested_args(self):
        info = parse_function_call_code("F(g(h(1,2),3), x);")
        self.assertEqual(info.function_name, "F")
        self.assertEqual(len(info.args), 2)
        self.assertEqual(info.args[0], "g(h(1,2),3)")
        self.assertEqual(info.args[1], "x")


if __name__ == "__main__":
    unittest.main()
