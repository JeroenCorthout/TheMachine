import http.client
import http.server
import json
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import test_graph as tg

a, graph = tg.a, tg.graph
enrichment = a.enrichment
KEY = 'not-a-real-key'  # a test value; a realistic-looking one trips secret scanners


# Salesflare's lead API shape for a person (GET /enrich/people?name=&domain=), with the much more it says than is kept.
LEE = {'name': 'Lee Parker', 'linkedin_id': 'lee-parker-1', 'linkedin_url': 'https://www.linkedin.com/in/lee-parker-1',
       'image_url': 'https://media.example/lee.jpg', 'location': {'address': '1 Main St', 'city': 'Springfield', 'state': 'IL', 'country_code': 'us'},
       'title': 'Head of Sales at Globex | Speaker | Dad', 'position': 'Head of Sales',
       'company': {'name': 'Globex', 'domain': 'globex.com', 'linkedin_id': 'globex', 'employees_num': 120},
       'about_me': 'I love selling.', 'social_followers': 900, 'social_connections': 500,
       'experience': [{'position': 'Head of Sales', 'company': {'name': 'Globex'}}], 'education': [{'school': 'Springfield U'}],
       'languages': ['English'], 'websites': ['https://lee.example']}
PEOPLE = {('Lee Parker', 'globex.com'): LEE,
          ('Kai Novak', 'hooli.example'): {'name': 'Someone Else', 'position': 'CEO', 'company': {'name': 'Hooli', 'domain': 'hooli.example'}},
          ('Rae Quinn', 'initech.example'): {'name': 'Rae Quinn', 'position': 'CTO', 'company': {'name': 'Other', 'domain': 'other.example'}}}
WORK = {'work': 'work:Acme', 'home': 'personal:me', 'chat': 'personal:whatsapp', 'slack': 'work:Acme'}


class Service(http.server.BaseHTTPRequestHandler):
    """A fake enrichment service: Globex is known, nobody else; a wrong key is refused."""
    seen = []

    def do_GET(self):
        Service.seen.append((self.path, self.headers.get('Authorization')))
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            self.send_response(401); self.end_headers(); return
        if self.path.startswith('/enrich/people'):
            asked = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            body = PEOPLE.get((asked.get('name', [''])[0], asked.get('domain', [''])[0]))
            if body is None:
                self.send_response(404); self.end_headers(); return
            body = json.dumps(body).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body); return
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

    def test_a_shared_first_name_or_surname_is_not_the_same_person(self):
        for wrong in ('Lee Jones', 'Kai Parker'):
            self.assertIsNone(enrichment.normalise_person({'name': wrong, 'position': 'CFO'}, 'Lee Parker', 'globex.com'))
        self.assertEqual(enrichment.normalise_person({'name': 'Lée M. Parker', 'position': 'CFO'}, 'Lee Parker', 'globex.com'), {'title': 'CFO'})

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


    def people_config(self, **extra):
        return {'accounts': [{'id': k, 'boundary': v} for k, v in WORK.items()],
                'enrichment': {**self.config['enrichment'], 'people_url': f'http://127.0.0.1:{self.server.server_port}/enrich/people?name={{name}}&domain={{domain}}', **extra}}

    def test_a_person_answer_is_cut_down_to_what_the_dashboard_shows(self):
        self.assertEqual(enrichment.normalise_person(LEE, 'Lee Parker', 'globex.com'),
                         {'title': 'Head of Sales', 'company': 'Globex', 'company_domain': 'globex.com', 'photo': 'https://media.example/lee.jpg',
                          'linkedin_url': 'https://www.linkedin.com/in/lee-parker-1', 'city': 'Springfield', 'country': 'US'})
        # Other services' shapes: People Data Labs, Hunter.
        pdl = enrichment.normalise_person({'data': {'full_name': 'lee parker', 'job_title': 'head of sales', 'job_company_name': 'globex',
                                                    'job_company_website': 'globex.com', 'linkedin_url': 'linkedin.com/in/leeparker',
                                                    'location_locality': 'springfield', 'location_country': 'united states', 'personal_emails': ['x@y.z']}},
                                          'Lee Parker', 'globex.com')
        self.assertEqual(pdl, {'title': 'head of sales', 'company': 'globex', 'company_domain': 'globex.com',
                               'linkedin_url': 'https://www.linkedin.com/in/leeparker', 'city': 'springfield', 'country': 'united states'})
        hunter = enrichment.normalise_person({'data': {'name': {'fullName': 'Lee Parker'}, 'employment': {'domain': 'globex.com', 'name': 'Globex', 'title': 'CFO'},
                                                       'geo': {'city': 'Springfield', 'countryCode': 'US'}, 'linkedin': {'handle': 'leeparker'}}}, 'Lee Parker', 'globex.com')
        self.assertEqual((hunter['title'], hunter['linkedin_url'], hunter['country']), ('CFO', 'https://www.linkedin.com/in/leeparker', 'US'))
        # A photo only as a plain https address; a LinkedIn address only to a profile.
        for bad in ('http://media.example/a.jpg', 'javascript:alert(1)', 'data:image/png;base64,AAAA', 'https://media.example/"><b>'):
            self.assertNotIn('photo', enrichment.normalise_person({'name': 'Lee Parker', 'position': 'CFO', 'image_url': bad}, 'Lee Parker', 'globex.com'), bad)
        self.assertNotIn('linkedin_url', enrichment.normalise_person({'name': 'Lee Parker', 'position': 'CFO', 'linkedin_url': 'https://evil.example/in/lee'}, 'Lee Parker', 'globex.com'))
        # Someone else: another name, or the same name at another company. Nothing worth showing is nothing.
        self.assertIsNone(enrichment.normalise_person(PEOPLE[('Kai Novak', 'hooli.example')], 'Kai Novak', 'hooli.example'))
        self.assertIsNone(enrichment.normalise_person(PEOPLE[('Rae Quinn', 'initech.example')], 'Rae Quinn', 'initech.example'))
        self.assertIsNone(enrichment.normalise_person({'name': 'Lee Parker', 'linkedin_url': 'https://www.linkedin.com/in/lee'}, 'Lee Parker', 'globex.com'))
        self.assertIsNone(enrichment.normalise_person({'success': False}, 'Lee Parker', 'globex.com'))
        self.assertEqual(enrichment.settings({'enrichment': {'url': 'https://leadapi.salesflare.com/enrich/companies?domain={domain}'}})['people_url'],
                         'https://leadapi.salesflare.com/enrich/people?name={name}&domain={domain}')

    def test_only_the_technical_skips(self):
        info = {'org': 'org:globex.com', 'org_by': 'email', 'identifiers': ['email:lee@globex.com']}
        ok = lambda name='Lee Parker', **changes: enrichment.work_contact({**info, **changes}, name)
        self.assertEqual(ok(), ('lee@globex.com', 'globex.com'))
        # Who they are to the owner doesn't matter: a colleague, family, only wrote in, only on a personal account.
        self.assertEqual(ok(colleague=True, seen_only=True, wrote=0, met=0, accounts=['chat'], hints=["shares the owner's surname"]), ('lee@globex.com', 'globex.com'))
        self.assertEqual(ok(org=None, org_by=None, identifiers=['email:lee@acme.com']), ('lee@acme.com', 'acme.com'))  # the owner's own domain
        self.assertEqual(ok(identifiers=['email:lee@gmail.com', 'email:lee@globex.com']), ('lee@globex.com', 'globex.com'))
        # Where a look-up can't work or would be wrong.
        self.assertIsNone(ok('lee'))                                # no full name to look up by
        self.assertIsNone(ok(org='org:gmail.com', identifiers=['email:lee@gmail.com']))  # free mail: no company to look in
        self.assertIsNone(ok(identifiers=['email:info@globex.com']))
        self.assertIsNone(ok(identifiers=['email:noreply@globex.com']))
        self.assertIsNone(ok(identifiers=['wa:3212345678']))

    def test_work_contacts_are_looked_up_on_their_own_budget_and_shown(self):
        def home(event):
            return {**event, 'account_id': 'home'}
        self.add(tg.mail(3, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee Parker <lee@globex.com>', ('SENT',)),
                 tg.mail(3, 'Kim Ray <kim@initech.example>', 'sam@acme.com'),                                        # only wrote in: looked up too
                 tg.mail(3, 'Bo Smith <bo@gmail.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Bo Smith <bo@gmail.com>', ('SENT',)),
                 home(tg.mail(3, 'Max Field <max@umbrella.example>', 'sam@acme.com')), home(tg.mail(2, 'Sam <sam@acme.com>', 'Max Field <max@umbrella.example>', ('SENT',))))
        self.build()
        config, now = self.people_config(), tg.NOW.isoformat()
        with self.store.connect() as db:  # everyone with a full name and a company address; Bo has only free mail
            self.assertEqual([p['identifier'] for p in enrichment.people_due(db, now, 10)], ['email:lee@globex.com', 'email:max@umbrella.example', 'email:kim@initech.example'])
        self.assertEqual(enrichment.people_step(self.store.connect, config, now, per_pass=1), 1)
        people = [p for p, _ in Service.seen if p.startswith('/enrich/people')]
        self.assertEqual(people, ['/enrich/people?name=Lee%20Parker&domain=globex.com'])  # name and domain only, never the address
        self.assertEqual(enrichment.people_step(self.store.connect, config, now), 0)  # the others aren't known; done until due again
        with self.store.connect() as db:
            self.assertEqual(enrichment.people_status(db)['found'], 1)
            self.assertEqual(enrichment.status(db, config)['found'], 0)  # the organisations' budget is untouched
            db.execute('DELETE FROM person_profiles')
            enrichment.save_person(db, 'email:someone@else.example', 'none', None, now)  # today's one look-up
        capped = self.people_config(people_per_day=1)
        self.assertEqual(enrichment.people_step(self.store.connect, capped, now), 0)
        self.assertEqual(enrichment.people_step(self.store.connect, config, now), 1)
        self.build()
        lee = self.node('email:lee@globex.com')
        self.assertEqual((lee['profile']['title'], lee['profile']['company_domain'], lee['org']), ('Head of Sales', 'globex.com', 'org:globex.com'))
        self.assertNotIn('about_me', json.dumps(lee))
        with self.store.connect() as db:
            lines = graph.context_lines(db, [r[0] for r in db.execute("SELECT event_id FROM graph_events WHERE node_id='email:lee@globex.com'")])
        self.assertIn('job title Head of Sales', lines)
        self.app.config = {**tg.CONFIG, **config}
        self.assertTrue({'graph_person_delete', 'set_people_lookup'} <= a.OWNER_ACTIONS and 'graph_person_lookup' not in a.OWNER_ACTIONS)
        # Settings, Look up work contacts: off means nobody is looked up.
        with self.assertRaisesRegex(ValueError, 'Only the owner'):
            self.app.action('set_people_lookup', {'on': False})
        self.assertEqual(self.app.action('set_people_lookup', {'on': False}, owner_dashboard=True), {'people_available': True, 'people': False})
        with self.store.connect() as db:
            self.assertEqual(enrichment.people_due(db, now, 10), [])
        self.assertEqual(enrichment.people_step(self.store.connect, config, now), 0)
        self.app.action('set_people_lookup', {'on': True}, owner_dashboard=True)
        self.assertEqual(self.app.lookups_view(), {'people_available': True, 'people': True})
        self.app.config = {**tg.CONFIG, 'enrichment': {'enabled': False, 'by': 'hosting'}}  # hosted: the hosting agent looks them up
        self.assertTrue(self.app.lookups_view()['people_available'])
        self.app.config = {**tg.CONFIG, 'enrichment': {**self.config['enrichment']}}  # a service without a people address
        self.assertFalse(self.app.lookups_view()['people_available'])


    def test_a_photo_is_saved_once_here_and_shown_from_here(self):
        JPEG = b'\xff\xd8\xff\xe0' + b'0' * 100
        self.add(tg.mail(3, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee Parker <lee@globex.com>', ('SENT',)))
        self.build()
        now, asked = tg.NOW.isoformat(), []
        fetch = lambda url: asked.append(url) or (JPEG, 'image/jpeg')
        self.assertEqual(enrichment.people_step(self.store.connect, self.people_config(), now), 1)
        self.assertEqual(enrichment.photos_step(self.store.connect, now, fetch=fetch), 1)
        self.assertEqual(asked, ['https://media.example/lee.jpg'])
        self.assertEqual(enrichment.photos_step(self.store.connect, now, fetch=fetch), 0)  # once: never asked again
        self.assertEqual(len(asked), 1)
        self.build()
        key = enrichment.photo_key('email:lee@globex.com')
        self.assertEqual(self.node('email:lee@globex.com')['profile']['photo'], 'api/person-photo?p=' + key)  # from here, not the service
        with self.store.connect() as db:
            self.assertEqual(enrichment.photo(db, key), (JPEG, 'image/jpeg'))
            self.assertIsNone(enrichment.photo(db, '../' + key))
            self.assertNotIn('media.example', db.execute('SELECT profile FROM person_profiles').fetchone()[0])
            # Looked up again with a link that has expired by then: the saved photo stays, and no 30-day look-up.
            enrichment.save_person(db, 'email:lee@globex.com', 'found', {'title': 'Head of Sales', 'photo': 'https://media.example/lee.jpg?e=1700000000&t=x'}, now)
            self.assertEqual(enrichment.people_due(db, (tg.NOW + tg.dt.timedelta(days=40)).isoformat(), 10), [])
        self.assertEqual(enrichment.photos_step(self.store.connect, now, fetch=fetch), 0)
        self.assertEqual(len(asked), 1)
        self.build()
        self.assertEqual(self.node('email:lee@globex.com')['profile']['photo'], 'api/person-photo?p=' + key)
        # Delete permanently takes the photo too.
        with self.store.connect() as db:
            graph.delete_person(db, 'email:lee@globex.com', tg.CONFIG, now)
            self.assertEqual(db.execute('SELECT count(*) FROM person_photos').fetchone()[0], 0)

    def test_an_expired_or_broken_photo_link_is_skipped_and_looked_up_again_in_30_days(self):
        self.add(tg.mail(3, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee Parker <lee@globex.com>', ('SENT',)),
                 tg.mail(3, 'Rae Quinn <rae@initech.example>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Rae Quinn <rae@initech.example>', ('SENT',)))
        self.build()
        now, asked = tg.NOW.isoformat(), []
        expired = 'https://media.licdn.com/dms/image/v2/x/profile-displayphoto-shrink_200_200/0?e=1700000000&v=beta&t=abc'
        later = str(int(tg.NOW.timestamp()) + 86400)
        self.assertTrue(enrichment.link_expired(expired, now))
        self.assertFalse(enrichment.link_expired(expired.replace('1700000000', later), now))
        self.assertTrue(enrichment.link_expired('https://s3.example/p.jpg?X-Amz-Date=20261001T000000Z&X-Amz-Expires=3600', now))
        self.assertFalse(enrichment.link_expired('https://media.example/lee.jpg', now))
        with self.store.connect() as db:
            enrichment.people_due(db, now, 10)  # the backfill starts (and ends: nobody was looked up before)
            enrichment.save_person(db, 'email:lee@globex.com', 'found', {'title': 'Head of Sales', 'photo': expired}, now)
            enrichment.save_person(db, 'email:rae@initech.example', 'found', {'title': 'CTO', 'photo': 'https://media.example/gone.jpg'}, now)
            self.assertNotIn('photo"', db.execute("SELECT profile FROM person_profiles WHERE identifier='email:lee@globex.com'").fetchone()[0])
        self.assertEqual(enrichment.photos_step(self.store.connect, now, fetch=lambda url: asked.append(url)), 0)  # Rae's: a 404
        self.assertEqual(asked, ['https://media.example/gone.jpg'])  # the expired one is never asked for
        self.assertEqual(enrichment.photos_step(self.store.connect, now, fetch=lambda url: asked.append(url)), 0)
        self.assertEqual(len(asked), 1)  # one try per link
        self.build()
        self.assertNotIn('photo', self.node('email:lee@globex.com')['profile'])  # initials show
        with self.store.connect() as db:
            day = lambda n: (tg.NOW + tg.dt.timedelta(days=n)).isoformat()
            self.assertEqual(enrichment.people_due(db, day(29), 10), [])
            self.assertEqual({p['identifier'] for p in enrichment.people_due(db, day(31), 10)}, {'email:lee@globex.com', 'email:rae@initech.example'})

    def test_a_photo_download_takes_only_a_small_image_from_a_public_address(self):
        import socket
        self.assertEqual(enrichment.image_type(b'\x89PNG\r\n\x1a\n' + b'0' * 10), 'image/png')
        self.assertEqual(enrichment.image_type(b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertIsNone(enrichment.image_type(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        self.assertIsNone(enrichment.image_type(b'<html>'))
        resolve = socket.getaddrinfo
        self.addCleanup(setattr, socket, 'getaddrinfo', resolve)
        for address in ('127.0.0.1', '10.0.0.8', '169.254.169.254', '::1'):
            socket.getaddrinfo = lambda *args, address=address, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
            self.assertIsNone(enrichment.download_photo('https://photos.example/me.jpg'))  # never to this Machine or its network
        self.assertIsNone(enrichment.download_photo('http://photos.example/me.jpg'))

    def test_the_one_time_backfill_looks_everyone_up_once_closest_first(self):
        self.add(tg.mail(3, 'Lee Parker <lee@globex.com>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'Lee Parker <lee@globex.com>', ('SENT',)),
                 tg.mail(3, 'Kai Novak <kai@hooli.example>', 'sam@acme.com'), tg.mail(3, 'Rae Quinn <rae@initech.example>', 'sam@acme.com'))
        self.build()
        before, now = (tg.NOW - tg.dt.timedelta(days=10)).isoformat(), tg.NOW.isoformat()
        with self.store.connect() as db:  # looked up by the old code, ten days ago: not due by the normal interval
            enrichment.save_person(db, 'email:lee@globex.com', 'found', {'title': 'Head of Sales'}, before)
            enrichment.save_person(db, 'email:kai@hooli.example', 'none', None, before)
            enrichment.save_person(db, 'email:rae@initech.example', 'none', None, before)
        capped = self.people_config(people_per_day=2)
        self.assertEqual(enrichment.people_step(self.store.connect, capped, now, per_pass=5), 1)  # the day's limit: two of three
        asked = [urllib.parse.parse_qs(urllib.parse.urlsplit(p).query)['name'][0] for p, _ in Service.seen if p.startswith('/enrich/people')]
        self.assertEqual(asked[0], 'Lee Parker')  # closest first (wrote both ways)
        self.assertEqual(len(asked), 2)
        with self.store.connect() as db:
            self.assertFalse(db.execute("SELECT value FROM enrich_prefs WHERE key='people_backfill'").fetchone()[0].startswith('done'))
        tomorrow = (tg.NOW + tg.dt.timedelta(days=1)).isoformat()
        enrichment.people_step(self.store.connect, capped, tomorrow)
        self.assertEqual(len([p for p, _ in Service.seen if p.startswith('/enrich/people')]), 3)  # the rest the next day
        with self.store.connect() as db:
            self.assertEqual(enrichment.people_due(db, tomorrow, 10), [])
            self.assertTrue(db.execute("SELECT value FROM enrich_prefs WHERE key='people_backfill'").fetchone()[0].startswith('done'))
            enrichment.save_person(db, 'email:kai@hooli.example', 'none', None, before)  # an old row again: no second backfill
            self.assertEqual(enrichment.people_due(db, tomorrow, 10), [])  # no match: the normal 90 days

    def test_the_dashboard_serves_a_saved_photo_to_the_owner_only_as_an_image(self):
        JPEG = b'\xff\xd8\xff\xe0' + b'1' * 50
        with self.store.connect() as db:
            enrichment.save_person(db, 'email:lee@globex.com', 'found', {'title': 'Head of Sales', 'photo': 'https://media.example/lee.jpg'}, tg.NOW.isoformat())
        enrichment.photos_step(self.store.connect, tg.NOW.isoformat(), fetch=lambda url: (JPEG, 'image/jpeg'))
        app = object.__new__(a.App)
        app.store, app.token, app.stop, app.graph_dirty, app.owner_token = self.store, 'test-token', threading.Event(), threading.Event(), 'owner-key'
        app.config = {'accounts': [], 'tailscale_owner': 'owner@example.com', 'dashboard_host': 'machine.example:8443', 'port': 0, 'http_timeout': 1}
        servers, real = [], a.http.server.ThreadingHTTPServer
        def capture(*args, **kwargs):
            servers.append(real(*args, **kwargs)); return servers[-1]
        for patcher in (patch.object(a.http.server, 'ThreadingHTTPServer', side_effect=capture), patch.object(a.signal, 'signal'),
                        patch.object(a.App, 'preparation_worker'), patch.object(a.App, 'delivery_worker')):
            patcher.start()
            self.addCleanup(patcher.stop)
        thread = threading.Thread(target=app.serve, daemon=True)
        thread.start()
        while not servers:
            pass
        self.addCleanup(lambda: (app.stop.set(), thread.join(5)))
        def get(path, owner=True):
            conn = http.client.HTTPConnection('127.0.0.1', servers[0].server_address[1], timeout=10)
            conn.request('GET', path, headers={'Host': 'machine.example:8443', **({'Tailscale-User-Login': 'owner@example.com'} if owner else {})})
            r = conn.getresponse()
            body = r.read()
            conn.close()
            return r.status, r.getheader('Content-Type'), r.getheader('X-Content-Type-Options'), body
        key = enrichment.photo_key('email:lee@globex.com')
        self.assertEqual(get('/api/person-photo?p=' + key), (200, 'image/jpeg', 'nosniff', JPEG))
        self.assertEqual(get('/api/person-photo?p=' + key, owner=False)[0], 403)
        self.assertEqual(get('/api/person-photo?p=' + 'f' * 32)[0], 404)
        self.assertEqual(get('/api/person-photo?p=email:lee@globex.com')[0], 404)

    def test_a_paired_self_hosted_machine_gets_the_hosted_budget(self):
        asked = []
        real = enrichment.people_due
        def due(db, now, limit):
            asked.append(limit)
            return real(db, now, limit)
        now = tg.NOW.isoformat()
        with patch.object(enrichment, 'people_due', side_effect=due):
            enrichment.people_step(self.store.connect, self.people_config(), now)
            enrichment.people_step(self.store.connect, self.people_config(), now, paired=True)
            with self.store.connect() as db:
                db.executemany('INSERT INTO person_profiles(identifier,status,profile,fetched_at) VALUES(?,?,?,?)',
                               [(f'email:p{n}@x.example', 'none', None, now) for n in range(2990)])
            enrichment.people_step(self.store.connect, self.people_config(), now, paired=True)  # 10 left of today's 3,000
            enrichment.people_step(self.store.connect, self.people_config(people_per_day=100), now, paired=True)  # the owner's own cap wins
        self.assertEqual(asked, [5, 25, 10, 0])
        self.assertEqual(enrichment.PAIRED_PEOPLE_PER_DAY, 3000)

if __name__ == '__main__':
    unittest.main()
