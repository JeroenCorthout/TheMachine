"""Fleet, Family and Friends members in the Graph: a marker and their shared name, from the directory kept here only."""
import tempfile
import unittest
from pathlib import Path

import test_graph as tg

a, graph = tg.a, tg.graph


def member(member_id, name, emails, machine=True, me=False):
    return {'member_id': member_id, 'name': name, 'emails': emails, 'machine': machine, 'me': me, 'label': name,
            'answers_with': 'Hosted: example' if machine else ''}


ME = member('fm_me', 'Sam Owner', ['sam@acme.com'], me=True)


class FakeFleet:
    """Only the directory kept on this Machine: any other call (a question to a peer's Machine) fails the test."""
    def __init__(self, fleets):
        self.directory = fleets

    def fleets(self):
        return self.directory

    def view(self):
        return {'on': True, 'fleets': [{'id': ref, 'kind': f['kind'], 'people': [{'member_id': m['member_id'], 'name': m['name']} for m in f['members']]}
                                       for ref, f in self.directory.items()]}

    def shared_view(self):  # shared connectors (shared_connectors.py): this Machine's own, no peer asked
        return {'prompts': [], 'shares': [], 'accounts': [], 'can_share': []}

    def __getattr__(self, name):
        raise AssertionError('the Graph must not use the Fleet for ' + name)


def work(*members, name='Acme'):
    return {'kind': 'work', 'name': name, 'members': [ME, *members]}


class GraphFleet(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App); self.app.store = self.store; self.app.config = tg.CONFIG
        self.app.graph_build = lambda: None
        self.now = a.now
        a.now = lambda: tg.NOW.isoformat(); self.addCleanup(setattr, a, 'now', self.now)
        for days in (1, 3, 6):
            self.add(tg.mail(days, 'kim <kim@acme.com>', 'sam@acme.com'), tg.mail(days, 'sam@acme.com', 'kim@acme.com', labels=('SENT',)),
                     tg.mail(days, 'Lee Park <lee@acme.com>', 'sam@acme.com'), tg.mail(days, 'sam@acme.com', 'lee@acme.com', labels=('SENT',)),
                     tg.mail(days, 'Ana Vos <ana@example.org>', 'sam@acme.com'), tg.mail(days, 'sam@acme.com', 'ana@example.org', labels=('SENT',)))
        self.rebuild()

    def rebuild(self):
        with self.store.connect() as db:
            db.isolation_level = None
            graph.build(db, tg.CONFIG, tg.NOW.isoformat())

    def add(self, *events):
        for e in events:
            self.store.commit_page(e['account_id'], {}, [e])

    def people(self, fleets):
        self.app.fleet = FakeFleet(fleets) if fleets is not None else None
        return {p['id']: p for p in self.app.graph_action({'limit': 15}, source='owner')['people']}

    def test_member_gets_marker_and_shared_name(self):
        people = self.people({'fl_1': work(member('fm_kim', 'Kim Lee-Park', ['kim@acme.com']))})
        kim = people['email:kim@acme.com']
        self.assertEqual(kim['name'], 'Kim Lee-Park')
        self.assertEqual(kim['name_from_messages'], 'kim')
        self.assertTrue(kim['fleet']['machine'] and kim['fleet']['named'])
        self.assertEqual([(x['kind'], x['label'], x['fleet_name'], x['member_id']) for x in kim['fleet']['in']], [('work', 'Work Fleet', 'Acme', 'fm_kim')])
        self.assertNotIn('fleet', people['email:lee@acme.com'])
        detail = self.app.graph_action({'node_id': 'email:kim@acme.com'}, source='owner')
        self.assertEqual((detail['name'], detail['fleet']['in'][0]['member_id']), ('Kim Lee-Park', 'fm_kim'))

    def test_complete_name_stays_and_no_machine_says_so(self):
        people = self.people({'fl_1': work(member('fm_lee', 'Leo P.', ['lee@acme.com'], machine=False))})
        lee = people['email:lee@acme.com']
        self.assertEqual(lee['name'], 'Lee Park')  # already a full name: the Fleet's doesn't replace it
        self.assertFalse(lee['fleet']['machine'])
        self.assertNotIn('named', lee['fleet'])

    def test_owner_name_wins_and_is_kept_after_leaving(self):
        with self.store.connect() as db:
            graph.rename(db, 'email:kim@acme.com', 'Kimberly', tg.NOW.isoformat())
        self.rebuild()
        directory = {'fl_1': work(member('fm_kim', 'Kim Lee-Park', ['kim@acme.com']))}
        self.assertEqual(self.people(directory)['email:kim@acme.com']['name'], 'Kimberly')
        directory['fl_1']['members'] = [ME]  # they left the Fleet
        kim = self.people(directory)['email:kim@acme.com']
        self.assertEqual(kim['name'], 'Kimberly')
        self.assertNotIn('fleet', kim)

    def test_leaving_takes_marker_and_name_away(self):
        directory = {'fl_1': work(member('fm_kim', 'Kim Lee-Park', ['kim@acme.com']))}
        self.assertIn('fleet', self.people(directory)['email:kim@acme.com'])
        directory['fl_1']['members'] = [ME]
        kim = self.people(directory)['email:kim@acme.com']
        self.assertEqual(kim['name'], 'kim')
        self.assertNotIn('fleet', kim)
        self.assertNotIn('fleet', self.people({})['email:kim@acme.com'])
        self.assertNotIn('fleet', self.people(None)['email:kim@acme.com'])  # no Fleet at all

    def test_friends_and_family_follow_their_membership(self):
        directory = {'fr_1': {'kind': 'friends', 'name': 'Friends', 'members': [ME, member('fp_ana', 'Ana Vos', ['ana@example.org'])],
                              'requests': {'in': [{'id': 'rq', 'name': 'Kim Lee-Park'}], 'out': []},
                              'blocked': [{'member_id': 'fp_x', 'name': 'Lee Park', 'emails': ['lee@acme.com']}]},
                     'fl_2': {'kind': 'family', 'name': 'Home', 'members': [ME, member('fm_ana', 'Ana Vos', ['ana@example.org'])]}}
        people = self.people(directory)
        self.assertEqual([x['label'] for x in people['email:ana@example.org']['fleet']['in']], ['Family', 'Friend'])
        self.assertNotIn('fleet', people['email:kim@acme.com'])  # a pending request is not a friend
        self.assertNotIn('fleet', people['email:lee@acme.com'])  # nor a blocked person

    def test_matched_on_addresses_only(self):
        people = self.people({'fl_1': work(member('fm_x', 'Lee Park', ['lee.park@other.example']), member('fm_y', 'Not An Address', ['@kim']))})
        self.assertFalse(any('fleet' in p for p in people.values()))

    def test_search_finds_a_member_by_shared_name_and_fleet_tab_links_back(self):
        directory = {'fl_1': work(member('fm_kim', 'Kim Lee-Park', ['kim@acme.com']))}
        self.app.fleet = FakeFleet(directory)
        found = self.app.graph_action({'query': 'lee-park'}, source='owner')['found']
        self.assertEqual([(p['id'], p['name']) for p in found], [('email:kim@acme.com', 'Kim Lee-Park')])
        view = self.app.fleet_view()
        self.assertEqual({p['member_id']: p.get('graph_node') for p in view['fleets'][0]['people']}, {'fm_me': None, 'fm_kim': 'email:kim@acme.com'})


class Names(unittest.TestCase):
    def test_incomplete(self):
        for name in ('kim', 'kim lee', 'kim@acme.com', '', 'Kim'):
            self.assertTrue(graph.incomplete_name(name), name)
        for name in ('Kim Lee', 'Kim-Ann de Vos'):
            self.assertFalse(graph.incomplete_name(name), name)


if __name__ == '__main__':
    unittest.main()
