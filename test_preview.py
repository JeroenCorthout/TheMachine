"""First-run preview (assistant preview / ./install.sh --preview): one regular review run with reviews
off, its cards shown but never sent or prepared, also not after activation. No Gateway, no Telegram."""
import contextlib, datetime as dt, importlib.util, io, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

a = load('preview_assistant', 'assistant.py')
cli = load('preview_cli', 'cli.py')


class Runner:
    def __init__(self, *results): self.results = list(results); self.requests = []
    def __call__(self, request): self.requests.append(request); return {'status': self.results.pop(0)}


class Daytime(a.dt.datetime):
    @classmethod
    def now(cls, tz=None): return cls(2030, 1, 2, 10, 0, tzinfo=a.dt.timezone.utc).astimezone(tz or a.dt.timezone.utc)  # a later weekday morning


def signal(source_id, days_ago, title='Invoice question'):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': when, 'title': title,
            'body': 'Can you confirm the amount?', 'metadata': {'from': 'Sam Example <sam@example.com>'}, 'deleted': False}


class Preview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'work', 'boundary': 'work:Acme'}], 'review_worker': {'enabled': False},
                           'delivery_enabled': False, 'telegram_owner': 'owner', 'prepare_ahead': True}
        self.store.commit_page('work', {}, [signal('recent', 2), signal('old', 20, 'Old thread')])

    def runs(self):
        with self.store.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM review_runs ORDER BY started_at')]

    def delivery(self, sid):
        with self.store.connect() as db: return dict(db.execute('SELECT * FROM deliveries WHERE suggestion_id=?', (sid,)).fetchone())

    def test_reviews_off_starts_nothing_until_the_owner_asks_for_a_preview(self):
        runner = Runner()
        self.assertIsNone(self.app.review_step(runner))
        self.assertEqual((runner.requests, self.runs()), ([], []))
        started = self.app.action('preview_review', {})
        self.assertEqual((started['waiting'], started['signals']), (1, 1))  # the 20-day-old signal is outside the week
        self.assertEqual(self.app.action('preview_review', {})['attached'], True)  # a second call follows the same run
        self.assertEqual(len(self.runs()), 1)
        self.assertEqual(self.app.config['review_worker'], {'enabled': False})  # config untouched

    def test_preview_uses_the_regular_review_run_then_reviews_stay_off(self):
        run_id = self.app.start_preview()['run_id']
        runner = Runner('admitted', 'pending', 'ok')
        self.assertEqual(self.app.review_step(runner), 'admitted')
        start = runner.requests[0]
        self.assertEqual((start['kind'], start['action'], start['job_id']), ('review', 'start', run_id))
        self.assertEqual(start['message'], a.review_prompt(run_id, ['work'], self.store.preference_summary(), self.store.facts_text(),
                                                                    sources=a.source_notes(a.account_services(self.app.config, ['work']))))
        self.assertEqual(self.app.review_step(runner), 'pending')
        self.assertEqual(self.app.review_step(runner), 'ok')
        self.assertEqual(self.runs()[0]['status'], 'ok')
        # Finished: with reviews off nothing new starts, even with a backlog waiting.
        self.assertIsNone(self.app.review_step(Runner()))
        self.assertEqual(len(self.runs()), 1)

    def test_a_start_the_gateway_declares_over_ends_the_run_so_a_new_one_can_start(self):
        # After a Codex cold-start timeout the Gateway answers every retry of that run id with
        # "submit a new turn"; the runner reports it lost instead of retrying for 35 minutes.
        self.app.start_preview()
        self.assertEqual(self.app.review_step(Runner('lost')), 'lost')
        self.assertEqual((self.runs()[0]['status'], self.runs()[0]['error']), ('lost', 'runtime_lost'))
        self.assertNotEqual(self.app.start_preview()['run_id'], self.runs()[0]['run_id'])

    def test_preview_claims_only_the_last_week(self):
        run_id = self.app.start_preview()['run_id']
        self.app.review_step(Runner('admitted'))
        claim = self.store.begin_analysis(['work'], 120, a.REVIEW_SESSION_PREFIX + run_id, run_id)
        self.assertEqual([e['title'] for e in claim['events']], ['Invoice question'])

    def test_refused_while_reviews_are_on_without_recent_signals_or_during_another_review(self):
        self.app.config['review_worker'] = {'enabled': True}
        with self.assertRaisesRegex(ValueError, 'already on'): self.app.start_preview()
        self.app.config['review_worker'] = {'enabled': False}
        with self.store.connect() as db: db.execute("INSERT INTO review_runs(run_id,status,started_at,updated_at) VALUES('x','running',?,?)", (a.now(), a.now()))
        with self.assertRaisesRegex(ValueError, 'Another review'): self.app.start_preview()
        with self.store.connect() as db: db.execute("UPDATE review_runs SET status='ok',finished_at=?", (a.now(),)); db.execute('UPDATE events SET analyzed_version=version')
        with self.assertRaisesRegex(ValueError, 'No unreviewed signals'): self.app.start_preview()

    def test_preview_cards_are_shown_never_sent_or_prepared_also_after_activation(self):
        self.app.start_preview()
        self.app.review_step(Runner('admitted'))
        saved = self.app.action('suggest', {'dedupe_key': 'invoice', 'title': 'Confirm the invoice', 'body': 'Sam asks to confirm the amount.',
                                            'event_ids': [1], 'when': 'now', 'prepare_ahead': True})
        self.assertEqual((saved['status'], saved['preview'], saved['deliver_after']), ('pending', True, None))
        with self.store.connect() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM preparations').fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM decisions WHERE choice='prepare_ahead'").fetchone())
        self.assertEqual(self.delivery(saved['id'])['status'], 'previewed')
        self.app.review_step(Runner('ok'))
        # --activate: a later regular card goes out; the preview card is not pushed as a backlog.
        self.app.config.update(delivery_enabled=True, review_worker={'enabled': True})
        later = self.store.suggest('later', 'Reply to Sam', 'Sam answered.', [1])
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram', return_value={'messageId': '901'}) as send:
            a.deliver(self.store, self.app.config)
        self.assertEqual([c.args[1]['id'] for c in send.call_args_list], [later['id']])
        self.assertEqual((self.delivery(saved['id'])['status'], self.delivery(later['id'])['status']), ('previewed', 'sent'))
        self.assertEqual(self.store.snapshot([])['suggestions'][-1]['status'], 'pending')  # still on the dashboard


STARTED = '2026-10-16T08:00:00+00:00'
def status(run_status, cards=(), **run):
    suggestions = [{'id': f'c{n}', 'status': 'pending', 'title': title, 'body': '**Sam** asks to confirm the amount.', 'created_at': '2026-10-16T08:03:00+00:00',
                    'source_line': 'Source: email from Sam Example · Thu, Oct 15', 'account_ids': '["work"]', 'event_ids': '[1]'} for n, title in enumerate(cards)]
    return {'suggestions': suggestions + [{'id': 'old', 'status': 'pending', 'title': 'Queued earlier', 'body': 'x', 'created_at': '2026-10-15T08:00:00+00:00'}],
            'deliveries': [{'suggestion_id': s['id'], 'status': 'previewed'} for s in suggestions] + [{'suggestion_id': 'old', 'status': 'queued'}],
            'review_runs': [{'run_id': 'r1', 'status': run_status, 'started_at': STARTED, 'claimed_count': 40, 'review_saved': run_status == 'ok',
                             'checked_count': 40, **run}]}


class PreviewCli(unittest.TestCase):
    config = {'port': 0, 'accounts': [{'id': 'work'}], 'dashboard_host': 'machine.example-tailnet.ts.net', 'dashboard_path': '/assistant/'}

    def run_cli(self, *states, argv=('preview',)):
        replies = [{'run_id': 'r1', 'started_at': STARTED, 'waiting': 40, 'signals': 40}, *states]
        calls = []
        def fake(cfg, action, args=None): calls.append(action); return replies.pop(0)
        out = io.StringIO()
        with patch.object(cli, 'call', side_effect=fake), patch.object(cli, 'load_config', return_value=self.config), \
                patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue(), calls

    def test_shows_progress_then_the_cards_and_that_nothing_was_sent(self):
        out, calls = self.run_cli(status('starting'), status('running', ['Confirm the invoice']), status('ok', ['Confirm the invoice', 'Book the venue']))
        self.assertEqual(calls, ['preview_review', 'status', 'status', 'status'])
        self.assertIn('Reviewing 40 signals from the last 7 days', out)
        self.assertIn('starting the review in OpenClaw', out); self.assertIn('reviewing 40 signals · 1 suggestion so far', out)
        self.assertIn('1. Confirm the invoice', out); self.assertIn('2. Book the venue', out)
        self.assertIn('Sam asks to confirm the amount.', out); self.assertNotIn('**', out)
        self.assertIn('Source: email from Sam Example', out); self.assertNotIn('Queued earlier', out)
        self.assertIn('Nothing was sent', out); self.assertIn('https://machine.example-tailnet.ts.net/assistant/', out)
        self.assertIn('./install.sh --activate', out)

    def test_quiet_week_and_json(self):
        out, _ = self.run_cli(status('ok'))
        self.assertIn('found nothing worth your time', out)
        out, _ = self.run_cli(status('ok', ['Confirm the invoice']), argv=('preview', '--json'))
        self.assertEqual([c['title'] for c in json.loads(out)['cards']], ['Confirm the invoice'])

    def test_failure_keeps_saved_cards_and_exits_with_a_clear_message(self):
        with self.assertRaises(SystemExit) as failure:
            out, _ = self.run_cli(status('timed_out', ['Confirm the invoice'], error='runtime_timeout'))
        self.assertIn("didn't finish (runtime_timeout)", str(failure.exception))
        self.assertIn('assistant preview again', str(failure.exception))

    def test_ctrl_c_leaves_the_run_going(self):
        with patch.object(cli, 'call', side_effect=[{'run_id': 'r1', 'started_at': STARTED, 'attached': True}, KeyboardInterrupt]), \
                patch.object(cli, 'load_config', return_value=self.config), contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(['preview'])
        self.assertIn('keeps running in the service', out.getvalue())


class PreviewInstaller(unittest.TestCase):
    def test_install_sh_preview_runs_the_cli_without_installing(self):
        import installer
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / 'config.json'; config.write_text('{}')
            with patch.dict(os.environ, {'ASSISTANT_CONFIG': str(config)}), patch.object(installer.subprocess, 'call', return_value=0) as call, \
                    patch.object(installer, 'check_prerequisites') as prerequisites:
                self.assertEqual(installer.main(['--preview']), 0)
            prerequisites.assert_not_called()
            self.assertEqual(call.call_args.args[0][-3:], ['--config', str(config), 'preview'])

    def test_closing_steps_offer_the_preview_before_activation(self):
        import installer
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            installer.next_steps({'credentials': {}, 'delivery_enabled': False, 'review_worker': {'enabled': False}}, True, True)
        self.assertLess(out.getvalue().index('./install.sh --preview'), out.getvalue().index('./install.sh --activate'))


if __name__ == '__main__':
    unittest.main()
