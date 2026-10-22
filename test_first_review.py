"""The first review of a new mailbox: it starts as soon as the mailbox's first import is in, not at the next batch slot,
and as one review (none halfway through the import). The same after connecting another mailbox. Locks, the gap between
runs and failure back-off stay as they are. No Gateway, no mail provider: the runner and the mailbox are fakes."""
import datetime as dt, importlib.util, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('first_review_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)


class Runner:
    def __init__(self, *results): self.results = list(results); self.requests = []
    def __call__(self, request): self.requests.append(request); return {'status': self.results.pop(0)}


def signal(source_id, account='work', days_ago=1, kind='gmail.message'):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
    return {'account_id': account, 'source_id': source_id, 'kind': kind, 'occurred_at': when, 'title': 'Pricing question',
            'body': 'Can you confirm?', 'metadata': {'from': 'Sam Example <sam@example.com>'}, 'deleted': False}


class Mailbox:
    """A mailbox with pages of message ids, read the way sync_gmail reads Gmail (and Outlook through it)."""
    pages = []

    def __init__(self, config, account): self.account = account
    def profile(self): return {'emailAddress': 'owner@example.com', 'historyId': '100'}

    def get(self, resource, params=None):
        if resource == 'messages':
            number = int(params.get('pageToken') or 0)
            out = {'messages': [{'id': i} for i in self.pages[number]]}
            if number + 1 < len(self.pages):
                out['nextPageToken'] = str(number + 1)
            return out
        if resource == 'history':
            return {'history': [], 'historyId': '101'}
        return {'id': resource.split('/')[1]}


class FirstReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.quick_first_review = lambda db: 0  # the regular reviews only; test_fast_first_look has the quick first one
        self.app.config = {'accounts': [{'id': 'work', 'service': 'outlook', 'transport': 'broker'},
                                        {'id': 'cal', 'service': 'outlook_calendar', 'transport': 'broker'}],
                           'review_worker': {'enabled': True, 'min_signals': 40, 'max_wait_minutes': 60, 'gap_seconds': 600},
                           'delivery_enabled': False}
        for name, value in (('Gmail', Mailbox), ('message_event', lambda account, raw, config: signal(raw['id'], account['id']))):
            patcher = patch.object(a, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def later(self, minutes):
        return patch.object(a, 'now', return_value=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat())

    def sync(self, account='work'):
        return a.sync_gmail(self.store, self.app.config, next(x for x in self.app.config['accounts'] if x['id'] == account))

    def step(self, runner, minutes=0):
        with self.later(minutes):
            return self.app.review_step(runner)

    def queue(self, minutes=0):
        with self.later(minutes), self.store.connect() as db:
            return self.app.review_queue(db)

    def runs(self):
        with self.store.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM review_runs ORDER BY started_at')]

    def finish(self, status='ok'):
        with self.store.connect() as db:
            db.execute('UPDATE review_runs SET status=?,finished_at=? WHERE finished_at IS NULL', (status, a.now()))
            if status == 'ok':
                db.execute('UPDATE events SET analyzed_version=version')

    def test_the_first_review_starts_as_soon_as_the_mailbox_is_in_and_only_once(self):
        self.store.commit_page('work', {'mode': 'bootstrap', 'base_history': '1', 'after': 1}, [], record_sync=False)
        queue = self.queue()  # the import has started, nothing in yet
        self.assertEqual((queue['why'], queue['first'], queue['waiting'], queue['starts_at']), ('mail', True, 0, None))
        self.store.commit_page('cal', {'mode': 'incremental'}, [signal('c1', 'cal', kind='calendar.event')])
        Mailbox.pages = [[f'm{n}' for n in range(40)], ['m40', 'm41']]
        self.assertEqual(self.sync()['mode'], 'bootstrap')  # page one of two: 41 signals wait, still no review
        self.assertIsNone(self.step(Runner()))
        self.assertEqual(self.queue()['why'], 'mail')
        self.assertEqual(self.sync()['mode'], 'incremental')  # the mailbox is in
        self.assertTrue(self.store.checkpoint('work')['backfilled_at'])
        self.assertEqual(self.queue()['why'], 'mail_in')
        runner = Runner('admitted', 'running')
        self.assertEqual(self.step(runner), 'admitted')
        self.assertEqual(self.step(runner), 'running')  # the same run is followed, no second one
        self.assertEqual([r['planned'] for r in self.runs()], [43])
        self.assertEqual([r['action'] for r in runner.requests], ['start', 'status'])
        self.finish()
        self.sync()  # the next sync keeps the mark; reviewed now, so new mail waits for the usual batch again
        self.store.commit_page('work', self.store.checkpoint('work'), [signal('new1'), signal('new2')])
        self.assertIsNone(self.step(Runner(), 15))
        self.assertEqual(self.queue(15)['why'], 'batch')

    def test_a_small_mailbox_does_not_wait_for_the_batch_slot(self):
        Mailbox.pages = [['m1', 'm2']]
        self.sync()
        self.assertEqual(self.step(Runner('admitted')), 'admitted')  # 2 signals, right away instead of in an hour
        self.assertEqual(len(self.runs()), 1)

    def test_an_empty_mailbox_starts_nothing(self):
        Mailbox.pages = [[]]
        self.sync()
        self.assertIsNone(self.step(Runner()))
        self.assertIsNone(self.queue())

    def test_a_failed_review_backs_off_and_then_tries_again(self):
        Mailbox.pages = [['m1']]
        self.sync()
        self.assertEqual(self.step(Runner('admitted')), 'admitted')
        self.finish('error')
        self.assertIsNone(self.step(Runner(), 0.5))  # back-off after the failure stays
        self.assertEqual(self.step(Runner('admitted'), 2), 'admitted')  # not an hour later

    def test_a_mailbox_that_cannot_be_read_is_not_waited_for_and_the_wait_is_bounded(self):
        self.store.commit_page('cal', {'mode': 'incremental'}, [signal(f'c{n}', 'cal', kind='calendar.event') for n in range(3)])
        self.store.commit_page('work', {'mode': 'bootstrap', 'base_history': '1', 'after': 1}, [], record_sync=False)
        self.assertIsNone(self.step(Runner()))  # the mailbox's import has started and is not in yet
        self.assertEqual(self.step(Runner('admitted'), 61), 'admitted')  # never past the longest batch wait
        self.finish()
        self.store.failed('work', a.SourceError('outlook:microsoft_signin_expired'))
        with self.store.connect() as db:
            self.assertEqual(self.app.mail_backfill(db), {'filling': [], 'fresh': []})

    def test_another_mailbox_gets_its_first_review_after_the_usual_gap(self):
        Mailbox.pages = [['m1']]
        self.sync()
        self.step(Runner('admitted'))
        self.finish()
        self.app.config['accounts'].append({'id': 'home', 'service': 'gmail', 'transport': 'gog'})
        self.store.commit_page('home', {'mode': 'bootstrap', 'base_history': '1', 'after': 1}, [], record_sync=False)
        self.assertEqual((self.queue(1)['why'], self.queue(1)['first']), ('mail', False))
        Mailbox.pages = [['h1', 'h2']]
        with self.later(1):
            self.sync('home')
        self.assertIsNone(self.step(Runner(), 2))  # the gap between runs stays
        self.assertEqual(self.step(Runner('admitted'), 11), 'admitted')
        self.assertEqual(len(self.runs()), 2)

    def test_mailboxes_imported_before_this_count_as_reviewed(self):
        self.store.commit_page('work', {'mode': 'incremental', 'history_id': '1', 'after': 1}, [signal('m1')])
        with self.store.connect() as db:
            self.assertEqual(self.app.mail_backfill(db), {'filling': [], 'fresh': []})
        self.assertIsNone(self.step(Runner()))
        self.assertEqual(self.queue()['why'], 'batch')

    def test_with_reviews_off_the_first_look_starts_without_the_settling_wait(self):
        self.app.config['review_worker'] = {'enabled': False}
        self.store.commit_page('work', {'mode': 'bootstrap', 'base_history': '1', 'after': 1}, [], record_sync=False)
        with patch.object(a, 'has_model', return_value=True):
            with self.store.connect() as db:
                self.assertEqual(self.app.first_look(db)['why'], 'mail')
            Mailbox.pages = [['m1', 'm2']]
            self.sync()
            with self.store.connect() as db:
                self.assertEqual(self.app.first_look(db)['state'], 'due')
            self.assertEqual(self.step(Runner('admitted')), 'admitted')
        self.assertEqual((self.runs()[0]['preview'], self.runs()[0]['auto']), (1, 1))


if __name__ == '__main__':
    unittest.main()
