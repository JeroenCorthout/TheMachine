import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('graph_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
graph = a.graph
enrichment = a.enrichment
NOW = dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)
CONFIG = {'accounts': [{'id': 'work', 'email': 'sam@acme.com'}, {'id': 'chat', 'service': 'whatsapp', 'email': 'Personal WhatsApp'},
                       {'id': 'slack', 'service': 'slack', 'email': 'sam@acme.com', 'user_id': 'U0OWNER'}]}
counter = iter(range(1, 10**6))


def at(days_ago, hour=0):
    return (NOW - dt.timedelta(days=days_ago, hours=hour)).isoformat()


def mail(days_ago, sender, to, labels=('INBOX',), cc='', thread=None, hour=0, title=None, **extra):
    n = next(counter)
    return {'account_id': 'work', 'source_id': f'm{n}', 'kind': 'gmail.message', 'occurred_at': at(days_ago, hour), 'title': title or f'Mail {n}', 'body': 'Hello',
            'deleted': False, 'metadata': {'from': sender, 'to': to, 'cc': cc, 'labels': list(labels), 'message-id': f'<{n}@x>',
                                           'thread_id': thread or f't{n}', **extra}}


def chat(days_ago, phone, name, mine=False, hour=0):
    n = next(counter)
    return {'account_id': 'chat', 'source_id': f'w{n}', 'kind': 'whatsapp.message', 'occurred_at': at(days_ago, hour), 'title': name, 'body': 'Hi',
            'deleted': False, 'metadata': {'chat_id': phone + '@s.whatsapp.net', 'chat_name': name, 'from_me': mine, 'from': 'You' if mine else name,
                                           'sender_id': None if mine else phone + '@s.whatsapp.net'}}


class Graph(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store; self.app.config = CONFIG
        self.now = a.now
        a.now = lambda: NOW.isoformat(); self.addCleanup(setattr, a, 'now', self.now)

    def add(self, *events):
        for e in events:
            self.store.commit_page(e['account_id'], {}, [e])

    def build(self):
        with self.store.connect() as db:
            db.isolation_level = None
            return graph.build(db, {**CONFIG, 'embeddings': {'model': 'test', 'dims': getattr(self, 'vector_dims', 4)}}, NOW.isoformat())

    def node(self, node_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM graph_nodes WHERE id=?', (node_id,)).fetchone()
            return graph.node_view(row) if row else None

    def ids(self, source_ids):
        with self.store.connect() as db:
            return [db.execute('SELECT id FROM events WHERE source_id=?', (s,)).fetchone()[0] for s in source_ids]

    def test_names_are_only_names(self):
        cases = {'Kim Lee (Initech)': 'Kim Lee', 'Ann Bell | Acme': 'Ann Bell', '(second) Kim Lee': 'Kim Lee',
                 'Ann Bell - Bell': 'Ann Bell', 'Lee at Example': 'Lee', 'Doe, Jane': 'Jane Doe',
                 'Doe EXT, Sam': 'Sam Doe', 'Jane Doe, PhD': 'Jane Doe', 'Dr. Ann Lee, DBA, MPA': 'Dr. Ann Lee', 'Pat 🚀 Lee': 'Pat Lee', 'Sam (@sam)': 'Sam',
                 'Anna von Berg via LinkedIn': 'Anna von Berg', 'Kim Lee-Bell': 'Kim Lee-Bell',
                 "Mary O'Neil": "Mary O'Neil", 'Dr. Ann Bell': 'Dr. Ann Bell', '(Initech)': '(Initech)'}
        self.assertEqual({k: graph.person_name(k) for k in cases}, cases)
        self.add(mail(3, 'Kim Lee (Initech) <kim@initech.example>', 'sam@acme.com'), mail(2, 'Sam <sam@acme.com>', 'kim@initech.example', ('SENT',)))
        self.build()
        self.assertEqual(self.node('email:kim@initech.example')['name'], 'Kim Lee')

    def test_owner_bulk_and_aliases_never_become_people(self):
        self.add(mail(1, 'Sam <sam@acme.com>', 'Ann <ann@client.com>', ('SENT',)),
                 mail(1, 'Sam Owner <sam@home.org>', 'ann@client.com', ('SENT',)),  # an alias the owner sends from
                 mail(2, 'News <newsletter@shop.com>', 'sam@acme.com', ('CATEGORY_PROMOTIONS', 'INBOX')),
                 mail(2, 'Robot <noreply@service.com>', 'sam@acme.com'),
                 mail(2, 'Acme info <info@acme.com>', 'sam@acme.com'),  # a shared role address at the owner's own domain
                 mail(3, 'Ann <ann@client.com>', 'Sam <sam@acme.com>, Sam Owner <sam@home.org>'),
                 chat(1, '00000000001', 'Sam Owner'),  # the owner's own name on another channel
                 {'account_id': 'work', 'source_id': 'crm1', 'kind': 'salesflare.email', 'occurred_at': at(2), 'title': 'Your workspace',
                  'body': 'Hi', 'deleted': False, 'metadata': {'contacts': [{'email': 'no-reply@email.slackhq.com', 'name': 'Slack'}]}})
        self.build()
        with self.store.connect() as db:
            people = {r['id'] for r in db.execute("SELECT id FROM graph_nodes WHERE kind='person'")}
        self.assertEqual(people, {'email:ann@client.com'})
        self.assertEqual(self.node('org:client.com')['name'], 'client.com')

    def test_owner_messages_weigh_more_a_burst_is_one_day_and_it_fades(self):
        self.add(*[chat(1, '00000000001', 'Bea Burst', hour=h) for h in range(10)],
                 chat(1, '00000000002', 'Cas Owner', mine=True),
                 chat(120, '00000000003', 'Dee Old'), chat(120, '00000000003', 'Dee Old', mine=True))
        self.build()
        bea, cas, dee = (self.node('wa:0000000000' + n)['strength'] for n in '123')
        self.assertAlmostEqual(bea, 1.0 * 0.5 ** (1 / 60), places=2)  # ten messages, one day, capped
        self.assertGreater(cas, 0.55)
        self.assertLess(dee, 0.25)  # two months of fading, twice over

    def test_same_full_name_joins_channels_unless_said_to_differ(self):
        self.add(mail(1, 'Ann Example <ann@client.com>', 'sam@acme.com'), chat(2, '00000000001', 'Ann Example'),
                 chat(2, '00000000009', 'Bo Ambiguous'), mail(2, 'Bo Ambiguous <bo@one.com>', 'sam@acme.com'), mail(2, 'Bo Ambiguous <bo@two.com>', 'sam@acme.com'))
        self.build()
        self.assertEqual(self.node('email:ann@client.com')['ids'], ['+00000000001', 'ann@client.com'])
        self.assertIsNone(self.node('wa:00000000001'))
        self.assertIsNotNone(self.node('wa:00000000009'))  # two addresses carry that name: not joined
        with self.store.connect() as db:
            graph.merge(db, 'wa:00000000001', 'email:ann@client.com', False, NOW.isoformat())
        self.build()
        self.assertIsNotNone(self.node('wa:00000000001'))
        with self.store.connect() as db:
            graph.merge(db, 'wa:00000000009', 'email:bo@one.com', True, NOW.isoformat())
        self.build()
        self.assertIn('+00000000009', self.node('email:bo@one.com')['ids'])

    def test_slack_email_joins_the_mail_identity(self):
        n = next(counter)
        self.add({'account_id': 'slack', 'source_id': f's{n}', 'kind': 'slack.message', 'occurred_at': at(1), 'title': '#DM U0ANN: hi', 'body': 'hi',
                  'deleted': False, 'metadata': {'conversation_type': 'dm', 'channel_name': 'DM U0ANN', 'user_id': 'U0ANN', 'channel_id': 'D1'}},
                 mail(2, 'Ann <ann@client.com>', 'sam@acme.com'))
        self.store.remember_name('slack:U0ANN', 'Ann Example', 'ann@client.com')
        self.assertEqual(self.store.names_to_check(['slack:U0ANN', 'slack:U0NEW']), ['slack:U0NEW'])
        self.build()
        ann = self.node('email:ann@client.com')
        self.assertEqual((ann['name'], ann['ids']), ('Ann Example', ['Slack', 'ann@client.com']))

    def test_slack_names_are_looked_up_a_few_at_a_time_once_a_month(self):
        calls = []
        slack = object.__new__(a.Slack); slack.account = {'user_id': 'U0OWNER'}
        slack.get = lambda method, params: calls.append(params['user']) or {'user': {'profile': {'real_name': 'Name ' + params['user'], 'email': params['user'].lower() + '@acme.com'}}}
        channels = [{'name': 'DM U0A'}, {'name': 'DM U0B'}, {'name': 'general'}, {'name': 'My notes'}]
        events = [{'metadata': {'user_id': 'U0C', 'channel_name': 'general'}}, {'metadata': {'user_id': 'U0OWNER', 'channel_name': 'general'}}]
        a.remember_slack_names(self.store, slack, events, channels, per_page=2)
        a.remember_slack_names(self.store, slack, events, channels, per_page=2)
        a.remember_slack_names(self.store, slack, events, channels, per_page=2)
        self.assertEqual(calls, ['U0A', 'U0B', 'U0C'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT name,email FROM identity_names WHERE identifier='slack:U0A'").fetchone()[:], ('Name U0A', 'u0a@acme.com'))

    def test_projects_from_review_and_cards_spread_to_threads_and_people(self):
        self.add(mail(3, 'Ann <ann@client.com>', 'sam@acme.com', thread='deal'), mail(2, 'Sam <sam@acme.com>', 'ann@client.com, Bob <bob@client.com>', ('SENT',), thread='deal'),
                 mail(1, 'Zed <zed@else.com>', 'sam@acme.com'))
        first, _, other = self.thread_ids()
        claim = self.store.begin_analysis(['work'], 10)
        with self.assertRaises(ValueError):
            self.store.finish_analysis(claim['analysis_id'], 'x', projects={'Acme deal': [999999]})
        self.store.finish_analysis(claim['analysis_id'], 'Reviewed', projects={'Acme deal': [first]})
        self.build()
        with self.store.connect() as db:
            project = db.execute("SELECT * FROM graph_nodes WHERE kind='project'").fetchone()
            self.assertEqual((project['name'], project['signals']), ('Acme deal', 2))  # the reply in the same thread joined it
            people = {n['id'] for n in graph.neighbours(db, project['id'])}
            self.assertEqual(people, {'email:ann@client.com', 'email:bob@client.com'})
            self.assertEqual(graph.active_projects(db), ['Acme deal'])
        card = self.store.suggest('zed-1', 'Answer Zed', 'Zed asked a question.', [other], project='acme  DEAL')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT project FROM suggestions WHERE id=?', (card['id'],)).fetchone()[0], 'Acme deal')
        self.build()
        with self.store.connect() as db:
            ids = {n['id'] for n in graph.neighbours(db, project['id'])}
        self.assertIn('email:zed@else.com', ids)

    def thread_ids(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT id,json_extract(metadata,'$.thread_id') t FROM events ORDER BY id").fetchall()
        deal = [r['id'] for r in rows if r['t'] == 'deal']
        return deal[0], deal[1], next(r['id'] for r in rows if r['t'] != 'deal')

    def test_card_decisions_strengthen_and_not_relevant_weakens(self):
        self.add(mail(5, 'Ann <ann@client.com>', 'sam@acme.com'), mail(5, 'Bob <bob@client.com>', 'sam@acme.com'), mail(5, 'Cy <cy@client.com>', 'sam@acme.com'))
        ann, bob, cy = self.ids(self.sources())
        self.build()
        base = self.node('email:ann@client.com')['strength']
        for key, event_id, choice, feedback in (('a', ann, 'accept', ''), ('a2', ann, 'complete', ''), ('b', bob, 'reject', ''), ('c', cy, 'reject', 'Already handled')):
            card = self.store.suggest(key, 'T', 'B', [event_id]) if key != 'a2' else card
            with self.store.connect() as db:
                db.execute('INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,?,?,?)', (key, card['id'], choice, feedback, 'dashboard', at(1)))
        self.build()
        self.assertAlmostEqual(self.node('email:ann@client.com')['strength'] - base, 0.5 ** (1 / 60), places=2)  # one boost per card
        self.assertIsNone(self.node('email:bob@client.com'))  # faded out entirely
        self.assertAlmostEqual(self.node('email:cy@client.com')['strength'], base, places=3)

    def sources(self):
        with self.store.connect() as db:
            return [r[0] for r in db.execute('SELECT source_id FROM events ORDER BY id')]

    def test_slack_root_and_reply_share_project_not_neighbour_or_other_account(self):
        channel = {'id': 'C1', 'name': 'projects', 'type': 'channel'}
        account = {'id': 'slack', 'slack_host': 'example.invalid'}
        stamp = str(int((NOW - dt.timedelta(days=1)).timestamp())) + '.000001'
        root = a.slack_event(account, channel, {'ts': stamp, 'text': 'Project root', 'user': 'U1', 'reply_count': 1})
        reply = a.slack_event(account, channel, {'ts': str(float(stamp) + 60), 'thread_ts': stamp, 'text': 'Project reply', 'user': 'U2'})
        other = a.slack_event(account, channel, {'ts': str(float(stamp) + 120), 'text': 'Unrelated root', 'user': 'U3'})
        foreign = {**root, 'account_id': 'other-slack'}
        self.add(root, reply, other, foreign)
        with self.store.connect() as db:
            root_id, reply_id, other_id, foreign_id = [r[0] for r in db.execute('SELECT id FROM events ORDER BY id')]
            pid = graph.label(db, [root_id], 'Exact thread', 'agent', NOW.isoformat())
        self.build()
        with self.store.connect() as db:
            members = {r[0] for r in db.execute('SELECT event_id FROM graph_events WHERE node_id=?', ('project:' + str(pid),))}
        self.assertEqual(members, {root_id, reply_id})
        with self.store.connect() as db:
            db.execute('DELETE FROM event_projects')
            graph.label(db, [reply_id], 'Exact thread', 'agent', NOW.isoformat())
        self.build()
        with self.store.connect() as db:
            members = {r[0] for r in db.execute('SELECT event_id FROM graph_events WHERE node_id=?', ('project:' + str(pid),))}
        self.assertEqual(members, {root_id, reply_id})

    def test_old_task_membership_is_complete_but_display_is_bounded(self):
        old = mail(3, 'Ann Example <ann@client.com>', 'sam@acme.com')
        self.add(old, *[mail(1, 'Ann Example <ann@client.com>', 'sam@acme.com', hour=i / 100) for i in range(45)])
        first = self.ids([old['source_id']])[0]
        card = self.store.suggest('older', 'Answer Ann', 'Question', [first])
        self.build()
        with self.store.connect() as db:
            self.assertTrue(graph.for_events(db, [first]))
            self.assertEqual(len(graph.detail(db, 'email:ann@client.com', graph.EVENTS_PER_NODE)['event_ids']), 40)
        self.assertIn('Ann Example', self.app.graph_lines(card['id']))

    def test_annotated_decisions_do_not_teach_graph(self):
        self.add(mail(3, 'Ann Example <ann@client.com>', 'sam@acme.com'))
        first = self.ids(self.sources())[0]
        self.build()
        baseline = self.node('email:ann@client.com')['strength']
        card = self.store.suggest('annotation', 'Answer Ann', 'Question', [first])
        with self.store.connect() as db:
            db.execute('INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,?,?,?)', ('bad', card['id'], 'accept', '', 'telegram', at(1)))
            db.execute('INSERT INTO decision_annotations(decision_id,status,reason) VALUES(?,?,?)', ('bad', 'misassociated', 'Wrong prompt'))
        self.build()
        self.assertEqual(self.node('email:ann@client.com')['strength'], baseline)

    def test_quiet_project_actually_cools(self):
        self.add(*[mail(15, 'Ann Example <ann@client.com>', 'sam@acme.com', hour=i / 100) for i in range(10)])
        with self.store.connect() as db:
            pid = graph.label(db, self.ids(self.sources()), 'Quiet project', 'agent', NOW.isoformat())
        self.build()
        self.assertEqual(self.node('project:' + str(pid))['state'], 'cooling')
        for ratio, expected in ((1.0, 'active'), (1.5, 'heating up')):
            self.assertEqual(graph.project_state({'strength': 1.0, 'trend': ratio}), expected)

    def test_going_quiet_is_measured_against_their_own_rhythm(self):
        weekly = [chat(d, '00000000001', 'Wes Weekly', mine=d % 2 == 0) for d in range(30, 120, 7)]
        self.add(*weekly, chat(30, '00000000001', 'Wes Weekly', mine=True),
                 *[chat(d, '00000000002', 'Rae Recent', mine=True) for d in (2, 9, 16, 23)])
        self.build()
        wes, rae = self.node('wa:00000000001'), self.node('wa:00000000002')
        self.assertEqual((wes['usual_gap_days'], wes['quiet']), (7, True))
        self.assertFalse(rae['quiet'])
        with self.store.connect() as db:
            self.assertEqual([p['id'] for p in graph.overview(db)['quiet']], ['wa:00000000001'])
        self.assertIn('• Gone quiet: Wes Weekly (usually every 7 days)', a.recap_graph_lines(self.store))

    def test_preparation_context_and_agent_lookup(self):
        self.add(mail(3, 'Ann Example <ann@client.com>', 'sam@acme.com', thread='deal'), mail(2, 'Sam <sam@acme.com>', 'Ann Example <ann@client.com>, Bob <bob@client.com>', ('SENT',), thread='deal'))
        first = self.ids(self.sources())[0]
        card = self.store.suggest('ann-1', 'Answer Ann', 'Ann asked about pricing.', [first], project='Acme deal')
        self.build()
        lines = self.app.graph_lines(card['id'])
        self.assertIn('People and projects', lines)
        self.assertIn('- Ann Example (ann@client.com): ', lines)
        self.assertIn('projects: Acme deal', lines)
        self.assertIn('often with Bob', lines)
        self.assertIn('- Project Acme deal: ', lines)
        found = self.app.graph_action({'query': 'ann example'})['found']
        self.assertEqual([f['id'] for f in found], ['email:ann@client.com'])
        detail = self.app.graph_action({'node_id': 'email:ann@client.com'})
        self.assertEqual(len(detail['recent']), 2)
        self.assertIn('email:bob@client.com', [n['id'] for n in detail['around']])
        with self.store.connect() as db:
            before = graph.overview(db)['counts']
        self.assertEqual((before['person'], before['project']), (2, 1))  # Ann and Bob (not the owner); the Acme deal
        self.app.graph_build = self.build
        self.app.graph_action({'close_project': 'acme deal'})
        with self.store.connect() as db:
            self.assertEqual(graph.overview(db)['projects'], [])
            self.assertEqual(graph.overview(db)['counts']['project'], 0)  # a closed project is not counted
        with self.assertRaises(ValueError):
            self.app.graph_action({'merge': ['email:ann@client.com']})

    def vectors(self, pairs):
        self.vector_dims = len(pairs[0][1])
        import struct
        with self.store.connect() as db:
            for source_id, values in pairs:
                size = sum(v * v for v in values) ** 0.5
                event_id, version = db.execute('SELECT id,version FROM events WHERE source_id=?', (source_id,)).fetchone()
                db.execute('INSERT OR REPLACE INTO event_vectors(event_id,version,model,dims,vector) VALUES(?,?,?,?,?)',
                           (event_id, version, 'test', len(values), struct.pack('<%df' % len(values), *[v / size for v in values])))

    def test_a_signal_that_reads_like_a_project_and_shares_a_person_is_probably_part_of_it(self):
        labelled = mail(5, 'Ann <ann@client.com>', 'sam@acme.com', title='Pricing')
        alike = mail(3, 'Ann <ann@client.com>', 'sam@acme.com')
        stranger = mail(3, 'Zed <zed@else.com>', 'sam@acme.com')
        other = mail(2, 'Ann <ann@client.com>', 'sam@acme.com')
        meeting = {'account_id': 'work', 'source_id': 'meet1', 'kind': 'calendar.event', 'occurred_at': at(2), 'title': 'Call', 'body': '', 'deleted': False,
                   'metadata': {'attendees': [{'email': 'sam@acme.com', 'self': True}, {'email': 'ann@client.com'}]}}
        self.add(labelled, alike, stranger, other, meeting)
        self.vectors([(labelled['source_id'], [1, 0.1, 0, 0]), (alike['source_id'], [1, 0.2, 0, 0]), (stranger['source_id'], [1, 0.1, 0, 0]),
                      (other['source_id'], [0, 0, 1, 0]), ('meet1', [1, 0.1, 0, 0])])
        first, guess, zed, unrelated, call = self.ids([labelled['source_id'], alike['source_id'], stranger['source_id'], other['source_id'], 'meet1'])
        with self.store.connect() as db:
            graph.label(db, [first], 'Acme deal', 'review', NOW.isoformat())
        self.build()
        with self.store.connect() as db:
            rows = dict(db.execute("SELECT event_id,probable FROM graph_events WHERE node_id LIKE 'project:%'").fetchall())
        self.assertEqual(rows, {first: 0, guess: 1})  # not the stranger, not a different topic, not a bare meeting
        self.assertEqual(self.store.project_hints([first, guess, zed]), {first: {'projects': ['Acme deal']}, guess: {'probably_project': 'Acme deal'}})
        detail = self.app.graph_action({'node_id': 'project:1'})
        self.assertEqual(detail['probable_signals'], 1)
        self.assertTrue(any(line.startswith(f'{guess} ') and line.endswith('(probably part of it)') for line in detail['recent']))
        claim = self.store.begin_analysis(['work'], 10)
        page = self.store.analysis_page(claim['analysis_id'], 1) if claim.get('pages', 1) > 1 else claim
        shown = {e['id']: e for e in page['events']}
        self.assertEqual(shown[guess].get('probably_project'), 'Acme deal')
        with self.store.connect() as db:  # a review confirms it
            graph.label(db, [guess], 'Acme deal', 'review', NOW.isoformat())
        self.build()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT probable FROM graph_events WHERE event_id=? AND node_id=?', (guess, 'project:1')).fetchone()[0], 0)

    def test_threads_that_read_alike_without_a_project_are_offered_once(self):
        threads = [mail(9 - n, 'Ann <ann@client.com>', 'sam@acme.com', thread=f'x{n}') for n in range(3)]
        threads.append(mail(5, 'Sam Stone <sam@acme.com>', 'Ann <ann@client.com>', ('SENT',), thread='x0'))
        chats = [chat(4 - n, '00000000001', 'Bo Chat', mine=n == 0) for n in range(3)]
        self.add(*threads, *chats)
        self.vectors([(e['source_id'], [1, 0.05 * i, 0, 0]) for i, e in enumerate(threads + chats)])
        self.build()
        first = self.store.unnamed_text('run1')
        self.assertIn('4 signals with Ann: ', first)
        self.assertIn('"Mail ', first)
        self.assertNotIn('Bo Chat', first)  # chats read alike per person, whatever the topic
        self.assertEqual(self.store.unnamed_text('run2'), '')
        self.assertIn('Possibly one piece of work nobody named', a.review_prompt('run', ['work'], unnamed=first))
        self.assertNotIn('Possibly one piece of work', a.review_prompt('run', ['work']))

    def test_the_owner_renames_people_organisations_and_projects(self):
        self.add(mail(3, 'Sam Stone <sam@acme.com>', 'Lee <lee@globex.com>', ('SENT',), thread='t1'),
                 mail(2, 'Lee <lee@globex.com>', 'sam@acme.com', thread='t1'), mail(1, 'Lee <lee@globex.com>', 'sam@acme.com', thread='t2'))
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM events ORDER BY id')]
            graph.label(db, ids[:2], 'Globex deal', 'agent', a.now())
            graph.label(db, ids[2:], 'Globex renewal', 'agent', a.now())
        self.build()
        self.app.graph_build = self.build
        rename = lambda node, name: self.app.action('graph_rename', {'node_id': node, 'name': name}, owner_dashboard=True)
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_rename', {'node_id': 'email:lee@globex.com', 'name': 'Leon'})
        self.assertEqual(rename('email:lee@globex.com', '  Lee   Parker ')['name'], 'Lee Parker')
        self.assertEqual(self.node('email:lee@globex.com')['name'], 'Lee Parker')
        rename('email:lee@globex.com', '')  # back to the name in their messages
        self.assertEqual(self.node('email:lee@globex.com')['name'], 'Lee')
        rename('org:globex.com', 'Globex Corp')
        self.assertEqual(self.node('org:globex.com')['name'], 'Globex Corp')
        with self.store.connect() as db:
            deal, renewal = (db.execute('SELECT id FROM projects WHERE name=?', (n,)).fetchone()[0] for n in ('Globex deal', 'Globex renewal'))
            db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,project,created_at) VALUES('s1','k1','Reply to Lee','','[]','[]','Globex deal',?)", (a.now(),))
        self.assertEqual(rename(f'project:{deal}', 'Globex expansion'), {'node_id': f'project:{deal}', 'name': 'Globex expansion'})
        self.assertEqual(self.node(f'project:{deal}')['name'], 'Globex expansion')
        joined = rename(f'project:{renewal}', 'globex EXPANSION')  # another project's name: they become one
        self.assertEqual(joined, {'node_id': f'project:{deal}', 'name': 'Globex expansion', 'merged': True})
        self.assertIsNone(self.node(f'project:{renewal}'))
        self.assertEqual(self.node(f'project:{deal}')['signals'], 3)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT project FROM suggestions WHERE id='s1'").fetchone()[0], 'Globex expansion')
        with self.assertRaisesRegex(ValueError, 'short label'):
            rename(f'project:{deal}', '')
        self.assertIn('graph_rename', a.OWNER_ACTIONS)

    def test_roles_only_as_said_by_the_owner_or_a_review(self):
        self.add(mail(3, 'Sam Stone <sam@acme.com>', 'Ann Stone <ann@home.example>, Pat <pat@client.com>, Lee <lee@globex.com>', ('SENT',)),
                 {'account_id': 'work', 'source_id': 'st1', 'kind': 'stripe.event', 'occurred_at': at(2), 'title': 'Payment failed', 'body': '', 'deleted': False,
                  'metadata': {'from': 'pat@client.com', 'customer_name': 'Client Ltd', 'type': 'invoice.payment_failed'}},
                 {'account_id': 'work', 'source_id': 'ic1', 'kind': 'intercom.conversation', 'occurred_at': at(2), 'title': 'Re: login', 'body': '', 'deleted': False,
                  'metadata': {'from': 'Kai <kai@acme.com>'}},
                 {'account_id': 'work', 'source_id': 'cr1', 'kind': 'salesflare.email', 'occurred_at': at(2), 'title': 'Hi', 'body': '', 'deleted': False,
                  'metadata': {'contacts': [{'email': 'lee@globex.com', 'name': 'Lee'}], 'crm_account': {'domain': 'globex.com', 'name': 'Globex'}}},
                 {'account_id': 'slack', 'source_id': 's1', 'kind': 'slack.message', 'occurred_at': at(1), 'title': 'DM', 'body': 'hey', 'deleted': False,
                  'metadata': {'channel_id': 'D1', 'channel_name': 'DM U0TEAM1', 'conversation_type': 'dm', 'user_id': 'U0TEAM1'}})
        self.build()
        role = lambda node: self.node(node)['role']
        for node in ('slack:U0TEAM1', 'email:pat@client.com', 'email:ann@home.example', 'email:lee@globex.com'):
            self.assertIsNone(role(node))  # a source doesn't say what someone is to the owner
        hints = lambda node: self.node(node)['hints']
        self.assertEqual(hints('email:pat@client.com'), ['billed through Stripe as Client Ltd'])
        self.assertEqual(hints('email:lee@globex.com'), ['a Salesflare contact at Globex'])
        self.assertEqual(hints('email:ann@home.example'), ["shares the owner's surname"])
        self.assertEqual(hints('slack:U0TEAM1'), ["in the owner's Slack"])
        self.assertEqual(hints('email:kai@acme.com'), ["an address at the owner's own domain"])  # a teammate answering support
        # Last contact is contact with the owner: Pat's later Stripe event and Kai's support answer don't count.
        tz = graph.owner_tz({})
        self.assertEqual(self.node('email:pat@client.com')['last_at'], dt.datetime.fromisoformat(at(3)).astimezone(tz).date().isoformat())
        self.assertFalse(self.node('email:pat@client.com')['seen_only'])
        self.assertTrue(self.node('email:kai@acme.com')['seen_only'])
        # A review is asked about the strongest people without a role, each once a month, and reads their messages.
        with self.store.connect() as db:
            db.execute("UPDATE graph_nodes SET strength=1 WHERE kind='person'")
        asked = self.store.role_text('run1', limit=10)
        self.assertIn('node_id email:pat@client.com', asked)
        self.assertIn('hints: billed through Stripe as Client Ltd', asked)
        self.assertEqual(self.store.role_text('run2', limit=10), '')
        prompt = a.review_prompt('run', ['work'], roles=asked)
        self.assertIn('Hints say where someone appears, not what they are', prompt)
        self.assertNotIn('Who they are to', a.review_prompt('run', ['work']))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT name FROM graph_nodes WHERE id='org:client.com'").fetchone()[0], 'Client Ltd')
        self.app.graph_build = self.build
        self.app.graph_action({'node_id': 'email:pat@client.com', 'role': 'CFO at Client Ltd'})
        self.assertEqual(role('email:pat@client.com'), 'CFO at Client Ltd')
        self.assertEqual(self.node('email:pat@client.com')['role_by'], 'review')
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_role', {'node_id': 'email:pat@client.com', 'role': 'friend'})
        self.app.action('graph_role', {'node_id': 'email:ann@home.example', 'role': 'sister'}, owner_dashboard=True)
        self.app.graph_action({'node_id': 'email:ann@home.example', 'role': 'cousin'})  # a review can't overrule the owner
        self.assertEqual(role('email:ann@home.example'), 'sister')
        self.assertEqual(self.node('email:ann@home.example')['role_by'], 'owner')
        self.app.action('graph_role', {'node_id': 'email:ann@home.example', 'role': ''}, owner_dashboard=True)
        self.assertIsNone(role('email:ann@home.example'))
        self.assertIn('graph_role', a.OWNER_ACTIONS)
        with self.store.connect() as db:
            lines = graph.context_lines(db, self.ids(['st1']))
        self.assertIn('- Pat (pat@client.com): CFO at Client Ltd; ', lines)

    def test_a_warm_up_tools_traffic_leaves_the_graph_and_the_owner_can_undo_it(self):
        self.add(mail(3, 'Rotating Name <ana@warmup-pool.example>', 'sam@acme.com', title='Your thoughts on our update, Sam'),
                 mail(2, 'Sam <sam@acme.com>', 'ana@warmup-pool.example', ('SENT',), title='Re: Your thoughts on our update, Sam'),
                 mail(2, 'Bo Other <bo@warmup-pool.example>', 'sam@acme.com', title='Seeking your input, Sam'),
                 mail(1, 'Pat Doe <pat@client.com>', 'sam@acme.com'), mail(1, 'Sam <sam@acme.com>', 'pat@client.com', ('SENT',)))
        self.build()
        with self.store.connect() as db:
            db.execute("UPDATE graph_nodes SET strength=1 WHERE kind='org'")
        with self.store.connect() as db:
            warm_id = db.execute("SELECT g.event_id FROM graph_events g WHERE g.node_id='email:ana@warmup-pool.example' LIMIT 1").fetchone()[0]
        # A roles turn sees the organisation's own messages, to judge what the exchanges are.
        _, orgs = self.store.role_evidence()
        warm = next(o for o in orgs if o['node_id'] == 'org:warmup-pool.example')
        self.assertTrue(warm['messages'])
        with self.assertRaisesRegex(ValueError, 'organisation node_id'):
            self.app.graph_action({'node_id': 'email:ana@warmup-pool.example', 'warmup': True})
        self.app.graph_action({'node_id': 'org:warmup-pool.example', 'warmup': True, 'event_id': warm_id})
        self.assertIsNone(self.node('org:warmup-pool.example'))
        self.assertIsNone(self.node('email:ana@warmup-pool.example'))
        self.assertIsNotNone(self.node('org:client.com'))
        seen = self.app.graph_action({})
        self.assertEqual([(w['domain'], w['source']) for w in seen['warmup']], [('warmup-pool.example', 'agent')])
        self.assertNotIn('org:warmup-pool.example', [o['id'] for o in seen['orgs']])
        with self.store.connect() as db:
            self.assertNotIn('warmup-pool.example', enrichment.due(db, NOW.isoformat(), 10))
            self.assertIn('client.com', enrichment.due(db, NOW.isoformat(), 10))
        # Only the owner undoes it here, and the owner's word stays: a later review can't hide it again.
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_warmup', {'domain': 'warmup-pool.example', 'warmup': False})
        self.app.action('graph_warmup', {'domain': 'warmup-pool.example', 'warmup': False}, owner_dashboard=True)
        self.assertIsNotNone(self.node('email:ana@warmup-pool.example'))
        self.assertEqual(self.app.graph_action({})['warmup'], [])
        kept = self.app.graph_action({'node_id': 'org:warmup-pool.example', 'warmup': True})
        self.assertIsNotNone(self.node('org:warmup-pool.example'))
        self.assertEqual(self.app.graph_action({})['warmup'], [])

    def test_roles_keep_their_evidence_the_owner_confirms_and_corrects_and_reviews_learn(self):
        pat = mail(3, 'Pat Doe <pat@client.com>', 'sam@acme.com', title='Intro')
        pat['body'] = 'Hi Sam,\nAs the CFO at Client Ltd I look after the renewal.\nPat'
        kim = mail(4, 'Kim <kim@client.com>', 'sam@acme.com')
        self.add(pat, mail(2, 'Sam <sam@acme.com>', 'Pat Doe <pat@client.com>', ('SENT',)), kim,
                 mail(2, 'Sam <sam@acme.com>', 'Kim <kim@client.com>, Ann Assist <ann@client.com>', ('SENT',)),
                 mail(5, 'Lee <lee@other.example>', 'sam@acme.com'), mail(4, 'Sam <sam@acme.com>', 'Lee <lee@other.example>', ('SENT',)))
        self.build()
        self.app.graph_build = self.build
        pat_id, kim_id = self.ids([pat['source_id'], kim['source_id']])
        with self.assertRaisesRegex(ValueError, 'words copied from that message'):
            self.app.graph_action({'node_id': 'email:pat@client.com', 'role': 'CFO at Client Ltd', 'event_id': pat_id, 'quote': 'I am the CFO'})
        with self.assertRaisesRegex(ValueError, 'one of their own signals'):
            self.app.graph_action({'node_id': 'email:pat@client.com', 'role': 'CFO at Client Ltd', 'event_id': kim_id, 'quote': 'Hello'})
        self.app.graph_action({'node_id': 'email:pat@client.com', 'role': 'CFO at Client Ltd', 'event_id': pat_id, 'quote': '"the CFO at  Client Ltd…"'})
        found = self.app.graph_action({'node_id': 'email:pat@client.com'})
        self.assertEqual((found['role'], found['role_by'], found['role_event_id']), ('CFO at Client Ltd', 'review', pat_id))
        self.assertEqual(found['role_quote'], '"the CFO at Client Ltd…"')
        # Who's who: a review's roles wait for the owner's ✓, with the line they came from.
        self.assertEqual([(w['name'], w['role'], w['event_id']) for w in self.app.graph_action({})['whos_who']], [('Pat Doe', 'CFO at Client Ltd', pat_id)])
        self.app.action('graph_role', {'node_id': 'email:pat@client.com', 'role': 'CFO at Client Ltd'}, owner_dashboard=True)
        found = self.app.graph_action({'node_id': 'email:pat@client.com'})
        self.assertEqual((found['role_by'], found['role_event_id']), ('owner', pat_id))  # confirmed: the owner's word, evidence kept
        self.assertEqual(self.app.graph_action({})['whos_who'], [])
        # A correction is kept, and later reviews learn from it.
        self.app.graph_action({'node_id': 'email:lee@other.example', 'role': 'intern'})
        self.app.action('graph_role', {'node_id': 'email:lee@other.example', 'role': 'head of finance'}, owner_dashboard=True)
        with self.store.connect() as db:
            self.assertEqual(graph.corrections_lines(db), '- Lee: a review said "intern", the owner made it "head of finance"')
            db.execute("UPDATE graph_nodes SET strength=1 WHERE kind IN ('person','org')")
        asked = self.store.role_text('run1', limit=10)
        self.assertIn('means by a role:\n- Lee: a review said "intern"', asked)
        self.assertIn('Organisations with no role yet', asked)
        self.assertIn('node_id org:client.com', asked)
        self.assertIn('Pat Doe (CFO at Client Ltd)', asked)  # its people with what they are to the owner
        self.assertNotIn('org:client.com', self.store.role_text('org-run2', limit=10))
        # An ask that didn't show its people with their roles yet is made once more.
        with self.store.connect() as db:
            db.execute("UPDATE graph_role_asks SET people_shown=0 WHERE identifier='org:client.com'")
        self.assertIn('node_id org:client.com', self.store.role_text('org-run2b', limit=10))
        self.assertNotIn('org:client.com', self.store.role_text('org-run2c', limit=10))
        # One of its people getting a role after the ask makes the organisation worth reading again.
        a.now = lambda: (NOW + dt.timedelta(minutes=5)).isoformat()
        self.app.action('graph_role', {'node_id': 'email:ann@client.com', 'role': 'office manager'}, owner_dashboard=True)
        a.now = lambda: NOW.isoformat()
        self.assertIn('node_id org:client.com', self.store.role_text('org-run3', limit=10))
        self.app.action('graph_role', {'node_id': 'email:ann@client.com', 'role': ''}, owner_dashboard=True)
        # Organisations get a role, and their people carry it as context.
        self.app.graph_action({'node_id': 'org:client.com', 'role': 'customer', 'event_id': pat_id, 'quote': 'look after the renewal'})
        self.assertEqual(self.node('org:client.com')['role'], 'customer')
        with self.store.connect() as db:
            self.assertIn('- Kim (kim@client.com): no role yet; at client.com (customer); ', graph.context_lines(db, [kim_id]))
        # How two people are related, as a link both ways; the owner's word stays and the owner can remove it.
        self.app.graph_action({'node_id': 'email:ann@client.com', 'related_to': 'email:kim@client.com', 'relation': 'assistant of'})
        self.assertEqual([(r['a_name'], r['relation'], r['b_name']) for r in self.node('email:kim@client.com')['relations']], [('Ann Assist', 'assistant of', 'Kim')])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT kind FROM graph_links WHERE a='email:ann@client.com' AND b='email:kim@client.com'").fetchone()[0], 'relation')
            self.assertIn('Ann Assist is assistant of Kim', graph.context_lines(db, [kim_id]))
        self.app.action('graph_relation', {'node_id': 'email:ann@client.com', 'related_to': 'email:kim@client.com', 'relation': 'manager of'}, owner_dashboard=True)
        self.app.graph_action({'node_id': 'email:kim@client.com', 'related_to': 'email:ann@client.com', 'relation': 'boss of'})
        self.assertEqual(self.node('email:kim@client.com')['relations'][0]['relation'], 'manager of')
        self.app.action('graph_relation', {'node_id': 'email:kim@client.com', 'related_to': 'email:ann@client.com', 'relation': ''}, owner_dashboard=True)
        self.assertEqual(self.node('email:kim@client.com')['relations'], [])
        # A review's role is read again when it's old and there was contact since, or when something new shows up.
        self.app.graph_action({'node_id': 'email:kim@client.com', 'role': 'trial user'})
        with self.store.connect() as db:
            self.assertEqual(graph.stale_roles(db, NOW.isoformat())[0]['why'], 'saved without the line it came from')
        self.app.graph_action({'node_id': 'email:kim@client.com', 'role': 'trial user', 'event_id': kim_id, 'quote': 'Hello'})
        with self.store.connect() as db:
            self.assertEqual(graph.stale_roles(db, NOW.isoformat()), [])
            db.execute("UPDATE person_roles SET checked_at=? WHERE identifier='email:kim@client.com'", (at(120),))
            self.assertEqual([(r['name'], r['why']) for r in graph.stale_roles(db, NOW.isoformat())], [('Kim', 'read 120 days ago, in touch since')])
            db.execute("UPDATE person_roles SET checked_at=?,hints='[]' WHERE identifier='email:kim@client.com'", (at(1),))
            db.execute("UPDATE graph_nodes SET info=json_set(info,'$.hints',json('[\"billed through Stripe as Client Ltd\"]')) WHERE id='email:kim@client.com'")
            self.assertEqual(graph.stale_roles(db, NOW.isoformat())[0]['why'], 'new since: billed through Stripe as Client Ltd')
            db.execute('DELETE FROM graph_role_asks')
        self.assertIn('may be out of date', self.store.role_text('run2'))
        # Cards: who they're about, ranked closest (and paying customers) first; nobody unnamed for long.
        with self.store.connect() as db:
            db.execute("UPDATE graph_nodes SET info=json_set(info,'$.paying',json('true')) WHERE id='org:client.com'")
            people, weight = graph.card_people(db, [kim_id])
        self.assertEqual((people[0]['name'], people[0]['role'], people[0]['paying']), ('Kim', 'trial user', True))
        self.assertGreater(weight, 2)  # closeness, plus a paying customer
        self.assertEqual(self.store.no_role_yet([kim_id]), [])
        with self.store.connect() as db:
            db.execute("DELETE FROM person_roles WHERE identifier='email:kim@client.com'")
        self.build()
        self.assertEqual(self.store.no_role_yet([kim_id]), [{'node_id': 'email:kim@client.com', 'name': 'Kim'}])
        card = self.store.suggest('k', 'Answer Kim', 'Kim asked.', [kim_id])['id']
        lines = self.app.graph_lines(card)
        self.assertIn('a cofounder, a sister or a friend reads differently', lines)
        self.assertIn('No role yet for Kim (email:kim@client.com)', lines)
        with self.store.connect() as db:
            db.execute('DELETE FROM graph_role_asks')
            db.execute("UPDATE graph_nodes SET strength=0.2 WHERE id='email:kim@client.com'")
        self.assertIn('- Kim (node_id email:kim@client.com; faint; ', self.store.role_text('run3'))  # on an open card: asked though faint

    def test_what_is_open_between_you_shows_on_the_person(self):
        ask = mail(3, 'Ann Example <ann@client.com>', 'sam@acme.com', thread='deal')
        reply = mail(2, 'Sam <sam@acme.com>', 'Ann Example <ann@client.com>', ('SENT',), thread='deal')
        self.add(ask, reply)
        ask_id, reply_id = self.ids([ask['source_id'], reply['source_id']])
        self.store.owner_user_ids = set()
        self.app.action('promise', {'direction': 'mine', 'event_id': reply_id, 'who': 'Ann', 'what': 'send the deck', 'due': '2026-10-20'})
        self.app.action('promise', {'direction': 'theirs', 'event_id': ask_id, 'who': 'Ann', 'what': 'sign the contract'})
        task = self.store.suggest('ann-1', 'Answer Ann', 'Ann asked about pricing.', [ask_id])
        other = self.store.suggest('ann-2', 'Book the call with Ann', 'Ann wants a call.', [reply_id])
        with self.store.connect() as db:
            db.execute("INSERT INTO facts(text,decision_id,suggestion_id,created_at) VALUES('Ann prefers calls before 10','d1',?,?)", (other['id'], NOW.isoformat()))
        self.build()
        detail = self.app.graph_action({'node_id': 'email:ann@client.com'})
        self.assertEqual([p['what'] for p in detail['open']['you_promised']], ['send the deck'])
        self.assertEqual([p['what'] for p in detail['open']['they_promised']], ['sign the contract'])
        self.assertEqual({c['title'] for c in detail['open']['cards']}, {'Answer Ann', 'Book the call with Ann'})
        self.assertNotIn('known', detail['open'])  # a fact on her card is no fact about her: no subject, on nobody's page
        lines = self.app.graph_lines(task['id'])
        self.assertIn('open: you promised to send the deck by Tue 20 Oct; they promised to sign the contract; card "Book the call with Ann"', lines)
        self.assertNotIn('card "Answer Ann"', lines)  # the task itself

    def test_a_fact_shows_on_who_it_is_about_never_on_who_sent_the_message(self):
        ask = mail(3, 'Ann Example <ann@client.com>', 'sam@acme.com', thread='deal')
        bob = mail(2, 'Bob Other <bob@other.com>', 'sam@acme.com', thread='bob')
        self.add(ask, bob)
        ask_id, bob_id = self.ids([ask['source_id'], bob['source_id']])
        self.store.owner_user_ids = set()
        self.build()
        card = self.store.suggest('ann-1', 'Answer Ann', 'Ann asked about pricing.', [ask_id])['id']
        self.store.decide(card, 'other', 'i never use capitals in chat, and Ann prefers calls before 10', 'note-1', 'dashboard')
        mine = self.app.action('remember_fact', {'text': 'The owner does not use capitals in chat.', 'decision_id': 'note-1'})
        hers = self.app.action('remember_fact', {'text': 'Ann prefers calls before 10.', 'decision_id': 'note-1', 'about': ['email:ann@client.com']})
        self.assertEqual(hers['about'], ['email:ann@client.com'])
        with self.assertRaisesRegex(ValueError, 'not a person, organisation or project'):
            self.app.action('remember_fact', {'text': 'Someone else entirely.', 'decision_id': 'note-1', 'about': ['email:nobody@x.com']})
        known = lambda node: [f['text'] for f in self.app.graph_action({'node_id': node})['open'].get('known', [])]
        self.assertEqual(known('email:ann@client.com'), ['Ann prefers calls before 10.'])  # the owner's own habit is not on her page
        self.assertEqual(known('email:bob@other.com'), [])
        self.assertIn('known: Ann prefers calls before 10.', self.app.graph_lines(card))
        self.build()  # a rebuild keeps who it is about
        self.assertEqual(known('email:ann@client.com'), ['Ann prefers calls before 10.'])
        # A review ties an older fact to who it is about; only kind=fact notes have a subject.
        self.assertEqual(self.app.action('remember_fact', {'id': mine['id'], 'about': ['email:bob@other.com']})['about'], ['email:bob@other.com'])
        self.assertEqual(known('email:bob@other.com'), ['The owner does not use capitals in chat.'])
        # Not about Bob: only that link goes, for good; the fact stays in the owner's memory.
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_fact_unlink', {'fact_id': mine['id'], 'node_id': 'email:bob@other.com'})
        self.app.action('graph_fact_unlink', {'fact_id': mine['id'], 'node_id': 'email:bob@other.com'}, owner_dashboard=True)
        self.assertEqual(known('email:bob@other.com'), [])
        self.assertIn('The owner does not use capitals in chat.', [f['text'] for f in self.store.facts()])
        self.app.action('remember_fact', {'id': mine['id'], 'about': ['email:bob@other.com']})  # a review can't put it back
        self.assertEqual(known('email:bob@other.com'), [])
        # Condensed, the new fact keeps who the originals were about.
        merged = self.app.action('remember_fact', {'text': 'Ann prefers calls before 10, never after 4.', 'replaces': [hers['id']]})
        self.assertEqual(known('email:ann@client.com'), ['Ann prefers calls before 10, never after 4.'])
        # Forget: gone everywhere, her page and the owner's memory.
        self.app.action('forget_fact', {'id': merged['id']}, owner_dashboard=True)
        self.assertEqual(known('email:ann@client.com'), [])
        self.assertNotIn(merged['id'], [f['id'] for f in self.store.facts()])

    def test_an_event_row_says_who_wrote_it_and_how_it_starts(self):
        accounts = [{'id': 'slack', 'service': 'slack', 'user_id': 'U0OWNER'}]
        glance = lambda kind, body, **m: a.event_glance({'kind': kind, 'account_id': 'slack', 'body': body, 'metadata': m}, accounts)
        self.assertEqual(glance('gmail.message', 'Hi Sam,\r\n\r\nthe   form', **{'from': 'Ann Example <ann@client.com>'}),
                         {'from': 'Ann Example', 'from_me': False, 'preview': 'Hi Sam, the form'})
        self.assertEqual(glance('gmail.message', 'Sure', **{'from': 'Sam <sam@acme.com>', 'labels': ['SENT']})['from'], 'You')
        self.assertEqual(glance('salesflare.email', 'From: Ann <ann@client.com>\nTo: Kim <kim@acme.com>\n\nOur internet is down')['preview'], 'Our internet is down')
        self.assertEqual(glance('slack.message', 'see <https://x.example|the doc>', user_id='U0OWNER', conversation_type='dm'),
                         {'from': 'You', 'from_me': True, 'preview': 'see the doc', 'dm': True})
        self.assertEqual(glance('slack.message', 'done', user_id='U0ANN', conversation_type='channel', channel_name='standup')['place'], '#standup')
        self.assertEqual(glance('whatsapp.message', 'x' * 300, from_me=False, sender_display='Ann')['preview'], 'x' * 240 + '…')
        # Chat messages carry their conversation, so a run of them is one row; mail does not.
        self.assertEqual(glance('whatsapp.message', 'hi', chat_id='g1@g.us')['thread'], 'slack:whatsapp.message:g1@g.us')
        self.assertNotIn('thread', glance('gmail.message', 'Hi', thread_id='t1'))


def linkedin(days_ago, conv, name, text, mine=False, headline=None):
    n = next(counter)
    return {'account_id': 'li', 'source_id': f'l{n}', 'kind': 'linkedin.message', 'occurred_at': at(days_ago), 'title': name, 'body': text,
            'deleted': False, 'metadata': {'conversation_id': conv, 'chat_name': name, 'from_me': mine, 'from': 'You' if mine else name,
                                           'headline': headline, 'group': False}}


class StatedOrganisations(unittest.TestCase):
    """Where someone works: from a work address, or as a source states it, read by a review with the words that say it."""
    add, build, node, ids = Graph.add, Graph.build, Graph.node, Graph.ids

    def setUp(self):
        Graph.setUp(self)
        self.app.graph_build = self.build
        self.store.accounts = CONFIG['accounts'] + [{'id': 'li', 'service': 'linkedin'}]
        self.store.owner_user_ids = set()

    def robin(self):
        first = linkedin(7, 'c-robin', 'Robin Vale', "We're sharing early access to our API with friends of Globex.", headline='Engineering at Globex')
        mine = linkedin(6, 'c-robin', 'Robin Vale', 'Would Globex be interested?', mine=True, headline='Engineering at Globex')
        later = linkedin(5, 'c-robin', 'Robin Vale', "I'll have someone on our API team schedule something", headline='Engineering at Globex')
        self.add(first, mine, later, linkedin(4, 'c-robin', 'Robin Vale', 'Thanks!', mine=True, headline='Engineering at Globex'))
        return self.ids([first['source_id'], mine['source_id'], later['source_id']])

    def test_a_linkedin_headline_is_kept_and_a_review_links_them_to_the_organisation_it_states(self):
        first, mine, later = self.robin()
        self.add(mail(3, 'Gra Ham <graham@globex.ai>', 'sam@acme.com'), mail(2, 'Sam <sam@acme.com>', 'Gra Ham <graham@globex.ai>', ('SENT',)))
        self.build()
        node = 'li:c-robin'
        view = self.node(node)
        self.assertEqual((self.app.graph_action({'node_id': node})['headline'], view['org']), ('Engineering at Globex', None))  # shown as is, never parsed by rule
        asked = self.store.role_text('run1', limit=10)
        self.assertIn('Where they work', asked)
        self.assertIn('LinkedIn headline: "Engineering at Globex"', asked)
        self.assertNotIn('Where they work', self.store.role_text('run2', limit=10))  # asked once a month at most
        with self.assertRaisesRegex(ValueError, 'event_id and quote'):
            self.app.graph_action({'node_id': node, 'org': 'Globex'})
        with self.assertRaisesRegex(ValueError, 'words copied'):
            self.app.graph_action({'node_id': node, 'org': 'Globex', 'event_id': later, 'quote': 'Globex employee'})
        with self.assertRaisesRegex(ValueError, "not the owner's message"):
            self.app.graph_action({'node_id': node, 'org': 'Globex', 'event_id': mine, 'quote': 'Would Globex be interested'})
        self.app.graph_action({'node_id': node, 'org': 'Globex', 'event_id': later, 'quote': 'Engineering at Globex'})
        found = self.app.graph_action({'node_id': node})
        self.assertEqual((found['org'], found['org_name'], found['org_by'], found['org_via'], found['org_at'], found['org_event_id']),
                         ('org:globex.ai', 'globex.ai', 'review', 'LinkedIn headline', at(5), later))
        self.assertIn(('li:c-robin', 'member'), [(l['id'], l['link']) for l in self.app.graph_action({'node_id': 'org:globex.ai'})['links']])
        with self.store.connect() as db:
            self.assertIn('at globex.ai', graph.context_lines(db, [later]))
        self.assertNotIn('Where they work', self.store.role_text('run3', limit=10))

    def test_an_organisation_only_a_source_states_stands_on_its_own_and_is_never_looked_up(self):
        first, _, _ = self.robin()
        self.build()
        self.app.graph_action({'node_id': 'li:c-robin', 'org': 'Globex', 'event_id': first, 'quote': 'friends of Globex'})
        found = self.app.graph_action({'node_id': 'li:c-robin'})
        self.assertEqual((found['org'], found['org_name'], found['org_via']), ('org:named:globex', 'Globex', 'LinkedIn'))
        org = self.node('org:named:globex')
        self.assertEqual((org['name'], org['domain'], org['signals']), ('Globex', None, 1))
        with self.store.connect() as db:
            self.assertEqual(enrichment.due(db, NOW.isoformat(), 10), [])
            with self.assertRaisesRegex(ValueError, 'email domain'):
                graph.hide_warmup(db, 'org:named:globex', True, 'agent', NOW.isoformat())
            db.execute('ROLLBACK')

    def test_the_owner_word_stays_and_a_work_address_wins_over_a_review(self):
        first, _, later = self.robin()
        sig = mail(3, 'Ann Bell <ann.bell@gmail.com>', 'sam@acme.com', title='Hello')
        sig['body'] = 'Thanks for the call.\n\nAnn Bell\nHead of Operations, Initech'
        self.add(sig, mail(2, 'Sam <sam@acme.com>', 'Ann Bell <ann.bell@gmail.com>', ('SENT',)),
                 kim := mail(3, 'Kim <kim@client.com>', 'sam@acme.com'), mail(2, 'Sam <sam@acme.com>', 'Kim <kim@client.com>', ('SENT',)))
        self.build()
        sig_id, kim_id = self.ids([sig['source_id'], kim['source_id']])
        self.app.graph_action({'node_id': 'email:ann.bell@gmail.com', 'org': 'Initech', 'event_id': sig_id, 'quote': 'Head of Operations, Initech'})
        ann = self.app.graph_action({'node_id': 'email:ann.bell@gmail.com'})
        self.assertEqual((ann['org_name'], ann['org_via'], ann['org_quote']), ('Initech', 'email', 'Head of Operations, Initech'))
        self.app.graph_action({'node_id': 'email:kim@client.com', 'org': 'Globex', 'event_id': kim_id, 'quote': 'Hello'})
        self.assertEqual((self.node('email:kim@client.com')['org'], self.node('email:kim@client.com')['org_by']), ('org:client.com', 'email'))
        self.app.action('graph_person_org', {'node_id': 'li:c-robin', 'org': 'Initech'}, owner_dashboard=True)
        said = self.app.graph_action({'node_id': 'li:c-robin', 'org': 'Globex', 'event_id': later, 'quote': 'Engineering at Globex'})
        self.assertEqual((self.node('li:c-robin')['org'], self.node('li:c-robin')['org_by']), ('org:named:initech', 'owner'))
        self.assertEqual(self.node('email:ann.bell@gmail.com')['org'], 'org:named:initech')  # one Initech, two people
        self.app.action('graph_person_org', {'node_id': 'email:ann.bell@gmail.com', 'org': 'Acme'}, owner_dashboard=True)
        self.assertEqual((self.node('email:ann.bell@gmail.com')['org'], self.node('email:ann.bell@gmail.com')['colleague']), (None, True))
        self.app.action('graph_person_org', {'node_id': 'li:c-robin', 'org': ''}, owner_dashboard=True)
        self.assertIsNone(self.node('li:c-robin')['org'])
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_person_org', {'node_id': 'li:c-robin', 'org': 'Globex'})

    def test_one_organisations_turn_backfills_from_what_is_stored(self):
        self.robin()
        self.build()
        self.app.config = {**CONFIG, 'review_worker': {'enabled': True}}
        started = self.app.orgs_backfill_step()
        self.assertEqual(started['people'], 1)
        self.assertIsNone(self.app.orgs_backfill_step())  # one at a time, once
        page = self.store.profile_evidence(1, a.PROFILE_SESSION_PREFIX + started['run_id'])
        self.assertEqual(page['people'][0]['linkedin_headline'], 'Engineering at Globex')
        self.assertTrue(all(m['from'] != 'the owner' for m in page['people'][0]['messages']))
        with self.store.connect() as db:
            db.execute("UPDATE profile_runs SET status='ok' WHERE run_id=?", (started['run_id'],))
        self.assertIsNone(self.app.orgs_backfill_step())
        prompt = a.orgs_prompt(started['run_id'], page['pages'])
        self.assertIn('states it plainly', prompt)
        self.assertNotIn('{', prompt.split('graph args')[0])


class Boards(unittest.TestCase):
    """Projects on a board, moved by what messages mean: each move keeps why and the message, the owner's drag wins
    until a newer message, an older message never undoes a newer move, and the board never sends anything."""
    STAGES = [{'name': 'Contacted', 'hint': 'Asked, no yes yet'}, 'Agreed', 'Live']

    def setUp(self):
        Graph.setUp(self)
        with self.store.connect() as db:
            graph.save_board(db, {'name': 'Listings', 'stages': self.STAGES, 'about': 'Partners we list with'}, NOW.isoformat())

    add, build, thread_ids = Graph.add, Graph.build, Graph.thread_ids

    def mails(self):
        self.add(mail(9, 'Sam <sam@acme.com>', 'ann@partner.example', ('SENT',), thread='deal'),
                 mail(3, 'Ann <ann@partner.example>', 'sam@acme.com', thread='deal'),
                 mail(1, 'Sam <sam@acme.com>', 'ann@partner.example', ('SENT',), thread='other'))
        with self.store.connect() as db:
            return [r[0] for r in db.execute('SELECT id FROM events ORDER BY occurred_at')]

    def test_migration_adds_the_stage_columns_to_an_older_projects_table(self):
        with self.store.connect() as db:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(projects)')}
            self.assertTrue({'board_id', 'stage', 'stage_reason', 'stage_event_id', 'stage_by', 'stage_at', 'next_step', 'org'} <= cols)
            self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('boards','project_moves')").fetchone())

    def test_review_moves_keep_reason_and_message_and_the_owner_wins_until_a_newer_message(self):
        asked, agreed, later = self.mails()
        with self.store.connect() as db:
            with self.assertRaises(ValueError):  # only the owner makes a board
                graph.set_stage(db, 'Paperkite', 'Contacted', 'review', NOW.isoformat(), board='Hiring', stages=self.STAGES, reason='asked', event_id=asked)
            with self.assertRaises(ValueError):  # a review move gives the message and why
                graph.set_stage(db, 'Paperkite', 'Contacted', 'review', NOW.isoformat(), board='Listings', stages=self.STAGES)
            first = graph.set_stage(db, 'Paperkite', 'Contacted', 'review', NOW.isoformat(), board='Listings', stages=self.STAGES,
                                    reason='asked for a listing', event_id=asked, next_step='Wait for their reply', org='https://www.Partner.example/x')
            node = first['node_id']
            row = db.execute('SELECT * FROM projects WHERE id=?', (node.split(':')[1],)).fetchone()
            self.assertEqual((row['stage'], row['stage_reason'], row['stage_event_id'], row['next_step'], row['org']),
                             ('Contacted', 'asked for a listing', asked, 'Wait for their reply', 'partner.example'))
            with self.assertRaises(ValueError):
                graph.set_stage(db, node, 'Nowhere', 'review', NOW.isoformat(), reason='x', event_id=agreed)
            graph.set_stage(db, node, 'agreed', 'review', NOW.isoformat(), reason='Ann said yes', event_id=agreed)
            # An older message never undoes a newer move
            kept = graph.set_stage(db, node, 'Contacted', 'review', NOW.isoformat(), reason='asked again', event_id=asked)
            self.assertEqual(kept['kept'], 'A later message moved it already')
            # The owner's drag wins over a review reading an earlier message
            graph.set_stage(db, node, 'Live', 'owner', NOW.isoformat())
            kept = graph.set_stage(db, node, 'Agreed', 'review', NOW.isoformat(), reason='Ann said yes', event_id=later)
            self.assertEqual(kept['kept'], 'The owner moved it after this message')
            self.assertEqual(db.execute('SELECT stage,stage_by FROM projects WHERE id=?', (node.split(':')[1],)).fetchone()[:], ('Live', 'owner'))
            # The owner moves only from the node id
            with self.assertRaises(ValueError):
                graph.set_stage(db, 'Paperkite', 'Agreed', 'owner', NOW.isoformat())
        # Something new after the owner's move moves it again
        self.add(mail(-1, 'Ann <ann@partner.example>', 'sam@acme.com', thread='deal'))
        with self.store.connect() as db:
            newer = db.execute('SELECT max(id) FROM events').fetchone()[0]
            moved = graph.set_stage(db, node, 'Agreed', 'review', NOW.isoformat(), reason='Ann wants changes first', event_id=newer)
            self.assertNotIn('kept', moved)
            moves = graph.project_moves(db, node)
        self.assertEqual([m['stage'] for m in moves['moves']], ['Agreed', 'Live', 'Agreed', 'Contacted'])
        self.assertEqual((moves['board'], moves['stage'], moves['moves'][1]['source']), ('Listings', 'Agreed', 'owner'))

    def test_board_shows_no_reply_age_org_and_card(self):
        asked, answered, mine = self.mails()
        with self.store.connect() as db:
            node = graph.set_stage(db, 'Paperkite', 'Contacted', 'review', NOW.isoformat(), board='Listings', stages=self.STAGES,
                                   reason='asked for a listing', event_id=asked, org='partner.example')['node_id']
            graph.label(db, [answered, mine], 'Paperkite', 'review', NOW.isoformat())
        card = self.store.suggest('pk-1', 'Follow up with Ann', 'Made up.', [mine], project='Paperkite')
        self.build()
        with self.store.connect() as db:
            board, = graph.boards(db, NOW.isoformat())
            self.assertEqual([s['name'] for s in board['stages']], ['Contacted', 'Agreed', 'Live', graph.STALLED])
            self.assertTrue(board['stages'][-1]['side'])
            item, = board['projects']
            self.assertEqual((item['id'], item['stage'], item['first'], item['days']), (node, 'Contacted', True, 9))
            self.assertEqual(item['no_reply_days'], 1)  # counted from the owner's first message after Ann's answer
            self.assertEqual(item['org']['name'], 'partner.example')
            self.assertEqual(item['card']['id'], card['id'])
            self.assertEqual(graph.boards_brief(db)[0]['board'], 'Listings')
            # A project on a board keeps its node in the graph
            self.assertTrue(db.execute('SELECT 1 FROM graph_nodes WHERE id=?', (node,)).fetchone())

    def test_finish_analysis_moves_only_from_its_own_batch(self):
        asked, agreed, _ = self.mails()
        claim = self.store.begin_analysis(['work'], 10)
        move = {'project': 'Paperkite', 'board': 'Listings', 'stage': 'Agreed', 'reason': 'Ann said yes', 'event_id': agreed}
        with self.assertRaises(ValueError):
            self.store.finish_analysis(claim['analysis_id'], 'x', stages=[{**move, 'event_id': 999999}])
        with self.assertRaises(ValueError):
            self.store.finish_analysis(claim['analysis_id'], 'x', stages={'Paperkite': 'Agreed'})
        done = self.store.finish_analysis(claim['analysis_id'], 'Reviewed', stages=[move])
        self.assertEqual(done['stages'][0]['stage'], 'Agreed')
        with self.store.connect() as db:  # the next review sees where it stands
            self.assertEqual(graph.boards_brief(db)[0]['board'], 'Listings')

    def test_owner_moves_by_hand_only_on_the_dashboard(self):
        asked, _, _ = self.mails()
        with self.store.connect() as db:
            node = graph.set_stage(db, 'Paperkite', 'Contacted', 'review', NOW.isoformat(), board='Listings', stages=self.STAGES,
                                   reason='asked for a listing', event_id=asked)['node_id']
        with self.assertRaises(ValueError):
            self.app.action('graph_stage', {'node_id': node, 'stage': 'Live'})
        self.app.graph_build = lambda *a, **k: None
        moved = self.app.action('graph_stage', {'node_id': node, 'stage': 'Live'}, owner_dashboard=True)
        self.assertEqual(moved['stage'], 'Live')
        self.assertIn('graph_stage', a.OWNER_ACTIONS)

    def test_a_suggested_board_is_nothing_until_the_owner_saves_it(self):
        self.mails()
        with self.store.connect() as db:
            for name in ('Hire: support lead', 'Hire: designer', 'Hire: backend'):
                graph.project_id(db, name, NOW.isoformat())
            args = {'name': 'Hiring', 'stages': ['Sourced', 'Interviews', 'Hired'], 'about': 'Roles we hire for', 'projects': ['Hire: support lead', 'Hire: designer']}
            with self.assertRaises(ValueError):  # three projects or more
                graph.suggest_board(db, args, 'review', NOW.isoformat())
            args['projects'].append('Hire: backend')
            with self.assertRaises(ValueError):  # a review can't make a board by moving a project onto it
                graph.set_stage(db, 'Hire: designer', 'Sourced', 'review', NOW.isoformat(), board='Hiring', stages=args['stages'], reason='x', event_id=1)
        said = self.app.action('graph', {'suggest_board': args})
        self.assertEqual(said['projects'], 3)
        with self.assertRaises(ValueError):  # once per name
            self.app.action('graph', {'suggest_board': args})
        with self.store.connect() as db:
            self.assertEqual([b['name'] for b in graph.boards(db, NOW.isoformat())], ['Listings'])
            sug, = graph.board_suggestions(db)
            self.assertEqual(([s['name'] for s in sug['stages']], len(sug['projects'])), (['Sourced', 'Interviews', 'Hired'], 3))
            self.assertIn('Hire: designer', graph.off_boards(db)['projects']); self.assertEqual(graph.off_boards(db)['suggested'], ['Hiring'])
        with self.assertRaises(ValueError):
            self.app.action('board_save', {'name': 'Hiring', 'stages': ['Sourced'], 'about': ''})  # owner only
        self.app.graph_build = lambda *a, **k: None
        ids = [p['id'] for p in sug['projects']][:2]  # the owner took one out before saving
        saved = self.app.action('board_save', {'suggestion_id': sug['id'], 'name': 'Hiring', 'stages': ['Sourced', 'Interviews', 'Hired'], 'side': False,
                                               'about': 'Roles we hire for', 'projects': ids}, owner_dashboard=True)
        self.assertEqual(saved['added'], 2)
        with self.store.connect() as db:
            self.assertEqual(graph.board_suggestions(db), [])
            board = next(b for b in graph.boards(db, NOW.isoformat()) if b['name'] == 'Hiring')
            self.assertEqual([s['name'] for s in board['stages']], ['Sourced', 'Interviews', 'Hired'])  # no Stalled row
            self.assertTrue(all(p['sorting'] and p['stage'] == 'Sourced' for p in board['projects']))
            brief = next(b for b in graph.boards_brief(db) if b['board'] == 'Hiring')
            self.assertEqual((brief['about'], sorted(brief['to_sort'])), ('Roles we hire for', sorted(p['name'] for p in board['projects'])))
            # A review places one by its messages, with the reason, even in the stage it waits in
            event = db.execute('SELECT id FROM events ORDER BY id LIMIT 1').fetchone()[0]
            placed = graph.set_stage(db, board['projects'][0]['id'], 'Sourced', 'agent', NOW.isoformat(), reason='applied with a CV', event_id=event)
            self.assertNotIn('kept', placed)
            self.assertEqual(len(next(b for b in graph.boards_brief(db) if b['board'] == 'Hiring')['to_sort']), 1)

    def test_editing_stages_renames_moves_to_the_nearest_earlier_stage_and_deleting_keeps_projects(self):
        asked, agreed, later = self.mails()
        with self.store.connect() as db:
            board_id = db.execute("SELECT id FROM boards WHERE name='Listings'").fetchone()[0]
            a = graph.set_stage(db, 'Paperkite', 'Agreed', 'review', NOW.isoformat(), board='Listings', reason='said yes', event_id=agreed)['node_id']
            b = graph.set_stage(db, 'Bramblebox', 'Live', 'review', NOW.isoformat(), board='Listings', reason='live', event_id=later)['node_id']
            c = graph.set_stage(db, 'Quillfeather', graph.STALLED, 'review', NOW.isoformat(), board='Listings', reason='declined', event_id=asked)['node_id']
            # Contacted renamed to Asked, Agreed removed, Live renamed to Listed, a new stage added; Stalled row off
            done = graph.save_board(db, {'board_id': board_id, 'name': 'Partner listings', 'about': 'Partners we list with',
                                         'stages': [{'name': 'Asked', 'was': 'Contacted'}, {'name': 'Listed', 'was': 'Live'}, 'Renewed'], 'side': False}, NOW.isoformat())
            self.assertEqual(done['moved'], [{'from': 'Agreed', 'to': 'Asked', 'count': 1}, {'from': graph.STALLED, 'to': None, 'count': 1}])
            stage = lambda n: db.execute('SELECT board_id,stage,stage_reason FROM projects WHERE id=?', (n.split(':')[1],)).fetchone()[:]
            self.assertEqual(stage(a), (board_id, 'Asked', 'Agreed was removed'))
            self.assertEqual(stage(b)[:2], (board_id, 'Listed'))
            self.assertEqual(stage(c), (None, None, None))  # no Stalled row: it left the board
            with self.assertRaises(ValueError):
                graph.save_board(db, {'name': 'Partner listings', 'stages': ['A'], 'about': ''}, NOW.isoformat())  # a second board by that name
            gone = graph.delete_board(db, board_id, NOW.isoformat())
            self.assertEqual(gone['projects'], 2)
            self.assertEqual(stage(a), (None, None, None))
            self.assertTrue(db.execute('SELECT 1 FROM projects WHERE id=? AND closed_at IS NULL', (a.split(':')[1],)).fetchone())  # the project stays
            self.assertEqual(graph.boards(db, NOW.isoformat()), [])


if __name__ == '__main__':
    unittest.main()
