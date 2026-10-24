"""Things that slipped: unanswered emails offered once to a review that saves. No live calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_ux as ux
a = ux.a

NOW = '2026-10-16T10:00:00+00:00'
ACCOUNTS = [{'id': 'work', 'email': 'owner@example.com', 'service': 'gmail', 'transport': 'gog', 'boundary': 'work:Example'}]


def mail(source_id, at, sender, to, title, body, thread, labels=('INBOX',)):
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': at, 'title': title, 'body': body,
            'deleted': False, 'metadata': {'from': sender, 'to': to, 'thread_id': thread, 'labels': list(labels)}}


def sent(source_id, at, to, title, body, thread):
    return mail(source_id, at, 'Owner <owner@example.com>', to, title, body, thread, ('SENT',))


class Slipped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = a.Store(Path(self.tmp.name) / 'events.sqlite')
        self.store.accounts = ACCOUNTS
        self.store.commit_page('work', {}, [
            # Sam has written before, so this isn't outreach.
            mail('h1', '2026-09-01T09:00:00+00:00', 'Sam <sam@client.example>', 'owner@example.com', 'Hello', 'Nice meeting you.', 't0'),
            # The owner's question to Sam, unanswered for 8 days: offered.
            sent('s1', '2026-10-08T09:00:00+00:00', 'Sam <sam@client.example>', 'Contract', 'Can you send the signed contract?', 't1'),
            # Answered: not offered.
            sent('s2', '2026-10-08T09:00:00+00:00', 'ann@client.example', 'Pricing', 'Does Tuesday work?', 't2'),
            mail('r2', '2026-10-09T09:00:00+00:00', 'Ann <ann@client.example>', 'owner@example.com', 'Re: Pricing', 'Yes.', 't2'),
            # No question, too recent, too old, or to a robot: not offered.
            sent('s3', '2026-10-08T09:00:00+00:00', 'bob@client.example', 'Thanks', 'Thanks for today.', 't3'),
            sent('s4', '2026-10-14T09:00:00+00:00', 'bob@client.example', 'Agenda', 'Shall we meet?', 't4'),
            sent('s5', '2026-09-20T09:00:00+00:00', 'bob@client.example', 'Old', 'Any news?', 't5'),
            sent('s6', '2026-10-08T09:00:00+00:00', 'noreply@vendor.example', 'Ticket', 'Why was I charged?', 't6'),
            # A question to the owner from someone they write with, unanswered: offered. Quoted questions don't count.
            mail('q1', '2026-10-12T09:00:00+00:00', 'Sam <sam@client.example>', 'owner@example.com', 'Invoice', 'Could you check the invoice?', 't7'),
            mail('q2', '2026-10-12T09:00:00+00:00', 'Sam <sam@client.example>', 'owner@example.com', 'Update', 'Here it is.\n> Can you send it?', 't8'),
            # A stranger, a newsletter-style update and a list mail: not offered.
            mail('q3', '2026-10-12T09:00:00+00:00', 'pitch@stranger.example', 'owner@example.com', 'Quick question', 'Got 5 minutes?', 't9'),
            mail('q4', '2026-10-12T09:00:00+00:00', 'Sam <sam@client.example>', 'owner@example.com', 'Docs', 'New docs?', 't10', ('CATEGORY_UPDATES',)),
            mail('q5', '2026-10-12T09:00:00+00:00', 'Sam <sam@client.example>', 'a@x.example, b@x.example, owner@example.com', 'All', 'Thoughts?', 't11'),
        ])

    def ids(self, *source_ids):
        with self.store.connect() as db:
            return [db.execute('SELECT id FROM events WHERE source_id=?', (s,)).fetchone()[0] for s in source_ids]

    def test_candidates_are_real_unanswered_questions(self):
        with patch.object(a, 'now', return_value=NOW):
            text = self.store.slipped('run-1')
        s1, q1 = self.ids('s1', 'q1')
        self.assertEqual(text.splitlines(), [
            f'- No answer for 8 days: the owner emailed sam@client.example "Contract" on Thu, Oct 8 (work, event {s1}).',
            f'- Waiting 4 days on the owner: sam@client.example asked "Invoice" on Mon, Oct 12 (work, event {q1}).'])

    def test_offered_once_to_a_review_that_saves(self):
        with patch.object(a, 'now', return_value=NOW):
            self.assertTrue(self.store.slipped('run-1'))
            self.assertEqual(self.store.slipped('run-2'), '')  # just offered
        later = '2026-10-16T18:00:00+00:00'
        with patch.object(a, 'now', return_value=later):
            self.assertEqual(len(self.store.slipped('run-3').splitlines()), 2)  # run-1 never saved: offered again
            with self.store.connect() as db:
                db.execute("INSERT INTO analyses(id,started_at,items,status,owner_run) VALUES('x',?,'[]','complete','run-3')", (later,))
        with patch.object(a, 'now', return_value='2026-10-17T10:00:00+00:00'):
            self.assertEqual(self.store.slipped('run-4'), '')
        # Something already on a card isn't offered at all.
        self.store.commit_page('work', {}, [sent('s7', '2026-10-09T09:00:00+00:00', 'kim@client.example', 'Demo', 'Free on Friday?', 't12')])
        [s7] = self.ids('s7')
        self.store.suggest('demo-kim', 'Follow up with Kim', 'Kim hasn’t answered.', [s7])
        with patch.object(a, 'now', return_value='2026-10-17T10:00:00+00:00'):
            self.assertEqual(self.store.slipped('run-5'), '')

    def test_outreach_is_left_out(self):
        # A batch to people who know the owner, cold emails, and questions only in a link: none are offered.
        known = [mail(f'k{i}', '2026-09-01T09:00:00+00:00', f'ceo{i}@co{i}.example', 'owner@example.com', 'Hi', 'Hello', f'tk{i}') for i in range(4)]
        self.store.commit_page('work', {}, known + [
            *[sent(f'b{i}', '2026-10-12T08:00:00+00:00', f'ceo{i}@co{i}.example', f'Re: Example / Company {i}', 'Would you be open to a call?', f'tb{i}') for i in range(4)],
            sent('c1', '2026-10-09T09:00:00+00:00', 'lee@prospect.example', 'Prospect x Example', 'Open to a partnership?', 'tc1'),
            sent('c2', '2026-10-10T09:00:00+00:00', 'lee@prospect.example', 'Re: Prospect x Example', 'Any thoughts?', 'tc1'),
            sent('u1', '2026-10-09T09:00:00+00:00', 'ceo0@co0.example', 'Thanks', 'Thanks!\n<https://track.example/s/?u=tel:123>', 'tu1')])
        # Someone who pitched the owner and got a question back is a conversation, not their outreach.
        self.store.commit_page('work', {}, [mail('p1', '2026-10-07T09:00:00+00:00', 'Pat <pat@vendor.example>', 'owner@example.com', 'Collab', 'Shall we work together?', 'tp1'),
                                            sent('p2', '2026-10-08T09:00:00+00:00', 'pat@vendor.example', 'Re: Collab', 'What do you have in mind?', 'tp1')])
        with patch.object(a, 'now', return_value=NOW):
            text = self.store.slipped('run-1')
        self.assertNotIn('ceo', text)
        self.assertNotIn('lee@prospect.example', text)
        self.assertIn('emailed pat@vendor.example "Re: Collab"', text)
        self.assertIn('emailed sam@client.example "Contract"', text)

    def test_review_prompt_section(self):
        self.assertNotIn('Possibly slipped', a.review_prompt('r', ['work']))
        prompt = a.review_prompt('r', ['work'], slipped='- No answer for 8 days: …')
        self.assertIn('Possibly slipped. Each is shown once.', prompt)
        self.assertIn('- No answer for 8 days: …', prompt)


if __name__ == '__main__':
    unittest.main()
