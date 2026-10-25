"""Unit tests for standalone_med_tools.stp_generator module."""

from __future__ import annotations

import os
import tempfile
import unittest

try:
    from standalone_med_tools.stp_generator import (
        DEVICE_CLSIDS,
        DEVICE_STEP_NAMES,
        bare_clsid,
        is_device_step_clsid,
        build_default_stp_section,
        build_error_entry,
        build_stp_text,
        get_default_error_recoveries,
        parse_existing_stp_sections,
        sync_stp_from_hsl,
    )

    _IMPORT_OK = True
except ImportError as exc:  # pragma: no cover
    _IMPORT_OK = False
    _IMPORT_ERR = str(exc)


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestDeviceConstants(unittest.TestCase):
    """Tests for DEVICE_CLSIDS and DEVICE_STEP_NAMES constants."""

    def test_device_clsids_not_empty(self):
        """DEVICE_CLSIDS has entries."""
        self.assertIsInstance(DEVICE_CLSIDS, set)
        self.assertGreater(len(DEVICE_CLSIDS), 0)

    def test_device_step_names_keys_match(self):
        """All DEVICE_CLSIDS have a corresponding name in DEVICE_STEP_NAMES."""
        for clsid in DEVICE_CLSIDS:
            self.assertIn(
                clsid,
                DEVICE_STEP_NAMES,
                f"CLSID {clsid} missing from DEVICE_STEP_NAMES",
            )

    def test_known_device_clsid(self):
        """ML_STAR Initialize CLSID is present (common Hamilton device step)."""
        init_clsid = "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}"
        self.assertIn(init_clsid, DEVICE_CLSIDS)
        self.assertEqual(DEVICE_STEP_NAMES[init_clsid], "Initialize")


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestBareClsid(unittest.TestCase):
    """Tests for the bare_clsid helper."""

    def test_strips_device_prefix(self):
        self.assertEqual(
            bare_clsid("ML_STAR:{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}"),
            "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",
        )

    def test_bare_passthrough(self):
        clsid = "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}"
        self.assertEqual(bare_clsid(clsid), clsid)


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestIsDeviceStepClsid(unittest.TestCase):
    """Tests for the is_device_step_clsid helper."""

    def test_known_clsid_returns_true(self):
        self.assertTrue(
            is_device_step_clsid("ML_STAR:{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}")
        )

    def test_unknown_clsid_returns_false(self):
        self.assertFalse(
            is_device_step_clsid("{00000000-0000-0000-0000-000000000000}")
        )


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestGetDefaultErrorRecoveries(unittest.TestCase):
    """Tests for get_default_error_recoveries."""

    def test_get_default_error_recoveries_structure(self):
        """Returns a list of strings (tokens) containing expected error markers."""
        bare = "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}"  # Aspirate
        tokens = get_default_error_recoveries(bare)
        self.assertIsInstance(tokens, list)
        self.assertGreater(len(tokens), 0)
        # All entries should be strings
        for tok in tokens:
            self.assertIsInstance(tok, str)
        # Should contain the error number markers
        self.assertIn('"(3"', tokens)   # Hardware error entry


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestBuildDefaultStpSection(unittest.TestCase):
    """Tests for build_default_stp_section."""

    _INIT_CLSID = "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}"
    _ASPIRATE_CLSID = "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}"

    def test_build_default_stp_section_format(self):
        """Output contains the expected DataDef header and closing bracket."""
        section = build_default_stp_section(
            "test_guid", self._INIT_CLSID, "ML_STAR.Initialize(...);"
        )
        self.assertIn("DataDef,HxPars,3,test_guid,", section)
        self.assertTrue(section.rstrip().endswith("];"))

    def test_build_default_stp_section_guid(self):
        """GUID appears in the output section."""
        guid = "my_unique_guid_12345"
        section = build_default_stp_section(
            guid, self._INIT_CLSID, "ML_STAR.Initialize(...);"
        )
        self.assertIn(guid, section)

    def test_build_default_stp_section_clsid(self):
        """Step name derived from CLSID appears in the output."""
        section = build_default_stp_section(
            "guid_asp", self._ASPIRATE_CLSID, "ML_STAR.Aspirate(...);"
        )
        # The step name "Aspirate" should appear
        self.assertIn("Aspirate", section)

    def test_initialize_section_has_always_initialize(self):
        """Initialize step type includes AlwaysInitialize field."""
        section = build_default_stp_section(
            "guid_init", self._INIT_CLSID, "ML_STAR.Initialize(...);"
        )
        self.assertIn('"3AlwaysInitialize"', section)

    def test_aspirate_section_has_tip_type(self):
        """Aspirate step type includes TipType field."""
        section = build_default_stp_section(
            "guid_asp", self._ASPIRATE_CLSID, "ML_STAR.Aspirate(...);"
        )
        self.assertIn('"3TipType"', section)

    def test_aspirate_section_has_channel_pattern(self):
        """Aspirate step type includes ChannelPattern field."""
        section = build_default_stp_section(
            "guid_asp", self._ASPIRATE_CLSID, "ML_STAR.Aspirate(...);"
        )
        self.assertIn('"1ChannelPattern"', section)


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestBuildStpText(unittest.TestCase):
    """Tests for build_stp_text."""

    def test_build_stp_text_empty(self):
        """Empty device_steps produces valid structure with header."""
        text = build_stp_text({}, author="TestUser")
        self.assertIn("HxCfgFile,3;", text)
        self.assertIn("ConfigIsValid,Y;", text)
        # Should contain AuditTrailData section
        self.assertIn("AuditTrailData", text)

    def test_build_stp_text_with_sections(self):
        """Sections for provided device steps appear in output."""
        device_steps = {
            "guid_1": {
                "clsid": "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",
                "code": "ML_STAR.Initialize(...);",
            },
            "guid_2": {
                "clsid": "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",
                "code": "ML_STAR.Aspirate(...);",
            },
        }
        text = build_stp_text(device_steps, author="TestUser")
        self.assertIn("guid_1", text)
        self.assertIn("guid_2", text)
        self.assertIn("HxCfgFile,3;", text)

    def test_build_stp_text_preserves_existing_section(self):
        """Existing sections are preserved verbatim instead of regenerated."""
        existing_section = (
            'DataDef,HxPars,3,guid_existing,\r\n'
            '[\r\n'
            '"1CustomField",\r\n'
            '"custom_value",\r\n'
            '")"'
            '\r\n];'
        )
        existing = {"guid_existing": existing_section}
        device_steps = {
            "guid_existing": {
                "clsid": "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",
                "code": "ML_STAR.Initialize(...);",
            },
        }
        text = build_stp_text(device_steps, existing_sections=existing, author="TestUser")
        self.assertIn("custom_value", text)
        self.assertEqual(existing_section, parse_existing_stp_sections(text)["guid_existing"])


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestParseExistingStpSections(unittest.TestCase):
    """Tests for parse_existing_stp_sections."""

    def test_parse_existing_stp_sections_empty(self):
        """Empty existing text must not authorize overwriting configured steps."""
        with self.assertRaises(ValueError):
            parse_existing_stp_sections("")

    def test_parse_existing_stp_sections_roundtrip(self):
        """Build then parse preserves section count."""
        device_steps = {
            "guid_a": {
                "clsid": "{1C0C0CB0-7C87-11D3-AD83-0004ACB1DCB2}",
                "code": "ML_STAR.Initialize(...);",
            },
            "guid_b": {
                "clsid": "{541143F5-7FA2-11D3-AD85-0004ACB1DCB2}",
                "code": "ML_STAR.Aspirate(...);",
            },
        }
        text = build_stp_text(device_steps, author="TestUser")
        sections = parse_existing_stp_sections(text)
        # Should find at least the two device step GUIDs
        # (AuditTrailData section uses same DataDef,HxPars,3 pattern)
        found_guids = {k for k in sections if k != "__Properties__"}
        self.assertIn("guid_a", found_guids)
        self.assertIn("guid_b", found_guids)

    def test_parse_properties_section(self):
        """Properties section captured under __Properties__ key."""
        text = (
            'DataDef,Method,1,Properties,\r\n'
            '{\r\n'
            'ReadOnly, "0"\r\n'
            '};\r\n'
        )
        sections = parse_existing_stp_sections(text)
        self.assertIn("__Properties__", sections)


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestSyncStpFromHsl(unittest.TestCase):
    """Tests for sync_stp_from_hsl (integration-level, uses temp files)."""

    def test_sync_stp_from_hsl_no_devices(self):
        """HSL with no device steps produces no .stp (or minimal .stp)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hsl_path = os.path.join(tmpdir, "test.hsl")
            stp_path = os.path.join(tmpdir, "test.stp")
            # Write a minimal HSL file with no device block markers
            with open(hsl_path, "w", encoding="utf-8") as f:
                f.write("// empty method\n")
            sync_stp_from_hsl(hsl_path, stp_path=stp_path)
            # With no device steps and no existing .stp, the function
            # returns early without creating the file.
            self.assertFalse(
                os.path.exists(stp_path),
                "No .stp should be created when there are no device steps",
            )


@unittest.skipUnless(_IMPORT_OK, f"Import failed: {globals().get('_IMPORT_ERR', '')}")
class TestBuildErrorEntry(unittest.TestCase):
    """Tests for build_error_entry."""

    def test_basic_structure(self):
        """Error entry contains opening and closing tokens."""
        entry = build_error_entry(3, 375, 374, True, [
            {"id": 3, "desc": 419, "title": 418, "is_default": True},
        ])
        self.assertIsInstance(entry, list)
        self.assertEqual(entry[0], '"(3"')
        self.assertEqual(entry[-1], '")"')

    def test_recovery_count(self):
        """NbrOfRecovery matches the number of recovery options."""
        recoveries = [
            {"id": 3, "desc": 419, "title": 418, "is_default": True},
            {"id": 1, "desc": 371, "title": 370, "is_default": False},
        ]
        entry = build_error_entry(3, 375, 374, True, recoveries)
        # Find NbrOfRecovery value token
        idx = entry.index('"3NbrOfRecovery"')
        self.assertEqual(entry[idx + 1], f'"{len(recoveries)}"')


if __name__ == "__main__":
    unittest.main()
