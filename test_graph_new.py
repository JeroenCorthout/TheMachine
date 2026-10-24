"""People and organisations added by hand (New person, New organisation): in the graph at once and after every
rebuild, also with no messages; never made twice; a later message joins them; deleting works."""
import unittest

import test_graph as tg

a, graph = tg.a, tg.graph


class GraphNew(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node

    def setUp(self):
        tg.Graph.setUp(self)

    def new(self, kind, **args):
        return self.app.action('graph_new', {'kind': kind, **args}, owner_dashboard=True)

    def test_a_person_with_no_messages_stays_with_their_organisation(self):
        made = self.new('person', name='Rae  Vos', email='Rae@Umber.example', org='Umber Labs')
        self.assertEqual(made, {'node_id': 'email:rae@umber.example', 'name': 'Rae Vos', 'created': True})
        view = self.node(made['node_id'])
        self.assertEqual((view['kind'], view['name'], view['org_by']), ('person', 'Rae Vos', 'owner'))
        with self.store.connect() as db:
            self.assertEqual(graph.page(db, 'people')['items'][0]['org_name'], 'Umber Labs')
        self.assertEqual([e['value'] for e in view['emails']], ['rae@umber.example'])
        self.assertTrue(view['manual'])
        self.build()
        self.assertEqual(self.node(made['node_id'])['name'], 'Rae Vos')
        org = self.node(view['org'])
        self.assertEqual((org['kind'], org['name']), ('org', 'Umber Labs'))
        # The same address again: the one already there, not a second
        self.assertEqual(self.new('person', name='Rae', email='rae@umber.example'), {'node_id': 'email:rae@umber.example', 'name': 'Rae Vos', 'existing': True})

    def test_a_person_without_an_address_shows_no_made_up_identifier_and_can_be_deleted(self):
        made = self.new('person', name='Lou Marsh')
        self.assertTrue(made['node_id'].startswith('manual:'))
        view = self.node(made['node_id'])
        self.assertEqual((view['name'], view['ids'], view['side']), ('Lou Marsh', [], 'personal'))
        self.app.delete_person(made['node_id'])
        self.assertIsNone(self.node(made['node_id']))
        self.build()
        self.assertIsNone(self.node(made['node_id']))

    def test_their_first_message_joins_the_person_added_by_hand(self):
        made = self.new('person', name='Ivy Park', email='ivy@globex.example')
        self.add(tg.mail(2, 'Ivy P <ivy@globex.example>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'ivy@globex.example', ('SENT',)))
        self.build()
        view = self.node(made['node_id'])
        self.assertEqual((view['name'], view['signals'], view['org']), ('Ivy Park', 2, 'org:globex.example'))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM graph_nodes WHERE kind='person'").fetchone()[0], 1)

    def test_an_organisation_by_website_or_name_once_and_deleted_while_empty(self):
        made = self.new('org', name='Umber Labs', website='https://www.umber.example/about')
        self.assertEqual(made, {'node_id': 'org:umber.example', 'name': 'Umber Labs', 'created': True})
        self.assertEqual(self.node('org:umber.example')['name'], 'Umber Labs')
        self.assertTrue(self.node('org:umber.example')['manual'])
        self.assertTrue(self.new('org', name='umber labs')['existing'])  # the same name: the one already there
        named = self.new('org', name='Corner Bakery')
        self.assertEqual(named['node_id'], 'org:named:corner-bakery')
        self.build()
        self.assertEqual(self.node('org:named:corner-bakery')['name'], 'Corner Bakery')
        # Someone added there joins it; while they are in it, it stays
        self.new('person', name='Bo Kent', org='Corner Bakery')
        with self.assertRaisesRegex(ValueError, 'People are in it'):
            self.app.action('graph_org_delete', {'node_id': 'org:named:corner-bakery'}, owner_dashboard=True)
        self.assertEqual(self.app.action('graph_org_delete', {'node_id': 'org:umber.example'}, owner_dashboard=True), {'deleted': 'Umber Labs'})
        self.assertIsNone(self.node('org:umber.example'))
        with self.assertRaisesRegex(ValueError, 'added yourself'):
            self.app.action('graph_org_delete', {'node_id': 'org:umber.example'}, owner_dashboard=True)

    def test_an_organisation_shows_the_messages_of_its_people(self):
        self.add(tg.mail(3, 'Ivy <ivy@globex.example>', 'sam@acme.com'), tg.mail(2, 'Jo <jo@globex.example>', 'sam@acme.com'),
                 tg.mail(1, 'Sam <sam@acme.com>', 'ivy@globex.example, jo@globex.example', ('SENT',)))
        self.build()
        found = self.app.graph_action({'node_id': 'org:globex.example', 'events': 20})
        self.assertEqual(len(found['recent']), 3)

    def test_notes_close_date_and_the_work_and_personal_lists(self):
        person = self.new('person', name='Rae Vos', email='rae@umber.example')['node_id']
        self.app.action('graph_person_edit', {'node_id': person, 'fields': {'notes': '  Met at the fair.\r\n\r\n\r\nWants a demo  '}}, owner_dashboard=True)
        self.assertEqual(self.node(person)['notes'], 'Met at the fair.\n\nWants a demo')
        org = self.new('org', name='Corner Bakery')['node_id']
        self.app.action('graph_org_edit', {'node_id': org, 'fields': {'notes': 'Pays late', 'industry': 'Food'}}, owner_dashboard=True)
        self.assertEqual((self.node(org)['profile']['notes'], self.node(org)['profile']['industry']), ('Pays late', 'Food'))
        made = self.app.action('project_save', {'name': 'Umber pilot', 'org': 'umber.example', 'close_date': '2026-12-15', 'people': [person]}, owner_dashboard=True)
        with self.store.connect() as db:
            self.assertEqual(graph.project_card(db, made['node_id'])['close_date'], '2026-12-15')
            self.assertEqual(graph.page(db, 'work')['total'], 1)
            self.assertEqual(graph.page(db, 'personal')['total'], 0)
            listed = graph.page(db, 'projects')['items'][0]
            self.assertEqual((listed['close_date'], listed['org_domain'], listed['board']), ('2026-12-15', 'umber.example', None))
        with self.assertRaisesRegex(ValueError, 'Expected close'):
            self.app.action('project_save', {'node_id': made['node_id'], 'close_date': 'soon'}, owner_dashboard=True)
        # An organisation by its name, when it has a website
        self.new('org', name='Fern Co', website='fern.example')
        self.app.action('project_save', {'node_id': made['node_id'], 'org': 'fern co'}, owner_dashboard=True)
        with self.store.connect() as db:
            self.assertEqual(graph.project_card(db, made['node_id'])['org'], 'fern.example')

    def test_what_is_refused(self):
        for kind, args, said in (('person', {'name': ' '}, 'name'), ('person', {'name': 'Al', 'email': 'not-an-address'}, 'not an email'),
                                 ('org', {'name': 'Acme', 'website': 'acme'}, 'not a website'), ('project', {'name': 'X'}, 'person or org')):
            with self.assertRaisesRegex(ValueError, said):
                self.new(kind, **args)
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_new', {'kind': 'person', 'name': 'Al'}, owner_dashboard=False)


if __name__ == '__main__':
    unittest.main()
