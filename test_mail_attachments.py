"""Email attachments at rest (mail_attachments.py): what a run takes from an email is sealed with the message key, the
run reads a private copy that goes afterwards, drafts and sends still get the real bytes, and files a run makes itself
stay as they are."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('assistant', ROOT / 'assistant.py')
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)
import mail_attachments
import message_seal
import next_actions

CANARY = b'%PDF-1.7 Zanzibarquill invoice 4471'


class Files(unittest.TestCase):
    def test_sealed_bytes_round_trip_and_fail_loudly(self):
        sealer = message_seal.Sealer(os.urandom(32))
        for data in [b'', b'x', CANARY * 1000]:
            sealed = sealer.seal_bytes(data)
            self.assertTrue(message_seal.is_sealed_bytes(sealed))
            self.assertEqual(sealer.open_bytes(sealed), data)
        self.assertNotIn(CANARY, sealer.seal_bytes(CANARY))
        self.assertEqual(sealer.open_bytes(CANARY), CANARY)  # plain bytes pass
        broken = bytearray(sealer.seal_bytes(CANARY)); broken[-20] ^= 1
        with self.assertRaises(message_seal.SealError):
            sealer.open_bytes(bytes(broken))
        with self.assertRaises(message_seal.SealError):
            message_seal.Sealer(os.urandom(32)).open_bytes(sealer.seal_bytes(CANARY))
        # a sealed file never opens as a sealed value, nor the other way round
        self.assertFalse(message_seal.is_sealed(sealer.seal_bytes(CANARY)))
        self.assertFalse(message_seal.is_sealed_bytes(sealer.seal('text')))


    def test_failed_atomic_replace_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'invoice.pdf'
            path.write_bytes(CANARY)
            with mock.patch.object(mail_attachments.os, 'replace', side_effect=OSError('rename failed')):
                with self.assertRaises(OSError):
                    mail_attachments._write(path, b'encrypted replacement', scrub=True)
            self.assertEqual(path.read_bytes(), CANARY)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_successful_replace_keeps_new_bytes_and_scrubs_only_old_inode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'invoice.pdf'
            path.write_bytes(CANARY)
            with open(path, 'rb') as original:
                mail_attachments._write(path, b'encrypted replacement', scrub=True)
                self.assertEqual(original.read(), b'\0' * len(CANARY))
            self.assertEqual(path.read_bytes(), b'encrypted replacement')

    def test_scrub_never_follows_a_symlink_to_another_file(self):
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / 'outside.txt'
            outside.write_bytes(CANARY)
            inside = Path(folder) / 'job-1'
            inside.mkdir()
            link = inside / 'invoice.pdf'
            link.symlink_to(outside)
            mail_attachments._write(link, b'encrypted replacement', scrub=True)
            self.assertEqual(outside.read_bytes(), CANARY)  # the target is neither scrubbed nor overwritten
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_bytes(), b'encrypted replacement')

    def test_attachment_names_are_plain_names_only(self):
        for name in ['invoice.pdf', 'Q3 report.v2.pdf', '..hidden']:
            self.assertEqual(mail_attachments.plain_name(name), name)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ['../escape.pdf', '../../etc/passwd', '/etc/passwd', 'a/b.pdf', '..', '.', '',
                         '..\\escape.pdf', 'C:evil.pdf', 'nul\0.pdf']:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'plain file name'):
                    mail_attachments.save({}, base / 'job-1', name, CANARY)
            self.assertEqual([p for p in base.rglob('*') if p.is_file()], [])


class AtRest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.workspace = base / 'workspace'
        self.attachments = self.workspace / 'data/assistant-attachments'
        self.store_dir = base / 'store'
        self.config = {'database': str(base / 'data/events.sqlite'), 'attachment_root': str(self.attachments),
                       'open_attachment_dir': str(base / 'open'), 'attachment_dir': str(self.store_dir),
                       'attachment_sources': [str(self.attachments), str(self.workspace / 'memory/assistant')], 'accounts': []}
        self.store = assistant.Store(self.config['database'], protect=True)

    def test_a_saved_attachment_is_sealed_and_the_run_reads_a_private_copy(self):
        saved = mail_attachments.save(self.config, self.attachments / 'job-1', 'invoice.pdf', CANARY)
        at_rest, copy = Path(saved['sealed']), Path(saved['saved'])
        self.assertEqual(at_rest, self.attachments / 'job-1/invoice.pdf')
        self.assertNotIn(CANARY, at_rest.read_bytes())
        self.assertTrue(message_seal.is_sealed_file(at_rest))
        self.assertEqual(copy.read_bytes(), CANARY)
        self.assertEqual(stat.S_IMODE(copy.parent.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(at_rest.stat().st_mode), 0o600)
        self.assertEqual(mail_attachments.read(at_rest, self.config), CANARY)

    def test_without_protection_it_stays_a_plain_file(self):
        config = self.config | {'database': str(Path(self.temp.name) / 'plain/events.sqlite')}
        assistant.Store(config['database'], protect=False)
        saved = mail_attachments.save(config, self.attachments / 'job-1', 'invoice.pdf', CANARY)
        self.assertEqual(saved, {'saved': str(self.attachments / 'job-1/invoice.pdf')})
        self.assertEqual(Path(saved['saved']).read_bytes(), CANARY)

    def test_drafts_attach_the_real_bytes_and_keep_email_ones_sealed(self):
        saved = mail_attachments.save(self.config, self.attachments / 'job-1', 'invoice.pdf', CANARY)
        made = self.attachments / 'job-1/summary.csv'; made.write_bytes(b'a,b\n1,2\n')
        for path in (saved['saved'], saved['sealed']):
            record, mine = next_actions.store_attachments([path, str(made)], self.config)
            self.assertEqual((record['size'], record['name']), (len(CANARY), 'invoice.pdf'))
            self.assertNotIn(CANARY, (self.store_dir / record['sha256']).read_bytes())
            self.assertEqual(next_actions.attachment_bytes(record, self.config), CANARY)
            self.assertEqual((self.store_dir / mine['sha256']).read_bytes(), b'a,b\n1,2\n')  # made by the run: as it is
        with self.assertRaisesRegex(ValueError, 'Attach only'):
            next_actions.store_attachments([str(Path(self.temp.name) / 'elsewhere.pdf')], self.config)

    def test_a_later_run_gets_a_readable_copy(self):
        saved = mail_attachments.save(self.config, self.attachments / 'job-1', 'invoice.pdf', CANARY)
        opened = mail_attachments.open_copy(self.config, saved['sealed'], 'job-2')
        self.assertEqual(Path(opened['saved']).read_bytes(), CANARY)
        self.assertEqual(Path(opened['saved']).parent.name, 'job-2')
        made = self.attachments / 'job-1/summary.csv'; made.write_bytes(b'plain')
        self.assertEqual(mail_attachments.open_copy(self.config, made, 'job-2'), {'saved': str(made.resolve())})
        for path, job in [(Path(self.temp.name) / 'x', 'job-2'), (saved['sealed'], '../x')]:
            with self.assertRaises(ValueError):
                mail_attachments.open_copy(self.config, path, job)

    def run_with_call(self, job, command, result, finished=True, started=None):
        stamp = (started or dt.datetime.now(dt.timezone.utc)).isoformat()
        with self.store.connect() as db:
            db.execute('INSERT INTO preparation_runs(job_id,suggestion_id,session_job_id,status,started_at,finished_at) VALUES(?,?,?,?,?,?)',
                       (job, 's-' + job, job, 'ok' if finished else 'running', stamp, stamp if finished else None))
            db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,result,started_at) VALUES(?,?,?,?,?,?)',
                       (job, 'c1', 'exec', json.dumps({'command': command}), json.dumps(result), stamp))

    def test_ended_runs_gog_downloads_are_sealed_in_place_and_made_files_stay(self):
        for job in ('job-1', 'job-2', 'job-3', 'job-4'):
            (self.attachments / job).mkdir(parents=True)
        fetched = self.attachments / 'job-1/261014-invoice.pdf'; fetched.write_bytes(CANARY)
        made = self.attachments / 'job-1/261014-filled.docx'; made.write_bytes(b'made by the run')
        clipped = self.attachments / 'job-2/offer.pdf'; clipped.write_bytes(CANARY + b'2')
        running = self.attachments / 'job-3/live.pdf'; running.write_bytes(CANARY + b'3')
        other = self.attachments / 'job-4/notes.pdf'; other.write_bytes(CANARY + b'4')
        gog = 'mkdir -p data/assistant-attachments/{0} && gog --readonly -a me@acme.test gmail attachment 1a2b ANG --out data/assistant-attachments/{0}/{1}'
        self.run_with_call('job-1', gog.format('job-1', fetched.name),
                           {'content': [{'type': 'text', 'text': f'path\tdata/assistant-attachments/job-1/{fetched.name}\ncached\tfalse\nbytes\t35'}]})
        self.run_with_call('job-2', gog.format('job-2', clipped.name),
                           {'clipped': True, 'preview': json.dumps({'content': [{'type': 'text', 'text': f'path\tdata/assistant-attachments/job-2/{clipped.name}\ncached'}]})[:120]})
        self.run_with_call('job-3', gog.format('job-3', running.name),
                           {'content': [{'type': 'text', 'text': f'path\tdata/assistant-attachments/job-3/{running.name}\n'}]}, finished=False)
        self.run_with_call('job-4', f'cp x data/assistant-attachments/job-4/{other.name}', {'content': [{'type': 'text', 'text': f'path\t{other}'}]})
        store_copy = self.store_dir / next_actions.hashlib.sha256(CANARY).hexdigest()
        self.store_dir.mkdir(); store_copy.write_bytes(CANARY)
        config = self.config | {'attachment_root': str(self.attachments)}
        self.assertEqual(mail_attachments.sweep(self.store.connect, config, store=self.store_dir), 2)
        for path in (fetched, clipped, store_copy):
            self.assertTrue(message_seal.is_sealed_file(path), path)
            self.assertNotIn(b'Zanzibarquill', path.read_bytes())
        self.assertEqual(mail_attachments.read(fetched, config), CANARY)
        self.assertEqual(made.read_bytes(), b'made by the run')  # the run made it: not an email's
        self.assertEqual(running.read_bytes(), CANARY + b'3')  # its run still works with it
        self.assertEqual(other.read_bytes(), CANARY + b'4')  # not taken from an email
        self.assertEqual(mail_attachments.sweep(self.store.connect, config, store=self.store_dir), 0)  # once
        # protection turned off: opened in place again
        self.assertEqual(mail_attachments.open_all(config, self.store_dir), 3)
        self.assertEqual((fetched.read_bytes(), store_copy.read_bytes()), (CANARY, CANARY))

    def test_copies_go_once_their_run_has_ended(self):
        saved = mail_attachments.save(self.config, self.attachments / 'job-1', 'invoice.pdf', CANARY)
        kept = mail_attachments.save(self.config, self.attachments / 'job-2', 'live.pdf', CANARY)
        self.run_with_call('job-1', 'assistant attachment --event 4 --save a1 --job job-1', {'saved': saved['saved']})
        self.run_with_call('job-2', 'assistant attachment --event 5 --save a1 --job job-2', {'saved': kept['saved']}, finished=False)
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11)
        mail_attachments.sweep(self.store.connect, self.config, now=later)
        self.assertFalse(Path(saved['saved']).exists())
        self.assertTrue(Path(kept['saved']).exists())
        self.assertTrue(Path(saved['sealed']).exists())
        # a run that never said it ended is over after two hours
        mail_attachments.sweep(self.store.connect, self.config, now=later + dt.timedelta(hours=3))
        self.assertFalse(Path(kept['saved']).exists())

    def test_gmail_attachments_are_saved_through_the_app(self):
        account = {'id': 'acme-gmail', 'service': 'gmail', 'transport': 'gog', 'email': 'alex@acme.test'}
        payload = {'parts': [{'partId': '1', 'filename': 'invoice.pdf', 'mimeType': 'application/pdf',
                              'body': {'size': len(CANARY), 'data': next_actions.base64.urlsafe_b64encode(CANARY).decode()}}]}
        app = types.SimpleNamespace(config=self.config | {'accounts': [account]}, store=self.store,
                                    gmail_message=lambda account_id, message_id: (account, {'payload': payload}))
        listed = assistant.App.mail_attachment(app, {'account_id': 'acme-gmail', 'message_id': '1a2b'})['attachments']
        self.assertEqual([(a['id'], a['name']) for a in listed], [('1', 'invoice.pdf')])
        saved = assistant.App.mail_attachment(app, {'account_id': 'acme-gmail', 'message_id': '1a2b', 'save': 'invoice.pdf', 'job_id': 'job-1'})
        self.assertEqual(Path(saved['saved']).read_bytes(), CANARY)
        self.assertTrue(message_seal.is_sealed_file(saved['sealed']))
        opened = assistant.App.mail_attachment(app, {'open': saved['sealed'], 'job_id': 'job-9'})
        self.assertEqual(Path(opened['saved']).read_bytes(), CANARY)


if __name__ == '__main__':
    unittest.main()
