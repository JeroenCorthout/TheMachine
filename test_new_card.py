"""New card: a card the owner makes themselves is a normal card, made through the same Store.suggest() as the review's,
with origin 'owner' and their words kept. The review fills it in but never dismisses it, folds it into another card or
rewrites their words; a same-matter card is offered as a merge the owner decides. Creating one sends nothing. Old
cards and an upgrade from the schema before this change are unaffected."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('newcard_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)

ACCOUNTS = [{'id': 'w', 'boundary': 'work:acme.example'}, {'id': 'p', 'boundary': 'personal:pat@home.example'}]


def mail(source_id, thread='t1', sender='Sam Lee <sam@client.example>', when='2026-10-20T09:00:00+00:00', title='Contract'):
    return {'account_id': 'w', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': when, 'title': title,
            'body': 'Could you send the signed contract?', 'metadata': {'from': sender, 'to': 'pat@acme.example', 'thread_id': thread},
            'deleted': False}


class Daytime(a.dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 21, 10, 0, tzinfo=tz or a.dt.timezone.utc)


class NewCard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'e.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.config = {'accounts': [dict(x) for x in ACCOUNTS], 'prepare_ahead': True}

    def mail_id(self, source_id):
        with self.store.connect() as db:
            return db.execute('SELECT id FROM events WHERE source_id=?', (source_id,)).fetchone()[0]

    def new(self, **args):
        return self.app.action('card_new', {'text': 'Send Pat the signed contract', **args}, owner_dashboard=True)

    def review(self, **args):
        return self.app.action('suggest', {'dedupe_key': 'w:contract', 'title': 'Send the signed contract to Sam',
                                           'body': 'Sam asked for it on Monday. I can draft the email.', 'when': 'now', **args})

    def row(self, sid):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM suggestions WHERE id=?', (sid,)).fetchone())

    def delivery(self, sid):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM deliveries WHERE suggestion_id=?', (sid,)).fetchone())

    def test_a_new_card_goes_through_the_same_suggest_as_the_reviews(self):
        real = a.Store.suggest
        with patch.object(a.Store, 'suggest', autospec=True, side_effect=real) as suggest:
            made = self.new(scope='work', due='2026-10-30')
        suggest.assert_called_once()
        card = self.row(made['id'])
        self.assertEqual((card['origin'], card['owner_words'], card['title'], card['status'], card['due']),
                         ('owner', 'Send Pat the signed contract', 'Send Pat the signed contract', 'pending', '2026-10-30'))
        self.assertEqual(json.loads(card['account_ids']), ['w'])
        # Its evidence is the owner's own signal, left for the next review, on the chosen account
        with self.store.connect() as db:
            event = dict(db.execute('SELECT * FROM events WHERE id=?', (made['event_id'],)).fetchone())
        self.assertEqual((event['kind'], event['account_id'], event['analyzed_version'], event['source_id']), ('owner.card', 'w', 0, 'owner:' + made['id']))
        self.assertEqual(json.loads(event['metadata'])['card_id'], made['id'])
        self.assertEqual(json.loads(card['event_ids']), [made['event_id']])
        # A normal card in every list: the dashboard shows it with its scope, and it can be snoozed and done like any
        self.store.accounts = self.app.config['accounts']
        snapshot = self.store.snapshot([])
        shown = next(s for s in snapshot['suggestions'] if s['id'] == made['id'])
        self.assertEqual((shown['origin'], shown['scope']), ('owner', 'work'))
        self.store.decide(made['id'], 'snooze', '', 'd-snooze', 'dashboard', snooze_option='1w')
        self.assertEqual(self.row(made['id'])['status'], 'snoozed')
        self.store.decide(made['id'], 'complete', '', 'd-done', 'dashboard')
        self.assertEqual(self.row(made['id'])['status'], 'completed')

    def test_scope_and_attachments_choose_the_account(self):
        self.assertEqual(json.loads(self.row(self.new(scope='personal')['id'])['account_ids']), ['p'])
        self.store.commit_page('w', {}, [mail('m1')])
        attached = self.new(text='Answer Sam', attach=[self.mail_id('m1')])
        card = self.row(attached['id'])
        self.assertEqual(json.loads(card['event_ids']), [attached['event_id'], self.mail_id('m1')])
        self.assertEqual(json.loads(card['account_ids']), ['w'])
        self.assertRegex(card['source_line'], r'^Source: email from Sam Lee')
        with self.assertRaisesRegex(ValueError, 'no longer there'):
            self.new(attach=[999])
        with self.assertRaisesRegex(ValueError, 'what needs doing'):
            self.new(text='   ')
        with self.assertRaisesRegex(ValueError, 'under 2000'):
            self.new(text='x' * 2001)

    def test_only_the_owner_on_the_dashboard_makes_renames_or_merges_cards(self):
        for name in ('card_new', 'card_rename', 'card_merge'):
            self.assertIn(name, a.OWNER_ACTIONS)
            with self.assertRaisesRegex(ValueError, 'by the owner, on the dashboard'):
                self.app.action(name, {'text': 'x', 'suggestion_id': 'x', 'title': 'x', 'decision_id': 'x'})

    def test_a_long_text_keeps_every_word_and_gets_a_short_title(self):
        text = 'Book the venue for the Acme offsite in March, ' + 'with room for forty people and a quiet corner for calls, ' * 4 + 'and ask Sam.\nBudget is tight.'
        card = self.row(self.new(text=text)['id'])
        self.assertLessEqual(len(card['title']), 90); self.assertTrue(card['title'].endswith('…'))
        self.assertEqual(card['owner_words'], text)
        self.assertLessEqual(len(card['body']), 700)

    def test_the_review_fills_it_in_and_keeps_their_words_where_they_put_it(self):
        made = self.new(due='2026-10-30')
        self.store.decide(made['id'], 'snooze', '', 'd1', 'dashboard', snooze_option='1w')
        filled = self.review(event_ids=[made['event_id']], due='2026-11-15', project='Acme renewal')
        self.assertEqual(filled['id'], made['id']); self.assertTrue(filled['owner_card'])
        card = self.row(made['id'])
        self.assertEqual(card['title'], 'Send the signed contract to Sam')
        self.assertEqual(card['body'], 'Sam asked for it on Monday. I can draft the email.')
        self.assertEqual(card['owner_words'], 'Send Pat the signed contract')  # their words stay
        self.assertEqual(card['due'], '2026-10-30')  # their date stands
        self.assertEqual(card['status'], 'snoozed')  # their own signal isn't news: it stays snoozed
        self.assertEqual(card['project'], 'Acme renewal')
        self.assertEqual(self.delivery(made['id'])['status'], 'not_sent')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 1)

    def test_separate_cannot_split_the_owners_signal_off_into_a_second_card(self):
        made = self.new()
        again = self.review(event_ids=[made['event_id']], separate=True, dedupe_key='other')
        self.assertEqual(again['id'], made['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM suggestions').fetchone()[0], 1)

    def test_the_review_never_dismisses_an_owner_card(self):
        made = self.new()
        self.review(event_ids=[made['event_id']])
        self.store.looks_handled(made['id'], 'Sam e-signed it on Tuesday', [made['event_id']])
        self.assertEqual(self.store.settle_faults('9.9'), [])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self.store.settle_awaiting(db); self.store.settle_ahead(db)
        card = self.row(made['id'])
        self.assertEqual(card['status'], 'pending')  # a hint at most; only the owner closes it
        with self.assertRaisesRegex(ValueError, 'owner'):
            self.app.action('decide', {'suggestion_id': made['id'], 'choice': 'reject', 'decision_id': 'agent-1'})

    def test_a_same_matter_card_is_offered_as_a_merge_never_merged_silently(self):
        self.store.commit_page('w', {}, [mail('m1')])
        other = self.review(event_ids=[1], dedupe_key='w:sam')
        made = self.new(text='Contract for Sam')
        offered = self.review(event_ids=[made['event_id']], card_id=other['id'])
        self.assertTrue(offered['merge_offered']); self.assertEqual(offered['id'], made['id'])
        mine, theirs = self.row(made['id']), self.row(other['id'])
        self.assertEqual((mine['status'], theirs['status']), ('pending', 'pending'))
        self.assertEqual(json.loads(mine['merge_offer'])['card_id'], other['id'])
        self.assertEqual(json.loads(theirs['event_ids']), [1])  # nothing moved
        # Keep both: the question stops for that pair, and the review is told so
        self.assertFalse(self.app.action('card_merge', {'suggestion_id': made['id'], 'merge': False, 'decision_id': 'k1'}, owner_dashboard=True)['merged'])
        self.assertFalse(self.review(event_ids=[made['event_id']], card_id=other['id'])['merge_offered'])
        self.assertTrue(json.loads(self.row(made['id'])['merge_offer'])['kept'])

    def test_merge_folds_the_owner_card_into_the_other_and_carries_their_words(self):
        self.store.commit_page('w', {}, [mail('m1')])
        other = self.review(event_ids=[1], dedupe_key='w:sam')
        made = self.new(text='Contract for Sam', due='2026-10-28')
        self.review(event_ids=[made['event_id']], card_id=other['id'])
        done = self.app.action('card_merge', {'suggestion_id': made['id'], 'merge': True, 'decision_id': 'm1'}, owner_dashboard=True)
        self.assertEqual(done, {'id': other['id'], 'merged': True, 'from': made['id']})
        kept, gone = self.row(other['id']), self.row(made['id'])
        self.assertEqual((kept['origin'], kept['owner_words'], kept['due'], kept['status']), ('owner', 'Contract for Sam', '2026-10-28', 'pending'))
        self.assertEqual(json.loads(kept['event_ids']), [1, made['event_id']])
        self.assertEqual(gone['status'], 'rejected')
        # The owner's signal now fills in the card it went into
        self.assertEqual(self.review(event_ids=[made['event_id']], dedupe_key='again')['id'], other['id'])
        chat = self.app.card_chat(made['id'])['items']
        self.assertIn('Merged into the other card', [i['text'] for i in chat if i['kind'] == 'event'])

    def test_creating_a_card_sends_nothing(self):
        self.store.commit_page('w', {}, [mail('m1')])
        made = self.new(attach=[1], scope='work')
        self.review(event_ids=[made['event_id']])
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram') as telegram, patch.object(a.subprocess, 'run') as run:
            a.deliver(self.store, {'telegram_owner': 'owner', 'accounts': ACCOUNTS})
        telegram.assert_not_called(); run.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0], 0)
            self.assertEqual(self.delivery(made['id'])['status'], 'not_sent')
        self.assertIsNone(self.row(made['id'])['deliver_after'])

    def test_a_draft_prepared_ahead_arrives_ready_like_any_card(self):
        made = self.new()
        filled = self.review(event_ids=[made['event_id']], prepare_ahead=True)
        self.assertTrue(filled['prepared_ahead'])
        self.assertEqual((self.row(made['id'])['status'], self.row(made['id'])['early']), ('accepted', 1))
        self.assertEqual(self.delivery(made['id'])['status'], 'queued')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM preparations WHERE suggestion_id=?', (made['id'],)).fetchone()[0], 'queued')

    def test_a_later_message_is_news_for_the_owner_card(self):
        self.store.commit_page('w', {}, [mail('m1')])
        made = self.new(attach=[1])
        self.store.decide(made['id'], 'snooze', '', 'd1', 'dashboard', snooze_option='1w')
        self.store.commit_page('w', {}, [mail('m2', when='2099-01-01T09:00:00+00:00', title='Re: Contract')])
        back = self.review(event_ids=[self.mail_id('m2')], dedupe_key='w:reply')
        self.assertEqual(back['id'], made['id']); self.assertTrue(back.get('brought_back'))
        self.assertEqual(self.row(made['id'])['owner_words'], 'Send Pat the signed contract')

    def test_the_chat_opens_with_their_words_and_the_card_shows_whom_it_is_about(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO graph_nodes(id,kind,name,strength,signals,info) VALUES('email:pat@acme.example','person','Pat Example',1,1,'{}'),"
                       "('org:acme.example','org','Acme',1,1,'{}'),('project:1','project','Acme renewal',1,1,'{}')")
            db.execute("INSERT INTO projects(id,name,created_at) VALUES(1,'Acme renewal','2026-10-01')")
        made = self.new(about=['email:pat@acme.example', 'org:acme.example', 'project:1'])
        card = self.row(made['id'])
        self.assertEqual(card['project'], 'Acme renewal')
        with self.store.connect() as db:
            meta = json.loads(db.execute('SELECT metadata FROM events WHERE id=?', (made['event_id'],)).fetchone()[0])
            self.assertEqual([x['id'] for x in meta['about']], ['email:pat@acme.example', 'org:acme.example'])
            a.graph.build(db, self.app.config, a.now())
            people, _ = a.graph.card_people(db, [made['event_id']])
            self.assertEqual([p['id'] for p in people], ['email:pat@acme.example'])
            self.assertIn(made['id'], [c['id'] for c in a.graph.open_between(db, 'org:acme.example').get('cards', [])])
        first = self.app.card_chat(made['id'])['items'][0]
        self.assertEqual((first['kind'], first['from'], first['mine'], first['text']), ('source', 'You', True, 'Send Pat the signed contract'))
        with self.assertRaisesRegex(ValueError, 'no longer in the Graph'):
            self.new(about=['email:nobody@acme.example'])

    def test_old_cards_are_unaffected(self):
        self.store.commit_page('w', {}, [mail('m1')])
        old = self.review(event_ids=[1], dedupe_key='w:sam')
        card = self.row(old['id'])
        self.assertEqual((card['origin'], card['owner_words'], card['title_suggested'], card['merge_offer']), (None, None, None, None))
        self.store.commit_page('w', {}, [mail('m2', when='2026-10-21T09:00:00+00:00', title='Re: Contract')])
        self.review(event_ids=[2], dedupe_key='w:sam2', title='Sam is waiting on the contract')
        self.assertEqual(self.row(old['id'])['title'], 'Sam is waiting on the contract')
        self.assertEqual(self.delivery(old['id'])['status'], 'queued')


class Upgrade(unittest.TestCase):
    def test_a_database_from_before_new_cards_upgrades_in_place(self):
        """The schema as it was: no origin, owner_words, title_suggested or merge_offer. Opening it adds them and
        leaves the cards as they were; then those cards work as before and new cards can be made."""
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'e.sqlite'
        store = a.Store(path, protect=False)
        store.commit_page('w', {}, [mail('m1')])
        sid = store.suggest('w:sam', 'Send Sam the contract', 'Sam asked for it.', [1])['id']
        with sqlite3.connect(path) as db:
            for column in ('origin', 'owner_words', 'title_suggested', 'merge_offer', 'title_source'):
                db.execute('ALTER TABLE suggestions DROP COLUMN ' + column)
            before = db.execute('SELECT * FROM suggestions').fetchall()
            columns_before = [r[1] for r in db.execute('PRAGMA table_info(suggestions)')]
        store = a.Store(path)
        with store.connect() as db:
            columns = [r[1] for r in db.execute('PRAGMA table_info(suggestions)')]
            self.assertEqual(columns[:len(columns_before)], columns_before)
            self.assertEqual(set(columns) - set(columns_before), {'origin', 'owner_words', 'title_suggested', 'merge_offer', 'title_source'})
            row = dict(db.execute('SELECT * FROM suggestions WHERE id=?', (sid,)).fetchone())
        self.assertEqual(tuple(row[c] for c in columns_before), tuple(before[0]))  # read through the app: opened if it sealed them
        self.assertEqual((row['title'], row['body'], row['origin']), ('Send Sam the contract', 'Sam asked for it.', None))
        app = object.__new__(a.App); app.store = store; app.config = {'accounts': ACCOUNTS}
        self.assertEqual(app.action('card_new', {'text': 'Call Pat'}, owner_dashboard=True)['status'], 'pending')
        self.assertTrue(app.action('card_rename', {'suggestion_id': sid, 'title': 'Contract', 'decision_id': 'r1'}, owner_dashboard=True)['renamed'])


if __name__ == '__main__':
    unittest.main()
