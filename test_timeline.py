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


def invite(days_ago, source_id, title, *emails, account='work', uid='', all_day=False, me='sam@acme.com', names=None):
    names = names or {}
    return {'account_id': account, 'source_id': source_id, 'kind': 'calendar.event', 'occurred_at': tg.at(days_ago), 'title': title, 'body': 'Participants: ' + ', '.join(emails),
            'deleted': False, 'metadata': {'attendees': [{'email': me, 'self': True, **({'displayName': names[me]} if me in names else {})}, *({'email': e, **({'displayName': names[e]} if e in names else {})} for e in emails)],
                                           'organizer': {'email': me, 'self': True}, 'all_day': all_day, **({'ical_uid': uid} if uid else {})}}


class Order(unittest.TestCase):
    """Upcoming meetings apart and soonest first, the rest strictly newest first; a meeting on two of the owner's calendars
    once; an all-day one says so; the person and the owner left out of who is on it."""
    add = tg.Graph.add
    build = tg.Graph.build
    accounts = tg.CONFIG['accounts'] + [{'id': 'home', 'email': 'sam@home.example'}]

    def setUp(self):
        tg.Graph.setUp(self)
        self.app.config = {**tg.CONFIG, 'accounts': self.accounts}
        self.store.accounts = self.accounts
        self.app.fleet = None
        juli, names = 'juli@globex.example', {'juli@globex.example': 'Juli Ex', 'bo@globex.example': 'Bo Ex', 'kim@other.example': 'Kim',
                                          'sam@home.example': 'Sam Owner', 'sam@old.example': 'Sam Owner'}
        self.add(tg.mail(3, 'Juli Ex <juli@globex.example>', 'sam@acme.com'), tg.mail(1, 'Sam <sam@acme.com>', juli, ('SENT',)),
                 invite(2, 'past', 'Review', juli, 'bo@globex.example', names=names),
                 invite(-1, 'soon', 'Lunch', juli, names=names),
                 invite(-9, 'later', 'Dinner', juli, 'kim@other.example', names=names),
                 # The same weekend on two calendars: the same iCalUID, the owner's other addresses on the second (one known only by their name)
                 invite(-5, 'wk-a', 'Weekend away', juli, 'bo@globex.example', uid='uid-1', all_day=True, names=names),
                 invite(-5, 'wk-b', 'Weekend away', juli, 'kim@other.example', 'sam@acme.com', 'sam@old.example', account='home', uid='uid-1', all_day=True, me='sam@home.example', names=names),
                 # The same meeting copied without an iCalUID: the same title at the same start
                 invite(4, 'cp-a', 'Board  meeting', juli, names=names),
                 invite(4, 'cp-b', 'board meeting', juli, account='home', me='sam@home.example', names=names))
        self.build()
        self.found = self.app.graph_timeline('email:' + juli, now=tg.NOW.isoformat())

    def test_upcoming_apart_soonest_first_then_newest_first(self):
        self.assertEqual([i['title'] for i in self.found['upcoming']], ['Lunch', 'Weekend away', 'Dinner'])
        at = [graph.parse_time(i['occurred_at']) for i in self.found['items']]
        self.assertEqual(at, sorted(at, reverse=True))
        self.assertTrue(all(i['occurred_at'] <= tg.NOW.isoformat() for i in self.found['items']))
        self.assertEqual(self.app.graph_timeline('email:juli@globex.example', cursor=1, now=tg.NOW.isoformat())['upcoming'], [])  # once, on the first page

    def test_copies_on_two_calendars_are_one_row_with_everyone_once(self):
        weekend = [i for i in self.found['upcoming'] if i['title'] == 'Weekend away']
        self.assertEqual(len(weekend), 1)
        self.assertEqual(weekend[0]['preview'], 'With Bo Ex, Kim')  # not Juli, not the owner on either address
        self.assertEqual([i['title'].casefold() for i in self.found['items'] if i['kind'] == 'calendar.event'], ['review', 'board  meeting'])

    def test_all_day_and_the_participant_line(self):
        weekend = next(i for i in self.found['upcoming'] if i['title'] == 'Weekend away')
        self.assertTrue(weekend['all_day'])
        lunch = next(i for i in self.found['upcoming'] if i['title'] == 'Lunch')
        self.assertFalse(lunch['all_day'])
        self.assertEqual(lunch['preview'], '')  # only Juli on it: nothing to say
        self.assertEqual(next(i for i in self.found['items'] if i['title'] == 'Review')['preview'], 'With Bo Ex')


if __name__ == '__main__':
    unittest.main()
