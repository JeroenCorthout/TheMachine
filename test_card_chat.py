"""Card chat: card_chat reads the card's own session through the Gateway helper and shows it as a conversation (the
owner's own notes from the service, never the service prompts; steps and replies redacted), and a chat message is the
Change path with a short follow-up prompt."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('card_chat_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def row(role, content, run, ms):
    return {'role': role, 'content': content, 'timestamp': ms, '__openclaw': {'runId': run, 'seq': ms}}


class CardChat(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'Evidence', 'body': 'Actual evidence', 'metadata': {}, 'deleted': False}], None)
        self.sid = self.store.suggest('task', 'Reply to the venue', 'I can draft this.', [1])['id']
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [], 'database': str(Path(self.temp.name) / 'events.sqlite')}
        self.calls = []

    def gateway(self, result):
        return patch.object(self.app, 'card_chat_gateway', side_effect=lambda request: self.calls.append(request) or result)

    def job(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM preparations WHERE suggestion_id=?', (self.sid,)).fetchone())

    def test_items_show_the_owner_and_the_agent_never_the_prompts(self):
        messages = [
            row('user', [{'type': 'text', 'text': 'Prepare suggestion abc. Preparation job ID: 123. /home/someone/x'}], 'r1', 1000),
            row('assistant', [{'type': 'thinking', 'thinking': 'secret plan'},
                              {'type': 'toolCall', 'name': 'browser', 'arguments': {'title': 'Opening /home/someone/notes.txt'}},
                              {'type': 'toolCall', 'name': 'exec', 'arguments': {}}], 'r1', 2000),
            row('toolResult', [{'type': 'text', 'text': 'tool output'}], 'r1', 2500),
            row('assistant', [{'type': 'text', 'text': 'Drafted it. Key api_key=xxxxxxxxxx at /tmp/draft.md'}], 'r1', 3000),
            row('user', [{'type': 'text', 'text': 'This continues the SAME session'}], 'r2', 5000),
            row('assistant', [{'type': 'text', 'text': 'Shorter now.'}], 'r2', 6000),
        ]
        owner = [{'kind': 'you', 'text': 'Do it', 'button': True, 'at': 1000}, {'kind': 'you', 'text': 'Make it shorter', 'button': False, 'at': 5000}]
        items = a.chat_items(messages, owner)
        self.assertEqual([i['kind'] for i in items], ['you', 'steps', 'reply', 'you', 'reply'])
        self.assertEqual(items[1]['steps'], ['Opening …', a.STEP_NAMES.get('exec', 'Working')])
        self.assertEqual(items[2]['text'], 'Drafted it. Key api_key=[redacted] at …')
        text = json.dumps(items)
        for hidden in ('Prepare suggestion', 'SAME session', 'secret plan', 'tool output', '/home/', 'xxxxxxxxxx'):
            self.assertNotIn(hidden, text)

    def test_learning_internals_stay_out_and_say_noted(self):
        msgs = [row('assistant', [{'type': 'text', 'text': 'Saved the reply.'}], 'r1', 1000),
                row('assistant', [{'type': 'text', 'text': '\U0001F4BE Learned: updated `some-skill` (Capture it.). Say "undo" to revert this skill change.'}], 'r1', 2000),
                row('assistant', [{'type': 'text', 'text': '\u2705 Found it in `the-file`.'}], 'r2', 3000),
                {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Late.'}], 'timestamp': 'x', '__openclaw': {'runId': 'r3', 'recordTimestampMs': 4000}}]
        items = a.chat_items(msgs, [])
        self.assertEqual([i['kind'] for i in items], ['reply', 'reply', 'reply'])
        self.assertTrue(items[0]['noted'])  # "Noted for next time" on the reply the learning came with
        self.assertNotIn('noted', items[1])
        self.assertNotIn('skill', json.dumps(items))
        self.assertEqual(items[1]['text'], 'Found it in the-file.')
        self.assertEqual(items[2]['at'], 4000)

    def test_steps_come_before_the_reply_and_saved_results_win(self):
        msgs = [row('assistant', [{'type': 'toolCall', 'name': 'exec', 'arguments': {}}], 'r1', 3000),
                row('assistant', [{'type': 'text', 'text': 'Free text the run also wrote.'}], 'r1', 4000)]
        saved = {'r1': [{'kind': 'reply', 'text': 'Drafted the reply.', 'drafts': [{'kind': 'email', 'body': 'Hi'}], 'at': 2000}],
                 'gone': [{'kind': 'reply', 'text': 'An older result.', 'drafts': [], 'at': 500}]}
        items = a.chat_items(msgs, [{'kind': 'source', 'text': 'Can you help?', 'at': 100}, {'kind': 'you', 'text': 'Shorter', 'at': 1000}], saved)
        self.assertEqual([(i['kind'], i.get('text')) for i in items],
                         [('source', 'Can you help?'), ('reply', 'An older result.'), ('you', 'Shorter'), ('steps', None), ('reply', 'Drafted the reply.')])
        self.assertLess(items[3]['at'], items[4]['at'])

    def test_the_service_side_tells_who_wrote_what_sent_and_tapped(self):
        with self.store.connect() as db:
            db.execute("UPDATE events SET metadata=? WHERE source_id='one'", (json.dumps({'from': 'Doe, Alex <alex@example.com>'}),))
        self.store.decide(self.sid, 'snooze', '', 'z1', 'dashboard')
        self.store.decide(self.sid, 'accept', '', 'a1', 'telegram')
        job = {'job_id': 'job1'}
        draft = {'kind': 'email', 'to': ['alex@example.com'], 'subject': 'Re: Evidence', 'body': 'Hi Alex, done.', 'thread_id': 't1'}
        with self.store.connect() as db:
            db.execute("INSERT INTO preparation_runs(job_id,suggestion_id,session_job_id,status,started_at) VALUES('job1',?,'job1','ok',?)", (self.sid, a.now()))
            db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,started_at) VALUES(?,?,?,?,?)',
                       (job['job_id'], 'c1', 'assistant_signals', json.dumps({'action': 'prepare', 'args': {
                           'suggestion_id': self.sid, 'body': 'Drafted it. See [notes](file:///x/notes.md).', 'next_actions': [draft]}}), a.now()))
            db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,error,started_at) VALUES(?,?,?,?,?,?)',
                       (job['job_id'], 'c2', 'assistant_signals', json.dumps({'action': 'prepare', 'args': {'suggestion_id': self.sid, 'body': 'Failed one'}}), 'bad', a.now()))
            for n, (source, status) in enumerate((('dashboard', 'sent'), ('auto', 'failed'))):
                db.execute('INSERT INTO action_jobs(id,suggestion_id,revision,payload,status,created_at,updated_at,approval_id,source,rfc_message_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (f'j{n}', self.sid, f'r{n}', json.dumps(draft), status, a.now(), a.now(), 'ap', source, ''))
            owner, results = self.app.card_chat_service(db, self.sid)
        source = owner[0]
        self.assertEqual((source['kind'], source['from'], source['via'], source['text']), ('source', 'Alex Doe', 'email', 'Actual evidence'))
        events = [(i['text'], i.get('mine')) for i in owner if i['kind'] == 'event']
        self.assertEqual(events, [('Snoozed', True), ('Do it in Telegram', True), ('Sent to Alex', True), ('Didn’t go out to Alex', False)])
        self.assertEqual(next(i for i in owner if i.get('sent'))['sent'], 'Hi Alex, done.')
        [result] = results[job['job_id']]
        self.assertEqual(result['text'], 'Drafted it. See notes.')
        self.assertEqual([(d['to'], d['body'], d['reply']) for d in result['drafts']], [(['Alex'], 'Hi Alex, done.', True)])

    def test_result_file_links_are_chips_not_bare_titles(self):
        body, files = a.chat_result_files('Drafted it. Nothing sent.\n\n[Draft and sources](memory/assistant/261023-note.md)\n'
                                          'See [the plan](/home/x/.openclaw/workspace/memory/assistant/plan.md) too. [Bad](memory/assistant/../secret.md)')
        self.assertEqual(files, [{'label': 'Draft and sources', 'path': 'memory/assistant/261023-note.md'},
                                 {'label': 'the plan', 'path': 'memory/assistant/plan.md'}])
        self.assertNotIn('Draft and sources', body)
        self.assertIn('See too.', body)
        self.assertEqual(a.chat_reply(body)[0].splitlines()[0], 'Drafted it. Nothing sent.')

    def test_names_read_as_people_say_them(self):
        self.assertEqual(a.chat_person('Lee, Sam'), 'Sam Lee')
        self.assertEqual(a.chat_person('"Alex Doe"'), 'Alex Doe')
        self.assertEqual(a.chat_person('Acme, Inc, Sales'), 'Acme, Inc, Sales')

    def test_a_tap_taken_back_at_once_is_left_out(self):
        for n, (choice, at) in enumerate((('complete', '2026-01-01T10:00:00+00:00'), ('reopen', '2026-01-01T10:00:20+00:00'),
                                          ('snooze', '2026-01-01T11:00:00+00:00'), ('reject', '2026-01-01T12:00:00+00:00'), ('undo', '2026-01-01T12:00:05+00:00'))):
            with self.store.connect() as db:
                db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,'','dashboard',?)", (f'd{n}', self.sid, choice, at))
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, self.sid)
        self.assertEqual([i['text'] for i in owner if i['kind'] == 'event'], ['Snoozed'])

    def test_a_conversation_source_names_who_the_card_is_about(self):
        body = 'Can you confirm the plan?\n1792672698 · Fin · conversation_attribute_updated_by_admin:\n1792673603 · Sam Lee · note: Hi there, we need your call.\nThanks\n1792674070 · Owner Person · note: ask why'
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'two', 'kind': 'intercom.conversation', 'occurred_at': a.now(),
                                             'title': 'Plan', 'body': body, 'metadata': {'from': ''}, 'deleted': False}], None)
        with self.store.connect() as db:
            event = db.execute("SELECT id FROM events WHERE source_id='two'").fetchone()['id']
        sid = self.store.suggest('plan', 'Sam needs your call on the plan', 'Sam asks.', [event])['id']
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, sid)
        self.assertEqual({k: owner[0][k] for k in ('from', 'via', 'text', 'at')},
                         {'from': 'Sam Lee', 'via': 'Intercom', 'text': 'Hi there, we need your call.\nThanks', 'at': 1792673603000})  # the connector's label

    def test_a_conversation_source_is_what_was_asked_then_the_note(self):
        body = ('Can I sync all inboxes on this plan?\n1792673603 · Sam Lee · note: Hi there.\n1792683559 · user · comment: They belong to different people\n'
                '1792683579 · user · comment: Great!\n1792683630 · user · comment: Maybe 3 to 6 users\n1792685607 · Sam Lee · note: We need to ask for this')
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'three', 'kind': 'intercom.conversation', 'occurred_at': a.now(),
                                             'title': 'Plan', 'body': body, 'metadata': {'from': '', 'created_at': 1792672698}, 'deleted': False}], None)
        with self.store.connect() as db:
            event = db.execute("SELECT id FROM events WHERE source_id='three'").fetchone()['id']
        sid = self.store.suggest('plan2', 'Sam needs your call on the plan', 'Sam asks.', [event])['id']
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, sid)
        self.assertEqual([(i['from'], i['text'], i['at'], i.get('note', False)) for i in owner if i['kind'] == 'source'],
                         [('Contact', 'Can I sync all inboxes on this plan?', 1792672698000, False),
                          ('Contact', 'They belong to different people\nMaybe 3 to 6 users', 1792683630000, False),
                          ('Sam Lee', 'We need to ask for this', 1792685607000, True)])

    def test_an_agent_result_leads_with_what_it_found(self):
        text, quiet = a.chat_agent_text('I wrote `summary.md` in this folder. Nothing else was changed, sent or posted.\n\nThe price is lower.\n\n'
                                        'Saved [reply-draft.md](/home/x/reply-draft.md). Left for you: send it. Nothing sent or published.', ['summary.md', 'reply-draft.md'])
        self.assertEqual((text, quiet), ('The price is lower.\n\nLeft for you: send it.', True))
        self.assertEqual(a.chat_agent_text('Fixed the bug in parser.py.', ['parser.py']), ('Fixed the bug in parser.py.', False))

    def test_changes_asked_of_a_coding_agent_show_before_its_answer(self):
        import agent_jobs
        with self.store.connect() as db:
            db.executescript(agent_jobs.SCHEMA)
            db.execute("INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,asks,summary,files,created_at,updated_at,finished_at) "
                       "VALUES('aj_1',?,'codex','local','result','b',?,'I edited summary.md. No, it checked nothing.',?,"
                       "'2026-01-01T10:00:00+00:00','2026-01-01T10:09:00+00:00','2026-01-01T10:09:00+00:00')",
                       (self.sid, '[{"at": "2026-01-01T10:08:00+00:00", "text": "Did it check shops?"}]', '[{"path": "summary.md", "size": 3}]'))
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, self.sid)
        agent = [i for i in owner if i.get('to') or i['kind'] == 'agent' or i.get('text', '').startswith('Handed')]
        self.assertEqual([(i['kind'], i['text']) for i in agent], [('event', 'Handed to Codex'), ('you', 'Did it check shops?'), ('agent', 'No, it checked nothing.')])
        self.assertEqual((agent[1]['to'], agent[2]['files'], agent[2]['nothing_sent']), ('Codex', ['summary.md'], False))

    def agent_turns(self, owner):
        return [(i['kind'], i['text']) for i in sorted(owner, key=lambda i: i['at']) if i.get('to') or i['kind'] == 'agent' or i.get('text', '').startswith('Handed')]

    def test_each_turn_of_a_coding_agent_gets_its_answer(self):
        import agent_jobs
        answers = [{'at': '2026-01-01T10:00:05+00:00', 'turn': 1, 'summary': 'Here is the summary.', 'files': []},
                   {'at': '2026-01-01T10:00:30+00:00', 'turn': 2, 'summary': 'Shorter now.', 'files': []},
                   {'at': '2026-01-01T10:09:00+00:00', 'turn': 3, 'summary': 'It checked no shops.', 'files': ['summary.md']}]
        with self.store.connect() as db:
            db.executescript(agent_jobs.SCHEMA)
            db.execute("INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,asks,summary,answers,turns,created_at,updated_at) "
                       "VALUES('aj_2',?,'claude','local','result','b',?,'It checked no shops.',?,3,'2026-01-01T10:00:00+00:00','2026-01-01T10:09:00+00:00')",
                       (self.sid, json.dumps([{'at': '2026-01-01T10:00:10+00:00', 'text': 'Make it shorter'}, {'at': '2026-01-01T10:08:00+00:00', 'text': 'Shops?'}]), json.dumps(answers)))
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, self.sid)
        # the handoff is the first turn, also when a change follows at once: every message has its own answer
        self.assertEqual(self.agent_turns(owner), [('event', 'Handed to Claude Code'), ('agent', 'Here is the summary.'), ('you', 'Make it shorter'),
                                                   ('agent', 'Shorter now.'), ('you', 'Shops?'), ('agent', 'It checked no shops.')])

    def test_an_old_job_reads_each_turns_answer_from_its_transcripts(self):
        """Jobs from before every answer was kept saved only the latest (the summary): the others come from the job
        folder's turn-N.jsonl, for Codex (exec and app server) and Claude Code alike, each after its own message."""
        import agent_jobs
        folder = Path(self.temp.name) / 'agent-jobs' / 'aj_00000000000000aa'
        (folder / 'work').mkdir(parents=True)
        (folder / 'work' / 'summary.md').write_text('two lines')
        line = lambda *events: '\n'.join(json.dumps(e) for e in events) + '\n'
        msg = lambda text: {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}
        (folder / 'turn-1.jsonl').write_text(line({'type': 'thread.started', 'thread_id': 't'}, msg('I’ll read the card first.'),
                                                  {'type': 'item.completed', 'item': {'type': 'file_change', 'changes': [{'path': str(folder / 'work' / 'summary.md')}]}},
                                                  msg('Created [summary.md](/home/x/summary.md) with four short lines. Nothing else was changed or sent.'),
                                                  {'type': 'turn.completed'}))
        (folder / 'turn-2.jsonl').write_text(line({'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Shortened `summary.md` to two lines. Only that file was changed.'}}},
                                                  {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}))
        (folder / 'turn-3.jsonl').write_text(line(msg('I don’t know that. I read the files and edited `summary.md`; I didn’t check any shops.')))
        import os
        for n, at in ((1, '2026-01-01T10:00:20+00:00'), (2, '2026-01-01T10:01:10+00:00'), (3, '2026-01-01T10:20:30+00:00')):
            stamp = a.chat_ms(at) / 1000
            os.utime(folder / f'turn-{n}.jsonl', (stamp, stamp))
        asks = [{'at': '2026-01-01T10:01:00+00:00', 'text': 'Make it two lines shorter'}, {'at': '2026-01-01T10:20:00+00:00', 'text': 'Did it check shops?'}]
        with self.store.connect() as db:
            db.executescript(agent_jobs.SCHEMA)
            db.execute("INSERT INTO agent_jobs(id,suggestion_id,tool,place,status,brief,asks,summary,files,turns,created_at,updated_at,finished_at) "
                       "VALUES('aj_00000000000000aa',?,'codex','local','result','b',?,?,?,3,'2026-01-01T10:00:00+00:00','2026-01-01T10:20:31+00:00','2026-01-01T10:20:31+00:00')",
                       (self.sid, json.dumps(asks), 'I don’t know that. I read the files and edited `summary.md`; I didn’t check any shops.', '[{"path": "summary.md", "size": 3}]'))
        with self.store.connect() as db:
            owner, _ = self.app.card_chat_service(db, self.sid)
        self.assertEqual(self.agent_turns(owner), [
            ('event', 'Handed to Codex'), ('agent', 'Created summary.md with four short lines. Nothing else was changed or sent.'),
            ('you', 'Make it two lines shorter'), ('agent', 'Shortened summary.md to two lines. Only that file was changed.'),
            ('you', 'Did it check shops?'), ('agent', 'I don’t know that. I didn’t check any shops.')])
        agent = [i for i in sorted(owner, key=lambda i: i['at']) if i['kind'] == 'agent']
        self.assertEqual([i['files'] for i in agent], [['summary.md'], [], ['summary.md']])
        # the last one was shortened: the whole answer is there for its More
        self.assertEqual(agent[2]['full'], 'I don’t know that. I read the files and edited summary.md; I didn’t check any shops.')
        self.assertNotIn('full', agent[1])

    def test_claude_code_transcripts_and_failed_turns(self):
        import agent_jobs
        folder = Path(self.temp.name) / 'job'
        folder.mkdir()
        (folder / 'turn-1.jsonl').write_text(json.dumps({'type': 'result', 'subtype': 'success', 'result': 'The price is lower.'}) + '\n')
        (folder / 'turn-2.jsonl').write_text(json.dumps({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True, 'result': 'x'}) + '\n')
        job = {'turns': 2, 'status': 'failed', 'created_at': '2026-01-01T10:00:00+00:00', 'asks': json.dumps([{'at': '2026-01-01T10:05:00+00:00', 'text': 'More?'}])}
        self.assertEqual([(x['turn'], x['summary']) for x in agent_jobs.turn_answers(job, folder)], [(1, 'The price is lower.')])
        self.assertIsNone(agent_jobs.transcript_answer(folder / 'missing.jsonl'))

    def test_a_shortened_agent_answer_keeps_what_it_says_after_a_semicolon(self):
        self.assertEqual(a.chat_agent_text('I edited `summary.md`; I didn’t check any shops.', ['summary.md'], full=True),
                         ('I didn’t check any shops.', False, 'I edited summary.md; I didn’t check any shops.'))
        self.assertEqual(a.chat_agent_text('Fixed it; tests pass.', ['x.py'], full=True), ('Fixed it; tests pass.', False, ''))

    def test_card_chat_reads_the_cards_session_with_its_own_notes(self):
        with self.gateway({}):
            empty = self.app.card_chat(self.sid)
        self.assertEqual((empty['session'], empty['active']), (False, False))
        self.assertEqual([(i['kind'], i['text']) for i in empty['items']], [('source', 'Actual evidence')])  # the card starts at its source
        self.assertEqual(self.calls, [])  # no session yet: nothing to ask the Gateway
        self.store.decide(self.sid, 'other', 'Make it shorter', 'chat-1', 'dashboard')
        job = self.job()
        with self.store.connect() as db:  # a send is shown by its action's own label, not as a decision too
            db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES('s1',?,'send','','dashboard',?)", (self.sid, a.now()))
        history = {'messages': [row('assistant', [{'type': 'text', 'text': 'On it.'}], job['job_id'], a.chat_ms(a.now()) + 1000)],
                   'deltaCursor': 'cur1', 'hasMore': False, 'inFlightRun': {'runId': job['job_id'], 'text': 'Writing to /home/x/y'}}
        with self.gateway(history):
            chat = self.app.card_chat(self.sid)
        self.assertEqual(self.calls[0]['session_key'], 'agent:main:assistant-review-preparation-' + job['session_job_id'])
        self.assertEqual((self.calls[0]['limit'], self.calls[0]['cursor']), (a.CHAT_HISTORY_LIMIT, None))
        self.assertTrue(chat['active'])
        self.assertEqual(chat['cursor'].split('|')[0], 'cur1')
        self.assertEqual(chat['live'], {'text': 'Writing to …'})
        self.assertEqual([(i['kind'], i['text']) for i in chat['items']], [('source', 'Actual evidence'), ('you', 'Make it shorter'), ('reply', 'On it.')])
        # Polling with the cursor: nothing new is just "unchanged", with the live reply
        with self.gateway({'kind': 'delta', 'messages': [], 'deltaCursor': 'cur2', 'inFlightRun': {'text': 'Writing more'}}):
            delta = self.app.card_chat(self.sid, chat['cursor'])
        self.assertEqual(self.calls[-1]['cursor'], 'cur1')
        self.assertEqual((delta['unchanged'], delta['cursor'].split('|')[0], delta['live']), (True, 'cur2', {'text': 'Writing more'}))
        self.assertNotIn('items', delta)
        # A send moving on (queued, then sent) is the service's own news: the whole chat comes back, not "unchanged"
        with self.store.connect() as db:
            db.execute("INSERT INTO action_jobs(id,suggestion_id,revision,payload,status,created_at,updated_at,approval_id,source,rfc_message_id) "
                       "VALUES('j1',?,'r1',?,'queued',?,?,'ap1','dashboard','')",
                       (self.sid, json.dumps({'kind': 'email', 'to': ['sam@example.com'], 'to_name': 'Sam', 'subject': 'Hi', 'body': 'Hello Sam'}), a.now(), a.now()))
        with self.gateway({'kind': 'delta', 'messages': [], 'deltaCursor': 'cur3'}):
            moved = self.app.card_chat(self.sid, delta['cursor'])
        self.assertIsNone(self.calls[-1]['cursor'])  # asked for the whole conversation again
        self.assertNotIn('unchanged', moved)
        self.assertIn(('event', 'Sending to Sam…'), [(i['kind'], i['text']) for i in moved['items']])
        with self.store.connect() as db:
            db.execute("UPDATE action_jobs SET status='sent',message_id='m1' WHERE id='j1'")
        with self.gateway({'messages': history['messages'], 'deltaCursor': 'cur4'}):
            sent = self.app.card_chat(self.sid, moved['cursor'])
        self.assertIn(('event', 'Sent to Sam'), [(i['kind'], i['text']) for i in sent['items']])
        with self.gateway({'error': 'unavailable'}):
            down = self.app.card_chat(self.sid)
        self.assertTrue(down['unavailable'])
        self.assertEqual(down['items'][1]['text'], 'Make it shorter')

    def test_card_chat_refuses_what_is_not_a_card(self):
        for bad in ('', '../x', 'ABCDEF0123456789', '0' * 16):
            with self.assertRaises(ValueError):
                self.app.card_chat(bad)
        with self.assertRaises(ValueError):
            self.app.card_chat(self.sid, 'x' * 5000)

    def test_card_chat_is_an_owner_dashboard_action(self):
        self.assertIn('card_chat', a.OWNER_ACTIONS)
        with self.gateway({}):
            self.assertEqual(self.app.action('card_chat', {'suggestion_id': self.sid, 'source': 'dashboard'}, owner_dashboard=True)['session'], False)

    def test_a_chat_message_gets_a_short_follow_up_prompt(self):
        self.store.decide(self.sid, 'accept', '', 'click', 'telegram')
        calls = []
        self.app.preparation_step(lambda r: calls.append(r) or {'status': 'admitted'})
        first = next(c['message'] for c in calls if c.get('message'))
        self.assertIn('the owner tapped Do it', first)
        with self.store.connect() as db:
            db.execute("UPDATE preparation_runs SET status='ok',finished_at=?", (a.now(),))
            db.execute("UPDATE preparations SET status='done'")
            db.execute("UPDATE suggestions SET status='prepared'")
        self.store.decide(self.sid, 'other', 'Make it shorter', 'chat-' + 'a' * 36, 'dashboard')
        calls.clear()
        self.app.preparation_step(lambda r: calls.append(r) or {'status': 'admitted'})
        chat = next(c['message'] for c in calls if c.get('message'))
        self.assertIn('wrote to you in the chat', chat)
        self.assertIn('This continues the SAME session', chat)
        self.assertNotIn('the owner tapped Do it', chat)
        self.assertLess(len(chat), len(first))
        # A Change from the card itself keeps the full prompt
        with self.store.connect() as db:
            db.execute("UPDATE preparation_runs SET status='ok',finished_at=?", (a.now(),))
            db.execute("UPDATE preparations SET status='done'")
            db.execute("UPDATE suggestions SET status='prepared'")
        self.store.decide(self.sid, 'other', 'Another note', 'd-change', 'dashboard')
        calls.clear()
        self.app.preparation_step(lambda r: calls.append(r) or {'status': 'admitted'})
        self.assertNotIn('wrote to you in the chat', next(c['message'] for c in calls if c.get('message')))


class ChatHandleTest(unittest.TestCase):
    def test_raw_chat_handles_never_name_anyone(self):
        import assistant
        self.assertEqual(assistant.chat_draft({'kind': 'slack', 'body': 'hi', 'to_name': 'andrea', 'to_email': 'andrea@example.com'})['to'], ['Andrea'])
        self.assertEqual(assistant.chat_draft({'kind': 'slack', 'body': 'hi', 'to': ['#DM']})['to'], [])
        for handle in ('#DM', '#DM U0A3LDWTNUS', 'D0A4EP54JM6', '#general'):
            self.assertTrue(assistant.CHAT_HANDLE.fullmatch(handle), handle)
            self.assertEqual(assistant.chat_name(handle, {}, 'Andrea Bustos'), 'Andrea')
        self.assertEqual(assistant.chat_name('sam@example.com', {}, ''), 'sam@example.com')


if __name__ == '__main__':
    unittest.main()
