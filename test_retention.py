import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('assistant', Path(__file__).with_name('assistant.py'))
assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assistant)
import retention  # noqa: E402

NOW = dt.datetime(2026, 10, 24, 3, 30, tzinfo=dt.timezone.utc)
ZONE = ZoneInfo('Europe/Brussels')


def ago(days, hours=0):
    return (NOW - dt.timedelta(days=days, hours=hours)).isoformat()


class Database(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / 'events.sqlite'
        self.store = assistant.Store(self.path)
        self.connect = self.store.connect

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, account, at, status='success', signals=0):
        with self.connect() as db:
            db.execute('INSERT INTO sync_activity VALUES(?,?,?,?,?,?,?)', (os.urandom(8).hex(), account, at, status, signals, None, None))

    def many(self, n):
        with self.connect() as db:
            db.executemany('INSERT INTO sync_activity VALUES(?,?,?,?,?,?,?)',
                           [(os.urandom(8).hex(), 'work-gmail', ago(40), 'success', i, 'x' * 200, None) for i in range(n)])

    def test_sync_history_after_30_days_goes_and_its_daily_counts_stay(self):
        for hours in (1, 2, 3):
            self.sync('work-gmail', ago(40, hours / 10), signals=hours)
        self.sync('work-gmail', ago(40, 0.4), status='failed', signals=None)
        self.sync('work-slack', ago(31), signals=5)
        self.sync('work-gmail', ago(29))
        self.sync('work-gmail', ago(0))
        with mock.patch.object(retention, 'BATCH', 2):  # several small transactions
            self.assertEqual(retention.prune_sync(self.connect, NOW), 5)
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sync_activity').fetchone()[0], 2)
            daily = {(r[0], r[1]): tuple(r[2:]) for r in db.execute('SELECT account_id,day,syncs,failed,signals FROM sync_daily')}
        self.assertEqual(daily, {('work-gmail', ago(40)[:10]): (4, 1, 6), ('work-slack', ago(31)[:10]): (1, 0, 5)})
        self.sync('work-gmail', ago(40, 0.5), signals=2)  # a late row of the same day adds to it
        retention.prune_sync(self.connect, NOW)
        with self.connect() as db:
            self.assertEqual(tuple(db.execute("SELECT syncs,failed,signals FROM sync_daily WHERE account_id='work-gmail'").fetchone()), (5, 1, 8))

    def test_recording_a_sync_no_longer_deletes_and_the_start_date_survives_pruning(self):
        self.sync('work-gmail', ago(45))
        with self.connect() as db:
            self.store.record_sync_activity(db, 'work-gmail', 'success', signals=1)
            self.assertEqual(db.execute('SELECT count(*) FROM sync_activity').fetchone()[0], 2)
        retention.prune_sync(self.connect, NOW)
        app = assistant.App.__new__(assistant.App)
        app.config, app.store = {}, self.store
        self.assertEqual(app.started_at()[:10], ago(45)[:10])

    def preparation(self, suggestion, status, job, started, finished, calls_at):
        with self.connect() as db:
            db.execute("INSERT INTO suggestions(id,dedupe_key,account_ids,event_ids,title,body,status,created_at) "
                       "VALUES(?,?,'[]','[]',?,?,?,?)", (suggestion, 'k-' + suggestion, 'Title', 'Body', status, started))
            db.execute('INSERT INTO preparation_runs(job_id,suggestion_id,session_job_id,status,started_at,finished_at) VALUES(?,?,?,?,?,?)',
                       (job, suggestion, job, 'ok' if finished else 'running', started, finished))
            for n, at in enumerate(calls_at):
                db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,started_at) VALUES(?,?,?,?,?)',
                           (job, f'c{n}', 'browser', '{}', at))

    def test_preparation_logs_after_90_days_go_unless_a_card_or_run_still_needs_them(self):
        self.suggestion_columns()
        self.preparation('done', 'completed', 'job-done', ago(100), ago(99), [ago(100), ago(99, 12)])
        self.preparation('dismissed', 'rejected', 'job-dismissed', ago(95), ago(95), [ago(95)])
        self.preparation('open', 'pending', 'job-open', ago(100), ago(99), [ago(100)])
        self.preparation('running', 'completed', 'job-running', ago(100), None, [ago(100)])
        self.preparation('recent', 'completed', 'job-recent', ago(10), ago(10), [ago(10)])
        self.preparation('ended-late', 'completed', 'job-late', ago(100), ago(50), [ago(100)])
        self.assertEqual(retention.prune_preparations(self.connect, NOW), 3)
        with self.connect() as db:
            left = sorted(r[0] for r in db.execute('SELECT DISTINCT job_id FROM preparation_calls'))
            runs = db.execute('SELECT count(*) FROM preparation_runs').fetchone()[0]
        self.assertEqual(left, ['job-late', 'job-open', 'job-recent', 'job-running'])
        self.assertEqual(runs, 6)  # the runs themselves stay

    def suggestion_columns(self):
        with self.connect() as db:
            self.columns = {r[1]: r for r in db.execute('PRAGMA table_info(suggestions)')}
            required = [name for name, r in self.columns.items() if r[3] and r[4] is None and not r[5]]
        missing = set(required) - {'id', 'dedupe_key', 'account_ids', 'event_ids', 'title', 'body', 'status', 'created_at'}
        self.assertFalse(missing, 'the test inserts suggestions with these columns only')

    def test_profile_evidence_is_cleared_after_90_days_and_the_run_stays(self):
        with self.connect() as db:
            for run, finished, status in (('old', ago(91), 'ok'), ('new', ago(5), 'ok'), ('going', None, 'running')):
                db.execute("INSERT INTO profile_runs(run_id,status,started_at,updated_at,finished_at,evidence,draft) VALUES(?,?,?,?,?,?,?)",
                           (run, status, ago(120), ago(1), finished, 'what it read', '{"ok":1}'))
        self.assertEqual(retention.prune_profile_evidence(self.connect, NOW), 1)
        with self.connect() as db:
            rows = {r[0]: (r[1], r[2]) for r in db.execute('SELECT run_id,evidence IS NULL,draft FROM profile_runs')}
        self.assertEqual(rows, {'old': (1, '{"ok":1}'), 'new': (0, '{"ok":1}'), 'going': (0, '{"ok":1}')})

    def test_space_goes_back_once_with_vacuum_then_in_incremental_steps(self):
        self.many(3000)
        retention.prune_sync(self.connect, NOW)
        wal = Path(str(self.path) + '-wal')
        before = self.path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
        self.assertEqual(retention.give_back_space(self.connect, self.path, busy=1)['vacuum'], 'skipped')
        done = retention.give_back_space(self.connect, self.path, busy=0)
        self.assertEqual(done['vacuum'], 'full')
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA auto_vacuum').fetchone()[0], 2)
            self.assertEqual(db.execute('PRAGMA freelist_count').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')  # still WAL after the VACUUM
        self.assertLess(self.path.stat().st_size, before)
        self.many(3000)
        retention.prune_sync(self.connect, NOW)
        again = retention.give_back_space(self.connect, self.path, busy=1)  # incremental steps need no quiet moment
        self.assertEqual(again['vacuum'], 'incremental')
        self.assertGreater(again['free_pages'], 0)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA freelist_count').fetchone()[0], 0)

    def test_a_too_large_database_waits_for_a_vacuum_rather_than_lock_writers_for_long(self):
        with mock.patch.object(retention, 'VACUUM_MAX_BYTES', 1):
            self.assertEqual(retention.give_back_space(self.connect, self.path, busy=0)['vacuum'], 'skipped')

    def test_runs_once_a_day_at_the_quiet_hour_and_catches_up_on_a_machine_off_at_night(self):
        midday = dt.datetime(2026, 10, 24, 11, 0, tzinfo=dt.timezone.utc)
        self.assertFalse(retention.due(self.connect, midday, ZONE))  # first check after an update: noted, not run
        self.assertTrue(retention.due(self.connect, dt.datetime(2026, 10, 25, 2, 0, tzinfo=dt.timezone.utc), ZONE))  # 03:00 local
        self.assertTrue(retention.due(self.connect, midday + dt.timedelta(hours=73), ZONE))  # never on at night
        report = retention.run(self.connect, str(self.path), now=NOW, busy=0, local_tz=ZONE)
        self.assertEqual(report['sync_activity'], 0)
        self.assertFalse(retention.due(self.connect, NOW + dt.timedelta(hours=1), ZONE))
        self.assertFalse(retention.due(self.connect, NOW + dt.timedelta(hours=12), ZONE))  # 17:30, not the quiet hour
        self.assertTrue(retention.due(self.connect, NOW + dt.timedelta(days=1), ZONE))
        self.assertTrue(retention.due(self.connect, NOW + dt.timedelta(hours=80), ZONE))

    def test_the_whole_run_reports_what_it_did_and_rotates_copies_next_to_the_database(self):
        self.sync('work-gmail', ago(31))
        old = self.dir / 'events.sqlite.bak-old'
        sqlite_file(old, NOW - dt.timedelta(days=40))
        report = retention.run(self.connect, str(self.path), now=NOW, busy=0, local_tz=ZONE)
        self.assertEqual(report['sync_activity'], 1)
        self.assertEqual(report['backups']['removed'], 1)
        self.assertFalse(old.exists())
        self.assertTrue(self.path.exists())
        with self.connect() as db:
            saved = json.loads(db.execute('SELECT report FROM retention_runs ORDER BY at DESC LIMIT 1').fetchone()[0])
        self.assertEqual(saved['sync_activity'], 1)


def sqlite_file(path, when, events=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE events(id)' if events else 'CREATE TABLE other(id)')
        if events:
            db.execute('CREATE TABLE suggestions(id)')
        db.commit()
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


class Backups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.live = sqlite_file(self.dir / 'events.sqlite', NOW - dt.timedelta(days=60))

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, days, hours=0):
        return NOW - dt.timedelta(days=days, hours=hours)

    def make(self):
        d = self.dir
        files = {
            'recent-1': sqlite_file(d / 'backups-manual' / 'events-before-a.sqlite', self.at(1)),
            'recent-2': sqlite_file(d / 'backups-manual' / 'events-before-b.sqlite', self.at(1, 1)),
            'recent-3': sqlite_file(d / 'events.sqlite.bak-port-1', self.at(6)),
            'day10-new': sqlite_file(d / 'backups' / 'events-before-port-2.sqlite', self.at(10)),
            'day10-old': sqlite_file(d / 'backups' / 'events-before-port-1.sqlite', self.at(10, 0.2)),
            'day10-other-db': sqlite_file(d / 'backups-manual' / 'assistant-before-x.sqlite', self.at(10, 0.3), events=False),
            'day10-sibling': sqlite_file(d / 'events.sqlite.bak-port-0', self.at(10, 0.4)),
            'day20': sqlite_file(d / 'repair-backups' / 'run' / 'pre-release.sqlite', self.at(20)),
            'day40': sqlite_file(d / 'deployment-backup' / 'port-x' / 'events.sqlite', self.at(40)),
            'day40-sibling': sqlite_file(d / 'events.sqlite.bak-old', self.at(40)),
        }
        for end in ('-wal', '-shm'):
            Path(str(files['day10-sibling']) + end).write_bytes(b'x')
        self.untouched = [
            d / 'deployment-backup' / 'port-x' / 'config.json',  # not a database copy
            d / 'backups-manual' / 'notes.sqlite',  # named like one, isn't one
            d / 'work-removal-backups' / 'old' / 'events.sqlite',  # its own 7-day rule
            d / 'agents-md-backups' / 'old.sqlite',
            d / '.events.sqlite.before-protection.d' / 'events.sqlite.before-protection.sealed',
            d / 'events.sqlite.before-protection',  # message protection's own copy
            d / 'old-results' / 'events.sqlite',  # not a backup folder
            d / 'config.json.bak',
        ]
        for path in self.untouched:
            if path.name.endswith('.sqlite') and path.name != 'notes.sqlite' or path.name.endswith('before-protection'):
                sqlite_file(path, self.at(90))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                os.utime(path, (self.at(90).timestamp(),) * 2)
        return files

    def test_all_of_the_last_week_then_one_per_day_and_database_up_to_30_days(self):
        files = self.make()
        result = retention.rotate_backups(self.dir, NOW, live=[self.live], local_tz=ZONE)
        gone = {k for k, p in files.items() if not p.exists()}
        self.assertEqual(gone, {'day10-old', 'day10-sibling', 'day40', 'day40-sibling'})
        self.assertEqual(result['removed'], 4)
        self.assertEqual(result['kept'], 6)
        self.assertGreater(result['freed_bytes'], 0)
        self.assertFalse(Path(str(files['day10-sibling']) + '-wal').exists())  # its companions go with it
        self.assertFalse(Path(str(files['day10-sibling']) + '-shm').exists())
        for path in self.untouched + [self.live]:
            self.assertTrue(path.exists(), path)
        self.assertTrue((self.dir / 'deployment-backup' / 'port-x').is_dir())  # still holds the config copy
        self.assertTrue((self.dir / 'backups').is_dir())

    def test_a_folder_the_rotation_emptied_goes_but_never_the_backup_folder(self):
        files = self.make()
        (self.dir / 'deployment-backup' / 'port-x' / 'config.json').unlink()
        self.untouched.remove(self.dir / 'deployment-backup' / 'port-x' / 'config.json')
        retention.rotate_backups(self.dir, NOW, live=[self.live], local_tz=ZONE)
        self.assertFalse(files['day40'].parent.exists())
        self.assertTrue((self.dir / 'deployment-backup').is_dir())

    def test_dry_run_and_the_command_for_port_scripts(self):
        files = self.make()
        result = retention.rotate_backups(self.dir, NOW, live=[self.live], dry_run=True, local_tz=ZONE)
        self.assertEqual(result['removed'], 4)
        self.assertTrue(all(p.exists() for p in files.values()))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(retention, 'utc_now', return_value=NOW):
            self.assertEqual(retention.main(['rotate-backups', '--data-dir', str(self.dir), '--list']), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed['removed'], 4)
        self.assertIn('events.sqlite.bak-old', printed['removed_files'])
        self.assertTrue(self.live.exists())
        self.assertFalse(files['day40'].exists())

    def test_the_live_database_is_never_a_copy_even_in_a_backup_folder(self):
        live = sqlite_file(self.dir / 'backups' / 'events.sqlite', self.at(50))
        self.assertEqual(retention.rotate_backups(self.dir, NOW, live=[live])['removed'], 0)
        self.assertTrue(live.exists())


if __name__ == '__main__':
    unittest.main()
