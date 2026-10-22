"""Export and import: what moves, what stays behind, and that keys only travel encrypted."""
import base64
import io
import os
import json
import shutil
import sqlite3
import tarfile
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import message_seal
import moving

HAS_OPENSSL = shutil.which('openssl') is not None


class Moving(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = patch.object(moving, 'own_connectors', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = self.tmp / 'hosted/data'
        self.data.mkdir(parents=True)
        db = sqlite3.connect(self.data / 'events.sqlite')
        db.execute('CREATE TABLE events(id TEXT, title TEXT)')
        db.execute("INSERT INTO events VALUES ('e1', 'Example subject')")
        db.commit()
        db.close()
        for name, text in (('slack.env', 'SLACK_TOKEN=xoxb-example\n'), ('owner-token', 'owner-example'),
                           ('machine-token', 'hosted-example'), ('events-old.sqlite.bak', 'old copy'),
                           ('notes.txt', 'kept as data')):
            (self.data / name).write_text(text)
        self.config = self.data / 'config.json'
        self.config.write_text(json.dumps({
            'database': str(self.data / 'events.sqlite'),
            'credentials': {'slack': str(self.data / 'slack.env'), 'google-broker': str(self.data / 'machine-token')},
            'google_broker': {'url': 'https://broker.example.test'}, 'manage_url': 'https://manage.example.test',
            'needs_you_file': str(self.data / 'needs-you.json'), 'enrichment': {'enabled': False, 'by': 'hosting'},
            'accounts': [{'id': 'work-gmail', 'service': 'gmail', 'transport': 'broker'},
                         {'id': 'slack', 'service': 'slack', 'transport': 'api', 'credential': 'slack'}]}))
        self.space = self.tmp / 'hosted/workspace'
        (self.space / 'memory').mkdir(parents=True)
        (self.space / 'tools/personal-assistant').mkdir(parents=True)
        (self.space / 'USER.md').write_text('About the owner\n')
        (self.space / 'memory/today.md').write_text('A note\n')
        (self.space / 'tools/personal-assistant/assistant.py').write_text('app code')
        self.claw = self.tmp / 'hosted/openclaw'
        (self.claw / 'credentials').mkdir(parents=True)
        (self.claw / 'openclaw.json').write_text(json.dumps({'channels': {'telegram': {'enabled': True, 'botToken': '123:example'}},
                                                             'commands': {'ownerAllowFrom': ['tg:1000']}}))
        (self.claw / 'credentials/relay.secret').write_text('relay-example')
        self.out = self.tmp / 'out'

    def export(self, passphrase=None):
        path, manifest = moving.export(self.config, self.out, passphrase=passphrase, workspace=self.space,
                                       openclaw_home=self.claw, source='hosted')
        self.code = passphrase or manifest.get('code')
        return path, manifest

    def files(self, path):
        """{name: bytes} of an export, opened with its code or passphrase."""
        plain = self.tmp / 'opened.tar.gz'
        moving._openssl(['-in', str(path), '-out', str(plain)], self.code, decrypt=True)
        with tarfile.open(plain, 'r:gz') as archive:
            found = {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
        plain.unlink()
        return found

    def names(self, path):
        return set(self.files(path)), None

    def read(self, path, name):
        return self.files(path)[name].decode()

    def target(self):
        local = self.tmp / 'local'
        return local / 'data/config.json', local / 'workspace', local / 'openclaw'

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_without_a_passphrase_no_key_leaves(self):
        path, manifest = self.export()
        self.assertTrue(path.name.endswith('.tar.gz.enc'))  # always encrypted, with an export code made for it
        self.assertRegex(manifest['code'], r'^([A-Z2-9]{4}-){4}[A-Z2-9]{4}$')
        self.assertNotIn(b'Example subject', path.read_bytes())
        names, _ = self.names(path)
        self.assertIn('data/events.sqlite', names)
        self.assertIn('data/notes.txt', names)
        self.assertIn('workspace/USER.md', names)
        self.assertIn('workspace/memory/today.md', names)
        self.assertFalse([n for n in names if n.startswith(('secrets/', 'workspace/tools'))])
        self.assertNotIn('data/events-old.sqlite.bak', names)
        blob = b''.join(self.files(path).values())
        self.assertNotIn(manifest['code'].encode(), blob)  # the code is never in the archive
        for secret in (b'xoxb-example', b'owner-example', b'hosted-example', b'123:example', b'relay-example'):
            self.assertNotIn(secret, blob)
        self.assertEqual(manifest['telegram'], {'enabled': True, 'owner_allow_from': ['tg:1000']})
        self.assertFalse(manifest['includes_keys'])
        self.assertTrue(any('sign in to Google again' in line for line in manifest['reconnect']))
        self.assertTrue(any(line.startswith('slack:') for line in manifest['reconnect']))
        self.assertIn('assistant import', self.read(path, 'README.txt'))

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_a_sealed_email_attachment_is_exported_opened(self):
        key = os.urandom(32)
        (self.data / message_seal.KEY_NAME).write_bytes(base64.b64encode(key) + b'\n')
        folder = self.space / 'data/assistant-attachments/job-1'
        folder.mkdir(parents=True)
        (folder / 'invoice.pdf').write_bytes(message_seal.Sealer(key).seal_bytes(b'%PDF invoice'))
        path, _ = self.export()
        self.assertEqual(self.read(path, 'workspace/data/assistant-attachments/job-1/invoice.pdf'), '%PDF invoice')
        self.assertNotIn(message_seal.KEY_NAME, ' '.join(self.names(path)[0]))

    def test_keys_kept_by_hand_stay_out_without_a_passphrase(self):
        (self.data / 'old/share/gogcli/keyring').mkdir(parents=True)
        (self.data / 'old/share/gogcli/keyring/_gogcli_key_v1_x').write_text('encrypted-example')
        (self.data / 'old/openclaw-before.json').write_text('{"gateway": {"auth": {"mode": "x"}}}')
        (self.data / 'answer.json').write_text('{"session_token": "abcdefghijklmnopqrstuvwxyz0123"}')
        (self.data / 'events.sqlite.bak-20261020').write_text('old copy')
        (self.data / 'events.pre-fix-20261020T101010Z.sqlite3').write_text('old copy')
        (self.space / 'memory/fine.md').write_text('A token is a small thing\n')
        path, _ = self.export()
        names, _ = self.names(path)
        self.assertFalse([n for n in names if 'keyring' in n or 'openclaw-before' in n or 'answer.json' in n or '.bak-' in n or '.pre-fix-' in n])
        self.assertIn('workspace/memory/fine.md', names)
        self.assertIn('data/notes.txt', names)

    def test_rebuildable_folders_stay_out_and_are_listed(self):
        for folder in ('.venvs/x/lib', 'project/node_modules/pkg', 'project/.git/objects', 'scripts/__pycache__'):
            (self.space / folder).mkdir(parents=True)
            (self.space / folder / 'blob').write_bytes(b'x' * 2000)
        (self.space / 'project/notes.md').write_text('mine\n')
        path, manifest = self.export()
        names, _ = self.names(path)
        self.assertIn('workspace/project/notes.md', names)
        self.assertFalse([n for n in names if any(p in n for p in ('.venvs', 'node_modules', '.git/', '__pycache__'))])
        self.assertEqual(set(manifest['left_out']) & {'.venvs', 'node_modules', '.git', '__pycache__'},
                         {'.venvs', 'node_modules', '.git', '__pycache__'})
        self.assertEqual(manifest['left_out']['node_modules'], 2000)
        self.assertIn('node_modules 2 KB', moving.left_out_text(manifest['left_out']))
        self.assertIn('node_modules', moving.estimate(self.config, workspace=self.space,
                                                      openclaw_home=self.claw)['left_out'])

    def test_the_database_copy_is_whole(self):
        path, _ = self.export()
        (self.tmp / 'check/data').mkdir(parents=True)
        (self.tmp / 'check/data/events.sqlite').write_bytes(self.files(path)['data/events.sqlite'])
        db = sqlite3.connect(self.tmp / 'check/data/events.sqlite')
        self.addCleanup(db.close)
        rows = db.execute('SELECT title FROM events').fetchall()
        self.assertEqual(rows, [('Example subject',)])

    def test_import_into_a_fresh_install(self):
        path, manifest = self.export()
        config_path, space, claw = self.target()
        with self.assertRaisesRegex(moving.MoveError, 'export code'):
            moving.import_(path, config_path, workspace=space, openclaw_home=claw)
        typed = manifest['code'].lower().replace('-', ' ')  # as someone may type it
        result = moving.import_(path, config_path, passphrase=typed, workspace=space, openclaw_home=claw)
        self.assertEqual([p.name for p in config_path.parent.parent.iterdir() if '.import-' in p.name], [])  # unpacked copy gone
        config = json.loads(config_path.read_text())
        data = str(config_path.parent)
        self.assertEqual(config['database'], data + '/events.sqlite')
        self.assertEqual(config['credentials'], {'slack': data + '/slack.env'})
        for key in ('google_broker', 'manage_url', 'needs_you_file'):
            self.assertNotIn(key, config)
        self.assertEqual(config['enrichment'], {'enabled': False})
        self.assertEqual(config['accounts'][0]['transport'], 'gog')
        self.assertEqual((space / 'USER.md').read_text(), 'About the owner\n')
        self.assertFalse((config_path.parent / 'slack.env').exists())
        self.assertFalse((claw / 'openclaw.json').exists())
        self.assertTrue(result['reconnect'])
        self.assertEqual(oct((config_path.parent / 'events.sqlite').stat().st_mode & 0o777), '0o600')

    def test_an_existing_setup_is_not_overwritten_without_force(self):
        path, _ = self.export()
        code = self.code
        config_path, space, claw = self.target()
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({'accounts': [{'id': 'mine'}]}))
        (space).mkdir(parents=True)
        (space / 'USER.md').write_text('Local version\n')
        with self.assertRaisesRegex(moving.MoveError, 'already has connected sources'):
            moving.import_(path, config_path, passphrase=code, workspace=space, openclaw_home=claw)
        result = moving.import_(path, config_path, passphrase=code, workspace=space, openclaw_home=claw, force=True)
        self.assertEqual((space / 'USER.md').read_text(), 'Local version\n')
        self.assertEqual((space / 'USER.md.from-export').read_text(), 'About the owner\n')
        self.assertTrue(any('USER.md' in note for note in result['notes']))
        self.assertTrue(list(config_path.parent.parent.glob('data.before-import-*')))

    def test_an_archive_reaching_outside_is_refused(self):
        bad = self.tmp / 'bad.tar.gz'
        with tarfile.open(bad, 'w:gz') as archive:
            info = tarfile.TarInfo('../escape.txt')
            info.size = 2
            archive.addfile(info, io.BytesIO(b'hi'))
        config_path, space, claw = self.target()
        with self.assertRaisesRegex(moving.MoveError, 'outside'):
            moving.import_(bad, config_path, workspace=space, openclaw_home=claw)
        self.assertFalse((self.tmp / 'escape.txt').exists())

    def test_a_zip_reaching_outside_is_refused(self):
        config_path, space, claw = self.target()
        for name in ('../escape.txt', 'C:/escape.txt', 'data\\..\\..\\escape.txt'):
            bad = self.tmp / 'bad.zip'
            with zipfile.ZipFile(bad, 'w') as archive:
                archive.writestr('manifest.json', '{}')
                archive.writestr(name, 'hi')
            with self.assertRaisesRegex(moving.MoveError, 'outside'):
                moving.import_(bad, config_path, workspace=space, openclaw_home=claw)
        self.assertFalse((self.tmp / 'escape.txt').exists())

    def test_an_older_tar_gz_export_still_imports(self):
        path, _ = self.export()
        older = self.tmp / 'the-machine-export-2026-01-01.tar.gz'
        with tarfile.open(older, 'w:gz') as archive:
            for name, data in self.files(path).items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        config_path, space, claw = self.target()
        moving.import_(older, config_path, workspace=space, openclaw_home=claw)
        self.assertEqual((space / 'USER.md').read_text(), 'About the owner\n')

    def test_a_short_passphrase_is_refused(self):
        with self.assertRaisesRegex(moving.MoveError, 'at least'):
            self.export('short')

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_with_a_passphrase_keys_move_encrypted(self):
        path, manifest = self.export('a long example passphrase')
        self.assertTrue(path.name.endswith('.tar.gz.enc'))
        self.assertEqual([p.name for p in self.out.iterdir()], [path.name])  # no plain archive or work folder left
        self.assertTrue(manifest['includes_keys'])
        self.assertNotIn('code', manifest)
        raw = path.read_bytes()
        self.assertNotIn(b'xoxb-example', raw)
        with self.assertRaises(tarfile.TarError):
            tarfile.open(path, 'r:gz').getnames()
        config_path, space, claw = self.target()
        with self.assertRaisesRegex(moving.MoveError, 'Wrong export code or passphrase'):
            moving.import_(path, config_path, passphrase='not the right one', workspace=space, openclaw_home=claw)
        moving.import_(path, config_path, passphrase='a long example passphrase', workspace=space, openclaw_home=claw)
        self.assertEqual((config_path.parent / 'slack.env').read_text(), 'SLACK_TOKEN=xoxb-example\n')
        self.assertEqual(oct((config_path.parent / 'slack.env').stat().st_mode & 0o777), '0o600')
        self.assertIn('123:example', (claw / 'openclaw.json').read_text())
        self.assertFalse((config_path.parent / 'machine-token').exists())

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_a_wrong_passphrase_that_decrypts_to_noise_is_still_a_wrong_passphrase(self):
        # The cipher has no integrity check: about 1 in 256 wrong passphrases get past the padding check.
        path, _ = self.export('a long example passphrase')
        config_path, space, claw = self.target()
        def noise(args, passphrase, decrypt=False):
            Path(args[args.index('-out') + 1]).write_bytes(b'\x93\x07noise' * 40)
        with patch.object(moving, '_openssl', side_effect=noise), \
                self.assertRaisesRegex(moving.MoveError, 'Wrong export code or passphrase'):
            moving.import_(path, config_path, passphrase='not the right one', workspace=space, openclaw_home=claw)

    def test_the_decrypted_export_is_read_only_inside_the_import_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            inside, outside = base / 'import', base / 'outside.tar.gz'
            inside.mkdir()
            outside.write_bytes(b'\x1f\x8b secret')
            for path in [base / 'import/../outside.tar.gz', outside, Path('/etc/passwd'), inside / '..']:
                with self.subTest(path=str(path)), self.assertRaisesRegex(moving.MoveError, 'not in the import folder'):
                    moving._head(path, inside)
            (inside / moving.DECRYPTED_NAME).symlink_to(outside)
            with self.assertRaisesRegex(moving.MoveError, 'could not be read'):
                moving._head(inside / moving.DECRYPTED_NAME, inside)
            (inside / moving.DECRYPTED_NAME).unlink()
            (inside / moving.DECRYPTED_NAME).write_bytes(b'PK\x03\x04rest')
            self.assertEqual(moving._head(inside / moving.DECRYPTED_NAME, inside), b'PK\x03\x04')

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_no_plain_copy_is_left_when_packing_fails(self):
        with patch.object(moving, 'readme', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                self.export()
        self.assertEqual(list(self.out.iterdir()), [])

    @unittest.skipUnless(HAS_OPENSSL, 'openssl not installed')
    def test_database_copies_stay_next_to_the_export_never_in_tmp(self):
        seen = []
        original = moving._open_messages
        def look(copy, path):
            seen.append((copy.parent.parent, oct(copy.parent.stat().st_mode & 0o777)))
            return original(copy, path)
        with patch.object(moving, '_open_messages', look):
            self.export()
        self.assertEqual(seen, [(self.out, '0o700')])

    def test_estimate_counts_without_writing(self):
        plain = moving.estimate(self.config, workspace=self.space, openclaw_home=self.claw)
        keys = moving.estimate(self.config, secrets=True, workspace=self.space, openclaw_home=self.claw)
        self.assertGreater(keys['files'], plain['files'])
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
