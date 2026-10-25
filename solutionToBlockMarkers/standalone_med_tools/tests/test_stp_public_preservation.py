"""Both public sync modules preserve synthetic configured STP data on failure."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from standalone_med_tools import med_generator as med, stp_generator as stp, hxcfgfile_codec as codec
from standalone_med_tools.checksum import generate_checksum_line
from test_med_strict_roundtrip import configured_device_hsl, minimal_stp_text, strict_sections


class PublicStpPreservationTests(unittest.TestCase):
    def test_both_parsers_preserve_quoted_delimiters_and_reject_empty_or_junk(self):
        section = r'DataDef,HxPars,3,configured,["1Label","a[0]","quoted \" ]; value"];'
        for parser in (med._parse_existing_stp_sections, stp.parse_existing_stp_sections):
            with self.subTest(parser=parser.__module__):
                self.assertEqual(section, parser(minimal_stp_text(section))['configured'])
                for invalid in ('', 'junk' + section, section + 'junk', section + section):
                    with self.assertRaises(ValueError):
                        parser(invalid)

    def test_sibling_default_sections_have_strict_separators(self):
        for clsid in sorted(stp.DEVICE_CLSIDS):
            with self.subTest(clsid=clsid):
                text = stp.build_default_stp_section('synthetic', clsid, '')
                self.assertEqual(1, len(strict_sections(text)))

    def test_sibling_invalid_mapping_never_defaults_configured_section(self):
        with patch.object(stp, 'build_default_stp_section') as default:
            with self.assertRaises(ValueError):
                stp.build_stp_text({'configured': {'clsid': 'unused', 'code': ''}},
                                   {'configured': 'DataDef,HxPars,3,other,["x"];'})
            default.assert_not_called()

    def test_both_sync_entrypoints_leave_original_and_clean_partial_files(self):
        for module, writer_name in ((med, 'transactional_binary_write'), (stp, '_transactional_binary_write')):
            for mode in ('converter-error', 'malformed', 'empty', 'read-error'):
                with self.subTest(module=module.__name__, mode=mode), tempfile.TemporaryDirectory() as folder:
                    hsl, target = Path(folder, 'fixture.hsl'), Path(folder, 'fixture.stp')
                    hsl.write_text(configured_device_hsl(), encoding='utf-8')
                    original = b'synthetic configured bytes'
                    target.write_bytes(original)
                    outputs = []
                    def convert(source, output):
                        outputs.append(output)
                        output.write_text('' if mode == 'empty' else 'DataDef,HxPars,3,configured,["broken];', encoding='latin1')
                        if mode == 'converter-error':
                            raise OSError('synthetic converter failure')
                    read_text = Path.read_text
                    def read(path, *args, **kwargs):
                        if mode == 'read-error' and path in outputs:
                            raise OSError('synthetic converted-text read failure')
                        return read_text(path, *args, **kwargs)
                    with patch.object(module, 'binary_to_text_file', side_effect=convert), \
                            patch.object(Path, 'read_text', new=read), patch.object(module, writer_name) as writer:
                        with self.assertRaises((OSError, ValueError)):
                            module.sync_stp_from_hsl(str(hsl), str(target))
                        writer.assert_not_called()
                    self.assertEqual(original, target.read_bytes())
                    self.assertEqual(1, len(outputs))
                    self.assertFalse(outputs[0].exists())
                    self.assertEqual([], list(Path(folder).glob('*.bak')))

    def test_sibling_selected_guid_preserved_without_defaulting(self):
        section = 'DataDef,HxPars,3,configured,["1Label","a[0]"];'
        with tempfile.TemporaryDirectory() as folder:
            hsl, target = Path(folder, 'fixture.hsl'), Path(folder, 'fixture.stp')
            hsl.write_text(configured_device_hsl(), encoding='utf-8')
            target.write_bytes(b'synthetic configured bytes')
            def convert(source, output):
                output.write_text(minimal_stp_text(section), encoding='latin1', newline='')
            with patch.object(stp, 'binary_to_text_file', side_effect=convert), \
                    patch.object(stp, 'build_default_stp_section', side_effect=AssertionError('configured GUID defaulted')), \
                    patch.object(stp, '_transactional_binary_write') as writer:
                stp.sync_stp_from_hsl(str(hsl), str(target))
            writer.assert_called_once()
            self.assertEqual(section, stp.parse_existing_stp_sections(writer.call_args.args[1])['configured'])

    def test_sibling_native_roundtrip_preserves_configured_tokens(self):
        codec.resolve_converter_path()
        section = r'DataDef,HxPars,3,configured,["1Label","a[0]","quoted \" ]; value"];'
        expected = {'configured': ['1Label', 'a[0]', 'quoted " ]; value'], 'AuditTrailData': [')']}
        with tempfile.TemporaryDirectory() as folder:
            hsl, target, source, decoded = (Path(folder, name) for name in
                ('fixture.hsl', 'fixture.stp', 'source.txt', 'decoded.txt'))
            hsl.write_text(configured_device_hsl(), encoding='utf-8')
            source.write_text(minimal_stp_text(section), encoding='latin1')
            codec.text_to_binary_file(source, target)
            with patch.object(stp, 'build_default_stp_section', side_effect=AssertionError('unexpected defaults')):
                stp.sync_stp_from_hsl(str(hsl), str(target))
            self.assertEqual(b'\x03\x00\x01\x00', target.read_bytes()[:4])
            codec.binary_to_text_file(target, decoded)
            for model in (codec.parse_binary_med(target.read_bytes()),
                          codec.parse_text_med(decoded.read_text(encoding='latin1'))):
                self.assertEqual(expected, {s.key: s.tokens for s in model.hxpars_sections})
                self.assertEqual(len(expected), len(model.hxpars_sections))
                self.assertEqual(('Method,Properties', 'ReadOnly', '0'),
                    (model.named_section.name, model.named_section.key, model.named_section.value))
            self.assertEqual([], list(Path(folder).glob('*.sync_*.txt')))
            self.assertEqual([], list(Path(folder).glob('*.bak')))

    def test_on_save_actual_med_write_keeps_checksum_current_when_stp_fails(self):
        codec.resolve_converter_path()  # This installed-environment test must not silently skip.
        with tempfile.TemporaryDirectory() as folder:
            hsl, binary, target = (Path(folder, 'fixture' + ext) for ext in ('.hsl', '.med', '.stp'))
            source = configured_device_hsl().replace('// {{{ 1 ', '// {{{ 9 ')
            source += generate_checksum_line(source, author='synthetic', valid=0, prefix_char='//') + '\n'
            hsl.write_text(source, encoding='utf-8')
            binary.write_bytes(b'synthetic previous MED')
            original = b'synthetic configured STP'
            target.write_bytes(original)
            with patch.object(med, 'extract_activity_data_from_med', return_value='AAAA'), \
                    patch.object(med, 'sync_stp_from_hsl', side_effect=ValueError('synthetic unreadable STP')), \
                    self.assertLogs(med.logger, level='ERROR'):
                with self.assertRaisesRegex(OSError, r'\.stp sync failed'):
                    med.correct_block_markers_on_save(str(hsl))
            self.assertEqual(original, target.read_bytes())
            self.assertEqual(b'\x03\x00\x01\x00', binary.read_bytes()[:4])
            self.assertTrue(codec.parse_binary_med(binary.read_bytes()).hxpars_sections)
            # A second checksum update must be a byte-identical no-op.
            corrected = hsl.read_bytes()
            self.assertNotEqual(source.encode(), corrected)
            med.update_checksum_in_file(str(hsl))
            self.assertEqual(corrected, hsl.read_bytes())
            self.assertEqual([], list(Path(folder).glob('*.bak')))


if __name__ == '__main__':
    unittest.main()
