"""A Change note that asks for the card itself back later snoozes the card (the Snooze button's own snooze),
instead of the run adding a separate reminder that the card doesn't show."""
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('snooze_ask_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
BXL = a.ZoneInfo('Europe/Brussels')


def at(y, m, d, h, mi=0):
    return a.dt.datetime(y, m, d, h, mi, tzinfo=BXL).astimezone(a.dt.timezone.utc).isoformat()


class SnoozeFromChange(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        zone = a.local_tz()
        a.set_timezone('Europe/Brussels')
        self.addCleanup(lambda: setattr(a, 'LOCAL_TZ', zone))
        self.clock = patch.object(a, 'now', return_value=at(2026, 10, 22, 10, 6))  # Thursday, summer time
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'work', 'service': 'gmail', 'transport': 'gog', 'email': 'owner@example.com', 'boundary': 'work:Example'}]}
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'New build', 'body': 'Please test the new build and send feedback.', 'deleted': False,
                                             'metadata': {'from': 'Sam <sam@example.com>', 'thread_id': 't1', 'labels': ['INBOX']}}])
        self.sid = self.store.suggest('task', 'Follow up: Test further and send feedback', 'Test it and send Sam feedback.', [1])['id']
        self.store.decide(self.sid, 'other', 'I will test it the coming weeks. Remind me about sending this feedback next week Tuesday?', 'n1', 'dashboard')

    def run_job(self):
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running'")
            return db.execute('SELECT job_id FROM preparations').fetchone()[0]

    def row(self):
        return dict(self.store.snapshot(self.app.config['accounts'])['suggestions'][0])

    def test_remind_me_about_this_card_snoozes_it(self):
        job = self.run_job()
        saved = self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': job, 'body': 'Back on Tuesday morning.',
                                            'outcome': 'snoozed', 'snooze_until': '2026-10-27T09:00', 'note_kind': 'new_ask'})
        # 09:00 in the owner's zone after the clock change (CET), not the UTC offset of today.
        self.assertEqual(saved['status'], 'snoozed')
        self.assertEqual(saved['snoozed_until'], '2026-10-27T08:00:00+00:00')
        # The run tells them how it comes back, as delivery does it: no Telegram and no phone here, so the dashboard.
        self.assertTrue(saved['note'].endswith('in these words: It’ll be back on the dashboard then.'))
        row = self.row()
        self.assertEqual((row['status'], row['snoozed_until']), ('snoozed', '2026-10-27T08:00:00+00:00'))
        self.assertTrue(a.card(row).endswith('Snoozed until Tue 27 Oct, 09:00.'))
        self.assertNotIn('Mark it done', a.card(row))
        with self.store.connect() as db:
            # The same decision the Snooze button records; no notification goes out now.
            self.assertEqual(db.execute("SELECT choice,source FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()[:], ('snooze', 'assistant'))
            self.assertIsNone(db.execute("SELECT 1 FROM deliveries WHERE suggestion_id=? AND status='queued' AND reminder=1", (self.sid,)).fetchone())
        # A retried identical save is a no-op.
        again = self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': job, 'body': 'Back on Tuesday morning.',
                                            'outcome': 'snoozed', 'snooze_until': '2026-10-27T09:00', 'note_kind': 'new_ask'})
        self.assertTrue(again['replayed'])
        # It comes back on Tuesday as it was, ready, saying it is back as asked.
        with patch.object(a, 'now', return_value=at(2026, 10, 27, 9, 1)):
            self.assertEqual(self.store.wake_snoozed(), 1)
        row = self.row()
        self.assertEqual(row['status'], 'prepared')
        self.assertIn('Back, as you asked.', a.card(row))

    def test_a_date_alone_lands_in_the_morning_and_offsets_are_kept(self):
        self.assertEqual(a.asked_snooze_time('2026-10-27'), '2026-10-27T08:00:00+00:00')
        self.assertEqual(a.asked_snooze_time('2026-10-27T09:00:00+01:00'), '2026-10-27T08:00:00+00:00')
        for bad in ('next tuesday', '', '2026-10-21T09:00', '2028-01-01'):
            with self.assertRaises(ValueError):
                a.asked_snooze_time(bad)

    def test_bad_time_saves_nothing(self):
        with self.assertRaises(ValueError):
            self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': self.run_job(), 'body': 'Later.',
                                        'outcome': 'snoozed', 'snooze_until': 'someday', 'note_kind': 'new_ask'})
        self.assertEqual(self.row()['status'], 'instructions')

    def test_the_run_is_told_a_reminder_about_the_card_is_its_snooze(self):
        job = {'job_id': 'a' * 32, 'suggestion_id': self.sid}
        prompt = a.preparation_prompt(job)
        self.assertIn("outcome:'snoozed'", prompt)
        self.assertIn('Do not also add an automation', prompt)
        self.assertIn('Europe/Brussels', prompt)
        self.assertIn('Thu 2026-10-22 10:06', prompt)
        self.assertIn("outcome:'snoozed'", (Path(__file__).with_name('PREPARATION.md')).read_text())


class DashboardSnoozedLine(unittest.TestCase):
    def test_dashboard_says_until_when(self):
        html = Path(__file__).with_name('dashboard.html').read_text()
        self.assertIn("return line('Snoozed until '+snoozedUntil(s.snoozed_until))", html)
        script = ("const DL='en-US';const snoozedUntil=" + html.split('const snoozedUntil=', 1)[1].split('\n', 1)[0]
                  + "\nconsole.log(snoozedUntil('2026-10-27T08:00:00+00:00'))")
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        out = subprocess.run([node, '-e', script], capture_output=True, text=True, env={**os.environ, 'TZ': 'Europe/Brussels'})
        self.assertEqual(out.stdout.strip(), 'Tue, Oct 27 at 09:00')


if __name__ == '__main__':
    unittest.main()
