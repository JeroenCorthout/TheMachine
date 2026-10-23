"""Rename: the owner's title for a card is kept in `title`, so every surface (the dashboard, Telegram, the Fleet's
handoff, older clients) shows it, while the generated title waits in title_suggested. Reviews keep updating that one,
never theirs. Reset goes back to the suggested title. It works the same on cards the review made and cards the owner made."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rename_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)

import test_fleet_requests as tr  # noqa: E402
from test_fleet import mail as fleet_mail  # noqa: E402


def mail(source_id, when='2026-10-20T09:00:00+00:00'):
    return {'account_id': 'w', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': when, 'title': 'Contract',
            'body': 'Could you send it?', 'metadata': {'from': 'Sam Lee <sam@client.example>', 'thread_id': 't1'}, 'deleted': False}


class Daytime(a.dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 21, 10, 0, tzinfo=tz or a.dt.timezone.utc)


class Rename(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'e.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'w', 'boundary': 'work:acme.example'}], 'prepare_ahead': False}
        self.store.commit_page('w', {}, [mail('m1'), mail('m2', '2026-10-21T09:00:00+00:00')])
        self.sid = self.review(event_ids=[1])['id']

    def review(self, **args):
        return self.app.action('suggest', {'dedupe_key': 'w:t1', 'title': 'Send the signed contract to Sam',
                                           'body': 'Sam asked on Monday.', 'when': 'now', **args})

    def rename(self, title=None, reset=False, decision_id=None, sid=None):
        return self.app.action('card_rename', {'suggestion_id': sid or self.sid, 'title': title, 'reset': reset,
                                               'decision_id': decision_id or a.uuid.uuid4().hex}, owner_dashboard=True)

    def row(self, sid=None):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM suggestions WHERE id=?', (sid or self.sid,)).fetchone())

    def test_the_owners_title_persists_and_a_review_never_overwrites_it(self):
        saved = self.rename('  Contract   for Sam  ')
        self.assertEqual(saved, {'id': self.sid, 'title': 'Contract for Sam', 'renamed': True, 'title_suggested': 'Send the signed contract to Sam'})
        self.review(event_ids=[2], dedupe_key='w:t1:reply', title='Sam is still waiting on the contract', body='Sam wrote again.')
        card = self.row()
        self.assertEqual((card['title'], card['title_suggested'], card['body'], card['title_source']), ('Contract for Sam', 'Sam is still waiting on the contract', 'Sam wrote again.', 'user'))
        # A preparation updates the body and next step; the title stays
        self.store.decide(self.sid, 'accept', '', 'd-accept', 'dashboard')
        self.store.prepare(self.sid, 'Drafted the reply to Sam.', title='Reply to Sam is drafted')
        card = self.row()
        self.assertEqual((card['title'], card['title_suggested']), ('Contract for Sam', 'Reply to Sam is drafted'))  # Reset gets the latest

    def test_reset_goes_back_to_the_latest_suggested_title(self):
        self.rename('Contract for Sam')
        self.review(event_ids=[2], dedupe_key='w:t1:reply', title='Sam is still waiting')
        self.assertEqual(self.rename(reset=True), {'id': self.sid, 'title': 'Sam is still waiting', 'renamed': False})
        card = self.row()
        self.assertEqual((card['title'], card['title_suggested'], card['title_source']), ('Sam is still waiting', None, 'auto'))
        # Typing the suggested title again is the same as reset; and reviews write the title again
        self.rename('Mine'); self.rename('Sam is still waiting')
        self.assertIsNone(self.row()['title_suggested'])
        self.assertTrue(self.rename(reset=True)['unchanged'])

    def test_empty_and_too_long_are_refused_and_markup_is_kept_as_text(self):
        with self.assertRaisesRegex(ValueError, 'Give the card a title'):
            self.rename('   ')
        with self.assertRaisesRegex(ValueError, 'under 200'):
            self.rename('x' * 201)
        self.assertEqual(self.row()['title'], 'Send the signed contract to Sam')
        self.assertEqual(self.rename('<img src=x onerror=alert(1)> & co')['title'], '<img src=x onerror=alert(1)> & co')
        self.assertEqual(len(self.rename('y' * 200)['title']), 200)

    def test_a_replayed_rename_is_saved_once_and_shows_as_one_quiet_line(self):
        self.rename('Contract for Sam', decision_id='r1')
        self.assertTrue(self.rename('Something else', decision_id='r1')['duplicate'])
        self.assertEqual(self.row()['title'], 'Contract for Sam')
        self.rename(reset=True, decision_id='r2')
        lines = [i['text'] for i in self.app.card_chat(self.sid)['items'] if i['kind'] == 'event']
        self.assertEqual(lines, ['Renamed', 'Back to the suggested title'])
        self.assertFalse(any(line.startswith('You') for line in lines))
        # Never a note on the card (latest_feedback): a rename has no words of its own
        self.store.accounts = self.app.config['accounts']
        shown = next(s for s in self.store.snapshot([])['suggestions'] if s['id'] == self.sid)
        self.assertIsNone(shown['latest_feedback'])

    def test_it_works_on_owner_cards_too(self):
        made = self.app.action('card_new', {'text': 'Send Pat the deck'}, owner_dashboard=True)
        self.rename('Deck for Pat', sid=made['id'])
        self.review(event_ids=[made['event_id']], dedupe_key='own', title='Send Pat the Q4 deck')
        card = self.row(made['id'])
        self.assertEqual((card['title'], card['title_suggested'], card['owner_words']), ('Deck for Pat', 'Send Pat the Q4 deck', 'Send Pat the deck'))

    def test_telegram_edits_its_card_to_the_new_title(self):
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET status='sent',message_id='77',needs_edit=0 WHERE suggestion_id=?", (self.sid,))
        self.rename('Contract for Sam')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT needs_edit FROM deliveries WHERE suggestion_id=?', (self.sid,)).fetchone()[0], 1)
        with patch.object(a.dt, 'datetime', Daytime), patch.object(a, 'telegram', return_value={'messageId': '77'}) as telegram:
            a.deliver(self.store, {'telegram_owner': 'owner', 'accounts': self.app.config['accounts']})
        edits = [c.args for c in telegram.call_args_list if c.args[2] == 'edit']
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0][1]['title'], 'Contract for Sam')
        self.assertIn('Contract for Sam', a.card(edits[0][1]))

    def test_an_older_client_reads_it_as_the_plain_title(self):
        self.rename('Contract for Sam')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT title FROM suggestions WHERE id=?', (self.sid,)).fetchone()[0], 'Contract for Sam')


class FleetTitle(unittest.TestCase):
    setUp = tr.Requests.setUp

    def test_a_handoff_carries_the_owners_title(self):
        self.store.commit_page('work', {}, [fleet_mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example')])
        sid = self.store.suggest('k-1', 'Send Sam the pricing', 'Sam asked for the pricing.', [1])['id']
        self.store.rename_card(sid, 'Pricing for Sam', decision_id='r1')
        self.assertEqual(self.fleet.handoff_draft(sid)['title'], 'Pricing for Sam')


if __name__ == '__main__':
    unittest.main()
