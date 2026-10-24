"""The Shared Graph (shared_graph.py, docs/shared-graph.md): a Work Fleet's Machines share their Work records, timelines
and client threads, sealed Machine to Machine. A fake Network stands in for the control plane, honest or not: it keeps
the sealed blobs until every Machine they were sealed for took them, and can't read, change, replay or forge them. No network, no model."""
import copy
import datetime as dt
import importlib.util
import json
import time
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch
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
DEAL = 'Our offer values the company at twelve million.'


def ago(days, hours=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)).isoformat(timespec='seconds')


class FakeNetwork:
    """Membership, roles, public keys and trust statements, as the control plane keeps them, and the Network's mailbox:
    sealed blobs, with a wrap per recipient, paged by the wraps' own sequence, each gone once its Machine took it."""
    def __init__(self):
        self.people = {'own1': ('Olive', 'owner'), 'adm2': ('Ada', 'admin'), 'mem3': ('Mo', 'member')}
        self.keys, self.trust, self.calls, self.blobs, self.wraps = {}, [], [], {}, []
        self.extra_members, self.gone = [], set()

    def held(self):
        """The blobs the mailbox still holds: those with a wrap nobody took yet."""
        return {w[0] for n, w in enumerate(self.wraps, 1) if n not in self.gone}

    def expire(self, member):
        """30 days on: what waited for this Machine is gone from the mailbox."""
        self.gone |= {n for n, w in enumerate(self.wraps, 1) if w[1] == member}

    def directory(self, me):
        if me not in self.people:
            return {'fleets': []}
        members = [{'member_id': m, 'name': n, 'label': n, 'role': r, 'account_email': m + '@acme.example', 'emails': [m + '@acme.example'],
                    'keys': self.keys.get(m), **({'me': True} if m == me else {})} for m, (n, r) in self.people.items()]
        group = {'id': FLEET, 'kind': 'work', 'name': 'Acme', 'asks': ['relationships'], 'trust': copy.deepcopy(self.trust),
                 'members': members + copy.deepcopy(self.extra_members), 'shared': [],
                 **({'security': {'level': self.level}} if getattr(self, 'level', None) else {})}
        return {'fleets': [group]}

    def graph_get(self, me, after):
        items = []
        for wseq, (blob_id, to, wrap, by) in enumerate(self.wraps, 1):
            if wseq > after and to == me and wseq not in self.gone:
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
                for n, w in enumerate(self.net.wraps, 1):  # as the control plane: a new snapshot or tag replaces what wasn't taken
                    old = self.net.blobs[w[0]]['header']
                    if n in self.net.gone or old['from'] != self.me:
                        continue
                    if header['kind'] == 'snapshot' and old['kind'] in ('snapshot', 'delta') and (
                            not header.get('only') or (old.get('only') and old.get('tag') == header.get('tag'))):
                        self.net.gone.add(n)  # the team's snapshot ends every chain; a chain's own only its own
                    elif header['kind'] in ('mail', 'file', 'ask', 'want') and old['kind'] == header['kind'] and old.get('tag') == header.get('tag'):
                        self.net.gone.add(n)
                self.net.blobs[header['id']] = {k: blob[k] for k in ('header', 'ct', 'sig')}
                for to, wrap in blob['wraps'].items():
                    if to in self.net.people:  # only members of the Fleet
                        self.net.wraps.append((header['id'], to, wrap, self.me))
            elif body['op'] == 'relay':  # passed on as its publisher signed it, a key sealed again for who missed it
                blob = body['blob']
                header = blob['header']
                assert header['kind'] in ('snapshot', 'delta', 'mail', 'file')
                if header.get('only') and set(body['wraps']) - set(header['to']):
                    raise ValueError('shared by name: only to the members it was sealed for')  # the control plane refuses it
                self.net.blobs.setdefault(header['id'], {k: blob[k] for k in ('header', 'ct', 'sig')})
                waiting = {w[1] for n, w in enumerate(self.net.wraps, 1) if w[0] == header['id'] and n not in self.net.gone}
                for to, wrap in body['wraps'].items():
                    if to in self.net.people and to != self.me and to not in waiting:
                        self.net.wraps.append((header['id'], to, wrap, self.me))
            elif body['op'] == 'ack':
                ids = set(body['ids'])
                self.net.gone |= {n for n, w in enumerate(self.net.wraps, 1) if w[1] == self.me and w[0] in ids}
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
    def machine(self, me, device=''):
        root = Path(self.temp.name) / (me + device)
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
        hold = sg.HOLD; sg.HOLD = 0; self.addCleanup(setattr, sg, 'HOLD', hold)  # its own test below
        if not self._testMethodName.startswith('test_sharing_is_off_until'):
            self.own.graph_pause(FLEET, False)  # an admin turned the team's Graph on (off by default: experimental)
        for box in self.boxes:
            if not self._testMethodName.startswith('test_the_first_share'):
                box.set_meta('graph_start:' + FLEET, {'shown': a.now(), 'started': a.now()})  # the owners looked it over
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
            self.assertNotIn(text, sent)  # every call and all the Network held: sealed

    def test_personal_never_shared_not_even_that_it_exists(self):
        self.olive_mail()
        self.rounds()
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        for text in ('Sam', 'friends.example', 'Saturday', PERSONAL):
            self.assertNotIn(text, received)
        with self.own.store.connect() as db:
            labels = self.own.thread_labels(db, [dict(r) for r in db.execute('SELECT * FROM events')])
            self.assertNotIn(self.event_id(self.own, 'home-5'), labels)  # no label at all on a personal account

    def test_a_personal_thread_in_a_work_account_never_goes_not_by_hand_nor_a_later_review(self):
        self.add(self.own, mail('work', 6, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Your daughter', 'Congrats on the graduation!', 1, 't-p'))
        self.judge(self.own, ('work-6', 'personal', 'Lee wrote'))
        self.rounds()
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        with self.own.store.connect() as db:
            with self.assertRaisesRegex(ValueError, 'Personal'):
                sg.set_hand(db, self.event_id(self.own, 'work-6'), 'team')
            label = self.own.thread_labels(db, [dict(db.execute("SELECT * FROM events WHERE source_id='work-6'").fetchone())])
        self.assertEqual((label[self.event_id(self.own, 'work-6')]['fixed'], label[self.event_id(self.own, 'work-6')]['view']), (True, 'personal'))
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        with self.assertRaisesRegex(ValueError, 'Personal threads are never shared'):
            app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-6'), 'share': 'team'})
        self.judge(self.own, ('work-6', 'client', 'Lee asked for a quote'), ('work-6', 'other', 'A note'), ('work-6', 'client', 'Lee wrote'))
        self.rounds()
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        for text in ('graduation', 'Your daughter', 'Lee asked for a quote', 'Lee wrote'):
            self.assertNotIn(text, received)  # not the thread, not a line about it

    def test_a_thread_later_sorted_personal_is_taken_back_and_stays_personal(self):
        self.add(self.own, mail('work', 7, 'Lee Park <lee@client.example>', 'own1@acme.example', 'New phone', 'Your new phone ships Friday.', 1, 't-phone'))
        self.judge(self.own, ('work-7', 'client', 'Lee wrote about a phone'))
        self.rounds()
        self.assertIn('New phone', json.dumps(self.team(self.mem, 'team_mail')))
        self.judge(self.own, ('work-7', 'personal', 'Lee wrote'))
        self.rounds()
        self.assertNotIn('New phone', json.dumps(self.team(self.mem, 'team_mail')))  # the owner's Machine took it back
        for later in ('sensitive', 'client', 'internal'):  # a later review never turns it back, not even into a line
            self.judge(self.own, ('work-7', later, 'Lee wrote'))
            self.rounds()
            self.assertEqual(self.team(self.mem, 'team_mail'), [])
        with self.own.store.connect() as db:
            self.assertEqual(db.execute('SELECT verdict FROM share_threads').fetchone()[0], 'personal')

    def test_family_friends_and_people_marked_personal_never_go(self):
        family = {'id': 'fl_' + '4' * 16, 'kind': 'family', 'name': 'Home', 'members': [
            {'member_id': 'fm_partner', 'name': 'Hana Lane', 'emails': ['hana@studio.example']}]}
        fleets = self.own.fleets
        self.own.fleets = lambda: {**fleets(), family['id']: family}
        self.add(self.own,
                 mail('work', 8, 'Hana Lane <hana@studio.example>', 'own1@acme.example', 'Weekend plans', 'The kids want the beach.', 1, 't-hana'),
                 mail('work', 9, 'Kim Moss <kim@pal.example>', 'own1@acme.example', 'Climbing trip', 'Trip is booked.', 1, 't-kim'),
                 mail('work', 10, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Renewal quote', BODY, 1, 't-renewal', cc='hana@studio.example'))
        with self.own.store.connect() as db:
            graph.person_edit(db, 'email:kim@pal.example', {'side': 'personal'}, a.now())
            db.commit()
            db.isolation_level = None
            graph.build(db, self.own.config, a.now())
        self.judge(self.own, ('work-8', 'client', 'Hana wrote'), ('work-9', 'client', 'Kim wrote'), ('work-10', 'client', 'Lee asked for a quote'))
        self.rounds()
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        for text in ('Hana', 'studio.example', 'beach', 'Kim', 'pal.example', 'Climbing', 'Renewal quote'):
            self.assertNotIn(text, received)  # a thread with a family member on it stays with the owner too
        with self.own.store.connect() as db:
            labels = self.own.thread_labels(db, [dict(r) for r in db.execute('SELECT * FROM events')])
        self.assertEqual({labels[self.event_id(self.own, s)]['share'] for s in ('work-8', 'work-9', 'work-10')}, {'only_you'})

    def test_internal_thread_is_never_shared_even_by_hand(self):
        self.olive_mail()
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-4'), 'team')
        self.rounds()
        self.assertNotIn(INTERNAL, json.dumps(self.team(self.mem, 'team_mail')))
        self.assertNotIn('Standup', json.dumps(self.team(self.mem, 'team_mail') + self.team(self.mem)))
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        with self.assertRaisesRegex(ValueError, 'Only colleagues'):
            app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-4'), 'share': 'team'})
        with self.own.store.connect() as db:
            label = self.own.thread_labels(db, [dict(db.execute('SELECT * FROM events WHERE source_id=?', ('work-4',)).fetchone())])
        self.assertEqual(list(label.values())[0]['fixed'], True)

    def test_a_sensitive_thread_goes_nowhere_not_even_that_it_exists(self):
        self.olive_mail()
        self.rounds()
        held = [json.loads(r['body']) for r in self.team(self.mem, 'team_mail') if json.loads(r['body']).get('private')]
        self.assertEqual(held, [])  # not even that it exists
        self.assertNotIn('Private thread', json.dumps(self.team(self.mem)))
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        self.assertNotIn(HEALTH, received)
        self.assertNotIn('A personal note', received)
        self.assertNotIn('something private', received)  # the review's own line stays home too

    def test_override_by_hand_wins_both_ways(self):
        self.olive_mail()
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-3'), 'team')     # the review said sensitive; the owner shares it
            sg.set_hand(db, self.event_id(self.own, 'work-1'), 'private')  # the review said client; the owner keeps it
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
        self.rounds()
        self.assertNotIn(HEALTH, json.dumps(self.team(self.mem, 'team_mail')))

    def test_keep_private_holds(self):
        self.olive_mail()
        self.rounds()
        self.assertTrue(self.team(self.mem))
        with self.own.store.connect() as db:
            sg.keep_private(db, 'org:client.example')
        self.rounds()
        self.assertEqual(self.team(self.mem), [])
        self.assertEqual(self.team(self.mem, 'team_mail'), [])

    def board(self, box, name, visibility, projects, stage='Talks'):
        with box.store.connect() as db:
            for project, sources in projects.items():
                graph.label(db, [self.event_id(box, x) for x in sources], project, 'review', a.now())
            graph.save_board(db, {'name': name, 'stages': [stage, 'Signed'], 'about': name, 'visibility': visibility,
                                  'projects': list(projects)}, a.now())
            board = db.execute('SELECT id FROM boards WHERE name=?', (name,)).fetchone()[0]
        with box.store.connect() as db:
            db.isolation_level = None
            graph.build(db, box.config, a.now())
        return board

    def projects_of(self, box):
        return {json.loads(r['body'])['name']: json.loads(r['body']) for r in self.team(box) if r['kind'] == 'project'}

    def test_team_project_summaries_do_not_repeat_withheld_signal_text(self):
        self.olive_mail()
        self.board(self.own, 'Renewals', 'team', {'Renewal: Lee': ['work-1', 'work-3', 'work-4']})
        for index, source in enumerate(('work-3', 'work-4')):
            with self.subTest(source=source):
                with self.own.store.connect() as db:
                    eid = self.event_id(self.own, source)
                    db.execute('UPDATE events SET occurred_at=? WHERE id=?', (ago(0, 2-index), eid))
                    graph.set_stage(db, 'project:1', 'Signed' if index == 0 else 'Talks', 'review', a.now(),
                                    reason='Withheld project reason', event_id=eid, next_step='Withheld project next step')
                self.rounds()
                record = self.projects_of(self.mem)['Renewal: Lee']
                self.assertIsNone(record['why'])
                self.assertIsNone(record['next'])
                self.assertNotIn('Withheld project', json.dumps(self.team(self.mem)))

    def test_same_stage_next_step_uses_its_own_sharing_provenance(self):
        self.olive_mail()
        self.board(self.own, 'Renewals', 'team', {'Renewal: Lee': ['work-1', 'work-3']})
        with self.own.store.connect() as db:
            graph.set_stage(db, 'project:1', 'Signed', 'review', a.now(), reason='Client signed',
                            event_id=self.event_id(self.own, 'work-1'), next_step='Send the client a welcome')
        self.rounds()
        self.assertEqual(self.projects_of(self.mem)['Renewal: Lee']['next'], 'Send the client a welcome')
        with self.own.store.connect() as db:
            graph.set_stage(db, 'project:1', 'Signed', 'review', a.now(), reason='Private health update',
                            event_id=self.event_id(self.own, 'work-3'), next_step='Private follow-up for surgery')
        self.rounds()
        record = self.projects_of(self.mem)['Renewal: Lee']
        self.assertEqual(record['why'], 'Client signed')
        self.assertIsNone(record['next'])
        self.assertNotIn('Private follow-up', json.dumps(self.team(self.mem)))

    def test_explicit_board_sharing_still_includes_its_authorized_sensitive_summary(self):
        self.olive_mail()
        board = self.board(self.own, 'Renewals', 'only_you', {'Renewal: Lee': ['work-1', 'work-3']})
        with self.own.store.connect() as db:
            graph.set_stage(db, 'project:1', 'Signed', 'review', a.now(), reason='Explicit sensitive reason',
                            event_id=self.event_id(self.own, 'work-3'), next_step='Explicit sensitive next step')
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.rounds()
        record = self.projects_of(self.mem)['Renewal: Lee']
        self.assertEqual(record['why'], 'Explicit sensitive reason')
        self.assertEqual(record['next'], 'Explicit sensitive next step')
        self.assertNotIn('Renewal: Lee', self.projects_of(self.adm))

    def test_an_only_you_board_its_projects_and_their_threads_never_reach_the_team_and_switching_retracts_them(self):
        self.olive_mail()
        board = self.board(self.own, 'Renewals', 'team', {'Renewal: Lee': ['work-1', 'work-2']})
        self.rounds()
        self.assertEqual(self.projects_of(self.mem)['Renewal: Lee']['board'], 'Renewals')
        self.assertIn('Renewal quote', [json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')])
        with self.own.store.connect() as db:
            self.assertTrue(graph.visible_to_team(db, 'project:1'))
            graph.save_board(db, {'board_id': board, 'name': 'Renewals', 'stages': ['Talks', 'Signed'], 'about': 'Renewals', 'visibility': 'only_you'}, a.now())
            self.assertFalse(graph.visible_to_team(db, 'project:1'))
            self.assertEqual(graph.hidden_project_ids(db), {1})
        self.rounds()
        self.assertEqual(self.projects_of(self.mem), {})
        self.assertNotIn('Renewal quote', [json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')])
        self.assertNotIn('Lee Park', [json.loads(r['body'])['name'] for r in self.team(self.mem) if r['kind'] == 'person'])
        # The project's own setting wins over its board's, both ways
        with self.own.store.connect() as db:
            graph.set_project_visibility(db, 'project:1', 'team')
            self.assertEqual(graph.hidden_project_ids(db), set())
            graph.set_project_visibility(db, 'project:1', None)
            self.assertEqual(graph.hidden_project_ids(db), {1})
            with self.assertRaises(ValueError):
                graph.set_project_visibility(db, 'project:1', 'everyone')
        with self.own.store.connect() as db:
            view = self.own.team_overview(db, {})
            self.assertEqual([b['name'] for b in view['boards']], [])

    def test_a_project_on_a_team_board_goes_with_its_board_record_before_any_thread_of_it_is_shared(self):
        self.olive_mail()
        self.board(self.own, 'Hiring', 'team', {'Hire: Ada': ['work-4']}, stage='Screening')  # only colleagues on it
        self.rounds()
        project = self.projects_of(self.mem)['Hire: Ada']
        self.assertEqual((project['board'], project['stage'], project['timeline'], project['threads']), ('Hiring', 'Screening', [], []))
        self.assertNotIn('Standup', [json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')])

    def test_the_first_share_waits_for_start_now_and_reminds_within_work_hours(self):
        told = []
        self.own.tell = lambda text, anchor: told.append((text, anchor))
        for box in (self.adm, self.mem):
            box.set_meta('graph_start:' + FLEET, {'shown': a.now(), 'started': a.now()})
        self.olive_mail()
        self.own.hours = {'work_day': lambda _: True, 'in_hours': lambda _: False}  # outside work hours: no reminder yet
        self.rounds()
        self.assertEqual(self.team(self.mem, 'team_mail'), [])  # nothing goes, however long it waits
        self.assertEqual(told, [])
        self.own.hours = {'work_day': lambda _: True, 'in_hours': lambda _: True}
        self.rounds()
        self.assertEqual(told, [('Your Work is ready to share with Acme. Look it over and start when you are ready.', 'shared-' + FLEET)])
        self.rounds()
        self.assertEqual(len(told), 1)  # gently: again only after a few days
        start = self.own.meta('graph_start:' + FLEET)
        start['reminded'] = ago(4)
        self.own.set_meta('graph_start:' + FLEET, start)
        self.rounds()
        self.assertEqual(len(told), 2)
        me = self.own.sharing()[0]
        self.assertFalse(me['started'])
        people = {p['name']: p['state'] for p in self.adm.sharing()[0]['people']}  # an admin sees who hasn't started
        self.assertEqual(people['Olive'], 'not_started')
        with self.own.store.connect() as db:
            sg.ensure(db)
        view = self.own.graph_overview(FLEET)
        self.assertEqual(view['preview']['threads'], 1)
        self.assertEqual(view['preview']['orgs'][0]['key'], '@client.example')
        self.assertTrue(all(x['why'].startswith('Goes when you start') for x in view['threads']))
        self.own.graph_start(FLEET)
        self.rounds()
        self.assertIn(BODY, json.dumps(self.team(self.mem, 'team_mail')))
        self.assertEqual({p['name']: p['state'] for p in self.adm.sharing()[0]['people']}['Olive'], 'sharing')
        self.rounds()
        self.assertEqual(len(told), 2)  # started: no more reminders

    # -- all the Work history, a batch at a time
    def client_threads(self, box, n, days):
        self.add(box, *[mail('work', 100 + i, 'Lee Park <lee@client.example>', box.email, f'Order {i}', f'Order number {i}', days + i, f't-order-{i}')
                        for i in range(n)])
        self.judge(box, *[(f'work-{100 + i}', 'client', f'Lee placed order {i}') for i in range(n)])

    def mail_puts(self, box, kind='mail'):
        return [c[3]['blob']['header'] for c in self.net.calls
                if c[0] == box.member_id and c[2] == '/graph' and c[3] and c[3].get('op') == 'put' and c[3]['blob']['header']['kind'] == kind]

    def test_history_goes_as_far_back_as_the_machine_has_it(self):
        self.client_threads(self.own, 2, 500)  # well past a year
        self.rounds()
        subjects = {json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')}
        self.assertEqual(subjects, {'Order 0', 'Order 1'})

    def test_the_backfill_is_batched_newest_first_and_resumes_where_it_stopped(self):
        self.client_threads(self.own, 6, 10)
        hour = sg.PER_HOUR
        sg.PER_HOUR = 4; self.addCleanup(setattr, sg, 'PER_HOUR', hour)
        self.own.set_meta('graph_puts', [])
        self.own.graph_round(force=True)  # the snapshot and three threads
        for box in (self.adm, self.mem):
            box.graph_round(force=True)
        first = sorted(json.loads(r['body'])['subject'] for r in self.team(self.mem, 'team_mail'))
        self.assertEqual(first, ['Order 0', 'Order 1', 'Order 2'])  # the newest
        self.own.graph_round(force=True)
        self.assertEqual(len(self.mail_puts(self.own)), 3)  # the budget is spent: nothing more this hour
        self.own.set_meta('graph_puts', [])  # an hour later
        self.rounds()
        self.assertEqual(len({json.loads(r['body'])['subject'] for r in self.team(self.mem, 'team_mail')}), 6)
        tags = [h['tag'] for h in self.mail_puts(self.own)]
        self.assertEqual(len(tags), len(set(tags)))  # each thread went once

    def test_the_history_pass_judges_old_threads_through_the_same_labels(self):
        self.add(self.own, mail('work', 7, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Old order', BODY, 300, 't-old'),
                 mail('work', 8, 'Ada <adm2@acme.example>', 'own1@acme.example', 'Old standup', INTERNAL, 300, 't-old-standup'))
        waiting = self.own.graph_unjudged()
        self.assertEqual([t['subject'] for t in waiting], ['Old order'])  # colleagues only: never asked about, never shared
        run = 'r' * 32
        with self.own.store.connect() as db:
            db.execute("INSERT INTO profile_runs(run_id,status,started_at,updated_at,evidence,kind,auto) VALUES(?,'running',?,?,?,'share',1)",
                       (run, a.now(), a.now(), json.dumps({'threads': waiting, 'page_size': 20})))
        self.assertEqual(self.own.store.profile_evidence(1, a.PROFILE_SESSION_PREFIX + run)['threads'][0]['subject'], 'Old order')
        with self.assertRaises(ValueError):  # only threads this pass was given
            self.own.store.save_profile({'team': [{'event_id': self.event_id(self.own, 'work-8'), 'share': 'client', 'line': 'x'}]}, a.PROFILE_SESSION_PREFIX + run)
        self.own.store.save_profile({'team': [{'event_id': waiting[0]['event_id'], 'share': 'client', 'line': 'Lee placed an old order'}]},
                                    a.PROFILE_SESSION_PREFIX + run)
        self.assertEqual(self.own.graph_unjudged(), [])
        self.rounds()
        self.assertIn('Old order', {json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')})

    # -- attachments go with a whole client thread
    def files(self, box, big=False):
        asked = []
        def fetch(event_id, part=None):
            asked.append((event_id, part))
            if part is None:
                return [{'id': 'p1', 'name': 'quote.pdf', 'mime': 'application/pdf', 'size': 9, 'kind': 'file'},
                        {'id': 'p2', 'name': 'logo.png', 'mime': 'image/png', 'size': 10, 'kind': 'file', 'inline': True},
                        {'id': 'p3', 'name': 'all-hands.mp4', 'mime': 'video/mp4', 'size': sg.MAX_FILE + 1, 'kind': 'file'}]
            return b'%PDF-1.4 ' if part == 'p1' else b'x' * (sg.MAX_FILE + 1)
        box.mail_files = fetch
        return asked

    def test_attachments_go_with_a_shared_client_thread_sealed_and_never_with_a_sensitive_one(self):
        self.olive_mail()
        asked = self.files(self.own)
        self.rounds()
        renewal, health = self.event_id(self.own, 'work-1'), self.event_id(self.own, 'work-3')
        self.assertNotIn(health, [e for e, _ in asked])  # a sensitive thread: no files, not even looked up
        self.assertNotIn('p3', [p for _, p in asked])  # too large: not even read
        with self.mem.store.connect() as db:
            rows = db.execute('SELECT fleet,member,tag,body FROM team_mail').fetchall()
            tid = next(f"{r['fleet']}:{r['member']}:{r['tag']}" for r in rows if not json.loads(r['body']).get('private'))
            thread = self.mem.team_thread(db, tid)
            listed = thread['messages'][0]['attachments']
            self.assertEqual([(x['name'], bool(x.get('file')), bool(x.get('too_large'))) for x in listed],
                             [('quote.pdf', True, False), ('all-hands.mp4', False, True)])
            self.assertEqual(self.mem.team_file(db, tid, listed[0]['file']), (b'%PDF-1.4 ', 'application/pdf', 'quote.pdf'))
            with self.assertRaises(ValueError):
                self.mem.team_file(db, tid, 'f' * 32)
        self.assertNotIn(nc.b64(b'%PDF-1.4 '), json.dumps(self.net.blobs))  # the Network had it sealed only
        self.assertNotIn('quote.pdf', json.dumps(self.net.blobs))
        files = len(self.mail_puts(self.own, 'file'))
        self.assertEqual(files, 2)  # one per message that has it
        self.rounds()
        self.assertEqual(len(self.mail_puts(self.own, 'file')), files)  # once, not every round

    def test_a_careful_fleet_shares_client_threads_without_their_attachments(self):
        self.net.level = 'careful'  # the Fleet's security level (security_levels.py)
        self.olive_mail()
        asked = self.files(self.own)
        self.rounds()
        self.assertFalse([p for _, p in asked if p])  # no attachment is even read
        self.assertEqual(self.mail_puts(self.own, 'file'), [])
        with self.mem.store.connect() as db:
            rows = db.execute('SELECT fleet,member,tag,body FROM team_mail').fetchall()
            tid = next(f"{r['fleet']}:{r['member']}:{r['tag']}" for r in rows if not json.loads(r['body']).get('private'))
            listed = self.mem.team_thread(db, tid)['messages'][0]['attachments']
        self.assertEqual([(x['name'], bool(x.get('file')), bool(x.get('not_shared'))) for x in listed],
                         [('quote.pdf', False, True), ('all-hands.mp4', False, True)])

    # -- stopping takes an admin's OK, like in any CRM
    def test_stopping_needs_an_admins_ok_and_what_went_before_stays(self):
        self.olive_mail()
        self.rounds()
        self.assertEqual(self.own.graph_optout(FLEET, True)['off'], 'asked')
        self.add(self.own, mail('work', 20, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Asked meanwhile', BODY, 0, 't-meanwhile'))
        self.judge(self.own, ('work-20', 'client', 'Lee wrote again'))
        self.rounds()
        subjects = lambda: {json.loads(r['body']).get('subject') for r in self.team(self.mem, 'team_mail')}
        self.assertIn('Asked meanwhile', subjects())  # still on until an admin agrees
        self.assertFalse([p for p in self.own._prompts() if p['kind'] == 'optout'])  # nobody approves their own
        self.assertFalse([p for p in self.mem._prompts() if p['kind'] == 'optout'])  # not an admin
        self.assertEqual(self.ok(self.adm, 'optout', 'Olive'), {'ok': True, 'done': True})
        self.rounds()
        self.assertEqual(self.own.sharing()[0]['off'], 'off')
        self.add(self.own, mail('work', 21, 'Lee Park <lee@client.example>', 'own1@acme.example', 'After the OK', BODY, 0, 't-after'))
        self.judge(self.own, ('work-21', 'client', 'Lee wrote after'))
        self.rounds()
        self.assertNotIn('After the OK', subjects())
        self.assertIn('Renewal quote', subjects())  # what was shared before stays with the Fleet
        self.assertEqual(self.own.graph_optout(FLEET, False)['off'], None)  # sharing again: at once
        self.rounds()
        self.assertIn('After the OK', subjects())

    def test_the_network_cant_stop_someones_sharing(self):
        self.olive_mail()
        self.rounds()
        self.own.graph_optout(FLEET, True)
        wish = self.own.meta('graph_off:' + FLEET)['at']
        forged = ft.sign(self.mem._private(), ft.optout(FLEET, 'own1', self.net.keys['own1']['sign'], 'mem3', wish, a.now()))  # not an admin
        own_ok = ft.sign(self.own._private(), ft.optout(FLEET, 'own1', self.net.keys['own1']['sign'], 'own1', wish, a.now()))  # their own
        other = ft.sign(self.adm._private(), ft.optout(FLEET, 'own1', self.net.keys['own1']['sign'], 'adm2', 'an earlier ask', a.now()))
        self.net.trust += [forged, own_ok, other]
        self.rounds()
        self.assertEqual(self.own.sharing()[0]['off'], 'asked')

    def test_member_who_left_gets_no_new_updates(self):
        self.olive_mail()
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
        self.rounds()
        self.assertIn('Kim Wu', json.dumps(self.team(self.own)))
        del self.net.people['mem3']
        self.rounds()
        self.assertIn('Kim Wu', json.dumps(self.team(self.own)))
        with self.own.store.connect() as db:
            self.assertTrue(db.execute('SELECT left_at FROM team_sources WHERE member=?', ('mem3',)).fetchone()['left_at'])
            self.assertIn('(left)', self.own.team_overview(db, {})['people'][0]['via'])

    # -- the Network as a mailbox, and catching up Machine to Machine
    def away(self, box, days):
        """A Machine that was off for this many days: the Network let go of what waited for it."""
        self.net.expire(box.member_id)
        box.set_meta('graph_fetched_at', ago(days))
        want = box.meta('graph_want:' + FLEET)
        if want:
            box.set_meta('graph_want:' + FLEET, {**want, 'at': ago(days)})

    def relays(self, to=None):
        return [c for c in self.net.calls if c[2] == '/graph' and c[3] and c[3].get('op') == 'relay' and (to is None or to in c[3]['wraps'])]

    def test_a_blob_goes_from_the_network_once_every_machine_took_it(self):
        self.olive_mail()
        self.rounds()
        for box in self.boxes:
            box.graph_fetch()
        self.assertEqual(self.net.held(), set())  # each Machine took what was sealed for it: the Network keeps nothing
        self.assertIn('Renewal quote', json.dumps([json.loads(r['body']) for r in self.team(self.mem, 'team_mail')]))
        kept = {r['kind'] for r in self.team(self.mem, 'team_blobs')}
        self.assertTrue({'snapshot', 'mail'} <= kept)  # the Machines keep it, to pass on to a teammate who missed it
        self.assertFalse(kept & {'key', 'ask', 'want'})

    def test_a_machine_away_longer_than_the_network_keeps_catches_up_from_a_teammate(self):
        self.boxes.remove(self.mem)  # away
        self.olive_mail()
        self.add(self.adm, mail('work', 9, 'Kim Wu <kim@globex.example>', 'adm2@acme.example', 'Pilot', 'Pilot starts Monday.', 1, 't-pilot'))
        self.judge(self.adm, ('work-9', 'client', 'Kim confirmed the pilot'))
        self.rounds()
        self.away(self.mem, 40)  # 30 days on: what waited for it is gone
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        self.boxes.append(self.mem)
        self.rounds(3)
        threads = json.dumps([json.loads(r['body']) for r in self.team(self.mem, 'team_mail')])
        self.assertIn('Renewal quote', threads)
        self.assertIn('Pilot', threads)
        names = json.dumps(self.team(self.mem))
        self.assertIn('Lee Park', names)
        self.assertIn('Kim Wu', names)
        self.assertTrue(self.relays('mem3'))  # passed on by teammates' Machines, still signed by their publishers
        self.assertNotIn(HEALTH, json.dumps(self.net.calls) + json.dumps(self.net.blobs))
        self.assertFalse([c for c in self.relays() if c[3]['blob']['header']['kind'] not in ('snapshot', 'delta', 'mail', 'file')])

    def test_a_new_member_gets_what_went_before_from_teammates(self):
        self.olive_mail()
        self.add(self.mem, mail('work', 8, 'Kim Wu <kim@globex.example>', 'mem3@acme.example', 'Pilot', 'Pilot starts Monday.', 2, 't-pilot'))
        self.judge(self.mem, ('work-8', 'client', 'Kim confirmed the pilot'))
        self.rounds()
        del self.net.people['mem3']  # Mo leaves: what they shared stays with the Fleet
        self.boxes.remove(self.mem)
        self.rounds()
        self.net.people['mem4'] = ('Noor', 'member')
        noor = self.machine('mem4'); self.boxes.append(noor)
        self.rounds()
        self.ok(self.adm, 'member', 'Noor')
        before = len(self.net.calls)
        self.rounds(4)
        threads = json.dumps([json.loads(r['body']) for r in self.team(noor, 'team_mail')])
        self.assertIn('Renewal quote', threads)
        self.assertIn('Pilot', threads)  # from someone who left, passed on by an admin's Machine
        self.assertIn('Lee Park', json.dumps(self.team(noor)))
        mo = [c for c in self.relays('mem4') if c[3]['blob']['header']['from'] == 'mem3']
        self.assertTrue(mo)
        self.assertEqual({c[0] for c in mo} - {'own1', 'adm2'}, set())
        later = [c[3]['blob']['header'] for c in self.net.calls[before:] if c[2] == '/graph' and c[3] and c[3].get('op') == 'put']
        self.assertFalse([h for h in later if h['kind'] == 'mail'])  # nobody published their threads again for Noor

    def test_only_an_admin_passes_on_what_someone_who_left_shared(self):
        self.add(self.mem, mail('work', 8, 'Kim Wu <kim@globex.example>', 'mem3@acme.example', 'Pilot', 'Pilot starts Monday.', 2, 't-pilot'))
        self.judge(self.mem, ('work-8', 'client', 'Kim confirmed the pilot'))
        self.rounds()
        self.net.people['mem4'] = ('Noor', 'member')
        noor = self.machine('mem4'); self.boxes.append(noor)
        self.rounds()
        self.ok(self.adm, 'member', 'Noor')
        self.rounds(4)
        self.assertIn('Pilot', json.dumps(self.team(noor, 'team_mail')))
        del self.net.people['mem3']
        self.rounds()
        with noor.store.connect() as db:
            sg.ensure(db)
            row = dict(db.execute("SELECT * FROM team_blobs WHERE member='mem3' AND kind='mail'").fetchone())
        header = json.loads(row['header'])
        wrap = nc.seal({'dk': row['dk'], 'h': row['sha'], 'from_key': self.net.keys['mem3']['sign']}, noor._private(), self.net.keys['adm2'],
                       sg.wrap_context(header, 'mem4', 'adm2'))
        with self.assertRaisesRegex(ValueError, 'admin'):  # Noor is a member, not an admin
            self.adm.graph_take({'header': header, 'ct': row['ct'], 'sig': row['sig'], 'wrap': wrap, 'by': 'mem4'})

    def test_catching_up_goes_newest_first_a_batch_a_round_and_resumes(self):
        for n in range(6):
            self.add(self.own, mail('work', 30 + n, 'Lee Park <lee@client.example>', 'own1@acme.example', f'Order {n}', BODY, 20 - n, f't-order{n}'))
            self.judge(self.own, (f'work-{30 + n}', 'client', f'Lee placed order {n}'))
        self.boxes.remove(self.mem)
        self.rounds(4)
        self.away(self.mem, 40)
        self.boxes.append(self.mem)
        old = sg.SERVE_BATCH
        sg.SERVE_BATCH = 2
        self.addCleanup(setattr, sg, 'SERVE_BATCH', old)
        before = len(self.net.calls)
        self.rounds(8)
        subjects = [json.loads(r['body'])['subject'] for r in self.team(self.mem, 'team_mail')]
        self.assertEqual(sorted(s for s in subjects if s.startswith('Order')), [f'Order {n}' for n in range(6)])
        mail_relays = [c for c in self.net.calls[before:] if c in self.relays('mem3') and c[3]['blob']['header']['kind'] == 'mail' and c[0] == 'own1']
        seqs = [c[3]['blob']['header']['seq'] for c in mail_relays]
        self.assertEqual(seqs, sorted(seqs, reverse=True))  # the newest first
        self.assertEqual(len(seqs), len(set(seqs)))  # each once: it went on where it stopped
        self.assertGreater(len({c[3]['blob']['header']['id'] for c in mail_relays}), 2)

    # -- a dishonest control plane
    def test_a_changed_blob_or_a_forged_one_is_refused(self):
        self.olive_mail()
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
        self.rounds()
        lines = a.recap_share_lines(self.own.store, {'dashboard_host': 'box.example', 'dashboard_path': '/'})
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], f"• Shared 1 thread with Acme this week, 1 held back. [What's shared](https://box.example/#shared={FLEET})")


    def test_empty_removal_blobs_remove_threads_files_and_catchup_requests(self):
        self.olive_mail()
        self.files(self.own)
        self.rounds()
        group = self.own._group(FLEET)
        me = self.own._me_in(group)
        thread = next(r for r in self.team(self.mem, 'team_mail')
                      if r['member'] == 'own1' and not json.loads(r['body']).get('private'))
        tag = thread['tag']
        old_id, _, wrap, by = next(w for w in self.net.wraps if w[1] == 'mem3' and self.net.blobs[w[0]]['header'].get('tag') == tag)
        old_item = {**copy.deepcopy(self.net.blobs[old_id]), 'wrap': wrap, 'by': by}
        attachments = [r['tag'] for r in self.team(self.mem, 'team_files') if r['member'] == 'own1']
        self.assertTrue(attachments)
        for ftag in attachments:
            self.own._put(group, me, 'file', None, ['mem3'], tag=ftag)
        self.own._put(group, me, 'mail', None, ['mem3'], tag=tag)
        self.own._put(group, me, 'want', {'at': a.now(), 'snap': {}, 'got': {}}, ['mem3'], tag='a' * 32)
        self.mem.graph_fetch()
        self.assertFalse([r for r in self.team(self.mem, 'team_mail') if r['tag'] == tag])
        self.assertFalse([r for r in self.team(self.mem, 'team_files') if r['tag'] in attachments])
        self.assertTrue([r for r in self.team(self.mem, 'team_wants') if r['member'] == 'own1'])
        self.own._put(group, me, 'want', None, ['mem3'], tag='a' * 32)
        self.mem.graph_fetch()
        self.assertFalse([r for r in self.team(self.mem, 'team_wants') if r['member'] == 'own1'])
        # The signed tombstones, not the old content, remain available for an offline teammate's catch-up.
        kept = [r for r in self.team(self.mem, 'team_blobs') if r['tag'] == tag]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['ct'], '')
        with self.assertRaisesRegex(ValueError, 'older'):
            self.mem.graph_take(old_item)

    @patch.object(sg, 'SERVE_BATCH', 3)  # same signed catch-up path, a smaller per-round budget keeps this fixture quick
    def test_catchup_chain_larger_than_one_round_advances_in_order(self):
        group = self.own._group(FLEET)
        me = self.own._me_in(group)
        initial = next(r for r in self.team(self.mem, 'team_sources') if r['member'] == 'own1')['seq']
        previous = self.own._put(group, me, 'snapshot', {'records': [], 'threads': []}, ['adm2', 'mem3'])
        for _ in range(sg.SERVE_BATCH + 1):
            previous = self.own._put(group, me, 'delta', {'prev': previous['seq'], 'records': [], 'threads': []}, ['adm2', 'mem3'])
        self.adm.graph_fetch()
        self.net.expire('mem3')
        self.mem._put(self.mem._group(FLEET), self.mem._me_in(self.mem._group(FLEET)), 'want',
                      {'at': a.now(), 'snap': {}, 'got': {}}, ['adm2'], tag='b' * 32)
        self.adm.graph_fetch()
        self.adm.set_meta('graph_puts', [])
        adm_group = self.adm._group(FLEET)
        found, trusted, _ = self.adm.confirmed(adm_group)
        self.adm._graph_serve(adm_group, self.adm._me_in(adm_group), found, trusted)
        self.mem.graph_fetch()
        source = next(r for r in self.team(self.mem, 'team_sources') if r['member'] == 'own1')
        self.assertGreater(source['seq'], initial)
        self.assertLess(source['seq'], previous['seq'])
        self.adm._graph_serve(adm_group, self.adm._me_in(adm_group), found, trusted)
        self.mem.graph_fetch()
        source = next(r for r in self.team(self.mem, 'team_sources') if r['member'] == 'own1')
        self.assertEqual(source['seq'], previous['seq'])

    def test_a_temporary_attachment_listing_failure_is_retried_without_new_mail(self):
        self.olive_mail()
        self.files(self.own)
        fetch = self.own.mail_files
        self.own.mail_files = lambda *args: (_ for _ in ()).throw(OSError('temporary provider outage'))
        self.rounds()
        self.assertFalse(self.team(self.mem, 'team_files'))
        self.own.mail_files = fetch
        self.rounds()
        self.assertTrue(self.team(self.mem, 'team_files'))

    # -- seamless, and still safe: removal, new devices, pause
    def new_thread(self, n, subject, body, days=0):
        self.add(self.own, mail('work', n, 'Lee Park <lee@client.example>', 'own1@acme.example', subject, body, days, f't-new-{n}'))
        self.judge(self.own, (f'work-{n}', 'client', 'Lee wrote'))

    def received(self, box):
        return json.dumps(self.team(box, 'team_mail'))

    def test_someone_taken_out_gets_nothing_new_even_if_the_network_keeps_them_in(self):
        self.olive_mail()
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        self.adm.graph_remove(FLEET, 'mem3')
        self.assertTrue(any(i['s']['t'] == 'remove' and i['s']['member'] == 'mem3' for i in self.net.trust))
        self.rounds(1)
        self.net.trust = [i for i in self.net.trust if i['s']['t'] != 'remove']  # a Network that hides the remove later
        self.new_thread(20, 'Second order', 'Forty more seats, please.')
        self.rounds()
        self.assertNotIn('Forty more seats', self.received(self.mem))
        self.assertIn(BODY, self.received(self.mem))  # what they had stays with them
        self.assertFalse([p for p in self.adm._prompts() if p['kind'] == 'member' and p['member'] == 'mem3'])  # no new OK asked
        self.assertEqual({p['name']: p['state'] for p in self.adm.sharing()[0]['people']}['Mo'], 'removed')
        self.adm.graph_remove(FLEET, 'mem3', back=True)
        self.rounds(6)
        self.assertIn('Forty more seats', self.received(self.mem))

    def test_someone_who_left_is_signed_out_so_the_network_cant_bring_them_back(self):
        self.rounds()
        person = self.net.people.pop('mem3')  # they left the Fleet
        self.rounds()
        self.assertTrue(any(i['s']['t'] == 'remove' and i['s']['member'] == 'mem3' for i in self.net.trust))
        self.net.people['mem3'] = person  # a Network that puts them back, with the same keys
        self.new_thread(21, 'After they left', 'Nothing of this goes to them.')
        self.rounds()
        self.assertNotIn('Nothing of this', self.received(self.mem))

    def test_a_new_machine_of_someone_admitted_needs_no_ok_but_waits_for_the_hold(self):
        self.olive_mail()
        self.rounds()
        phone = self.machine('mem3', '-new')  # Mo's new Machine: new keys, same person
        phone.set_meta('graph_start:' + FLEET, {'shown': a.now(), 'started': a.now()})
        self.boxes = [self.own, self.adm, phone]
        self.rounds()
        self.assertFalse([p for p in self.adm._prompts() if p['kind'] == 'member'])  # no tap for a known person
        devices = self.adm.sharing()[0]['new_devices']
        self.assertEqual([d['name'] for d in devices], ['Mo'])
        self.assertEqual([d['name'] for d in self.own.sharing()[0]['new_devices']], ['Mo'])  # every admin sees it
        self.assertEqual(phone.sharing()[0]['new_devices'], [])
        self.assertNotIn(BODY, self.received(phone))  # not during the hold
        for box in (self.own, self.adm):
            rekey = box.meta('graph_rekey:' + FLEET) or {}
            for r in rekey.values():
                r['since'] = ago(2)
            box.set_meta('graph_rekey:' + FLEET, rekey)
        self.rounds(3)
        self.assertTrue(any(i['s']['t'] == 'member' and i['s'].get('device') and i['s']['member'] == 'mem3' for i in self.net.trust))
        self.assertIn(BODY, self.received(phone))

    def test_not_theirs_keeps_a_new_key_out(self):
        self.rounds()
        stranger = self.machine('mem3', '-forged')  # keys the Network made up for Mo
        self.boxes = [self.own, self.adm, stranger]
        self.rounds()
        for box in (self.own, self.adm):
            box.graph_rekey_no(FLEET, 'mem3')
            rekey = box.meta('graph_rekey:' + FLEET) or {}
            self.assertNotIn('mem3', rekey)
        self.olive_mail()
        self.rounds(3)
        self.assertFalse(any(i['s']['t'] == 'member' and i['s'].get('device') for i in self.net.trust))
        self.assertEqual(self.received(stranger), '[]')

    def test_an_admin_pauses_sharing_for_everyone(self):
        self.olive_mail()
        self.adm.graph_pause(FLEET, True)
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))
        self.assertEqual(self.own.sharing()[0]['paused']['by'], 'Ada')
        with self.assertRaisesRegex(ValueError, 'confirmed admin'):
            self.mem.graph_pause(FLEET, False)
        self.adm.graph_pause(FLEET, False)
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))

    def test_sharing_off_with_remove_deletes_it_everywhere_also_on_a_machine_that_was_away(self):
        self.olive_mail()
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        self.boxes.remove(self.mem)  # away
        self.new_thread(40, 'Late order', 'One more order, sent while Mo was away.')
        self.rounds()  # waits in the mailbox for Mo's Machine
        time.sleep(1.1)  # statements and blobs carry whole seconds: the removal comes after the late blob
        with self.assertRaisesRegex(ValueError, 'confirmed admin'):
            self.mem.graph_pause(FLEET, True, remove=True)
        self.adm.graph_pause(FLEET, True, remove=True)
        self.rounds()
        self.assertTrue(self.own.sharing()[0]['paused']['removed'])
        for box in (self.own, self.adm):
            self.assertEqual(self.team(box, 'team_mail') + self.team(box) + self.team(box, 'team_blobs'), [])
        self.assertFalse(self.own.sharing()[0]['started'])  # each owner looks it over again
        self.adm.graph_pause(FLEET, False)  # back on before Mo's Machine is: the removal still reaches it
        self.rounds()
        self.boxes.append(self.mem)
        self.rounds()
        self.assertEqual(self.team(self.mem, 'team_mail') + self.team(self.mem) + self.team(self.mem, 'team_blobs'), [])
        self.assertNotIn('One more order', json.dumps(self.team(self.mem, 'team_mail')))  # the late blob from before is dropped
        self.own.graph_start(FLEET)
        self.rounds(3)
        self.assertIn(BODY, self.received(self.mem))  # Olive started again: it goes again, from the start
        self.assertIn('One more order', self.received(self.mem))

    def test_an_unshared_thread_is_never_passed_on_from_an_old_copy(self):
        self.olive_mail()
        self.rounds()
        with self.adm.store.connect() as db:
            sg.ensure(db)
            old = dict(db.execute("SELECT * FROM team_blobs WHERE member='own1' AND kind='mail'").fetchone())
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-1'), 'private')
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))
        self.assertNotIn(BODY, self.received(self.adm))
        header = json.loads(old['header'])  # a teammate's Machine that kept an old copy passes it on anyway
        wrap = nc.seal({'dk': old['dk'], 'h': old['sha'], 'from_key': self.net.keys['own1']['sign']}, self.adm._private(), self.net.keys['mem3'],
                       sg.wrap_context(header, 'adm2', 'mem3'))
        with self.assertRaisesRegex(ValueError, 'no longer shared|older'):  # its tombstone or the newer list stops it
            self.mem.graph_take({'header': header, 'ct': old['ct'], 'sig': old['sig'], 'wrap': wrap, 'by': 'adm2'})
        with self.mem.store.connect() as db:  # a Machine that never got the tombstone: the newer thread list still stops it
            db.execute("DELETE FROM team_blobs WHERE member='own1' AND kind='mail'")
        with self.assertRaisesRegex(ValueError, 'no longer shared'):
            self.mem.graph_take({'header': header, 'ct': old['ct'], 'sig': old['sig'], 'wrap': wrap, 'by': 'adm2'})
        self.assertNotIn(BODY, self.received(self.mem))

    def test_sharing_is_off_until_an_admin_turns_it_on(self):
        self.olive_mail()
        self.rounds()
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        self.assertEqual(self.own.sharing()[0]['paused'], {'default': True})
        with self.assertRaisesRegex(ValueError, 'confirmed admin'):
            self.mem.graph_pause(FLEET, False)
        self.adm.graph_pause(FLEET, False)
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        self.assertIsNone(self.own.sharing()[0]['paused'])

    def test_each_connection_is_shared_or_not_work_on_and_personal_never(self):
        self.olive_mail()
        self.add(self.own, mail('home', 20, 'Kim Wu <kim@globex.example>', 'own1@home.example', 'Pilot from home', 'Pilot price, from my own address.', 1, 't-home-pilot'))
        self.judge(self.own, ('home-20', 'client', 'Kim asked about the pilot'), ('home-5', 'personal', 'Sam wrote'))
        conns = {c['id']: c for c in self.own.sharing()[0]['connections']}
        self.assertEqual((conns['work']['on'], conns['home']['on'], conns['home']['personal']), (True, False, True))
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        with self.assertRaisesRegex(ValueError, 'Personal connections are never shared'):
            self.own.graph_connection(FLEET, 'home', True)
        self.own.set_meta('graph_conn:' + FLEET, {'home': True})  # an old choice from before can't turn a personal one on
        self.rounds()
        self.assertNotIn('Pilot price', self.received(self.mem))
        self.assertNotIn(PERSONAL, self.received(self.mem))
        self.own.graph_connection(FLEET, 'work', False)  # off: what it shared is taken back
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))

    def test_an_admin_can_require_work_email_and_threads_kept_private_still_stay(self):
        self.olive_mail()
        with self.assertRaisesRegex(ValueError, 'confirmed admin'):
            self.mem.graph_set_policy(FLEET, ['mail'])
        self.adm.graph_set_policy(FLEET, ['mail', 'calendar', 'everything'])
        self.rounds()
        self.assertEqual(self.own.sharing()[0]['require'], ['mail', 'calendar'])
        work = next(c for c in self.own.sharing()[0]['connections'] if c['id'] == 'work')
        self.assertEqual((work['required'], work['on']), (True, True))
        self.assertFalse(next(c for c in self.own.sharing()[0]['connections'] if c['id'] == 'home')['required'])  # personal: never required
        with self.assertRaisesRegex(ValueError, 'Required'):
            self.own.graph_connection(FLEET, 'work', False)
        self.own.set_meta('graph_conn:' + FLEET, {'work': False})  # an old choice can't beat what the Fleet requires
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-1'), 'private')  # Keep private on one thread still holds
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))
        forged = ft.sign(self.mem._private(), {'t': 'policy', 'fleet': FLEET, 'member': 'mem3', 'key': self.net.keys['mem3']['sign'], 'by': 'mem3',
                                               'require': [], 'at': '2099-01-01T00:00:00+00:00'})
        self.net.trust.append(forged)  # a Network that makes one up, or a member who isn't an admin: ignored
        self.rounds()
        self.assertEqual(self.own.sharing()[0]['require'], ['mail', 'calendar'])

    def test_an_only_you_board_shares_nothing_and_switching_to_it_takes_back_what_went(self):
        self.olive_mail()
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        with self.own.store.connect() as db:  # this project is Only you
            db.execute("INSERT INTO projects(id,name,created_at,visibility) VALUES(77,'Quiet deal',?,'only_you')", (a.now(),))
            db.execute("INSERT INTO event_projects(event_id,project_id,source,created_at) VALUES(?,77,'test',?)", (self.event_id(self.own, 'work-2'), a.now()))
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))  # the whole thread, also its other messages
        self.assertNotIn('Quiet deal', json.dumps(self.team(self.mem)))

    # -- what goes, judged per thread: when unsure, not
    def test_a_new_client_thread_waits_an_hour_first(self):
        sg.HOLD = 3600
        self.olive_mail()
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))
        with self.own.store.connect() as db:
            db.execute('UPDATE share_threads SET sorted_at=?', (ago(0, 2),))
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))

    def test_unsure_waits_in_share_these_and_other_never_goes(self):
        self.add(self.own, mail('work', 30, 'Pat Peer <pat@peer.example>', 'own1@acme.example', 'Swap listings?', 'Shall we list each other?', 1, 't-peer'),
                 mail('work', 31, 'Ads Team <team@ads.example>', 'own1@acme.example', 'Grow your reach', 'Our new ad formats are here.', 1, 't-ads'))
        self.judge(self.own, ('work-30', 'unsure', 'Pat asked about listings'), ('work-31', 'other', ''))
        self.rounds()
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        view = self.own.graph_overview(FLEET)
        self.assertEqual([x['title'] for x in view['unsure']], ['Swap listings?'])
        self.assertEqual(view['sorted']['other'], 1)
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        app.graph_share_action('graph_share_many', {'event_ids': [self.event_id(self.own, 'work-30')], 'share': 'team'})
        self.rounds()
        self.assertIn('Shall we list each other?', self.received(self.mem))
        self.assertNotIn('ad formats', self.received(self.mem))

    def test_sensitive_sticks_whatever_a_later_review_says(self):
        self.olive_mail()
        for later in ('client', 'unsure', 'other'):
            self.judge(self.own, ('work-3', later, 'Lee wrote'))
            with self.own.store.connect() as db:
                self.assertEqual(db.execute('SELECT verdict FROM share_threads WHERE event_id=?', (self.event_id(self.own, 'work-3'),)).fetchone()[0], 'sensitive')
        self.rounds()
        self.assertNotIn(HEALTH, self.received(self.mem))

    def test_a_shared_thread_a_later_review_calls_sensitive_is_taken_back(self):
        self.olive_mail()
        self.rounds()
        self.assertIn(BODY, self.received(self.mem))
        self.judge(self.own, ('work-1', 'sensitive', 'Lee wrote about the deal'))
        self.rounds()
        self.assertNotIn(BODY, self.received(self.mem))
        self.assertNotIn(BODY, self.received(self.adm))

    def test_automated_senders_are_never_a_client(self):
        self.add(self.own, mail('work', 40, 'Site Audit <no-reply@audit.example>', 'own1@acme.example', 'Weekly audit', '12 broken links found.', 1, 't-audit'))
        self.judge(self.own, ('work-40', 'client', 'An audit'))
        self.rounds()
        self.assertNotIn('broken links', self.received(self.mem))

    def test_choosing_twice_for_one_company_asks_once_to_make_it_a_rule(self):
        self.add(self.own, *[mail('work', 50 + i, 'Lee Park <lee@client.example>', 'own1@acme.example', f'Note {i}', f'Note body {i}', 1, f't-note-{i}') for i in range(3)])
        self.judge(self.own, *[(f'work-{50 + i}', 'client', 'Lee wrote') for i in range(3)])
        app = object.__new__(a.App); app.store, app.fleet, app.config = self.own.store, self.own, self.own.config
        first = app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-50'), 'share': 'only_you'})
        self.assertIsNone(first['suggest'])
        second = app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-51'), 'share': 'only_you'})
        self.assertEqual(second['suggest'], {'key': '@client.example', 'name': 'Client', 'choice': 'private'})
        again = app.graph_share_action('graph_share', {'event_id': self.event_id(self.own, 'work-52'), 'share': 'only_you'})
        self.assertIsNone(again['suggest'])  # asked once
        app.graph_share_action('graph_rule', {'key': '@client.example', 'choice': 'private', 'name': 'Client'})
        self.add(self.own, mail('work', 60, 'Kai <kai@client.example>', 'own1@acme.example', 'Later', 'A later note.', 0, 't-later'))
        self.judge(self.own, ('work-60', 'client', 'Kai wrote'))
        self.rounds()
        self.assertNotIn('A later note.', self.received(self.mem))

    def test_the_review_sorts_every_thread_again_once_hand_choices_stay(self):
        self.olive_mail()
        with self.own.store.connect() as db:
            sg.set_hand(db, self.event_id(self.own, 'work-4'), 'private')
            db.execute("DELETE FROM share_meta WHERE key='sorter'")
            sg.ensure(db)
            calls = dict(db.execute('SELECT event_id,coalesce(verdict,hand) FROM share_threads').fetchall())
        self.assertEqual(calls, {self.event_id(self.own, 'work-1'): None, self.event_id(self.own, 'work-3'): None, self.event_id(self.own, 'work-4'): 'internal'})  # a thread with a hand choice keeps its call
        with self.own.store.connect() as db:
            pending = sg.unjudged(db, self.own.scopes(db)[FLEET], 10)
        thread = next(x for x in pending if x['subject'] == 'Re: Renewal quote')
        self.assertEqual([x['text'] for x in thread['earlier']], [BODY])  # what an earlier message says counts too

    # -- what went wrong in a live Fleet: these are its cases, with made-up names
    def side_company(self, box=None):
        """A second company of Olive's, connected as Work too: its own mailbox, at its own domain."""
        box = box or self.own
        box.config['accounts'].append({'id': 'side', 'email': 'olive@sidecorp.example', 'boundary': 'work:Sidecorp', 'client': 'x', 'transport': 'gog'})
        self.add(box, mail('side', 1, 'Pat Moss <pat@buyer.example>', 'olive@sidecorp.example', 'Clinic pilot', 'Sidecorp pilot for three clinics.', 1, 't-side'),
                 mail('side', 2, 'Olive <olive@sidecorp.example>', 'pat@buyer.example', 'Re: Clinic pilot', 'Happy to start in May.', 0, 't-side'))
        self.judge(box, ('side-1', 'client', 'Pat asked about a pilot'))

    def test_automated_billing_security_and_recruiting_mail_never_goes(self):
        def categorised(m, *labels):
            m['metadata']['labels'] = list(labels)
            return m
        self.add(self.own,
                 mail('work', 70, 'Tool Co <invoice+statements@pay.example>', 'own1@acme.example', 'Your receipt from Tool Co', 'Receipt 2543, paid by card.', 1, 't-receipt'),
                 mail('work', 71, 'Cloud <payments-noreply@cloud.example>', 'own1@acme.example', 'Invoices due soon', 'Two invoices are due.', 1, 't-due'),
                 categorised(mail('work', 72, 'AI Lab <team@mail.ailab.example>', 'own1@acme.example', 'Out of usage credits', 'Your organization is out of credits.', 1, 't-credits'), 'CATEGORY_UPDATES', 'INBOX'),
                 categorised(mail('work', 73, 'Ann Fox <ann@agency.example>', 'own1@acme.example', 'The part they leave out', 'A newsletter about success.', 1, 't-news'), 'CATEGORY_PROMOTIONS'),
                 mail('work', 74, 'Code Host <noreply@codehost.example>', 'own1@acme.example', 'A token was added', 'A new access token was added to your account.', 1, 't-token'),
                 mail('work', 75, 'Jo Hart <jo@recruit.example>', 'own1@acme.example', 'Interview opportunity', 'A role I think fits you, Olive.', 1, 't-recruit'),
                 categorised(mail('work', 76, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Billing question', 'Can we move to yearly billing?', 2, 't-bill'), 'CATEGORY_UPDATES'),
                 mail('work', 77, 'Olive <own1@acme.example>', 'lee@client.example', 'Re: Billing question', 'Yes, from next month.', 1, 't-bill'))
        # the review got some wrong; the rules hold anyway, and its own calls for security and recruiting say sensitive
        self.judge(self.own, ('work-70', 'client', 'A receipt'), ('work-71', 'client', 'Invoices'), ('work-72', 'client', 'Credits'),
                   ('work-73', 'client', 'A newsletter'), ('work-74', 'sensitive', 'A security alert'), ('work-75', 'sensitive', 'A recruiter wrote'),
                   ('work-76', 'client', 'Lee asked about yearly billing'))
        self.rounds()
        received = json.dumps(self.team(self.mem) + self.team(self.mem, 'team_mail'))
        for text in ('Receipt 2543', 'Two invoices', 'out of credits', 'newsletter about success', 'access token', 'A token was added',
                     'A security alert', 'role I think fits', 'Interview', 'A recruiter wrote', 'Jo'):
            self.assertNotIn(text, received)
        self.assertIn('yearly billing', received)  # a customer asking about their bill, and Olive answered: a client thread
        with self.own.store.connect() as db:
            scope = self.own.scopes(db)[FLEET]
            views = {s: sg.visibility(db, scope, dict(db.execute('SELECT * FROM events WHERE source_id=?', (s,)).fetchone()))
                     for s in ('work-70', 'work-71', 'work-72', 'work-73', 'work-74', 'work-76')}
            self.assertEqual(views, {'work-70': 'other', 'work-71': 'other', 'work-72': 'other', 'work-73': 'other', 'work-74': 'other', 'work-76': 'team'})
            self.assertEqual([x['subject'] for x in sg.unjudged(db, scope, 50) if x['subject'] in ('Your receipt from Tool Co', 'Out of usage credits')], [])
        self.assertGreaterEqual(self.own.graph_overview(FLEET)['sorted']['other'], 4)

    def test_the_review_is_told_billing_security_and_recruiting_are_never_client(self):
        for words in ('own bills', 'support tickets with the tools and suppliers', 'security', 'recruiting', 'never choose client'):
            self.assertIn(words, a.SHARE_VERDICTS)

    def test_a_second_companys_mailbox_starts_off_and_its_owner_decides(self):
        self.olive_mail()
        self.side_company()
        # the domain decides, not the name: a mailbox named for this Fleet at another company's domain is another company's
        self.own.config['accounts'].append({'id': 'named', 'email': 'olive@othercorp.example', 'boundary': 'work:Acme', 'service': 'gmail'})
        self.own.config['accounts'].append({'id': 'code', 'email': 'olive-codes', 'boundary': 'work:Acme', 'service': 'github'})
        conns = {c['id']: c for c in self.own.sharing()[0]['connections']}
        self.assertEqual((conns['side']['work'], conns['side']['company'], conns['side']['on'], conns['side']['personal']), (False, False, False, False))  # never in the Fleet's briefs either
        self.assertEqual((conns['named']['company'], conns['named']['on']), (False, False))
        self.assertEqual((conns['code']['company'], conns['code']['on']), (True, True))  # no address: named for the Fleet
        self.assertEqual((conns['work']['company'], conns['work']['on']), (True, True))
        self.adm.graph_set_policy(FLEET, ['mail'])  # what the Fleet requires is its own company's mail, never another's
        self.rounds()
        conns = {c['id']: c for c in self.own.sharing()[0]['connections']}
        self.assertEqual((conns['side']['required'], conns['side']['on'], conns['side']['kind']), (False, False, 'mail'))
        self.assertEqual((conns['named']['required'], conns['named']['on']), (False, False))
        self.assertEqual(self.own.fleet_domains(FLEET), {'acme.example'})  # Olive's other addresses aren't the Fleet's
        self.assertIn(BODY, self.received(self.mem))
        self.assertNotIn('Sidecorp pilot', self.received(self.mem))
        self.own.graph_connection(FLEET, 'side', True)
        self.rounds()
        self.assertIn('Sidecorp pilot', self.received(self.mem))
        self.own.graph_connection(FLEET, 'side', False)  # taken back from the teammates' Machines
        self.rounds()
        self.assertNotIn('Sidecorp pilot', self.received(self.mem))
        self.assertIn(BODY, self.received(self.mem))

    def test_a_gmail_account_without_a_service_is_work_email_the_fleet_can_require(self):
        for acc in self.own.config['accounts']:
            acc.pop('service', None)
        self.adm.graph_set_policy(FLEET, ['mail'])
        self.rounds()
        work = next(c for c in self.own.sharing()[0]['connections'] if c['id'] == 'work')
        self.assertEqual((work['kind'], work['service'], work['required'], work['on']), ('mail', 'gmail', True, True))

    def test_an_update_keeps_a_fleet_that_was_sharing_on_and_sorts_everything_again(self):
        """The version before shared without a Fleet switch, without per-connection choices and with an older sorting: a
        receipt, a second company's mailbox and a deal went as client. After the update the Fleet stays on, those are
        taken back at once or once sorted again, and a real client thread stays with the team throughout."""
        self.olive_mail()
        self.side_company()
        receipt = mail('work', 80, 'Tool Co <hello@toolco.example>', 'own1@acme.example', 'Your receipt', 'Receipt 99, paid.', 1, 't-r')
        receipt['metadata']['labels'] = ['CATEGORY_UPDATES', 'INBOX']  # an ordinary sender address, filed as an update by the mailbox
        self.add(self.own, receipt,
                 mail('work', 81, 'Max Rue <max@buyer.example>', 'own1@acme.example', 'Acquisition', 'Our offer to buy your company.', 1, 't-deal'))
        self.judge(self.own, ('work-80', 'client', 'A receipt'), ('work-81', 'client', 'Max wrote'))
        self.own.set_meta('graph_conn:' + FLEET, {'side': True})
        with patch.object(sg.Scope, 'automated', new_callable=unittest.mock.PropertyMock, return_value=set()):  # the old sorting
            self.rounds()
        for text in (BODY, 'Receipt 99', 'Sidecorp pilot', 'offer to buy'):
            self.assertIn(text, self.received(self.mem))
        # the update: no switch statement yet (the version before had none), no connection choices, every call sorted again
        self.net.trust = [t for t in self.net.trust if t['s'].get('t') != 'pause']
        for box in self.boxes:
            box.set_meta('graph_pause:' + FLEET, None)
        self.own.set_meta('graph_conn:' + FLEET, None)
        with self.own.store.connect() as db:
            db.execute("DELETE FROM share_meta WHERE key='sorter'")
            sg.ensure(db)
        group = self.mem.directory()['fleets'][0]
        self.assertIsNone(self.mem.graph_paused(group))  # a member's Machine that shared before: still on, before any admin says so
        self.rounds()
        self.assertIsNone(self.own.sharing()[0]['paused'])  # stays on, for everyone
        said = [t['s'] for t in self.net.trust if t['s'].get('t') == 'pause']
        self.assertEqual([(s['by'], s['on']) for s in said], [('own1', False)])  # signed once by an admin's Machine that was sharing
        received = self.received(self.mem)
        self.assertNotIn('Receipt 99', received)  # a rule says no: taken back at once
        self.assertNotIn('Sidecorp pilot', received)  # another company's mailbox: off unless its owner turns it on
        self.assertIn(BODY, received)  # waits to be sorted again: stays with the team meanwhile
        self.assertIn('offer to buy', received)
        waiting = self.own.graph_unjudged(1)
        self.assertEqual(len(waiting), 1)
        self.assertIn(waiting[0]['subject'], ('Re: Renewal quote', 'Acquisition'))  # what went before is sorted first
        tombstones = len([b for b in self.net.blobs.values() if b['header']['kind'] == 'mail'])
        self.judge(self.own, ('work-1', 'client', 'Lee asked for the renewal quote'), ('work-81', 'sensitive', 'Max wrote'))
        self.rounds()
        received = self.received(self.mem)
        self.assertIn(BODY, received)
        self.assertNotIn('offer to buy', received)  # sorted again as sensitive: taken back
        self.assertNotIn('Max wrote', json.dumps(self.team(self.mem)))
        self.assertTrue(self.own.sharing()[0]['started'])  # it shared before: no new Start now
        self.assertLessEqual(len([b for b in self.net.blobs.values() if b['header']['kind'] == 'mail']) - tombstones, 1)  # no take back and send again

    def test_a_fleet_that_never_shared_stays_off_after_the_update(self):
        self.olive_mail()
        self.net.trust = [t for t in self.net.trust if t['s'].get('t') != 'pause']
        for box in self.boxes:
            box.set_meta('graph_pause:' + FLEET, None)
        self.rounds()
        self.assertEqual(self.own.sharing()[0]['paused'], {'default': True})
        self.assertEqual(self.team(self.mem, 'team_mail'), [])
        self.assertEqual([t for t in self.net.trust if t['s'].get('t') == 'pause'], [])

    # -- a board or a thread shared with chosen teammates, by name
    def deal_board(self):
        """Olive's Only you M&A board: a buyer's terms (sensitive), the buyer's billing robot, a personal note from the
        buyer, and the deal's thread in her second company's mailbox."""
        self.olive_mail()
        self.side_company()
        self.add(self.own,
                 mail('work', 20, 'Bo Hale <bo@buyer.example>', 'own1@acme.example', 'Acquisition terms', DEAL, 3, 't-deal'),
                 mail('work', 21, 'Olive <own1@acme.example>', 'bo@buyer.example', 'Re: Acquisition terms', 'Thanks, we will review.', 2, 't-deal'),
                 mail('work', 22, 'Buyer <billing-noreply@buyer.example>', 'own1@acme.example', 'Data room invoice', 'Invoice 88 for the data room.', 2, 't-room'),
                 mail('work', 23, 'Bo Hale <bo@buyer.example>', 'own1@acme.example', 'Our kids', 'Family weekend plans.', 2, 't-kids'))
        self.judge(self.own, ('work-20', 'sensitive', 'Bo sent the acquisition terms'), ('work-22', 'client', 'An invoice'), ('work-23', 'personal', 'Family'))
        return self.board(self.own, 'Acquisition conversations', 'only_you',
                          {'Sale: Buyer': ['work-20', 'work-21', 'work-22', 'work-23', 'side-1']})

    def only_blobs(self):
        return [c[3]['blob'] for c in self.net.calls if c[2] == '/graph' and c[3] and c[3].get('op') in ('put', 'relay')
                and c[3]['blob']['header'].get('only')]

    def test_a_board_shared_by_name_goes_only_to_them_sensitive_too_and_never_personal_automated_or_another_company(self):
        board = self.deal_board()
        self.rounds()
        self.assertNotIn(DEAL, self.received(self.mem))
        with self.assertRaises(ValueError):
            self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'someone'}])  # only confirmed teammates
        out = self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.assertEqual((out['visibility'], out['shared_with']), ('people', ['Mo']))
        self.rounds()
        self.assertIn(DEAL, self.received(self.mem))  # sensitive, and shared with Mo by name: it goes to Mo
        self.assertEqual(self.projects_of(self.mem)['Sale: Buyer']['board'], 'Acquisition conversations')
        self.assertIn(BODY, self.received(self.mem))  # and the rest of the team's Graph as before
        for text in ('Invoice 88', 'Family weekend', 'Sidecorp pilot', HEALTH, PERSONAL):
            self.assertNotIn(text, self.received(self.mem) + json.dumps(self.team(self.mem)))
        self.assertNotIn(DEAL, self.received(self.adm))  # Ada isn't on it
        self.assertNotIn('Sale: Buyer', self.projects_of(self.adm))
        self.assertIn(BODY, self.received(self.adm))
        blobs = self.only_blobs()
        self.assertTrue(blobs)
        self.assertEqual({tuple(b['header']['to']) for b in blobs}, {('mem3',)})  # sealed to Mo alone
        self.assertEqual({k for b in blobs for k in b.get('wraps') or {}}, {'mem3'})
        self.assertNotIn(DEAL, json.dumps(self.net.calls) + json.dumps(self.net.blobs))
        with self.own.store.connect() as db:
            self.assertEqual(graph.hidden_project_ids(db), {1})  # never the whole team's
        # Back to Only you: taken back from Mo's Machine, signed by Olive's
        self.own.graph_board_share(board, [])
        self.rounds()
        self.assertNotIn(DEAL, self.received(self.mem))
        self.assertNotIn('Sale: Buyer', self.projects_of(self.mem))
        self.assertIn(BODY, self.received(self.mem))
        with self.own.store.connect() as db:
            self.assertEqual(db.execute('SELECT visibility FROM boards WHERE id=?', (board,)).fetchone()[0], 'only_you')

    def test_threads_of_a_board_shared_by_name_are_sorted_first_and_then_go_only_to_them(self):
        board = self.deal_board()
        self.add(self.own,
                 mail('work', 24, 'Bo Hale <bo@buyer.example>', 'own1@acme.example', 'Closing date', 'We aim to close on June 3.', 1, 't-close'),
                 mail('work', 25, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Support question', 'How do I export a report?', 0, 't-support'))
        with self.own.store.connect() as db:
            graph.label(db, [self.event_id(self.own, 'work-24')], 'Sale: Buyer', 'review', a.now())
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        waiting = self.own.graph_unjudged(1)
        self.assertEqual([w['subject'] for w in waiting], ['Closing date'])  # the owner waits on it: before the rest
        self.assertIn('Support question', [w['subject'] for w in self.own.graph_unjudged()])
        self.judge(self.own, ('work-24', 'sensitive', 'Bo set a closing date'))
        self.assertNotIn('Closing date', [w['subject'] for w in self.own.graph_unjudged()])
        self.rounds()
        self.assertIn('close on June 3', self.received(self.mem))  # sorted: it goes to Mo
        self.assertNotIn('close on June 3', self.received(self.adm) + json.dumps(self.team(self.adm)))

    def test_another_company_or_a_personal_account_on_a_board_shared_by_name_stays_out(self):
        board = self.deal_board()
        self.add(self.own,
                 mail('side', 3, 'Pat Moss <pat@buyer.example>', 'olive@sidecorp.example', 'Side deal', 'Sidecorp side letter.', 1, 't-side-deal'),
                 mail('home', 24, 'Bo Hale <bo@buyer.example>', 'own1@home.example', 'From home', 'Home note on the sale.', 1, 't-home-deal'))
        with self.own.store.connect() as db:
            graph.label(db, [self.event_id(self.own, 'side-3'), self.event_id(self.own, 'home-24')], 'Sale: Buyer', 'review', a.now())
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        subjects = [w['subject'] for w in self.own.graph_unjudged()]
        self.assertNotIn('Side deal', subjects)  # never even queued to be sorted
        self.assertNotIn('From home', subjects)
        self.judge(self.own, ('side-3', 'client', 'Pat sent a side letter'), ('home-24', 'client', 'Bo wrote from home'))
        self.rounds()
        self.assertIn(DEAL, self.received(self.mem))
        for text in ('Sidecorp side letter', 'Home note on the sale', 'Sidecorp pilot'):
            for box in (self.mem, self.adm):
                self.assertNotIn(text, self.received(box) + json.dumps(self.team(box)))
        self.assertNotIn('Sidecorp side letter', json.dumps(self.net.calls) + json.dumps(self.net.blobs))

    def deal_timeline(self):
        """The M&A board's project with its whole timeline: a meeting with the buyer and its Zoom notes, an Intercom
        conversation, and the same deal in Olive's second company's calendar and on her personal calendar."""
        board = self.deal_board()
        self.own.config['accounts'] += [{'id': 'cal', 'email': 'own1@acme.example', 'service': 'calendar', 'boundary': 'work:Acme'},
                                        {'id': 'zoom', 'service': 'zoom', 'boundary': 'work:Acme'},
                                        {'id': 'ic', 'service': 'intercom', 'boundary': 'work:Acme'},
                                        {'id': 'sidecal', 'email': 'olive@sidecorp.example', 'service': 'calendar', 'boundary': 'work:Sidecorp'},
                                        {'id': 'homecal', 'email': 'own1@home.example', 'service': 'calendar', 'boundary': 'personal:Home'}]
        meeting = lambda acct, n, title, body, who: {'account_id': acct, 'source_id': f'{acct}-{n}', 'kind': 'calendar.event', 'occurred_at': ago(1), 'title': title,
                                                     'body': body, 'deleted': False, 'metadata': {'organizer': {'email': who}, 'attendees': [
                                                         {'email': who}, {'email': 'bo@buyer.example'}], 'end_at': ago(1)}}
        self.add(self.own,
                 meeting('cal', 1, 'Due diligence call', 'Agenda: walk through the data room.', 'own1@acme.example'),
                 {'account_id': 'zoom', 'source_id': 'zoom-1', 'kind': 'zoom.summary', 'occurred_at': ago(1), 'title': 'Due diligence call',
                  'body': 'Bo wants the closing in June.', 'deleted': False, 'metadata': {'host_email': 'own1@acme.example', 'meeting_id': '9'}},
                 {'account_id': 'ic', 'source_id': 'ic-1', 'kind': 'intercom.conversation', 'occurred_at': ago(1), 'title': 'Buyer question',
                  'body': 'Can our team test the API before closing?', 'deleted': False,
                  'metadata': {'conversation_id': 'c1', 'from': 'Bo Hale <bo@buyer.example>'}},
                 meeting('sidecal', 1, 'Sidecorp board', 'Sidecorp board prep for the sale.', 'olive@sidecorp.example'),
                 meeting('homecal', 1, 'Dinner with Bo', 'Dinner at home, kids too.', 'own1@home.example'))
        with self.own.store.connect() as db:
            graph.label(db, [self.event_id(self.own, x) for x in ('cal-1', 'zoom-1', 'ic-1', 'sidecal-1', 'homecal-1')], 'Sale: Buyer', 'review', a.now())
            graph.set_stage(db, 'Sale: Buyer', 'Signed', 'review', a.now(), reason='Bo signed the letter of intent', event_id=self.event_id(self.own, 'work-20'))
            graph.set_stage(db, 'Sale: Buyer', 'Talks', 'review', a.now(), reason='Sidecorp board wants to wait', event_id=self.event_id(self.own, 'sidecal-1'))
            db.isolation_level = None
            graph.build(db, self.own.config, a.now())
        return board

    def test_a_board_shared_by_name_takes_its_full_timeline_meetings_notes_and_intercom_only_to_them(self):
        board = self.deal_timeline()
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.rounds()
        received = self.received(self.mem)
        for text in ('walk through the data room', 'closing in June', 'test the API before closing'):
            self.assertIn(text, received)  # a meeting, its Zoom notes and an Intercom conversation: by being on the board
            self.assertNotIn(text, self.received(self.adm) + json.dumps(self.team(self.adm)))  # Ada isn't on it
        kinds = {json.loads(r['body'])['subject']: json.loads(r['body']).get('kind') for r in self.team(self.mem, 'team_mail')}
        self.assertEqual(kinds['Due diligence call'], 'meetings')
        self.assertEqual(kinds['Buyer question'], 'intercom')
        story = json.dumps(self.projects_of(self.mem)['Sale: Buyer']['timeline'])
        self.assertIn('Moved to Signed: Bo signed the letter of intent', story)
        self.assertIn('Meeting: Due diligence call', story)
        self.assertNotIn('Sidecorp board wants to wait', story)  # a move that came from another company's calendar stays
        blobs = self.only_blobs()
        self.assertEqual({tuple(b['header']['to']) for b in blobs}, {('mem3',)})  # sealed to Mo alone
        self.assertNotIn('closing in June', json.dumps(self.net.calls) + json.dumps(self.net.blobs))
        self.assertEqual(self.own.graph_unjudged(), [])  # nothing of it waits to be sorted

    def test_another_company_or_a_personal_item_on_a_shared_board_never_goes(self):
        board = self.deal_timeline()
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}, {'fleet': FLEET, 'member': 'adm2'}])
        self.rounds()
        self.assertIn('walk through the data room', self.received(self.adm))
        for box in (self.mem, self.adm):
            everything = self.received(box) + json.dumps(self.team(box))
            for text in ('Sidecorp board prep', 'Sidecorp board', 'Dinner at home', 'Dinner with Bo', 'Sidecorp pilot', 'Family weekend', 'Invoice 88'):
                self.assertNotIn(text, everything)
        subjects = [w['subject'] for w in self.own.graph_unjudged()]
        self.assertNotIn('Sidecorp board', subjects)
        self.assertNotIn('Dinner with Bo', subjects)

    def test_meetings_of_a_team_board_are_sorted_first_and_go_only_when_client(self):
        self.olive_mail()
        self.own.config['accounts'].append({'id': 'cal', 'email': 'own1@acme.example', 'service': 'calendar', 'boundary': 'work:Acme'})
        meet = lambda n, title, body: {'account_id': 'cal', 'source_id': f'cal-{n}', 'kind': 'calendar.event', 'occurred_at': ago(1), 'title': title, 'body': body,
                                       'deleted': False, 'metadata': {'organizer': {'email': 'own1@acme.example'}, 'attendees': [{'email': 'lee@client.example'}]}}
        self.add(self.own, meet(1, 'Renewal call', 'Walk Lee through the new plan.'), meet(2, 'Lee check-in', 'Lee asked about a raise for his team.'),
                 meet(3, 'Unrelated call', 'Not on any board.'))
        self.board(self.own, 'Renewals', 'team', {'Renewal: Client': ['work-1', 'cal-1', 'cal-2']})
        waiting = [w['subject'] for w in self.own.graph_unjudged()]
        self.assertEqual(sorted(waiting[:2]), ['Lee check-in', 'Renewal call'])  # the board's meetings first
        self.assertNotIn('Unrelated call', waiting)  # a meeting on no board isn't sorted or shared, as before
        self.rounds()
        self.assertNotIn('Walk Lee through', self.received(self.mem))  # not sorted yet: stays
        self.judge(self.own, ('cal-1', 'client', 'Renewal call with Lee'), ('cal-2', 'sensitive', 'Lee and a raise'))
        self.rounds()
        self.assertIn('Walk Lee through', self.received(self.mem))
        self.assertIn('Walk Lee through', self.received(self.adm))
        self.assertNotIn('a raise for his team', self.received(self.mem) + json.dumps(self.team(self.mem)))

    def test_a_project_added_to_a_shared_board_goes_and_one_moved_off_is_taken_back(self):
        board = self.deal_board()
        self.add(self.own, mail('work', 30, 'Cy Ross <cy@fund.example>', 'own1@acme.example', 'Term sheet', 'Our term sheet for the round.', 1, 't-fund'))
        self.judge(self.own, ('work-30', 'sensitive', 'Cy sent a term sheet'))
        with self.own.store.connect() as db:
            graph.label(db, [self.event_id(self.own, 'work-30')], 'Raise: Fund', 'review', a.now())
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.rounds()
        self.assertNotIn('term sheet for the round', self.received(self.mem))
        edit = {'board_id': board, 'name': 'Acquisition conversations', 'stages': ['Talks', 'Signed'], 'about': 'M&A', 'visibility': 'people'}
        with self.own.store.connect() as db:
            graph.save_board(db, {**edit, 'projects': ['Sale: Buyer', 'Raise: Fund']}, a.now())
        self.rounds()
        self.assertIn('term sheet for the round', self.received(self.mem))
        self.assertNotIn('term sheet for the round', self.received(self.adm))
        with self.own.store.connect() as db:
            graph.save_board(db, {**edit, 'projects': ['Sale: Buyer']}, a.now())
            self.assertEqual(db.execute('SELECT visibility FROM boards WHERE id=?', (board,)).fetchone()[0], 'people')
        self.rounds()
        self.assertNotIn('term sheet for the round', self.received(self.mem))  # moved off: taken back
        self.assertIn(DEAL, self.received(self.mem))

    def test_what_is_shared_by_name_is_never_passed_on_to_anyone_else(self):
        board = self.deal_board()
        self.own.graph_board_share(board, [{'fleet': FLEET, 'member': 'mem3'}])
        self.rounds()
        self.assertIn(DEAL, self.received(self.mem))
        # A Machine that passes it on anyway (an old version): the teammate's Machine refuses it
        with self.own.store.connect() as db:
            row = dict(db.execute("SELECT * FROM team_blobs WHERE kind='mail' AND header LIKE '%\"only\": true%'").fetchone())
        header = json.loads(row['header'])
        keys = self.own._public(next(g for g in self.own.directory()['fleets'] if g['id'] == FLEET), 'adm2')
        wrap = nc.seal({'dk': row['dk'], 'h': row['sha'], 'from_key': nc.public_keys(self.own._private())['sign']}, self.own._private(), keys,
                       sg.wrap_context(header, 'own1', 'adm2'))
        with self.assertRaisesRegex(ValueError, 'by name'):
            self.adm.graph_take({'header': header, 'ct': row['ct'], 'sig': row['sig'], 'wrap': wrap, 'by': 'own1'})
        # Someone new catches up on the team's Graph, never on what was shared with Mo
        self.net.people['mem4'] = ('Noor', 'member')
        noor = self.machine('mem4'); self.boxes.append(noor)
        self.rounds()
        self.ok(self.adm, 'member', 'Noor')
        self.rounds(4)
        self.assertIn(BODY, self.received(noor))
        self.assertNotIn(DEAL, self.received(noor))
        self.assertFalse([c for c in self.relays('mem4') if c[3]['blob']['header'].get('only')])
        # Mo away longer than the Network keeps: Olive's Machine passes on his own chain
        self.away(self.mem, 40)
        with self.mem.store.connect() as db:
            for table in ('team_mail', 'team_records', 'team_blobs', 'team_sources'):
                db.execute(sg.FLEET_DELETE[table], (FLEET,))
        self.mem.set_meta('graph_confirmed:' + FLEET, None)
        self.rounds(4)
        self.assertIn(DEAL, self.received(self.mem))
        self.assertIn('Sale: Buyer', self.projects_of(self.mem))

    def test_a_thread_shared_by_hand_with_chosen_teammates_never_a_personal_one(self):
        self.olive_mail()
        self.add(self.own, mail('work', 23, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Our kids', 'Family weekend plans.', 2, 't-kids'))
        self.judge(self.own, ('work-23', 'personal', 'Family'))
        self.rounds()
        self.assertNotIn(HEALTH, self.received(self.adm))
        out = self.own.graph_thread_share(self.event_id(self.own, 'work-3'), [{'fleet': FLEET, 'member': 'adm2'}])
        self.assertEqual(out['shared_with'], ['Ada'])
        self.assertEqual(out['label']['people'], ['Ada'])
        self.rounds()
        self.assertIn(HEALTH, self.received(self.adm))  # sensitive, shared by hand with Ada alone
        self.assertNotIn(HEALTH, self.received(self.mem))
        with self.assertRaisesRegex(ValueError, 'Personal'):
            self.own.graph_thread_share(self.event_id(self.own, 'work-23'), [{'fleet': FLEET, 'member': 'adm2'}])
        with self.assertRaises(ValueError):
            self.own.graph_thread_share(self.event_id(self.own, 'home-5'), [{'fleet': FLEET, 'member': 'adm2'}])
        self.own.graph_thread_share(self.event_id(self.own, 'work-3'), [])
        self.rounds()
        self.assertNotIn(HEALTH, self.received(self.adm))

    def test_only_the_share_sheet_shares_a_board_by_name(self):
        board = self.deal_board()
        with self.own.store.connect() as db:
            with self.assertRaises(ValueError):
                graph.clean_visibility('people')
            with self.assertRaises(ValueError):
                graph.save_board(db, {'board_id': board, 'name': 'Acquisition conversations', 'stages': ['Talks'], 'about': 'M&A', 'visibility': 'people'}, a.now())


class StaticSql(unittest.TestCase):
    def test_the_shared_graph_builds_no_sql_from_strings(self):
        """Every query is a fixed string: lists go in as one JSON parameter (json_each), tables by a fixed statement each."""
        import ast
        for name in ('shared_graph.py',):
            tree = ast.parse(Path(__file__).with_name(name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') in ('execute', 'executemany') and node.args:
                    self.assertNotIsInstance(node.args[0], ast.JoinedStr, f'{name}:{node.lineno} builds SQL with an f-string')

    def test_a_list_parameter_matches_like_the_placeholders_did(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE event_projects(event_id, project_id)'); db.execute('CREATE TABLE graph_events(event_id, node_id, probable)')
        db.executemany('INSERT INTO event_projects VALUES (?,?)', [(1, 7), (2, 8), (3, 9)])
        db.executemany('INSERT INTO graph_events VALUES (?,?,?)', [(4, 'project:7', 0), (5, 'project:8', 1), (6, "project:7') OR 1=1 --", 0)])
        self.assertEqual(graph.project_event_ids(db, {7, 8}), {1, 2, 4})
        self.assertEqual(graph.project_event_ids(db, set()), set())
        self.assertEqual(set(sg.FLEET_DELETE), {'team_records', 'team_mail', 'team_files', 'team_asks', 'team_sources', 'team_blobs', 'team_wants', 'share_out'})

if __name__ == '__main__':
    unittest.main()
