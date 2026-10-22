"""The quick first look and what the health report says about the way to the first card. On a new install with reviews
off, a quick first look over the newest signals starts as soon as the first page of mail is in; the full first look of
the week follows once the import settled, without reviewing (or carding) the same signals again. Failed tries come
back within minutes. The report holds timestamps, counts and codes only. No Gateway, nothing is sent."""
import datetime as dt, importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fast_first_look_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)


class Runner:
    def __init__(self, *results): self.results = list(results); self.requests = []
    def __call__(self, request): self.requests.append(request); return {'status': self.results.pop(0)}


def signal(source_id, account='work', hours_ago=2, title='Pricing question'):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat()
    return {'account_id': account, 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': when, 'title': title,
            'body': 'Can you confirm?', 'metadata': {'from': 'Sam Example <sam@example.com>'}, 'deleted': False}


class FastFirstLook(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'gateway', 'service': 'gateway'}, {'id': 'work', 'service': 'gmail'}],
                           'review_worker': {'enabled': False}, 'delivery_enabled': False}
        model = patch.object(a, 'has_model', return_value=True); model.start(); self.addCleanup(model.stop)

    def later(self, minutes):
        return patch.object(a, 'now', return_value=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat(timespec='seconds'))

    def state(self, minutes=0):
        with self.later(minutes), self.store.connect() as db:
            return self.app.first_look(db)

    def step(self, runner, minutes=0):
        with self.later(minutes):
            return self.app.review_step(runner)

    def runs(self):
        with self.store.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM review_runs ORDER BY started_at')]

    def finish(self, status='ok', error=None, minutes=0):
        with self.later(minutes), self.store.connect() as db:
            db.execute('UPDATE review_runs SET status=?,error=?,finished_at=? WHERE finished_at IS NULL', (status, error, a.now()))

    def claim(self, run_id):
        """What the review run gets to see, all of it marked reviewed (as a finished review does)."""
        seen = []
        while True:
            claim = self.store.begin_analysis(['work'], 40, a.REVIEW_SESSION_PREFIX + run_id, run_id)
            if not claim.get('analysis_id'):
                return seen
            ids = [e['id'] for e in claim['events']]
            seen += ids
            with self.store.connect() as db:
                db.execute('UPDATE events SET analyzed_version=version WHERE id IN (' + ','.join('?' * len(ids)) + ')', ids)
                db.execute("UPDATE analyses SET status='complete',finished_at=? WHERE id=?", (a.now(), claim['analysis_id']))

    def test_a_quick_look_at_the_newest_mail_starts_with_the_first_page_then_the_full_week_without_repeats(self):
        bootstrap = {'mode': 'bootstrap', 'base_history': '1', 'after': 0}
        self.store.commit_page('work', bootstrap, [], record_sync=False)
        self.assertEqual(self.state()['why'], 'mail')  # not even the first page yet
        self.store.commit_page('work', {**bootstrap, 'page_token': 'p2'}, [signal(f'new{n}') for n in range(30)])
        self.assertEqual(self.state(), {'state': 'due', 'quick': True, 'waiting': a.QUICK_LOOK_SIGNALS, 'hours': 48, 'days': 7})
        self.assertEqual(self.step(Runner('admitted')), 'admitted')  # at once: no waiting for the rest of the mail
        run = self.runs()[0]
        self.assertEqual((run['preview'], run['auto'], run['window_hours'], run['budget'], run['planned']), (1, 1, 48, 20, 20))
        quick = self.claim(run['run_id'])
        self.assertEqual(len(quick), a.QUICK_LOOK_SIGNALS)  # bounded
        self.assertEqual(self.step(Runner('ok'), 3), 'ok')
        # The rest of the mail, older too, comes in; the full look waits for it, then starts at once
        self.store.commit_page('work', {**bootstrap, 'page_token': 'p3'}, [signal(f'old{n}', hours_ago=24 * 4) for n in range(10)])
        self.assertEqual(self.state(4)['why'], 'mail')
        self.store.commit_page('work', {'mode': 'incremental', 'history_id': '2', 'after': 0, 'backfilled_at': a.now()}, [])
        self.assertEqual(self.state(5), {'state': 'due', 'waiting': 20, 'days': 7})
        self.assertEqual(self.step(Runner('admitted'), 5), 'admitted')
        full = self.runs()[1]
        self.assertEqual((full['window_hours'], full['budget']), (None, None))
        rest = self.claim(full['run_id'])
        self.assertEqual(len(rest), 20)
        self.assertFalse(set(quick) & set(rest))  # nothing reviewed twice, so no card twice
        self.assertEqual(self.step(Runner('ok'), 9), 'ok')
        self.assertIsNone(self.state(10))

    def test_after_a_quick_look_that_left_nothing_the_first_look_is_done(self):
        self.store.commit_page('work', {}, [signal('m1')])
        self.step(Runner('admitted'))
        self.claim(self.runs()[0]['run_id'])
        self.finish()
        self.assertIsNone(self.state(1))

    def test_failed_tries_come_back_within_minutes_doubling_and_a_rate_limit_waits_longer(self):
        self.store.commit_page('work', {}, [signal('m1')])
        self.step(Runner('admitted'))
        self.finish('error', 'runtime_error')
        self.assertEqual(self.state(1)['why'], 'retry')
        self.assertEqual(self.state(3)['state'], 'due')  # 2 minutes after the first failure
        self.step(Runner('admitted'), 3)
        self.finish('error', 'runtime_error', minutes=3)
        self.assertEqual(self.state(6)['why'], 'retry')
        self.assertEqual(self.state(8)['state'], 'due')  # then 4
        self.step(Runner('admitted'), 8)
        self.finish('error', 'rate_limited', minutes=8)
        # Three quick tries: the full look takes over, and after a rate limit it waits the long wait
        look = self.state(20)
        self.assertEqual((look.get('quick'), look['why']), (None, 'retry'))
        self.assertEqual(self.state(40)['state'], 'due')

    def test_the_report_has_timestamps_counts_and_codes_only(self):
        accounts = self.app.config['accounts']
        with self.store.connect() as db:
            self.assertEqual(a.first_cards_report(db, accounts)['sources'], [{'service': 'gmail', 'first_page_at': None, 'first_import_at': None}])
        bootstrap = {'mode': 'bootstrap', 'base_history': '1', 'after': 0}
        self.store.commit_page('work', bootstrap, [], record_sync=False)
        self.store.commit_page('work', {**bootstrap, 'page_token': 'p2'}, [signal(f'm{n}', title='Secret pricing words') for n in range(3)])
        with self.later(0):
            self.step(Runner('admitted'))
        with self.store.connect() as db:
            report = a.first_cards_report(db, accounts)
        self.assertEqual((report['look']['state'], report['look']['quick'], report['look']['planned']), ('running', True, 3))
        self.assertEqual(report['eta_minutes'], 1)
        self.store.commit_page('work', {'mode': 'incremental', 'history_id': '2', 'after': 0, 'backfilled_at': a.now()}, [])
        card = self.store.suggest('pricing', 'Reply to Sam about pricing', 'Sam asked.', [1])
        self.store.note_card_shown()
        self.finish('ok', minutes=1)
        self.store.decide(card['id'], 'reject', 'Not useful', 'd1', 'dashboard')
        with self.store.connect() as db:
            report = a.first_cards_report(db, accounts)
        source = report['sources'][0]
        self.assertTrue(source['first_page_at'] and source['first_import_at'])
        self.assertEqual((report['quick']['status'], report['quick']['cards'], report['quick']['tries']), ('ok', 1, 1))
        self.assertNotIn('full', report)
        self.assertEqual((report['cards'], report['first_action']), (1, 'dismiss'))
        self.assertTrue(report['first_card_at'] and report['first_card_shown_at'] and report['first_action_at'])
        self.assertNotIn('eta_minutes', report)  # cards are there
        text = json.dumps(report)
        for secret in ('Secret', 'pricing', 'Sam', 'example.com', 'work'):
            self.assertNotIn(secret, text)

    def test_an_install_with_cards_from_before_keeps_no_first_card_milestones(self):
        self.store.commit_page('work', {}, [signal('m1', hours_ago=24 * 30)])
        card = self.store.suggest('old', 'Reply to Sam', 'Sam asked.', [1])
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET created_at='2026-01-01T00:00:00+00:00'")
            db.execute("UPDATE milestones SET at='2026-10-01T00:00:00+00:00' WHERE name='tracking_since'")
        self.store.note_card_shown()
        self.store.decide(card['id'], 'complete', '', 'd1', 'dashboard')
        with self.store.connect() as db:
            db.execute("UPDATE decisions SET created_at='2026-02-01T00:00:00+00:00'")
            report = a.first_cards_report(db, [])
        self.assertEqual((report['first_card_at'], report['first_card_shown_at'], report['first_action']), (None, None, None))
        # A source past its first import before this was recorded keeps none either
        other = a.Store(Path(self.temp.name) / 'second.sqlite')
        other.commit_page('home', {'mode': 'incremental'}, [], record_sync=False)
        other.commit_page('home', {'mode': 'incremental', 'history_id': '3'}, [])
        with other.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM milestones WHERE name LIKE 'first_%'").fetchone())


    def test_once_reviews_are_on_the_first_review_is_a_quick_one_at_once_then_the_rest(self):
        self.app.config.update(review_worker={'enabled': True}, delivery_enabled=True)
        bootstrap = {'mode': 'bootstrap', 'base_history': '1', 'after': 0}
        self.store.commit_page('work', bootstrap, [signal(f'new{n}') for n in range(25)] + [signal(f'old{n}', hours_ago=24 * 4) for n in range(5)])
        self.assertEqual(self.step(Runner('admitted')), 'admitted')  # mail still coming in, only 30 signals: still at once
        run = self.runs()[0]
        self.assertEqual((run['preview'], run['auto'], run['window_hours'], run['budget'], run['planned']), (0, 1, 48, 20, 20))
        quick = self.claim(run['run_id'])
        self.assertEqual(len(quick), 20)
        with self.store.connect() as db:
            self.assertFalse({e for (e,) in db.execute("SELECT id FROM events WHERE source_id LIKE 'old%'")} & set(quick))  # the newest only
        self.assertEqual(self.step(Runner('ok'), 2), 'ok')
        with self.store.connect() as db:
            report = a.first_cards_report(db, self.app.config['accounts'])
            self.assertEqual((report['quick']['status'], report['quick']['planned']), ('ok', 20))
        self.store.commit_page('work', {'mode': 'incremental', 'history_id': '2', 'after': 0, 'backfilled_at': a.now()}, [])
        self.assertEqual(self.step(Runner('admitted'), 4), 'admitted')  # the rest follows right away, as a regular review
        rest = self.runs()[1]
        self.assertEqual((rest['window_hours'], rest['budget']), (None, None))
        self.assertFalse(set(quick) & set(self.claim(rest['run_id'])))
        self.assertEqual(self.step(Runner('ok'), 6), 'ok')
        with self.store.connect() as db:
            report = a.first_cards_report(db, self.app.config['accounts'])
            self.assertEqual(report['full']['status'], 'ok')
            self.assertEqual(self.app.quick_first_review(db), 0)  # once

    def test_an_install_that_reviewed_before_gets_no_quick_review(self):
        self.app.config.update(review_worker={'enabled': True}, delivery_enabled=True)
        with self.store.connect() as db:
            db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at,finished_at) VALUES('r0','ok',?,?,?)", ('2026-01-01T00:00:00+00:00',) * 3)
        self.store.commit_page('work', {}, [signal(f'm{n}') for n in range(5)])
        with self.store.connect() as db:
            self.assertEqual(self.app.quick_first_review(db), 0)
        self.step(Runner('admitted'))
        self.assertTrue(all(r['window_hours'] is None for r in self.runs()))


if __name__ == '__main__':
    unittest.main()
