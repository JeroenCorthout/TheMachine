"""The Shared Graph (shared_graph.py, docs/shared-graph.md): a Work Fleet's Machines share their Work records, timelines
and client threads, sealed Machine to Machine. A fake Network stands in for the control plane, honest or not: it keeps
the sealed blobs until every Machine they were sealed for took them, and can't read, change, replay or forge them. No network, no model."""
import copy
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
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
                 'members': members + copy.deepcopy(self.extra_members), 'shared': []}
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
                self.net.blobs[header['id']] = {k: blob[k] for k in ('header', 'ct', 'sig')}
                for to, wrap in blob['wraps'].items():
                    if to in self.net.people:  # only members of the Fleet
                        self.net.wraps.append((header['id'], to, wrap, self.me))
            elif body['op'] == 'relay':  # passed on as its publisher signed it, a key sealed again for who missed it
                blob = body['blob']
                header = blob['header']
                assert header['kind'] in ('snapshot', 'delta', 'mail', 'file')
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

    def test_a_personal_thread_in_a_work_account_stays_out_until_the_owner_says_team(self):
        self.add(self.own, mail('work', 6, 'Lee Park <lee@client.example>', 'own1@acme.example', 'Your daughter', 'Congrats on the graduation!', 1, 't-p'))
        self.judge(self.own, ('work-6', 'personal', 'Lee wrote'))
        self.rounds()
        self.assertNotIn('graduation', json.dumps(self.team(self.mem, 'team_mail')))
        self.assertEqual(self.team(self.mem, 'team_mail'), [])

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

    def test_sensitive_client_thread_shows_only_that_it_exists(self):
        self.olive_mail()
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

    def test_sharing_starts_as_soon_as_the_machine_is_confirmed(self):
        self.olive_mail()
        self.rounds(1)
        self.assertTrue(self.team(self.mem))
        self.assertIsNone(self.own.meta('graph_start:' + FLEET))
        self.assertGreaterEqual(self.own.sharing()[0]['teammates'], 1)  # the Team page says Work is shared only now

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
        self.assertRegex(lines[0], r'^• Shared with Acme: 1 thread, 1 person, \d+ projects?\. 1 held back\. \[Review\]\(https://box\.example/#graph=held\)$')


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

if __name__ == '__main__':
    unittest.main()
