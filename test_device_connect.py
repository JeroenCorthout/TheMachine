"""Sign in with Intercom for a paired self-hosted Machine: the control plane seals the token for this Machine's own key
(network_crypto.open_box), the Fleet picks it up once when the inbox says one waits (fleet.py connects_round), and
onboarding.connect_delivered checks it (GET /me) before keeping it, turning replies on only for the owner's own
sign-in. No live calls: Intercom is the fake from test_intercom_replies, the relay a stub."""
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def call(self, method, path, body=None, timeout=40):
        self.calls.append((method, path, body))
        if (method, path) == ('GET', '/connects'):
            items, self.items = self.items, []
            return {'device': 'dev_1', 'connects': items}
        return {'ok': True}


class Round(unittest.TestCase):
    def fleet_with(self, items):
        f = fleet.Fleet.__new__(fleet.Fleet)
        f.private = nc.new_keys()
        f.relay = Relay(f.private, items)
        f.connects_due, f.got = False, []
        f.connect_delivered = lambda body: f.got.append(body) or {'ok': True, 'name': 'Example Support', 'replies': True}
        return f

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

    def test_one_it_cannot_open_is_reported_and_never_connected(self):
        f = self.fleet_with([])
        other = nc.new_keys()
        env = nc.seal_box({'command': 'connect'}, nc.unb64(nc.public_keys(other)['box'], 32), context('dev_1', 'dc_9'))
        f.relay.items, f.connects_due = [{'id': 'dc_9', 'sealed': env}], True
        f.connects_round()
        self.assertEqual(f.got, [])
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


if __name__ == '__main__':
    unittest.main()
