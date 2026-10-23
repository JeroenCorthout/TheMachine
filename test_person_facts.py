"""Facts about people that connectors read while they run (job title, place, links, time zone, their own words),
kept with where they came from and when; what someone says about themselves beats a record, which beats a guess."""
import json
import types
import unittest

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment
x = a.load_connector('x')
linkedin = a.load_connector('linkedin')
NOW = tg.NOW.isoformat()


class PersonFacts(unittest.TestCase):
    add, build, node = tg.Graph.add, tg.Graph.build, tg.Graph.node

    def setUp(self):
        tg.Graph.setUp(self)

    def facts(self, identifier):
        with self.store.connect() as db:
            return {r[0]: tuple(r[1:]) for r in db.execute('SELECT field,value,rank,source FROM person_facts WHERE identifier=?', (identifier,))}

    def test_a_fact_they_state_beats_a_record_and_a_record_beats_a_guess(self):
        self.add(tg.mail(2, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'lee@globex.com', ('SENT',)))
        with self.store.connect() as db:
            # The look-up's guess (person_profiles) says Sales Rep in Springfield.
            db.execute("INSERT INTO person_profiles(identifier,status,profile,fetched_at) VALUES('email:lee@globex.com','found',?,?)",
                       (json.dumps({'title': 'Sales Rep', 'city': 'Springfield', 'country': 'US'}), NOW))
            enrichment.offer_facts(db, [(['email:lee@globex.com'], {'title': 'Account Manager', 'phone': '+1 555 0100'}, enrichment.RECORD)], 'salesflare', NOW)
            enrichment.offer_facts(db, [(['email:lee@globex.com'], {'title': 'Head of Sales', 'timezone': 'America/Chicago'}, enrichment.STATED)], 'slack', NOW)
            # A record later doesn't replace what Lee said; it does fill a field Lee didn't.
            self.assertEqual(enrichment.offer_facts(db, [(['email:lee@globex.com'], {'title': 'Intern', 'company': 'Globex Inc'}, enrichment.RECORD)], 'google', NOW), 1)
        self.assertEqual(self.facts('email:lee@globex.com')['title'], ('Head of Sales', 2, 'slack'))
        self.build()
        profile = self.node('email:lee@globex.com')['profile']
        self.assertEqual((profile['title'], profile['phone'], profile['timezone'], profile['company'], profile['city']),
                         ('Head of Sales', '+1 555 0100', 'America/Chicago', 'Globex Inc', 'Springfield'))
        self.assertEqual(profile['sources']['title'], {'from': 'Slack', 'at': NOW[:10], 'own': True})
        self.assertEqual(profile['sources']['phone']['from'], 'Salesflare')
        self.assertNotIn('city', profile['sources'])  # the look-up's own fields keep no source of their own
        # Lee clears their Slack title: it goes, and the record shows again after the next read.
        with self.store.connect() as db:
            enrichment.offer_facts(db, [(['email:lee@globex.com'], {'title': ''}, enrichment.STATED)], 'slack', NOW)
        self.assertNotIn('title', self.facts('email:lee@globex.com'))

    def test_only_facts_in_their_own_shape_are_kept(self):
        clean = enrichment.clean_fact
        self.assertEqual(clean('title', '  Head\nof\tSales \x07'), 'Head of Sales')
        self.assertLessEqual(len(clean('bio', 'word ' * 200)), 400)
        self.assertEqual(clean('website', 'https://lee.example/about'), 'https://lee.example/about')
        self.assertIsNone(clean('website', 'javascript:alert(1)'))
        self.assertEqual(clean('linkedin_url', 'https://www.linkedin.com/in/lee-parker-1?trk=x'), 'https://www.linkedin.com/in/lee-parker-1')
        self.assertIsNone(clean('linkedin_url', 'https://evil.example/in/lee'))
        self.assertEqual(clean('x', '@lee_p'), 'lee_p')
        self.assertIsNone(clean('x', 'lee parker'))
        self.assertIsNone(clean('phone', 'call me'))
        self.assertEqual(clean('timezone', 'Europe/Brussels'), 'Europe/Brussels')
        self.assertIsNone(clean('timezone', 'Mars/Olympus'))
        self.assertIsNone(clean('password', 'x'))

    def test_x_keeps_what_the_user_objects_it_read_say_about_people_in_the_page(self):
        data = {'profiles': {
            '101': {'screen_name': 'lee_p', 'bio': 'Sales at Globex. https://lee.example', 'location': 'Springfield', 'website': 'https://lee.example',
                    'photo': 'https://pbs.twimg.com/profile_images/123/abc_normal.jpg'},
            '202': {'screen_name': 'kim', 'bio': '', 'photo': 'https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png'},
            '303': {'screen_name': 'stranger', 'bio': 'not in this page'}}}
        offers = x.profiles(data, {'x:101', 'x:202'})
        self.assertEqual([o['ids'] for o in offers], [['x:101'], ['x:202']])
        self.assertEqual(offers[0]['photo'], 'https://pbs.twimg.com/profile_images/123/abc_200x200.jpg')
        self.assertIsNone(offers[1]['photo'])  # X's default egg isn't a photo
        self.assertEqual(offers[0]['facts']['location'], 'Springfield')
        # Through a sync: the reader's page goes in, the facts and their own photo come out, nothing else is asked.
        page = {'account': {'screen_name': 'sam'}, 'mentions': [{'id': '9', 'at': 1792700000000, 'text': '@sam hi', 'author': {'id': '101', 'name': 'Lee Parker', 'screen_name': 'lee_p'}}],
                'dms': [], 'state': {}, **data}
        helpers = types.SimpleNamespace(**{k: getattr(a.CONNECTOR_HELPERS, k) for k in vars(a.CONNECTOR_HELPERS)})
        helpers.local_tz = lambda: a.dt.timezone.utc
        a.now = lambda: '2026-10-16T12:00:00+00:00'
        x.sync(self.store, {}, {'id': 'me-x', 'handle': 'sam', 'active_hours': [0, 24]}, helpers, source=types.SimpleNamespace(page=lambda since, state: page))
        self.assertEqual(self.facts('x:101')['bio'], ('Sales at Globex. https://lee.example', 2, 'x'))
        self.assertEqual(self.facts('x:303'), {})
        with self.store.connect() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT identifier,url,chosen,source FROM contact_photo_links')],
                             [('x:101', 'https://pbs.twimg.com/profile_images/123/abc_200x200.jpg', 1, 'x')])

    def test_linkedin_keeps_the_headline_link_and_photo_of_the_person_in_a_conversation(self):
        page = {'profiles': {'urn:li:msg_conversation:(urn:li:fsd_profile:me,2-abc)': {
                    'headline': 'Engineering at Globex', 'url': 'https://www.linkedin.com/in/robin-vale',
                    'photo': 'https://media.licdn.com/dms/image/v2/abc/profile-displayphoto-shrink_200_200/0/1?e=1&v=beta&t=x'},
                 'not-a-conversation': {'headline': 'x'}}}
        offers = linkedin.profiles(page)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]['ids'], ['li:urn:li:msg_conversation:(urn:li:fsd_profile:me,2-abc)'])
        self.assertTrue(offers[0]['photo'].startswith('https://media.licdn.com/dms/image/'))
        self.assertIsNone(linkedin.profiles({'profiles': {'urn:li:msg_conversation:1': {'photo': 'https://evil.example/p.jpg'}}})[0]['photo'])
        # Their own words show on their page (read there, never copied into the graph).
        self.add(tg.linkedin(3, 'c-robin', 'Robin Vale', 'Hi Sam'), tg.linkedin(2, 'c-robin', 'Robin Vale', 'Hi Robin', mine=True))
        with self.store.connect() as db:
            enrichment.offer_facts(db, [(['li:c-robin'], {'headline': 'Engineering at Globex', 'bio': 'Builds APIs.'}, enrichment.STATED)], 'linkedin', NOW)
        self.build()
        self.assertNotIn('headline', json.dumps(self.node('li:c-robin')))
        with self.store.connect() as db:
            view = graph.detail(db, 'li:c-robin')
        self.assertEqual((view['headline'], view['bio']['text'], view['bio']['from']), ('Engineering at Globex', 'Builds APIs.', 'LinkedIn'))

    def test_slack_profiles_give_title_phone_and_time_zone_from_the_lookup_already_done(self):
        slack = object.__new__(a.Slack); slack.account = {'user_id': 'U0OWNER'}
        calls = []
        slack.get = lambda method, params: calls.append(method) or {'user': {'id': params['user'], 'tz': 'Europe/Brussels', 'profile': {
            'real_name': 'Ari Moss', 'title': 'Designer', 'phone': '+32 470 00 00 01'}}}
        a.remember_slack_names(self.store, slack, [{'metadata': {'user_id': 'U0ARI', 'channel_name': 'general'}}])
        self.assertEqual(calls, ['users.info'])  # the same one lookup as for their name
        self.assertEqual(self.facts('slack:U0ARI'), {'title': ('Designer', 2, 'slack'), 'phone': ('+32 470 00 00 01', 2, 'slack'),
                                                     'timezone': ('Europe/Brussels', 2, 'slack')})
        self.assertIsNone(enrichment.slack_facts({'id': 'B1', 'is_bot': True, 'profile': {'title': 'Bot'}}))

    def test_google_contacts_give_job_title_company_and_phone_with_the_scopes_they_have(self):
        contact = {'emailAddresses': [{'value': 'Kim@Family.example'}], 'phoneNumbers': [{'canonicalForm': '+32470000001'}],
                   'organizations': [{'name': 'Old Co', 'title': 'Intern', 'endDate': {'year': 2020}},
                                     {'name': 'Hooli', 'title': 'CTO', 'metadata': {'primary': True, 'source': {'type': 'CONTACT'}}}]}
        self.assertEqual(enrichment.google_facts(contact), [(['email:kim@family.example'], {'title': 'CTO', 'company': 'Hooli'}, enrichment.RECORD),
                                                            (['email:kim@family.example'], {'phone': '+32470000001'}, enrichment.RECORD)])
        asked, facts = [], []
        def read(config, account, api, version, method, params, scope):
            asked.append((method, params.get('personFields') or params.get('readMask'), scope))
            return {'connections': [contact]} if method == 'people.connections.list' else {}
        a.google_photo_offers({}, {'email': 'sam@acme.com'}, read=read, facts=facts)
        self.assertEqual(len(facts), 2)
        self.assertEqual([s for _, _, s in asked], [s for _, _, s, _ in a.GOOGLE_PHOTO_LISTS])  # the same lists and scopes as for photos
        self.assertNotIn('organizations', asked[2][1])  # other contacts don't have them: that list is asked as before

    def test_facts_already_in_stored_signals_are_kept_once_and_go_with_the_person(self):
        self.add(tg.linkedin(3, 'c-robin', 'Robin Vale', 'Hi Sam', headline='Engineering at Globex'),
                 {'account_id': 'crm', 'source_id': 'sf1', 'kind': 'salesflare.email', 'occurred_at': tg.at(2), 'title': 'Deal', 'body': 'x', 'deleted': False,
                  'metadata': {'contacts': [{'email': 'Lee@globex.com', 'name': 'Lee Parker', 'role': 'President'}]}})
        self.assertEqual(enrichment.facts_from_signals(self.store.connect, NOW), 2)
        self.assertEqual(enrichment.facts_from_signals(self.store.connect, NOW), 0)  # once
        self.assertEqual(self.facts('li:c-robin')['headline'], ('Engineering at Globex', 2, 'linkedin'))
        self.assertEqual(self.facts('email:lee@globex.com')['title'], ('President', 1, 'salesflare'))
        self.assertIn('person_facts', a.message_seal.DERIVED)  # sealed with message contents
        self.add(tg.mail(2, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'lee@globex.com', ('SENT',)))
        self.build()
        with self.store.connect() as db:
            graph.delete_person(db, 'email:lee@globex.com', tg.CONFIG, NOW)
            enrichment.offer_facts(db, [(['email:lee@globex.com'], {'title': 'CEO'}, enrichment.STATED)], 'slack', NOW)  # deleted for good: not again
        self.assertEqual(self.facts('email:lee@globex.com'), {})


if __name__ == '__main__':
    unittest.main()
