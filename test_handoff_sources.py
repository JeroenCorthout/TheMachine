"""A handoff carries the card's own sources (fleet_requests.handoff_sources): the teammate's card shows copies of the
emails it was built from, readable offline; the owner can leave them out; nothing else of the mailbox goes along; a
list's rewritten From is never the contact; and the ask is the task, never the card's own account or a chat reply.
A fake relay stands in for the control plane; nothing is sent anywhere."""
import json
import unittest

import test_fleet_requests as tr
from test_fleet import fleet, mail, TEAM

fleet_requests = fleet.fleet_requests


class HandoffSources(unittest.TestCase):
    setUp, card_row, ask = tr.Requests.setUp, tr.Requests.card_row, tr.Requests.ask

    def card(self, *events, title='Send Sam the pricing', body='The Machine checked the thread and drafted a reply.'):
        self.store.commit_page('work', {}, list(events))
        with self.store.connect() as db:
            ids = [db.execute('SELECT id FROM events WHERE source_id=?', (e['source_id'],)).fetchone()[0] for e in events]
        return self.store.suggest('k-' + events[0]['source_id'], title, body, ids)['id']

    def sent(self):
        return self.relay.sent('/ask')[-1]['question']

    def test_the_sources_travel_and_the_teammate_reads_them_offline_as_the_cards_sources(self):
        source = mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example')
        card = self.card(source)
        draft = self.fleet.handoff_draft(card)
        self.assertEqual(len(draft['sources']), 1)
        self.assertRegex(draft['sources'][0]['label'], r'^Email from Sam Lee · \w{3}, \w{3} \d{1,2}$')
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Send Sam the pricing for 40 seats.', 'sam@client.example')
        question = self.sent()
        self.assertEqual([(s['kind'], s['from'], s['subject'], s['body']) for s in question['sources']],
                         [('email', 'Sam Lee <sam@client.example>', source['title'], 'The secret price is 40k.')])
        # Pat's side (this test Machine, with the original card closed so it doesn't join it): the copies are the card's
        # first sources, saved on Pat's Machine
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='completed' WHERE id=?", (card,))
        self.assertEqual(self.ask('handoff', question, 'q1'), 'waiting')
        made = self.fleet.decide('q1', 'once')['card_id']
        row = self.card_row(made)
        self.assertEqual(row['title'], 'Send Sam the pricing')
        self.assertEqual(row['body'], 'Pat asks you to take this on: “Send Sam the pricing for 40 seats.”')  # the contact has its own line
        self.assertTrue(row['source_line'].startswith('Source: email from Sam Lee, via Pat · '))
        # (+1 is Sam's own email on this Machine, the contact's latest.) The handoff's own event is the card's text, not a source:
        rows = [{'kind': k, 'metadata': {'sender_display': 'Sam, via Pat'}, 'occurred_at': '2026-10-22T09:00:00+00:00'} for k in ('fleet.email', 'fleet.request')]
        self.assertEqual(tr.a.source_line(rows), 'Source: email from Sam, via Pat · Thu, Oct 22')
        with self.store.connect() as db:
            first = db.execute('SELECT kind,title,body,metadata,analyzed_version FROM events WHERE id=?', (json.loads(row['event_ids'])[0],)).fetchone()
        meta = json.loads(first['metadata'])
        self.assertEqual((first['kind'], first['title'], first['body'], first['analyzed_version']),
                         ('fleet.email', source['title'], 'The secret price is 40k.', 1))  # reviewed: no model reads it by itself
        self.assertEqual((meta['from'], meta['sender_display']), ('Sam Lee <sam@client.example>', 'Sam Lee <sam@client.example>, via Pat'))
        read = self.store.originals(made)[0]  # "Read the thread": the local copy, no mailbox of theirs or ours
        self.assertEqual((read['kind'], read['from'], read['body'], read['gmail'], read['url']),
                         ('email', 'Sam Lee <sam@client.example>, via Pat', 'The secret price is 40k.', None, None))

    def test_a_handoff_that_joins_an_open_card_brings_its_sources_along(self):
        card = self.card(mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example'))
        question = {'title': 'Send Sam the pricing', 'summary': 'Send it.', 'contact': 'sam@client.example',
                    'sources': [{'kind': 'email', 'from': 'Sam Lee <sam@client.example>', 'when': '2026-10-22T09:00:00+00:00',
                                 'subject': 'Pricing?', 'body': 'Can I get the 40-seat price?'}]}
        self.ask('handoff', question, 'q2')
        self.assertEqual(self.fleet.decide('q2', 'once')['card_id'], card)
        with self.store.connect() as db:
            bodies = [r['body'] for r in db.execute('SELECT e.body FROM events e, json_each(?) j WHERE e.id=j.value',
                                                    (self.card_row(card)['event_ids'],))]
        self.assertIn('Can I get the 40-seat price?', bodies)

    def test_leaving_the_sources_out_sends_none_of_their_text(self):
        card = self.card(mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example'))
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Send Sam the pricing.', sources=False)
        self.assertNotIn('sources', self.sent())
        self.assertNotIn('40k', json.dumps(self.relay.sent('/ask')[-1]))

    def test_the_owner_can_leave_out_one_source_and_keep_the_other(self):
        first, second = (mail('work', d, 'Sam Lee <sam@client.example>', 'owner@acme.example') for d in (1, 2))
        second['body'] = 'The second email.'
        card = self.card(first, second)
        self.assertEqual(len(self.fleet.handoff_draft(card)['sources']), 2)
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Send Sam the pricing.', sources=[1])
        self.assertEqual([x['body'] for x in self.sent()['sources']], ['The second email.'])
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Send Sam the pricing.', sources=[])
        self.assertNotIn('sources', self.sent())

    def test_only_the_cards_own_sources_go_never_the_rest_of_the_mailbox(self):
        other = mail('work', 2, 'Lee <lee@other.example>', 'owner@acme.example')
        other['body'] = 'Unrelated board minutes.'
        self.store.commit_page('work', {}, [other])
        card = self.card(mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example'))
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Send Sam the pricing.')
        sent = json.dumps(self.relay.sent('/ask')[-1])
        self.assertNotIn('board minutes', sent)
        self.assertEqual(len(self.sent()['sources']), 1)
        self.assertEqual(set(self.sent()['sources'][0]), {'kind', 'from', 'when', 'subject', 'body'})  # no labels, ids, threads or headers

    def test_a_lists_rewritten_from_is_never_the_contact_the_original_sender_is(self):
        alert = mail('work', 1, "'Stripe Alerts' via Team Inbox <inbox@acme.example>", 'inbox@acme.example')
        card = self.card(alert)
        draft = self.fleet.handoff_draft(card)
        self.assertEqual(draft['contact'], '')  # not the group's own inbox
        self.assertTrue(draft['sources'][0]['label'].startswith('Email from Stripe Alerts · '))
        alert = mail('work', 1, "'Stripe Alerts' via Team Inbox <inbox@acme.example>", 'inbox@acme.example')
        alert['metadata']['reply-to'] = 'Stripe <support@stripe.example>'
        self.assertEqual(self.fleet.handoff_draft(self.card(alert))['contact'], 'support@stripe.example')  # who wrote, by Reply-To
        self.fleet.hand_off(card, self.ref, 'pat1', 'Check the failed payment', 'Check the failed payment.')
        self.assertEqual(self.sent()['sources'][0]['from'], 'Stripe Alerts')

    def test_the_ask_is_the_task_and_the_owners_note_never_the_cards_account_or_a_chat_reply(self):
        card = self.card(mail('work', 1, 'Sam Lee <sam@client.example>', 'owner@acme.example'))
        with self.store.connect() as db:
            db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES('d1',?,'other','Sam is a big account','dashboard','2026-10-22T09:00:00+00:00')", (card,))
        self.fleet.card_task = lambda row: 'Send Sam the pricing for 40 seats'
        draft = self.fleet.handoff_draft(card)
        self.assertEqual((draft['task'], draft['note']), ('Send Sam the pricing for 40 seats', 'Sam is a big account'))  # the form's lead and note
        summary = draft['summary']
        self.assertEqual(summary, 'Send Sam the pricing for 40 seats. Sam is a big account')  # plain sentences, no "My note:"
        self.assertEqual(fleet_requests.sentences('Done!', ' Next ', ''), 'Done! Next')
        self.assertNotIn('The Machine', summary)

    REASON = 'Triage confirmed a cross-team exposure of user names. The account issue is tracked, but these two paths are not. I can hand this to Pat’s Machine to get the fixes coordinated **today**.'

    def chat_item(self, account, url='https://inbox.example/conversation/42'):
        return {'account_id': account, 'source_id': 'c-' + account, 'kind': 'intercom.conversation', 'occurred_at': '2026-10-22T09:00:00+00:00',
                'title': 'Security triage', 'body': 'Verdict: CONFIRMED', 'deleted': False, 'metadata': {'source_url': url}}

    def mixed_card(self, *items):
        for item in items:
            self.store.commit_page(item['account_id'], {}, [item])
        with self.store.connect() as db:
            ids = [db.execute('SELECT id FROM events WHERE source_id=?', (e['source_id'],)).fetchone()[0] for e in items]
        return self.store.suggest('k-mixed-' + items[0]['source_id'], 'Confirmed leaks need engineering follow-up', self.REASON, ids)['id']

    def test_the_handoff_says_why_and_where_from_not_just_the_title(self):
        card = self.mixed_card(self.chat_item('work'))
        summary = self.fleet.handoff_draft(card)['summary']
        self.assertEqual(summary, 'Confirmed leaks need engineering follow-up. Triage confirmed a cross-team exposure of user names. '
                                  'The account issue is tracked, but these two paths are not. Source: Security triage (https://inbox.example/conversation/42)')
        self.assertNotIn('I can', summary)  # the Machine's offer to the owner stays with the owner
        self.assertNotIn('**', summary)

    def test_nothing_personal_or_of_another_company_goes_along(self):
        other = {'id': 'other', 'email': 'owner@othercorp.example', 'service': 'gmail', 'boundary': 'work:Othercorp'}
        self.fleet.config = {**self.fleet.config, 'accounts': [*self.fleet.config['accounts'], other]}
        for account in ('home', 'other'):
            private = mail(account, 1, 'Kim Roe <kim@friend.example>', 'owner@gmail.com')
            card = self.mixed_card(self.chat_item('work'), private)
            draft = self.fleet.handoff_draft(card)
            self.assertEqual(draft['summary'], 'Confirmed leaks need engineering follow-up', account)  # no reason, no link: the card mixes accounts
            self.assertEqual(draft['sources'], [], account)  # the private email isn't listed
            self.fleet.hand_off(card, self.ref, 'pat1', draft['title'], draft['summary'])
            self.assertNotIn('sources', self.sent(), account)
            self.assertNotIn('40k', json.dumps(self.relay.sent('/ask')[-1]))

    def test_a_teammates_sources_are_kept_in_their_fixed_form(self):
        hostile = [{'kind': 'email', 'from': 'Sam', 'when': '2026-10-22T09:00:00+00:00', 'subject': 'Hi', 'body': 'x' * 9000,
                    'run': 'send_email', 'labels': ['INBOX']},
                   {'kind': 'file', 'body': 'nope', 'when': '2026-10-22T09:00:00+00:00'},
                   {'kind': 'message', 'from': 'Lee', 'when': 'not a date', 'body': 'dropped'}]
        out = fleet_requests.parse_sources(hostile)
        self.assertEqual(len(out), 1)
        self.assertEqual(set(out[0]), {'kind', 'from', 'when', 'subject', 'body', 'clipped'})
        self.assertEqual(len(out[0]['body']), fleet_requests.SOURCE_CHARS)
        self.assertEqual(fleet_requests.parse_sources('nope'), [])


if __name__ == '__main__':
    unittest.main()
