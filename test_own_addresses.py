"""The owner's own addresses (own_addresses.py): found from the connected sources, never a shared one, and used
wherever the owner is told apart from other people."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import ms_graph
import own_addresses as oa
import test_graph as tg

graph = tg.graph

NOW = tg.NOW.isoformat()
CONFIG = {'accounts': [
    {'id': 'work-mail', 'service': 'gmail', 'transport': 'gog', 'email': 'sam@acme.test'},
    {'id': 'side-mail', 'service': 'gmail', 'transport': 'gog', 'email': 'sam.lee@sideco.test'},
    {'id': 'work-cal', 'service': 'calendar', 'transport': 'gog', 'email': 'sam@acme.test', 'calendar_id': 'primary'},
    # A calendar of someone else's the owner reads: its self attendee is that person, never the owner
    {'id': 'kid-cal', 'service': 'calendar', 'transport': 'gog', 'email': 'sam.lee@sideco.test', 'calendar_id': 'robin@example.test'},
    {'id': 'work-slack', 'service': 'slack', 'transport': 'maton', 'email': 'sam@acme.test', 'user_id': 'U1'},
    # Shared from a Fleet: whatever it says, never the owner's
    {'id': 'team-intercom', 'service': 'intercom', 'transport': 'api', 'email': 'kim@acme.test', 'shared': {'fleet': 'f1'}},
]}


def sendas(*entries):
    return {'sendAs': [{'verificationStatus': 'accepted', **e} for e in entries]}


def mail(account, sender, labels=(), to='someone@client.test'):
    return (1, account, 'gmail.message', dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc), {'from': sender, 'to': to, 'labels': list(labels)})


def meeting(account, *attendees):
    return (2, account, 'calendar.event', dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc), {'attendees': list(attendees)})


class Detection(unittest.TestCase):
    def detect(self, aliases=(), signals=(), team=(), config=CONFIG):
        fetched = {'side-mail': oa.send_as_entries(sendas({'sendAsEmail': 'sam.lee@sideco.test', 'isPrimary': True}, *aliases), 'sam.lee@sideco.test')}
        return oa.detect(config, fetched, oa.evidence(config, list(signals)), team)

    def test_aliases_calendar_and_profiles_are_yours(self):
        found = self.detect(aliases=[{'sendAsEmail': 'sam@sideco.test', 'treatAsAlias': True}],
                            signals=[meeting('work-cal', {'email': 'sam.l@acme.test', 'self': True}, {'email': 'pat@client.test'})])
        self.assertEqual(found['sam@sideco.test'], {'sources': ['alias'], 'state': 'yours', 'reason': None})
        self.assertEqual(found['sam.l@acme.test']['state'], 'yours')
        self.assertEqual(found['sam@acme.test']['sources'], ['account', 'slack'])
        self.assertEqual(found['sam.lee@sideco.test']['state'], 'yours')
        self.assertNotIn('pat@client.test', found)

    def test_shared_account_and_someone_elses_calendar_never_count(self):
        found = self.detect(signals=[meeting('kid-cal', {'email': 'robin@example.test', 'self': True})])
        self.assertNotIn('kim@acme.test', found)
        self.assertNotIn('robin@example.test', found)

    def test_role_addresses_are_only_maybe(self):
        found = self.detect(aliases=[{'sendAsEmail': 'sales@sideco.test', 'treatAsAlias': True}, {'sendAsEmail': 'support-eu@sideco.test'}])
        self.assertEqual((found['sales@sideco.test']['state'], found['sales@sideco.test']['reason']), ('maybe', 'Looks like a team address'))
        self.assertEqual(found['support-eu@sideco.test']['state'], 'maybe')

    def test_send_as_that_is_not_an_alias_is_maybe(self):
        found = self.detect(aliases=[{'sendAsEmail': 'robin.lee@sideco.test', 'treatAsAlias': False}])
        self.assertEqual(found['robin.lee@sideco.test']['state'], 'maybe')

    def test_sent_through_another_persons_server_is_shared(self):
        found = self.detect(aliases=[{'sendAsEmail': 'office.lee@sideco.test', 'treatAsAlias': True,
                                      'smtpMsa': {'host': 'smtp.sideco.test', 'username': 'kim@sideco.test'}}])
        self.assertEqual(found['office.lee@sideco.test']['state'], 'shared')

    def test_unverified_send_as_is_ignored(self):
        found = self.detect(aliases=[{'sendAsEmail': 'new@sideco.test', 'verificationStatus': 'pending'}])
        self.assertNotIn('new@sideco.test', found)

    def test_a_teammate_using_it_makes_it_shared(self):
        found = self.detect(aliases=[{'sendAsEmail': 'deals@sideco.test', 'treatAsAlias': True}], team=['deals@sideco.test'])
        self.assertEqual((found['deals@sideco.test']['state'], found['deals@sideco.test']['reason']), ('shared', 'A teammate uses it too'))

    def test_several_names_make_it_shared(self):
        # A Google Group: mail from it carries each sender's own name
        signals = [mail('work-mail', '"Sam Lee" <crew@acme.test>', ['SENT']), mail('work-mail', '"Kim Park via Crew" <crew@acme.test>')]
        found = self.detect(aliases=[{'sendAsEmail': 'crew@acme.test', 'treatAsAlias': True}], signals=signals)
        self.assertEqual((found['crew@acme.test']['state'], found['crew@acme.test']['reason']), ('shared', 'Mail from it comes under several names'))

    def test_one_name_in_several_spellings_is_still_one_person(self):
        signals = [mail('side-mail', '"Sam Lee" <sam@sideco.test>', ['SENT']), mail('work-mail', '"Sam L." <sam@sideco.test>'),
                   mail('work-mail', '"Lee, Sam" <sam@sideco.test>')]
        found = self.detect(aliases=[{'sendAsEmail': 'sam@sideco.test', 'treatAsAlias': True}], signals=signals)
        self.assertEqual(found['sam@sideco.test']['state'], 'yours')

    def test_only_sent_from_is_maybe(self):
        found = self.detect(signals=[mail('work-mail', '"Sam Lee" <desk@acme.test>', ['SENT'])])
        self.assertEqual((found['desk@acme.test']['state'], found['desk@acme.test']['reason']), ('maybe', 'Only seen in mail you sent'))

    def test_workspace_alias_with_the_owners_name_is_yours(self):
        # Mail from two people lands under it in the owner's mailbox at its domain, and it carries the owner's name
        signals = [mail('side-mail', '"Sam Lee" <sam.lee@sideco.test>', ['SENT']),
                   mail('side-mail', 'Pat <pat@client.test>', to='"Sam Lee" <s.lee@sideco.test>'),
                   mail('side-mail', 'Ivy <ivy@client.test>', to='s.lee@sideco.test')]
        self.assertEqual(self.detect(signals=signals)['s.lee@sideco.test'], {'sources': ['received'], 'state': 'yours', 'reason': None})
        # Under one sender only, or at another domain than the mailbox: a maybe at most
        self.assertEqual(self.detect(signals=signals[:2])['s.lee@sideco.test']['state'], 'maybe')
        other = [signals[0], mail('side-mail', 'Pat <pat@client.test>', to='"Sam Lee" <sam.lee@old-job.test>'),
                 mail('side-mail', 'Ivy <ivy@client.test>', to='"Sam Lee" <sam.lee@old-job.test>')]
        self.assertEqual(self.detect(signals=other)['sam.lee@old-job.test'], {'sources': ['named'], 'state': 'maybe', 'reason': 'Has your name'})

    def test_a_sender_using_the_owners_name_is_not_the_owner(self):
        signals = [mail('side-mail', '"Sam Lee" <sam.lee@sideco.test>', ['SENT']), mail('side-mail', '"Sam Lee" <sam.lee@lookalike.test>'),
                   mail('side-mail', '"Sam Lee" <sam.lee@lookalike.test>')]
        self.assertNotIn('sam.lee@lookalike.test', self.detect(signals=signals))

    def test_a_shared_inbox_with_the_owners_name_is_still_shared(self):
        signals = [mail('side-mail', '"Sam Lee" <sam.lee@sideco.test>', ['SENT']),
                   mail('side-mail', 'Pat <pat@client.test>', to='"Sam Lee" <info@sideco.test>'),
                   mail('side-mail', 'Ivy <ivy@client.test>', to='info@sideco.test'),
                   mail('work-mail', '"Kim Park" <info@sideco.test>'), mail('work-mail', '"Robin Vale" <info@sideco.test>')]
        self.assertEqual(self.detect(signals=signals)['info@sideco.test']['state'], 'shared')

    def test_people_count(self):
        self.assertEqual(oa.people({'sam lee', 'lee sam', 'sam leeman'}), 1)
        self.assertEqual(oa.people({'sam lee', 'kim park'}), 2)

    def test_config_wins(self):
        config = {**CONFIG, 'owner_emails': ['info@sam.test']}
        found = self.detect(config=config, team=['info@sam.test'])
        self.assertEqual(found['info@sam.test']['state'], 'yours')


class Stored(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        oa.ensure(self.db)
        fetched = {'side-mail': oa.send_as_entries(sendas({'sendAsEmail': 'sam.lee@sideco.test', 'isPrimary': True},
                                                          {'sendAsEmail': 'sam@sideco.test', 'treatAsAlias': True},
                                                          {'sendAsEmail': 'sales@sideco.test', 'treatAsAlias': True}), 'sam.lee@sideco.test')}
        oa.save(self.db, oa.detect(CONFIG, fetched, {'sent': set(), 'calendar': set(), 'names': {}}), NOW)

    def test_yours_confirm_and_remove(self):
        self.assertEqual(oa.yours(self.db, CONFIG), {'sam@acme.test', 'sam.lee@sideco.test', 'sam@sideco.test'})
        oa.choose(self.db, 'sales@sideco.test', 'confirm', NOW)
        oa.choose(self.db, 'sam@sideco.test', 'remove', NOW)
        self.assertEqual(oa.yours(self.db, CONFIG), {'sam@acme.test', 'sam.lee@sideco.test', 'sales@sideco.test'})
        self.assertIn('sam@sideco.test', oa.excluded(self.db, CONFIG))
        with self.assertRaises(ValueError):
            oa.choose(self.db, 'stranger@client.test', 'confirm', NOW)

    def test_not_owner_emails_excludes(self):
        config = {**CONFIG, 'not_owner_emails': ['sam@sideco.test']}
        self.assertNotIn('sam@sideco.test', oa.yours(self.db, config))

    def test_view(self):
        v = oa.view(self.db, CONFIG)
        self.assertEqual([a['address'] for a in v['yours']], ['sam.lee@sideco.test', 'sam@acme.test', 'sam@sideco.test'])
        self.assertEqual(v['yours'][2], {'address': 'sam@sideco.test', 'sources': ['Gmail alias'], 'removable': True})
        self.assertFalse(v['yours'][0]['removable'])
        self.assertEqual(v['maybe'], [{'address': 'sales@sideco.test', 'sources': ['Gmail alias'], 'why': 'Looks like a team address'}])
        oa.choose(self.db, 'sales@sideco.test', 'remove', NOW)
        self.assertEqual(oa.view(self.db, CONFIG)['maybe'], [])

    def test_fetch_once_a_day_and_a_failed_read_keeps_the_last_list(self):
        self.assertTrue(oa.fetch_due(self.db, 'side-mail', NOW))
        oa.save_fetched(self.db, 'side-mail', [{'address': 'sam@sideco.test', 'alias': True}], NOW)
        later = lambda hours: (tg.NOW + dt.timedelta(hours=hours)).isoformat()
        self.assertFalse(oa.fetch_due(self.db, 'side-mail', later(11)))
        self.assertTrue(oa.fetch_due(self.db, 'side-mail', later(24)))
        oa.save_fetched(self.db, 'side-mail', None, later(24))
        self.assertEqual(oa.fetched(self.db, CONFIG), {'side-mail': [{'address': 'sam@sideco.test', 'alias': True}]})


class Outlook(unittest.TestCase):
    def test_proxy_addresses_as_send_as(self):
        answer = ms_graph.send_as({'mail': 'Sam@Acme.test', 'proxyAddresses': ['SMTP:sam@acme.test', 'smtp:sam.lee@acme.test', 'SIP:sam@acme.test', 'x500:/o=x']})
        self.assertEqual([e['address'] for e in oa.send_as_entries(answer, 'sam@acme.test')], ['sam@acme.test', 'sam.lee@acme.test'])
        self.assertEqual([e['primary'] for e in oa.send_as_entries(answer, 'sam@acme.test')], [True, False])


class InTheGraph(unittest.TestCase):
    """The build finds the owner's alias, and their timelines say You for it: a mail from it, and no "Sam Lee" among
    the people on a meeting. The graph never makes a person of it."""
    add = tg.Graph.add
    build = tg.Graph.build

    def setUp(self):
        tg.Graph.setUp(self)
        self.app.fleet = None
        self.store.accounts = tg.CONFIG['accounts']
        with self.store.connect() as db:
            oa.save_fetched(db, 'work', oa.send_as_entries(sendas({'sendAsEmail': 'sam@acme.com', 'isPrimary': True},
                                                                  {'sendAsEmail': 'sam.lee@acme-labs.example', 'treatAsAlias': True}), 'sam@acme.com'), NOW)
        self.add(tg.mail(3, 'Ivy Park <ivy@globex.example>', 'sam@acme.com'),
                 tg.mail(2, 'Sam Lee <sam.lee@acme-labs.example>', 'ivy@globex.example'),  # from the alias, not in Sent here
                 {'account_id': 'work', 'source_id': 'cal1', 'kind': 'calendar.event', 'occurred_at': tg.at(1), 'title': 'Pilot call', 'body': '',
                  'deleted': False, 'metadata': {'attendees': [{'email': 'sam@acme.com', 'self': True},
                                                               {'email': 'sam.lee@acme-labs.example', 'displayName': 'Sam Lee'},
                                                               {'email': 'ivy@globex.example', 'displayName': 'Ivy Park'}]}})
        self.build()

    def test_alias_is_the_owner(self):
        with self.store.connect() as db:
            self.assertIn('sam.lee@acme-labs.example', graph.Owner(tg.CONFIG, db).emails)
            people = {r[0] for r in db.execute("SELECT id FROM graph_nodes WHERE kind='person'")}
        self.assertNotIn('sam.lee@acme-labs.example', graph.Owner(tg.CONFIG).emails)
        self.assertIn('email:ivy@globex.example', people)
        self.assertNotIn('email:sam.lee@acme-labs.example', people)
        self.assertIn('sam.lee@acme-labs.example', self.app.owner_emails())
        items = self.app.graph_timeline('email:ivy@globex.example')['items']
        sent = next(i for i in items if i['kind'] == 'gmail.message' and i['from_me'])
        self.assertEqual(sent['from'], 'You')
        call = next(i for i in items if i['kind'] == 'calendar.event')
        self.assertEqual(call['preview'], '')  # Ivy is a given on her own page, and Sam is the owner
        views = self.store.event_views([sent['id']])
        self.assertEqual(views[0]['from'], 'You')

    def test_removed_alias_is_someone_else_again(self):
        with self.store.connect() as db:
            oa.choose(db, 'sam.lee@acme-labs.example', 'remove', NOW)
        self.assertNotIn('sam.lee@acme-labs.example', self.app.owner_emails())
        call = next(i for i in self.app.graph_timeline('email:ivy@globex.example')['items'] if i['kind'] == 'calendar.event')
        self.assertEqual(call['preview'], 'With Sam Lee')


class Dashboard(unittest.TestCase):
    """Settings, Account: the list in status, and Confirm or Remove from the dashboard only."""
    def setUp(self):
        tg.Graph.setUp(self)
        self.app.graph_dirty = tg.a.threading.Event()
        with self.store.connect() as db:
            oa.save_fetched(db, 'work', oa.send_as_entries(sendas({'sendAsEmail': 'sales@acme.com', 'treatAsAlias': True}), 'sam@acme.com'), NOW)
            oa.refresh(db, tg.CONFIG, [], (), NOW)

    def test_confirm_from_the_dashboard(self):
        self.assertEqual(self.app.own_addresses_view()['maybe'][0]['address'], 'sales@acme.com')
        with self.assertRaises(ValueError):
            self.app.action('own_address', {'address': 'sales@acme.com', 'choice': 'confirm'})
        out = self.app.action('own_address', {'address': 'sales@acme.com', 'choice': 'confirm'}, owner_dashboard=True)
        self.assertEqual(out['own_addresses']['maybe'], [])
        self.assertIn('sales@acme.com', self.app.owner_emails())
        self.assertTrue(self.app.graph_dirty.is_set())

    def test_fetch_reads_send_as_once_a_day(self):
        asked = []
        def read(config, account, api, version, method, params, scope):
            asked.append((account['id'], method, scope))
            return sendas({'sendAsEmail': 'sam@acme.com', 'isPrimary': True}, {'sendAsEmail': 'sam@acme.org', 'treatAsAlias': True})
        self.app.config = {'accounts': [{'id': 'mail', 'service': 'gmail', 'transport': 'gog', 'email': 'sam@acme.com'},
                                        {'id': 'cal', 'service': 'calendar', 'transport': 'gog', 'email': 'sam@acme.com'}]}
        self.app.own_addresses_fetch(read)
        self.app.own_addresses_fetch(read)
        self.assertEqual(asked, [('mail', 'gmail.users.settings.sendAs.list', 'https://www.googleapis.com/auth/gmail.readonly')])


if __name__ == '__main__':
    unittest.main()
