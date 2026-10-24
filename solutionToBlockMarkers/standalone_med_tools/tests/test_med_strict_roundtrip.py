"""Strict text grammar and vendor conversion checks, without running a method.

Synthetic expressions and token names exercise quoting only. Successful container
conversion does not establish that VENUS can evaluate those expressions or that
default device parameters are appropriate for hardware.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from standalone_med_tools import hxcfgfile_codec as codec
from standalone_med_tools import med_generator as generator
from standalone_med_tools.checksum import generate_checksum_line


def strict_sections(text):
    """Independent test oracle: exactly one comma between quoted body tokens."""
    sections = []
    token_pattern = re.compile(r'"(?:[^"\\\r\n]|\\["\\rnt])*"')
    for header in re.finditer(r'DataDef,HxPars,3,([^,\r\n]+),\s*\[', text):
        cursor, tokens = header.end(), []
        while True:
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            match = token_pattern.match(text, cursor)
            if not match:
                raise ValueError('Expected one quoted token in ' + header.group(1))
            tokens.append(json.loads(match.group(0)))
            cursor = match.end()
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if text.startswith('];', cursor):
                break
            if cursor >= len(text) or text[cursor] != ',':
                raise ValueError('Missing comma in ' + header.group(1))
            cursor += 1
        sections.append((header.group(1), tokens))
    if not sections:
        raise ValueError('No HxPars sections found')
    return sections


def step(kind, code, identity='fixture_step'):
    return generator.HslStepRecord(identity, generator.STEP_CLSID[kind],
                                   [generator.HslStepBlock(1, code, 1)])


def builder_cases():
    flags = generator.ComponentFlags(False, False, False, False)
    load = generator.HslStepRecord('fixture_load', 'ML_STAR:{54114402-7FA2-11D3-AD85-0004ACB1DCB2}', [])
    normal = generator.HslStepRecord('fixture_device', 'ML_STAR:{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}', [])
    return [
        ('step', generator.build_step_section(step('SingleLibFunction', 'Library::Call(Inner("quoted"));'))),
        ('device', generator.build_device_step_stub(normal)),
        ('load-carrier', generator.build_device_step_stub(load)),
        ('devices', generator._build_hx_met_ed_data([generator.DeviceInfo('Device"name\\tail', 'unused.lay')], flags)),
        ('main', generator._build_main_definition()),
        ('submethods', generator._build_submethods_section([
            generator.SubmethodInfo('Sub"name\\tail', [{'name': 'param"name\\tail', 'type': 1, 'direction': 0}], False)])),
        ('default-stp', generator.build_default_stp_section('fixture_stp', load.clsid, 'ML_STAR.LoadCarrier(ML_STAR.Sequence);')),
    ]


def container_text(section):
    content = 'HxCfgFile,3;\r\n\r\nConfigIsValid,Y;\r\n\r\n' + section + '\r\n'
    return content + generate_checksum_line(content, author='offline-fixture', valid=0, prefix_char='*') + '\r\n'


class StrictGrammarTests(unittest.TestCase):
    def test_independent_oracle_rejects_missing_and_trailing_separators(self):
        valid = 'DataDef,HxPars,3,test,\n[\n"first",\n"second"\n];'
        self.assertEqual([('test', ['first', 'second'])], strict_sections(valid))
        for invalid in (valid.replace('"first",', '"first"'), valid.replace('"second"', '"second",'),
                        valid.replace('"first",', '"first",,')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                strict_sections(invalid)

    def test_formatter_rejects_malformed_input_instead_of_joining_it(self):
        good = ['DataDef,HxPars,3,test,', '[', '"a"', '];']
        self.assertEqual([('test', ['a'])], strict_sections(generator._format_hxpars_section(good)))
        invalid = [None, [], good[:-1], ['bad header', '[', '"a"', '];'],
                   [good[0], '[', '"a",', '];'], [good[0], '[', '"a" "b"', '];'],
                   [good[0], '[', 'unquoted', '];'], [good[0], '[', 3, '];'],
                   [good[0], '[', '"raw\nnewline"', '];']]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                generator._format_hxpars_section(value)

    def test_every_builder_emits_strict_separator_grammar(self):
        for name, section in builder_cases():
            with self.subTest(builder=name):
                self.assertEqual(1, len(strict_sections(section)))

    def test_quoted_nested_expressions_preserve_existing_variable_containers(self):
        cases = [
            ('ArrayDeclare', 'arr.SetSize(Length("quoted"));', ['1NewSize', 'Length("quoted")']),
            ('SingleLibFunction', 'Library::Call(Inner("quoted"));', ['(Inner("quoted")']),
            ('ArraySetAt', 'arr.SetAt(Index("quoted"), Value("quoted"));', ['1Index', 'Index("quoted")', '(Value("quoted")']),
        ]
        for kind, code, expected in cases:
            with self.subTest(kind=kind):
                tokens = strict_sections(generator.build_step_section(step(kind, code)))[0][1]
                for token in expected:
                    self.assertIn(token, tokens)

    def test_quoted_string_and_numeric_array_values_keep_existing_container_policy(self):
        for value in ('"literal"', '3.5'):
            tokens = strict_sections(generator.build_step_section(step('ArraySetAt', 'arr.SetAt(0, ' + value + ');')))[0][1]
            self.assertNotIn('(' + value, tokens)
            self.assertIn(value, tokens)

    def test_escape_string_handles_quotes_backslashes_and_all_newline_forms(self):
        original = 'quote" slash\\ CR\r LF\n CRLF\r\n end'
        section = generator._format_hxpars_section(['DataDef,HxPars,3,test,', '[', '"' + generator._escape_string(original) + '"', '];'])
        self.assertEqual(original, strict_sections(section)[0][1][0])

    def test_complete_med_including_outlining_has_strict_grammar(self):
        text = generator.build_med_text('AAAA', {'fixture_step': step('Comment', '// "quoted"')},
                                       [generator.SubmethodInfo('Sub', [{'name': 'p', 'type': 1, 'direction': 0}], False)],
                                       [generator.DeviceInfo('ML_STAR', 'unused.lay')], generator.ComponentFlags(False, False, False, False))
        self.assertEqual(5, len(strict_sections(text)))


class StpPreservationTests(unittest.TestCase):
    """Synthetic string fixtures and mocked sync only; no vendor/native calls."""

    def test_quoted_brackets_terminators_and_escaped_quotes_are_preserved(self):
        section = r'''DataDef,HxPars,3,g,
[
"1SequenceName",
"a[0]",
"literal ]; and };",
"escaped \" ]; still quoted",
"backslash \\ end",
"DataDef,HxPars,3,not_a_section,["
];'''
        self.assertEqual({'g': section}, generator._parse_existing_stp_sections(section))
        self.assertEqual(['1SequenceName', 'a[0]', 'literal ]; and };',
                          'escaped " ]; still quoted', 'backslash \\ end',
                          'DataDef,HxPars,3,not_a_section,['],
                         codec.parse_text_med(container_text(section)).hxpars_sections[0].tokens)

    def test_multiple_sections_and_properties_retain_exact_original_text(self):
        properties = r'DataDef,Method,1,Properties,{ReadOnly, "quoted \" }; value"};'
        first = 'DataDef,HxPars,3,g1,\r\n["a[0]", "b];c"];'
        second = 'DataDef,HxPars,3,g2,\n[ "other" ];'
        audit = 'DataDef,HxPars,3,AuditTrailData,["original audit"];'
        expected = {'__Properties__': properties, 'g1': first, 'g2': second, 'AuditTrailData': audit}
        self.assertEqual(expected, generator._parse_existing_stp_sections(
            'HxCfgFile,3;\nConfigIsValid,Y;\n' + '\n\n'.join(expected.values())))

    def test_malformed_or_duplicate_sections_raise_instead_of_disappearing(self):
        good = 'DataDef,HxPars,3,g,["value"];'
        malformed = [
            '', 'HxCfgFile,3;', 'DataDef,HxPars,3,g,{"value"};',
            'DataDef,HxPars,3,g,["unterminated];', 'DataDef,HxPars,3,g,["value"',
            'DataDef,HxPars,3,g,["a" "b"];', 'DataDef,HxPars,3,g,["a",];',
            'DataDef,HxPars,3,g,["a",,"b"];', good + '\n' + good,
            'DataDef,Method,1,Properties,{ReadOnly,};',
            'DataDef,Method,1,Properties,{ReadOnly,"unterminated};',
            'DataDef,Method,1,Properties,{ReadOnly,"0"',
            'DataDef,Unknown,9,section,["value"];',
        ]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                generator._parse_existing_stp_sections(value)

    def test_build_preserves_selected_section_bytes_without_generating_defaults(self):
        section = r'DataDef,HxPars,3,g,["a[0]", "quoted \" ]; value", "last"];'
        existing = generator._parse_existing_stp_sections(section)
        with patch.object(generator, 'build_default_stp_section', side_effect=AssertionError('existing GUID defaulted')):
            built = generator.build_stp_text({'g': {'clsid': 'unused', 'code': 'unused'}}, existing, author='synthetic')
        self.assertEqual(section, generator._parse_existing_stp_sections(built)['g'])
        self.assertEqual(strict_sections(section), [row for row in strict_sections(built) if row[0] == 'g'])

    def test_only_absent_selected_guid_receives_default_and_existing_policy_is_unchanged(self):
        selected = 'DataDef,HxPars,3,g,["configured[0]"];'
        orphan = 'DataDef,HxPars,3,orphan,["old"];'
        audit = 'DataDef,HxPars,3,AuditTrailData,["old audit"];'
        existing = generator._parse_existing_stp_sections('\n'.join([selected, orphan, audit]))
        added = 'DataDef,HxPars,3,new,["synthetic default"];'
        with patch.object(generator, 'build_default_stp_section', return_value=added) as default:
            built = generator.build_stp_text({'g': {'clsid': 'unused', 'code': 'unused'},
                                             'new': {'clsid': 'fixture', 'code': 'fixture'}}, existing, author='synthetic')
        default.assert_called_once_with('new', 'fixture', 'fixture')
        actual = generator._parse_existing_stp_sections(built)
        self.assertEqual({'__Properties__', 'g', 'new', 'AuditTrailData'}, set(actual))
        self.assertEqual(selected, actual['g'])
        self.assertEqual(added, actual['new'])
        self.assertNotEqual(audit, actual['AuditTrailData'])

    def test_build_rejects_invalid_existing_mapping_before_default_generation(self):
        for existing in ({'g': ''}, {'g': 'DataDef,HxPars,3,other,["x"];'},
                         {'g': 'DataDef,HxPars,3,g,["broken];'}):
            with self.subTest(existing=existing), patch.object(generator, 'build_default_stp_section') as default:
                with self.assertRaises(ValueError):
                    generator.build_stp_text({'g': {'clsid': 'unused', 'code': 'unused'}}, existing)
                default.assert_not_called()

    def test_sync_parse_failure_propagates_cleans_temp_and_never_writes(self):
        with patch.object(Path, 'read_text', side_effect=['// synthetic HSL', 'DataDef,HxPars,3,g,["unterminated];']), \
             patch.object(generator.os.path, 'exists', side_effect=lambda path: str(path).endswith('.stp')), \
             patch.object(generator, 'binary_to_text_file') as converter, \
             patch.object(generator, '_unique_temp_suffix', return_value='fixture'), \
             patch.object(generator, '_safe_unlink') as cleanup, \
             patch.object(generator, 'transactional_binary_write') as write:
            with self.assertRaises(ValueError):
                generator.sync_stp_from_hsl('fixture.hsl', 'fixture.stp')
            converter.assert_called_once_with(Path('fixture.stp'), Path('fixture.stp.sync_fixture.txt'))
            cleanup.assert_called_once_with('fixture.stp.sync_fixture.txt')
            write.assert_not_called()

    def test_sync_read_failure_propagates_cleans_temp_and_never_writes(self):
        with patch.object(Path, 'read_text', return_value='// synthetic HSL'), \
             patch.object(generator.os.path, 'exists', side_effect=lambda path: str(path).endswith('.stp')), \
             patch.object(generator, 'binary_to_text_file', side_effect=OSError('synthetic conversion unavailable')), \
             patch.object(generator, '_unique_temp_suffix', return_value='fixture'), \
             patch.object(generator, '_safe_unlink') as cleanup, \
             patch.object(generator, 'transactional_binary_write') as write:
            with self.assertRaisesRegex(OSError, 'synthetic conversion unavailable'):
                generator.sync_stp_from_hsl('fixture.hsl', 'fixture.stp')
            cleanup.assert_called_once_with('fixture.stp.sync_fixture.txt')
            write.assert_not_called()


class ConverterResolutionTests(unittest.TestCase):
    def test_default_uses_known_tools_relative_location(self):
        expected = Path(codec.__file__).resolve().parents[3] / codec.CONVERTER_RELATIVE_PATH
        with patch.dict(os.environ):
            os.environ.pop('HAMILTON_BINARY_ASCII_CONVERTER', None)
            with patch.object(Path, 'is_file', return_value=True):
                self.assertEqual(expected, codec.resolve_converter_path())

    def test_explicit_override_and_missing_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'explicit-converter.exe'
            executable.write_bytes(b'fixture, never executed')
            with patch.dict(os.environ, {'HAMILTON_BINARY_ASCII_CONVERTER': str(executable)}):
                self.assertEqual(executable, codec.resolve_converter_path())
            missing = Path(folder) / 'absent.exe'
            with patch.dict(os.environ, {'HAMILTON_BINARY_ASCII_CONVERTER': str(missing)}):
                for wrapper in (codec.binary_to_text_file, codec.text_to_binary_file):
                    with self.subTest(wrapper=wrapper.__name__), self.assertRaisesRegex(FileNotFoundError, '^Converter not found at '):
                        wrapper(Path('input'), Path('output'))

    def test_both_wrappers_share_resolver_and_preserve_nonzero_exit(self):
        executable = Path('selected-converter.exe')
        with patch.object(codec, 'resolve_converter_path', return_value=executable) as resolver:
            with patch('subprocess.run') as run:
                codec.binary_to_text_file(Path('source.med'), Path('target.txt'))
                codec.text_to_binary_file(Path('source.txt'), Path('target.med'))
                self.assertEqual(2, resolver.call_count)
                self.assertEqual(['--ascii', '--binary'], [call.args[0][1] for call in run.call_args_list])
                self.assertTrue(all(call.kwargs == {'check': True} for call in run.call_args_list))
            with patch('subprocess.run', side_effect=subprocess.CalledProcessError(2, [str(executable)])):
                for wrapper in (codec.binary_to_text_file, codec.text_to_binary_file):
                    with self.subTest(wrapper=wrapper.__name__), self.assertRaises(subprocess.CalledProcessError):
                        wrapper(Path('input'), Path('output'))


class VendorRoundtripTests(unittest.TestCase):
    def test_each_builder_preserves_all_sections_and_tokens_through_vendor(self):
        try:
            codec.resolve_converter_path()
        except FileNotFoundError as exc:
            if str(exc).startswith('Converter not found at '):
                self.skipTest(str(exc))
            raise
        cases = builder_cases()
        for kind, code in [('ArrayDeclare', 'arr.SetSize(Length("quoted"));'),
                           ('ArraySetAt', 'arr.SetAt(Index("quoted"), Value("quoted"));')]:
            cases.append((kind, generator.build_step_section(step(kind, code))))
        for name, section in cases:
            with self.subTest(builder=name), tempfile.TemporaryDirectory() as folder:
                source, binary, converted = (Path(folder) / name for name in ('source.txt', 'fixture.med', 'converted.txt'))
                original = container_text(section)
                source.write_text(original, encoding='latin1', newline='')
                codec.text_to_binary_file(source, binary)
                payload = binary.read_bytes()
                self.assertEqual(b'\x03\x00\x01\x00', payload[:4])
                codec.binary_to_text_file(binary, converted)
                expected = strict_sections(original)
                self.assertEqual(expected, strict_sections(converted.read_text(encoding='latin1')))
                parsed_binary = codec.parse_binary_med(payload)
                self.assertEqual(expected, [(part.key, part.tokens) for part in parsed_binary.hxpars_sections])


if __name__ == '__main__':
    unittest.main()
