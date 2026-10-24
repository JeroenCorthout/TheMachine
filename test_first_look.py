"""The first look: on a new install with reviews off, one preview of the last week starts by itself once a model and a
source are there and the first import settled. Read-only like any preview; never twice, never after the owner's own
preview or after reviews were on. Also what the dashboard shows while reviews run, and the graph picking up new
organization profiles. No Gateway, no Telegram: the runner is a fake and nothing is sent."""
import datetime as dt, importlib.util, json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('first_look_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)


class Runner:
    def __init__(self, *results): self.results = list(results); self.requests = []
    def __call__(self, request): self.requests.append(request); return {'status': self.results.pop(0)}


def signal(source_id, account='work', days_ago=1, kind='gmail.message'):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
    return {'account_id': account, 'source_id': source_id, 'kind': kind, 'occurred_at': when, 'title': 'Pricing question',
            'body': 'Can you confirm?', 'metadata': {'from': 'Sam Example <sam@example.com>'}, 'deleted': False}


class FirstLook(unittest.TestCase):
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

    def runs(self):
        with self.store.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM review_runs ORDER BY started_at')]

    def test_it_waits_for_a_model_a_source_and_the_first_import_then_starts_one_bounded_preview(self):
        self.store.commit_page('gateway', {}, [signal('log', 'gateway', kind='gateway.log')])
        self.assertEqual(self.state(10)['why'], 'sources')  # The Machine's own log is not a source
        with patch.object(a, 'has_model', return_value=False):
            self.assertEqual(self.state(10)['why'], 'model')
        self.store.commit_page('work', {}, [signal(f'm{n}', days_ago=3) for n in range(30)] + [signal('old', days_ago=20)])  # nothing for a quick look
        self.assertEqual(self.state()['why'], 'settling')  # signals still coming in
        self.assertEqual(self.state(4), {'state': 'due', 'waiting': 30, 'days': 7})
        runner = Runner('admitted')
        with self.later(4):
            self.assertEqual(self.app.review_step(runner), 'admitted')
        run = self.runs()[0]
        self.assertEqual((run['preview'], run['auto'], run['planned']), (1, 1, 31))  # the last week (and its log), never the 20-day-old one
        self.assertEqual(runner.requests[0]['action'], 'start')
        self.assertEqual(self.app.config['review_worker'], {'enabled': False})  # reviews stay off: that is the owner's choice
        self.assertIsNone(self.state(5))  # running: nothing new
        with self.store.connect() as db:
            db.execute("UPDATE review_runs SET status='ok',finished_at=?", (a.now(),))
        self.assertIsNone(self.state(60))  # once is enough
        self.assertIsNone(self.app.review_step(Runner()))

    def test_a_review_prompt_over_the_runner_limit_ends_the_run_visibly_instead_of_retrying_as_unavailable(self):
        self.store.commit_page('work', {}, [signal(f'm{n}', days_ago=3) for n in range(30)])
        runner = Runner('admitted')
        with self.later(4), patch.object(a, 'PROMPT_LIMIT', 100), self.assertLogs(a.LOG, 'ERROR'):
            self.assertEqual(self.app.review_step(runner), 'error')
        self.assertEqual(runner.requests, [])  # never sent: the runner would refuse it every time
        run, = self.runs()
        self.assertEqual((run['error'], run['last_error']), ('prompt_too_long', 'prompt_too_long'))
        self.assertTrue(run['finished_at'])

    def test_the_runner_and_the_service_agree_on_the_prompt_limit(self):
        runner = open(a.ROOT / 'preparation-run.mjs').read()
        self.assertIn(f'request.message.length>{a.PROMPT_LIMIT})', runner)

    def test_a_failed_first_look_is_tried_again_later_at_most_three_times(self):
        self.store.commit_page('work', {}, [signal('m1', days_ago=3)])
        for attempt in range(a.FIRST_LOOK_TRIES):
            with self.later(5 + attempt * 40):
                self.assertEqual(self.app.review_step(Runner('admitted')), 'admitted')
            with self.later(5 + attempt * 40), self.store.connect() as db:
                db.execute("UPDATE review_runs SET status='error',finished_at=? WHERE finished_at IS NULL", (a.now(),))
            if attempt < a.FIRST_LOOK_TRIES - 1:
                self.assertEqual(self.state(6 + attempt * 40)['why'], 'retry')
        self.assertIsNone(self.state(500))
        self.assertEqual(len(self.runs()), a.FIRST_LOOK_TRIES)

    def test_a_first_look_a_gateway_restart_cut_off_is_not_a_try(self):
        self.store.commit_page('work', {}, [signal('m1', days_ago=3)])
        for attempt in range(2 * a.FIRST_LOOK_TRIES):
            with self.later(5 + attempt * 40):
                self.assertEqual(self.app.review_step(Runner('admitted')), 'admitted')
            with self.later(5 + attempt * 40), self.store.connect() as db:
                db.execute("UPDATE review_runs SET status=?,finished_at=? WHERE finished_at IS NULL", ('aborted' if attempt < 4 else 'error', a.now()))
            if attempt < 2 * a.FIRST_LOOK_TRIES - 1:
                self.assertEqual(self.state(6 + attempt * 40)['why'], 'retry')
        self.assertIsNone(self.state(500))  # and never more than twice as many runs in all

    def test_never_after_the_owners_own_preview_after_reviews_were_on_or_when_turned_off(self):
        self.store.commit_page('work', {}, [signal('m1')])
        self.app.config['review_worker']['first_look'] = False
        self.assertIsNone(self.state(10))
        self.app.config['review_worker'] = {'enabled': False}
        self.app.start_preview()
        self.assertIsNone(self.state(10))
        with self.store.connect() as db:
            db.execute("UPDATE review_runs SET preview=0,status='ok',finished_at=?", (a.now(),))
        self.assertIsNone(self.state(10))
        self.app.config['review_worker'] = {'enabled': True}
        self.assertIsNone(self.state(10))

    def test_the_live_view_shows_progress_steps_and_the_next_review(self):
        self.store.commit_page('work', {}, [signal(f'm{n}') for n in range(5)] + [signal('c1', kind='calendar.event')])
        with self.later(5):
            self.app.review_step(Runner('admitted'))
        run_id = self.runs()[0]['run_id']
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM events ORDER BY id')]
            items = [{'id': i, 'version': 1, 'page': 0} for i in ids]
            db.execute("INSERT INTO analyses(id,started_at,items,status,owner_session,owner_run) VALUES('a1',?,?,'complete',?,?)",
                       (a.now(), json.dumps(items[:4]), a.REVIEW_SESSION_PREFIX + run_id, run_id))
            db.execute("INSERT INTO analyses(id,started_at,items,status,owner_session,owner_run) VALUES('a2',?,?,'running',?,?)",
                       (a.now(), json.dumps(items[4:]), a.REVIEW_SESSION_PREFIX + run_id, run_id))
            db.execute("INSERT INTO graph_nodes(id,kind,name,strength,signals,info) VALUES('org:globex.example','org','Globex',1,1,'{}')")
        transcript = Path(self.temp.name) / 'agent.sqlite'
        self.app.config['agent_db'] = str(transcript)
        with sqlite3.connect(transcript) as db:
            db.execute('CREATE TABLE session_windows(session_key TEXT,session_id TEXT,created_at INTEGER)')
            db.execute('CREATE TABLE transcript_events(session_id TEXT,seq INTEGER,created_at INTEGER,event_json TEXT,event_zstd BLOB)')
            db.execute('INSERT INTO session_windows VALUES(?,?,1)', (a.REVIEW_SESSION_PREFIX + run_id, 's1'))
            base = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000) + 60_000
            calls = [{'action': 'graph', 'args': {'node_id': 'org:globex.example'}},
                     {'action': 'events', 'args': {'query': 'the secret words of a message'}},
                     {'action': 'suggest', 'args': {'title': '**Reply** to Globex', 'body': 'Never shown'}}]
            for n, call in enumerate(calls):
                event = {'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'name': 'assistant_signals', 'arguments': call}]}}
                db.execute('INSERT INTO transcript_events VALUES(?,?,?,?,NULL)', ('s1', n, base + n * 1000, json.dumps(event)))
        live = self.app.review_live()
        run = live['runs'][0]
        self.assertEqual((run['done_count'], run['planned'], run['claimed_count'], run['account_ids']), (4, 6, 6, ['work']))
        titles = [s['title'] for s in run['steps']]
        self.assertEqual(titles[:3], ['Suggesting: Reply to Globex', 'Looking through earlier messages', 'Looking up Globex'])
        self.assertIn('Reading 4 emails', titles)
        self.assertIn('Reading 1 email, 1 meeting', titles)
        self.assertNotIn('secret', json.dumps(run['steps']))
        self.assertNotIn('Never shown', json.dumps(run['steps']))
        self.assertIsNone(live['queue'])  # reviews off: no regular review waits
        status = self.store.snapshot([{'id': 'work', 'boundary': 'work:Acme'}])
        self.assertEqual(status['review_runs'][0]['done_count'], 4)

    def test_queue_says_how_many_wait_and_when_the_next_review_starts(self):
        self.app.config['review_worker'] = {'enabled': True, 'min_signals': 40, 'max_wait_minutes': 60, 'gap_seconds': 600}
        self.store.commit_page('work', {}, [signal('m1'), signal('m2')])
        with self.store.connect() as db:
            queue = self.app.review_queue(db)
        self.assertEqual((queue['waiting'], queue['why']), (2, 'batch'))
        start = dt.datetime.fromisoformat(queue['starts_at']) - dt.datetime.now(dt.timezone.utc)
        self.assertTrue(dt.timedelta(minutes=58) < start <= dt.timedelta(minutes=61))

    def test_the_graph_rebuilds_soon_after_new_organisation_profiles(self):
        before = self.app.graph_freshness()
        with self.store.connect() as db:
            a.enrichment.save(db, 'globex.example', 'found', {'name': 'Globex', 'industry': 'Software'}, a.now())
        self.assertNotEqual(self.app.graph_freshness(), before)


class MainModelMustRun(unittest.TestCase):
    """A main model set in the config is not enough: its provider needs a sign-in or key (`openclaw models status`,
    no model call), else the first look waits and the dashboard says so. A fallback without one doesn't block."""
    def setUp(self):
        a._model_ready.clear(); self.addCleanup(a._model_ready.clear)

    def status(self, missing, fallbacks=()):
        out = json.dumps({'resolvedDefault': 'openai/gpt-6-astra', 'fallbacks': list(fallbacks), 'auth': {'missingProvidersInUse': missing}})
        calls = []
        return calls, lambda args, **kw: calls.append(args) or type('R', (), {'stdout': out, 'returncode': 0})()

    def test_an_unsigned_main_model_is_no_model(self):
        with patch.object(a, 'main_model', return_value='openai/gpt-6-astra'):
            calls, run = self.status(['openai'])
            self.assertFalse(a.model_ready({'openclaw': '/x/openclaw'}, run))
            self.assertEqual(calls, [['/x/openclaw', 'models', 'status', '--json']])
            self.assertFalse(a.model_ready({}, run))  # cached: no second node process
            self.assertEqual(len(calls), 1)
            with patch.object(a, 'model_ready', return_value=False):
                self.assertFalse(a.has_model())
                item = a.needs_you({'accounts': [], 'manage_url': 'https://cp.example/setup'})[0]
            self.assertEqual((item['id'], item['url'], item['action']), ('model-signed-out', 'https://cp.example/setup#model', 'Fix it'))
            self.assertIn('openai/gpt-6-astra', item['text'])

    def test_a_signed_in_main_model_or_an_unsigned_fallback_is_fine_and_unknown_does_not_block(self):
        with patch.object(a, 'main_model', return_value='claude-cli/claude-sonnet-5-5'):
            self.assertTrue(a.model_ready({}, self.status([])[1]))
            a._model_ready.clear()
            self.assertTrue(a.model_ready({}, self.status(['openai'], ['openai/gpt-6-astra'])[1]))
            a._model_ready.clear()
            self.assertIsNone(a.model_ready({}, lambda *x, **k: (_ for _ in ()).throw(OSError('no openclaw'))))
            a._model_ready.clear()
            with patch.object(a, 'model_ready', return_value=None):
                self.assertTrue(a.has_model())
                self.assertEqual(a.needs_you({'accounts': []}), [])
        with patch.object(a, 'main_model', return_value=''):
            self.assertFalse(a.has_model())


if __name__ == '__main__':
    unittest.main()
