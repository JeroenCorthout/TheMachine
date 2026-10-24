"""The pinned OpenClaw package: the pin follows the plugin's version, and a registry record that names another checksum
(or a version that isn't pinned) installs nothing."""
import json
import unittest

import openclaw_pin

GOOD = 'sha512-' + 'A' * 86 + '=='
OTHER = 'sha512-' + 'B' * 86 + '=='


class PinTest(unittest.TestCase):
    def test_the_pin_is_the_version_the_plugin_asks_for(self):
        data = openclaw_pin.pin()
        self.assertEqual(data['version'], openclaw_pin.plugin_version())
        self.assertEqual(data['package'], 'openclaw')
        self.assertRegex(data['integrity'], openclaw_pin.INTEGRITY)

    def test_only_the_pinned_checksum_passes(self):
        data = {'package': 'openclaw', 'version': '2026.10.5', 'integrity': GOOD}
        asked = []
        record = lambda integrity: lambda url: asked.append(url) or {'dist': {'integrity': integrity}}
        self.assertEqual(openclaw_pin.check('2026.10.5', data, record(GOOD)), GOOD)
        self.assertEqual(asked, ['https://registry.npmjs.org/openclaw/2026.10.5'])
        with self.assertRaises(openclaw_pin.PinMismatch):
            openclaw_pin.check('2026.10.5', data, record(OTHER))
        with self.assertRaises(openclaw_pin.PinMismatch):
            openclaw_pin.check('2026.10.5', data, lambda url: {})
        with self.assertRaises(openclaw_pin.PinMismatch):  # not the pinned version: not even asked
            openclaw_pin.check('2026.11.1', data, lambda url: self.fail('asked the registry'))
        with self.assertRaises(openclaw_pin.PinMismatch):
            openclaw_pin.check('2026.10.5', {**data, 'integrity': 'sha1-abc'}, record('sha1-abc'))

    def test_the_file_is_plain_json(self):
        self.assertEqual(json.loads(openclaw_pin.PIN_FILE.read_text())['version'], openclaw_pin.pin()['version'])


if __name__ == '__main__':
    unittest.main()
