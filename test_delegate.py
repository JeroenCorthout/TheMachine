"""Handing a task to someone: a Slack DM or email with delegate:true; after the owner's
Send the card waits for their reply, closes when they answer and comes back if they don't. No live calls."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import next_actions as n
import test_ux as ux
a = ux.a

GMAIL = {'id': 'work', 'email': 'owner@example.com', 'service': 'gmail', 'transport': 'gog', 'boundary': 'work:Example'}
SLACK = {'id': 'slack', 'service': 'slack', 'transport': 'maton', 'boundary': 'work:Example', 'credential': 'maton',
         'credential_variable': 'KEY', 'connection_id': 'conn-1', 'user_id': 'UOWNER1'}
OTHER_SLACK = {**SLACK, 'id': 'other-slack', 'boundary': 'work:Other'}


def email(source_id, at, sender='customer@client.example', thread='t1', **meta):
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'gmail.message', 'occurred_at': at, 'title': 'Invoice question',
            'body': 'Can someone check our invoice?', 'deleted': False,
            'metadata': {'from': sender, 'thread_id': thread, 'labels': ['INBOX'], **meta}}


def slack(source_id, at, user, channel='D0SAM1', text='On it'):
    return {'account_id': 'slack', 'source_id': source_id, 'kind': 'slack.message', 'occurred_at': at, 'title': 'Sam',
            'body': text, 'deleted': False, 'metadata': {'channel_id': channel, 'user_id': user}}


class Delegate(unittest.TestCase):
    row = ux.UX.row
    decide = ux.UX.decide

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = a.Store(Path(self.tmp.name) / 'events.sqlite')
        self.store.owner_user_ids = {'UOWNER1'}
        self.store.commit_page('work', {}, [email('m1', '2026-10-15T08:00:00+00:00')])
        with self.store.connect() as db:
            event_id = db.execute('SELECT id FROM events').fetchone()[0]
        self.sid = self.store.suggest('invoice-question', 'Invoice question from Client', 'They ask about an invoice.', [event_id])['id']

    def app(self):
        app = object.__new__(a.App)
        app.store = self.store
        app.config = {'accounts': [GMAIL, SLACK, OTHER_SLACK]}
        return app

    def ready(self, action):
        self.decide('accept')
        self.app().action('prepare', {'suggestion_id': self.sid, 'body': 'Message for Sam ready.', 'next_action': action, 'outcome': 'needs_you', 'needs': 'approve the send'})
        return self.row()['next_action']

    def confirm(self):
        return self.app().action('confirm_action', {'suggestion_id': self.sid, 'revision': self.row()['next_action']['revision'],
                                                    'approval_id': 'owner-approval'}, owner_dashboard=True)

    def transport(self, send):
        class T:
            def preflight(self, action): return None
            def send(self, action, job): return send(action, job)
            def reconcile(self, action, job): return {}
        return T()

    def slack_action(self, **extra):
        return {'kind': 'slack', 'label': 'Send to Sam', 'account_id': 'slack', 'to_email': 'Sam@Example.com', 'to_name': 'Sam',
                'body': 'Could you check [the invoice](https://app.example.com/i/1)?', 'delegate': True, **extra}

    def suggestion(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM suggestions WHERE id=?', (self.sid,)).fetchone())

    def test_slack_draft_is_checked_and_previewed(self):
        action = self.ready(self.slack_action())
        self.assertEqual((action['kind'], action['to_email'], action['wait_days']), ('slack', 'sam@example.com', 3))
        self.assertIn('Slack message to Sam (sam@example.com)', n.preview(action))
        self.assertIn('waits 3 days for Sam’s reply', n.preview(action))
        # Same rows as the plugin's packRows: the Send shares its row with Mark done.
        self.assertEqual([[b['label'] for b in block['buttons']] for block in a.controls(self.row())['blocks']],
                         [['Send to Sam', 'Mark done', 'Snooze'], ['Change', 'Not needed']])
        for bad in ({'account_id': 'other-slack'}, {'user_id': 'U0SAM12'}, {'to_email': None, 'user_id': 'nope'},
                    {'wait_days': 30}, {'delegate': 'yes'}, {'label': 'Delegate'}, {'to_name': ''}, {'channel': 'C1'}):
            with self.subTest(bad), self.assertRaises(ValueError):
                self.app().action('prepare', {'suggestion_id': self.sid, 'body': 'x', 'outcome': 'done',
                                              'next_action': {k: v for k, v in self.slack_action(**bad).items() if v is not None}})

    def test_after_the_send_the_card_waits_and_closes_when_they_reply(self):
        self.ready(self.slack_action())
        self.confirm()
        sent = []
        with patch.object(a, 'now', return_value='2026-10-16T09:00:00+00:00'):
            self.app().action_step(self.transport(lambda action, job: (sent.append(action), {'message_id': 'slack:D0SAM1:1.2', 'channel_id': 'D0SAM1', 'user_id': 'U0SAM12'})[1]))
        self.assertEqual(len(sent), 1)
        row = self.suggestion()
        self.assertEqual((row['status'], row['resume_status']), ('snoozed', 'prepared'))
        self.assertEqual(row['snoozed_until'], '2026-10-19T07:00:00+00:00')  # 3 days, Monday 09:00 local time
        self.assertIn('Sent to Sam. Waiting for their reply until Mon, Oct 19', a.card(self.row()))
        # The owner's own message in that DM, someone else in another chat and older messages don't count.
        self.store.commit_page('slack', {}, [slack('s0', '2026-10-16T08:00:00+00:00', 'U0SAM12'),
                                             slack('s1', '2026-10-16T10:00:00+00:00', 'UOWNER1'),
                                             slack('s2', '2026-10-16T10:00:00+00:00', 'U0SAM12', channel='C0GENERAL')])
        with self.store.connect() as db:
            self.store.settle_awaiting(db)
        self.assertEqual(self.suggestion()['status'], 'snoozed')
        self.store.commit_page('slack', {}, [slack('s3', '2026-10-16T11:00:00+00:00', 'U0SAM12', text='Done, invoice fixed')])
        with self.store.connect() as db:
            self.store.settle_awaiting(db)
            decision = db.execute("SELECT * FROM decisions WHERE id=?", ('answered:' + self.sid,)).fetchone()
            reply_id = db.execute("SELECT id FROM events WHERE source_id='s3'").fetchone()[0]
        self.assertEqual(self.suggestion()['status'], 'completed')
        self.assertEqual((decision['choice'], decision['source']), ('complete', 'assistant'))
        self.assertEqual(a.card(self.row()).splitlines()[-1], 'Sam replied, so I closed this.')
        # Their answer is reviewed as a reply to this card.
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM events WHERE id=?', (reply_id,))]
        self.assertEqual(self.store.card_replies(rows)[reply_id]['suggestion_id'], self.sid)

    def test_no_reply_brings_the_card_back(self):
        self.ready(self.slack_action(wait_days=1))
        self.confirm()
        with patch.object(a, 'now', return_value='2026-10-14T09:00:00+00:00'):
            self.app().action_step(self.transport(lambda *_: {'message_id': 'slack:D0SAM1:1.2', 'channel_id': 'D0SAM1', 'user_id': 'U0SAM12'}))
        self.assertEqual(self.suggestion()['snoozed_until'], '2026-10-15T07:00:00+00:00')
        self.store.wake_snoozed()
        self.assertEqual(self.suggestion()['status'], 'prepared')
        self.assertIn('No reply from Sam yet.', a.card(self.row()))

    def test_delegated_email_waits_for_that_persons_reply(self):
        action = self.ready({'kind': 'email', 'label': 'Send to the accountant', 'account_id': 'work', 'to': ['books@accountant.example'],
                             'to_name': 'the accountant', 'subject': 'Invoice check', 'body': 'Could you check this invoice?\n\nThanks,\n\nOwner',
                             'delegate': True, 'wait_days': 2})
        self.assertIn('waits 2 days', n.preview(action))
        with self.assertRaises(ValueError):
            n.validate({**{k: v for k, v in action.items() if k not in ('revision', 'from', 'signature')}, 'to': ['a@x.example', 'b@x.example']},
                       self.app().config, ['work'])
        self.confirm()
        with patch.object(a, 'now', return_value='2026-10-16T09:00:00+00:00'):
            self.app().action_step(self.transport(lambda *_: {'message_id': 'gm-1'}))
        with self.store.connect() as db:
            job_rfc = db.execute('SELECT rfc_message_id FROM action_jobs').fetchone()[0]
        self.assertEqual(self.suggestion()['status'], 'snoozed')
        # Another email from them about something else doesn't close it; their reply to this one does.
        self.store.commit_page('work', {}, [email('m2', '2026-10-16T10:00:00+00:00', 'Books <books@accountant.example>', thread='t9')])
        with self.store.connect() as db:
            self.store.settle_awaiting(db)
        self.assertEqual(self.suggestion()['status'], 'snoozed')
        self.store.commit_page('work', {}, [email('m3', '2026-10-16T11:00:00+00:00', 'Books <books@accountant.example>', thread='t10', **{'in-reply-to': job_rfc})])
        with self.store.connect() as db:
            self.store.settle_awaiting(db)
        self.assertEqual(self.suggestion()['status'], 'completed')

    def test_without_delegate_a_send_closes_the_card_with_one_status(self):
        self.ready(self.slack_action(delegate=None))
        self.confirm()
        self.app().action_step(self.transport(lambda *_: {'message_id': 'slack:D0SAM1:1.2', 'channel_id': 'D0SAM1', 'user_id': 'U0SAM12'}))
        self.assertEqual(self.suggestion()['status'], 'completed')
        self.assertIn('✓ Slack message sent to Sam at ', a.card(self.row()))


class SlackSender(unittest.TestCase):
    action = {'kind': 'slack', 'account_id': 'slack', 'to_email': 'sam@example.com', 'to_name': 'Sam',
              'body': 'See [the invoice](https://app.example.com/i?a=1&b=2) <now>'}

    def run_with(self, replies, action=None):
        calls = []

        class Response(io.BytesIO):
            def __init__(self, body):
                super().__init__(json.dumps(body).encode())
                self.headers = {'Maton-Connection': 'conn-1'}
            def __enter__(self): return self
            def __exit__(self, *_): self.close()

        def urlopen(request, timeout):
            method = request.full_url.split('/api/')[1].split('?')[0]
            calls.append((method, request.get_method(), json.loads(request.data) if request.data else request.full_url))
            if isinstance(replies[method], type):
                raise replies[method]()
            return Response(replies[method])
        sender = n.SlackTransport({'accounts': [SLACK]}, lambda config, name: 'KEY=secret')
        with patch('urllib.request.urlopen', urlopen):
            result = sender.send(action or self.action, {'id': 'job', 'created_at': '2026-10-16T09:00:00+00:00'})
        return result, calls

    USER = {'ok': True, 'user': {'id': 'U0SAM12', 'real_name': 'Sam Peeters', 'profile': {'display_name': 'sam'}}}

    def test_sends_one_direct_message_in_slack_format(self):
        result, calls = self.run_with({'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                                       'chat.postMessage': {'ok': True, 'ts': '1.2'}})
        self.assertEqual(result, {'message_id': 'slack:D0SAM1:1.2', 'channel_id': 'D0SAM1', 'user_id': 'U0SAM12'})
        self.assertEqual([c[:2] for c in calls], [('users.lookupByEmail', 'GET'), ('conversations.open', 'POST'), ('chat.postMessage', 'POST')])
        self.assertEqual(calls[2][2]['channel'], 'D0SAM1')
        self.assertEqual(calls[2][2]['text'], 'See <https://app.example.com/i?a=1&b=2|the invoice> &lt;now&gt;')

    def test_wrong_person_is_refused_before_anything_is_posted(self):
        sender = n.SlackTransport({'accounts': [SLACK]}, lambda config, name: 'KEY=secret')
        with patch.object(sender, 'call', return_value={'ok': True, 'user': {'id': 'U1', 'real_name': 'Samantha Jones'}}):
            with self.assertRaises(ValueError) as caught:
                sender.preflight(self.action)
        self.assertIn('That Slack user is Samantha Jones, not Sam', str(caught.exception))
        with patch.object(sender, 'call', return_value={'ok': True, 'user': {'id': 'U1', 'real_name': 'Sam Peeters', 'is_bot': True}}):
            with self.assertRaises(ValueError):
                sender.preflight(self.action)
        self.assertEqual(set(n.SlackTransport.METHODS), {'users.info', 'users.lookupByEmail', 'conversations.open', 'chat.postMessage', 'conversations.history'})

    def test_unknown_post_outcome_is_reconciled_not_resent(self):
        result, _ = self.run_with({'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                                   'chat.postMessage': TimeoutError})
        self.assertEqual(result, {})
        import urllib.error
        class Refused(urllib.error.HTTPError):
            def __init__(self): super().__init__('https://api.maton.ai', 403, 'Forbidden', {}, None)
        result, _ = self.run_with({'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                                   'chat.postMessage': Refused})
        self.assertEqual(result, {'rejected': 403})
        class Limited(urllib.error.HTTPError):
            def __init__(self): super().__init__('https://api.maton.ai', 429, 'Too Many Requests', {'Retry-After': '30'}, None)
        result, _ = self.run_with({'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                                   'chat.postMessage': Limited})
        self.assertEqual(result, {'not_sent': True})  # waits and sends again, never dropped as refused
        result, _ = self.run_with({'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                                   'chat.postMessage': {'ok': False, 'error': 'ratelimited'}})
        self.assertEqual(result, {'not_sent': True})
        sender = n.SlackTransport({'accounts': [SLACK]}, lambda config, name: 'KEY=secret')
        text = n.slack_text(self.action['body'])
        replies = {'users.lookupByEmail': self.USER, 'conversations.open': {'ok': True, 'channel': {'id': 'D0SAM1'}},
                   'conversations.history': {'ok': True, 'messages': [{'user': 'UOWNER1', 'text': text, 'ts': '9.9'}, {'user': 'U0SAM12', 'text': text, 'ts': '9.8'}]}}
        with patch.object(sender, 'call', side_effect=lambda action, method, params: replies[method]):
            self.assertEqual(sender.reconcile(self.action, {'id': 'job', 'created_at': '2026-10-16T09:00:00+00:00'})['message_id'], 'slack:D0SAM1:9.9')


if __name__ == '__main__':
    unittest.main()
