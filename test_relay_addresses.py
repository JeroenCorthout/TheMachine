"""System and relay addresses never become people; where the real sender is known, the signal is theirs."""
import types
import unittest

import test_graph as tg
from connectors import intercom
from test_connector_intercom import ACCOUNT

graph = tg.graph


def support(source_id, days_ago, sender, **metadata):
    return {'account_id': 'work', 'source_id': source_id, 'kind': 'intercom.conversation', 'occurred_at': tg.at(days_ago), 'title': 'Login help',
            'body': '', 'deleted': False, 'metadata': {'conversation_id': source_id, 'from': sender, **metadata}}


class Relays(unittest.TestCase):
    add = tg.Graph.add
    build = tg.Graph.build
    node = tg.Graph.node

    def setUp(self):
        tg.Graph.setUp(self)

    def people(self):
        with self.store.connect() as db:
            return {r['id'] for r in db.execute("SELECT id FROM graph_nodes WHERE kind='person'")}

    def test_system_and_relay_addresses_are_never_people(self):
        for address in ('operator+abc123@intercom.io', 'ann.lee@client-1a2b3c.intercom-mail.com', 'author@noreply.github.com',
                        '1234+kim@users.noreply.github.com', 'reply+AB12CD@reply.github.com', 'reply+t0k3n@tool-x.gleap-mail.com',
                        'notification@slack.com', 'calendar-notification@google.com', 'notifications@github.com',
                        'app_no_reply@mail.example', 'no.reply.desk@gov.example', 'do-not-reply@shop.example'):
            self.assertTrue(graph.relay(address), address)
        for address in ('kim@intercom.io', 'ann@slack.com', 'sam.reply@client.example', 'replies@client.example', 'ann@client.example'):
            self.assertFalse(graph.relay(address), address)

    def test_support_opened_by_the_operator_bot_is_the_customers_and_old_ones_are_nobodys(self):
        self.add(support('ic1', 2, 'operator+abc123@intercom.io', contact_email='ann@client.example', contact_name='Ann Lee'),
                 support('ic2', 3, 'operator+abc123@intercom.io'),  # an old event: the customer is not known
                 support('ic3', 4, 'Kim <kim@globex.example>'))
        self.build()
        self.assertEqual(self.people(), {'email:ann@client.example', 'email:kim@globex.example'})
        self.assertIn('wrote to support (Intercom)', self.node('email:ann@client.example')['hints'])

    def test_replies_through_a_relay_and_notification_mail_make_no_people(self):
        self.add(tg.mail(3, 'Sam <sam@acme.com>', 'Ann Lee <ann.lee@client-1a2b3c.intercom-mail.com>', ('SENT',)),
                 tg.mail(2, 'Kim Park <notifications@github.com>', 'Repo <repo@noreply.github.com>', cc='Author <author@noreply.github.com>, sam@acme.com'),
                 tg.mail(2, 'Ann Lee (Slack) <notification@slack.com>', 'sam@acme.com'),
                 tg.mail(1, 'Sam <sam@acme.com>', 'Support <reply+t0k3n@tool-x.gleap-mail.com>, Pat <pat@client.example>', ('SENT',)))
        self.build()
        self.assertEqual(self.people(), {'email:pat@client.example'})

    def test_the_connector_names_the_customer_when_the_operator_bot_opened_it(self):
        raw = {'type': 'conversation', 'id': '7', 'updated_at': 100, 'created_at': 50, 'title': 'Pricing',
               'source': {'body': '<p>Hi! How can we help?</p>', 'author': {'type': 'bot', 'email': 'operator+abc123@intercom.io', 'name': 'Operator'}},
               'conversation_parts': {'conversation_parts': [
                   {'id': 'p2', 'created_at': 70, 'body': '<p>Thanks</p>', 'author': {'type': 'admin', 'name': 'Sam', 'email': 'sam@acme.com'}, 'part_type': 'comment'},
                   {'id': 'p1', 'created_at': 60, 'body': '<p>Question</p>', 'author': {'type': 'user', 'name': 'Ann  Lee', 'email': 'Ann@Client.example'},
                    'part_type': 'comment'}], 'total_count': 2}}
        helpers = types.SimpleNamespace(SourceError=ValueError, iso=lambda t: tg.at(0))
        metadata = intercom.event(ACCOUNT, raw, helpers)['metadata']
        self.assertEqual((metadata['contact_email'], metadata['contact_name']), ('ann@client.example', 'Ann Lee'))
        raw['source']['author'] = {'type': 'lead', 'email': 'kim@globex.example', 'name': 'Kim'}
        self.assertEqual(intercom.event(ACCOUNT, raw, helpers)['metadata']['contact_email'], 'kim@globex.example')


if __name__ == '__main__':
    unittest.main()
