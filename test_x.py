"""X connector plugin: mentions, replies and DMs from the x-inbox page shape, events and sync, and
Send (replies and DMs) from the next action through x-inbox. No live calls: the browser is always mocked."""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
import next_actions as n
import test_ux as ux
sys.path.insert(0, str(Path(__file__).parent / 'integrations'))
import x_inbox

spec = importlib.util.spec_from_file_location('assistant_x', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
x = a.load_connector('x')
H = a.CONNECTOR_HELPERS

ACCOUNT = {'id': 'x-sf', 'service': 'x', 'transport': 'x-inbox', 'handle': 'acme', 'primary': 'owner',
           'email': '@acme', 'boundary': 'work:Example'}
NOW = '2026-10-16T10:00:00+00:00'


def mention(**extra):
    return {'id': '1978000000000000001', 'at': 1792130000000, 'text': '@acme does the Gmail sync work with shared inboxes?', 'lang': 'en',
            'conversation_id': '1977000000000000000',
            'author': {'id': '42', 'name': 'Ada', 'screen_name': 'ada', 'followers': 1200, 'verified': False},
            'reply_to': {'id': '1977000000000000000', 'screen_name': 'acme', 'to_account': True, 'text': 'New: shared inboxes are here.'},
            'quoted': None, **extra}


def dm(**extra):
    return {'id': '1978000000000000009', 'at': 1792131000000, 'conversation_id': '42-777', 'from_me': False,
            'from': {'id': '42', 'name': 'Ada', 'screen_name': 'ada'}, 'with': 'Ada', 'group': False, 'request': True,
            'text': 'Hi! Could someone help me with an invoice?', **extra}


def page(mentions=(), dms=(), state=None):
    return {'account': {'id': '777', 'screen_name': 'acme'}, 'mentions': list(mentions), 'dms': list(dms),
            'state': state or {'mention_since_id': '1978000000000000001', 'dm_cursor': 'C1'}}


class Source:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    def page(self, since, state):
        self.calls.append((since, state))
        return self.pages.pop(0)


class XSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = a.Store(Path(self.tmp.name) / 'a.sqlite')
        a.set_timezone('Europe/Brussels')

    def test_events_carry_who_what_it_answers_and_a_link(self):
        reply, message = x.events(ACCOUNT, page([mention()], [dm()]), H)
        self.assertEqual(reply['kind'], 'x.mention')
        self.assertEqual(reply['title'], 'Ada (@ada) replied to @acme')
        self.assertIn('[In reply to @acme: “New: shared inboxes are here.”]', reply['body'])
        self.assertEqual(reply['metadata']['source_url'], 'https://x.com/ada/status/1978000000000000001')
        self.assertEqual(message['kind'], 'x.dm')
        self.assertTrue(message['metadata']['request'])
        self.assertEqual(message['metadata']['source_url'], 'https://x.com/messages/42-777')
        self.assertEqual(x.events(ACCOUNT, page(dms=[dm(from_me=True)]), H)[0]['metadata']['from'], 'You')
        plain = x.events(ACCOUNT, page([mention(reply_to=None)]), H)[0]
        self.assertEqual(plain['title'], 'Ada (@ada) mentioned @acme')
        for bad in (page([mention(id='x')]), page([mention(at='soon')]), page(dms=[dm(from_me=None)])):
            with self.assertRaises(a.SourceError):
                x.events(ACCOUNT, bad, H)

    def test_sync_keeps_the_cursor_dedupes_and_sleeps_at_night(self):
        src = Source(page([mention(), mention()], [dm()]), page([mention()], [], {'mention_since_id': '1978000000000000001', 'dm_cursor': 'C2'}))
        with patch.object(a, 'now', return_value=NOW):
            self.assertEqual(x.sync(self.store, {}, ACCOUNT, H, src)['events'], 2)
            x.sync(self.store, {}, ACCOUNT, H, src)
        self.assertEqual(src.calls[0][1], {})
        self.assertEqual(src.calls[1][1], {'mention_since_id': '1978000000000000001', 'dm_cursor': 'C1'})
        self.assertEqual(self.store.checkpoint('x-sf')['page']['dm_cursor'], 'C2')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 2)
        with patch.object(a, 'now', return_value='2026-10-16T22:30:00+00:00'):  # 00:30 local time
            x.sync(self.store, {}, ACCOUNT, H, src)
        self.assertEqual(len(src.calls), 2)

    def test_an_account_with_its_own_browser_profile_reads_there(self):
        ok = subprocess.CompletedProcess([], 0, json.dumps(page([mention()])), '')
        with patch.object(a.subprocess, 'run', return_value=ok) as run:
            x.Source({}, {**ACCOUNT, 'browser_profile': 'machine-2'}, H).page(5, {})
        self.assertEqual(run.call_args.kwargs['env']['X_INBOX_BROWSER_PROFILE'], 'machine-2')

    def test_reader_maps_errors_and_reads_only(self):
        source = x.Source({'x_inbox': '/bin/x-inbox-test'}, ACCOUNT, H)
        ok = subprocess.CompletedProcess([], 0, json.dumps(page([mention()])), '')
        with patch.object(a.subprocess, 'run', return_value=ok) as run:
            source.page(5, {'dm_cursor': 'C1'})
        command = run.call_args.args[0]
        self.assertEqual(command[:6], ['/bin/x-inbox-test', 'sync', '--handle', 'acme', '--primary', 'owner'])
        self.assertEqual(json.loads(command[command.index('--state') + 1]), {'dm_cursor': 'C1'})
        for stderr, code in (('the OpenClaw browser is busy', 'x_browser_busy'), ('X is signed out in the OpenClaw browser', 'x_signed_out'),
                             ('@acme is not signed in in the OpenClaw browser', 'x_signed_out'), ('X rate limit reached', 'x_rate_limited'),
                             ('the browser did not answer in time', 'x_read_timeout'), ('X read failed: boom', 'x_read_failed')):
            with patch.object(a.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'x-inbox: ' + stderr)):
                with self.assertRaises(a.SourceError) as e:
                    source.page(5, {})
                self.assertEqual(str(e.exception), code)
        with patch.object(a.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"mentions": []}', '')):
            with self.assertRaises(a.SourceError):
                source.page(5, {})

    def test_review_context_source_line_and_promises(self):
        with patch.object(a, 'now', return_value=NOW):
            self.store.commit_page('x-sf', {}, x.events(ACCOUNT, page([], [dm(id='1', at=1792130000000, text='Hello?'), dm()]), H))
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY occurred_at")]
        context = self.store.conversation_context([rows[1]])
        self.assertEqual(len(context[rows[1]['id']]), 1)
        self.assertTrue(context[rows[1]['id']][0].endswith('Ada (@ada): Hello?'))
        self.assertTrue(a.source_line([rows[1]]).startswith('Source: X message from Ada (@ada) · '))
        watch = a.awaiting_key(rows[1])
        self.assertEqual(watch['conversation'], '42-777')
        self.assertTrue(a.awaiting_match(watch, {**rows[1], 'metadata': {**json.loads(rows[1]['metadata']), 'message_id': '2'}}))



MENTION_ID, CONV = '1978000000000000001', '42-777'


class XSend(unittest.TestCase):
    row = ux.UX.row
    decide = ux.UX.decide

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = a.Store(Path(self.tmp.name) / 'events.sqlite')
        self.store.commit_page('x-sf', {}, x.events(ACCOUNT, page([mention()], [dm()]), H))
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM events ORDER BY kind")]
        self.sid = self.store.suggest('x-task', 'Answer Ada', 'Ada asks about shared inboxes.', ids)['id']
        self.accounts = [ACCOUNT, {**ACCOUNT, 'id': 'x-me', 'handle': 'owner'}, {'id': 'work', 'email': 'owner@example.com', 'service': 'gmail', 'transport': 'gog'}]

    def app(self):
        app = object.__new__(a.App)
        app.store = self.store
        app.config = {'accounts': self.accounts}
        return app

    def save(self, **draft):
        action = {'kind': 'x', 'account_id': 'x-sf', 'reply_to': MENTION_ID, 'body': 'Yes, shared inboxes sync too.', **draft}
        action = {k: v for k, v in action.items() if v is not None}
        return self.app().action('prepare', {'suggestion_id': self.sid, 'body': 'Reply ready.', 'next_action': action})

    def ready(self, **draft):
        self.decide('accept')
        self.save(**draft)
        return self.row()['next_action']

    def confirm(self, key='owner-approval'):
        return self.app().action('confirm_action', {'suggestion_id': self.sid, 'revision': self.row()['next_action']['revision'],
                                                    'approval_id': key}, owner_dashboard=True)

    def job(self):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM action_jobs ORDER BY created_at DESC LIMIT 1').fetchone())

    def transport(self, send=None, preflight=None, reconcile=None):
        class T:
            def preflight(self, action): return preflight(action) if preflight else None
            def send(self, action, job): return send(action, job) if send else {'message_id': 'x:1979000000000000005'}
            def reconcile(self, action, job): return reconcile(action, job) if reconcile else {}
        return T()

    def test_reply_names_the_post_from_the_task_and_offers_reply(self):
        action = self.ready()
        self.assertEqual((action['handle'], action['to_screen_name'], action['to_name'], action['max_length']), ('acme', 'ada', 'Ada', 280))
        self.assertEqual(action['to_text'], '@acme does the Gmail sync work with shared inboxes?')
        self.assertEqual(n.preview(action), 'Reply on X as @acme to @ada: “@acme does the Gmail sync work with shared inboxes?”\n\nYes, shared inboxes sync too.')
        self.assertEqual(a.controls(self.row())['blocks'][0]['buttons'][0]['label'], 'Reply on X')

    def test_dm_answers_a_conversation_from_the_task(self):
        action = self.ready(reply_to=None, conversation_id=CONV, label='Send reply', body='Happy to help: what does the invoice say?')
        self.assertEqual((action['conversation_id'], action['to_name'], action['max_length']), (CONV, 'Ada', 10000))
        self.assertEqual(n.preview(action), 'X message as @acme to Ada\n\nHappy to help: what does the invoice say?')
        self.assertEqual(n.label(action), 'Send reply')

    def test_only_task_posts_and_conversations_plain_text_within_x_limits(self):
        self.decide('accept')
        link = ' https://example.com/' + 'a' * 60  # counts 23, like X
        for bad in ({'reply_to': '1978000000000000099'}, {'reply_to': None, 'conversation_id': '7-8'}, {'conversation_id': CONV},
                    {'reply_to': None}, {'account_id': 'x-me'}, {'account_id': 'work'}, {'label': 'Post it'}, {'label': 'Open on X'},
                    {'body': 'a' * 281}, {'body': 'a' * 257 + link}, {'body': '你' * 141}, {'body': ' '}, {'media': ['a.png']},
                    {'reply_to': None, 'conversation_id': CONV, 'body': 'a' * 10001}):
            with self.assertRaises(ValueError, msg=bad):
                self.save(**bad)
        self.save(body='a' * 256 + link)
        self.save(body='a' * 280, label='Reply')
        self.accounts[0] = {**ACCOUNT, 'long_posts': True}
        self.save(body='a' * 2000)
        self.assertEqual(self.row()['next_action']['max_length'], 25000)

    def test_edit_keeps_the_x_limit(self):
        action = self.ready()
        with self.assertRaises(ValueError):
            n.revise(action, body='a' * 281)
        self.assertEqual(n.revise(action, body='Shorter.')['body'], 'Shorter.')
        self.assertNotEqual(n.revise(action, body='Shorter.')['revision'], action['revision'])
        revised = self.app().action('revise_draft', {'suggestion_id': self.sid, 'revision': action['revision'], 'body': 'Yes, they do.'},
                                    owner_dashboard=True)
        self.assertEqual(revised['status'], 'revised')
        current = self.row()['next_action']
        self.assertEqual((current['body'], current['reply_to'], current.get('subject')), ('Yes, they do.', MENTION_ID, None))

    def test_approved_reply_sends_once_and_card_says_posted(self):
        self.ready()
        self.confirm()
        self.confirm('second-click')
        calls = []
        self.app().action_step(self.transport(send=lambda action, job: (calls.append(action), {'message_id': 'x:1979000000000000005'})[1]))
        self.app().action_step(self.transport(send=lambda *_: self.fail('Duplicate send')))
        self.assertEqual([c['body'] for c in calls], ['Yes, shared inboxes sync too.'])
        self.assertEqual(self.job()['status'], 'sent')
        self.assertIn('✓ Reply posted on X to Ada at ', str(a.card(self.row())))
        # Ada's answer to that reply comes back to the card.
        with patch.object(a, 'now', return_value='2099-01-01T00:00:00+00:00'):
            self.store.commit_page('x-sf', {}, x.events(ACCOUNT, page([mention(id='1980000000000000001', at=4070908800000, text='Thanks!',
                                   reply_to={'id': '1979000000000000005', 'screen_name': 'acme', 'to_account': True, 'text': 'Yes'})]), H))
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM events WHERE kind='x.mention' ORDER BY occurred_at DESC LIMIT 1")]
        self.assertEqual(list(self.store.card_replies(rows).values())[0]['suggestion_id'], self.sid)

    def test_approved_dm_card_says_message_sent(self):
        self.ready(reply_to=None, conversation_id=CONV, label='Send')
        self.confirm()
        self.app().action_step(self.transport(send=lambda *_: {'message_id': 'xdm:1979000000000000007'}))
        self.assertIn('✓ X message sent to Ada at ', str(a.card(self.row())))

    def test_busy_browser_waits_without_sending(self):
        self.ready()
        self.confirm()
        def busy(_): raise n.XBusy()
        self.assertEqual(self.app().action_step(self.transport(preflight=busy, send=lambda *_: self.fail('sent while busy'))), 'waiting')
        self.assertEqual(self.job()['status'], 'queued')
        with self.store.connect() as db:
            db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.assertEqual(self.app().action_step(self.transport(send=lambda *_: {'not_sent': True})), 'waiting')
        self.assertEqual(self.job()['status'], 'queued')

    def test_unknown_outcome_never_resends(self):
        self.ready()
        self.confirm()
        self.app().action_step(self.transport(send=lambda *_: {}))
        self.assertEqual(self.job()['status'], 'uncertain')
        self.assertIn('this X reply', self.job()['error'])
        with self.store.connect() as db:
            db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.app().action_step(self.transport(reconcile=lambda *_: {}, send=lambda *_: self.fail('resent')))
        self.assertEqual(self.job()['error'], 'I couldn’t confirm whether this X reply was sent. I’m checking X; I won’t send it again.')
        with self.store.connect() as db:
            db.execute('UPDATE action_jobs SET retry_at=NULL')
        self.app().action_step(self.transport(reconcile=lambda *_: {'message_id': 'x:1979000000000000005'}))
        self.assertEqual(self.job()['status'], 'sent')

    def test_signed_out_blocks_cleanly(self):
        self.ready()
        self.confirm()
        def out(_): raise ValueError('X is signed out in the OpenClaw browser. Nothing was sent.')
        self.assertEqual(self.app().action_step(self.transport(preflight=out, send=lambda *_: self.fail('sent'))), 'blocked')
        self.assertEqual(self.job()['error'], 'X is signed out in the OpenClaw browser. Nothing was sent.')


class XTransportTest(unittest.TestCase):
    config = {'accounts': [ACCOUNT], 'x_inbox': '/bin/x-inbox-test'}
    reply = {'kind': 'x', 'account_id': 'x-sf', 'handle': 'acme', 'reply_to': MENTION_ID, 'to_screen_name': 'ada', 'body': 'Yes!', 'max_length': 280}
    dm = {'kind': 'x', 'account_id': 'x-sf', 'handle': 'acme', 'conversation_id': CONV, 'to_name': 'Ada', 'body': 'Hi Ada', 'max_length': 10000}

    def test_default_reader_is_the_bundled_one(self):
        self.assertEqual(n.XTransport({'accounts': [ACCOUNT]}).cli(self.reply), str(n.X_INBOX))
        self.assertTrue(n.X_INBOX.exists())
        self.assertEqual(n.X_INBOX, x.READER)

    def test_preview_first_then_send_with_the_job_token(self):
        t, seen = n.XTransport(self.config), []
        def run(args, **_):
            seen.append(args)
            if '--yes' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({'sent': 'sent', 'id': '1979000000000000005'}), '')
            return subprocess.CompletedProcess(args, 0, json.dumps({'account': {'id': '2'}, 'reply_to': {'id': MENTION_ID, 'screen_name': 'ada'}}), '')
        with patch.object(n.subprocess, 'run', side_effect=run):
            t.preflight(self.reply)
            self.assertEqual(t.send(self.reply, {'id': 'J1'}), {'message_id': 'x:1979000000000000005'})
        self.assertEqual(seen[0], ['/bin/x-inbox-test', 'send', '--handle', 'acme', '--primary', 'owner', '--reply-to', MENTION_ID, '--text', 'Yes!', '--json'])
        self.assertEqual(seen[1][seen[1].index('--send-id') + 1], 'assistant-J1')
        self.assertIn('--yes', seen[1])
        long_posts = {'accounts': [{**ACCOUNT, 'long_posts': True}], 'x_inbox': '/bin/x-inbox-test'}
        with patch.object(n.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({'sent': 'sent', 'id': '77'}), '')) as call:
            self.assertEqual(n.XTransport(long_posts).send(self.dm, {'id': 'J2'}), {'message_id': 'xdm:77'})
        self.assertIn('--conversation', call.call_args.args[0])
        self.assertNotIn('--long', call.call_args.args[0])
        with patch.object(n.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as call:
            n.XTransport(long_posts).send(self.reply, {'id': 'J3'})
        self.assertIn('--long', call.call_args.args[0])

    def test_preflight_refuses_cleanly_and_maps_busy(self):
        t = n.XTransport(self.config)
        other = subprocess.CompletedProcess([], 0, json.dumps({'reply_to': {'id': '5'}}), '')
        with patch.object(n.subprocess, 'run', return_value=other), self.assertRaisesRegex(ValueError, 'Nothing was sent'):
            t.preflight(self.reply)
        for stderr, message in (('x-inbox: X is signed out in the OpenClaw browser', 'X is signed out in the OpenClaw browser. Nothing was sent.'),
                                ('x-inbox: @acme is not signed in in the OpenClaw browser', 'X is signed out in the OpenClaw browser. Nothing was sent.'),
                                ("x-inbox: this is an encrypted X Chat conversation; sending there isn't supported yet", 'I can’t send in this encrypted X chat yet. Nothing was sent.'),
                                ('x-inbox: the post to reply to is no longer available on X', 'That X post is no longer available. Nothing was sent.'),
                                ('x-inbox: boom', 'I couldn’t reach X. Nothing was sent; tap Send again.')):
            with patch.object(n.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', stderr)), self.assertRaises(ValueError) as e:
                t.preflight(self.dm)
            self.assertEqual(str(e.exception), message)
        with patch.object(n.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2, '', 'x-inbox: the OpenClaw browser is busy')), self.assertRaises(n.XBusy):
            t.preflight(self.reply)

    def test_send_outcomes_never_resend_on_doubt(self):
        t = n.XTransport(self.config)
        cases = [(subprocess.CompletedProcess([], 4, json.dumps({'sent': 'failed', 'status': 187}), ''), {'rejected': 187}),
                 (subprocess.CompletedProcess([], 4, json.dumps({'sent': 'not-sent', 'status': 'encrypted'}), ''), {'rejected': 'encrypted'}),
                 (subprocess.CompletedProcess([], 4, json.dumps({'sent': 'uncertain'}), ''), {}),
                 (subprocess.CompletedProcess([], 2, '', 'x-inbox: this send was already attempted; not sending again'), {}),
                 (subprocess.CompletedProcess([], 1, 'Traceback', ''), {}),
                 (subprocess.CompletedProcess([], 2, '', 'x-inbox: the OpenClaw browser is busy'), {'not_sent': True})]
        for result, expected in cases:
            with patch.object(n.subprocess, 'run', return_value=result):
                self.assertEqual(t.send(self.reply, {'id': 'J'}), expected)
        for out, expected in (({'found': True, 'id': '9'}, {'message_id': 'x:9'}), ({'found': False, 'attempted': True}, {})):
            with patch.object(n.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(out), '')) as call:
                self.assertEqual(t.reconcile(self.reply, {'id': 'J'}), expected)
            self.assertEqual(call.call_args.args[0][1:], ['verify', '--send-id', 'assistant-J'])
        with self.assertRaisesRegex(ValueError, 'X sending isn’t set up'):
            n.ActionTransport(None, None).send(self.reply, {'id': 'J'})
        router = n.ActionTransport(object(), object(), None, t)
        self.assertIs(router.pick(self.dm), t)


class XInboxSend(unittest.TestCase):
    """x-inbox send itself, with the browser mocked: what it runs, what it logs, and that it never sends twice."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / 'sends.jsonl'
        self.calls, self.navigated, self.answers = [], [], {}

        class Lock:
            def close(self): pass
        for target, value in (('SENDS_LOG', self.log), ('with_tab', lambda url, fn, switching=False: (self.calls.append(('tab', switching)), fn('T1'))[1]),
                              ('one_at_a_time', lambda: Lock()), ('evaluate', self.evaluate), ('oc', lambda *args, **_: self.navigated.append(args) or '')):
            p = patch.object(x_inbox, target, value)
            p.start()
            self.addCleanup(p.stop)

    def evaluate(self, target, js, timeout_ms=90000):
        params = json.loads(js.split('const P = ', 1)[1].split(';\n', 1)[0])
        step = params.get('mode') or ('compose-send' if params['send'] else 'compose-check')
        self.calls.append((step, params, [json.loads(l)['result'] for l in self.log.read_text().splitlines()] if self.log.exists() else []))
        answer = self.answers.get(step)
        if isinstance(answer, Exception):
            raise answer
        account = {'id': '777', 'screen_name': params.get('handle', 'acme')}
        return answer or {'resolve': {'account': account, 'reply_to': {'id': MENTION_ID, 'screen_name': 'ada', 'name': 'Ada', 'text': 'Does it sync?'},
                                      'conversation': {'id': CONV, 'with': 'Ada', 'screen_names': ['ada']}},
                          'dm': {'account': account, 'sent': 'sent', 'id': '1979000000000000007'}, 'compose-check': {'checked': True},
                          'compose-send': {'sent': 'sent', 'id': '1979000000000000005'}, 'back': {'account': account}}.get(step, {})

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['x-inbox', *args]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                x_inbox.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def send(self, *args):
        return self.main('send', '--handle', 'acme', '--primary', 'owner', *args)

    def steps(self):
        return [c[0] for c in self.calls]

    def entries(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_preview_writes_nothing(self):
        code, out, _ = self.send('--reply-to', MENTION_ID, '--text', 'Yes!')
        self.assertEqual(code, 0)
        self.assertIn('Would reply on X as @acme to @ada (Ada): “Does it sync?”', out)
        self.assertEqual(self.steps(), ['tab', 'resolve'])
        self.assertEqual(self.calls[0], ('tab', False))  # a reply preview never switches accounts
        self.assertFalse(self.log.exists())
        self.assertEqual(self.navigated, [])
        code, out, _ = self.send('--conversation', CONV, '--text', 'Hi', '--json')
        self.assertEqual((code, json.loads(out)['conversation']['with']), (0, 'Ada'))
        self.assertNotIn('dm', self.steps())
        self.assertFalse(self.log.exists())
        self.assertEqual(self.main('send', '--handle', 'acme', '--reply-to', MENTION_ID, '--text', 'Yes!')[0], 2)  # --primary is required

    def test_check_fills_the_composer_but_never_clicks(self):
        code, out, _ = self.send('--reply-to', MENTION_ID, '--text', 'Yes & no?', '--check')
        self.assertEqual(code, 0)
        self.assertIn('checked in X’s composer', out)
        self.assertEqual(self.steps(), ['tab', 'resolve', 'compose-check', 'back'])
        self.assertEqual(self.navigated[0][-1], 'https://x.com/intent/post?in_reply_to=' + MENTION_ID + '&text=Yes%20%26%20no%3F')
        self.assertFalse(self.log.exists())
        # The primary account also needs custody: another run may have left
        # the global X session on a brand account before this command starts.
        self.calls.clear()
        code, _, _ = self.main('send', '--handle', 'acme', '--primary', 'acme',
                               '--reply-to', MENTION_ID, '--text', 'Yes!', '--check')
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[0], ('tab', True))
        self.assertTrue(self.calls[1][1]['switch'])
        self.assertFalse(self.log.exists())

    def test_reply_logs_intent_before_the_one_click_and_switches_back(self):
        code, out, _ = self.send('--reply-to', MENTION_ID, '--text', 'Yes!', '--send-id', 'assistant-J1', '--yes', '--json')
        self.assertEqual((code, json.loads(out)['sent'], json.loads(out)['id']), (0, 'sent', '1979000000000000005'))
        self.assertEqual(self.steps(), ['tab', 'resolve', 'compose-check', 'compose-send', 'back'])
        self.assertEqual(self.calls[0], ('tab', True))
        resolve, send = self.calls[1][1], self.calls[3]
        self.assertTrue(resolve['switch'] and resolve['keep'])
        self.assertEqual(send[2], ['intent'])  # logged before the click
        self.assertEqual((send[1]['text'], send[1]['reply_to'], send[1]['author']), ('Yes!', MENTION_ID, 'ada'))
        self.assertEqual([e['result'] for e in self.entries()], ['intent', 'sent'])
        self.assertEqual(self.entries()[0]['token'], 'assistant-J1')

    def test_dm_once_with_a_request_id_from_the_token(self):
        code, out, _ = self.send('--conversation', CONV, '--text', 'Hi Ada', '--send-id', 'assistant-J2', '--yes', '--json')
        self.assertEqual((code, json.loads(out)['sent']), (0, 'sent'))
        self.assertEqual(self.steps().count('dm'), 1)
        dm = next(c for c in self.calls if c[0] == 'dm')
        self.assertEqual(dm[2], ['intent'])
        self.assertEqual(dm[1]['request_id'], str(uuid.uuid5(uuid.NAMESPACE_URL, 'x-inbox:assistant-J2')))
        self.assertEqual((dm[1]['conversation'], dm[1]['text']), (CONV, 'Hi Ada'))

    def test_a_logged_token_is_never_sent_again(self):
        for result in ('intent', 'uncertain', 'sent'):
            self.log.write_text(json.dumps({'at': '2026-10-16T10:00:00+00:00', 'token': 'assistant-J3', 'result': result}) + '\n')
            self.calls.clear()
            code, _, err = self.send('--conversation', CONV, '--text', 'Hi', '--send-id', 'assistant-J3', '--yes', '--json')
            self.assertEqual(code, 2)
            self.assertIn('not sending again', err)
            self.assertEqual(self.calls, [])
        self.log.write_text('{broken\n')
        code, _, err = self.send('--conversation', CONV, '--text', 'Hi', '--send-id', 'assistant-J4', '--yes', '--json')
        self.assertEqual((code, self.calls), (2, []))
        self.assertIn('damaged', err)

    def test_uncertain_and_refused_outcomes(self):
        self.answers['dm'] = RuntimeError('tab crashed')
        code, out, _ = self.send('--conversation', CONV, '--text', 'Hi', '--send-id', 'assistant-J5', '--yes', '--json')
        self.assertEqual((code, json.loads(out)['sent']), (4, 'uncertain'))
        self.assertEqual([e['result'] for e in self.entries()], ['intent', 'uncertain'])
        self.answers['compose-send'] = {'sent': 'failed', 'status': 187, 'detail': 'duplicate'}
        code, out, _ = self.send('--reply-to', MENTION_ID, '--text', 'Yes', '--send-id', 'assistant-J6', '--yes', '--json')
        self.assertEqual((code, json.loads(out)['status']), (4, 187))

    def test_encrypted_signed_out_and_busy_stop_before_anything_is_written(self):
        self.answers['resolve'] = {'error': 'encrypted'}
        code, out, _ = self.send('--conversation', 'e123', '--text', 'Hi', '--send-id', 'assistant-J7', '--yes', '--json')
        self.assertEqual((code, json.loads(out)['sent'], json.loads(out)['status']), (4, 'not-sent', 'encrypted'))
        self.assertIn('encrypted X Chat', json.loads(out)['detail'])
        self.answers['resolve'] = {'error': 'signed-out'}
        code, _, err = self.send('--reply-to', MENTION_ID, '--text', 'Hi', '--json')
        self.assertEqual(code, 2)
        self.assertIn('signed out', err)
        self.answers['resolve'] = {'error': 'wrong-account'}
        code, _, err = self.send('--reply-to', MENTION_ID, '--text', 'Hi', '--send-id', 'assistant-J8', '--yes', '--json')
        self.assertEqual(code, 2)
        self.assertIn('busy', err)
        self.assertNotIn('dm', self.steps())
        self.assertNotIn('compose-send', self.steps())
        self.assertFalse(self.log.exists())

    def test_length_limits(self):
        code, _, err = self.send('--reply-to', MENTION_ID, '--text', 'a' * 281)
        self.assertEqual(code, 2)
        self.assertIn('longer than X allows (281 of 280', err)
        self.assertEqual(self.send('--reply-to', MENTION_ID, '--text', 'a' * 281, '--long')[0], 0)
        self.assertEqual(self.send('--conversation', CONV, '--text', 'a' * 10001)[0], 2)
        self.assertEqual(x_inbox.x_length('hi https://example.com/a 你好 😀'), 34)
        self.assertEqual(x_inbox.x_length('a' * 100), n.x_length('a' * 100))

    def test_verify_reads_the_log_then_x(self):
        self.log.write_text(json.dumps({'at': 'now', 'token': 'T1', 'result': 'sent', 'id': '55'}) + '\n'
                            + json.dumps({'at': 'now', 'token': 'T2', 'result': 'intent', 'handle': 'acme', 'primary': 'owner',
                                          'conversation': CONV, 'request_id': 'R2', 'text': 'Hi', 'at_ms': 1000}) + '\n'
                            + json.dumps({'at': 'now', 'token': 'T2', 'result': 'uncertain'}) + '\n')
        self.assertEqual(json.loads(self.main('verify', '--send-id', 'T1')[1]), {'found': True, 'id': '55', 'via': 'log'})
        self.assertEqual(self.calls, [])
        self.answers['verify-dm'] = {'found': ['1979000000000000007']}
        self.assertEqual(json.loads(self.main('verify', '--send-id', 'T2')[1])['id'], '1979000000000000007')
        verify = next(c for c in self.calls if c[0] == 'verify-dm')[1]
        self.assertEqual((verify['request_id'], verify['switch']), ('R2', True))
        self.answers['verify-dm'] = {'found': ['1', '2']}
        self.assertFalse(json.loads(self.main('verify', '--send-id', 'T2')[1])['found'])
        self.assertEqual(json.loads(self.main('verify', '--send-id', 'T9')[1])['attempted'], None)


if __name__ == '__main__':
    unittest.main()


class BrowserDown(unittest.TestCase):
    """The shared browser crashed or hung: the read says so (not a vague failure or a sign-in), retries soon and
    starts that browser again; never the Gateway, at most once per 15 minutes."""
    def setUp(self):
        a.BROWSER_RECOVERED.clear()
        self.addCleanup(a.BROWSER_RECOVERED.clear)

    def test_an_unreachable_browser_is_named_and_restarted(self):
        recover = MagicMock(return_value=True)
        helpers = types.SimpleNamespace(**{**vars(H), 'recover_browser': recover})
        down = subprocess.CompletedProcess([], 2, '', 'x-inbox: browser command failed: Error: browserType.connectOverCDP: Timeout 9000ms exceeded.')
        with patch.object(a.subprocess, 'run', return_value=down):
            with self.assertRaises(a.SourceError) as e:
                x.Source({'x_inbox': '/bin/x-inbox-test'}, ACCOUNT, helpers).page(5, {})
        self.assertEqual((str(e.exception), e.exception.retry_after), ('x_browser_unreachable', 300))
        recover.assert_called_once_with('openclaw', own_cdp=False, config={'x_inbox': '/bin/x-inbox-test'})
        self.assertEqual(a.browser_read_failure('linkedin', 'browser command failed: Error: browser request timed out'),
                         'linkedin_browser_unreachable')
        self.assertEqual(a.browser_read_failure('x', 'browser command failed: private title'), 'x_browser_failed')

    def run_recover(self, status, own_cdp=False):
        calls = []

        def fake(cmd, **kw):
            if cmd[0] != 'openclaw':  # the exclusive lease is tested separately
                return subprocess.CompletedProcess(cmd, 0, '', '')
            calls.append(cmd[4:])
            if cmd[4:] == ['--json', 'status']:
                return subprocess.CompletedProcess(cmd, 0 if status else 1, json.dumps(status or {}), '')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with patch.object(a.subprocess, 'run', side_effect=fake):
            done = a.recover_browser('openclaw', own_cdp=own_cdp)
        return done, calls

    def test_recover_restarts_only_a_hung_or_stopped_browser(self):
        done, calls = self.run_recover({'running': True, 'cdpReady': False, 'cdpHttp': False})
        self.assertTrue(done)
        self.assertEqual(calls, [['--json', 'status'], ['stop'], ['start']])
        self.assertEqual(self.run_recover({'running': False})[1], [])  # once per 15 minutes
        a.BROWSER_RECOVERED.clear()
        self.assertEqual(self.run_recover({'running': False})[1], [['--json', 'status'], ['start']])
        a.BROWSER_RECOVERED.clear()
        self.assertEqual(self.run_recover({'running': True, 'cdpReady': True, 'cdpHttp': True}), (False, [['--json', 'status']]))
        a.BROWSER_RECOVERED.clear()
        self.assertEqual(self.run_recover(None), (False, [['--json', 'status']]))  # Gateway not answering: leave it
        a.BROWSER_RECOVERED.clear()
        self.assertEqual(self.run_recover({'running': False}, own_cdp=True), (False, []))  # an account's own browser
