"""The Graph as a CRM: owners from the Work Fleet, organizations with several websites (merge, split, likely doubles),
lists filtered and sorted on the Machine, and a spreadsheet imported (preview, import, undo)."""
import json
import unittest

import test_graph as tg

a, graph = tg.a, tg.graph
import crm  # noqa: E402
import crm_import  # noqa: E402
import shared_graph  # noqa: E402

DIRECTORY = {'w1': {'kind': 'work', 'name': 'Acme', 'members': [
    {'member_id': 'm-me', 'name': 'Sam', 'emails': ['sam@acme.com'], 'me': True},
    {'member_id': 'm-kim', 'name': 'Kim Vale', 'emails': ['kim@acme.com']},
    {'member_id': 'm-ola', 'name': 'Ola Berg', 'emails': ['ola@acme.com']}]},
    'f1': {'kind': 'friends', 'name': 'Friends', 'members': [{'member_id': 'm-pal', 'name': 'Pal', 'emails': ['pal@x.example']}]}}


class FakeFleet:
    def fleets(self):
        return DIRECTORY


class Crm(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node

    def setUp(self):
        tg.Graph.setUp(self)
        self.app.fleet = None

    def act(self, action, /, **args):
        return self.app.action(action, args, owner_dashboard=True)

    def mails(self):
        self.add(tg.mail(3, 'Ivy <ivy@globex.example>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'ivy@globex.example', ('SENT',)),
                 tg.mail(5, 'Jo <jo@globex.io>', 'sam@acme.com'), tg.mail(4, 'Sam <sam@acme.com>', 'jo@globex.io', ('SENT',)),
                 tg.mail(6, 'Bo <bo@umber.example>', 'sam@acme.com'), tg.mail(6, 'Sam <sam@acme.com>', 'bo@umber.example', ('SENT',)))
        self.build()

    # ---------------------------------------------------------------- owner

    def test_an_owner_is_picked_from_the_work_fleet_only_and_shows_in_the_timeline(self):
        self.mails()
        with self.assertRaisesRegex(ValueError, 'Work Fleet'):
            self.act('graph_owner', node_id='email:ivy@globex.example', owner='m-kim')  # no Fleet: nobody to pick
        self.app.fleet = FakeFleet()
        self.assertEqual([m['id'] for m in self.app.owner_choices()], ['m-kim', 'm-ola'])  # not me, not a friend
        with self.assertRaisesRegex(ValueError, 'Work Fleet'):
            self.act('graph_owner', node_id='email:ivy@globex.example', owner='m-pal')
        done = self.act('graph_owner', node_id='email:ivy@globex.example', owner='m-kim')
        self.assertEqual(done['owner_name'], 'Kim Vale')
        self.build()  # it stays through a rebuild
        view = self.node('email:ivy@globex.example')
        self.assertEqual((view['owner'], view['owner_name']), ('m-kim', 'Kim Vale'))
        self.act('graph_owner', node_id='email:ivy@globex.example', owner='me')
        self.assertIsNone(self.node('email:ivy@globex.example').get('owner'))
        with self.store.connect() as db:
            lines = graph.detail(db, 'email:ivy@globex.example')['changes']
        self.assertEqual([(c['field'], c['new']['id']) for c in lines], [('owner', 'me'), ('owner', 'm-kim')])
        self.assertEqual(lines[1]['old']['id'], 'me')
        self.assertTrue(self.act('graph_owner', node_id='email:ivy@globex.example', owner='me')['kept'])

    def test_mine_filters_the_lists_and_the_owner_goes_on_board_tiles(self):
        self.mails()
        self.app.fleet = FakeFleet()
        self.act('board_save', name='Sales', stages=['Lead', 'Won'])
        p = self.act('project_save', name='Globex pilot', board_id=1, org='globex.example', value='12000', close_date='2026-11-30')
        self.act('project_save', name='Umber deal', board_id=1, org='umber.example', value='3000', close_date='2026-12-20')
        self.act('graph_owner', node_id=p['node_id'], owner='m-ola')
        self.act('graph_owner', node_id='org:umber.example', owner='m-kim')
        with self.store.connect() as db:
            mine = graph.page(db, 'projects', filters={'owner': 'me'})
            self.assertEqual([i['name'] for i in mine['items']], ['Umber deal'])
            self.assertEqual(mine['total'], 1)
            self.assertEqual([i['name'] for i in graph.page(db, 'projects', filters={'owner': 'm-ola'})['items']], ['Globex pilot'])
            orgs = graph.page(db, 'orgs', filters={'owner': 'me'})['items']
            self.assertNotIn('org:umber.example', [o['id'] for o in orgs])
            tile = next(t for b in graph.boards(db, tg.NOW.isoformat()) for t in b['projects'] if t['id'] == p['node_id'])
            self.assertEqual(tile['owner_name'], 'Ola Berg')

    def test_a_picked_owner_goes_with_a_shared_record_only_as_a_member_id(self):
        self.assertEqual(shared_graph.owner_pick({'owner': 'm-kim', 'owner_name': 'Kim Vale'}), {'owner_pick': 'm-kim'})
        self.assertEqual(shared_graph.owner_pick({}), {})
        clean = shared_graph._clean_record({'r': 'x', 't': 'org', 'name': 'Globex', 'owner_pick': 'm-kim', 'owner_name': 'Kim Vale'})
        self.assertEqual(clean['owner_pick'], 'm-kim')
        self.assertNotIn('owner_name', clean)
        self.assertNotIn('owner_pick', shared_graph._clean_record({'r': 'x', 't': 'org', 'name': 'G', 'owner_pick': '<script>'}))
        entry = {'sources': [{'record': {'score': 5}, 'member': 'm-ola', 'name': 'Ola Berg'},
                             {'record': {'score': 1, 'owner_pick': 'm-kim'}, 'member': 'm-kim', 'name': 'Kim Vale'}]}
        out = shared_graph.resolve(entry)
        self.assertEqual((out['owner'], out['owner_name'], out['owner_hand']), ('m-kim', 'Kim', True))
        self.assertEqual(shared_graph.resolve({'sources': entry['sources'][:1]})['owner'], 'm-ola')  # else the closest, as before

    # ---------------------------------------------------------------- organizations with several websites

    def test_same_organisation_moves_people_projects_and_notes_and_split_off_undoes_it(self):
        self.mails()
        self.act('graph_org_edit', node_id='org:globex.io', fields={'notes': 'Met at the fair'})
        self.act('graph_org_edit', node_id='org:globex.example', fields={'notes': 'Main office'})
        p = self.act('project_save', name='Globex io trial', org='globex.io')
        done = self.act('graph_org_merge', node_id='org:globex.io', into='org:globex.example')
        self.assertEqual(done['node_id'], 'org:globex.example')
        self.assertIsNone(self.node('org:globex.io'))
        org = self.node('org:globex.example')
        self.assertEqual(org['domains'], ['globex.example', 'globex.io'])
        self.assertEqual(self.node('email:jo@globex.io')['org'], 'org:globex.example')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT org FROM projects WHERE id=?', (int(p['node_id'][8:]),)).fetchone()[0], 'globex.example')
            notes = json.loads(db.execute("SELECT fields FROM org_edits WHERE domain='globex.example'").fetchone()[0])['notes']
            self.assertEqual(notes, 'Main office\n\nMet at the fair')
            members = {r[0] for r in db.execute("SELECT a FROM graph_links WHERE b='org:globex.example' AND kind='member'")}
            self.assertEqual(members, {'email:ivy@globex.example', 'email:jo@globex.io'})
            self.assertEqual(graph.detail(db, 'org:globex.example')['changes'][0]['field'], 'merged')
            # enrichment looks up every website
            db.execute("DELETE FROM org_profiles")
            self.assertIn('globex.io', enrichment_due(db))
            # a new project at the other website lands on the one organization
            self.assertEqual(graph.org_card(db, 'globex.io')['node_id'], 'org:globex.example')
        self.act('graph_org_split', alias='org:globex.io')
        self.assertEqual(self.node('email:jo@globex.io')['org'], 'org:globex.io')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT org FROM projects WHERE id=?', (int(p['node_id'][8:]),)).fetchone()[0], 'globex.io')
            self.assertEqual(json.loads(db.execute("SELECT fields FROM org_edits WHERE domain='globex.example'").fetchone()[0])['notes'], 'Main office')
            self.assertEqual(crm.org_doubles(db), [])  # kept apart from then on

    def test_split_off_preserves_later_edits_to_the_combined_organization(self):
        self.mails()
        self.act('graph_org_edit', node_id='org:globex.io', fields={'notes': 'Met at the fair'})
        self.act('graph_org_edit', node_id='org:globex.example', fields={'notes': 'Main office'})
        self.act('graph_org_merge', node_id='org:globex.io', into='org:globex.example')
        self.act('graph_org_edit', node_id='org:globex.example', fields={'notes': 'Updated after the merge'})
        self.act('graph_org_split', alias='org:globex.io')
        with self.store.connect() as db:
            notes = json.loads(db.execute("SELECT fields FROM org_edits WHERE domain='globex.example'").fetchone()[0])['notes']
        self.assertEqual(notes, 'Updated after the merge')
        self.assertIsNotNone(self.node('org:globex.io'))

    def test_a_website_added_to_an_organisation_and_a_named_one_keeps_its_name(self):
        self.mails()
        made = self.act('graph_new', kind='org', name='Globex Group')
        self.act('graph_org_merge', node_id='org:globex.example', into=made['node_id'])
        self.build()
        self.assertEqual(self.node('org:globex.example')['name'], 'Globex Group')  # the one with a website stays, with the name picked
        self.act('graph_org_merge', node_id='org:umber.example', website='https://umber.io')
        self.assertEqual(self.node('org:umber.example')['domains'], ['umber.example', 'umber.io'])
        with self.assertRaisesRegex(ValueError, 'already'):
            self.act('graph_org_merge', node_id='org:umber.example', website='umber.io')
        with self.assertRaisesRegex(ValueError, 'free email'):
            self.act('graph_org_merge', node_id='org:umber.example', website='gmail.com')
        # a later address at the added website joins the organization
        self.add(tg.mail(1, 'Lu <lu@umber.io>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'lu@umber.io', ('SENT',)))
        self.build()
        self.assertEqual(self.node('email:lu@umber.io')['org'], 'org:umber.example')

    def test_likely_doubles_are_offered_until_merged_or_kept_apart(self):
        self.mails()
        with self.store.connect() as db:
            pairs = crm.org_doubles(db)
            self.assertEqual([(p['a']['id'], p['b']['id'], p['why']) for p in pairs],
                             [('org:globex.example', 'org:globex.io', 'Same name')])
        self.act('graph_org_apart', a='org:globex.io', b='org:globex.example')
        with self.store.connect() as db:
            self.assertEqual(crm.org_doubles(db), [])

    # ---------------------------------------------------------------- filters and sorts

    def test_projects_people_and_organisations_filter_and_sort_by_their_projects(self):
        self.mails()
        self.act('board_save', name='Sales', stages=['Lead', 'Proposal', 'Won'])
        self.act('board_save', name='Hiring', stages=['Applied', 'Hired'])
        self.act('project_save', name='Globex pilot', board_id=1, stage='Proposal', org='globex.example', value='12000', close_date='2026-11-30',
                 people=['email:ivy@globex.example'])
        self.act('project_save', name='Umber deal', board_id=1, org='umber.example', value='3000', close_date='2026-12-20', people=['email:bo@umber.example'])
        self.act('project_save', name='No value', board_id=2)
        with self.store.connect() as db:
            names = lambda name, **f: [i['name'] for i in graph.page(db, name, f.pop('sort', ''), filters=f)['items']]
            self.assertEqual(names('projects', sort='value'), ['Globex pilot', 'Umber deal', 'No value'])
            self.assertEqual(names('projects', sort='close'), ['Globex pilot', 'Umber deal', 'No value'])
            self.assertEqual(names('projects', board='1', stage='proposal'), ['Globex pilot'])
            self.assertEqual(names('projects', value_min='5000'), ['Globex pilot'])
            self.assertEqual(names('projects', close_from='2026-12-01', close_to='2026-12-31'), ['Umber deal'])
            self.assertEqual(names('projects', org='Umber'), ['Umber deal'])  # a name: one in the Graph
            self.assertEqual(names('projects', org='Nobody'), [])
            self.assertEqual(names('projects', org='umber.example'), ['Umber deal'])
            self.assertEqual(names('people', value_min='5000'), ['Ivy'])
            self.assertEqual(names('people', sort='value')[:2], ['Ivy', 'Bo'])
            self.assertEqual([i['id'] for i in graph.page(db, 'orgs', 'close', filters={'board': '1'})['items']], ['org:globex.example', 'org:umber.example'])
            self.assertEqual(names('people', org='org:globex.example'), ['Ivy'])
            found = graph.page(db, 'projects', filters={'value_min': '1'})
            self.assertEqual((found['total'], found['filters']), (2, {'value_min': 1.0}))
            with self.assertRaisesRegex(ValueError, 'a number'):
                graph.page(db, 'projects', filters={'value_min': 'lots'})
            with self.assertRaisesRegex(ValueError, 'a date'):
                graph.page(db, 'projects', filters={'close_to': 'soon'})

    # ---------------------------------------------------------------- import

    def test_import_guesses_a_company_column_as_the_organization(self):
        for header in ('Company', 'Organisation', 'Organization', 'Account', 'Firm', 'Company name'):
            self.assertEqual(crm_import.guess('person', ['Full name', 'E-mail', header, 'Job title']), {0: 'name', 1: 'email', 2: 'org', 3: 'title'})
            self.assertEqual(crm_import.guess('project', ['Deal', header, 'Value'])[1], 'org')
            self.assertEqual(crm_import.guess('org', [header, 'Website'])[0], 'name')
        rows = [['Ann', 'ann@x.example', '+32 470 12 34 56'], ['Bo', 'Bo <bo@y.example>', '0470 123 456']]
        self.assertEqual(crm_import.guess('person', ['Wie', 'Adres', 'Nummer'], rows), {1: 'email', 2: 'phone'})  # by the values

    def test_import_previews_new_matches_and_skips_then_imports_and_undoes(self):
        self.mails()
        text = ('First name;Last name;E-mail;Company;Job title;Phone\n'
                'Ivy;Stone;ivy@globex.example;Globex;CTO;\n'
                'Nia;Holm;nia@new.example;Newco;CEO;+32 470 00 00 00\n'
                'Nia;Holm;nia@new.example;Newco;CEO;\n'
                ';;not-an-address;;;\n'
                'Rex;;;;;\n')
        with self.store.connect() as db:
            seen = crm_import.preview(db, 'person', text)
        self.assertEqual([c['field'] for c in seen['columns']], ['first_name', 'last_name', 'email', 'org', 'title', 'phone'])
        self.assertEqual(seen['counts'], {'new': 2, 'match': 1, 'skip': 2})
        self.assertEqual([(r['status'], r.get('why')) for r in seen['rows']],
                         [('match', 'Same email'), ('new', None), ('skip', 'Twice in the file'), ('skip', 'Not an email address'), ('new', None)])
        self.assertEqual(seen['rows'][0]['match_name'], 'Ivy')
        done = self.act('graph_import_run', kind='person', text=text, name='contacts.csv')
        self.assertEqual((done['new'], done['updated'], done['skipped']), (2, 1, 2))
        self.assertEqual(self.node('email:ivy@globex.example')['name'], 'Ivy')  # a match keeps its name
        with self.store.connect() as db:
            ivy = graph.detail(db, 'email:ivy@globex.example')
            self.assertEqual(graph.person_edits_for(db, ['email:ivy@globex.example']).get('title'), 'CTO')
            nia = graph.detail(db, 'email:nia@new.example')
            self.assertEqual((nia['name'], nia['org_name']), ('Nia Holm', 'Newco'))
            self.assertEqual(crm_import.last(db)['id'], done['import_id'])
        del ivy
        self.act('graph_import_undo', import_id=done['import_id'])
        self.assertIsNone(self.node('email:nia@new.example'))
        with self.store.connect() as db:
            self.assertIsNone(graph.person_edits_for(db, ['email:ivy@globex.example']).get('title'))
            self.assertIsNone(crm_import.last(db))
        with self.assertRaisesRegex(ValueError, 'last import'):
            self.act('graph_import_undo', import_id=done['import_id'])

    def test_import_of_organisations_and_projects_matches_any_website_and_only_the_last_undoes(self):
        self.mails()
        self.act('graph_org_merge', node_id='org:globex.io', into='org:globex.example')
        self.act('board_save', name='Sales', stages=['Lead', 'Won'])
        orgs = 'Name,Website,Industry\nGlobex,globex.io,Software\nTeal Co,https://teal.example,Retail\n'
        done = self.act('graph_import_run', kind='org', text=orgs)
        self.assertEqual((done['new'], done['updated']), (1, 1))
        self.assertEqual(self.node('org:globex.example')['profile'].get('industry'), 'Software')
        self.assertEqual(self.node('org:teal.example')['name'], 'Teal Co')
        projects = 'Deal,Company,Pipeline,Stage,Amount,Close date,Notes\nTeal rollout,teal.example,Sales,won,"8,000",2026-12-01,Signed soon\nBad,,,,,,\n'
        with self.store.connect() as db:
            self.assertEqual(crm_import.preview(db, 'project', projects)['counts'], {'new': 2, 'match': 0, 'skip': 0})
        second = self.act('graph_import_run', kind='project', text=projects)
        self.assertEqual(second['new'], 2)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE name='Teal rollout'").fetchone()
            self.assertEqual((row['org'], row['value'], row['stage'], row['close_date'], row['notes']), ('teal.example', 8000.0, 'Won', '2026-12-01', 'Signed soon'))
            sealed = db.execute('SELECT undo FROM graph_imports WHERE id=?', (second['import_id'],)).fetchone()[0]
            self.assertIn('project:', sealed)
        with self.assertRaisesRegex(ValueError, 'last import'):
            self.act('graph_import_undo', import_id=done['import_id'])
        self.act('graph_import_undo', import_id=second['import_id'])
        with self.store.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM projects WHERE name='Teal rollout'").fetchone())
        with self.store.connect() as db:
            self.assertEqual(crm_import.preview(db, 'org', 'Industry\nRetail\n')['problem'], 'Pick the column with the name')
        with self.assertRaisesRegex(ValueError, 'name'):
            self.act('graph_import_run', kind='org', text='Industry\nRetail\n')

    def test_import_suggests_easy_fixes_and_applies_only_the_ones_kept(self):
        self.mails()
        self.app.config['country'] = 'BE'
        text = ('Name,Email,Company,Phone,Tags\n'
                '"JANE  VAN DAM",jane@gmial.com,acme robotics bv,0475 12 34 56,"Partner; Pilot"\n'
                ',Bo Lund <bo@lund.example>,Lund,+46 70 123 45 67,\n'
                'Jane van Dam,jane@gmail.com,,,Board\n')
        with self.store.connect() as db:
            plain = crm_import.preview(db, 'person', text, region='BE')
            seen = crm_import.preview(db, 'person', text, fixes={'types': 'all'}, region='BE')
        self.assertEqual(plain['columns'][4]['field'], 'tags')
        self.assertEqual([r['status'] for r in plain['rows']], ['new', 'skip', 'new'])  # nothing fixed unless kept
        self.assertEqual({t['type']: t['count'] for t in seen['fix_types']}, {'case': 2, 'split': 1, 'email': 1, 'phone': 2, 'tags': 1, 'merge': 1})
        fixes = {f['id']: (f['before'], f['after']) for f in seen['fixes']}
        self.assertEqual(fixes['2:name'], ('JANE  VAN DAM', 'Jane van Dam'))
        self.assertEqual(fixes['2:email'], ('jane@gmial.com', 'jane@gmail.com'))
        self.assertEqual(fixes['2:org'], ('acme robotics bv', 'Acme Robotics BV'))
        self.assertEqual(fixes['2:phone'], ('0475 12 34 56', '+32475123456'))  # the country from the Machine
        self.assertEqual(fixes['3:email'], ('Bo Lund <bo@lund.example>', 'bo@lund.example'))
        self.assertEqual(fixes['4:merge'][1], 'Merged into line 2 (adds tags)')
        self.assertEqual([(r['status'], r.get('why')) for r in seen['rows']], [('new', None), ('new', None), ('skip', 'Merged into line 2')])
        self.assertEqual(seen['rows'][1]['record']['name'], 'Bo Lund')
        done = self.act('graph_import_run', kind='person', text=text, fixes={'types': 'all', 'off': ['2:org']})
        self.assertEqual((done['new'], done['skipped']), (2, 1))
        with self.store.connect() as db:
            jane = graph.detail(db, 'email:jane@gmail.com')
            self.assertEqual((jane['name'], jane['org_name']), ('Jane van Dam', 'acme robotics bv'))  # the one left out
            self.assertEqual(jane.get('tags'), ['Partner', 'Pilot', 'Board'])
            self.assertEqual(graph.person_edits_for(db, ['email:jane@gmail.com']).get('phones'), {'added': ['+32475123456'], 'removed': []})
        self.act('graph_import_undo', import_id=done['import_id'])
        with self.store.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM crm_tags').fetchone())

    def test_import_fixes_dates_values_and_stages_by_rules(self):
        self.act('board_save', name='Sales', stages=['Lead', 'Proposal', 'Negotiation', 'Won'])
        text = ('Deal,Pipeline,Stage,Amount,Close date\n'
                'A,Sales,proposal sent,€12k,25/12/2026\n'
                'B,Sales,Negotation,"12.500,50 EUR",03/04/2027\n'
                'C,Sales,Verbal yes,about five,Dec 1 2026\n')
        with self.store.connect() as db:
            seen = crm_import.preview(db, 'project', text, fixes={'types': 'all'}, region='US')
        fixes = {f['id']: f['after'] for f in seen['fixes']}
        self.assertEqual(fixes, {'2:stage': 'Proposal', '2:value': '12000', '2:close_date': '2026-12-25', '3:stage': 'Negotiation',
                                 '3:value': '12500.5', '3:close_date': '2027-04-03', '4:close_date': '2026-12-01'})  # 25/12 says day first
        with self.store.connect() as db:
            self.assertEqual([(c['line'], c['field']) for c in crm_import.ai_evidence(db, 'project', text, 'US')['cells']], [(4, 'stage'), (4, 'value')])

    def test_import_check_by_the_owners_own_model(self):
        self.act('board_save', name='Sales', stages=['Lead', 'Proposal', 'Won'])
        self.app.config['country'] = 'BE'
        text = 'Klant,Bedrag,Fase,Wie\nTeal,ongeveer vijf,Mondeling ja,Ann\nUmber,8000,Lead,Bo\n'
        self.app.model_room = lambda most=80: True
        ready = a.model_ready
        try:
            a.model_ready = lambda config=None, run=None: False
            self.assertEqual(self.act('graph_import_ai', kind='project', text=text), {'ai': False, 'why': 'model'})
            a.model_ready = lambda config=None, run=None: True
            started = self.act('graph_import_ai', kind='project', text=text)
        finally:
            a.model_ready = ready
        self.assertTrue(started['ai'])
        session = a.PROFILE_SESSION_PREFIX + started['run_id']
        sent = []
        self.app.profile_step(lambda request: sent.append(request) or {'status': 'admitted'})
        self.assertIn('CSV import check', sent[0]['message'])
        evidence = self.store.profile_evidence(1, session)  # headers, a few values and the unclear cells: not the file
        self.assertEqual([c['header'] for c in evidence['columns']], ['Klant', 'Bedrag', 'Fase', 'Wie'])
        self.assertEqual(evidence['stages'], ['Lead', 'Proposal', 'Won', 'Stalled or declined'])
        self.store.save_profile({'mapping': {'0': 'name', '1': 'value', '2': 'stage', '3': 'nope'},
                                 'reasons': {'0': 'Klant is Dutch for client: the deal is named after them', '1': 'Bedrag means amount'},
                                 'fixes': [{'line': 2, 'field': 'value', 'after': '5000', 'why': 'vijf is five, about 5k'},
                                           {'line': 2, 'field': 'stage', 'after': 'Signed', 'why': 'not a stage'},
                                           {'line': 9, 'field': 'value', 'after': '9000', 'why': 'not shown'}]}, session)
        self.app.profile_step(lambda request: {'status': 'ok'})
        self.assertEqual(self.act('graph_import_ai_status', run_id=started['run_id']), {'status': 'ok', 'columns': 3, 'fixes': 1})
        with self.store.connect() as db:
            rules = crm_import.preview(db, 'project', text)  # Klant is no header the rules know
        self.assertEqual((rules['columns'][0]['field'], rules['problem'], rules['total']), (None, 'Pick the column with the name', 0))
        seen = self.act('graph_import_preview', kind='project', text=text, ai_run=started['run_id'], fixes={'types': 'all'})
        self.assertEqual([c['field'] for c in seen['columns']], ['name', 'value', 'stage', None])
        self.assertEqual(seen['columns'][0]['reason'], 'Klant is Dutch for client: the deal is named after them')
        self.assertEqual([(f['id'], f['after'], f.get('ai')) for f in seen['fixes']], [('2:value', '5000', True)])
        other = self.act('graph_import_preview', kind='project', text=text + 'Ash,1,Lead,Cy\n', ai_run=started['run_id'])
        self.assertIsNone(other['columns'][0]['field'])  # a different file: the check isn't for it

    def test_import_undo_keeps_later_owner_edits(self):
        self.mails()
        result = self.act('graph_import_run', kind='person', text='Name,Email,Title\nIvy,ivy@globex.example,Imported title\n')
        self.act('graph_person_edit', node_id='email:ivy@globex.example', fields={'title': 'Owner correction'})
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.act('graph_import_undo', import_id=result['import_id'])
        with self.store.connect() as db:
            fields = json.loads(db.execute('SELECT fields FROM person_edits WHERE identifier=?', ('email:ivy@globex.example',)).fetchone()[0])
        self.assertEqual(fields['title'], 'Owner correction')

    def test_import_undo_still_works_after_unrelated_edits(self):
        self.mails()
        result = self.act('graph_import_run', kind='person', text='Name,Email,Title\nIvy,ivy@globex.example,Imported title\n')
        self.act('graph_person_edit', node_id='email:jo@globex.io', fields={'title': 'Other contact'})
        self.act('graph_import_undo', import_id=result['import_id'])
        with self.store.connect() as db:
            ivy = db.execute('SELECT fields FROM person_edits WHERE identifier=?', ('email:ivy@globex.example',)).fetchone()
            jo = db.execute('SELECT fields FROM person_edits WHERE identifier=?', ('email:jo@globex.io',)).fetchone()
        self.assertIsNone(ivy)
        self.assertEqual(json.loads(jo[0])['title'], 'Other contact')

    def test_import_undo_does_not_delete_a_new_record_edited_afterwards(self):
        result = self.act('graph_import_run', kind='person', text='Name,Email\nNew contact,new@example.net\n')
        self.act('graph_person_edit', node_id='email:new@example.net', fields={'notes': 'Keep this correction'})
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.act('graph_import_undo', import_id=result['import_id'])
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM manual_nodes WHERE identifier=?', ('email:new@example.net',)).fetchone())

    def test_only_the_owner_on_the_dashboard(self):
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_import_run', {'kind': 'person', 'text': 'Name\nA\n'}, owner_dashboard=False)
        self.assertTrue({'graph_owner', 'graph_import_run', 'graph_org_merge'} <= a.OWNER_ACTIONS)


def enrichment_due(db):
    return a.enrichment.due(db, tg.NOW.isoformat(), 50)


if __name__ == '__main__':
    unittest.main()
