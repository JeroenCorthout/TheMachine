"""Custom fields and tags on Graph records: fields per record type with simple types, values kept through a rebuild
and sealed at rest, tags on the node for the lists' filter, and both carried with a record shared with the team."""
import sqlite3
import unittest

import test_crm as tc
import test_graph as tg

graph = tg.graph
import crm_fields  # noqa: E402
import shared_graph  # noqa: E402


class Fields(unittest.TestCase):
    add, build, node = tg.Graph.add, tg.Graph.build, tg.Graph.node
    setUp, act, mails = tc.Crm.setUp, tc.Crm.act, tc.Crm.mails

    def test_fields_per_record_type_with_their_types(self):
        self.mails()
        renew = self.act('graph_field_save', kind='org', name='Renewal', type='date')
        seats = self.act('graph_field_save', kind='org', name='Seats', type='number')
        plan = self.act('graph_field_save', kind='org', name='Plan', type='choice', choices='Starter\nPro\nPro')
        self.assertEqual(plan['choices'], ['Starter', 'Pro'])  # the same one once
        self.act('graph_field_save', kind='person', name='Birthday', type='date')
        with self.assertRaisesRegex(ValueError, 'already'):
            self.act('graph_field_save', kind='org', name='seats', type='text')
        with self.assertRaisesRegex(ValueError, 'two choices'):
            self.act('graph_field_save', kind='org', name='Tier', type='choice', choices='Gold')
        with self.assertRaisesRegex(ValueError, 'Pick text'):
            self.act('graph_field_save', kind='org', name='Odd', type='colour')
        org = 'org:globex.example'
        self.act('graph_field_set', node_id=org, field_id=renew['id'], value='2027-03-01')
        self.act('graph_field_set', node_id=org, field_id=seats['id'], value='1,200')
        self.act('graph_field_set', node_id=org, field_id=plan['id'], value='pro')
        for field, value, error in ((seats, 'lots', 'a number'), (renew, 'next spring', 'a date'), (plan, 'Gold', 'one of Starter, Pro')):
            with self.assertRaisesRegex(ValueError, error):
                self.act('graph_field_set', node_id=org, field_id=field['id'], value=value)
        with self.assertRaisesRegex(ValueError, 'gone'):  # a person's field on an organization
            birthday = next(f for f in self.fields('person'))
            self.act('graph_field_set', node_id=org, field_id=birthday['id'], value='2000-01-01')
        self.build()  # values stay through a rebuild
        with self.store.connect() as db:
            view = graph.detail(db, org)
        self.assertEqual([(f['name'], f['value']) for f in view['fields']], [('Renewal', '2027-03-01'), ('Seats', '1200'), ('Plan', 'Pro')])
        self.act('graph_field_set', node_id=org, field_id=seats['id'], value='')  # empty clears it
        self.act('graph_field_save', id=plan['id'], name='Plan', choices='Starter\nBusiness')  # Pro taken out: its values go
        with self.store.connect() as db:
            self.assertEqual([(f['name'], f['value']) for f in graph.detail(db, org)['fields']], [('Renewal', '2027-03-01'), ('Seats', None), ('Plan', None)])
        self.act('graph_field_delete', id=renew['id'])
        self.assertEqual([f['name'] for f in self.fields('org')], ['Seats', 'Plan'])

    def fields(self, kind):
        with self.store.connect() as db:
            return crm_fields.fields(db, kind)

    def test_values_are_sealed_at_rest_and_follow_a_person_and_a_merged_organisation(self):
        self.store = self.app.store = tg.a.Store(self.store.path, protect=True)
        self.mails()
        note = self.act('graph_field_save', kind='person', name='Met at', type='text')
        self.act('graph_field_set', node_id='email:ivy@globex.example', field_id=note['id'], value='The Ghent meetup')
        raw = sqlite3.connect(self.store.path).execute('SELECT value FROM crm_values').fetchone()[0]
        self.assertNotIn('Ghent', str(raw))  # sealed in the database file
        tier = self.act('graph_field_save', kind='org', name='Tier', type='text')
        self.act('graph_field_set', node_id='org:globex.io', field_id=tier['id'], value='Gold')
        self.act('graph_tag', node_id='org:globex.io', tag='partner')
        self.act('graph_org_merge', node_id='org:globex.io', into='org:globex.example')
        with self.store.connect() as db:
            view = graph.detail(db, 'org:globex.example')
            self.assertEqual(view['tags'], ['partner'])
            self.assertEqual([f['value'] for f in view['fields']], ['Gold'])
            self.assertEqual(graph.detail(db, 'email:ivy@globex.example')['fields'][0]['value'], 'The Ghent meetup')

    def test_tags_go_on_the_node_and_filter_the_lists(self):
        self.mails()
        self.act('graph_tag', node_id='email:ivy@globex.example', tag='VIP')
        self.act('graph_tag', node_id='email:bo@umber.example', tag='vip')  # the spelling in use is kept
        self.act('graph_tag', node_id='email:bo@umber.example', tag='Ghent')
        with self.assertRaisesRegex(ValueError, 'no commas'):
            self.act('graph_tag', node_id='email:bo@umber.example', tag='a,b')
        self.assertEqual(self.node('email:ivy@globex.example')['tags'], ['VIP'])  # at once, before a rebuild
        self.build()
        self.assertEqual(self.node('email:bo@umber.example')['tags'], ['VIP', 'Ghent'])
        with self.store.connect() as db:
            found = graph.page(db, 'people', filters={'tag': 'vip'})
            self.assertEqual(sorted(i['id'] for i in found['items']), ['email:bo@umber.example', 'email:ivy@globex.example'])
            self.assertEqual(found['total'], 2)
            self.assertEqual([i['id'] for i in graph.page(db, 'people', filters={'tag': 'Ghent'})['items']], ['email:bo@umber.example'])
            self.assertEqual(crm_fields.tags_in_use(db)[0], {'tag': 'VIP', 'count': 2})
        self.act('graph_tag', node_id='email:ivy@globex.example', tag='vip', add=False)
        self.build()
        self.assertNotIn('tags', self.node('email:ivy@globex.example'))

    def test_fields_and_tags_go_with_a_shared_record_only_cleaned(self):
        self.mails()
        f = self.act('graph_field_save', kind='org', name='Tier', type='text')
        self.act('graph_field_set', node_id='org:globex.example', field_id=f['id'], value='Gold')
        self.act('graph_tag', node_id='org:globex.example', tag='partner')
        with self.store.connect() as db:
            row = graph.resolve(db, 'org:globex.example')
            extra = crm_fields.shared(db, row)
        self.assertEqual(extra, {'fields': [{'name': 'Tier', 'type': 'text', 'value': 'Gold'}], 'tags': ['partner']})
        clean = shared_graph._clean_record({'r': 'x', 't': 'org', 'name': 'Globex', **extra})
        self.assertEqual((clean['fields'], clean['tags']), (extra['fields'], extra['tags']))
        odd = shared_graph._clean_record({'r': 'x', 't': 'org', 'name': 'G', 'fields': 'nope', 'tags': [{'x': 1}, 'ok']})
        self.assertNotIn('fields', odd)
        self.assertEqual(odd['tags'], ['ok'])
        entry = {'sources': [{'record': {'score': 3, 'fields': [{'name': 'Tier', 'type': 'text', 'value': 'Gold'}], 'tags': ['partner']}, 'member': 'm-ola', 'name': 'Ola'},
                             {'record': {'score': 1, 'fields': [{'name': 'tier', 'type': 'text', 'value': 'Silver'}, {'name': 'Seats', 'type': 'number', 'value': '9'}],
                                         'tags': ['Partner', 'beta']}, 'member': 'm-kim', 'name': 'Kim'}]}
        out = shared_graph.resolve(entry)
        self.assertEqual([(x['name'], x['value']) for x in out['fields']], [('Tier', 'Gold'), ('Seats', '9')])  # the closest one's value
        self.assertEqual(out['tags'], ['partner', 'beta'])

    def test_field_actions_are_the_owners_only(self):
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_field_save', {'kind': 'org', 'name': 'X', 'type': 'text'}, owner_dashboard=False)
        self.assertTrue({'graph_field_save', 'graph_field_delete', 'graph_field_set', 'graph_tag'} <= tg.a.OWNER_ACTIONS)


if __name__ == '__main__':
    unittest.main()
