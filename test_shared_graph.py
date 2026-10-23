"""The Shared Graph (shared_graph.py, docs/shared-graph.md): a Work Fleet's Machines share their Work records, timelines
and client threads, sealed Machine to Machine. A fake Network stands in for the control plane, honest or not: it keeps
the sealed blobs and hands them out, and can't read, change, replay or forge them. No network, no model."""
import copy
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('graph_share_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
fleet, graph = a.fleet, a.graph
import fleet_trust as ft
import network_crypto as nc
import shared_graph as sg

FLEET = 'fl_' + '3' * 16
BODY = 'The renewal quote for 40 seats is attached, valid until Friday.'
HEALTH = 'I will be out for surgery next week, please keep this between us.'
INTERNAL = 'Can you cover my Tuesday standup?'
PERSONAL = 'Dinner at ours on Saturday?'


def ago(days, hours=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)).isoformat(timespec='seconds')


class FakeNetwork:
    """Membership, roles, public keys and trust statements, as the control plane keeps them, and the graph replica: sealed
    blobs, with a wrap per recipient, paged by the wraps' own sequence."""
    def __init__(self):
        self.people = {'own1': ('Olive', 'owner'), 'adm2': ('Ada', 'admin'), 'mem3': ('Mo', 'member')}
        self.keys, self.trust, self.calls, self.blobs, self.wraps = {}, [], [], {}, []
        self.extra_members = []

    def directory(self, me):
        if me not in self.people:
            return {'fleets': []}
        members = [{'member_id': m, 'name': n, 'label': n, 'role': r, 'account_email': m + '@acme.example', 'emails': [m + '@acme.example'],
                    'keys': self.keys.get(m), **({'me': True} if m == me else {})} for m, (n, r) in self.people.items()]
        group = {'id': FLEET, 'kind': 'work', 'name': 'Acme', 'asks': ['relationships'], 'trust': copy.deepcopy(self.trust),
                 'members': members + copy.deepcopy(self.extra_members), 'shared': []}
        return {'fleets': [group]}

    def graph_get(self, me, after):
        items = []
        for wseq, (blob_id, to, wrap, by) in enumerate(self.wraps, 1):
            if wseq > after and to == me:
                b = self.blobs[blob_id]
                items.append({'cursor': wseq, 'header': copy.deepcopy(b['header']), 'ct': b['ct'], 'sig': b['sig'], 'wrap': wrap, 'by': by})
        return {'items': items[:50], 'more': len(items) > 50}


class FakeRelay:
    def __init__(self, net, me):
        self.net, self.me = net, me

    def call(self, method, path, body=None, timeout=40):
        route = path.split('?')[0]
        self.net.calls.append((self.me, method, route, copy.deepcopy(body)))
        if self.me not in self.net.people and route not in ('/directory', '/me', '/keys'):
            raise ValueError('not in a Fleet')
        if route == '/directory':
            return self.net.directory(self.me)
        if route == '/keys':
            self.net.keys[self.me] = {k: body[k] for k in ('v', 'box', 'sign', 'sig')}
            return {'ok': True}
        if route == '/trust':
            self.net.trust.append(body['item'])
            return {'ok': True}
        if route == '/graph' and method == 'POST':
            if body['op'] == 'put':
                blob = body['blob']
                header = blob['header']
                assert header['from'] == self.me  # the control plane checks the publisher is who calls
                self.net.blobs[header['id']] = {k: blob[k] for k in ('header', 'ct', 'sig')}
                for to, wrap in blob['wraps'].items():
                    if to in self.net.people:  # only members of the Fleet
                        self.net.wraps.append((header['id'], to, wrap, self.me))
            elif body['op'] == 'wrap':
                for to, wrap in body['wraps'].items():
                    if to in self.net.people:
                        self.net.wraps.append((body['id'], to, wrap, self.me))
            return {'ok': True}
        if route == '/graph':
            after = int(path.split('after=')[1].split('&')[0])
            return self.net.graph_get(self.me, after)
        if route == '/inbox':
            return {'asks': [], 'answers': [], 'ack': True}
        return {'ok': True}


def mail(account, n, sender, to, title, body, days, thread, cc=''):
    return {'account_id': account, 'source_id': f'{account}-{n}', 'kind': 'gmail.message', 'occurred_at': ago(days), 'title': title, 'body': body,
            'deleted': False, 'metadata': {'from': sender, 'to': to, 'cc': cc, 'labels': ['INBOX'], 'message-id': f'<{account}{n}@x>', 'thread_id': thread}}


class SharedGraph(unittest.TestCase):
    def machine(self, me):
        root = Path(self.temp.name) / me
        root.mkdir(parents=True, exist_ok=True)
        email = me + '@acme.example'
        config = {'database': str(root / 'events.sqlite'), 'network_keys': str(root / 'network-keys.json'), 'credentials': {},
                  'fleet': {'url': 'https://cp.example/api/fleet'},
                  'accounts': [{'id': 'work', 'email': email, 'service': 'gmail', 'boundary': 'work:Acme'},
                               {'id': 'home', 'email': me + '@home.example', 'service': 'gmail', 'boundary': 'personal:Home'}]}
        store = a.Store(root / 'events.sqlite')
        box = fleet.Fleet(store.connect, config, lambda: 'token', relay=FakeRelay(self.net, me))
        box.member_id, box.store, box.email = me, store, email
        return box

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.net = FakeNetwork()
        self.own, self.adm, self.mem = self.machine('own1'), self.machine('adm2'), self.machine('mem3')
        self.boxes = [self.own, self.adm, self.mem]
        self.rounds()
        self.ok(self.own, 'admin', 'Ada')  # the owner's OK: Ada is an admin
        self.rounds()
        self.ok(self.adm, 'member', 'Mo')  # an admin's OK: Mo's Machine gets the team's Graph
        self.rounds()

    def rounds(self, n=2):
        for _ in range(n):
            for box in self.boxes:
                box.directory(refresh=True)
                box.shared_round()
                box.graph_round(force=True)

    def ok(self, box, kind, name):
        prompt = next(p for p in box._prompts() if p['kind'] == kind and p['name'] == name)
        return box.share_ok(prompt['id'], 'yes')

    def start(self, *boxes):
        for box in boxes:
            box.set_meta('graph_start:' + FLEET, ago(1))

    def add(self, box, *events):
        for e in events:
            box.store.commit_page(e['account_id'], {}, [e])
        with box.store.connect() as db:
            db.isolation_level = None
            graph.build(db, box.config, a.now())

    def event_id(self, box, source_id):
        with box.store.connect() as db:
            return db.execute('SELECT id FROM events WHERE source_id=?', (source_id,)).fetchone()[0]

    def judge(self, box, *calls):
        with box.store.connect() as db:
            sg.judge(db, [{'event_id': self.event_id(box, s), 'share': v, 'line': line} for s, v, line in calls])

    def team(self, box, table='team_records'):
        with box.store.connect() as db:
            sg.ensure(db)
            return [dict(r) for r in db.execute(f'SELECT * FROM {table}')]

    def olive_mail(self):
        """Olive's Machine: a client thread, a sensitive one with the same client, one with only colleagues, and her
        personal account."""
        self.add(self.own,
                 mail('work', 1, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Renewal quote', BODY, 3, 't-renewal'),
                 mail('work', 2, 'Olive <own1@acme.example>', 'lee@client.example', 'Re: Renewal quote', 'Thanks, Lee. Checking with finance.', 2, 't-renewal'),
                 mail('work', 3, 'Lee Park <lee@client.example>', 'own1@acme.example', 'A personal note', HEALTH, 2, 't-health'),
                 mail('work', 4, 'Ada <adm2@acme.example>', 'own1@acme.example', 'Standup', INTERNAL, 1, 't-standup'),
                 mail('home', 5, 'Sam Friend <sam@friends.example>', 'own1@home.example', 'Saturday', PERSONAL, 1, 't-dinner'))
        self.judge(self.own, ('work-1', 'client', 'Lee asked for the renewal quote'), ('work-3', 'sensitive', 'Lee wrote about something private'),
                   ('work-4', 'internal', 'Ada asked about the standup'))

    # -- the whole way
    def test_client_thread_reaches_teammates_and_the_network_reads_none_of_it(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        for box in (self.adm, self.mem):
            people = [json.loads(r['body']) for r in self.team(box) if r['kind'] == 'person']
            self.assertEqual([p['name'] for p in people], ['Lee Park'])
            self.assertIn('Lee asked for the renewal quote', [x['line'] for x in people[0]['timeline']])
            threads = [json.loads(r['body']) for r in self.team(box, 'team_mail')]
            full = next(t for t in threads if not t.get('private'))
            self.assertEqual(full['subject'], 'Renewal quote')
            self.assertEqual(full['messages'][0]['body'], BODY)
        sent = json.dumps(self.net.calls) + json.dumps(self.net.blobs)
        for text in (BODY, 'Lee Park', 'lee@client.example', 'client.example', 'Renewal quote', 'renewal', HEALTH):
            self.assertNotIn(text, sent)  # every call and the whole replica: sealed

    def test_personal_never_shared_not_even_that_it_exists(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        for text in ('Sam', 'friends.example', 'Saturday', PERSONAL):
            self.assertNotIn(text, received)
        with self.own.store.connect() as db:
            labels = self.own.thread_labels(db, [dict(r) for r in db.execute('SELECT * FROM events')])
            self.assertNotIn(self.event_id(self.own, 'home-5'), labels)  # no label at all on a personal account

    def test_a_personal_thread_in_a_work_account_stays_out_until_the_owner_says_team(self):
        self.add(self.own, mail('work', 6, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Your daughter', 'Congrats on the graduation!', 1, 't-p'))
        self.judge(self.own, ('work-6', 'personal', 'Lee wrote'))
        self.start(self.own)
        self.rounds()
        self.assertNotIn('graduation', json.dumps(self.team(self.mem, 'team_mail')))
        self.assertEqual(self.team(self.mem, 'team_mail'), [])

    def test_internal_thread_is_never_shared_even_by_hand(self):
        self.olive_mail()
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-4'), 'team')
        self.start(self.own)
        self.rounds()
        self.assertNotIn(INTERNAL, json.dumps(self.team(self.mem, 'team_mail')))
        self.assertNotIn('Standup', json.dumps(self.team(self.mem, 'team_mail') + self.team(self.mem)))
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        with self.assertRaisesRegex(ValueError, 'Only colleagues'):
            app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-4'), 'share': 'team'})
        with self.own.store.connect() as db:
            label = self.own.thread_labels(db, [dict(db.execute('SELECT * FROM events WHERE source_id=?', ('work-4',)).fetchone())])
        self.assertEqual(list(label.values())[0]['fixed'], True)

    def test_sensitive_client_thread_shows_only_that_it_exists(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        held = [json.loads(r['body']) for r in self.team(self.mem, 'team_mail') if json.loads(r['body']).get('private')]
        self.assertEqual(len(held), 1)
        self.assertRegex(held[0]['line'], r'^Private thread with Lee, \d{1,2} [A-Z][a-z]{2}$')
        self.assertEqual(set(held[0]), {'tag', 'private', 'at', 'line'})
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        self.assertNotIn(HEALTH, received)
        self.assertNotIn('A personal note', received)
        self.assertNotIn('something private', received)  # the review's own line stays home too

    def test_override_by_hand_wins_both_ways(self):
        self.olive_mail()
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-3'), 'team')     # the review said sensitive; the owner shares it
            sg.set_hand(db, self.event_id(self.own, 'work-1'), 'private')  # the review said client; the owner keeps it
        self.start(self.own)
        self.rounds()
        threads = json.dumps(self.team(self.mem, 'team_mail'))
        self.assertIn(HEALTH, threads)
        self.assertNotIn(BODY, threads)
        # and a later review doesn't undo it
        self.judge(self.own, ('work-3', 'sensitive', 'Lee wrote'), ('work-1', 'client', 'Lee asked'))
        self.rounds()
        threads = json.dumps(self.team(self.mem, 'team_mail'))
        self.assertIn(HEALTH, threads)
        self.assertNotIn(BODY, threads)

    def test_a_later_review_never_turns_sensitive_into_client(self):
        self.olive_mail()
        self.judge(self.own, ('work-3', 'client', 'Lee wrote'))
        self.start(self.own)
        self.rounds()
        self.assertNotIn(HEALTH, json.dumps(self.team(self.mem, 'team_mail')))

    def test_keep_private_holds(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        self.assertTrue(self.team(self.mem))
        with self.own.store.connect() as db:
            sg.keep_private(db, 'org:client.example')
        self.rounds()
        self.assertEqual(self.team(self.mem), [])
        self.assertEqual(self.team(self.mem, 'team_mail'), [])

    def test_nothing_before_the_start_day(self):
        self.olive_mail()
        self.rounds()
        self.assertEqual(self.team(self.mem), [])
        self.assertGreater(self.own.meta('graph_start:' + FLEET), a.now())  # a day after the owner was told
        self.own.graph_share_now(FLEET)
        self.rounds()
        self.assertTrue(self.team(self.mem))

    def test_member_who_left_gets_no_new_updates(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        self.assertTrue(self.team(self.mem))
        before = len(self.net.wraps)
        del self.net.people['mem3']
        self.rounds()
        self.add(self.own, mail('work', 7, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Signed', 'Signed order form attached.', 0, 't-renewal'))
        self.judge(self.own, ('work-7', 'client', 'Lee signed the order form'))
        self.rounds()
        self.assertFalse([w for w in self.net.wraps[before:] if w[1] == 'mem3'])
        later = [c[3]['blob'] for c in self.net.calls if c[2] == '/graph' and c[3] and c[3].get('op') == 'put'
                 and c[3]['blob']['header']['id'] not in {w[0] for w in self.net.wraps[:before]}]
        self.assertTrue(later)
        self.assertFalse([b for b in later if 'mem3' in b['wraps'] or 'mem3' in b['header']['to']])  # nothing sealed for them at all
        self.assertEqual(self.team(self.mem), [])  # and their Machine dropped what it had of this Fleet
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        kv_now, _ = self.own.graph_key(FLEET)
        self.assertNotIn('mem3', self.own.graph_keys(FLEET)[kv_now]['to'])  # a new graph key, without them

    def test_what_someone_who_left_shared_stays_with_the_fleet(self):
        self.add(self.mem, mail('work', 8, 'Kim Wu <kim@globex.example>', 'mem3@acme.example', 'Pilot', 'Pilot starts Monday.', 2, 't-pilot'))
        self.judge(self.mem, ('work-8', 'client', 'Kim confirmed the pilot'))
        self.start(self.mem)
        self.rounds()
        self.assertIn('Kim Wu', json.dumps(self.team(self.own)))
        del self.net.people['mem3']
        self.rounds()
        self.assertIn('Kim Wu', json.dumps(self.team(self.own)))
        with self.own.store.connect() as db:
            self.assertTrue(db.execute('SELECT left_at FROM team_sources WHERE member=?', ('mem3',)).fetchone()['left_at'])
            self.assertIn('(left)', self.own.team_overview(db, {})['people'][0]['via'])

    # -- a dishonest control plane
    def test_a_changed_blob_or_a_forged_one_is_refused(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        self.assertTrue(self.team(self.mem))
        blob_id, _, wrap, by = next(w for w in self.net.wraps if w[1] == 'mem3' and self.net.blobs[w[0]]['header']['kind'] == 'snapshot')
        b = self.net.blobs[blob_id]
        item = {'header': copy.deepcopy(b['header']), 'ct': b['ct'], 'sig': b['sig'], 'wrap': wrap, 'by': by}
        data = bytearray(nc.unb64(b['ct'])); data[60] ^= 1
        changes = [
            ('changed text', {**item, 'ct': nc.b64(bytes(data))}),
            ('another sender', {**item, 'header': {**item['header'], 'from': 'adm2'}}),
            ('another seq', {**item, 'header': {**item['header'], 'seq': item['header']['seq'] + 10**9}}),
            ('a replay', item),  # the same snapshot again: older than or equal to the one taken
            ('re-sealed by a member', {**item, 'by': 'mem3'}),
        ]
        for why, forged in changes:
            with self.subTest(why), self.assertRaises((nc.SealError, ValueError)):
                self.mem.graph_take(forged)

    def test_the_network_cant_add_a_member_or_an_admin(self):
        """The control plane lists a Machine of its own with its own keys and signs a member statement for it: no
        teammate's Machine seals anything for it, and nothing it publishes is taken."""
        self.olive_mail()
        self.start(self.own)
        evil = nc.new_keys()
        public = nc.public_keys(evil)
        self.net.extra_members = [{'member_id': 'cp9', 'name': 'Support', 'label': 'Support', 'role': 'admin', 'account_email': 'x@acme.example',
                                   'emails': [], 'keys': public}]
        self.net.trust.append(ft.sign(evil, ft.statement('member', FLEET, 'cp9', public['sign'], 'cp9')))
        self.net.trust.append(ft.sign(evil, ft.statement('admin', FLEET, 'cp9', public['sign'], 'own1')))
        self.rounds()
        self.assertFalse([w for w in self.net.wraps if w[1] == 'cp9'])
        for c in self.net.calls:
            if c[2] == '/graph' and c[3] and c[3].get('op') == 'put':
                self.assertNotIn('cp9', c[3]['blob']['wraps'])
        header = {'v': 1, 'fleet': FLEET, 'from': 'cp9', 'id': 'ab' * 16, 'seq': 10**13, 'kind': 'snapshot', 'at': a.now(), 'to': ['mem3'], 'kv': 'x'}
        blob, _ = sg.make(evil, header, {'records': [{'r': 'cd' * 16, 't': 'person', 'name': 'Planted'}]},
                          {'mem3': self.net.keys['mem3']})
        with self.assertRaisesRegex(ValueError, 'confirmed member'):
            self.mem.graph_take({**blob, 'wrap': blob['wraps']['mem3'], 'by': 'cp9'})
        self.assertNotIn('Planted', json.dumps(self.team(self.mem)))

    def test_a_blob_from_a_teammate_not_confirmed_yet_waits_and_is_taken_once_they_are(self):
        self.net.people['mem4'] = ('Kim', 'member')
        kim = self.machine('mem4'); self.boxes.append(kim)
        self.rounds()
        header = {'v': 1, 'fleet': FLEET, 'from': 'mem4', 'id': 'ef' * 16, 'seq': 10**12, 'kind': 'snapshot', 'at': a.now(), 'to': ['own1'], 'kv': None}
        blob, _ = sg.make(kim.relay.private, header, {'records': [{'r': 'cd' * 16, 't': 'person', 'name': 'Noa Quill', 'score': 1}], 'threads': []},
                          {'own1': self.net.keys['own1']})
        self.net.blobs[header['id']] = {k: blob[k] for k in ('header', 'ct', 'sig')}
        self.net.wraps.append((header['id'], 'own1', blob['wraps']['own1'], 'mem4'))
        self.own.graph_fetch()
        self.assertNotIn('Noa Quill', json.dumps(self.team(self.own)))
        self.ok(self.own, 'member', 'Kim')
        self.rounds()
        self.assertIn('Noa Quill', json.dumps(self.team(self.own)))

    # -- conflicts
    def test_conflict_hand_wins_else_the_closest(self):
        entry = {'kind': 'person', 'sources': [
            {'fleet': FLEET, 'member': 'adm2', 'name': 'Ada', 'left': False, 'record': {'name': 'Lee Park', 'role': 'Buyer', 'score': 4.0}},
            {'fleet': FLEET, 'member': 'mem3', 'name': 'Mo', 'left': False, 'record': {'name': 'Lee P.', 'role': 'CFO', 'score': 1.0}}]}
        out = sg.resolve(entry)
        self.assertEqual((out['name'], out['role'], out['owner']), ('Lee Park', 'Buyer', 'adm2'))
        entry['sources'][1]['record']['role_hand'] = True
        self.assertEqual(sg.resolve(entry)['role'], 'CFO')
        self.assertEqual(sg.via(entry['sources']), 'via Ada and Mo')
        stage = sg.pick_stage([{'value': 'Proposal', 'at': '2026-10-20', 'hand': False}, {'value': 'Signed', 'at': '2026-10-18', 'hand': False},
                               {'value': 'Lost', 'at': '2026-10-01', 'hand': False}])
        self.assertEqual(stage['value'], 'Proposal')  # the newest move a message showed
        stage = sg.pick_stage([{'value': 'Proposal', 'at': '2026-10-20', 'hand': False}, {'value': 'Signed', 'at': '2026-10-18', 'hand': True}])
        self.assertEqual(stage['value'], 'Signed')  # a move by hand stands

    def test_the_closer_teammate_owns_a_shared_person(self):
        self.olive_mail()
        self.add(self.adm, *[mail('work', 10 + i, 'Lee Park <lee@client.example>', 'adm2@acme.example', f'Call {i}', 'Notes from the call.', i, f't-call{i}')
                             for i in range(1, 9)],
                 *[mail('work', 30 + i, 'Ada <adm2@acme.example>', 'lee@client.example', f'Re: Call {i}', 'Thanks!', i, f't-call{i}') for i in range(1, 9)])
        self.judge(self.adm, *[(f'work-{10 + i}', 'client', 'Lee and Ada spoke') for i in range(1, 9)])
        self.start(self.own, self.adm)
        self.rounds(3)
        with self.mem.store.connect() as db:
            people = self.mem.team_overview(db, {})['people']
            entry = next(e for e in self.mem.team_graph(db) if e['kind'] == 'person')
        lee = next(p for p in people if p['name'] == 'Lee Park')
        self.assertEqual(lee['via'], 'via Ada and Olive')  # one person, both teammates, the closest first
        self.assertEqual(sg.resolve(entry)['owner'], 'adm2')

    # -- the review's call
    def test_finish_analysis_takes_team_calls_only_for_its_batch(self):
        store = self.own.store
        self.add(self.own, mail('work', 40, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Hello', 'Hi', 1, 't-h'))
        claim = store.begin_analysis(['work'], 10, 'agent:main:assistant-review-x', 'run1')
        event = self.event_id(self.own, 'work-40')
        with self.assertRaisesRegex(ValueError, 'belong to this batch'):
            store.finish_analysis(claim['analysis_id'], 'ok', 'agent:main:assistant-review-x', 'run1', team=[{'event_id': 999999, 'share': 'client'}])
        with self.assertRaisesRegex(ValueError, 'share'):
            store.finish_analysis(claim['analysis_id'], 'ok', 'agent:main:assistant-review-x', 'run1', team=[{'event_id': event, 'share': 'public'}])
        store.finish_analysis(claim['analysis_id'], 'ok', 'agent:main:assistant-review-x', 'run1', team=[{'event_id': event, 'share': 'client', 'line': 'Lee said hello'}])
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT verdict FROM share_threads').fetchone()['verdict'], 'client')

    def test_a_review_cant_set_team_or_only_you_by_hand(self):
        self.olive_mail()
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        with self.assertRaisesRegex(ValueError, 'finish_analysis team'):
            app.graph_action({'event_id': self.event_id(self.own, 'work-3'), 'share': 'team', '_review_session': 'agent:main:assistant-review-x'})
        with self.assertRaisesRegex(ValueError, 'finish_analysis team'):
            app.graph_action({'node_id': 'org:client.example', 'keep_private': False, '_review_session': 'agent:main:assistant-review-x'})
        app.graph_action({'event_id': self.event_id(self.own, 'work-3'), 'share': 'team'})  # the owner in chat
        with self.own.store.connect() as db:
            self.assertEqual(db.execute("SELECT hand FROM share_threads WHERE thread='gm:t-health'").fetchone()['hand'], 'team')

    def test_weekly_report_line(self):
        self.olive_mail()
        self.start(self.own)
        self.rounds()
        lines = a.recap_share_lines(self.own.store, {'dashboard_host': 'box.example', 'dashboard_path': '/'})
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r'^• Shared with Acme: 1 thread, 1 person, \d+ projects?\. 1 held back\. \[Review\]\(https://box\.example/#graph=held\)$')


if __name__ == '__main__':
    unittest.main()
