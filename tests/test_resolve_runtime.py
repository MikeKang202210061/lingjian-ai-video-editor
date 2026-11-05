"""Custom installations must work without global environment modifications."""
import json
import tempfile
import unittest
from pathlib import Path

from resolve_runtime import bridge_environment, configure_library


class RuntimeTests(unittest.TestCase):
    def test_unicode_library_roundtrip_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            install = root / '达芬奇'
            install.mkdir()
            library = install / 'fusionscript.dll'
            library.touch()
            configure_library(str(library), root)
            original = {'UNCHANGED': 'value'}
            result = bridge_environment(original, root)
            self.assertEqual(result['RESOLVE_SCRIPT_LIB'], str(library.resolve()))
            self.assertEqual(original, {'UNCHANGED': 'value'})
            other = root / 'fusionscript.dll'
            other.touch()
            result = bridge_environment({'RESOLVE_SCRIPT_LIB': str(other)}, root)
            self.assertEqual(result['RESOLVE_SCRIPT_LIB'], str(other))

    def test_invalid_setting_preserves_previous_and_bad_config_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / 'fusionscript.dll'
            library.touch()
            configure_library(str(library), root)
            config = root / '.local/resolve.json'
            previous = config.read_bytes()
            for invalid in ('fusionscript.dll', str(root / 'missing/fusionscript.dll'), str(config)):
                with self.assertRaises(ValueError):
                    configure_library(invalid, root)
                self.assertEqual(config.read_bytes(), previous)
            config.write_text(json.dumps({'script_library': str(library), 'exec': 'forbidden'}))
            with self.assertRaises(ValueError):
                bridge_environment({}, root)


if __name__ == '__main__':
    unittest.main()
