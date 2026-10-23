"""Shared connectors (shared_connectors.py, fleet_trust.py): a Fleet admin shares Intercom once and teammates' Machines
read it, with the token sealed for each approved Machine only and checked against a signed grant. A fake Network
stands in for the control plane, honest or not; no network, no Intercom."""
import hashlib
import copy
import importlib.util
import json
import secrets
import stat
import tempfile
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('shared_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
fleet = a.fleet
import fleet_trust as ft
import network_crypto as nc

FLEET = 'fl_' + '2' * 16
TOKEN = 'fixture-intercom-token'
NEW_TOKEN = 'fixture-replacement-token'


class FakeNetwork:
    """The control plane's part: membership and roles, public keys, trust statements, the shared flag, and the
    mailbox. It stores what it's given; it never gets the token."""
    def __init__(self):
        self.people = {'own1': ('Olive', 'owner'), 'adm2': ('Ada', 'admin'), 'mem3': ('Mo', 'member')}
        self.keys, self.asks, self.trust, self.shared, self.calls = {}, {}, [], {}, []

    def directory(self, me):
        if me not in self.people:
            return {'fleets': []}
        group = {'id': FLEET, 'kind': 'work', 'name': 'Acme', 'asks': ['relationships'], 'trust': copy.deepcopy(self.trust),
                 'shared': [copy.deepcopy(s) for s in self.shared.values()],
                 'members': [{'member_id': m, 'name': n, 'label': n, 'role': r, 'account_email': m + '@acme.example', 'emails': [],
                              'keys': self.keys.get(m), **({'me': True} if m == me else {})} for m, (n, r) in self.people.items()]}
        return {'fleets': [group]}


class FakeRelay:
    def __init__(self, net, me):
        self.net, self.me = net, me

    def call(self, method, path, body=None, timeout=40):
        route = path.split('?')[0]
        self.net.calls.append((self.me, method, route, copy.deepcopy(body)))
        if route == '/directory':
            return self.net.directory(self.me)
        if route == '/me':
            return {'ok': True}
        if route == '/keys':
            self.net.keys[self.me] = {k: body[k] for k in ('v', 'box', 'sign', 'sig')}
            return {'ok': True}
        if route == '/trust':
            self.net.trust.append(body['item'])
            return {'ok': True}
        if route == '/shared':
            op, service = body['op'], body['service']
            if op == 'set':
                self.net.shared[service] = {'service': service, 'grant': body['grant'], 'name': body['name'], 'workspace': body['workspace'],
                                            'by': self.me, 'held': [], 'grants': []}
            elif op == 'off':
                self.net.shared.pop(service, None)
            elif op == 'grant':
                self.net.shared[service]['grants'].append(body['item'])
            elif op == 'held' and self.net.shared.get(service, {}).get('grant') == body['grant']:
                self.net.shared[service]['held'].append(self.me)
            return {'ok': True}
        if route == '/ask':
            ids = {}
            for to in body['to']:
                ask_id = f'q{len(self.net.asks) + 1}'
                if body.get('question') is not None or to not in (body.get('sealed') or {}):
                    raise ValueError('sealed only')
                self.net.asks[ask_id] = {'id': ask_id, 'from': self.me, 'to': to, 'kind': body['kind'], 'request_id': body.get('request_id'),
                                         'sealed': body['sealed'][to], 'status': 'queued'}
                ids[to] = ask_id
            return {'ids': ids}
        if route == '/inbox':
            asks, answers = [], []
            for r in self.net.asks.values():
                if r['to'] == self.me and r['status'] == 'queued':
                    r['status'] = 'delivered'
                    asks.append({'id': r['id'], 'fleet': 'work', 'fleet_id': FLEET, 'from': r['from'], 'kind': r['kind'], 'request_id': r['request_id'],
                                 **({'question': {}, 'sealed': r['sealed']} if r.get('sealed') else {'question': r.get('clear')})})
                if r['from'] == self.me and r.get('answered') and not r.get('acked'):
                    answers.append({'id': r['id'], 'fleet_id': FLEET, 'from': r['to'], 'kind': r['kind'], 'status': r['status'], 'receipt': 'rc' + r['id'],
                                    'answer': None, **({'sealed': r['answer_sealed']} if r.get('answer_sealed') else {})})
            # shared: the control plane's stamp of what is shared now (fleet_shared.stamp): it changes on a stop or a replace
            stamp = {'shared': hashlib.sha256(json.dumps(sorted((k, v['grant']) for k, v in self.net.shared.items())).encode()).hexdigest()[:16]} \
                if 'shared=' in path and self.me in self.net.people else {}
            return {'asks': asks, 'answers': answers, 'ack': True, **stamp}
        if route == '/answer':
            self.net.asks[body['id']].update(status=body['status'], answered=True, answer_sealed=body.get('sealed'))
            return {'ok': True}
        if route == '/ack':
            for item in body.get('answers', []):
                self.net.asks[item['id']]['acked'] = True
            return {'ok': True}
        return {'ok': True}


class Shared(unittest.TestCase):
    def machine(self, me, intercom=False):
        root = Path(self.temp.name) / me
        root.mkdir(parents=True, exist_ok=True)
        config = {'database': str(root / 'events.sqlite'), 'network_keys': str(root / 'network-keys.json'), 'credentials': {},
                  'fleet': {'url': 'https://cp.example/api/fleet'},
                  'accounts': [{'id': 'work', 'email': me + '@acme.example', 'service': 'gmail', 'boundary': 'work:Acme'}]}
        if intercom:
            secret = root / 'intercom.env'
            secret.write_text((intercom if isinstance(intercom, str) else TOKEN) + '\n')
            config['credentials']['intercom'] = str(secret)
            config['accounts'].append({'id': 'intercom', 'service': 'intercom', 'transport': 'api', 'boundary': 'work:Acme', 'credential': 'intercom',
                                       'workspace_id': 'abc123', 'workspace_name': 'Acme Support', 'region': 'us', 'admin_id': '7'})
        store = a.Store(root / 'events.sqlite')
        box = fleet.Fleet(store.connect, config, lambda: 'token', relay=FakeRelay(self.net, me))
        box.member_id = me
        return box

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.net = FakeNetwork()
        self.own, self.adm, self.mem = self.machine('own1', intercom=True), self.machine('adm2'), self.machine('mem3')
        self.boxes = [self.own, self.adm, self.mem]
        self.everyone_sees()

    def everyone_sees(self, rounds=2):
        for _ in range(rounds):
            for box in self.boxes:  # also who left: their directory shows no Fleet now
                box.directory(refresh=True)
                box.shared_round()

    def run_all(self, rounds=3):
        for _ in range(rounds):
            for box in self.boxes:
                if box.member_id in self.net.people:
                    box.directory(refresh=True)
                    box.step(wait=0)

    def prompts(self, box, kind=None):
        return [p for p in box.shared_view()['prompts'] if kind in (None, p['kind'])]

    def ok(self, box, kind, member):
        prompt = next(p for p in self.prompts(box, kind) if p['name'] == self.net.people[member][0])
        return box.share_ok(prompt['id'], 'yes')

    def vouch_admin(self):
        """The owner's Machine confirms Ada as an admin (their owner's OK)."""
        self.everyone_sees()
        self.ok(self.own, 'admin', 'adm2')
        self.everyone_sees()

    def held(self, box):
        return next((x for x in box.config['accounts'] if x.get('service') == 'intercom' and x.get('shared') and not x['shared'].get('origin')), None)

    def token_of(self, box):
        account = self.held(box)
        return Path(box.config['credentials'][account['credential']]).read_text().strip() if account else None

    def inject(self, sender, to, question):
        """A sealed connector message straight from sender's Machine keys, as a dishonest Machine could send it."""
        ask_id = f'q{len(self.net.asks) + 1}'
        request = secrets.token_hex(16)
        env = nc.seal(question, sender.relay.private, self.net.keys[to], {'p': 'ask', 'k': 'connector', 'f': sender.member_id, 't': to, 'r': request})
        self.net.asks[ask_id] = {'id': ask_id, 'from': sender.member_id, 'to': to, 'kind': 'connector', 'request_id': request, 'sealed': env, 'status': 'queued'}
        return ask_id

    def forge(self, signer, to, token=TOKEN, grant=None, by=None, to_fp=None):
        target = next(m for m in self.net.directory(to)['fleets'][0]['members'] if m['member_id'] == to)
        grant = grant or (self.net.shared.get('intercom') or {}).get('grant') or secrets.token_hex(16)
        item = ft.grant(signer.relay.private, FLEET, 'intercom', 'abc123', token, grant, to, to_fp or ft.keys_of(target)['fp'],
                        by or signer.member_id, '2099-01-01T00:00:00+00:00')
        return {'op': 'share', 'grant': item, 'token': token, 'fleet_name': 'Acme', 'account': {'workspace_id': 'abc123'}}

    # -- the whole way
    def test_shared_with_a_member_end_to_end_and_the_network_never_sees_the_token(self):
        self.vouch_admin()
        self.own.share('intercom', FLEET, ['mem3'])
        self.run_all()
        for box in (self.adm, self.mem):  # the confirmed admin by default, the member because the owner ticked them
            account = self.held(box)
            self.assertEqual(account['shared']['fleet_name'], 'Acme')
            self.assertEqual((account['workspace_id'], account['boundary']), ('abc123', 'work:Acme'))
            path = Path(box.config['credentials'][account['credential']])
            self.assertEqual(path.read_text().strip(), TOKEN)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(TOKEN, json.dumps(box.config))
        self.assertNotIn(TOKEN, json.dumps(self.net.calls))  # every call the Network got, the sealed envelopes included
        self.assertEqual(sorted(self.net.shared['intercom']['held']), ['adm2', 'mem3'])
        with self.mem.connect() as db:  # nothing of it in the Fleet's tables either
            for table in ('fleet_in', 'fleet_out', 'fleet_log', 'fleet_meta'):
                self.assertNotIn(TOKEN, json.dumps([list(r) for r in db.execute(f'SELECT * FROM {table}')]))
        self.assertEqual(a.reconnect_help(self.held(self.mem), None), {'shared_by': 'Acme'})  # no Reconnect or Disconnect

    def join(self, member='mem4', name='Kim'):
        self.net.people[member] = (name, 'member')
        self.boxes.append(self.machine(member))
        self.everyone_sees()
        return self.boxes[-1]

    def test_a_later_joiner_waits_for_an_admins_ok(self):
        self.vouch_admin()
        self.own.share('intercom', FLEET, [])
        self.run_all()
        self.assertIsNone(self.held(self.mem))
        self.assertFalse(self.prompts(self.own, 'recipient'))  # Mo was not ticked: not asked again
        kim = self.join()
        self.run_all()
        self.assertEqual([p['name'] for p in self.prompts(self.own, 'recipient')], ['Kim'])
        self.assertFalse([q for q in self.net.asks.values() if q['to'] == 'mem4'])  # nothing sent before the OK
        self.ok(self.own, 'recipient', 'mem4')
        self.run_all()
        self.assertEqual(self.token_of(kim), TOKEN)

    def test_a_grant_from_a_second_admin_is_taken(self):
        self.vouch_admin()
        self.own.share('intercom', FLEET, [])
        self.run_all()
        kim = self.join()
        self.run_all()
        self.ok(self.adm, 'recipient', 'mem4')  # Ada says OK on her own Machine, which signs and sends it
        self.run_all()
        self.assertEqual(self.token_of(kim), TOKEN)
        first = next(q for q in self.net.asks.values() if q['to'] == 'mem4')
        self.assertEqual(first['from'], 'adm2')
        self.assertFalse(self.prompts(self.own, 'recipient'))  # the owner isn't asked any more: Ada said OK

    # -- what a receiver refuses
    def shared_flag(self, members=()):
        self.vouch_admin()
        self.own.share('intercom', FLEET, list(members))
        self.run_all()

    def refused(self, sender, question):
        self.inject(sender, 'mem3', question)
        self.mem.step(wait=0)
        self.assertIsNone(self.held(self.mem))

    def test_a_forged_grant_is_refused(self):
        self.shared_flag()
        question = self.forge(self.own, 'mem3')
        question['grant']['s']['workspace'] = 'other'  # changed after signing
        self.refused(self.own, question)
        self.refused(self.own, self.forge(stranger('own1'), 'mem3'))  # signed by a key no confirmed admin has

    def test_a_token_that_does_not_match_its_grant_is_refused(self):
        self.shared_flag()
        question = self.forge(self.own, 'mem3')
        question['token'] = NEW_TOKEN
        self.refused(self.own, question)

    def test_a_grant_for_another_recipient_is_refused(self):
        self.shared_flag()
        self.refused(self.own, self.forge(self.own, 'mem3', to_fp='0000 0000 0000 0000 0000 0000'))
        question = self.forge(self.own, 'adm2')  # Ada's grant, sealed for Mo
        self.refused(self.own, question)

    def test_a_sender_who_is_not_an_admin_is_refused(self):
        self.shared_flag()
        self.refused(self.mem, self.forge(self.mem, 'mem3'))  # a member signing and sending their own
        self.refused(self.mem, self.forge(self.own, 'mem3'))  # a member passing on the owner's grant

    def test_a_cp_only_admin_is_refused_and_shown_not_confirmed(self):
        self.net.people['mem3'] = ('Mo', 'admin')  # promoted on the control plane only
        self.net.people['mem4'] = ('Kim', 'member')
        self.boxes.append(self.machine('mem4'))
        self.everyone_sees()
        self.own.share('intercom', FLEET, [])
        self.run_all()
        self.assertIn('Mo', [p['name'] for p in self.prompts(self.own, 'admin')])
        self.assertIsNone(self.held(self.mem))  # not a confirmed admin: not a default recipient
        self.assertIn('Mo', next(x for x in self.own.shared_view()['shares'] if 'unconfirmed' in x)['unconfirmed'])
        kim = self.boxes[-1]
        self.inject(self.mem, 'mem4', self.forge(self.mem, 'mem4'))
        kim.step(wait=0)
        self.assertIsNone(self.held(kim))

    def test_a_revoked_grant_is_refused_and_a_held_one_dropped(self):
        self.shared_flag(['mem3'])
        self.assertEqual(self.token_of(self.mem), TOKEN)
        path = Path(self.mem.config['credentials'][self.held(self.mem)['credential']])
        self.own.share_stop(FLEET, 'intercom')
        self.everyone_sees()
        self.assertIsNone(self.held(self.mem))
        self.assertFalse(path.exists())
        self.refused(self.own, self.forge(self.own, 'mem3', grant=secrets.token_hex(16)))

    def test_a_stop_reaches_a_member_at_once_not_at_their_next_directory(self):
        """The inbox says the shared connectors changed: the Machine looks again right away and drops the token."""
        self.shared_flag(['mem3'])
        path = Path(self.mem.config['credentials'][self.held(self.mem)['credential']])
        self.mem.directory(refresh=True)
        self.mem.step(wait=5)  # sees the stamp as it is now
        self.assertEqual(self.token_of(self.mem), TOKEN)
        self.own.share_stop(FLEET, 'intercom')
        self.mem.last['shared'] = time.monotonic()  # no regular round due, and the directory is fresh
        self.mem.step(wait=5)
        self.assertIsNone(self.held(self.mem))
        self.assertFalse(path.exists())

    def test_a_changed_recipient_key_needs_a_new_ok(self):
        self.shared_flag(['mem3'])
        self.mem.drop_shared([self.held(self.mem)['id']])
        self.net.shared['intercom']['held'].remove('mem3')
        fresh = nc.new_keys()  # a reinstall: new keys on the Network
        Path(self.mem.config['network_keys']).write_text(json.dumps(fresh))
        self.boxes[2] = self.mem = self.machine('mem3')
        self.net.keys.pop('mem3')
        sent = len(self.net.asks)
        self.everyone_sees()
        self.own.step(wait=0)
        self.assertFalse([q for q in list(self.net.asks.values())[sent:] if q['to'] == 'mem3' and q['from'] == 'own1'])
        self.assertEqual([p['name'] for p in self.prompts(self.own, 'rekey')], ['Mo'])

    # -- leaving, replacing, stale grants
    def test_someone_leaving_asks_for_a_replace_and_the_new_token_goes_to_who_stays(self):
        self.net.people['mem4'] = ('Kim', 'member')
        self.boxes.append(self.machine('mem4'))
        kim = self.boxes[-1]
        self.vouch_admin()
        self.own.share('intercom', FLEET, ['mem3', 'mem4'])
        self.run_all()
        old = self.net.shared['intercom']['grant']
        del self.net.people['mem3']  # Mo leaves; their Machine drops it
        self.everyone_sees()
        self.assertIsNone(self.held(self.mem))
        [card] = self.prompts(self.own, 'replace')
        self.assertEqual(card['name'], 'Mo')
        self.own.connector_profile = lambda service, account, token: account
        self.own.share_replace(FLEET, 'intercom', NEW_TOKEN)
        self.run_all()
        self.assertNotEqual(self.net.shared['intercom']['grant'], old)
        self.assertEqual(self.token_of(kim), NEW_TOKEN)
        self.assertEqual(self.token_of(self.adm), NEW_TOKEN)
        self.assertFalse(self.prompts(self.own, 'replace'))

    def test_the_original_admin_leaves_and_another_admin_replaces(self):
        self.net.people['mem4'] = ('Kim', 'member')
        self.boxes.append(self.machine('mem4'))
        kim = self.boxes[-1]
        self.vouch_admin()
        self.own.share('intercom', FLEET, ['mem4'])
        self.run_all()
        stale = self.forge(self.adm, 'mem4')  # a grant for the token as it was
        self.net.people['adm2'] = ('Ada', 'owner')  # the owner's account is deleted: Ada, a confirmed admin, takes over
        del self.net.people['own1']
        self.everyone_sees()
        self.assertEqual(self.token_of(kim), TOKEN)  # the grants stay valid until replaced
        self.assertEqual([p['name'] for p in self.prompts(self.adm, 'replace')], ['Olive'])
        self.adm.connector_profile = lambda service, account, token: account
        self.adm.share_replace(FLEET, 'intercom', NEW_TOKEN)
        self.run_all()
        self.assertEqual(self.token_of(kim), NEW_TOKEN)
        self.inject(self.adm, 'mem4', stale)  # the old grant again: replaced, so refused
        kim.step(wait=0)
        self.assertEqual(self.token_of(kim), NEW_TOKEN)

    def test_a_stale_grant_is_refused_even_when_the_network_shows_it_again(self):
        self.shared_flag(['mem3'])
        held_at = self.held(self.mem)['shared']['at']
        older = self.forge(self.own, 'mem3')
        older['grant'] = ft.grant(self.own.relay.private, FLEET, 'intercom', 'abc123', TOKEN, self.net.shared['intercom']['grant'], 'mem3',
                                  older['grant']['s']['to_fp'], 'own1', '2000-01-01T00:00:00+00:00')
        ask_id = self.inject(self.own, 'mem3', older)
        self.mem.step(wait=0)
        self.assertEqual(self.net.asks[ask_id]['status'], 'declined')
        self.assertEqual(self.held(self.mem)['shared']['at'], held_at)

    def test_unsealed_is_dropped(self):
        self.shared_flag()
        self.net.asks['clear'] = {'id': 'clear', 'from': 'own1', 'to': 'mem3', 'kind': 'connector', 'request_id': None, 'sealed': None,
                                  'clear': self.forge(self.own, 'mem3'), 'status': 'queued'}
        self.mem.step(wait=0)
        self.assertIsNone(self.held(self.mem))

    def test_the_owners_named_successor_stays_confirmed_after_the_owner_leaves(self):
        self.everyone_sees()
        self.ok(self.own, 'successor', 'adm2')  # Ada is an admin on the control plane, not confirmed: the owner names her
        self.net.people['adm2'] = ('Ada', 'owner')  # the owner deleted their account
        del self.net.people['own1']
        self.everyone_sees()
        self.assertIn('adm2', self.mem.trust(self.mem.directory()['fleets'][0])[0])

    def test_a_new_owner_nobody_signed_for_is_not_confirmed(self):
        self.everyone_sees()
        self.net.people['adm2'] = ('Ada', 'owner')
        del self.net.people['own1']
        self.everyone_sees()  # Ada's Machine publishes a root of its own: Mo pinned Olive's
        trusted, unconfirmed = self.mem.trust(self.mem.directory()['fleets'][0])
        self.assertNotIn('adm2', trusted)
        self.assertIn('adm2', unconfirmed)

    def test_a_fleet_that_is_off_drops_what_was_shared(self):
        self.vouch_admin()
        self.own.share('intercom', FLEET, ['mem3'])
        self.run_all()
        account = self.held(self.mem)
        path = Path(self.mem.config['credentials'][account['credential']])
        self.assertEqual(self.mem.drop_received(), [account['id']])
        self.assertIsNone(self.held(self.mem))
        self.assertFalse(path.exists())
        self.assertTrue(any(x.get('shared', {}).get('origin') for x in self.own.config['accounts']))  # the owner's own stays

    def test_work_removal_takes_a_shared_account_and_its_token_file(self):
        import work_removal
        self.vouch_admin()
        self.own.share('intercom', FLEET, ['mem3'])
        self.run_all()
        account = self.held(self.mem)
        self.assertIn((account['id'], 'boundary'), [(x['id'], why) for x, why in work_removal.matching(self.mem.config, ['acme.example'], ['Acme'])])


def stranger(as_member):
    """A Machine-shaped signer with a key nobody pinned, posing as as_member."""
    keys = nc.new_keys()
    box = type('Stranger', (), {})()
    box.relay = type('R', (), {'private': keys})()
    box.member_id = as_member
    return box


if __name__ == '__main__':
    unittest.main()
