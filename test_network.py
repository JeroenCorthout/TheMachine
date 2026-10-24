"""The Machine Network on a Machine (network_crypto.py, network.py): the crypto against its RFC vectors, sealed
questions and answers end to end between two Machines, pinned keys and key-change warnings, nothing ever in the
clear (a Machine without keys gets nothing, and anything unsealed is dropped), the outbox while the Network is away, delivery state and endpoint failover. A fake Network stands in
for the control plane; no network."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

spec = importlib.util.spec_from_file_location('network_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
fleet = a.fleet
import network
import network_crypto as nc

CONFIG = {'accounts': [{'id': 'work', 'email': 'owner@acme.example', 'service': 'gmail', 'boundary': 'work:Acme'}],
          'fleet': {'url': 'https://cp.example/api/fleet'}, 'credentials': {}}
NAMES = {'me1': 'Owner', 'pat1': 'Pat'}


class FakeNetwork:
    """The relay's mailbox for two members: it stores what it's given and can't open sealed bodies."""
    def __init__(self):
        self.keys, self.asks, self.down, self.hide_keys, self.old = {}, {}, False, set(), False

    def directory(self, me):
        return {'fleets': [{'id': 'fl_' + '1' * 16, 'kind': 'work', 'name': 'Acme', 'asks': ['relationships', 'meeting'], 'members': [
            {'member_id': m, 'name': NAMES[m], 'role': 'member', 'emails': [], 'keys': None if m in self.hide_keys else self.keys.get(m),
             **({'me': True} if m == me else {})} for m in NAMES]}]}


class FakeRelay:
    def __init__(self, net, me):
        self.net, self.me, self.calls = net, me, []

    def call(self, method, path, body=None, timeout=40):
        self.calls.append((method, path, body))
        if self.net.down:
            raise fleet.Unavailable('away')
        route = path.split('?')[0]
        if self.net.old and route in ('/keys', '/delivery'):  # a Network from before: no such route
            raise fleet.Off()
        if route == '/directory':
            return self.net.directory(self.me)
        if route == '/keys':
            self.net.keys[self.me] = {k: body[k] for k in ('v', 'box', 'sign', 'sig')}
            return {'ok': True}
        if route == '/ask':
            ids = {}
            for to in body.get('to') or [m for m in NAMES if m != self.me]:
                ask_id = f'a{len(self.net.asks) + 1}'
                sealed = (body.get('sealed') or {}).get(to)
                if sealed is None or body.get('question') is not None:  # the Network carries sealed messages only
                    raise ValueError('their Machine needs an update before it can get questions on The Machine Network')
                self.net.asks[ask_id] = {'id': ask_id, 'from': self.me, 'to': to, 'kind': body['kind'], 'request_id': body.get('request_id'),
                                         'question': None, 'sealed': sealed, 'status': 'queued'}
                ids[to] = ask_id
            return {'ids': ids}
        if route == '/inbox':
            asks, answers = [], []
            for r in self.net.asks.values():
                if r['to'] == self.me and r['status'] == 'queued':
                    r['status'] = 'delivered'
                    asks.append({'id': r['id'], 'fleet': 'work', 'fleet_id': 'fl_' + '1' * 16, 'from': r['from'], 'kind': r['kind'], 'request_id': r['request_id'],
                                 **({'question': {}, 'sealed': r['sealed']} if r['sealed'] else {'question': r['question']})})
                if r['from'] == self.me and r.get('answered') and not r.get('acked'):
                    answers.append({'id': r['id'], 'fleet': 'work', 'fleet_id': 'fl_' + '1' * 16, 'from': r['to'], 'kind': r['kind'], 'status': r['status'],
                                    'receipt': 'rc' + r['id'], **({'answer': None, 'sealed': r['answer_sealed']} if r.get('answer_sealed') else {'answer': r.get('answer')})})
            # a handoff its asker took back: what their Machine sealed for this one, until it acks
            withdrawn = [{'id': r['id'], 'fleet': 'work', 'fleet_id': 'fl_' + '1' * 16, 'from': r['from'], 'name': NAMES[r['from']], 'kind': r['kind'],
                          'sealed': r['withdrawn']} for r in self.net.asks.values() if r['to'] == self.me and r.get('withdrawn')]
            return {'asks': asks, 'answers': answers, 'withdrawn': withdrawn, 'ack': True}
        if route == '/withdraw':  # only the member who asked, sealed, and only a handoff (cloud/fleet.py withdraw)
            r = self.net.asks.get(body.get('id'))
            if not r or r['from'] != self.me or r['kind'] != 'handoff':
                raise ValueError('no such handoff of yours')
            if body.get('sealed') is None:
                raise ValueError('sealed only')
            r.update(status='withdrawn', withdrawn=body['sealed'] if r['status'] != 'queued' else None)
            return {'ok': True}
        if route == '/answer':
            r = self.net.asks[body['id']]
            r.update(status=body['status'], answered=body['status'] != 'waiting', answer=body.get('answer'), answer_sealed=body.get('sealed'))
            return {'ok': True}
        if route == '/ack':
            for item in body.get('answers', []):
                self.net.asks[item['id']]['acked'] = True
            for item in body.get('withdrawn', []):
                if self.net.asks[item['id']]['to'] == self.me:
                    self.net.asks[item['id']]['withdrawn'] = None
            return {'ok': True}
        if route == '/delivery':
            return {'delivery': {i: {'status': self.net.asks[i]['status'], 'delivered': self.net.asks[i]['status'] != 'queued'} for i in body['ids'] if i in self.net.asks}}
        return {'ok': True}


class Crypto(unittest.TestCase):
    def test_rfc_vectors(self):
        k = bytes.fromhex('a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4')
        u = bytes.fromhex('e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c')
        self.assertEqual(nc.x25519(k, u).hex(), 'c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552')
        alice = bytes.fromhex('77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a')
        self.assertEqual(nc.x25519_public(alice).hex(), '8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a')
        sk = bytes.fromhex('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb')
        self.assertEqual(nc.ed25519_public(sk).hex(), '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c')
        sig = nc.ed25519_sign(sk, b'\x72')
        self.assertEqual(sig.hex(), '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00')
        self.assertTrue(nc.ed25519_verify(nc.ed25519_public(sk), b'\x72', sig))
        self.assertFalse(nc.ed25519_verify(nc.ed25519_public(sk), b'x', sig))

    def test_pure_path_matches(self):
        """The stdlib code is what a Machine without `cryptography` runs: the same answers as the fast path."""
        fast, nc._cx = nc._cx, None
        try:
            alice = bytes.fromhex('77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a')
            self.assertEqual(nc.x25519_public(alice).hex(), '8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a')
            A, B = nc.new_keys(), nc.new_keys()
            ctx = {'p': 'ask', 'k': 'meeting', 'f': 'me1', 't': 'pat1', 'r': 'x'}
            env = nc.seal({'emails': ['sam@client.example']}, A, nc.public_keys(B), ctx)
        finally:
            nc._cx = fast
        self.assertEqual(nc.open_sealed(env, B, nc.public_keys(A), ctx), {'emails': ['sam@client.example']})

    def test_seal_binds_who_what_and_which(self):
        A, B, C = nc.new_keys(), nc.new_keys(), nc.new_keys()
        ctx = {'p': 'ask', 'k': 'relationships', 'f': 'me1', 't': 'pat1', 'r': 'req'}
        env = nc.seal({'about': 'client.example'}, A, nc.public_keys(B), ctx)
        self.assertNotIn('client', json.dumps(env))
        self.assertEqual(nc.open_sealed(env, B, nc.public_keys(A), ctx), {'about': 'client.example'})
        for bad in ({**ctx, 't': 'kim1'}, {**ctx, 'r': 'other'}, {**ctx, 'p': 'answer'}, {**ctx, 'k': 'brief'}):
            with self.assertRaises(nc.SealError):
                nc.open_sealed(env, B, nc.public_keys(A), bad)
        with self.assertRaises(nc.SealError):  # not for them
            nc.open_sealed(env, C, nc.public_keys(A), ctx)
        with self.assertRaises(nc.SealError):  # not from them
            nc.open_sealed(env, B, nc.public_keys(C), ctx)
        flipped = {**env, 'ct': nc.b64(bytes([nc.unb64(env['ct'])[0] ^ 1]) + nc.unb64(env['ct'])[1:])}
        with self.assertRaises(nc.SealError):
            nc.open_sealed(flipped, B, nc.public_keys(A), ctx)

    def test_keys_self_signed_and_kept_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'network-keys.json'
            first = nc.load_or_make(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(nc.load_or_make(path), first)
            public = nc.public_keys(first)
            self.assertRegex(nc.check_keys(public)['fingerprint'], r'^([0-9a-f]{4} ){5}[0-9a-f]{4}$')
            self.assertIsNone(nc.check_keys({**public, 'box': nc.public_keys(nc.new_keys())['box']}))


class Machines(unittest.TestCase):
    def machine(self, me, sealed=True, where=''):
        root = Path(self.temp.name) / where / me
        root.mkdir(parents=True, exist_ok=True)
        store = a.Store(root / 'events.sqlite')
        relay = FakeRelay(self.net, me)
        config = {**CONFIG, **({'network_keys': str(root / 'network-keys.json')} if sealed else {})}
        box = fleet.Fleet(store.connect, config, lambda: 'token', relay=relay)
        box.raw = relay
        return box

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.net = FakeNetwork()
        self.me, self.pat = self.machine('me1'), self.machine('pat1')
        for box in (self.me, self.pat, self.me):  # each publishes its keys, then sees the other's
            box.directory(refresh=True)

    def test_sealed_question_and_answer_end_to_end(self):
        self.assertIn('me1', self.net.keys); self.assertIn('pat1', self.net.keys)
        sent = self.me.ask('relationships', {'about': 'client.example'})
        stored = self.net.asks[sent['ids']['pat1']]
        self.assertIsNone(stored['question'])  # the Network only holds the sealed envelope
        self.assertNotIn('client.example', json.dumps(stored))
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            row = db.execute('SELECT * FROM fleet_in').fetchone()
        self.assertEqual(json.loads(row['question'])['about'], 'client.example')
        self.pat.decide(row['id'], 'once')
        self.assertIsNone(stored['answer'])
        self.assertIsNotNone(stored['answer_sealed'])
        self.me.step(wait=0)
        with self.me.connect() as db:
            out = db.execute('SELECT * FROM fleet_out').fetchone()
        self.assertEqual(out['status'], 'answered')
        self.assertTrue(stored.get('acked'))

    def test_key_change_is_warned_and_pinned(self):
        self.pat.relay.private = nc.new_keys()
        self.pat.relay.public = nc.public_keys(self.pat.relay.private)
        self.pat.directory(refresh=True)
        self.me.directory(refresh=True)
        changes = self.me.view()['sealing']['key_changes']
        self.assertEqual([c['member_id'] for c in changes], ['pat1'])
        self.me.ask('relationships', {'about': 'client.example'})  # sealed for the new keys
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fleet_in').fetchone()[0], 1)

    def test_no_downgrade_when_keys_disappear(self):
        self.net.hide_keys.add('pat1')
        self.me.directory(refresh=True)
        with self.assertRaises(ValueError):
            self.me.ask('relationships', {'about': 'client.example'})
        self.assertEqual(self.net.asks, {})

    def test_nothing_in_the_clear_to_or_from_a_machine_without_keys(self):
        self.net = FakeNetwork()
        self.me, old = self.machine('me1', where='new'), self.machine('pat1', sealed=False, where='new')
        self.me.directory(refresh=True); old.directory(refresh=True)
        with self.assertRaises(ValueError):  # pat1's Machine has no keys: it can't be asked
            self.me.ask('relationships', {'about': 'client.example'})
        with self.assertRaises(ValueError):  # and a Machine without keys can't ask
            old.ask('relationships', {'about': 'client.example'})
        self.assertEqual(self.net.asks, {})

    def test_tampered_or_unsealed_is_dropped(self):
        sent = self.me.ask('relationships', {'about': 'client.example'})
        stored = self.net.asks[sent['ids']['pat1']]
        stored['sealed'] = {**stored['sealed'], 'mac': stored['sealed']['mac'][::-1]}
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fleet_in').fetchone()[0], 0)
        self.assertEqual(stored['status'], 'declined')
        self.net.asks['x1'] = {'id': 'x1', 'from': 'me1', 'to': 'pat1', 'kind': 'relationships', 'request_id': 'r', 'question': {'about': 'client.example'},
                               'sealed': None, 'status': 'queued'}
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fleet_in').fetchone()[0], 0)
        # an unsealed answer to one of this Machine's questions is dropped too, never shown
        mine = self.me.ask('relationships', {'about': 'client.example'})['ids']['pat1']
        self.net.asks[mine].update(status='answered', answered=True, answer={'knows': True}, answer_sealed=None)
        self.me.step(wait=0)
        with self.me.connect() as db:
            self.assertNotIn('knows', json.dumps([dict(r) for r in db.execute('SELECT * FROM fleet_out')]))

    def test_outbox_keeps_answers_while_the_network_is_away_and_never_acts_twice(self):
        sent = self.me.ask('relationships', {'about': 'client.example'})
        ask_id = sent['ids']['pat1']
        self.pat.step(wait=0)
        self.net.down = True
        result = self.pat.decide(ask_id, 'once')
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(self.pat.relay.waiting(), 1)
        self.net.down = False
        self.net.asks[ask_id]['status'] = 'queued'  # the Network delivers it again: it never heard the answer
        with self.pat.connect() as db:
            db.execute("UPDATE network_outbox SET next_at='2000-01-01'")
        self.pat.step(wait=0)
        self.assertEqual(self.pat.relay.waiting(), 0)
        self.assertEqual(self.net.asks[ask_id]['status'], 'answered')
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fleet_in').fetchone()[0], 1)

    def handoff(self):
        sent = self.me.ask('handoff', {'title': 'Send Sam the pricing', 'summary': 'Sam wants pricing.'}, context='handoff:c1', to=['pat1'])
        ask_id = sent['ids']['pat1']
        self.pat.step(wait=0)
        with self.me.connect() as db:
            row = dict(db.execute('SELECT * FROM fleet_out WHERE id=?', (ask_id,)).fetchone())
        return ask_id, row

    def test_a_handoff_taken_back_reaches_their_machine_sealed_and_only_from_the_sender(self):
        ask_id, row = self.handoff()
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM fleet_in WHERE id=?', (ask_id,)).fetchone()[0], 'waiting')
        self.assertTrue(self.me.withdraw(row))
        withdraw = [b for _, p, b in self.me.raw.calls if p == '/withdraw'][-1]
        self.assertEqual(sorted(withdraw), ['id', 'sealed'])  # nothing else travels, and nothing in the clear
        self.assertNotIn('pricing', json.dumps(withdraw))
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            got = db.execute('SELECT status,choice FROM fleet_in WHERE id=?', (ask_id,)).fetchone()
            stage = db.execute('SELECT stage FROM fleet_requests WHERE id=?', (ask_id,)).fetchone()[0]
            log = db.execute("SELECT outcome FROM fleet_log WHERE direction='in' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual((tuple(got), stage, log), (('withdrawn', 'withdrawn'), 'withdrawn', 'taken back by Owner'))
        self.assertIsNone(self.net.asks[ask_id]['withdrawn'])  # acknowledged: the Network keeps nothing of it
        with self.pat.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM fleet_in')]
        self.assertEqual(fleet.telegram_message(rows, 'https://machine.example/')['text'].count('Taken back by Owner'), 1)
        with self.assertRaisesRegex(ValueError, 'answered or has expired'):  # nothing left to accept
            self.pat.decide(ask_id, 'once')
        # Pat's Machine can't take back what Owner asked, and the Network refuses it too
        self.assertFalse(self.pat.withdraw({'id': ask_id, 'fleet': self.pat.ref('work'), 'member_id': 'me1'}))

    def test_a_withdrawal_that_does_not_check_out_is_dropped(self):
        ask_id, row = self.handoff()
        other, _ = self.handoff()
        self.me.withdraw({**row, 'id': other})  # sealed for the other question
        sealed = self.net.asks[other]['withdrawn']
        self.net.asks[other]['withdrawn'] = None
        self.net.asks[ask_id].update(status='withdrawn', withdrawn=sealed)  # the relay moves it onto this one
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM fleet_in WHERE id=?', (ask_id,)).fetchone()[0], 'waiting')
        self.assertIsNone(self.net.asks[ask_id]['withdrawn'])  # dropped and acknowledged, never acted on
        self.net.asks[ask_id]['withdrawn'] = {'v': 1, 'ct': 'plain'}  # unsealed or made up: dropped too
        self.pat.step(wait=0)
        with self.pat.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM fleet_in WHERE id=?', (ask_id,)).fetchone()[0], 'waiting')

    def test_not_delivered_yet_is_shown(self):
        sent = self.me.ask('relationships', {'about': 'client.example'})
        self.me.delivery_round()
        asked = self.me.view()['asked']
        self.assertEqual([x['delivered'] for x in asked], [False])
        self.pat.step(wait=0)
        self.me.delivery_round()
        self.assertTrue(self.net.asks[sent['ids']['pat1']]['status'] != 'queued')


class OlderNetwork(Machines):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.net = FakeNetwork()
        self.net.old = True
        self.me, self.pat = self.machine('me1'), self.machine('pat1')
        for box in (self.me, self.pat, self.me):
            box.directory(refresh=True)

    def test_no_keys_route_sends_nothing_in_the_clear_and_is_never_off(self):
        self.assertEqual(self.net.keys, {})
        with self.assertRaises(ValueError):
            self.me.ask('relationships', {'about': 'client.example'})
        self.assertEqual(self.net.asks, {})
        self.me.step(wait=0)  # includes the delivery round: a 404 there is not "off"
        self.assertFalse(self.me.off)
        self.me.delivery_round()
        self.assertEqual(self.me.meta('network_delivery'), None)

    # the end-to-end tests of Machines don't apply to an older Network
    test_sealed_question_and_answer_end_to_end = test_key_change_is_warned_and_pinned = test_no_downgrade_when_keys_disappear = None
    test_tampered_or_unsealed_is_dropped = test_outbox_keeps_answers_while_the_network_is_away_and_never_acts_twice = None
    test_not_delivered_yet_is_shown = test_nothing_in_the_clear_to_or_from_a_machine_without_keys = None
    test_a_handoff_taken_back_reaches_their_machine_sealed_and_only_from_the_sender = test_a_withdrawal_that_does_not_check_out_is_dropped = None


class Failover(unittest.TestCase):
    def test_next_endpoint_when_one_is_away(self):
        relay = fleet.Relay('https://a.example/api/fleet', lambda: 't', ['https://b.example/api/fleet'])
        seen = []

        def call(base, method, path, body, timeout):
            seen.append(base)
            if base.startswith('https://a.'):
                raise urllib.error.URLError('down')
            return {'ok': base}
        relay._call = call
        self.assertEqual(relay.call('GET', '/directory'), {'ok': 'https://b.example/api/fleet'})
        self.assertEqual(relay.call('GET', '/directory'), {'ok': 'https://b.example/api/fleet'})
        self.assertEqual(seen, ['https://a.example/api/fleet', 'https://b.example/api/fleet', 'https://b.example/api/fleet'])

    def test_last_endpoint_error_is_raised(self):
        relay = fleet.Relay('https://a.example/api/fleet', lambda: 't')
        relay._call = lambda *args: (_ for _ in ()).throw(fleet.Unavailable('away'))
        with self.assertRaises(fleet.Unavailable):
            relay.call('GET', '/directory')


if __name__ == '__main__':
    unittest.main()
