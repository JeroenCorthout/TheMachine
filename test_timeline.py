"""The timeline on a person's and an organization's page: one list, newest first, from every source. Intercom
conversations on their address, their Intercom contact or their organization's domain; the Zoom summary of a meeting
they were in; the cards, promises and project moves around them; a source filter and pages."""
import unittest

import test_graph as tg

a, graph = tg.a, tg.graph
ACCOUNTS = tg.CONFIG['accounts'] + [{'id': 'help', 'service': 'intercom'}, {'id': 'zoom', 'service': 'zoom'}]


def intercom(days_ago, cid, sender, title, contact_ids=(), is_open=False, contact_email=''):
    return {'account_id': 'help', 'source_id': cid, 'kind': 'intercom.conversation', 'occurred_at': tg.at(days_ago), 'title': title,
            'body': 'Hi, a question', 'deleted': False,
            'metadata': {'conversation_id': cid, 'from': sender, 'contact_email': contact_email, 'contact_ids': list(contact_ids),
                         'open': is_open, 'state': 'open' if is_open else 'closed', 'source_url': 'https://app.intercom.com/a/inbox/w/conversation/' + cid}}


def meeting(days_ago, title, *emails):
    return {'account_id': 'work', 'source_id': 'cal' + title, 'kind': 'calendar.event', 'occurred_at': tg.at(days_ago), 'title': title, 'body': '',
            'deleted': False, 'metadata': {'attendees': [{'email': 'sam@acme.com', 'self': True}, *({'email': e} for e in emails)],
                                           'organizer': {'email': 'sam@acme.com', 'self': True}}}


def zoom(days_ago, minutes, title):
    start = (tg.NOW - tg.dt.timedelta(days=days_ago) + tg.dt.timedelta(minutes=minutes)).isoformat()
    return {'account_id': 'zoom', 'source_id': 'z' + title, 'kind': 'zoom.summary', 'occurred_at': start, 'title': title, 'body': 'We agreed on a pilot.',
            'deleted': False, 'metadata': {'meeting_id': '1', 'start_at': start, 'host_email': 'sam@acme.com', 'source_url': 'https://zoom.us/doc/1'}}


class Timeline(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build

    def setUp(self):
        tg.Graph.setUp(self)
        self.app.config = {**tg.CONFIG, 'accounts': ACCOUNTS}
        self.store.accounts = ACCOUNTS
        self.app.fleet = None
        self.add(tg.mail(3, 'Ivy Park <ivy@globex.example>', 'sam@acme.com'), tg.mail(2, 'Sam <sam@acme.com>', 'ivy@globex.example', ('SENT',)),
                 tg.mail(1, 'Sam <sam@acme.com>', 'ivy@globex.example', ('SENT',)),
                 meeting(5, 'Pilot call', 'ivy@globex.example'), zoom(5, 4, 'Pilot call'), zoom(9, 0, 'Another call'),
                 # Her own conversation, one from her other address under the same Intercom contact, a colleague's on the
                 # domain, and someone else's
                 intercom(4, '101', 'ivy@globex.example', 'Export fails', ('c-ivy',), is_open=True),
                 intercom(6, '102', 'ivy.park@home.example', 'Invoice copy', ('c-ivy',)),
                 intercom(7, '103', 'operator+abc@intercom.io', 'Seats question', contact_email='leo@globex.example'),
                 intercom(8, '104', 'max@other.example', 'Not hers'))
        self.build()

    def timeline(self, node, source='', cursor=0, limit=40):
        return self.app.graph_timeline(node, source, cursor, limit)

    def test_a_person_has_intercom_meetings_zoom_and_mail_in_one_list(self):
        found = self.timeline('email:ivy@globex.example')
        titles = [i.get('title') for i in found['items']]
        self.assertIn('Export fails', titles)
        self.assertIn('Invoice copy', titles)  # the same Intercom contact, another address
        self.assertNotIn('Seats question', titles)  # a colleague's: on the organization, not on her
        self.assertNotIn('Not hers', titles)
        self.assertIn('Pilot call', [i['title'] for i in found['items'] if i['kind'] == 'zoom.summary'])
        self.assertNotIn('Another call', titles)  # no meeting of hers then
        self.assertEqual(found['sources'], {'email': 3, 'meetings': 2, 'intercom': 2})
        at = [i['occurred_at'] for i in found['items']]
        self.assertEqual(at, sorted(at, reverse=True))
        export = next(i for i in found['items'] if i.get('title') == 'Export fails')
        self.assertEqual((export['state'], export['source'], export['source_url']), ('open', 'intercom', 'https://app.intercom.com/a/inbox/w/conversation/101'))
        self.assertEqual(next(i for i in found['items'] if i.get('title') == 'Invoice copy')['state'], 'closed')

    def test_an_organisation_has_its_domains_intercom_conversations(self):
        found = self.timeline('org:globex.example')
        titles = [i.get('title') for i in found['items']]
        self.assertIn('Seats question', titles)  # Operator opened it; the customer is on the domain
        self.assertIn('Export fails', titles)
        self.assertNotIn('Not hers', titles)

    def test_a_source_filter_and_pages(self):
        only = self.timeline('email:ivy@globex.example', 'intercom')
        self.assertEqual({i['source'] for i in only['items']}, {'intercom'})
        self.assertEqual(only['sources']['email'], 3)  # the counts stay those of every source
        first = self.timeline('email:ivy@globex.example', limit=3)
        self.assertEqual((len(first['items']), first['next']), (3, '3'))
        rest = self.timeline('email:ivy@globex.example', cursor=first['next'], limit=40)
        self.assertIsNone(rest['next'])
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in rest['items']})
        self.assertEqual(len(first['items']) + len(rest['items']), 7)

    def test_cards_promises_and_project_moves_are_on_it(self):
        with self.store.connect() as db:
            mail = db.execute("SELECT id FROM events WHERE kind='gmail.message' ORDER BY occurred_at DESC").fetchone()[0]
            for cid, title, status in (('c1', 'Send Ivy the pilot plan', 'pending'), ('c2', 'Old idea', 'rejected')):
                db.execute("INSERT INTO suggestions(id,dedupe_key,title,body,event_ids,account_ids,status,created_at) VALUES(?,?,?,'',?,'[\"work\"]',?,?)",
                           (cid, cid, title, f'[{mail}]', status, tg.at(1)))
            db.execute("INSERT INTO promises(direction,who,what,due,check_at,event_id,account_id,status,created_at,updated_at) "
                       "VALUES('mine','Sam','Send the pilot plan',NULL,?,?,'work','open',?,?)", (tg.at(0), mail, tg.at(1), tg.at(1)))
            db.execute("INSERT INTO projects(id,name,created_at,stage) VALUES(7,'Globex pilot',?,'Proposal')", (tg.at(2),))
            db.execute("INSERT INTO project_people(project_id,node_id,created_at) VALUES(7,'email:ivy@globex.example',?)", (tg.at(2),))
            db.execute("INSERT INTO project_moves(project_id,stage,source,at,created_at) VALUES(7,'Proposal','owner',?,?)", (tg.at(1), tg.at(1)))
        found = self.timeline('email:ivy@globex.example', 'cards')
        self.assertEqual(sorted((i['type'], i.get('title') or i.get('what')) for i in found['items']),
                         [('card', 'Send Ivy the pilot plan'), ('promise', 'Send the pilot plan')])
        moves = self.timeline('email:ivy@globex.example', 'projects')['items']
        self.assertEqual([(m['type'], m['project'], m['stage'], m['project_id']) for m in moves], [('move', 'Globex pilot', 'Proposal', 'project:7')])

    def test_a_person_with_no_signals_has_an_empty_timeline(self):
        made = self.app.action('graph_new', {'kind': 'person', 'name': 'Lou Marsh'}, owner_dashboard=True) if hasattr(self.app, 'action') else None
        if not made:
            self.skipTest('graph_new not available')
        found = self.timeline(made['node_id'])
        self.assertEqual((found['items'], found['next'], found['sources']), ([], None, {}))

    def test_the_team_graph_is_never_given_the_timeline(self):
        source = (tg.Path(tg.__file__).with_name('shared_graph.py')).read_text()
        self.assertNotIn('graph.timeline', source)
        self.assertNotIn('graph_timeline', source)


if __name__ == '__main__':
    unittest.main()
