"""Sign in with Intercom for a paired self-hosted Machine, started on its own dashboard: the Machine signs a start
request with its own key (fleet.py connect_start), the control plane seals the token for this Machine's own key
(network_crypto.open_box), the Fleet picks it up once when the inbox says one waits (connects_round), and
onboarding.connect_delivered checks it (GET /me) before keeping it, turning replies on only for the owner's own
sign-in. No live calls: Intercom is the fake from test_intercom_replies, the relay a stub."""
import contextlib
import json
import logging
import sqlite3
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import assistant
import fleet
import network
import network_crypto as nc
import onboarding
from test_intercom_replies import Fake, REPLY
from test_connectors import a

TOKEN = REPLY


def context(device, cid):
    return {'p': 'connect', 't': device, 'r': cid}


class Sealing(unittest.TestCase):
    def test_only_this_machine_opens_it_and_only_bound_to_this_connect(self):
        private = nc.new_keys()
        box = nc.unb64(nc.public_keys(private)['box'], 32)
        env = nc.seal_box({'command': 'connect'}, box, context('dev_1', 'dc_1'))
        self.assertEqual(nc.open_box(env, private, context('dev_1', 'dc_1')), {'command': 'connect'})
        for ctx in (context('dev_2', 'dc_1'), context('dev_1', 'dc_2')):
            with self.assertRaises(nc.SealError):
                nc.open_box(env, private, ctx)
        with self.assertRaises(nc.SealError):
            nc.open_box(env, nc.new_keys(), context('dev_1', 'dc_1'))  # another Machine's key
        with self.assertRaises(nc.SealError):
            nc.open_box({**env, 'alg': nc.ALG}, private, context('dev_1', 'dc_1'))  # never taken for a Machine's message
        changed = {**env, 'ct': nc.b64(bytes([nc.unb64(env['ct'])[0] ^ 1]) + nc.unb64(env['ct'])[1:])}
        with self.assertRaises(nc.SealError):
            nc.open_box(changed, private, context('dev_1', 'dc_1'))


class Relay:
    """The control plane's side, as seen by the Machine: connects waiting, handed out once, results recorded."""

    def __init__(self, private, items):
        self.private, self.items, self.calls = private, items, []
        self.start_url = 'https://app.themachine.computer/connect/start/' + 'A' * 32

    def call(self, method, path, body=None, timeout=40):
        self.calls.append((method, path, body))
        if (method, path) == ('POST', '/connect-start'):
            return {'url': self.start_url}
        if (method, path) == ('GET', '/connects'):
            items, self.items = self.items, []
            return {'device': 'dev_1', 'connects': items}
        return {'ok': True}


def fleet_with(test, items, config=None):
    """A Fleet as on a paired self-hosted Machine, its relay a stub, its state in a temporary database."""
    temp = tempfile.TemporaryDirectory()
    test.addCleanup(temp.cleanup)
    f = fleet.Fleet.__new__(fleet.Fleet)

    @contextlib.contextmanager
    def connect():
        db = sqlite3.connect(Path(temp.name) / 'fleet.sqlite')
        try:
            with db:
                yield db
        finally:
            db.close()
    f.connect = connect
    with f.connect() as db:
        fleet.ensure(db)
    f.config = config or {'credentials': {'fleet': '/token'}, 'accounts': []}
    f.private = nc.new_keys()
    f.relay = Relay(f.private, items)
    f.connects_due, f.got = False, []
    f.connect_delivered = lambda body: f.got.append(body) or {'ok': True, 'name': 'Example Support', 'replies': True}
    return f


class Start(unittest.TestCase):
    """Only this Machine starts a sign-in for itself: the request is signed with its own sign key."""

    def test_signed_with_its_own_key_and_only_the_control_planes_start_link_is_opened(self):
        f = fleet_with(self, [])
        self.assertEqual(f.signin_state('intercom'), {'state': 'none'})
        with self.assertLogs(fleet.LOG, logging.INFO) as logs:
            self.assertEqual(f.connect_start('intercom'), f.relay.start_url)
        [(method, path, body)] = f.relay.calls
        self.assertEqual((method, path), ('POST', '/connect-start'))
        self.assertEqual(set(body), {'source', 'another', 'nonce', 'at', 'sig'})
        self.assertEqual((body['source'], body['another']), ('intercom', False))
        self.assertLess(abs(body['at'] - time.time()), 5)
        # as the control plane checks it (device_connect.start_message), with the sign key this Machine published
        message = fleet.START_PREFIX + json.dumps({k: body[k] for k in ('source', 'another', 'nonce', 'at')}, sort_keys=True, separators=(',', ':')).encode()
        sign = nc.unb64(nc.public_keys(f.private)['sign'], 32)
        self.assertTrue(nc.ed25519_verify(sign, message, nc.unb64(body['sig'], 64)))
        self.assertFalse(nc.ed25519_verify(sign, message.replace(b'"another":false', b'"another":true'), nc.unb64(body['sig'], 64)))
        self.assertNotIn(f.relay.start_url.rsplit('/', 1)[1], '\n'.join(logs.output))  # the link is not logged
        self.assertEqual(f.signin_state('intercom'), {'state': 'started'})
        self.assertEqual(f.signin_state('intercom', clock=lambda: time.time() + fleet.SIGNIN_EXPIRES + 1), {'state': 'stopped'})
        nonce = body['nonce']
        f.connect_start('intercom')
        self.assertNotEqual(f.relay.calls[-1][2]['nonce'], nonce)  # a new one each time

    def test_anything_but_the_start_page_of_its_own_control_plane_is_refused(self):
        for url in ('https://evil.example/connect/start/' + 'A' * 32, 'http://app.themachine.computer/connect/start/' + 'A' * 32,
                    'https://app.themachine.computer/login?next=x', 'https://app.themachine.computer/connect/start/' + 'A' * 32 + '?x=1', ''):
            f = fleet_with(self, [])
            f.relay.start_url = url
            with self.assertRaises(ValueError):
                f.connect_start('intercom')
            self.assertEqual(f.signin_state('intercom'), {'state': 'none'})

    def test_only_listed_sources_and_only_with_keys(self):
        f = fleet_with(self, [])
        with self.assertRaises(ValueError):
            f.connect_start('gmail')
        f.relay.private = None
        with self.assertRaises(ValueError):
            f.connect_start('intercom')
        self.assertEqual(f.relay.calls, [])


class Round(unittest.TestCase):
    def fleet_with(self, items):
        return fleet_with(self, items)

    def sealed(self, f, cid, body):
        return {'id': cid, 'sealed': nc.seal_box(body, nc.unb64(nc.public_keys(f.private)['box'], 32), context('dev_1', cid))}

    def test_picked_up_when_the_inbox_says_so_opened_connected_and_reported(self):
        f = self.fleet_with([])
        body = {'command': 'connect', 'args': {'source': 'intercom', 'secrets': {'Intercom access token': TOKEN}, 'replies': True}}
        f.relay.items = [self.sealed(f, 'dc_1', body)]
        self.assertEqual(f.connects_round(), [])  # nothing said one waits: no call at all
        self.assertEqual(f.relay.calls, [])
        f.connects_due = True
        with self.assertLogs(fleet.LOG, logging.DEBUG) as logs:
            fleet.LOG.debug('start')
            self.assertEqual(f.connects_round(), ['dc_1'])
        self.assertEqual(f.got, [body])
        self.assertEqual(f.relay.calls[-1], ('POST', '/connects', {'id': 'dc_1', 'result': {'ok': True, 'name': 'Example Support', 'replies': True}}))
        self.assertNotIn(TOKEN, '\n'.join(logs.output))
        self.assertFalse(f.connects_due)
        # what the dashboard shows
        self.assertEqual(f.signin_state('intercom'), {'state': 'done', 'name': 'Example Support', 'replies': True})

    def test_one_it_cannot_open_is_reported_and_never_connected(self):
        f = self.fleet_with([])
        other = nc.new_keys()
        env = nc.seal_box({'command': 'connect'}, nc.unb64(nc.public_keys(other)['box'], 32), context('dev_1', 'dc_9'))
        f.relay.items, f.connects_due = [{'id': 'dc_9', 'sealed': env}], True
        f.connect_start('intercom')
        f.connects_round()
        self.assertEqual(f.got, [])
        self.assertEqual(f.signin_state('intercom')['state'], 'failed')  # the dashboard says so
        method, path, body = f.relay.calls[-1]
        self.assertEqual((method, path, body['result']['ok']), ('POST', '/connects', False))

    def test_the_inbox_hint_sets_it_due(self):
        source = Path(fleet.__file__).read_text()
        self.assertIn("if found.get('connects'):\n            self.connects_due = True", source)


class ConnectDelivered(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / 'config.json'
        self.path.write_text(json.dumps({'database': str(root / 'events.sqlite'), 'credentials': {}, 'accounts': []}))
        self.fake = Fake(self)
        patch('urllib.request.OpenerDirector.open', side_effect=self.fake.respond).start()
        patch.object(onboarding, 'assistant', return_value=a).start()
        self.addCleanup(patch.stopall)

    def args(self, **change):
        return {'source': 'intercom', 'answers': {}, 'secrets': {'Intercom access token': TOKEN}, **change}

    def test_checked_then_kept_and_replies_only_from_the_owners_own_sign_in(self):
        self.assertEqual(onboarding.connect_delivered(self.path, self.args(replies=True)), {'ok': True, 'name': 'Example Support', 'replies': True})
        config = json.loads(self.path.read_text())
        [account] = config['accounts']
        self.assertEqual((account['workspace_id'], account['admin_id'], account['reply_admin_id']), ('abc123', '42', '42'))
        self.assertNotIn(TOKEN, self.path.read_text())
        self.assertEqual(self.fake.requests[0][:2], ('GET', '/me'))  # checked before it is kept
        self.assertEqual(self.fake.writes(), [])  # nothing sent to check it

    def test_without_the_replies_flag_it_only_reads(self):
        self.assertEqual(onboarding.connect_delivered(self.path, self.args()), {'ok': True, 'name': 'Example Support'})
        [account] = json.loads(self.path.read_text())['accounts']
        self.assertNotIn('reply_credential', account)

    def test_a_token_intercom_turns_down_is_not_kept(self):
        self.fake.me['app']['id_code'] = 'bad code!'  # not a profile that checks out
        with self.assertRaises(onboarding.SetupError):
            onboarding.connect_delivered(self.path, self.args(replies=True))
        self.assertEqual(json.loads(self.path.read_text())['accounts'], [])

    def test_only_listed_sources_and_only_the_key_they_ask_for(self):
        for args in (self.args(source='gmail'), self.args(source='github'), self.args(secrets={}),
                     self.args(secrets={'Intercom access token': TOKEN, 'Other': 'x' * 10}),
                     self.args(secrets={'Intercom access token': 'has space in it'}), 'not a dict'):
            with self.assertRaises(onboarding.SetupError):
                onboarding.connect_delivered(self.path, args)
        self.assertEqual(self.fake.requests, [])


class Dashboard(unittest.TestCase):
    """Connections, + Add on a self-hosted Machine: Sign in with Intercom once paired, else pair first."""

    def app(self, paired=True, manage=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'config.json'
        config = {'accounts': [], 'database': str(Path(temp.name) / 'events.sqlite'), **({'manage_url': manage} if manage else {})}
        path.write_text(json.dumps(config))
        app = object.__new__(assistant.App)
        app.config, app.config_path = config, path
        app.fleet = fleet_with(self, []) if paired else None
        return app

    def test_paired_it_signs_in_and_follows_how_it_goes(self):
        app = self.app()
        found = app.action('connect_sources', {}, owner_dashboard=True)
        [intercom] = [x for x in found['sources'] if x['service'] == 'intercom']
        self.assertTrue(intercom['signin'])
        self.assertEqual((found['paired'], found['pair_url']), (True, fleet.PAIR_PAGE))
        with self.assertRaises(ValueError):
            app.action('connect_signin', {'name': 'intercom'})  # only from the owner's dashboard
        self.assertEqual(app.action('connect_signin', {'name': 'intercom'}, owner_dashboard=True),
                         {'url': app.fleet.relay.start_url, 'state': 'started'})
        self.assertEqual(app.action('connect_signin_state', {'name': 'intercom'}, owner_dashboard=True), {'state': 'started'})
        for name in ('github', 'slack', ''):  # Gmail signs in with Google: test_device_grants.py
            with self.assertRaises(ValueError):
                app.action('connect_signin', {'name': name}, owner_dashboard=True)

    def test_not_paired_it_says_pair_first(self):
        app = self.app(paired=False)
        found = app.action('connect_sources', {}, owner_dashboard=True)
        self.assertFalse(found['paired'])
        with self.assertRaisesRegex(ValueError, 'Pair this Machine first'):
            app.action('connect_signin', {'name': 'intercom'}, owner_dashboard=True)

    def test_a_hosted_machine_keeps_its_setup_page(self):
        app = self.app(manage='https://app.example.org/setup')
        found = app.action('connect_sources', {}, owner_dashboard=True)
        self.assertNotIn('paired', found)
        self.assertFalse(any(x.get('signin') for x in found['sources']))
        with self.assertRaises(ValueError):
            app.action('connect_signin', {'name': 'intercom'}, owner_dashboard=True)


if __name__ == '__main__':
    unittest.main()
