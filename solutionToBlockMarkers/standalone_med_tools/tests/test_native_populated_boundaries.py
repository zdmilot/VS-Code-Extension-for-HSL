"""Populated synthetic MED/STP serialization; never execute the stored HSL."""
from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from standalone_med_tools import hxcfgfile_codec as codec, med_generator as med, stp_generator as stp
from test_med_strict_roundtrip import container_text, strict_sections, step


PAYLOADS = {'raw-tab': 'left\tright', 'raw-c0-01': 'left\x01right',
            'raw-latin1': 'café µ', 'combined': 'café\tµ\x01'}
FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5)


def values_after(tokens, key):
    return [tokens[i + 1] for i, value in enumerate(tokens[:-1]) if value == key]


def hsl_fixture(kind, code, identity='raw_step'):
    return ('method main()\n{\n// {{ 1 1 0 "' + identity + '" "' + med.STEP_CLSID[kind] + '"\n'
            + code + '\n// }} ""\n}\n')


def raw_cases():
    for variant, payload in PAYLOADS.items():
        yield variant, 'SingleLibFunction', '\tresult = Library::Call("' + payload + '", variable);'
        yield variant, 'Comment', '// synthetic ' + payload


class PopulatedNativeBoundaries(unittest.TestCase):
    def setUp(self):
        converter = codec.resolve_converter_path()
        self.qualification_observations = {
            'converter_path': str(converter),
            'converter_sha256': hashlib.sha256(converter.read_bytes()).hexdigest(),
            'scope': 'decoded token serialization and fail-closed preservation; no HSL expression or hardware execution',
            'variants': [], 'roundtrips_completed': 0,
        }

    def observe(self, name, **values):
        record = {'variant': name, **values}
        self.qualification_observations['variants'].append(record)
        return record

    def compare_native(self, binary, decoded, expected):
        codec.binary_to_text_file(binary, decoded)
        actual_text = strict_sections(decoded.read_text(encoding='latin1'))
        actual_binary = [(s.key, s.tokens) for s in codec.parse_binary_med(binary.read_bytes()).hxpars_sections]
        actual_codec_text = [(s.key, s.tokens) for s in codec.parse_text_med(decoded.read_text(encoding='latin1')).hxpars_sections]
        # Match section names without treating native section reordering as corruption.
        self.assertEqual(len(expected), len(actual_text))
        self.assertEqual(sorted(expected), sorted(actual_text))
        self.assertEqual(sorted(expected), sorted(actual_binary))
        self.assertEqual(sorted(expected), sorted(actual_codec_text))
        self.qualification_observations['roundtrips_completed'] += 1
        return actual_binary

    def native_section(self, section, expected):
        with tempfile.TemporaryDirectory() as folder:
            source, binary, decoded = (Path(folder, p) for p in ('source.txt', 'synthetic.med', 'decoded.txt'))
            source.write_text(container_text(section), encoding='latin1', newline='')
            codec.text_to_binary_file(source, binary)
            return self.compare_native(binary, decoded, expected)

    def test_raw_characters_in_populated_step_builders(self):
        for variant, kind, code in raw_cases():
            with self.subTest(variant=variant, kind=kind):
                section = med.build_step_section(step(kind, code))
                expected = strict_sections(section)
                self.assertEqual([code], values_after(expected[0][1], '1-533921779'))
                if kind == 'SingleLibFunction':
                    self.assertEqual(['result'], values_after(expected[0][1], '1ReturnValue'))
                    self.assertEqual(['"' + PAYLOADS[variant] + '"'], values_after(expected[0][1], '1Value.0'))
                observation = self.observe(variant + ':' + kind, input_code_latin1_hex=code.encode('latin1').hex())
                actual = self.native_section(section, expected)
                decoded = values_after(actual[0][1], '1-533921779')[0]
                observation['decoded_code_latin1_hex'] = decoded.encode('latin1').hex()
                self.assertEqual(code.encode('latin1'), decoded.encode('latin1'))

    def test_raw_characters_through_full_sync_med_from_hsl(self):
        for variant, kind, code in raw_cases():
            with self.subTest(variant=variant, kind=kind), tempfile.TemporaryDirectory() as folder:
                hsl, binary, stp_path, decoded = (Path(folder, p) for p in ('fixture.hsl', 'fixture.med', 'fixture.stp', 'decoded.txt'))
                hsl.write_text(hsl_fixture(kind, code), encoding='utf-8', newline='')
                original_hsl = hsl.read_bytes()
                stp_path.write_bytes(b'synthetic unchanged STP')
                with patch.object(med, '_format_timestamp', return_value='2026-01-02 03:04'):
                    section = med.build_step_section(step(kind, code, 'raw_step'))
                    expected_step = strict_sections(section)[0]
                    med.sync_med_from_hsl(str(hsl), str(binary), activity_data_base64='AAAA')
                codec.binary_to_text_file(binary, decoded)
                models = (codec.parse_binary_med(binary.read_bytes()), codec.parse_text_med(decoded.read_text(encoding='latin1')))
                for model in models:
                    selected = [(s.key, s.tokens) for s in model.hxpars_sections if s.key == 'raw_step']
                    self.assertEqual([expected_step], selected)
                    self.assertEqual([code], values_after(selected[0][1], '1-533921779'))
                text_sections = strict_sections(decoded.read_text(encoding='latin1'))
                self.assertEqual([expected_step], [s for s in text_sections if s[0] == 'raw_step'])
                self.assertEqual(original_hsl, hsl.read_bytes())
                self.assertEqual(b'synthetic unchanged STP', stp_path.read_bytes())
                self.assertEqual([], list(Path(folder).glob('~hxsync_*')))
                self.assertEqual([], list(Path(folder).glob('*.bak')))
                self.observe(variant + ':' + kind, input_code_latin1_hex=code.encode('latin1').hex(),
                             decoded_code_latin1_hex=values_after(selected[0][1], '1-533921779')[0].encode('latin1').hex())
                self.qualification_observations['roundtrips_completed'] += 1

    def test_direct_writer_non_latin1_rejects_and_removes_partial_temp(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as folder:
                target = Path(folder, 'fixture.med')
                if existing:
                    target.write_bytes(b'synthetic original MED')
                original = {p.name: p.read_bytes() for p in Path(folder).iterdir()}
                with patch.object(med, 'text_to_binary_file') as converter:
                    with self.assertRaises(UnicodeEncodeError):
                        med.transactional_binary_write(str(target), 'HxCfgFile,3;\n"漢"')
                    converter.assert_not_called()
                self.assertEqual(original, {p.name: p.read_bytes() for p in Path(folder).iterdir()})
                self.observe('direct-writer', existing_target=existing, originals_preserved=True, temporary_files_remaining=0)

    def test_sync_non_latin1_rejects_preserves_all_original_files(self):
        with tempfile.TemporaryDirectory() as folder:
            hsl, binary, stp_path = (Path(folder, 'fixture' + ext) for ext in ('.hsl', '.med', '.stp'))
            hsl.write_text(hsl_fixture('Comment', '// unsupported 漢'), encoding='utf-8')
            binary.write_bytes(b'synthetic original MED'); stp_path.write_bytes(b'synthetic original STP')
            original = {p.name: p.read_bytes() for p in Path(folder).iterdir()}
            with patch.object(med, 'text_to_binary_file') as converter:
                with self.assertRaisesRegex(ValueError, 'Latin-1'):
                    med.sync_med_from_hsl(str(hsl), str(binary), activity_data_base64='AAAA')
                converter.assert_not_called()
            self.assertEqual(original, {p.name: p.read_bytes() for p in Path(folder).iterdir()})
            self.observe('full-sync', originals_preserved=True, temporary_files_remaining=0)

    def test_on_save_non_latin1_surfaces_failure_and_preserves_binary_companions(self):
        with tempfile.TemporaryDirectory() as folder:
            hsl, binary, stp_path = (Path(folder, 'fixture' + ext) for ext in ('.hsl', '.med', '.stp'))
            hsl.write_text(hsl_fixture('Comment', '// unsupported 漢'), encoding='utf-8')
            binary.write_bytes(b'synthetic original MED'); stp_path.write_bytes(b'synthetic original STP')
            with patch.object(med, 'extract_activity_data_from_med', return_value='AAAA'), \
                    patch.object(med, 'text_to_binary_file') as converter, \
                    self.assertLogs(med.logger, level='ERROR'):
                with self.assertRaisesRegex(OSError, r'\.med sync failed') as raised:
                    med.correct_block_markers_on_save(str(hsl))
                self.assertIsInstance(raised.exception.__cause__, ValueError)
                converter.assert_not_called()
            self.assertEqual(b'synthetic original MED', binary.read_bytes())
            self.assertEqual(b'synthetic original STP', stp_path.read_bytes())
            self.assertEqual({'fixture.hsl', 'fixture.med', 'fixture.stp'}, {p.name for p in Path(folder).iterdir()})
            self.observe('on-save', binary_companions_preserved=True, temporary_files_remaining=0,
                         limitation='HSL checksum can change before companion sync; no multi-file atomicity claim')

    def test_default_guid_literal_backslash_preserves_python_header_and_token(self):
        guid = r'fixture\tname'
        clsid = sorted(med.DEVICE_CLSIDS)[0]
        for module in (med, stp):
            with self.subTest(module=module.__name__):
                section = module.build_default_stp_section(guid, clsid, '')
                expected = strict_sections(section)
                self.assertEqual(guid, expected[0][0])
                self.assertEqual([guid], values_after(expected[0][1], '1CommandStepFileGuid'))
                self.observe(module.__name__, literal_header=guid, literal_guid_token=guid)

    def test_native_converter_rejects_backslash_in_section_header(self):
        # Python's existing header grammar permits this spelling; native input
        # requires ASCII letters/digits/underscores. Do not claim interoperability.
        guid = r'fixture\tname'
        section = med.build_default_stp_section(guid, sorted(med.DEVICE_CLSIDS)[0], '')
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder, 'source.txt'), Path(folder, 'fixture.stp')
            source.write_text(container_text(section), encoding='latin1', newline='')
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                codec.text_to_binary_file(source, target)
            self.assertFalse(target.exists())
            self.observe('native-header', input_header=guid, exit_code=raised.exception.returncode,
                         verdict='unsupported header rejected; not a native GUID roundtrip')

    def test_default_guid_quote_rejected_by_existing_header_grammar(self):
        for module in (med, stp):
            with self.subTest(module=module.__name__):
                with self.assertRaisesRegex(ValueError, 'HxPars section'):
                    module.build_default_stp_section('bad"header', sorted(med.DEVICE_CLSIDS)[0], '')
                self.observe(module.__name__, quote_in_header='rejected')

    def test_default_builder_token_parity_for_every_device_clsid(self):
        with patch.object(med, 'datetime') as med_time, patch.object(stp, 'datetime') as stp_time:
            med_time.now.return_value = stp_time.now.return_value = FIXED_TIME
            for clsid in sorted(med.DEVICE_CLSIDS):
                with self.subTest(clsid=clsid):
                    expected = strict_sections(med.build_default_stp_section('fixed_guid', clsid, ''))
                    actual = strict_sections(stp.build_default_stp_section('fixed_guid', clsid, ''))
                    self.assertEqual(expected, actual)
                    self.observe(clsid, decoded_tokens_equal=True)

    def test_populated_multiblock_and_return_value_variants(self):
        variants = [
            ('submethod-return', 'SubmethodCall', ['answer = Compute(value, "café µ");'],
             {'1ReturnValue': ['answer'], '1FunctionName': ['Compute'], '3FieldCount': ['2'], '1Value.0': ['value'], '1Value.1': ['"café µ"']}),
            ('library-return', 'SingleLibFunction', ['answer = Library::Compute(value, "text");'],
             {'1ReturnValue': ['answer'], '1FunctionName': ['Library::Compute'], '3FieldCount': ['2']}),
            ('loop-for', 'Loop', ['{\nfor(counter = 0; counter < 3;)\n{\ncounter = counter + 1;', '}\n}'],
             {'1LoopCounter': ['counter'], '3NbrOfIterations': ['3']}),
            ('loop-while', 'Loop', ['{\ncounter = 0; while (flag)\n{\ncounter = counter + 1;', '}\n}'],
             {'1LoopCounter': ['counter'], '3NbrOfIterations': ['0']}),
            ('if-then-else', 'IfThenElse', ['if (value > 0) {', '} else {', '}'], {}),
        ]
        for name, kind, blocks, fields in variants:
            with self.subTest(variant=name):
                record = med.HslStepRecord('populated', med.STEP_CLSID[kind],
                    [med.HslStepBlock(i + 1, code, i + 1) for i, code in enumerate(blocks)])
                section = med.build_step_section(record); expected = strict_sections(section)
                self.assertEqual(blocks, values_after(expected[0][1], '1-533921779'))
                for field, values in fields.items():
                    self.assertEqual(values, values_after(expected[0][1], field))
                actual = self.native_section(section, expected)
                self.assertEqual(blocks, values_after(actual[0][1], '1-533921779'))
                self.observe(name, block_count=len(blocks), populated_fields=fields, all_decoded_tokens_equal=True)


if __name__ == '__main__':
    unittest.main()
