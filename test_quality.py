"""Card quality (hit rate), follow-ups on other people's promises, and checks before a result is saved."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('quality_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def mail(source_id, at, sender='Sam <sam@example.com>', thread='t1', labels=('INBOX',), title='Contract'):
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': at, 'title': title,
            'body': 'I will send the contract by Friday.', 'deleted': False,
            'metadata': {'from': sender, 'thread_id': thread, 'labels': list(labels)}}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'work', 'service': 'gmail', 'transport': 'gog', 'email': 'owner@example.com', 'boundary': 'work:Example'}]}

    def q(self, sql, *args):
        with self.store.connect() as db:
            return dict(db.execute(sql, args).fetchone())

    def deliver(self, sent):
        class Noon(a.dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return a.dt.datetime(2026, 10, 14, 12, tzinfo=a.ZoneInfo('Europe/Brussels')).astimezone(tz or a.dt.timezone.utc)
        with patch.object(a.dt, 'datetime', Noon), patch.object(a, 'telegram', side_effect=lambda *x, **k: (sent.append(x), {'messageId': '7'})[1]):
            a.deliver_one(self.store, self.app.config, edit_cards=False)


class OthersPromises(Base):
    def setUp(self):
        super().setUp()
        self.store.commit_page('work', {}, [mail('promise', '2026-10-10T09:00:00+00:00'),
                                            mail('mine', '2026-10-10T10:00:00+00:00', 'Owner <owner@example.com>', labels=('SENT',))])

    def follow_up(self, **extra):
        return self.app.action('suggest', {'dedupe_key': 'contract', 'title': "Contract from Sam hasn't arrived", 'body': 'Sam promised it by Friday.',
                                           'event_ids': [1], 'awaiting_event_id': 1, 'due': '2099-10-20', **extra})

    def test_follow_up_waits_for_the_deadline_and_needs_their_message(self):
        with patch.object(a, 'now', return_value='2026-10-19T10:00:00+00:00'):
            made = self.follow_up()
        self.assertIn('closes itself', made['awaiting'])
        self.assertTrue(made['deliver_after'].startswith('2099-10-21T06:30'))  # the morning after, 08:30 Brussels
        with self.assertRaises(ValueError):  # The owner's own sent email is not someone else's promise
            self.app.action('suggest', {'dedupe_key': 'x', 'title': 'T', 'body': 'B', 'event_ids': [2], 'awaiting_event_id': 2, 'due': '2026-10-16'})
        with self.assertRaises(ValueError):  # without a deadline it would go out right away
            self.app.action('suggest', {'dedupe_key': 'y', 'title': 'T', 'body': 'B', 'event_ids': [1], 'awaiting_event_id': 1})
        with self.assertRaises(ValueError):
            self.app.action('suggest', {'dedupe_key': 'z', 'title': 'T', 'body': 'B', 'event_ids': [1], 'awaiting_event_id': 2, 'due': '2026-10-16'})

    def test_closes_itself_when_they_write_first(self):
        sid = self.follow_up()['id']
        # The owner's own message in the thread doesn't count; theirs (a new thread, same address) does.
        self.store.commit_page('work', {}, [mail('nudge', '2026-10-11T09:00:00+00:00', 'Owner <owner@example.com>', labels=('SENT',)),
                                            mail('sent-it', '2026-10-12T09:00:00+00:00', thread='t2', title='The contract')])
        sent = []
        self.deliver(sent)
        self.assertEqual(sent, [])
        self.assertEqual(self.q('SELECT status FROM suggestions WHERE id=?', sid)['status'], 'completed')
        self.assertEqual(self.q('SELECT status FROM deliveries WHERE suggestion_id=?', sid)['status'], 'cancelled')
        self.assertIn('They followed up: The contract', self.q("SELECT feedback FROM decisions WHERE suggestion_id=?", sid)['feedback'])
        self.assertIsNone(self.store.quality(30, '2026-10-30T00:00:00+00:00')['hit_rate'])  # never reached the owner: not counted

    def test_arrives_when_nothing_came(self):
        sid = self.follow_up()['id']
        self.store.commit_page('work', {}, [mail('other', '2026-10-12T09:00:00+00:00', 'Kim <kim@example.com>', thread='t9')])
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET deliver_after='2026-10-14T06:00:00+00:00' WHERE id=?", (sid,))
        sent = []
        self.deliver(sent)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.q('SELECT status FROM suggestions WHERE id=?', sid)['status'], 'pending')


class HitRate(Base):
    def card(self, key, kind='gmail.message', *choices, status=None):
        n = len(self.store.events(['work'], limit=1000)) + 1
        self.store.commit_page('work', {}, [{**mail(key, a.now()), 'kind': kind}])
        sid = self.store.suggest(key, 'Card ' + key, 'Body', [n])['id']
        for i, choice in enumerate(choices):
            self.store.decide(sid, choice, 'Shorter' if choice == 'other' else '', f'{key}-{i}', 'dashboard')
        return sid

    def test_hit_rate_first_time_and_sources_reach_dashboard_reviews_and_recap(self):
        self.card('a', 'gmail.message', 'accept')
        self.card('b', 'gmail.message', 'accept', 'other')
        self.card('c', 'gmail.message', 'complete')
        self.card('d', 'whatsapp.message', 'reject')
        self.card('e', 'whatsapp.message', 'reject')
        self.card('f', 'whatsapp.message', 'reject')
        self.card('g', 'gmail.message')
        q = self.store.quality()
        self.assertEqual((q['cards'], q['worth_it'], q['dismissed'], q['open'], q['hit_rate']), (7, 3, 3, 1, 50))
        self.assertEqual((q['do_it'], q['first_time'], q['first_time_rate']), (2, 1, 50))
        self.assertEqual(q['by_source']['WhatsApp'], {'worth_it': 0, 'dismissed': 3, 'open': 0})
        summary = self.store.preference_summary()
        self.assertTrue(summary.startswith('Cards in the last 30 days by source'))
        self.assertIn('- WhatsApp: 0 of 3 worth it', summary)
        self.assertIn('- email: 3 of 3 worth it', summary)
        self.assertEqual(self.store.snapshot(self.app.config['accounts'])['quality']['previous']['hit_rate'], None)
        recap = a.weekly_recap(self.store, self.app.config)
        self.assertIn('**What changed**\n• 50% of cards worth it (3 of 6). Do it was right first time in 1 of 2.', recap)

    def test_trend_per_local_day_and_week(self):
        at = {'a': '2026-10-14T21:30:00+00:00', 'b': '2026-10-14T22:30:00+00:00', 'c': '2026-10-12T08:00:00+00:00', 'd': '2026-10-08T08:00:00+00:00'}
        ids = {k: self.card(k, 'gmail.message', choice) for k, choice in [('a', 'accept'), ('b', 'reject'), ('c', 'accept'), ('d', 'reject')]}
        with self.store.connect() as db:
            for k, sid in ids.items():
                db.execute('UPDATE suggestions SET created_at=? WHERE id=?', (at[k], sid))
                db.execute('UPDATE decisions SET created_at=? WHERE suggestion_id=?', (at[k], sid))
        with patch.object(a, 'now', return_value='2026-10-15T10:00:00+00:00'):
            t = self.store.quality_trend()
        days = {d['start']: d for d in t['daily']}
        self.assertEqual((len(t['daily']), t['daily'][-1]['start'], len(t['weekly']), t['weekly'][-1]['start']), (14, '2026-10-15', 8, '2026-10-12'))
        self.assertEqual((days['2026-10-14']['worth_it'], days['2026-10-14']['dismissed']), (1, 0))  # 23:30 local
        self.assertEqual((days['2026-10-15']['dismissed'], days['2026-10-15']['hit_rate']), (1, 0))  # 00:30 local
        self.assertEqual([(w['worth_it'], w['dismissed'], w['hit_rate']) for w in t['weekly'][-2:]], [(0, 1, 0), (2, 1, 67)])
        self.assertIn('trend', self.store.quality_report())


class CheckBeforeSaving(Base):
    def setUp(self):
        super().setUp()
        self.store.commit_page('work', {}, [mail('one', a.now())])
        self.sid = self.store.suggest('task', 'Reply to Sam', 'I can draft it.', [1])['id']
        self.store.decide(self.sid, 'accept', '', 'go', 'telegram')

    def save(self, body, **extra):
        email = {'kind': 'email', 'account_id': 'work', 'to': ['sam@example.com'], 'subject': 'Contract', 'body': body}
        return self.app.action('prepare', {'suggestion_id': self.sid, 'body': 'Drafted.', 'next_action': email, **extra})

    def test_placeholders_and_missing_attachments_go_back_to_the_run(self):
        with self.assertRaisesRegex(ValueError, r'Check before saving: .*placeholder \(\[Name\]\).*mentions an attachment'):
            self.save('Hi [Name], please find attached the contract.\n\nCheers,\n\nAlex')
        self.assertEqual(self.q('SELECT status FROM suggestions WHERE id=?', self.sid)['status'], 'accepted')
        with self.assertRaisesRegex(ValueError, 'no email next action'):
            self.app.action('prepare', {'suggestion_id': self.sid, 'body': 'Draft:\nSubject: Contract\nHi Sam'})
        self.assertEqual(self.save('Hi Sam, thanks for the attached invoice.\n\nCheers,\n\nAlex')['status'], 'prepared')

    def test_checked_saves_a_deliberate_one(self):
        self.assertEqual(self.save('Hi Sam, see attached [TBD].\n\nCheers,\n\nAlex', checked=True)['status'], 'prepared')


class NotesRulesAndOutcome(Base):
    def setUp(self):
        super().setUp()
        self.store.commit_page('work', {}, [mail('one', a.now())])
        self.sid = self.store.suggest('task', 'Reply to Sam', 'I can draft it.', [1])['id']
        self.store.decide(self.sid, 'accept', '', 'go', 'telegram')

    def run_job(self):
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running'")
            return db.execute('SELECT job_id FROM preparations').fetchone()[0]

    def save(self, **extra):
        return self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': self.run_job(), 'body': 'Drafted.', **extra})

    def test_results_say_whether_they_are_finished(self):
        with self.assertRaisesRegex(ValueError, 'Say whether the job is finished'):
            self.save()
        with self.assertRaisesRegex(ValueError, 'Say whether the job is finished'):
            self.save(outcome='needs_you')
        self.save(outcome='needs_you', needs='Approve the send.')
        row = dict(self.store.snapshot(self.app.config['accounts'])['suggestions'][0])
        self.assertTrue(a.card(row).endswith('Needs you: Approve the send.'))
        self.store.decide(self.sid, 'other', 'Go ahead and put it live', 'go2', 'telegram')
        self.save(outcome='done', note_kind='continue')
        row = dict(self.store.snapshot(self.app.config['accounts'])['suggestions'][0])
        self.assertTrue(a.card(row).endswith('Finished. Mark it done if it looks right.'))

    def test_an_answer_is_not_a_finished_task(self):
        # The owner asked what Do it would do: the answer is ready, the task itself isn't, so Do it leads again.
        self.store.decide(self.sid, 'other', 'What exactly would you do when I tap Do it?', 'q1', 'dashboard')
        self.save(outcome='answered', note_kind='new_ask')
        row = dict(self.store.snapshot(self.app.config['accounts'])['suggestions'][0])
        card = a.card(row)
        self.assertIn('Answered. Nothing done yet.', card)
        self.assertNotIn('Finished', card);self.assertNotIn('Mark it done', card)
        self.assertEqual([b['label'] for b in a.controls(row)['blocks'][0]['buttons']][0], 'Do it')
        self.store.decide(self.sid, 'accept', '', 'go-now', 'dashboard')
        self.assertEqual(self.store.snapshot(self.app.config['accounts'])['suggestions'][0]['status'], 'accepted')

    def test_a_task_found_already_done_shows_that_once(self):
        # Nothing to approve: no buttons allowed, and a later handled hint doesn't add a second line.
        with self.assertRaisesRegex(ValueError, 'nothing left to approve'):
            self.save(outcome='already_done', next_actions=[{'kind': 'prepare', 'label': 'Apply the fix', 'instructions': 'Apply the fix to the form'}])
        self.save(outcome='already_done', note_kind=None)
        self.store.commit_page('work', {}, [mail('two', a.now())])
        self.store.looks_handled(self.sid, 'Sam replied to it himself.', [2])
        row = dict(self.store.snapshot(self.app.config['accounts'])['suggestions'][0])
        card = a.card(row)
        self.assertIn('Looks done already. Mark it done if that’s right.', card)
        self.assertNotIn('Looks handled', card)
        self.assertNotIn('approve', card)

    def test_change_notes_are_sorted_and_count_as_what_they_were(self):
        self.save(outcome='needs_you', needs='approve the send')
        self.store.decide(self.sid, 'other', 'Always put a blank line before my name', 'n1', 'telegram')
        with self.assertRaisesRegex(ValueError, "Say what the owner's Change note was"):
            self.save(outcome='needs_you', needs='approve the send')
        self.save(outcome='needs_you', needs='approve the send', note_kind='style')
        self.store.decide(self.sid, 'other', 'try again', 'n2', 'telegram')
        self.save(outcome='needs_you', needs='approve the send', note_kind='continue')
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT decision_id,kind FROM note_kinds ORDER BY decision_id')], [('n1', 'style'), ('n2', 'continue')])
        q = self.store.quality()
        self.assertEqual((q['do_it'], q['first_time'], q['finished']), (1, 0, 0))
        self.assertEqual(self.store.quality_report()['notes'], {'style': 1, 'fact': 0, 'continue': 1, 'new_ask': 0, 'unsorted': 0})
        with self.store.connect() as db:  # keep going alone: right first time, not finished
            db.execute("DELETE FROM decisions WHERE id='n1'")
        q = self.store.quality()
        self.assertEqual((q['first_time'], q['finished'], q['first_time_rate'], q['finished_rate']), (1, 0, 100, 0))

    def test_draft_rules_reach_drafts_and_mark_the_chart(self):
        self.store.decide(self.sid, 'other', 'Always use my HTML signature', 'n1', 'telegram')
        rule = self.app.action('remember_fact', {'text': 'Use the HTML signature in every reply.', 'decision_id': 'n1', 'kind': 'draft'})
        fact = self.app.action('remember_fact', {'text': 'Sam left Example Corp.', 'decision_id': 'n1'})
        with self.assertRaises(ValueError):
            self.app.action('remember_fact', {'text': 'Something else here', 'decision_id': 'n1', 'kind': 'mood'})
        self.assertEqual(self.store.facts_text(kind='draft'), '- Use the HTML signature in every reply.')
        self.assertEqual(self.store.facts_text(), '- Sam left Example Corp.')
        prompt = a.preparation_prompt({'job_id': 'j' * 16, 'suggestion_id': self.sid}, self.store.facts_text(), rules=self.store.facts_text(kind='draft'))
        self.assertIn("Draft rules from the owner's notes", prompt)
        self.assertIn('- Use the HTML signature in every reply.', prompt)
        self.store.forget_fact(rule['id'])
        self.store.forget_fact(fact['id'])
        texts = [c['text'] for c in self.store.rule_changes('2000-01-01')]
        self.assertEqual(texts, ['Draft rule: Use the HTML signature in every reply.', 'Removed draft rule: Use the HTML signature in every reply.'])
        rules = Path(self.temp.name) / 'AGENTS.md'
        rules.write_text('one')
        self.store.note_rule_files([rules])
        self.store.note_rule_files([rules])
        rules.write_text('two')
        self.store.note_rule_files([rules])
        self.assertEqual([c['text'] for c in self.store.rule_changes('2000-01-01')][2:], ['AGENTS.md tracked', 'AGENTS.md edited'])
        today = self.store.quality_trend()['daily'][-1]
        self.assertEqual(today['changes'], texts + ['AGENTS.md edited'])  # "tracked" is only the starting point


if __name__ == '__main__':
    unittest.main()
