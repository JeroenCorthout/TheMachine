"""Promises tracker: reviews record promises both ways, someone else's close when they write again,
and an open one becomes a card when it's due. No live calls."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import cli
import test_ux as ux
a = ux.a

GMAIL = {'id': 'work', 'email': 'owner@example.com', 'service': 'gmail', 'transport': 'gog', 'boundary': 'work:Example'}
SLACK = {'id': 'slack', 'service': 'slack', 'transport': 'maton', 'boundary': 'work:Example', 'user_id': 'UOWNER1'}
MONDAY = '2026-10-19T09:00:00+00:00'  # 11:00 in Brussels


def email(source_id, at, sent=False, sender='sam@client.example', thread='t1', title='Contract'):
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': at, 'title': title,
            'body': 'I’ll send the signed contract by Friday.', 'deleted': False,
            'metadata': {'from': 'owner@example.com' if sent else sender, 'to': sender if sent else 'owner@example.com',
                         'thread_id': thread, 'labels': ['SENT'] if sent else ['INBOX']}}


class Promises(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        zone = patch.object(a, 'LOCAL_TZ', ZoneInfo('Europe/Brussels'))
        zone.start()
        self.addCleanup(zone.stop)
        self.clock = patch.object(a, 'now', return_value=MONDAY)
        self.clock.start()
        self.addCleanup(lambda: self.clock.stop())  # at() swaps the patcher: stop whichever one runs last, or now() stays faked for later tests
        self.store = a.Store(Path(self.tmp.name) / 'events.sqlite')
        self.store.owner_user_ids = {'UOWNER1'}
        self.store.commit_page('work', {}, [email('theirs', '2026-10-19T08:00:00+00:00'),
                                            email('mine', '2026-10-19T08:30:00+00:00', sent=True, title='Re: Deck')])
        with self.store.connect() as db:
            self.theirs, self.mine = [r[0] for r in db.execute('SELECT id FROM events ORDER BY source_id DESC')]

    def at(self, stamp):
        self.clock.stop()
        self.clock = patch.object(a, 'now', return_value=stamp)
        self.clock.start()

    def app(self):
        app = object.__new__(a.App)
        app.store = self.store
        app.config = {'accounts': [GMAIL, SLACK], 'digest_at': '08:30', 'review_worker': {'enabled': True}}
        return app

    def record(self, **args):
        return self.app().action('promise', {'direction': 'theirs', 'event_id': self.theirs, 'who': 'Sam Lee',
                                             'what': 'send the signed contract', **args})

    def row(self, promise_id):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM promises WHERE id=?', (promise_id,)).fetchone())

    def test_a_promise_is_recorded_once_without_a_card(self):
        saved = self.record(due='2026-10-23')
        self.assertEqual(saved['status'], 'open')
        self.assertTrue(self.record(due='2026-10-23')['duplicate'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM promises').fetchone()[0], 1)

    def test_a_plan_made_together_is_one_promise(self):
        # "Let's go over the deck Sunday" from them, and the yes to it from the owner: the same plan once
        saved = self.record(what='go over the deck together on Sunday', due='2026-10-25')
        again = self.record(direction='mine', event_id=self.mine, who='sam lee', what='Go over the deck  together on Sunday', due='2026-10-25')
        self.assertEqual((again['id'], again['duplicate']), (saved['id'], True))
        # Both sides said it: one plan, kept as the owner's, checked as theirs
        self.assertEqual({k: self.row(saved['id'])[k] for k in ('direction', 'together')}, {'direction': 'mine', 'together': 1})
        self.assertIn('you with Sam Lee: go over the deck', self.store.promises_text())
        # Something else with them, or the same words with someone else, is its own promise
        self.assertEqual(self.record(direction='mine', event_id=self.mine, what='send the deck')['status'], 'open')
        self.assertEqual(self.record(who='Alex Kim', what='go over the deck together on Sunday')['status'], 'open')
        # Once the first is closed, a new one can be recorded
        self.app().action('promise', {'id': saved['id'], 'status': 'kept', 'reason': 'They went over it'})
        self.assertNotIn('duplicate', self.record(event_id=self.mine, what='go over the deck together on Sunday'))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM promises').fetchone()[0], 4)

    def test_the_same_promise_stored_twice_is_kept_once(self):
        # Rows from before promise() caught these: both sides of one plan, and one said twice
        with self.store.connect() as db:
            for direction, event, who, what in [('theirs', self.theirs, 'Sam Lee', 'plan the move on Sunday'), ('mine', self.mine, 'sam lee', 'Plan the move  on Sunday'),
                                                ('theirs', self.theirs, 'Alex Kim', 'send the photos'), ('theirs', self.mine, 'Alex Kim', 'send the photos'),
                                                ('mine', self.mine, 'Sam Lee', 'send the deck')]:
                db.execute("INSERT INTO promises(direction,who,what,due,check_at,event_id,account_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                           (direction, who, what, '2026-10-25', MONDAY, event, 'work', MONDAY, MONDAY))
        with self.store.connect() as db:
            self.store.settle_promises(db)
        rows = [r for r in self.store.promises() if r['state'] == 'open']
        self.assertEqual(sorted((r['who'], r['direction'], r['together']) for r in rows),
                         [('Alex Kim', 'theirs', 0), ('Sam Lee', 'mine', 0), ('sam lee', 'mine', 1)])
        self.assertEqual([(self.row(i)['status'], self.row(i)['note']) for i in (1, 4)],
                         [('dropped', 'The same as promise 2'), ('dropped', 'The same as promise 3')])

    def test_direction_must_match_who_wrote_the_message(self):
        with self.assertRaisesRegex(ValueError, 'messages they sent'):
            self.record(direction='mine')
        # Someone else's promise can sit in the owner's own ask that they took on.
        self.assertEqual(self.record(event_id=self.mine, what='make an issue for it')['status'], 'open')
        with self.assertRaisesRegex(ValueError, 'direction'):
            self.record(direction='ours')
        with self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
            self.record(due='Friday')
        self.assertEqual(self.record(direction='mine', event_id=self.mine, what='send the deck')['status'], 'open')

    def test_when_each_kind_is_checked(self):
        check = lambda **args: self.row(self.app().action('promise', {'event_id': self.mine, 'direction': 'mine', 'who': args.pop('who'),
                                                                       'what': 'send the deck', **args})['id'])['check_at']
        # Mine: the morning it's due (08:30 Brussels); due today, three hours from now at the earliest.
        self.assertEqual(check(who='Ann', due='2026-10-23'), '2026-10-23T06:30:00+00:00')
        self.assertEqual(check(who='Bo', due='2026-10-19'), '2026-10-19T12:00:00+00:00')
        self.assertEqual(check(who='Cy'), '2026-10-22T06:30:00+00:00')
        # Theirs: the morning after their date; without one, a week after they said it.
        self.assertEqual(self.row(self.record(due='2026-10-23')['id'])['check_at'], '2026-10-24T06:30:00+00:00')
        self.assertEqual(self.row(self.record(who='Kim')['id'])['check_at'], '2026-10-26T07:30:00+00:00')  # winter time

    def test_their_promise_closes_itself_when_they_write_again(self):
        promise = self.record(due='2026-10-23')['id']
        self.store.commit_page('work', {}, [email('reply', '2026-10-20T10:00:00+00:00', title='Signed contract')])
        self.at('2026-10-24T07:00:00+00:00')
        self.app().promise_step()
        row = self.row(promise)
        self.assertEqual(row['status'], 'answered')
        self.assertIn('Signed contract', row['note'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 0)

    def test_a_chat_promise_stays_open_when_they_write_again(self):
        chat = lambda source_id, at, mine=False: {'account_id': 'work', 'source_id': source_id, 'kind': 'whatsapp.message', 'occurred_at': at,
                                                  'title': 'Pat', 'body': 'sure' if mine else 'Can you check both paints?', 'deleted': False,
                                                  'metadata': {'chat_id': 'pat@s.whatsapp.net', 'from_me': mine, 'sender_id': None if mine else 'pat@s.whatsapp.net'}}
        self.store.commit_page('work', {}, [chat('ask', '2026-10-19T08:40:00+00:00'), chat('yes', '2026-10-19T08:41:00+00:00', mine=True)])
        with self.store.connect() as db:
            ask, yes = [r[0] for r in db.execute("SELECT id FROM events WHERE source_id IN ('ask','yes') ORDER BY source_id")]
        # A bare "sure" to a request is the owner's promise; the request says what.
        mine = self.app().action('promise', {'direction': 'mine', 'event_id': yes, 'who': 'Pat', 'what': 'check both paints'})['id']
        theirs = self.app().action('promise', {'direction': 'theirs', 'event_id': ask, 'who': 'Pat', 'what': 'send the paint colours'})['id']
        self.assertIsNone(self.row(theirs)['watch'])
        self.store.commit_page('work', {}, [chat('later', '2026-10-20T10:00:00+00:00')])
        self.app().promise_step()
        self.assertEqual((self.row(mine)['status'], self.row(theirs)['status']), ('open', 'open'))

    def test_an_open_promise_becomes_a_prepared_card_when_due(self):
        theirs = self.record(due='2026-10-23')['id']
        mine = self.app().action('promise', {'direction': 'mine', 'event_id': self.mine, 'who': 'Alex', 'what': 'send the Q3 deck', 'due': '2026-10-21'})['id']
        self.at('2026-10-20T07:00:00+00:00')
        self.app().promise_step()
        self.assertEqual(self.row(mine)['status'], 'open')
        self.at('2026-10-21T07:00:00+00:00')
        self.app().promise_step()
        card = self.store.snapshot([])['suggestions'][0]
        self.assertEqual((card['title'], card['due'], card['dedupe_key']), ('Send the Q3 deck for Alex', '2026-10-21', f'promise:{mine}'))
        self.assertIn('You told Alex on Monday 19 Oct you’d send the Q3 deck by **Wednesday 21 Oct**', card['body'])
        self.assertEqual((card['status'], card['early']), ('accepted', 1))  # prepared ahead
        self.assertEqual(self.row(mine)['suggestion_id'], card['id'])
        self.at('2026-10-24T07:00:00+00:00')
        self.app().promise_step()
        waiting = next(s for s in self.store.snapshot([])['suggestions'] if s['dedupe_key'] == f'promise:{theirs}')
        self.assertEqual(waiting['title'], 'Waiting on Sam Lee: send the signed contract')
        self.assertIsNotNone(waiting['awaiting'])  # a late reply still closes the card
        self.app().promise_step()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 2)

    def test_reviews_off_a_due_promise_waits_without_a_card(self):
        # A new install's first look records promises; with reviews off none becomes a card or gets prepared.
        mine = self.app().action('promise', {'direction': 'mine', 'event_id': self.mine, 'who': 'Alex', 'what': 'send the Q3 deck', 'due': '2026-10-21'})['id']
        self.at('2026-10-21T07:00:00+00:00')
        off = self.app()
        off.config['review_worker'] = {'enabled': False}
        off.promise_step()
        self.assertEqual(self.row(mine)['status'], 'open')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM preparations').fetchone()[0], 0)
        self.app().promise_step()  # reviews on: it comes
        self.assertEqual(self.row(mine)['status'], 'carded')

    def test_cards_from_promises_are_capped_per_day(self):
        for n in range(7):
            self.record(who=f'Person {n}')
        self.at('2026-10-27T07:00:00+00:00')
        for _ in range(4):
            self.app().promise_step()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM promises WHERE status='carded'").fetchone()[0], a.PROMISE_CARDS_PER_DAY)

    def test_a_review_closes_or_moves_an_open_promise(self):
        promise = self.record(due='2026-10-23')['id']
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.app().action('promise', {'id': promise, 'status': 'kept'})
        moved = self.app().action('promise', {'id': promise, 'due': '2026-10-30', 'reason': 'Sam asked for another week'})
        self.assertEqual(moved['check_at'], '2026-10-31T07:30:00+00:00')  # winter time by then
        self.app().action('promise', {'id': promise, 'status': 'kept', 'reason': 'The contract arrived on Slack'})
        self.assertEqual(self.row(promise)['status'], 'kept')
        with self.assertRaisesRegex(ValueError, 'no longer open'):
            self.app().action('promise', {'id': promise, 'status': 'dropped', 'reason': 'x'})

    def test_only_the_owner_closes_from_the_dashboard(self):
        promise = self.record()['id']
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app().action('promise_close', {'id': promise, 'status': 'kept'})
        self.app().action('promise_close', {'id': promise, 'status': 'dropped'}, owner_dashboard=True)
        self.assertEqual(self.row(promise)['status'], 'dropped')
        self.assertIn('promise_close', a.OWNER_ACTIONS)

    def test_the_list_follows_the_card(self):
        promise = self.record(due='2026-10-20')['id']
        self.at('2026-10-21T07:00:00+00:00')
        self.app().promise_step()
        listed = {p['id']: p for p in self.store.snapshot([])['promises']}
        self.assertEqual(listed[promise]['state'], 'card')
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='completed'")
        self.assertEqual(self.store.promises()[0]['state'], 'kept')

    def test_reviews_see_the_open_promises_and_how_to_record_them(self):
        self.record(due='2026-10-23')
        self.app().action('promise', {'direction': 'mine', 'event_id': self.mine, 'who': 'Alex', 'what': 'send the Q3 deck'})
        text = self.store.promises_text()
        self.assertIn('Sam Lee → you: send the signed contract (due Fri 23 Oct; said Mon 19 Oct', text)
        self.assertIn('you → Alex: send the Q3 deck (no date;', text)
        prompt = a.review_prompt('run', ['work'], promises=text)
        self.assertIn('record it with promise', prompt)
        self.assertIn('Open promises (the owner\'s and those made to them):\n- #', prompt)
        self.assertNotIn('Open promises', a.review_prompt('run', ['work']))

    def test_weekly_recap_leaves_out_settled_and_undated_promises(self):
        promise = self.record(due='2026-10-16')['id']
        self.app().action('promise', {'direction': 'mine', 'event_id': self.mine, 'who': 'Alex', 'what': 'send the Q3 deck'})
        self.app().action('promise_close', {'id': promise, 'status': 'kept'}, owner_dashboard=True)
        recap = a.weekly_recap(self.store, {'accounts': [GMAIL]}, a.dt.datetime.fromisoformat('2026-10-23T15:00:00+00:00'))
        self.assertNotIn('Sam Lee', recap)  # kept: nothing left to do
        self.assertNotIn('Alex', recap)  # open, but without a date it isn't due yet

    def test_cli_lists_open_promises(self):
        self.record(due='2026-10-23')
        out = io.StringIO()
        with patch.object(cli, 'load_config', return_value={}), patch.object(cli, 'call', return_value={'promises': self.store.promises()}), redirect_stdout(out):
            cli.main(['promises'])
        self.assertIn('Promised to you (1)\n  #1  Sam Lee: send the signed contract · due Fri 23 Oct', out.getvalue())


if __name__ == '__main__':
    unittest.main()
