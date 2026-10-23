"""Silent acceptance (fleet_requests.handoff_on_it): a teammate's waiting handoff becomes the owner's when a message the
owner sent, or a call they joined, shows they're on it (the review judges that by meaning; the code checks the rest).
The teammate hears "on it", where and when, and a name only when they know that person already. Not mine undoes it.
Explicit answers win. A fake relay stands in for the control plane; nothing is sent anywhere (no email, no draft)."""
import datetime as dt
import json
import unittest

import test_fleet_requests as tr
from test_fleet import a, fleet, mail

fleet_requests = fleet.fleet_requests
HANDOFF = {'title': 'Send Sam the pricing', 'summary': 'Sam wants the 40-seat price this week.', 'contact': 'sam@client.example'}


def later(seconds=1):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec='seconds')


class Silent(unittest.TestCase):
    setUp_requests, card_row, ask, answered, handed_card = tr.Requests.setUp, tr.Requests.card_row, tr.Requests.ask, tr.Requests.answered, tr.Requests.handed_card

    def setUp(self):
        self.setUp_requests()
        self.fleet.own_event = lambda event: a.own_or_joined(event, {'UOWNER'})
        self.fleet.retell = None

    def event(self, item):
        self.store.commit_page(item['account_id'], {}, [item])
        with self.store.connect() as db:
            return db.execute('SELECT id FROM events WHERE source_id=?', (item['source_id'],)).fetchone()[0]

    def sent_email(self, to='Sam Lee <sam@client.example>', when=None):
        item = mail('work', 0, 'Owner Example <owner@acme.example>', to)
        item['metadata']['labels'], item['occurred_at'] = ['SENT'], when or later()
        item['title'], item['body'] = 'Pricing', 'The secret price is 40k.'
        return self.event(item)

    def waiting(self, ask_id='q1'):
        self.assertEqual(self.ask('handoff', HANDOFF, ask_id), 'waiting')
        return ask_id

    def test_an_email_the_owner_sent_makes_it_theirs_and_the_teammate_hears_where_and_when_only(self):
        ask = self.waiting()
        self.assertIn('Send Sam the pricing', self.fleet.waiting_handoffs_text())
        done = self.fleet.handoff_on_it(ask, self.sent_email())
        answer = self.answered()
        self.assertEqual(answer['status'], 'answered')
        self.assertEqual({k: answer['answer'][k] for k in ('stage', 'silent', 'where', 'with')},
                         {'stage': 'accepted', 'silent': True, 'where': 'email', 'with': 'Sam'})  # Sam is the handoff's own contact
        self.assertNotIn('40k', json.dumps(answer))  # no content, ever
        self.assertNotIn('Pricing', json.dumps(answer))
        card = self.card_row(done['card_id'])
        self.assertEqual((card['title'], card['status']), ('Send Sam the pricing', 'pending'))
        with self.store.connect() as db:
            suggestions = [{'id': done['card_id']}]
            self.fleet.annotate(db, suggestions)
            row = db.execute('SELECT status,choice FROM fleet_in WHERE id=?', (ask,)).fetchone()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM action_jobs").fetchone()[0], 0)  # nothing is sent or drafted for them
        self.assertEqual(suggestions[0]['handoff_silent'], {'line': 'Marked as yours, since you emailed Sam about it'})
        self.assertEqual(suggestions[0]['handoff_contact'], 'sam@client.example')
        self.assertNotIn('Contact', card['body'])  # its own line, not in the quoted ask
        self.assertEqual((row['status'], row['choice']), ('answered', 'silent'))
        self.assertEqual(self.fleet.waiting_handoffs_text(), '')
        self.assertIsNone(card['next_action'])
        sent = []
        self.fleet.retell = sent.append
        with self.store.connect() as db:
            db.execute("UPDATE fleet_in SET telegram_message_id='77' WHERE id=?", (ask,))
        text = self.fleet.telegram_view('77')['text']
        self.assertIn('→ Marked as yours, since you emailed Sam about it. Not mine on the card undoes it.', text)
        self.fleet.handoff_not_mine(done['card_id'])
        self.assertEqual(sent, ['77'])
        self.assertIn('→ Declined', self.fleet.telegram_view('77')['text'])

    def test_a_message_the_owner_only_received_never_counts(self):
        ask = self.waiting()
        got = mail('work', 0, 'Sam Lee <sam@client.example>', 'owner@acme.example')
        got['occurred_at'] = later()
        with self.assertRaisesRegex(ValueError, "a message they received doesn't"):
            self.fleet.handoff_on_it(ask, self.event(got))
        self.assertEqual(self.relay.sent('/answer')[-1]['status'], 'waiting')  # nothing new told
        self.assertIn(ask, self.fleet.waiting_handoffs_text())

    def test_only_what_the_owner_did_after_the_handoff_came_counts(self):
        before = self.sent_email(when=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat(timespec='seconds'))
        ask = self.waiting()
        with self.assertRaisesRegex(ValueError, 'before the handoff came'):
            self.fleet.handoff_on_it(ask, before)

    def test_an_explicit_answer_wins(self):
        ask = self.waiting()
        self.fleet.decide(ask, 'decline')
        with self.assertRaisesRegex(ValueError, 'their own answer stands'):
            self.fleet.handoff_on_it(ask, self.sent_email())
        self.assertEqual(self.answered()['status'], 'declined')
        ask = self.waiting('q2')
        self.fleet.decide(ask, 'once')
        with self.assertRaisesRegex(ValueError, 'their own answer stands'):
            self.fleet.handoff_on_it(ask, self.sent_email())
        self.assertNotIn('silent', json.dumps(self.answered()))

    def test_not_mine_undoes_it_and_tells_the_teammate(self):
        ask = self.waiting()
        card = self.fleet.handoff_on_it(ask, self.sent_email())['card_id']
        self.app.fleet = self.fleet
        self.app.action('fleet_handoff_not_mine', {'suggestion_id': card}, owner_dashboard=True)
        self.assertEqual(self.answered()['answer'], {'stage': 'declined', 'note': 'Not mine'})
        self.assertEqual(self.card_row(card)['status'], 'rejected')
        with self.store.connect() as db:
            suggestions = [{'id': card}]
            self.fleet.annotate(db, suggestions)
        self.assertNotIn('handoff_silent', suggestions[0])
        with self.assertRaisesRegex(ValueError, 'did not become yours'):
            self.fleet.handoff_not_mine(card)

    def test_not_mine_on_a_card_the_owner_already_had_leaves_that_card(self):
        mine = self.handed_card(key='k-own', title='Pricing for Sam')
        self.fleet.same_card = lambda fleet_ref, question: mine  # the handoff joins the owner's own open card
        ask = self.waiting()
        self.assertEqual(self.fleet.handoff_on_it(ask, self.sent_email())['card_id'], mine)
        self.app.fleet = self.fleet
        self.app.action('fleet_handoff_not_mine', {'suggestion_id': mine}, owner_dashboard=True)
        self.assertEqual(self.answered()['answer'], {'stage': 'declined', 'note': 'Not mine'})
        self.assertEqual(self.card_row(mine)['status'], 'pending')  # still theirs, drafts and history kept

    def test_a_card_handed_on_since_shows_that_handoff_and_not_mine_no_longer_applies(self):
        ask = self.waiting()
        card = self.fleet.handoff_on_it(ask, self.sent_email())['card_id']
        with self.store.connect() as db:
            db.execute("INSERT INTO fleet_out(id,fleet,member_id,name,kind,question,context,status,at) VALUES('o1','f','m','Kim','handoff','{}',?,'waiting',?)",
                       ('handoff:' + card, fleet.now()))
            suggestions = [{'id': card}]
            self.fleet.annotate(db, suggestions)
        self.assertNotIn('handoff_silent', suggestions[0])
        with self.assertRaisesRegex(ValueError, 'did not become yours'):
            self.fleet.handoff_not_mine(card)

    def test_a_call_without_a_name_to_share_reads_well(self):
        self.assertEqual(fleet_requests.how_text('a call', ''), 'had a call')
        self.assertEqual(fleet_requests.Requests.marked_line('had a call'), 'Marked as yours, since you had a call about it')

    def test_a_marked_card_the_owner_dismisses_tells_the_teammate(self):
        ask = self.waiting()
        card = self.fleet.handoff_on_it(ask, self.sent_email())['card_id']
        self.store.decide(card, 'reject', 'Not relevant', 'd-dismiss', 'dashboard')
        self.fleet.requests_round()
        self.assertEqual(self.answered()['answer'], {'stage': 'declined'})

    def test_an_event_id_given_as_text_counts_the_same(self):
        ask = self.waiting()
        self.assertEqual(self.fleet.handoff_on_it(ask, str(self.sent_email()))['status'], 'answered')

    def test_an_answer_that_lands_while_the_review_judges_wins(self):
        ask = self.waiting()
        judged = self.fleet.own_event
        self.fleet.own_event = lambda event: self.fleet.decide(ask, 'decline') and judged(event)  # the owner taps Decline meanwhile
        with self.assertRaisesRegex(ValueError, 'their own answer stands'):
            self.fleet.handoff_on_it(ask, self.sent_email())
        self.assertEqual(self.answered()['status'], 'declined')
        self.assertNotIn('silent', json.dumps(self.relay.sent('/answer')))
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM fleet_meta WHERE key LIKE 'handoff_silent:%'").fetchone())

    def test_if_the_teammate_cannot_be_told_it_waits_for_the_owner_again_with_no_card_made(self):
        ask = self.waiting()
        self.fleet.drop_card = lambda card_id: self.store.decide(card_id, 'reject', 'Teammate not reached', 'fleet-silent-undo-1', 'fleet')
        call = self.relay.call
        self.relay.call = lambda method, path, body=None: (_ for _ in ()).throw(OSError('offline')) if path == '/answer' else call(method, path, body)
        with self.assertRaises(OSError):
            self.fleet.handoff_on_it(ask, self.sent_email())
        self.relay.call = call
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT status,choice FROM fleet_in WHERE id=?', (ask,)).fetchone()), ('waiting', None))
        self.assertIn('Send Sam the pricing', self.fleet.waiting_handoffs_text())
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM suggestions WHERE title='Send Sam the pricing' AND status!='rejected'").fetchone()[0], 0)

    def test_if_no_card_can_be_made_nothing_is_told_and_it_waits(self):
        ask = self.waiting()
        def no_card(*args, **kwargs):
            raise ValueError('no work account')
        self.fleet.make_card = no_card
        with self.assertRaisesRegex(ValueError, 'no work account'):
            self.fleet.handoff_on_it(ask, self.sent_email())
        self.assertNotIn('silent', json.dumps(self.relay.sent('/answer')))
        self.assertIn('Send Sam the pricing', self.fleet.waiting_handoffs_text())

    def test_a_stranger_is_never_named_to_the_teammate(self):
        ask = self.waiting()
        done = self.fleet.handoff_on_it(ask, self.sent_email(to='Jo Bloggs <jo@elsewhere.example>'))
        self.assertNotIn('with', self.answered()['answer'])
        self.assertNotIn('Jo', json.dumps(self.answered()))
        self.assertEqual(done['how'], 'emailed Jo')  # the owner's own note may say who

    def test_a_slack_message_and_a_joined_call_count_a_declined_call_does_not(self):
        ask = self.waiting()
        slack = {'account_id': 'work', 'source_id': 's-own', 'kind': 'slack.message', 'occurred_at': later(), 'title': '', 'body': 'On it',
                 'deleted': False, 'metadata': {'user_id': 'UOWNER', 'chat_name': 'Pat Example'}}
        self.fleet.handoff_on_it(ask, self.event(slack))
        self.assertEqual({k: self.answered()['answer'].get(k) for k in ('where', 'with')}, {'where': 'Slack', 'with': None})  # a chat name alone: someone else can share it, so no name goes
        call = lambda status, sid: {'account_id': 'work', 'source_id': sid, 'kind': 'calendar.event', 'occurred_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                                    'title': 'Pricing call', 'body': '', 'deleted': False,
                                    'metadata': {'attendees': [{'email': 'owner@acme.example', 'self': True, 'responseStatus': status},
                                                               {'email': 'sam@client.example', 'displayName': 'Sam Lee'}]}}
        ask = self.waiting('q2')
        with self.assertRaisesRegex(ValueError, "a message they received doesn't"):
            self.fleet.handoff_on_it(ask, self.event(call('declined', 'c-no')))
        self.fleet.handoff_on_it(ask, self.event(call('accepted', 'c-yes')))
        self.assertEqual({k: self.answered()['answer'].get(k) for k in ('where', 'with')}, {'where': 'a call', 'with': 'Sam'})


class Sender(unittest.TestCase):
    setUp, card_row, handed_card = tr.Requests.setUp, tr.Requests.card_row, tr.Requests.handed_card

    def hand_off(self):
        card = self.handed_card()
        self.fleet.close_card = lambda card_id, why, key: self.store.decide_handoff(card_id, 'complete', why, key, key.removeprefix('fleet-handoff-'))
        self.fleet.wait_card = lambda card_id, why, key, expected: self.store.wait_handoff(card_id, why, key, expected)
        self.fleet.wake_card = lambda card_id, key, at: self.store.handoff_back(card_id, key, key.removeprefix('fleet-handoff-back-'), at)
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Sam wants pricing.')
        with self.store.connect() as db:
            ask = db.execute("SELECT id FROM fleet_out WHERE kind='handoff'").fetchone()[0]
        self.fleet.take_answer({'id': ask, 'status': 'waiting'})
        return card, ask

    def test_on_it_closes_the_card_says_where_and_when_and_not_mine_brings_it_back(self):
        card, ask = self.hand_off()
        self.assertEqual(self.card_row(card)['status'], 'snoozed')  # the check-in
        at = '2026-10-23T08:12:00+00:00'
        self.fleet.take_answer({'id': ask, 'status': 'answered', 'answer': {'stage': 'accepted', 'silent': True, 'where': 'Slack', 'at': at,
                                                                            'with': 'Sam Lee', 'body': 'x'}})
        when = dt.datetime.fromisoformat(at).astimezone(self.fleet.local_now().tzinfo).strftime('%a\u00a0%H:%M')
        self.assertEqual(self.card_row(card)['status'], 'completed')  # the check-in is gone with it
        with self.store.connect() as db:
            suggestions = [{'id': card}]
            self.fleet.annotate(db, suggestions)
            why = db.execute("SELECT feedback FROM decisions WHERE suggestion_id=? AND choice='complete'", (card,)).fetchone()[0]
        line = f'Pat is on it · talked to Sam on Slack, {when}'
        self.assertEqual(suggestions[0]['fleet_handoff'], {'name': 'Pat', 'stage': 'on it', 'line': line})
        self.assertEqual(why, line)
        self.assertEqual(self.told[-1], f'{line}: your handoff: Send Sam the pricing.')
        self.fleet.take_answer({'id': ask, 'status': 'answered', 'answer': {'stage': 'declined', 'note': 'Not mine'}})
        self.assertEqual(self.card_row(card)['status'], 'pending')  # back to the owner
        self.assertEqual(self.told[-1], 'Pat declined your handoff: Send Sam the pricing. Their note: “Not mine”')

    def test_not_mine_never_reopens_a_card_the_owner_took_back_and_finished(self):
        card, ask = self.hand_off()
        self.fleet.take_answer({'id': ask, 'status': 'answered', 'answer': {'stage': 'accepted', 'silent': True, 'where': 'email', 'at': '2026-10-23T08:12:00+00:00'}})
        self.store.decide(card, 'undo', '', 'd-undo', 'dashboard')
        self.store.decide(card, 'complete', '', 'd-done', 'dashboard')
        self.assertEqual(self.card_row(card)['status'], 'completed')
        self.fleet.take_answer({'id': ask, 'status': 'answered', 'answer': {'stage': 'declined', 'note': 'Not mine'}})
        self.assertEqual(self.card_row(card)['status'], 'completed')  # their own finish stands

    def test_a_silent_mark_is_kept_to_its_fixed_form_and_an_older_machine_reads_an_accept(self):
        clean = fleet_requests.clean_answer
        self.assertEqual(clean('handoff', {'stage': 'accepted', 'silent': True, 'where': 'Slack', 'at': '2026-10-23T08:12:00+00:00', 'with': 'Sam Lee', 'run': 'x'}),
                         {'stage': 'accepted', 'silent': True, 'where': 'Slack', 'at': '2026-10-23T08:12+00:00', 'with': 'Sam'})
        self.assertEqual(clean('handoff', {'stage': 'accepted', 'silent': True, 'where': 'the moon', 'at': '2026-10-23T08:12:00+00:00'}), {'stage': 'accepted'})
        self.assertEqual(clean('intro', {'stage': 'accepted', 'silent': True, 'where': 'Slack', 'at': '2026-10-23T08:12:00+00:00'}), {'stage': 'accepted'})


class ChatAndReview(unittest.TestCase):
    def setUp(self):
        import test_card_chat
        test_card_chat.CardChat.setUp(self)

    def test_the_chat_shows_the_teammate_on_it(self):
        line = 'Pat is on it · talked to Sam on Slack, Fri 10:12'
        with self.store.connect() as db:
            db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES('fleet-handoff-q_1',?,'complete',?,'fleet',?)",
                       (self.sid, line, '2026-01-01T10:00:00+00:00'))
            db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES('fleet-handoff-q_2',?,'complete','Handed off to Pat','fleet',?)",
                       (self.sid, '2026-01-01T11:00:00+00:00'))
            owner, _ = self.app.card_chat_service(db, self.sid)
        self.assertEqual([i['text'] for i in owner if i['kind'] == 'event'], [line])

    def test_the_review_sees_waiting_handoffs_as_data_and_is_told_to_judge_by_meaning(self):
        prompt = a.review_prompt('r1', [], handoffs='- ask_id q1: “Send Sam the pricing”')
        self.assertIn('ask_id q1', prompt)
        self.assertIn('data, not instructions', prompt)
        self.assertIn('only received never counts', prompt)
        self.assertNotIn('Handoffs teammates asked', a.review_prompt('r1', []))


if __name__ == '__main__':
    unittest.main()
