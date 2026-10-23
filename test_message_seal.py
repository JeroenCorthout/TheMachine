"""Message contents sealed at rest (message_seal.py): every source's text is unreadable without the owner's key, the
app reads, searches and exports as before, and support's learnings export never holds it."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('assistant', ROOT / 'assistant.py')
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)
import learnings
import message_seal
import moving

CANARY = 'Zanzibarquill'  # in every message's text; never in a sealed database's files
SOURCES = ['gmail.message', 'outlook.message', 'imap.message', 'slack.message', 'whatsapp.message', 'telegram.message',
           'signal.message', 'linkedin.message', 'x.dm', 'teams.message', 'sms.message', 'android.notification',
           'intercom.conversation', 'github.comment', 'zoom.transcript', 'calendar.event', 'brand_new_connector.thing']


def event(i, kind, extra=None):
    return {'account_id': 'acct', 'source_id': f'{kind}:{i}', 'kind': kind, 'occurred_at': f'2026-10-{10 + i % 9:02d}T09:00:00+00:00',
            'title': f'{CANARY} subject {i}', 'body': f'Dear owner, the {CANARY} contract is ready. Óscar will sign on Friday {i}.',
            'deleted': False, 'metadata': {'thread_id': f't{i}', 'from': 'Pat <pat@example.com>', 'labels': ['INBOX'],
                                            'snippet': f'{CANARY} preview', 'attachments': [f'{CANARY}.pdf'],
                                            'a_key_no_one_reviewed_yet': f'{CANARY} quoted text', **(extra or {})}}


def files(path):
    return b''.join(p.read_bytes() for p in Path(path).parent.glob(Path(path).name + '*'))


class Sealer(unittest.TestCase):
    def setUp(self):
        self.sealer = message_seal.Sealer(os.urandom(32))

    def test_round_trip_and_fresh_nonce(self):
        for text in ['a', 'Grüße 👋\n' * 500, CANARY]:
            sealed = self.sealer.seal(text)
            self.assertTrue(message_seal.is_sealed(sealed))
            if len(text) > 4:  # one letter turns up among random bytes by chance
                self.assertNotIn(text.encode(), sealed)
            self.assertEqual(self.sealer.open(sealed), text)
        self.assertNotEqual(self.sealer.seal('same'), self.sealer.seal('same'))
        for value in [None, '', 3, b'\x00plain blob']:
            self.assertEqual(self.sealer.seal(value), value)
            self.assertEqual(self.sealer.open(value), value)

    def test_tampering_and_another_key_fail_loudly(self):
        sealed = bytearray(self.sealer.seal('pay the invoice'))
        sealed[-20] ^= 1
        with self.assertRaises(message_seal.SealError):
            self.sealer.open(bytes(sealed))
        with self.assertRaises(message_seal.SealError):
            message_seal.Sealer(os.urandom(32)).open(self.sealer.seal('pay the invoice'))

    def test_text_the_gateway_plugin_seals_opens_here_and_fails_loudly_otherwise(self):
        sealed = self.sealer.seal_inbox(CANARY + ' Grüße 👋')
        self.assertTrue(sealed.startswith(message_seal.INBOX))
        self.assertNotIn(CANARY, sealed)
        self.assertEqual(message_seal.open_inbox(self.sealer, sealed), CANARY + ' Grüße 👋')
        self.assertNotEqual(self.sealer.seal_inbox('same'), self.sealer.seal_inbox('same'))
        for value in ['plain, from before', '', None]:
            self.assertEqual(message_seal.open_inbox(self.sealer, value), value)
            self.assertEqual(message_seal.open_inbox(None, value), value)
        with self.assertRaises(message_seal.SealError):
            message_seal.Sealer(os.urandom(32)).open_inbox(sealed)
        with self.assertRaises(message_seal.SealError):
            message_seal.open_inbox(None, sealed)

    def test_metadata_keeps_only_lookup_keys_in_the_clear_new_keys_sealed(self):
        text = assistant.encoded(event(1, 'gmail.message')['metadata'])
        sealed = self.sealer.seal_json(text)
        self.assertNotIn(CANARY, sealed)
        self.assertEqual(set(json.loads(sealed)), {'thread_id', 'from', 'labels', message_seal.SEALED_KEY})
        self.assertEqual(self.sealer.open_json(sealed), text)  # byte for byte what the app wrote (sorted keys)
        self.assertEqual(self.sealer.seal_json(sealed), sealed)
        only_plain = assistant.encoded({'thread_id': 'x', 'from_me': True})
        self.assertEqual(self.sealer.seal_json(only_plain), only_plain)
        self.assertTrue(message_seal.is_sealed(self.sealer.seal_json('"not an object"', frozenset())))

    def test_plain_metadata_marker_is_not_an_encrypted_envelope(self):
        for metadata in ({'thread_id': 't1', 'snippet': CANARY + ' mentions mc1_sealed'},
                         {'thread_id': 't1', 'mc1_sealed': CANARY},
                         {'thread_id': 't1', 'mc1_sealed': None}):
            text = json.dumps(metadata)
            self.assertFalse(message_seal.is_sealed_json(text))
            self.assertEqual(self.sealer.open_any(text), text)
            sealed = self.sealer.seal_json(text)
            self.assertNotIn(CANARY, sealed)
            self.assertTrue(message_seal.is_sealed_json(sealed))
            self.assertEqual(json.loads(self.sealer.open_json(sealed)), metadata)
            self.assertEqual(self.sealer.seal_json(sealed), sealed)

    def test_index_words_fold_like_sqlite_and_hide_the_word(self):
        self.assertEqual(self.sealer.words('Óscar, OSCAR oscar'), ' '.join([self.sealer.words('oscar')] * 3))
        self.assertNotIn('oscar', self.sealer.words('Óscar'))
        self.assertEqual(self.sealer.words('e-mail'), self.sealer.words('e mail'))


class Protected(unittest.TestCase):
    def setUp(self):
        # these tests choose plain or sealed themselves, also when the whole suite runs sealed
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('MESSAGE_PROTECTION_TEST', None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.sqlite'

    def fill(self, store):
        store.commit_page('acct', {'cursor': 1}, [event(i, kind) for i, kind in enumerate(SOURCES)])
        with store.connect() as db:
            db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,created_at,next_action,outcome) "
                       "VALUES('s1','k1',?,?,'[1]','[\"acct\"]','2026-10-10',?,?)",
                       (f'Reply about {CANARY}', f'Draft: {CANARY} looks good', json.dumps({'kind': 'email', 'body': CANARY}),
                        json.dumps({'state': 'needs_you', 'needs': CANARY})))
            db.execute("INSERT INTO draft_edits VALUES('d1','s1','a','b','edit',?,?,?,?,'owner','2026-10-10')", (CANARY,) * 4)
            db.execute("INSERT INTO facts(text,decision_id,created_at) VALUES('Prefers short replies','x','2026-10-10')")

    def test_every_source_is_sealed_at_rest_and_the_app_reads_and_searches_as_before(self):
        store = assistant.Store(self.path, protect=True)
        self.fill(store)
        self.assertNotIn(CANARY.encode(), files(self.path))
        self.assertNotIn(CANARY.lower().encode(), files(self.path))
        rows = store.events(['acct'], limit=100)
        self.assertEqual({r['kind'] for r in rows}, set(SOURCES))
        self.assertTrue(all(CANARY in r['title'] and CANARY in r['body'] and r['metadata']['snippet'] for r in rows))
        self.assertEqual(len(store.events(['acct'], query=CANARY.lower())), len(SOURCES))
        self.assertEqual(len(store.events(['acct'], query='oscar friday')), len(SOURCES))  # folded like before
        self.assertEqual(store.events(['acct'], query='absent'), [])
        with store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE json_extract(metadata,'$.thread_id')='t3'").fetchone()[0], 1)
            card = db.execute('SELECT * FROM suggestions').fetchone()
            self.assertIn(CANARY, card['title'])
            self.assertEqual(json.loads(card['next_action'])['body'], CANARY)
            db.execute("INSERT INTO event_search(event_search,rank) VALUES('integrity-check',1)")
        raw = sqlite3.connect(self.path)
        self.assertEqual(raw.execute("SELECT json_extract(next_action,'$.kind'), json_extract(outcome,'$.state') FROM suggestions").fetchone(),
                         ('email', 'needs_you'))
        self.assertEqual(raw.execute("SELECT text FROM facts").fetchone()[0], 'Prefers short replies')  # a learning stays readable

    def test_without_the_key_nothing_reads_or_writes_message_text(self):
        store = assistant.Store(self.path, protect=True)
        self.fill(store)
        raw = sqlite3.connect(self.path)
        self.assertEqual(raw.execute('SELECT typeof(title), typeof(body) FROM events LIMIT 1').fetchone(), ('blob', 'blob'))
        with self.assertRaises(sqlite3.OperationalError):  # a tool without the key can't slip plain text in
            raw.execute("INSERT INTO events(account_id,source_id,kind,occurred_at,title,body,metadata,content_hash,ingested_at) "
                        "VALUES('a','b','k','t','plain','plain','{}','h','t')")
        message_seal.key_path(self.path).rename(self.path.with_name('elsewhere'))
        with self.assertRaises(message_seal.SealError):
            assistant.Store(self.path)

    def test_a_path_that_forgets_to_seal_is_sealed_by_the_database(self):
        store = assistant.Store(self.path, protect=True)
        with store.connect() as db:  # plain SQL, as a future code path might write
            db.execute("INSERT INTO events(account_id,source_id,kind,occurred_at,title,body,metadata,content_hash,ingested_at) "
                       "VALUES('acct','x','new.kind','2026-10-10',?,?,?,'h','t')", (CANARY, CANARY, json.dumps({'note': CANARY})))
            db.execute("UPDATE events SET body=? WHERE source_id='x'", (CANARY + ' again',))
        self.assertNotIn(CANARY.encode(), files(self.path))
        self.assertEqual(store.events(['acct'], query=CANARY.lower())[0]['body'], CANARY + ' again')

    def test_a_table_made_later_is_guarded_and_sealed(self):
        store = assistant.Store(self.path, protect=True)
        with store.connect() as db:
            db.execute('DROP TABLE IF EXISTS network_replies')
        with sqlite3.connect(self.path) as raw:  # made, and written to, by code that knew nothing of sealing
            raw.execute('CREATE TABLE network_replies(ask_id TEXT PRIMARY KEY,body TEXT NOT NULL,at TEXT NOT NULL)')
            raw.execute("INSERT INTO network_replies VALUES('a',?,'t')", (CANARY,))
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT body FROM network_replies').fetchone()[0], CANARY)
            db.execute("INSERT INTO network_replies VALUES('b',?,'t')", (CANARY,))
        with store.connect():
            pass
        self.assertEqual(sqlite3.connect(self.path).execute("SELECT count(*) FROM network_replies WHERE typeof(body)='blob'").fetchone()[0], 2)

    def test_a_phone_notification_waits_sealed_and_arrives_readable(self):
        store = assistant.Store(self.path, protect=True)
        account = {'id': 'phone', 'service': 'android'}
        key = 'a' * 64
        for i, text in enumerate([store.sealer.seal_inbox(CANARY + ' sealed'), CANARY + ' from before the update']):
            envelope = {'id': f'n{i}', 'at_ms': 1000 + i, 'change': 'posted', 'package_name': 'com.example',
                        'notification_key_hash': key + str(i), 'text': text}
            with sqlite3.connect(self.path) as raw:  # as the Gateway's plugin writes it: no key on that connection
                raw.execute('INSERT INTO android_inbox VALUES(?,?,?)', (envelope['id'], json.dumps(envelope), '2020-01-01T00:00:00+00:00'))
        with store.connect() as db:
            rows = db.execute('SELECT * FROM android_inbox ORDER BY id').fetchall()
        events, consumed = store.android_feed_batch(account, rows)
        self.assertEqual(sorted(e['body'] for e in events), [CANARY + ' from before the update', CANARY + ' sealed'])
        self.assertEqual(sorted(consumed), ['n0', 'n1'])

    def test_role_quotes_promises_and_run_evidence_sealed_in_place_and_promises_still_dedupe(self):
        store = assistant.Store(self.path, protect=True)
        self.fill(store)
        columns = (('person_roles', 'quote'), ('promises', 'what'), ('promises', 'note'), ('profile_runs', 'evidence'))
        with sqlite3.connect(self.path) as raw:  # a database sealed before these columns were: no guards, plain text
            for table, column in columns:
                for when in ('insert', 'update'):
                    raw.execute(f'DROP TRIGGER mc_{table}_{column}_{when}')
            raw.execute("INSERT INTO person_roles(identifier,role,source,created_at,event_id,quote) VALUES('email:pat@example.com','lawyer','review','t',1,?)",
                        (f'{CANARY} counsel',))
            raw.execute("INSERT INTO promises(direction,who,what,check_at,event_id,account_id,status,note,created_at,updated_at) "
                        "VALUES('theirs','Pat',?,'2026-10-20',1,'acct','answered',?,'t','t')", (f'send the {CANARY} contract', f'They wrote again: {CANARY}'))
            raw.execute("INSERT INTO profile_runs(run_id,status,started_at,updated_at,evidence,kind) VALUES('r','ok','t','t',?,'profile')",
                        (json.dumps({'messages': [CANARY]}),))
        self.assertIn(CANARY.encode(), files(self.path))
        store = assistant.Store(self.path, protect=True)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT quote FROM person_roles').fetchone()[0], f'{CANARY} counsel')
            self.assertEqual(json.loads(db.execute('SELECT evidence FROM profile_runs').fetchone()[0]), {'messages': [CANARY]})
        raw = sqlite3.connect(self.path)
        for table, column in columns:
            self.assertEqual(raw.execute(f'SELECT typeof({column}) FROM {table}').fetchone()[0], 'blob', f'{table}.{column}')
        raw.close()
        first = store.promise({'direction': 'theirs', 'who': 'Pat', 'what': f'send the {CANARY} contract', 'event_id': 1})
        self.assertTrue(first['duplicate'])
        other = store.promise({'direction': 'theirs', 'who': 'Pat', 'what': 'confirm the seat count', 'event_id': 1})
        self.assertEqual(store.promise({'direction': 'theirs', 'who': 'Pat', 'what': 'confirm the seat count', 'event_id': 1}),
                         {'id': other['id'], 'status': 'open', 'duplicate': True})
        self.assertEqual(sorted(p['what'] for p in store.promises()), ['confirm the seat count', f'send the {CANARY} contract'])
        self.assertNotIn(CANARY.encode(), files(self.path))

    def test_migration_seals_existing_messages_and_rolls_back(self):
        plain = assistant.Store(self.path)
        self.fill(plain)
        self.assertIn(CANARY.encode(), files(self.path))
        self.assertEqual(len(plain.events(['acct'], query=CANARY.lower())), len(SOURCES))
        sealed = assistant.Store(self.path, protect=True)
        self.assertNotIn(CANARY.encode(), files(self.path))  # VACUUM: no free page keeps the old text
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir() if 'protection' in p.name), [])  # the copy goes once checked
        self.assertEqual(len(sealed.events(['acct'], query=CANARY.lower())), len(SOURCES))
        self.assertTrue(message_seal.is_protected(sqlite3.connect(self.path)))
        assistant.Store(self.path)  # no setting: stays as it is
        self.assertTrue(message_seal.is_protected(sqlite3.connect(self.path)))
        opened = assistant.Store(self.path, protect=False)
        self.assertFalse(message_seal.is_protected(sqlite3.connect(self.path)))
        self.assertIn(CANARY.encode(), files(self.path))
        self.assertEqual(len(opened.events(['acct'], query=CANARY.lower())), len(SOURCES))
        opened.commit_page('acct', {'cursor': 2}, [event(99, 'slack.message')])
        self.assertEqual(sqlite3.connect(self.path).execute("SELECT count(*) FROM events WHERE typeof(body)='blob'").fetchone()[0], 0)
        with opened.connect() as db:
            db.execute("INSERT INTO event_search(event_search,rank) VALUES('integrity-check',1)")

    def test_a_failed_migration_changes_nothing(self):
        plain = assistant.Store(self.path)
        self.fill(plain)
        original = message_seal.protect
        message_seal.protect = lambda db: (original(db), (_ for _ in ()).throw(RuntimeError('power cut')))
        try:
            with self.assertLogs(assistant.LOG, 'ERROR'):  # logged; the app starts as it was
                self.assertFalse(assistant.Store(self.path, protect=True).protected)
        finally:
            message_seal.protect = original
        self.assertFalse(message_seal.is_protected(sqlite3.connect(self.path)))
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir() if 'protection' in p.name), [])  # no copy stays behind
        self.assertEqual(len(assistant.Store(self.path).events(['acct'], query=CANARY.lower())), len(SOURCES))

    def everything_but_the_database(self):
        return b''.join(p.read_bytes() for p in self.path.parent.rglob('*') if p.is_file() and not p.name.startswith('events.sqlite'))

    def test_the_copy_while_switching_is_encrypted(self):
        self.fill(assistant.Store(self.path))
        seen = []
        original = message_seal.protect
        def look(db):
            seen.append(self.everything_but_the_database())
            safety = self.path.with_name('.events.sqlite.before-protection.d')
            seen.append(sorted(p.name for p in safety.iterdir()))
            seen.append(oct(safety.stat().st_mode & 0o777))
            return original(db)
        message_seal.protect = look
        try:
            assistant.Store(self.path, protect=True)
        finally:
            message_seal.protect = original
        self.assertNotIn(CANARY.encode(), seen[0])
        self.assertEqual(seen[1:], [['events.sqlite.before-protection.sealed'], '0o700'])
        self.assertFalse(self.path.with_name('.events.sqlite.before-protection.d').exists())

    def test_a_change_that_does_not_open_is_put_back(self):
        self.fill(assistant.Store(self.path))
        original = assistant.Store.check_sealed
        calls = []
        def broken(store):
            calls.append(1)
            if len(calls) == 1:
                raise message_seal.SealError('does not open')
            return original(store)
        assistant.Store.check_sealed = broken
        try:
            with self.assertLogs(assistant.LOG, 'ERROR'):
                store = assistant.Store(self.path, protect=True)
        finally:
            assistant.Store.check_sealed = original
        self.assertFalse(store.protected)
        self.assertFalse(message_seal.is_protected(sqlite3.connect(self.path)))  # put back as it was
        self.assertFalse(self.path.with_name('.events.sqlite.before-protection.d').exists())
        self.assertEqual(len(assistant.Store(self.path).events(['acct'], query=CANARY.lower())), len(SOURCES))

    def test_a_plain_copy_from_an_earlier_version_is_removed_once_the_database_checks_out(self):
        self.fill(assistant.Store(self.path, protect=True))
        legacy = self.path.with_name('events.sqlite.before-protection')
        legacy.write_bytes(CANARY.encode() * 100)
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-protection-failed'])
        assistant.Store(self.path, protect=True)
        self.assertFalse(legacy.exists())
        self.assertEqual(message_seal.problems(self.path), [])

    def test_a_plain_copy_stays_when_the_database_does_not_check_out(self):
        self.fill(assistant.Store(self.path, protect=True))
        legacy = self.path.with_name('events.sqlite.before-protection')
        legacy.write_bytes(b'kept')
        original = assistant.Store.database_ok
        assistant.Store.database_ok = lambda store: False
        try:
            with self.assertLogs(assistant.LOG, 'ERROR'):
                assistant.Store(self.path, protect=True)
        finally:
            assistant.Store.database_ok = original
        self.assertTrue(legacy.exists())
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-protection-failed'])

    def test_an_encrypted_copy_opens_only_with_its_key(self):
        self.fill(assistant.Store(self.path))
        key, there = os.urandom(32), Path(self.temp.name) / 'copy.sealed'
        assistant.seal_copy(self.path, there, key)
        self.assertNotIn(CANARY.encode(), there.read_bytes())
        self.assertEqual(sorted(p.name for p in there.parent.iterdir() if p.name.startswith('copy')), ['copy.sealed'])
        back = Path(self.temp.name) / 'back.sqlite'
        assistant.open_copy(there, back, key)
        self.assertEqual(sqlite3.connect(back).execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with self.assertRaises(message_seal.SealError):
            assistant.open_copy(there, back, os.urandom(32))
        there.write_bytes(there.read_bytes()[:-20])
        with self.assertRaises(message_seal.SealError):
            assistant.open_copy(there, back, key)

    def test_an_export_opens_the_messages_and_leaves_the_key_behind(self):
        config = Path(self.temp.name) / 'config.json'
        config.write_text(json.dumps({'database': str(self.path), 'accounts': []}))
        self.fill(assistant.Store(self.path, protect=True))
        found = self.exported(config)
        self.assertNotIn(f'data/{message_seal.KEY_NAME}', found)
        db = Path(self.temp.name) / 'exported.sqlite'
        db.write_bytes(found['data/events.sqlite'])
        self.assertFalse(message_seal.is_protected(sqlite3.connect(db)))
        self.assertIn(CANARY.encode(), db.read_bytes())

    def exported(self, config):
        import tarfile
        archive, manifest = moving.export(config, Path(self.temp.name) / 'out', workspace=Path(self.temp.name) / 'ws',
                                          openclaw_home=Path(self.temp.name) / 'oc')
        plain = Path(self.temp.name) / 'opened.tar.gz'
        moving._openssl(['-in', str(archive), '-out', str(plain)], manifest['code'], decrypt=True)
        with tarfile.open(plain, 'r:gz') as opened:
            return {m.name: opened.extractfile(m).read() for m in opened.getmembers() if m.isfile()}

    def app(self, **config):
        app = object.__new__(assistant.App)
        app.config, app.stop = {'database': str(self.path), **config}, __import__('threading').Event()
        return app

    def test_on_by_default_off_only_when_config_says_so(self):
        self.assertTrue(self.app().protection_now({'database': str(self.path)}))  # a new install
        self.assertTrue(self.app().protection_now({'database': str(self.path), 'message_protection': None}))
        self.assertFalse(self.app().protection_now({'database': str(self.path), 'message_protection': False}))
        self.fill(assistant.Store(self.path))
        app = self.app()
        store = assistant.Store(self.path, protect=app.protection_now(app.config))  # an upgrade, with nothing running
        self.assertTrue(store.protected and message_seal.is_protected(sqlite3.connect(self.path)))
        self.assertNotIn(CANARY.encode(), files(self.path))
        self.assertEqual(oct(message_seal.key_path(self.path).stat().st_mode & 0o777), '0o600')

    def test_an_upgrade_waits_until_nothing_runs_then_restarts_to_seal(self):
        self.fill(assistant.Store(self.path))
        now = assistant.dt.datetime.now(assistant.dt.timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at) VALUES('r1','running',?,?)", (now, now))
        app = self.app()
        self.assertIsNone(app.protection_now(app.config))  # a review runs: it is not cut off
        self.assertTrue(app.protection_waits)
        self.assertFalse(assistant.Store(self.path, protect=None).protected)
        restarts = []
        original = assistant.restart_self
        assistant.restart_self = lambda: restarts.append(1) or True
        try:
            waiter = __import__('threading').Thread(target=app.protection_waiter, kwargs={'every': 0.05})
            waiter.start()
            __import__('time').sleep(0.3)
            self.assertEqual(restarts, [])
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE review_runs SET status='complete'")
            waiter.join(5)
        finally:
            assistant.restart_self = original
        self.assertEqual(restarts, [1])
        self.assertTrue(assistant.Store(self.path, protect=app.protection_now(app.config)).protected)
        self.assertFalse(app.protection_waits)

    def test_the_key_is_checked_by_doctor_and_the_dashboard(self):
        assistant.Store(self.path, protect=True)
        self.assertEqual(message_seal.problems(self.path), [])
        key = message_seal.key_path(self.path)
        key.chmod(0o644)
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-open'])
        self.assertEqual(assistant.needs_you({'database': str(self.path), 'accounts': []})[0]['action'], f'Run: chmod 600 {key}')
        key.rename(key.with_name('elsewhere'))
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-missing'])

    def test_a_key_the_host_keeps_off_the_disk_is_waited_for_and_never_made_anew(self):
        self.fill(assistant.Store(self.path, protect=True))
        key = message_seal.key_path(self.path)
        memory = Path(self.temp.name) / 'run' / message_seal.KEY_NAME  # the host's copy in memory (/run)
        memory.parent.mkdir()
        key.rename(memory)
        key.symlink_to(memory)
        self.assertTrue(message_seal.held_elsewhere(self.path))
        store = assistant.Store(self.path, protect=True)  # read through the link, as before
        self.assertEqual(len(store.events(['acct'], query=CANARY.lower())), len(SOURCES))
        self.assertEqual(message_seal.problems(self.path), [])
        saved = memory.read_bytes()
        memory.unlink()  # a restart: /run is empty until the host puts the key back
        self.assertEqual([p[0] for p in message_seal.problems(self.path)], ['message-key-waiting'])
        with self.assertRaises(message_seal.SealError):
            assistant.Store(self.path, protect=True)
        with self.assertRaises(message_seal.SealError):
            message_seal.load_key(self.path, create=True)
        self.assertFalse(memory.exists())  # no new key that would leave the messages unreadable
        said = []
        self.assertFalse(message_seal.wait_for_key(self.path, say=said.append, every=0.01, limit=0.03))
        self.assertIn('Waiting for the message key', said[0])
        memory.write_bytes(saved)
        self.assertTrue(message_seal.wait_for_key(self.path, say=said.append, every=0.01, limit=0.03))
        self.assertTrue(assistant.Store(self.path).protected)
        import work_removal
        config = Path(self.temp.name) / 'config.json'
        config.write_text(json.dumps({'database': str(self.path), 'accounts': []}))
        folder = work_removal.backup(config, {'database': str(self.path), 'accounts': []})
        self.assertFalse((folder / message_seal.KEY_NAME).exists())  # its own copy would put the key back on the disk

    def test_an_export_moves_over_and_is_sealed_again_with_the_new_machines_key(self):
        config = Path(self.temp.name) / 'config.json'
        config.write_text(json.dumps({'database': str(self.path), 'accounts': []}))
        self.fill(assistant.Store(self.path, protect=True))
        there = Path(self.temp.name) / 'there' / 'events.sqlite'
        there.parent.mkdir()
        there.write_bytes(self.exported(config)['data/events.sqlite'])
        app = self.app(database=str(there))
        store = assistant.Store(there, protect=app.protection_now(app.config))
        self.assertTrue(store.protected)
        self.assertNotEqual(message_seal.key_path(there).read_bytes(), message_seal.key_path(self.path).read_bytes())
        self.assertNotIn(CANARY.encode(), files(there))
        self.assertEqual(len(store.events(['acct'], query=CANARY.lower())), len(SOURCES))

    def test_learnings_hold_what_the_machine_learned_never_message_text(self):
        store = assistant.Store(self.path, protect=True)
        self.fill(store)
        with store.connect() as db:
            db.execute("INSERT INTO decisions VALUES('d','s1','dismiss','Too pushy','dashboard','2026-10-11')")
        workspace = Path(self.temp.name) / 'ws'
        (workspace / 'memory').mkdir(parents=True)
        (workspace / 'USER.md').write_text('# About me\nShort answers.')
        (workspace / 'memory' / 'notes.md').write_text('Likes Fridays off.')
        out = learnings.export(self.path, workspace, {'accounts': [{'id': 'gmail:owner@example.com', 'service': 'gmail'}],
                                                      'credentials': {'api-token': '/secret'}})
        text = json.dumps(out)
        self.assertNotIn(CANARY.lower(), text.lower())
        self.assertNotIn('owner@example.com', text)
        self.assertNotIn('/secret', text)
        self.assertIn('Too pushy', text)
        self.assertIn('Prefers short replies', text)
        self.assertEqual(out['notes']['USER.md'], '# About me\nShort answers.')
        self.assertEqual(out['tables']['suggestions'][0]['status'], 'pending')
        self.assertEqual(out['tables']['action_jobs'], [])
        self.assertEqual(sum(r['n'] for r in out['counts']['signals']), len(SOURCES))
        self.assertEqual(learnings.export(self.path)['tables']['suggestions'][0]['kind'], 'gmail.message')


class NothingOutsideTheSealedFields(unittest.TestCase):
    def test_learnings_never_select_a_sealed_column(self):
        sealed = {('events', c) for c in ('title', 'body', 'metadata')}
        sealed |= {(t, c) for t, cs in message_seal.DERIVED.items() for c in cs}
        sealed |= {(t, c) for t, c, _ in message_seal.DERIVED_JSON}
        for table, columns in learnings.TABLES.items():
            for column in columns:
                self.assertNotIn((table, column), sealed)
                if ' AS ' in column:  # computed: only a JSON column's plain keys, counts, or another table's kind
                    for t, c, plain in message_seal.DERIVED_JSON:
                        if t == table and c in column:
                            self.assertTrue(re.search(r"'\$\.(\w+)'", column)[1] in plain)

    def test_every_text_column_that_could_hold_a_message_is_classified(self):
        """A new table or column whose name says it may hold message text has to be sealed (message_seal.DERIVED) or
        listed here with the reason it is not a message."""
        not_messages = {
            'events.content_hash': 'a hash', 'promise_log.detail': 'an event kind, a source, a date or an id (promise_logged)', 'milestones.detail': 'a count or a first-look state (first_cards_report)', 'facts.text': 'a learned fact', 'rule_changes.text': 'a learned rule',
            'decisions.feedback': "the owner's feedback", 'decision_annotations.reason': 'a status reason',
            'profile_runs.draft': 'profile notes (USER.md)', 'skills.body': 'a skill', 'skills.description': 'a skill',
            'skills.note': 'a skill', 'skill_hints.summary': 'a skill hint', 'tool_approval_proposals.title': 'a proposal',
            'tool_approval_proposals.detail': 'a proposal', 'checkpoints.state': 'a sync cursor (MESSAGE_SEAL_AUDIT checks)',
            'analyses.items': 'ids and versions', 'task_pages.state': "a task page's stage (offered, shared, done)", 'preparations.details': 'why a preparation stopped',
            'preparations.reason': 'a reason code', 'preparations.questions': 'what a stopped run asks the owner',
            'android_inbox.payload': "its text sealed by the Gateway's plugin (message_seal.INBOX); deleted once read",
            'network_outbox.body': 'matched in SQL until sent; sealed end to end in transit (network_crypto)',
            'fleet_log.what': "a log of what was shared, by kind", 'fleet_out.context': 'matched in SQL: an id',
            'fleet_rules.rule': 'a sharing rule', 'fleet_handled.state': 'done or settled', 'fleet_briefs.view': 'counts and dates (fleet brief)',
            'fleet_notices.view': 'a Fleet notice', 'fleet_notices.reason': 'a reason code', 'fleet_splits.title': 'a meeting title',
            'graph_nodes.info': 'people and organisations', 'network_people.reasons': 'why someone is suggested',
            'faults.message': "an error's own message with anything private taken out (assistant.fault_message)",
            'voices.text': 'how the owner writes on a channel, learned like a fact',
            'voice_merges.merged_text': 'the same learned channel voice, retained only for undo',
            'suggestions.title_source': 'who wrote the title: auto or user',
            'skill_clusters.description': 'a skill suggested from learned notes', 'skill_clusters.body': 'a skill suggested from learned notes',
            'board_suggestions.state': 'open, saved or dismissed',
        }
        sealed = {f'{t}.{c}' for t, cs in message_seal.DERIVED.items() for c in cs} | {f'{t}.{c}' for t, c, _ in message_seal.DERIVED_JSON}
        sealed |= {'events.title', 'events.body', 'events.metadata'}
        words = re.compile(r'title|body|subject|text|snippet|question|answer|summary|draft|what|payload|params|result|preview|'
                           r'description|note|content|feedback|reason|detail|view|info|items|context|rule|state|transcript|quote|evidence|message$')
        with tempfile.TemporaryDirectory() as temp:
            store = assistant.Store(Path(temp) / 'events.sqlite')
            import agent_jobs, discovery, fleet, fleet_requests, network, skills
            with store.connect() as db:
                for module in (network, fleet, skills, fleet_requests, discovery, agent_jobs):
                    db.executescript(module.SCHEMA)
                fleet.Fleet.ensure_schema(db) if hasattr(fleet.Fleet, 'ensure_schema') else None
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'event_search%'"):
                    for _, column, kind, *_ in db.execute(f'PRAGMA table_info({table})'):
                        name = f'{table}.{column}'
                        if kind.upper() in ('TEXT', '') and words.search(column) and not column.endswith(('_id', '_at', 'status', '_kind', '_until', '_state', '_hash')):
                            with self.subTest(column=name):
                                self.assertTrue(name in sealed or name in not_messages, f'{name}: seal it or say why it is no message')

    def test_connectors_store_only_through_the_sealed_path(self):
        for path in sorted((ROOT / 'connectors').glob('*.py')) + sorted((ROOT / 'integrations').glob('*.py')):
            text = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r'\bsqlite3\b|INSERT\s+INTO|UPDATE\s+\w+\s+SET')
        writes = [m for p in ROOT.glob('*.py') if not p.name.startswith('test_')
                  for m in re.finditer(r'(INSERT(?: OR \w+)? INTO|UPDATE) events\b', p.read_text()) if p.name != 'assistant.py']
        self.assertEqual(writes, [])

    def test_a_connector_putting_message_text_in_a_plain_field_is_caught(self):
        leaky = event(1, 'new.connector', {'thread_id': 'Dear owner the zanzibarquill contract is ready'})
        self.assertEqual(message_seal.plain_leaks(leaky), ['metadata.thread_id'])
        self.assertEqual(message_seal.plain_leaks(event(1, 'new.connector'), {'cursor': 'abc'}), [])
        self.assertEqual(message_seal.plain_leaks(event(1, 'x'), {'last': 'the zanzibarquill contract is ready'}), ['cursor'])

    def test_every_connector_keeps_message_text_in_the_sealed_fields(self):
        """The connector suites again, with commit_page refusing any event whose text shows in a plain field."""
        suites = ['test_connectors', 'test_new_connectors', 'test_x', 'test_linkedin', 'test_whatsapp', 'test_ms_graph',
                  'test_salesflare', 'test_connector_trello', 'test_github_stripe', 'test_connector_shortcut', 'test_email_bodies',
                  'test_gmail_exclusions', 'test_android_coalescing', 'test_gateway', 'test_ingestion_recovery']
        result = subprocess.run([sys.executable, '-m', 'unittest', *suites], cwd=ROOT, capture_output=True, text=True, timeout=900,
                                env={**os.environ, 'MESSAGE_SEAL_AUDIT': '1'})
        self.assertNotIn('keeps message text in the clear', result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == '__main__':
    unittest.main()
