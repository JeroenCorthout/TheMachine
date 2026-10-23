"""A Claude Code or Codex job's result is part of its card's state: the card's chat turn, its preparation and the
review's status read it as the latest truth of what was done, so the next step never repeats finished work."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import agent_jobs

spec = importlib.util.spec_from_file_location('card_agent_work_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

DONE = 'The acme.example link is now live across all 14 languages, keeping the existing wording.'
STALE = 'Next, edit the agency article: link the existing words to acme.example and publish.'


class CardAgentWork(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'Final entry', 'body': 'Pat sent the final entry.', 'metadata': {}, 'deleted': False}], None)
        self.sid = self.store.suggest('task', 'Acme sent the final entry', STALE, [1])['id']
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [], 'database': str(Path(self.temp.name) / 'events.sqlite')}
        with self.store.connect() as db:
            db.executescript(agent_jobs.SCHEMA)

    def job(self, status='result', summary=DONE, progress=None, id='aj_00000000000000a1'):
        with self.store.connect() as db:
            db.execute("INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,asks,summary,progress,files,created_at,updated_at,started_at,finished_at) "
                       "VALUES(?,?,'codex','local',?,'b',?,?,?,'[]','2026-10-23T09:17:00+00:00','2026-10-23T09:45:00+00:00','2026-10-23T09:31:00+00:00',?)",
                       (id, self.sid, status, json.dumps([{'at': '2026-10-23T09:30:00+00:00', 'text': 'Add the link in every language'}]),
                        summary if status == 'result' else None, progress, '2026-10-23T09:45:00+00:00' if status == 'result' else None))

    def chat_turn(self):
        """A first run, then the owner asks in the card's chat: the short follow-up turn's message."""
        self.store.decide(self.sid, 'accept', '', 'click', 'dashboard')
        self.app.preparation_step(lambda r: {'status': 'admitted'})
        with self.store.connect() as db:
            db.execute("UPDATE preparation_runs SET status='ok',finished_at=?", (a.now(),))
            db.execute("UPDATE preparations SET status='done'")
            db.execute("UPDATE suggestions SET status='prepared'")
        self.store.decide(self.sid, 'other', 'Alright, and what is the next step?', 'chat-' + 'b' * 36, 'dashboard')
        calls = []
        self.app.preparation_step(lambda r: calls.append(r) or {'status': 'admitted'})
        return next(c['message'] for c in calls if c.get('message'))

    def test_the_chat_turn_knows_what_the_finished_job_did(self):
        self.job()
        message = self.chat_turn()
        self.assertIn('wrote to you in the chat', message)
        self.assertIn('Codex finished', message)
        self.assertIn(DONE, message)
        self.assertIn('Add the link in every language', message)
        self.assertIn('what it reports done is done', message)
        # The stale next step is not what the turn is told about the work: the job's result comes after it, as the newer truth.
        self.assertNotIn(STALE, message)

    def test_a_full_preparation_knows_it_too(self):
        self.job()
        self.store.decide(self.sid, 'accept', '', 'click', 'dashboard')
        calls = []
        self.app.preparation_step(lambda r: calls.append(r) or {'status': 'admitted'})
        message = next(c['message'] for c in calls if c.get('message'))
        self.assertIn('the owner tapped Do it', message)
        self.assertIn(DONE, message)

    def test_a_running_job_shows_its_progress(self):
        self.job(status='running', progress='Editing the German page')
        message = self.chat_turn()
        self.assertIn('Codex is still working on it', message)
        self.assertIn('Editing the German page', message)

    def test_the_review_status_carries_the_result(self):
        self.job()
        self.app.agents = agent_jobs.AgentJobs(self.store.connect, {'agents': {}}, Path(self.temp.name) / 'agent-jobs')
        snapshot = self.store.snapshot([])
        with self.store.connect() as db:
            self.app.agents.annotate(db, snapshot['suggestions'])
        snapshot.update(analyses=[], decisions=snapshot.get('decisions', []))
        card = next(s for s in a.compact_status(snapshot)['suggestions'] if s['id'] == self.sid)
        self.assertEqual((card['agent_work']['tool'], card['agent_work']['status'], card['agent_work']['result']), ('Codex', 'result', DONE))
        self.assertEqual(card['agent_work']['at'], '2026-10-23T09:45:00+00:00')
        self.assertIn('agent_work', a.review_prompt('run', ['work']))

    def test_no_jobs_add_nothing(self):
        self.assertEqual(self.app.agent_work(self.sid), '')
        self.assertNotIn('Claude Code or Codex on this card', self.chat_turn())


if __name__ == '__main__':
    unittest.main()
