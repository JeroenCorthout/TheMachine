"""Work hours (`work_hours`): one setting for work days, the hours cards may go out, when snoozes land,
the digest and the time zone. The defaults are exactly the old fixed behavior."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ux as ux

a = ux.a
BXL = a.ZoneInfo('Europe/Amsterdam')
NYC = a.ZoneInfo('America/New_York')


def at(y, m, d, h, mi=0, zone=BXL):
    return dt.datetime(y, m, d, h, mi, tzinfo=zone).astimezone(dt.timezone.utc).isoformat()


class Hours(unittest.TestCase):
    def setUp(self):
        a.set_timezone('Europe/Amsterdam')
        a.set_work_hours(None)
        self.addCleanup(a.set_work_hours, None)
        self.addCleanup(a.set_timezone, 'Europe/Amsterdam')

    def test_defaults_keep_the_old_fixed_hours(self):
        self.assertEqual(a.work_hours(), {'days': [1, 2, 3, 4, 5], 'start': '08:00', 'end': '23:00', 'morning': '09:00'})
        self.assertEqual(a.work_hours({}), a.WORK_HOURS_DEFAULT)
        local = lambda h, mi=0: dt.datetime(2026, 10, 14, h, mi, tzinfo=BXL)
        self.assertEqual([a.in_hours(local(h, m)) for h, m in [(7, 59), (8, 0), (22, 59), (23, 0), (2, 0)]], [False, True, True, False, False])
        with patch.object(a, 'now', return_value=at(2026, 10, 9, 15)):  # Friday
            self.assertEqual(a.snooze_time('tomorrow', work=True), at(2026, 10, 12, 9))
            self.assertEqual(a.snooze_time('tomorrow'), at(2026, 10, 10, 9))
            self.assertEqual(a.thread_return_time(1), at(2026, 10, 12, 9))
            self.assertEqual(a.snooze_choices(True)[1], ['tomorrow', 'Monday · 9:00'])
        saturday = dt.datetime(2026, 10, 10, 16, tzinfo=BXL)
        self.assertEqual(a.delivery_time('now', current=saturday, work=True), at(2026, 10, 12, 8, 30))

    def test_days_parse_names_ranges_and_numbers(self):
        self.assertEqual(a.work_hours({'days': 'mon-fri'})['days'], [1, 2, 3, 4, 5])
        self.assertEqual(a.work_hours({'days': 'sun-thu'})['days'], [1, 2, 3, 4, 7])
        self.assertEqual(a.work_hours({'days': 'Mon, Wed,friday'})['days'], [1, 3, 5])
        self.assertEqual(a.work_hours({'days': [6, 7, 6]})['days'], [6, 7])
        self.assertEqual(a.work_hours({'start': '7:05'})['start'], '07:05')
        for bad in ({'days': []}, {'days': [0]}, {'days': 'funday'}, {'days': [True]}, {'start': '25:00'}, {'morning': 'nine'}, []):
            with self.assertRaises(ValueError):
                a.work_hours(bad)

    def test_custom_week_and_hours_move_everything(self):
        a.set_timezone('America/New_York')
        a.set_work_hours({'days': 'sun-thu', 'start': '09:00', 'end': '17:30', 'morning': '08:15'})
        with patch.object(a, 'now', return_value=at(2026, 10, 15, 15, zone=NYC)):  # Thursday
            self.assertEqual(a.snooze_time('tomorrow', work=True), at(2026, 10, 18, 8, 15, NYC))  # Friday and Saturday off
            self.assertEqual(a.snooze_time('tomorrow'), at(2026, 10, 16, 8, 15, NYC))
            self.assertEqual(a.thread_return_time(1), at(2026, 10, 18, 8, 15, NYC))
            self.assertEqual(a.snooze_choices(True)[1], ['tomorrow', 'Sunday · 8:15'])
        friday = dt.datetime(2026, 10, 16, 10, tzinfo=NYC)
        self.assertEqual(a.delivery_time('now', current=friday, work=True), at(2026, 10, 18, 8, 30, NYC))
        self.assertIsNone(a.delivery_time('now', current=dt.datetime(2026, 10, 18, 10, tzinfo=NYC), work=True))  # Sunday works
        self.assertIsNone(a.delivery_time('now', current=friday, work=False))
        self.assertTrue(a.in_hours(dt.datetime(2026, 10, 16, 9, tzinfo=NYC)))
        self.assertFalse(a.in_hours(dt.datetime(2026, 10, 16, 17, 30, tzinfo=NYC)))

    def test_hours_may_wrap_past_midnight_and_equal_means_always(self):
        a.set_work_hours({'start': '20:00', 'end': '04:00'})
        local = lambda h: dt.datetime(2026, 10, 14, h, tzinfo=BXL)
        self.assertEqual([a.in_hours(local(h)) for h in (19, 20, 23, 0, 3, 4, 12)], [False, True, True, True, True, False, False])
        a.set_work_hours({'start': '00:00', 'end': '00:00'})
        self.assertTrue(all(a.in_hours(local(h)) for h in range(24)))


class Saving(unittest.TestCase):
    setUp = ux.UX.setUp

    def app(self, path):
        app = object.__new__(a.App)
        app.store, app.config_path = self.store, path
        app.config = json.loads(path.read_text()) if path else {}
        app.config.setdefault('accounts', [])
        return app

    def config_file(self, extra=None):
        path = Path(self.tmp.name if hasattr(self, 'tmp') else tempfile.mkdtemp()) / 'config.json'
        path.write_text(json.dumps({'database': 'x', 'accounts': [], 'digest_at': '08:30', 'other': {'kept': True}, **(extra or {})}, indent=2) + '\n')
        path.chmod(0o600)
        return path

    def tearDown(self):
        a.set_work_hours(None)
        a.set_timezone('Europe/Amsterdam')

    def test_owner_saves_on_the_dashboard_into_config_json(self):
        path = self.config_file()
        app = self.app(path)
        with self.assertRaises(ValueError):
            app.action('set_work_hours', {'days': [1]})  # not the owner's dashboard
        self.assertIn('set_work_hours', a.OWNER_ACTIONS)  # the local CLI's service call refuses it too
        saved = app.action('set_work_hours', {'days': [1, 2, 3, 4], 'start': '09:00', 'end': '18:00', 'morning': '09:30',
                                              'digest_at': '9:00', 'timezone': 'Europe/London'}, owner_dashboard=True)
        self.assertEqual(saved, {'days': [1, 2, 3, 4], 'start': '09:00', 'end': '18:00', 'morning': '09:30', 'timezone': 'Europe/London',
                                 'digest_at': '09:00', 'saveable': True})
        written = json.loads(path.read_text())
        self.assertEqual(written['work_hours'], {'days': [1, 2, 3, 4], 'start': '09:00', 'end': '18:00', 'morning': '09:30'})
        self.assertEqual((written['timezone'], written['digest_at'], written['other']), ('Europe/London', '09:00', {'kept': True}))
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(json.loads(path.with_name('config.json.bak').read_text())['digest_at'], '08:30')
        self.assertEqual((a.WORK_HOURS['days'], a.local_tz().key, app.config['digest_at']), ([1, 2, 3, 4], 'Europe/London', '09:00'))
        self.assertEqual(app.action('status', {'full': True})['hours']['morning'], '09:30')

    def test_bad_values_change_nothing(self):
        path = self.config_file()
        before = path.read_text()
        app = self.app(path)
        for bad in ({'days': []}, {'start': '8'}, {'timezone': 'Mars/Olympus'}, {'digest_at': '30:00'}):
            with self.assertRaises(ValueError):
                app.action('set_work_hours', bad, owner_dashboard=True)
        self.assertEqual(path.read_text(), before)
        self.assertEqual(a.WORK_HOURS, a.WORK_HOURS_DEFAULT)

    def test_cards_wait_for_the_hours(self):
        a.set_work_hours({'start': '09:00', 'end': '17:00'})

        def deliver(hour):
            class Clock(a.dt.datetime):
                @classmethod
                def now(cls, tz=None): return cls(2026, 10, 14, hour, 0, tzinfo=BXL).astimezone(tz or a.dt.timezone.utc)
            with patch.object(a.dt, 'datetime', Clock), patch.object(a, 'now', return_value=at(2026, 10, 14, hour)), \
                    patch.object(a, 'telegram', return_value={'messageId': '7'}) as send:
                a.deliver_one(self.store, {'telegram_owner': '1000001', 'accounts': []}, edit_cards=False)
            return [call.args[2] for call in send.call_args_list]
        self.assertEqual(deliver(18), [])  # 08:00-23:00 would have sent it
        self.assertEqual(deliver(8), [])
        self.assertEqual(deliver(10), ['send'])

    def test_without_a_config_file_it_says_so(self):
        app = self.app(None)
        self.assertFalse(app.action('status', {'full': True})['hours']['saveable'])
        with self.assertRaises(ValueError):
            app.action('set_work_hours', {'days': [1]}, owner_dashboard=True)

    def test_a_broken_setting_falls_back_to_the_defaults(self):
        with patch.object(a, 'Store'), patch.object(a, 'credential'), patch.object(a, 'owner_credential'), patch.object(a.fleet, 'settings', return_value=None):
            a.App({'database': 'x', 'accounts': [], 'work_hours': {'days': []}})
        self.assertEqual(a.WORK_HOURS, a.WORK_HOURS_DEFAULT)


class Cli(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('hours_cli', Path(__file__).with_name('cli.py'))
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'config.json'
        self.path.write_text(json.dumps({'database': 'x', 'accounts': [], 'timezone': 'Europe/Brussels'}, indent=2) + '\n')

    def run_cli(self, *argv):
        out = io.StringIO()
        with patch.object(self.cli.onboarding, 'run') as run, contextlib.redirect_stdout(out):
            self.cli.main(['--config', str(self.path), *argv])
        return out.getvalue(), run

    def test_shows_and_sets_hours(self):
        out, run = self.run_cli('hours')
        self.assertIn('Work days: Mon, Tue, Wed, Thu, Fri', out)
        self.assertIn('Hours: 08:00–23:00', out)
        run.assert_not_called()
        self.assertNotIn('work_hours', json.loads(self.path.read_text()))
        out, run = self.run_cli('hours', '--days', 'sun-thu', '--start', '7:30', '--end', '16:00', '--json')
        self.assertEqual(json.loads(out)['days'], [1, 2, 3, 4, 7])
        self.assertEqual(json.loads(self.path.read_text())['work_hours'], {'days': [1, 2, 3, 4, 7], 'start': '07:30', 'end': '16:00', 'morning': '09:00'})
        if self.cli.assistant.shutil.which('systemctl'):
            self.assertEqual(run.call_args[0][0], ['systemctl', '--user', 'restart', 'personal-assistant.service'])
        out, run = self.run_cli('hours', '--morning', '08:45', '--no-restart')
        run.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text())['work_hours']['days'], [1, 2, 3, 4, 7])  # kept

    def test_bad_input_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cli('hours', '--days', 'someday')
        self.assertNotIn('work_hours', json.loads(self.path.read_text()))


if __name__ == '__main__':
    unittest.main()
