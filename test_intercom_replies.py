"""Intercom replies: the one write, as the owner's own admin, only after their Send on that exact text. No live calls:
every HTTP request goes to a fake that records it."""
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

from connectors import intercom
import next_actions as n
import onboarding
from test_connectors import a, Response
import test_ux as ux

READ, REPLY = 'fixture-read-token', 'fixture-reply-token'
ACCOUNT = {**intercom.SETUP['account'], 'workspace_id': 'abc123', 'workspace_name': 'Example Support', 'admin_id': '42',
           'region': 'us', 'boundary': 'work:Example Support',
           'reply_credential': 'intercom-reply', 'reply_admin_id': '42', 'reply_admin_name': 'Alex Example'}
SHARED = {**ACCOUNT, 'id': 'intercom-fleet', 'credential': 'shared-intercom-f1',
          'shared': {'fleet': 'f1', 'fleet_name': 'Example', 'service': 'intercom', 'grant': 'g1', 'at': '2026-10-23', 'by': 'm2'}}
ME = {'type': 'admin', 'id': '42', 'name': 'Alex Example', 'email': 'alex@example.com', 'app': {'id_code': 'abc123', 'name': 'Example Support'}}


def conversation(parts=()):
    return {'type': 'conversation', 'id': '7', 'updated_at': 100, 'created_at': 50, 'title': 'Export question',
            'source': {'body': '<p>Can I export my data?</p>', 'author': {'type': 'user', 'name': 'Casey Customer', 'email': 'casey@example.org'}},
            'conversation_parts': {'conversation_parts': list(parts), 'total_count': len(parts)}}


def part(pid, body, admin='42', created=2_000_000_000):
    return {'id': pid, 'part_type': 'comment', 'created_at': created, 'body': body, 'author': {'type': 'admin', 'id': admin, 'name': 'Alex'}}


class Fake:
    """Intercom, faked: who each token is, and the conversation's parts. Records every request and its token."""

    def __init__(self, test):
        self.test, self.requests, self.parts, self.me = test, [], [part('p1', '<p>Earlier</p>')], copy.deepcopy(ME)
        self.fail = None

    def respond(self, request, timeout):
        token = request.get_header('Authorization').removeprefix('Bearer ')
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.selector, token, body))
        self.test.assertTrue(request.full_url.startswith('https://api.intercom.io/'))
        if request.selector == '/me':
            return Response(json.dumps(self.me).encode())
        if request.selector == '/conversations/7' and request.get_method() == 'GET':
            return Response(json.dumps(conversation(self.parts)).encode())
        if request.selector == '/conversations/7/reply' and request.get_method() == 'POST':
            if self.fail:
                raise urllib.error.HTTPError(request.full_url, self.fail, 'no', {}, io.BytesIO(b''))
            self.parts.append(part('p2', body['body']))
            return Response(json.dumps(conversation(self.parts)).encode())
        raise AssertionError('unexpected request ' + request.selector)

    def writes(self):
        return [r for r in self.requests if r[0] == 'POST']


class Allowlist(unittest.TestCase):
    """repliable: the one write, exactly. Anything else is refused before any token is read."""

    def payload(self, **change):
        return {'type': 'admin', 'admin_id': '42', 'message_type': 'comment', 'body': '<p>Hi</p>', **change}

    def test_only_a_comment_as_the_owners_own_admin_in_one_conversation(self):
        self.assertTrue(intercom.repliable('POST', '/conversations/7/reply', self.payload(), ACCOUNT))
        for method, path, payload in [
                ('POST', '/conversations/7/reply', self.payload(admin_id='43')),            # someone else's admin id
                ('POST', '/conversations/7/reply', self.payload(message_type='note')),      # an internal note
                ('POST', '/conversations/7/reply', self.payload(message_type='close')),
                ('POST', '/conversations/7/reply', self.payload(message_type='assignment')),
                ('POST', '/conversations/7/reply', self.payload(type='user')),
                ('POST', '/conversations/7/reply', {**self.payload(), 'assignee_id': '43'}),
                ('POST', '/conversations/7/reply', {**self.payload(), 'attachment_urls': ['https://x.invalid/a']}),
                ('POST', '/conversations/7/reply', self.payload(body='')),
                ('POST', '/conversations/7/parts', {'message_type': 'close', 'type': 'admin', 'admin_id': '42'}),
                ('POST', '/conversations/7/tags', {'id': '1', 'admin_id': '42'}),
                ('PUT', '/conversations/7', {'read': True}),
                ('DELETE', '/conversations/7', None),
                ('POST', '/conversations/7/reply/x', self.payload()),
                ('POST', '/conversations', self.payload()),
                ('POST', '/messages', self.payload())]:
            self.assertFalse(intercom.repliable(method, path, payload, ACCOUNT), (method, path, payload))
        self.assertFalse(intercom.repliable('POST', '/conversations/7/reply', self.payload(), SHARED))
        self.assertFalse(intercom.repliable('POST', '/conversations/7/reply', self.payload(), {**ACCOUNT, 'reply_credential': 'intercom'}))

    def test_any_other_write_is_refused_before_the_token_is_read(self):
        credential = Mock(return_value=REPLY)
        helpers = types.SimpleNamespace(SourceError=a.SourceError, iso=a.CONNECTOR_HELPERS.iso, credential=credential)
        replier = intercom.Replier({}, ACCOUNT, helpers)
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('no request')):
            for method, path, payload in [('POST', '/conversations/7/reply', self.payload(admin_id='43')),
                                          ('POST', '/conversations/7/reply', self.payload(message_type='note')),
                                          ('POST', '/conversations/7/parts', {'message_type': 'close', 'type': 'admin', 'admin_id': '42'}),
                                          ('POST', '/conversations/7/tags', {'id': '1'})]:
                with self.assertRaises(a.SourceError) as caught:
                    replier.request(method, path, payload)
                self.assertEqual(caught.exception.category, 'unsupported_intercom_method')
            # The reading connector never writes at all, not even the reply
            with self.assertRaises(a.SourceError):
                intercom.Source({}, ACCOUNT, helpers).request('POST', '/conversations/7/reply', self.payload())
        credential.assert_not_called()

    def test_a_fleet_shared_token_can_never_reply_even_with_a_reply_file(self):
        helpers = types.SimpleNamespace(SourceError=a.SourceError, credential=Mock(return_value=REPLY))
        for account in (SHARED, {**SHARED, 'shared': True}, {**ACCOUNT, 'reply_credential': ACCOUNT['credential']},
                        {**ACCOUNT, 'reply_credential': None}, {**ACCOUNT, 'reply_admin_id': ''}):
            self.assertFalse(intercom.replies_on(account))
            self.assertFalse(n.intercom_replies_on(account))
            with self.assertRaises(a.SourceError):
                intercom.Replier({}, account, helpers)
        helpers.credential.assert_not_called()
        # The one who shared it keeps their own replies
        self.assertTrue(intercom.replies_on({**ACCOUNT, 'shared': {'fleet': 'f1', 'origin': True}}))

    def test_reply_text_is_plain_html(self):
        self.assertEqual(intercom.reply_html('Hi Casey,\n\nYes: <b>CSV</b> & JSON.\nThanks'),
                         '<p>Hi Casey,</p><p>Yes: &lt;b&gt;CSV&lt;/b&gt; &amp; JSON.<br>Thanks</p>')


class EnableReplies(unittest.TestCase):
    """Only the owner's own sign-in turns replies on: its token in a file of its own, its admin the only one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / 'config.json'
        read = root / 'intercom.env'
        read.write_text(READ)
        account = {k: v for k, v in ACCOUNT.items() if not k.startswith('reply_')}
        self.path.write_text(json.dumps({'database': str(root / 'events.sqlite'), 'credentials': {'intercom': str(read)},
                                         'accounts': [account, {k: v for k, v in SHARED.items() if not k.startswith('reply_')}]}))
        self.fake = Fake(self)
        patch('urllib.request.OpenerDirector.open', side_effect=self.fake.respond).start()
        self.addCleanup(patch.stopall)

    def test_the_sign_in_token_goes_in_its_own_private_file(self):
        self.assertEqual(onboarding.enable_replies(self.path, 'intercom', REPLY), ('42', 'Alex Example'))
        config = json.loads(self.path.read_text())
        account = next(x for x in config['accounts'] if x['id'] == 'intercom')
        self.assertEqual((account['reply_credential'], account['reply_admin_id'], account['reply_admin_name']), ('intercom-reply', '42', 'Alex Example'))
        file = Path(config['credentials']['intercom-reply'])
        self.assertEqual(file.read_text().strip(), REPLY)
        self.assertEqual(stat.S_IMODE(os.stat(file).st_mode), 0o600)
        self.assertEqual(Path(config['credentials']['intercom']).read_text(), READ)  # the read token is untouched
        self.assertEqual([(m, p, t) for m, p, t, _ in self.fake.requests], [('GET', '/me', REPLY)])

    def test_refused_for_a_fleet_grant_or_another_workspace(self):
        with self.assertRaises(onboarding.SetupError):
            onboarding.enable_replies(self.path, 'intercom-fleet', REPLY)
        self.assertEqual(self.fake.requests, [])
        self.fake.me['app']['id_code'] = 'other'
        with self.assertRaises(onboarding.SetupError):
            onboarding.enable_replies(self.path, 'intercom', REPLY)
        self.assertFalse(any(x.get('reply_credential') for x in json.loads(self.path.read_text())['accounts']))


class Send(unittest.TestCase):
    """From a card: drafted, seen, Send, then the one write as the owner's admin. Never before their Send."""
    row = ux.UX.row
    decide = ux.UX.decide

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.fake = Fake(self)
        patch('urllib.request.OpenerDirector.open', side_effect=self.fake.respond).start()
        self.addCleanup(patch.stopall)
        tokens = {'intercom': READ, 'intercom-reply': REPLY, 'shared-intercom-f1': READ}
        self.helpers = types.SimpleNamespace(SourceError=a.SourceError, iso=a.CONNECTOR_HELPERS.iso, credential=lambda config, name: tokens[name])
        self.accounts = [dict(ACCOUNT), dict(SHARED)]
        events = [intercom.event(ACCOUNT, conversation(), self.helpers), intercom.event(SHARED, {**conversation(), 'id': '8'}, self.helpers)]
        for event in events:
            self.store.commit_page(event['account_id'], {}, [event])
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM events')]
        self.sid = self.store.suggest('ic-task', 'Answer Casey', 'Casey asks about exporting.', ids)['id']

    def app(self):
        app = object.__new__(a.App)
        app.store, app.config = self.store, {'accounts': self.accounts}
        return app

    def save(self, **draft):
        action = {k: v for k, v in {'kind': 'intercom', 'label': 'Send reply', 'account_id': 'intercom', 'conversation_id': '7',
                                    'body': 'Yes, under Settings > Export.', **draft}.items() if v is not None}
        return self.app().action('prepare', {'suggestion_id': self.sid, 'body': 'Reply ready.', 'next_action': action})

    def ready(self, **draft):
        self.decide('accept')
        self.save(**draft)
        return self.row()['next_action']

    def confirm(self, revision=None, key='owner-approval'):
        return self.app().action('confirm_action', {'suggestion_id': self.sid, 'revision': revision or self.row()['next_action']['revision'],
                                                    'approval_id': key}, owner_dashboard=True)

    def transport(self):
        return n.ActionTransport(None, None, intercom=n.IntercomTransport(self.app().config, intercom, self.helpers))

    def job(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM action_jobs ORDER BY created_at DESC LIMIT 1').fetchone())

    def test_the_draft_names_who_it_goes_to_and_as_whom(self):
        action = self.ready()
        self.assertEqual((action['to_name'], action['as_name'], action['admin_id']), ('Casey Customer', 'Alex Example', '42'))
        self.assertEqual(n.preview(action), 'Intercom reply to Casey Customer, as Alex Example\n\nYes, under Settings > Export.')
        for bad in ({'conversation_id': '999'}, {'account_id': 'intercom-fleet', 'conversation_id': '8'}, {'label': 'Close it'},
                    {'message_type': 'note'}, {'admin_id': '43'}, {'assignee_id': '43'}, {'body': 'a' * 10001}, {'body': ' '}):
            with self.assertRaises(ValueError, msg=bad):
                self.save(**bad)

    def test_nothing_goes_out_without_the_owners_send(self):
        self.ready()
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [])
        # The agent can't approve it, and send_now doesn't apply to Intercom
        with self.assertRaises(ValueError):
            self.app().action('confirm_action', {'suggestion_id': self.sid, 'revision': self.row()['next_action']['revision'], 'approval_id': 'x'})
        with self.assertRaises(ValueError):
            self.app().action('prepare', {'suggestion_id': self.sid, 'body': 'Sent.', 'send_now': True, 'next_action': {
                'kind': 'intercom', 'account_id': 'intercom', 'conversation_id': '7', 'body': 'Yes'}})
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [])

    def test_only_the_exact_text_the_owner_saw_goes_out(self):
        seen = self.ready()['revision']
        edited = self.app().action('revise_draft', {'suggestion_id': self.sid, 'revision': seen, 'body': 'Yes: Settings, then Export.'},
                                   owner_dashboard=True)
        self.assertNotEqual(edited['revision'], seen)
        with self.assertRaises(ValueError):
            self.confirm(seen)  # approving the old text does not send the new one
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [])
        self.confirm(edited['revision'])
        self.app().action_step(self.transport())
        self.assertEqual([r[3]['body'] for r in self.fake.writes()], ['<p>Yes: Settings, then Export.</p>'])

    def test_an_approved_reply_goes_once_as_the_owner_with_the_reply_token(self):
        self.ready()
        self.confirm()
        self.confirm(key='second-click')
        self.app().action_step(self.transport())
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [('POST', '/conversations/7/reply', REPLY, {
            'type': 'admin', 'admin_id': '42', 'message_type': 'comment', 'body': '<p>Yes, under Settings &gt; Export.</p>'})])
        self.assertTrue(all(token == REPLY for _, _, token, _ in self.fake.requests))  # never the read token
        self.assertEqual((self.job()['status'], self.job()['message_id']), ('sent', 'intercom:7:p2'))
        self.assertIn('✓ Sent as Alex Example in Intercom at ', str(a.card(self.row())))
        with self.store.connect() as db:
            owner, _ = self.app().card_chat_service(db, self.sid)
        events = [e['text'] for e in owner if e.get('kind') == 'event']
        self.assertIn('Sent as Alex Example in Intercom', events)
        self.assertFalse(any(e.startswith('You ') for e in events))

    def test_refused_with_someone_elses_admin_id(self):
        self.ready()
        self.confirm()
        with self.store.connect() as db:  # a payload changed after the Send: another admin
            job = db.execute('SELECT id,payload FROM action_jobs').fetchone()
            db.execute('UPDATE action_jobs SET payload=? WHERE id=?', (json.dumps({**json.loads(job['payload']), 'admin_id': '43'}), job['id']))
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual((self.job()['status'], self.job()['error']), ('blocked', 'This reply would go as someone else in Intercom. Nothing was sent.'))
        # And a reply token that is now someone else in Intercom
        self.accounts[0]['reply_admin_id'] = '42'
        self.fake.me['id'] = '43'
        with self.assertRaises(ValueError):
            n.IntercomTransport(self.app().config, intercom, self.helpers).preflight({**self.row()['next_action'], 'admin_id': '42'})
        self.assertEqual(self.fake.writes(), [])

    def test_refused_with_the_shared_token(self):
        self.ready()
        self.confirm()
        self.accounts[0] = {**self.accounts[0], 'shared': {'fleet': 'f1', 'grant': 'g1'}}  # it turned into a Fleet grant
        self.app().action_step(self.transport())
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.job()['status'], 'blocked')
        self.assertIn('only reads', self.job()['error'])
        self.accounts[0] = {**ACCOUNT, 'reply_credential': 'intercom'}  # the read token as the reply token
        with self.assertRaises(ValueError):
            n.IntercomTransport(self.app().config, intercom, self.helpers).preflight(self.row()['next_action'])
        self.assertEqual(self.fake.writes(), [])

    def test_an_unclear_outcome_is_looked_for_and_never_resent(self):
        self.ready()
        self.confirm()
        self.fake.fail = 502
        self.app().action_step(self.transport())
        self.assertEqual(self.job()['status'], 'uncertain')
        self.fake.fail = None
        self.fake.parts.append(part('p9', '<p>Yes, under Settings &gt; Export.</p>'))  # it did go through
        with self.store.connect() as db:
            db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.app().action_step(self.transport())
        self.assertEqual((self.job()['status'], self.job()['message_id']), ('sent', 'intercom:7:p9'))
        self.assertEqual(len(self.fake.writes()), 1)

    def test_the_customers_answer_brings_the_card_back(self):
        self.ready()
        self.confirm()
        self.app().action_step(self.transport())
        answer = conversation(self.fake.parts + [{'id': 'p3', 'part_type': 'comment', 'created_at': 4_000_000_000, 'body': '<p>Thanks!</p>',
                                                  'author': {'type': 'user', 'name': 'Casey Customer'}}])
        answer['updated_at'] = 4_000_000_000
        self.store.commit_page('intercom', {}, [intercom.event(ACCOUNT, answer, self.helpers)])
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM events WHERE kind='intercom.conversation' AND account_id='intercom'")]
        self.assertEqual(list(self.store.card_replies(rows).values())[0]['suggestion_id'], self.sid)


class ConnectionsRow(unittest.TestCase):
    """The Connections card says who replies go out as, or that it only reads."""

    def test_replies_as_the_owner_only_with_their_own_sign_in(self):
        with tempfile.TemporaryDirectory() as temp:
            store = a.Store(Path(temp) / 'events.sqlite')
            plain = {k: v for k, v in ACCOUNT.items() if not k.startswith('reply_')}
            rows = {c['id']: c for c in store.snapshot([ACCOUNT, {**SHARED, 'id': 'intercom-fleet'}, {**plain, 'id': 'intercom-read'}])['connectors']}
        self.assertEqual(rows['intercom']['replies_as'], 'Alex Example')
        self.assertEqual(rows['intercom-fleet']['replies_as'], '')
        self.assertEqual(rows['intercom-read']['replies_as'], '')


if __name__ == '__main__':
    unittest.main()
