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


def minimal_stp_text(section=''):
    """Synthetic form from the supplied KB Labware_Property_Query.stp example.

    The example proves ReadOnly,"0" and AuditTrailData,")" only, not a universal
    Properties grammar or a native device parameter layout. No real file is copied.
    """
    properties = 'DataDef,Method,1,Properties,\r\n{\r\nReadOnly, "0"\r\n};'
    audit = 'DataDef,HxPars,3,AuditTrailData,\r\n[\r\n")"\r\n];'
    return container_text(properties + '\r\n' + (section + '\r\n' if section else '') + audit)


def configured_device_hsl():
    # Existing registered CLSID identifies a selected GUID; the body is a comment
    # and is never run. Configured tokens below exercise serialization only.
    return ('// {{ 1 1 0 "configured" "ML_STAR:{54114402-7FA2-11D3-AD85-0004ACB1DCB2}"\n'
            '// synthetic configured token fixture; no hardware operation\n// }} ""\n')


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


class StpReviewRegressionTests(unittest.TestCase):
    """Real temporary-file cleanup and synthetic sync error propagation."""

    def test_parser_rejects_junk_instead_of_searching_for_next_section(self):
        first = 'DataDef,HxPars,3,first,["one"];'
        second = 'DataDef,HxPars,3,second,["two"];'
        for junk in ('"x"];', 'garbage', ';', 'HxCfgFile,3;', 'ConfigIsValid,Y;'):
            for text in (junk + first, first + junk + second, first + junk):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    generator._parse_existing_stp_sections(text)
        valid = container_text(first + '\r\n\t' + second)
        self.assertEqual({'first': first, 'second': second}, generator._parse_existing_stp_sections(valid))
        with self.assertRaises(ValueError):
            generator._parse_existing_stp_sections(valid + second)

    def test_partial_conversion_file_is_really_removed_and_stp_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            hsl, stp = Path(folder, 'fixture.hsl'), Path(folder, 'fixture.stp')
            hsl.write_text('// synthetic HSL', encoding='utf-8')
            original = b'synthetic original STP sentinel'
            stp.write_bytes(original)
            outputs = []
            def partial(source, target):
                outputs.append(target)
                target.write_text('partial converter output', encoding='latin1')
                raise OSError('synthetic partial-conversion failure')
            with patch.object(generator, 'binary_to_text_file', side_effect=partial), \
                 patch.object(generator, 'transactional_binary_write') as writer:
                with self.assertRaisesRegex(OSError, 'partial-conversion'):
                    generator.sync_stp_from_hsl(str(hsl), str(stp))
                writer.assert_not_called()
            self.assertEqual(1, len(outputs))
            self.assertFalse(outputs[0].exists())
            self.assertEqual(original, stp.read_bytes())

    def test_temp_read_failure_is_distinct_and_really_cleans_output(self):
        with tempfile.TemporaryDirectory() as folder:
            hsl, stp = Path(folder, 'fixture.hsl'), Path(folder, 'fixture.stp')
            hsl.write_text('// synthetic HSL', encoding='utf-8')
            original = b'synthetic original STP sentinel'
            stp.write_bytes(original)
            outputs, read_text = [], Path.read_text
            def converted(source, target):
                outputs.append(target)
                target.write_text(minimal_stp_text(), encoding='latin1')
            def cannot_read(path, *args, **kwargs):
                if path in outputs:
                    raise OSError('synthetic converted-text read failure')
                return read_text(path, *args, **kwargs)
            with patch.object(generator, 'binary_to_text_file', side_effect=converted) as converter, \
                 patch.object(Path, 'read_text', new=cannot_read), \
                 patch.object(generator, 'transactional_binary_write') as writer:
                with self.assertRaisesRegex(OSError, 'converted-text read failure'):
                    generator.sync_stp_from_hsl(str(hsl), str(stp))
                converter.assert_called_once()
                writer.assert_not_called()
            self.assertFalse(outputs[0].exists())
            self.assertEqual(original, stp.read_bytes())

    def test_sync_selected_guid_retains_configured_section_before_write(self):
        section = r'DataDef,HxPars,3,configured,["1SyntheticLabel", "a[0]", "quoted \" ]; value"];'
        text = minimal_stp_text(section)
        with tempfile.TemporaryDirectory() as folder:
            hsl, stp = Path(folder, 'fixture.hsl'), Path(folder, 'fixture.stp')
            hsl.write_text(configured_device_hsl(), encoding='utf-8')
            stp.write_bytes(b'synthetic original STP sentinel')
            def converted(source, target):
                target.write_text(text, encoding='latin1', newline='')
            with patch.object(generator, 'binary_to_text_file', side_effect=converted), \
                 patch.object(generator, 'build_default_stp_section', side_effect=AssertionError('existing GUID defaulted')), \
                 patch.object(generator, 'transactional_binary_write') as writer:
                generator.sync_stp_from_hsl(str(hsl), str(stp))
            writer.assert_called_once()
            self.assertEqual(str(stp), writer.call_args.args[0])
            self.assertEqual(section, generator._parse_existing_stp_sections(writer.call_args.args[1])['configured'])
            self.assertEqual([], list(Path(folder).glob('*.sync_*.txt')))

    def test_on_save_surfaces_stp_failure_and_preserves_existing_stp(self):
        with tempfile.TemporaryDirectory() as folder:
            hsl, med, stp = (Path(folder, 'fixture' + ext) for ext in ('.hsl', '.med', '.stp'))
            hsl.write_text(configured_device_hsl(), encoding='utf-8')
            med.write_bytes(b'synthetic MED sentinel')
            original = b'synthetic original STP sentinel'
            stp.write_bytes(original)
            def malformed(source, target):
                target.write_text('DataDef,HxPars,3,configured,["unterminated];', encoding='latin1')
            with patch.object(generator, 'reconcile_block_marker_headers', side_effect=lambda value: value), \
                 patch.object(generator, 'renumber_block_markers', side_effect=lambda value: value), \
                 patch.object(generator, 'sync_med_from_hsl') as med_sync, \
                 patch.object(generator, 'binary_to_text_file', side_effect=malformed), \
                 patch.object(generator, 'transactional_binary_write') as writer, \
                 patch.object(generator, 'update_checksum_in_file') as checksums, \
                 self.assertLogs(generator.logger, level='ERROR'):
                with self.assertRaisesRegex(OSError, r'\.stp sync failed') as caught:
                    generator.correct_block_markers_on_save(str(hsl))
                self.assertIsInstance(caught.exception.__cause__, ValueError)
                med_sync.assert_called_once_with(str(hsl), str(med))
                writer.assert_not_called()
                checksums.assert_not_called()
            self.assertEqual(original, stp.read_bytes())
            self.assertEqual([], list(Path(folder).glob('*.sync_*.txt')))


class VendorStpSyncTests(unittest.TestCase):
    """Native conversion of synthetic STP containers only; no method execution."""

    def _roundtrip_sync(self, selected):
        try:
            codec.resolve_converter_path()
        except FileNotFoundError as exc:
            if str(exc).startswith('Converter not found at '):
                self.skipTest(str(exc))
            raise
        section = r'DataDef,HxPars,3,configured,["1SyntheticLabel", "a[0]", "quoted \" ]; value"];' if selected else ''
        expected_tokens = {'AuditTrailData': [')']}
        if selected:
            expected_tokens['configured'] = ['1SyntheticLabel', 'a[0]', 'quoted " ]; value']
        with tempfile.TemporaryDirectory() as folder:
            hsl, stp, source, before, after = (Path(folder, name) for name in
                                             ('fixture.hsl', 'fixture.stp', 'source.txt', 'before.txt', 'after.txt'))
            hsl.write_text(configured_device_hsl() if selected else '// synthetic empty method', encoding='utf-8')
            source.write_text(minimal_stp_text(section), encoding='latin1', newline='')
            codec.text_to_binary_file(source, stp)
            codec.binary_to_text_file(stp, before)
            existing = generator._parse_existing_stp_sections(before.read_text(encoding='latin1'))
            expected_keys = {'__Properties__'} | set(expected_tokens)
            self.assertEqual(expected_keys, set(existing))
            writer = generator.transactional_binary_write
            def checked_write(path, text):
                generated = generator._parse_existing_stp_sections(text)
                self.assertEqual(expected_keys, set(generated))
                for key in ('__Properties__', 'configured') if selected else ('__Properties__',):
                    self.assertEqual(existing[key], generated[key])
                # AuditTrailData retains the established regenerated-empty policy.
                self.assertEqual(expected_tokens, {s.key: s.tokens for s in codec.parse_text_med(text).hxpars_sections})
                return writer(path, text)
            with patch.object(generator, 'build_default_stp_section', side_effect=AssertionError('unexpected defaults')), \
                 patch.object(generator, 'transactional_binary_write', side_effect=checked_write) as checked:
                generator.sync_stp_from_hsl(str(hsl), str(stp))
                checked.assert_called_once()
            payload = stp.read_bytes()
            self.assertEqual(b'\x03\x00\x01\x00', payload[:4])
            codec.binary_to_text_file(stp, after)
            models = [codec.parse_binary_med(payload), codec.parse_text_med(after.read_text(encoding='latin1'))]
            for model in models:
                self.assertEqual(('Method,Properties', 'ReadOnly', '0'),
                                 (model.named_section.name, model.named_section.key, model.named_section.value))
                self.assertEqual(expected_tokens, {s.key: s.tokens for s in model.hxpars_sections})
                self.assertEqual(len(expected_tokens), len(model.hxpars_sections))
            self.assertEqual([], list(Path(folder).glob('*.sync_*.txt')))

    def test_minimal_properties_and_audit_only_sync_through_vendor(self):
        self._roundtrip_sync(False)

    def test_selected_configured_guid_sync_preserves_tokens_through_vendor(self):
        self._roundtrip_sync(True)


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
