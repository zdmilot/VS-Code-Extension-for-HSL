"""Golden native format boundaries, never method or device execution."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from standalone_med_tools import hxcfgfile_codec as codec, med_generator as med, stp_generator as stp
from test_med_strict_roundtrip import container_text, strict_sections, step


class NativeCodecBoundaries(unittest.TestCase):
    def roundtrip(self, section, expected=None):
        codec.resolve_converter_path()
        with tempfile.TemporaryDirectory() as folder:
            source, binary, decoded = (Path(folder, name) for name in ('input.txt', 'fixture.med', 'decoded.txt'))
            source.write_text(container_text(section), encoding='latin1')
            codec.text_to_binary_file(source, binary)
            codec.binary_to_text_file(binary, decoded)
            wanted = strict_sections(section) if expected is None else expected
            self.assertEqual(wanted, strict_sections(decoded.read_text(encoding='latin1')))
            self.assertEqual(wanted, [(p.key, p.tokens) for p in codec.parse_binary_med(binary.read_bytes()).hxpars_sections])
            self.assertEqual(wanted, [(p.key, p.tokens) for p in codec.parse_text_med(decoded.read_text(encoding='latin1')).hxpars_sections])

    def test_golden_latin1_tab_control_and_literal_backslash(self):
        section = r'DataDef,HxPars,3,golden,["caf\0xe9","\0x09","\0x01","\t","\\t"];'
        expected = [('golden', ['caf\xe9', '\t', '\x01', '\t', '\\t'])]
        self.assertEqual(expected, strict_sections(section))
        self.roundtrip(section, expected)

    def test_all_registered_general_step_builders_roundtrip(self):
        for kind in sorted(med.STEP_CLSID):
            with self.subTest(kind=kind):
                self.roundtrip(med.build_step_section(step(kind, '// synthetic serialization only')))
        self.qualification_observations = {'registered_general_step_variants': len(med.STEP_CLSID),
            'roundtrips_attempted': len(med.STEP_CLSID), 'scope': 'container serialization, not expression or device execution'}

    def test_all_device_builders_and_both_default_stp_builders_roundtrip(self):
        for clsid in sorted(med.DEVICE_CLSIDS):
            with self.subTest(clsid=clsid):
                self.roundtrip(med.build_device_step_stub(med.HslStepRecord('device', 'ML_STAR:' + clsid, [])))
                for module in (med, stp):
                    self.roundtrip(module.build_default_stp_section('configured', clsid, ''))
        self.qualification_observations = {'device_variants': len(med.DEVICE_CLSIDS),
            'roundtrips_attempted': len(med.DEVICE_CLSIDS) * 3, 'scope': 'MED stub and two default STP builders; no physical qualification'}

    def test_empty_converter_override_has_explicit_configuration_error(self):
        with patch.dict(os.environ, {'HAMILTON_BINARY_ASCII_CONVERTER': ''}):
            with self.assertRaisesRegex(ValueError, 'empty'):
                codec.resolve_converter_path()

    def test_non_latin1_source_rejected_before_output_write(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder, 'synthetic.med')
            original = b'synthetic existing bytes'
            target.write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'Latin-1'):
                section = med.build_step_section(step('SingleLibFunction', 'Library::Call("\u6f22");'))
                med.transactional_binary_write(str(target), container_text(section))
            self.assertEqual(original, target.read_bytes())
            self.assertEqual([target], list(Path(folder).iterdir()))

    def test_real_converter_rejects_truncated_binary(self):
        codec.resolve_converter_path()
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder, 'invalid.med'), Path(folder, 'output.txt')
            source.write_bytes(b'\x03\x00\x01\x00\x01')
            with self.assertRaises(subprocess.CalledProcessError):
                codec.binary_to_text_file(source, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
