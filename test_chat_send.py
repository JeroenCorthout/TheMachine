"""The card chat sends a draft only after the owner saw it: a draft written in the same turn is shown, never sent."""
import unittest
import test_next_actions as tn
import test_ux as ux
a = ux.a


class ChatSend(unittest.TestCase):
    setUp = ux.UX.setUp
    row = ux.UX.row
    app = tn.NextActions.app
    draft = tn.NextActions.draft

    def say(self, text, key):
        """The owner writes in the card's chat: a preparation turn starts for it."""
        self.store.decide(self.sid, 'other', text, key, 'dashboard')
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running'")
            return db.execute('SELECT job_id FROM preparations').fetchone()[0]

    def save(self, job, reply, send=False, **draft):
        args = {'suggestion_id': self.sid, 'job_id': job, 'body': reply, 'next_action': self.draft(**draft), 'outcome': 'needs_you', 'needs': 'check the draft and send it', 'note_kind': 'new_ask'}
        return self.app().action('prepare', {**args, **({'send_now': True} if send else {})})

    def sends(self):
        with self.store.connect() as db:
            return db.execute("SELECT count(*) FROM action_jobs").fetchone()[0]

    def test_a_draft_written_in_the_same_turn_is_shown_not_sent(self):
        job = self.say("Nope, it's done. We just email Pat now", 'chat-plan')
        with self.assertRaisesRegex(ValueError, 'Not sent: this draft is new'):
            self.save(job, 'Emailed Pat.', send=True)
        self.assertEqual(self.sends(), 0)
        self.assertIsNone(self.row()['next_action'])  # nothing saved by the refused call; the turn saves it to show
        self.save(job, 'The email to Pat is ready for you to check and send.')
        self.assertEqual(self.row()['next_action']['body'], 'Exact approved draft.')
        self.assertEqual(self.sends(), 0)
        self.assertIn('Send', [b['label'] for b in a.controls(self.row())['blocks'][0]['buttons']])

    def test_a_shown_draft_goes_out_after_the_owner_says_send_it(self):
        job = self.say('Email Pat at acme.example', 'chat-1')
        self.save(job, 'The email to Pat is ready.')
        job = self.say('Yes, send it', 'chat-2')
        saved = self.save(job, 'Sent it to Pat.', send=True)
        self.assertEqual(saved['send'], {'status': 'queued'})
        self.assertEqual(self.sends(), 1)

    def test_a_shown_draft_changed_in_the_next_turn_is_shown_again(self):
        job = self.say('Email Pat at acme.example', 'chat-1')
        self.save(job, 'The email to Pat is ready.')
        job = self.say('Make it warmer', 'chat-2')
        with self.assertRaisesRegex(ValueError, 'Not sent'):
            self.save(job, 'Warmer, and sent.', send=True, body='A warmer draft.')
        self.assertEqual(self.sends(), 0)

    def test_an_unrelated_message_after_a_shown_draft_sends_nothing(self):
        job = self.say('Email Pat at acme.example', 'chat-1')
        self.save(job, 'The email to Pat is ready.')
        job = self.say('When did Sam last write?', 'chat-2')
        self.app().action('prepare', {'suggestion_id': self.sid, 'job_id': job, 'body': 'Sam wrote on Tuesday.',
                                      'next_action': self.draft(), 'outcome': 'answered', 'note_kind': 'new_ask'})
        self.assertEqual(self.sends(), 0)

    def test_an_owner_edit_after_their_message_is_not_what_they_approved(self):
        job = self.say('Email Pat at acme.example', 'chat-1')
        self.save(job, 'The email to Pat is ready.')
        job = self.say('Yes, send it', 'chat-2')
        old = self.row()['next_action']
        # They change the text after writing "send it": the send waits for their own Send on the new text.
        with self.store.connect() as db:
            was = db.execute('SELECT status FROM suggestions').fetchone()[0]
            db.execute("UPDATE suggestions SET status='prepared'")
        new = self.app().action('revise_draft', {'suggestion_id': self.sid, 'revision': old['revision'], 'body': 'Their own words.'}, owner_dashboard=True)
        with self.store.connect() as db:
            db.execute('UPDATE suggestions SET status=?', (was,))
        with self.assertRaisesRegex(ValueError, 'Not sent'):
            self.save(job, 'Sent.', send=True, body='Their own words.')
        self.assertEqual(self.sends(), 0)
        self.assertNotEqual(new['revision'], old['revision'])


if __name__ == '__main__':
    unittest.main()
