import http.server
import json
import threading
import unittest
from pathlib import Path

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment
KEY = 'not-a-real-key'  # a test value; a realistic-looking one trips secret scanners


class Service(http.server.BaseHTTPRequestHandler):
    """A fake enrichment service: Globex is known, nobody else; a wrong key is refused."""
    seen = []

    def do_GET(self):
        Service.seen.append((self.path, self.headers.get('Authorization')))
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            self.send_response(401); self.end_headers(); return
        if 'mismatch.example' in self.path:  # the service matched a page that merely links to this domain
            body = json.dumps({'name': 'Srsr company', 'website': 'https://mismatch.example/blog/post', 'about_us': 'srsr'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body); return
        if 'globex.com' not in self.path:
            self.send_response(404); self.end_headers(); return
        body = json.dumps({'name': 'Globex', 'industry': 'Manufacturing', 'size': '51-200', 'headquarters': 'Springfield', 'founded': '1989',
                           'about_us': 'Globex makes   everything.', 'linkedin_url': 'https://www.linkedin.com/company/globex'}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


class Enrichment(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node
    ids = tg.Graph.ids
    def setUp(self):
        tg.Graph.setUp(self)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.key = Path(self.temp.name) / 'enrich.key'; self.key.write_text(KEY + '\n')
        self.config = {'enrichment': {'url': f'http://127.0.0.1:{self.server.server_port}/enrich/companies?domain={{domain}}',
                                      'headers': {'Authorization': 'Bearer {key}'}, 'key_file': str(self.key)}}
        Service.seen.clear()

    def test_any_service_shape_becomes_one_small_profile(self):
        flat = enrichment.normalise({'name': 'Acme', 'employees_num': 7, 'size': '2-10', 'industry': 'Software', 'founded': 2014,
                                     'headquarters': 'Antwerp', 'linkedin_id': 'acme', 'about_us': 'word ' * 100})
        self.assertEqual((flat['employees'], flat['founded'], len(flat['about']) <= 300, flat['about'][-6:]), (7, 2014, True, ' word…'))
        wrapped = enrichment.normalise({'organization': {'name': 'Initech', 'industry': 'software', 'estimated_num_employees': '120',
                                                         'linkedin_url': 'http://linkedin.com/company/initech', 'founded_year': 1998}})
        self.assertEqual(wrapped, {'name': 'Initech', 'industry': 'software', 'employees': 120, 'founded': 1998, 'linkedin_url': 'http://linkedin.com/company/initech'})
        nested = enrichment.normalise({'data': {'name': 'Hooli', 'category': {'industry': 'Internet'}, 'metrics': {'employees': 900},
                                                'geo': {'city': 'Palo Alto'}, 'linkedin': {'handle': 'company/hooli'}}})
        self.assertEqual((nested['industry'], nested['employees'], nested['location'], nested['linkedin_url']),
                         ('Internet', 900, 'Palo Alto', 'https://www.linkedin.com/company/hooli'))
        self.assertEqual(enrichment.normalise({'data': {'custom_sector': 'Retail', 'name': 'Shop'}}, {'industry': ['custom_sector']})['industry'], 'Retail')
        self.assertIsNone(enrichment.normalise({'status': 'not_found'}))
        self.assertEqual(enrichment.summary({'industry': 'Software', 'size': '2-10', 'location': 'Antwerp', 'founded': 2014}),
                         'Software, 2-10 people, Antwerp, founded 2014')
        preset = enrichment.settings({'enrichment': {'provider': 'apollo', 'key_env': 'X'}})
        self.assertEqual((preset['url'], preset['headers']), ('https://api.apollo.io/api/v1/organizations/enrich?domain={domain}', {'X-Api-Key': '{key}'}))
        self.assertIsNone(enrichment.settings({'enrichment': {'url': 'https://example.com/no-domain'}}))
        self.assertIsNone(enrichment.settings({}))

    def test_a_logo_is_kept_only_as_a_plain_https_address(self):
        logo = 'https://media.licdn.com/dms/image/v2/C4D0BAQF/company-logo_200_200/0/1?e=2147483647&v=beta&t=abc'
        self.assertEqual(enrichment.normalise({'name': 'Attio', 'image_url': logo})['logo'], logo)
        self.assertEqual(enrichment.normalise({'organization': {'name': 'Initech', 'logo_url': ' https://cdn.example/i.png '}})['logo'], 'https://cdn.example/i.png')
        for bad in ('http://cdn.example/i.png', 'javascript:alert(1)', 'https://cdn.example/a b.png', 'https://cdn.example/"><script>',
                    'data:image/png;base64,AAAA', '//cdn.example/i.png', ['https://cdn.example/i.png'], 'https://' + 'a' * 600 + '.example/i.png'):
            self.assertNotIn('logo', enrichment.normalise({'name': 'Acme', 'logo': bad}), bad)
        self.assertIsNone(enrichment.normalise({'image_url': logo}))  # a logo alone is not a profile

    def test_an_answer_about_another_company_counts_as_not_found(self):
        other = enrichment.another_company
        # Real shapes: a one-person page that lists a blog post on the domain, a site on a hosting subdomain, a booking page.
        self.assertTrue(other({'name': 'Srsr company', 'website': 'https://expandi.io/blog/manage-multiple-linkedin-accounts/#:~:text=x'}, 'expandi.io'))
        self.assertTrue(other({'name': 'River-End Trading Est.', 'website': 'https://3rdmhldg.softr.io/subsdiaries'}, 'softr.io'))
        self.assertTrue(other({'name': 'HERMANN STITZ & CO. KG', 'website': 'https://zeeg.me/hermannstitz'}, 'zeeg.me'))
        # The company itself: its home page (with tracking or a language), or a page while the name matches the domain.
        for profile, domain in (({'name': 'Zapier', 'website': 'https://zapier.com/?utm_source=linkedin'}, 'zapier.com'),
                                ({'name': 'Bloomreach', 'website': 'https://www.bloomreach.com/en'}, 'bloomreach.com'),
                                ({'name': 'Lyvia', 'website': 'lyviagroup.com'}, 'lyviagroup.com'),
                                ({'name': 'Apple', 'website': 'http://www.apple.com/careers'}, 'apple.com'),
                                ({'name': 'Qatalog (Acquired by ClickUp)', 'website': 'https://clickup.com/brain'}, 'clickup.com'),
                                ({'name': 'Renamed Co', 'website': 'https://www.oldname.com/'}, 'oldname.com'),
                                ({'name': 'No Site'}, 'nosite.com')):
            self.assertFalse(other(profile, domain), domain)

    def test_fetch_sends_the_key_and_never_shows_it(self):
        cfg = enrichment.settings(self.config)
        self.assertEqual(enrichment.fetch(cfg, 'globex.com')['size'], '51-200')
        self.assertEqual(Service.seen[-1], ('/enrich/companies?domain=globex.com', 'Bearer ' + KEY))
        self.assertIsNone(enrichment.fetch(cfg, 'unknown.example'))
        self.assertIsNone(enrichment.fetch(cfg, 'mismatch.example'))
        self.key.write_text('wrong')
        with self.assertRaises(enrichment.EnrichmentError) as caught:
            enrichment.fetch(cfg, 'globex.com')
        self.assertEqual(str(caught.exception), 'the service rejected the key')
        self.assertNotIn('wrong', str(caught.exception))

    def test_the_graph_looks_up_its_organisations_a_few_a_day_and_shows_them(self):
        self.add(tg.mail(3, 'Lee <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee <lee@globex.com>', ('SENT',)),
                 tg.mail(3, 'Kim <kim@initech.example>', 'sam@acme.com'), tg.mail(3, 'Bo <bo@gmail.com>', 'sam@acme.com'))
        self.build()
        now = tg.NOW.isoformat()
        self.assertEqual(enrichment.step(self.store.connect, self.config, now), 1)
        # Never free mail, and not Kim, who only wrote in: cold outreach is not worth a look-up.
        self.assertEqual(sorted(p for p, _ in Service.seen), ['/enrich/companies?domain=globex.com'])
        self.assertEqual(enrichment.step(self.store.connect, self.config, now), 0)  # done until it's due again
        self.assertEqual(len(Service.seen), 1)
        with self.store.connect() as db:
            self.assertEqual(enrichment.status(db, self.config)['found'], 1)
            db.execute('DELETE FROM org_profiles')
        self.assertEqual(enrichment.step(self.store.connect, {'enrichment': {**self.config['enrichment'], 'per_day': 1}}, now), 1)
        self.assertEqual(enrichment.step(self.store.connect, {'enrichment': {**self.config['enrichment'], 'per_day': 1}}, now), 0)  # the daily cap
        self.build()
        org = self.node('org:globex.com')
        self.assertEqual((org['name'], org['about']), ('Globex', 'Manufacturing, 51-200 people, Springfield, founded 1989'))
        self.assertEqual(org['profile']['about'], 'Globex makes everything.')
        with self.store.connect() as db:
            lines = graph.context_lines(db, [r[0] for r in db.execute("SELECT event_id FROM graph_events WHERE node_id='email:lee@globex.com'")])
        self.assertIn('at Globex (Manufacturing, 51-200 people, Springfield, founded 1989)', lines)
        # The CLI's one-domain lookup and status, through the app.
        self.app.config = {**tg.CONFIG, **self.config}
        self.app.graph_dirty = threading.Event()
        self.assertEqual(self.app.action('enrich', {'domain': 'https://www.Globex.com/about'})['profile']['name'], 'Globex')
        self.assertEqual(self.app.action('enrich', {})['service'], '127.0.0.1')
        with self.assertRaisesRegex(ValueError, 'Give a domain'):
            self.app.action('enrich', {'domain': 'not a domain'})

    def test_look_ups_go_to_people_the_owner_deals_with_not_cold_outreach_or_bulk(self):
        meeting = {'account_id': 'work', 'source_id': 'meet-hooli', 'kind': 'calendar.event', 'occurred_at': tg.at(4), 'title': 'Call', 'body': '',
                   'deleted': False, 'metadata': {'attendees': [{'email': 'sam@acme.com', 'self': True}, {'email': 'gav@hooli.example'}]}}
        cold = [tg.mail(d, f'Rep {n} <rep{n}@leadgen.example>', 'sam@acme.com') for n in range(4) for d in (1, 2, 3, 5, 8)]  # strong, one way
        self.add(*cold, meeting,
                 tg.mail(9, 'Lee <lee@globex.com>', 'sam@acme.com'), tg.mail(8, 'Sam <sam@acme.com>', 'Lee <lee@globex.com>', ('SENT',)),  # a reply
                 tg.mail(3, 'Sam <sam@acme.com>', 'Ola <ola@prospect.example>', ('SENT',)),  # the owner wrote, no answer yet
                 tg.mail(2, 'Deals <deals@news.shop.example>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'Deals <deals@news.shop.example>', ('SENT',)),
                 tg.mail(2, 'Bo <bo@yahoo.co.uk>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', 'Bo <bo@yahoo.co.uk>', ('SENT',)))
        self.build()
        with self.store.connect() as db:
            strongest = db.execute("SELECT json_extract(info,'$.domain') FROM graph_nodes WHERE kind='org' ORDER BY strength DESC LIMIT 1").fetchone()[0]
            self.assertEqual(strongest, 'leadgen.example')  # what "strongest first" used to spend the day's look-ups on
            picked = enrichment.due(db, tg.NOW.isoformat(), 10)
        # Both ways and meetings first, then someone the owner wrote to; never one-way inbound, a campaign sender or free mail.
        self.assertEqual(picked[2:], ['prospect.example'])
        self.assertEqual(sorted(picked[:2]), ['globex.com', 'hooli.example'])
        with self.store.connect() as db:
            self.assertEqual(enrichment.due(db, tg.NOW.isoformat(), 1), picked[:1])
        self.assertEqual(enrichment.worth('acme-partner.example', [{'identifiers': ['email:noreply@acme-partner.example'], 'wrote': 2, 'heard': 3}]), 0)
        self.assertEqual(enrichment.worth('gmail.com', [{'wrote': 2, 'heard': 3}]), 0)

    def test_the_owner_edits_an_organisation_and_drops_a_wrong_match(self):
        self.add(tg.mail(3, 'Lee <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee <lee@globex.com>', ('SENT',)))
        self.build()
        now = tg.NOW.isoformat()
        enrichment.step(self.store.connect, self.config, now)
        edit = lambda args: self.app.action('graph_org_edit', {'node_id': 'org:globex.com', **args}, owner_dashboard=True)
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('graph_org_edit', {'node_id': 'org:globex.com', 'fields': {'industry': 'Toys'}})
        self.assertIn('graph_org_edit', a.OWNER_ACTIONS)
        with self.assertRaisesRegex(ValueError, 'Founded is a year'):
            edit({'fields': {'founded': 'long ago'}})
        edit({'fields': {'industry': 'Toys', 'website': 'globex.example', 'founded': '2001'}, 'name': 'Globex Toys'})
        org = self.node('org:globex.com')
        self.assertEqual((org['name'], org['profile']['industry'], org['profile']['website'], org['profile']['founded'], org['edited']),
                         ('Globex Toys', 'Toys', 'https://globex.example', 2001, ['founded', 'industry', 'website']))
        self.assertEqual(org['profile']['about'], 'Globex makes everything.')  # what the owner left alone stays looked up
        with self.store.connect() as db:  # a refresh never undoes the owner's details
            enrichment.save(db, 'globex.com', 'found', {'name': 'Globex', 'industry': 'Manufacturing'}, now)
        self.build()
        self.assertEqual(self.node('org:globex.com')['profile']['industry'], 'Toys')
        edit({'fields': {'industry': ''}})
        self.build()
        self.assertEqual(self.node('org:globex.com')['profile']['industry'], 'Manufacturing')  # an empty field gives it back
        # Wrong company: the looked-up details go and the domain isn't looked up again; the owner's stay.
        self.app.action('graph_org_wrong', {'node_id': 'org:globex.com', 'wrong': True}, owner_dashboard=True)
        org = self.node('org:globex.com')
        self.assertTrue(org['wrong'])
        self.assertEqual(org['profile'], {'website': 'https://globex.example', 'founded': 2001})
        with self.store.connect() as db:
            self.assertEqual(enrichment.due(db, '2030-01-01T00:00:00+00:00', 10), [])
            enrichment.save(db, 'globex.com', 'found', {'name': 'Globex'}, now)  # a late answer doesn't bring it back
            self.assertEqual(db.execute("SELECT status FROM org_profiles WHERE domain='globex.com'").fetchone()[0], 'wrong')
        self.app.action('graph_org_wrong', {'node_id': 'org:globex.com', 'wrong': False}, owner_dashboard=True)
        with self.store.connect() as db:
            self.assertEqual(enrichment.due(db, now, 10), ['globex.com'])
        with self.assertRaisesRegex(ValueError, 'organisation'):
            self.app.action('graph_org_wrong', {'node_id': 'email:lee@globex.com', 'wrong': True}, owner_dashboard=True)


if __name__ == '__main__':
    unittest.main()
