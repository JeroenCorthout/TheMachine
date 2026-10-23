"""A title The Machine wrote keeps describing where things stand: a review that brings a card up to date
and a preparation result can give it a new one. A title the owner chose (title_source 'user') is never replaced.
A prepared-ahead card that falls back to a normal suggestion goes out as the card, not as a "Reminder"."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('card_titles_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def mail(n, body):
    return {'account_id': 'work', 'source_id': f'm{n}', 'kind': 'gmail.message', 'occurred_at': f'2026-10-2{n}T09:00:00+00:00',
            'title': 'Bounty', 'body': body, 'deleted': False,
            'metadata': {'from': 'Sam <sam@example.com>', 'thread_id': 't1', 'labels': ['INBOX']}}


class CardTitles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [{'id': 'work', 'service': 'gmail', 'transport': 'gog', 'email': 'owner@example.com', 'boundary': 'work:Example'}]}
        self.store.commit_page('work', {}, [mail(1, 'Could you send the bounty by PayPal?'),
                                            mail(2, 'Thanks, it arrived. Could you send me a receipt?')])
        self.sid = self.store.suggest('bounty', 'Sam asks how to receive the bounty', 'Sam asks for PayPal. I can reply.', [1])['id']

    def card(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT title,title_source,body,status FROM suggestions WHERE id=?', (self.sid,)).fetchone())

    def update(self, title):
        return self.store.suggest('bounty-receipt', title, 'The bounty arrived and Sam asks for a receipt.', [2],
                                  card_id=self.sid, match=True)

    def user_titled(self, title='Pay the bounty'):
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET title=?,title_source='user' WHERE id=?", (title, self.sid))

    def notes(self):
        with self.store.connect() as db:
            return [r[0] for r in db.execute("SELECT feedback FROM decisions WHERE suggestion_id=? AND choice='update'", (self.sid,))]

    def accepted_job(self):
        self.store.decide(self.sid, 'accept', '', 'n1', 'dashboard')
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running'")
            return db.execute('SELECT job_id FROM preparations').fetchone()[0]

    def prepare(self, **extra):
        return self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': self.accepted_job(),
                                           'body': 'Your receipt reply is ready.', 'outcome': 'needs_you', 'needs': 'check the reply', **extra})

    def test_new_cards_have_an_automatic_title(self):
        self.assertEqual(self.card()['title_source'], 'auto')

    def test_a_review_update_gives_an_automatic_title_the_new_situation(self):
        self.update('Sam asks for a receipt for the bounty')
        self.assertEqual(self.card()['title'], 'Sam asks for a receipt for the bounty')
        self.assertIn('Before: Sam asks how to receive the bounty', self.notes()[-1])

    def test_an_unchanged_title_is_not_noted_as_a_change(self):
        self.update('Sam asks how to receive the bounty')
        self.assertNotIn('Before:', self.notes()[-1])

    def test_the_owners_own_title_survives_a_review_update(self):
        self.user_titled()
        self.update('Sam asks for a receipt for the bounty')
        card = self.card()
        self.assertEqual((card['title'], card['title_source']), ('Pay the bounty', 'user'))
        self.assertEqual(card['body'], 'The bounty arrived and Sam asks for a receipt.')
        self.assertNotIn('Before:', self.notes()[-1])

    def test_a_preparation_result_can_bring_the_title_up_to_date(self):
        self.prepare(title='  Send Sam   the bounty receipt ')
        self.assertEqual(self.card()['title'], 'Send Sam the bounty receipt')

    def test_a_preparation_without_a_title_keeps_the_title(self):
        self.prepare()
        self.assertEqual(self.card()['title'], 'Sam asks how to receive the bounty')

    def test_a_preparation_never_replaces_the_owners_own_title(self):
        self.user_titled()
        self.prepare(title='Send Sam the bounty receipt')
        self.assertEqual(self.card()['title'], 'Pay the bounty')

    def test_a_too_long_preparation_title_is_refused(self):
        with self.assertRaises(ValueError):
            self.prepare(title='x' * 91)

    def test_the_next_notification_carries_the_current_title(self):
        self.update('Sam asks for a receipt for the bounty')
        sent = []
        with self.store.connect() as db:
            db.execute("INSERT INTO push_subscriptions(endpoint,p256dh,auth,created_at) VALUES('https://push.example/1','k','a',?)", (a.now(),))
        row = {**self.card(), 'id': self.sid, 'reminder': 0}
        with patch.object(a, 'push_send', lambda store, config, subs, payload, label, sender=None: sent.append(payload)):
            a.push_notify(self.store, {'dashboard_host': 'machine.example'}, row)
        self.assertEqual(sent[0]['title'], 'Sam asks for a receipt for the bounty')

    def test_a_fallen_back_ahead_card_is_not_sent_as_a_reminder(self):
        # Reopened by a new signal and prepared ahead; the run got stuck, so the card goes out as a suggestion.
        # It had a notification before (message_id): the new one is the card itself, without "Reminder:".
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='accepted',early=1 WHERE id=?", (self.sid,))
            db.execute("INSERT INTO preparations(suggestion_id,decision_id,job_id,status,created_at,updated_at) VALUES(?,?,?,'blocked',?,?)",
                       (self.sid, 'ahead:x', 'job1', a.now(), a.now()))
            db.execute("UPDATE deliveries SET status='queued',message_id='41',reminder=1 WHERE suggestion_id=?", (self.sid,))
            self.store.settle_ahead(db)
            status = db.execute('SELECT status FROM suggestions WHERE id=?', (self.sid,)).fetchone()[0]
            reminder = db.execute('SELECT reminder FROM deliveries WHERE suggestion_id=?', (self.sid,)).fetchone()[0]
        self.assertEqual((status, reminder), ('pending', 0))


if __name__ == '__main__':
    unittest.main()
