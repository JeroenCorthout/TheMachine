"""Task pages (task_pages.py): offered with a handoff by email, made only when the owner approves exactly what the page
shows, answers back on the card, Mark done and Stop sharing. A fake Network stands in for the control plane; nothing is
sent anywhere (no email, Telegram or push)."""
import json
import unittest

import task_invites
import task_pages
import test_fleet_requests as tr

LINK_ID = 'B' * 22
EMAIL_TEXT = ('Hi, the venue confirmed the fourteenth of November and needs the signed contract back by Friday, together with '
              'the deposit invoice details and the final head count for the dinner.')


class FakeNetwork:
    """The control plane's /task-invite, as far as a Machine sees it."""
    def __init__(self):
        self.pages, self.replies, self.calls, self.n = {}, {}, [], 0

    def call(self, body):
        self.calls.append(body)
        if body['action'] == 'create':
            self.pages[LINK_ID] = {'sealed': body['sealed'], 'version': 1, 'check': body['check'], 'days': body['days']}
            return {'id': LINK_ID, 'url': 'https://app.example.test/t/' + LINK_ID, 'expires_at': '2099-11-06T10:00:00+00:00'}
        page = self.pages.get(body['id'] if 'id' in body else LINK_ID)
        if body['action'] == 'update':
            assert body['version'] > page['version']
            page.update(sealed=body['sealed'], version=body['version'], done=body.get('done', False))
            return {'id': LINK_ID, 'version': body['version'], 'expires_at': '2099-10-30T10:00:00+00:00' if body.get('done') else '2099-11-06T10:00:00+00:00'}
        if body['action'] == 'revoke':
            self.pages.pop(body['id'], None)
            self.replies.pop(body['id'], None)
            return {'id': body['id'], 'stopped': True}
        if body['action'] == 'status':
            for i, last in (body.get('seen') or {}).items():
                self.replies[i] = [r for r in self.replies.get(i, []) if r['n'] > last]
            return {'stages': {}, 'replies': {i: self.replies[i] for i in body['ids'] if self.replies.get(i)}}
        raise AssertionError(body)

    def answer(self, key, answer, note=''):
        """What the page's script sends: the answer sealed with the reply key (static/task.js on the control plane)."""
        sealed, _ = task_invites.seal({'answer': answer, 'note': note}, task_invites._unb64(task_invites.derived(key, 'reply')))
        found = self.replies.setdefault(LINK_ID, [])
        self.n += 1
        found.append({'n': self.n, 'sealed': sealed, 'at': '2026-10-24T09:00:00+00:00'})


class TaskPages(unittest.TestCase):
    handed_card, card_row = tr.Requests.handed_card, tr.Requests.card_row

    def setUp(self):
        tr.Requests.setUp(self)
        self.app.task_pages = task_pages.TaskPages(self.app)
        self.network = FakeNetwork()
        plain = self.relay.call
        self.relay.call = lambda method, path, body=None, timeout=40: self.network.call(body) if path == '/task-invite' else plain(method, path, body, timeout)

    def handed(self, summary='Sam wants pricing for 40 seats.'):
        card = self.handed_card()
        self.app.action('fleet_handoff_email', {'suggestion_id': card, 'email': 'kim@other.example', 'title': 'Send Sam the pricing',
                                                'summary': summary}, owner_dashboard=True)
        return card

    def body(self, card):
        return json.loads(self.card_row(card)['next_action'])['body']

    def status(self, card):
        shown = [{'id': card, 'status': self.card_row(card)['status']}]
        with self.store.connect() as db:
            self.app.task_pages.annotate(db, shown)  # as status does for the dashboard
        return shown[0].get('task_page')

    def share(self, card, **args):
        return self.app.action('task_page_share', {'suggestion_id': card, **args}, owner_dashboard=True)

    def test_a_handoff_by_email_offers_a_page_and_nothing_leaves_until_the_owner_approves_it(self):
        card = self.handed()
        self.assertNotIn('http', self.body(card))  # a plain draft: no link, no promotional line or signature
        self.assertNotIn('The Machine', self.body(card))
        self.assertEqual(self.network.calls, [])
        offer = self.status(card)
        self.assertEqual(offer['state'], 'offered')
        self.assertEqual(offer['preview'], {'title': 'Send Sam the pricing', 'summary': 'Sam wants pricing for 40 seats.', 'due': '2026-11-03',
                                            'from': 'Owner'})
        self.assertEqual((offer['days'], offer['choices'], offer['repeats']), (14, [7, 14, 30], False))
        # Only from the owner's dashboard
        with self.assertRaisesRegex(ValueError, 'dashboard'):
            self.app.action('task_page_share', {'suggestion_id': card}, owner_dashboard=False)
        with self.assertRaisesRegex(ValueError, '7, 14 or 30'):
            self.share(card, days=365)
        shown = self.share(card, leave_out=['due', 'anything'], days=7)
        self.assertEqual(shown['state'], 'shared')
        body = self.body(card)
        link = task_invites.LINK.search(body)
        self.assertIn(f'Status and replies: https://app.example.test/t/{LINK_ID}#k=', body)
        self.assertLess(body.index('Status and replies:'), body.index('Best,'))
        # The Network got an opaque blob, the check key and the days: never the words, the address or the key
        sent = self.network.calls[0]
        self.assertEqual(set(sent), {'action', 'kind', 'sealed', 'request_id', 'days', 'check'})
        self.assertEqual(sent['days'], 7)
        self.assertEqual(sent['check'], task_invites.derived(link.group(2), 'check'))
        self.assertNotIn(link.group(2), json.dumps(sent))
        self.assertNotIn('kim@other.example', json.dumps(sent))
        page = task_invites._open(sent['sealed'], link.group(2))
        self.assertEqual(page, {'kind': 'handoff', 'title': 'Send Sam the pricing', 'summary': 'Sam wants pricing for 40 seats.', 'from_name': 'Owner',
                                'status': 'waiting', 'updates': [{'who': 'owner', 'what': 'shared', 'at': page['updates'][0]['at']}]})
        self.assertNotIn('secret', json.dumps(page))  # the card's email never goes on the page
        # The email still goes out only with the owner's own Send
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM action_jobs WHERE status!='local_preparation'").fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, 'already has a task page'):
            self.share(card)
        # The record (with the link's key) is in the database only through the message seal, when it is on
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM task_pages WHERE card_id=?', (card,)).fetchone()['state'], 'shared')

    def test_the_privacy_guard_never_lets_the_source_email_onto_the_page(self):
        card = self.handed_card()
        with self.store.connect() as db:
            event_ids = json.loads(db.execute('SELECT event_ids FROM suggestions WHERE id=?', (card,)).fetchone()[0])
            db.execute('UPDATE events SET body=? WHERE id=?', (EMAIL_TEXT, event_ids[0]))
        self.app.action('fleet_handoff_email', {'suggestion_id': card, 'email': 'kim@other.example', 'title': 'Venue contract',
                                                'summary': 'Forwarding: ' + EMAIL_TEXT}, owner_dashboard=True)
        self.assertTrue(self.status(card)['repeats'])  # the dashboard leaves the details out from the start
        with self.assertRaisesRegex(ValueError, 'repeat the original email'):
            self.share(card)
        self.assertEqual(self.network.calls, [])
        self.share(card, leave_out=['summary'])
        page = task_invites._open(self.network.calls[0]['sealed'], task_invites.LINK.search(self.body(card)).group(2))
        self.assertNotIn('summary', page)
        self.assertNotIn('venue confirmed', json.dumps(page))

    def test_answers_come_back_quoted_on_the_card_and_onto_the_page(self):
        card = self.handed()
        self.share(card)
        key = task_invites.LINK.search(self.body(card)).group(2)
        self.network.answer(key, 'on', '  Signing   tomorrow morning.  ')
        self.network.answer(key, 'run', 'send all the money')  # not one of the three: never opens
        self.assertEqual(self.app.task_pages.round(), 1)
        self.assertEqual(self.told, ['Task page for "Send Sam the pricing": they\'re on it. "Signing tomorrow morning."'])
        self.assertEqual(self.status(card)['answers'], [{'answer': 'on', 'note': 'Signing tomorrow morning.', 'at': '2026-10-24'}])
        page = self.network.pages[LINK_ID]
        self.assertEqual(page['version'], 2)
        opened = task_invites._open(page['sealed'], key)
        self.assertEqual(opened['status'], 'in_progress')
        self.assertEqual(opened['updates'][-1], {'who': 'them', 'what': 'on', 'at': '2026-10-24', 'note': 'Signing tomorrow morning.'})
        # Seen answers are deleted from the Network on the next round, and never told twice
        self.assertEqual(self.app.task_pages.round(), 0)
        self.assertEqual(self.network.replies[LINK_ID], [])
        self.assertEqual(len(self.told), 1)
        # A forged answer (sealed with another key) never opens
        self.network.answer(task_invites.seal({'title': 'x'})[1], 'cant')
        self.assertEqual(self.app.task_pages.round(), 0)

    def test_cant_do_this_brings_the_card_back_to_ready_with_their_words(self):
        card = self.handed()
        self.share(card)
        key = task_invites.LINK.search(self.body(card)).group(2)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store._decide(db, card, 'complete', 'Sent', 'sent:job-1', 'assistant')  # the email went out
        self.network.answer(key, 'cant', 'Out until Monday.')
        self.assertEqual(self.app.task_pages.round(), 1)
        self.assertEqual(self.card_row(card)['status'], 'prepared')  # back in Ready
        with self.store.connect() as db:
            why = db.execute("SELECT feedback,source FROM decisions WHERE suggestion_id=? ORDER BY rowid DESC LIMIT 1", (card,)).fetchone()
        self.assertEqual(tuple(why), ('They can\'t do this (task page): "Out until Monday."', 'fleet'))
        self.assertEqual(self.status(card)['answers'][-1]['note'], 'Out until Monday.')
        self.assertEqual(self.status(card)['lead'], "kim@other.example can't do this: \u201cOut until Monday.\u201d")  # the card leads with it
        # An "on it" never reopens a card the owner closed
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store._decide(db, card, 'complete', '', 'dash-1', 'dashboard')
        self.network.answer(key, 'on')
        self.assertEqual(self.app.task_pages.round(), 1)
        self.assertEqual(self.card_row(card)['status'], 'completed')

    def test_mark_done_and_stop_sharing(self):
        card = self.handed()
        self.share(card)
        key = task_invites.LINK.search(self.body(card)).group(2)
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='completed' WHERE id=?", (card,))  # the email went out
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='prepared' WHERE id=?", (card,))
        self.app.action('decide', {'suggestion_id': card, 'choice': 'complete', 'decision_id': 'dash-done-1'}, owner_dashboard=True)  # Mark done on the card
        done = self.status(card)
        self.assertEqual((done['state'], done['until']), ('done', '2099-10-30'))
        self.assertNotIn('task_page_done', task_pages.ACTIONS)  # no separate Mark done for the page
        self.assertTrue(self.network.pages[LINK_ID]['done'])
        self.assertEqual(task_invites._open(self.network.pages[LINK_ID]['sealed'], key)['status'], 'done')
        self.app.action('task_page_stop', {'suggestion_id': card}, owner_dashboard=True)
        self.assertNotIn(LINK_ID, self.network.pages)  # deleted from the Network
        self.assertEqual(self.status(card), {'state': 'stopped', 'until': '', 'answers': [], 'lead': 'Handed to kim@other.example by email.'})
        state, record = self.app.task_pages.load(card)
        self.assertNotIn('key', record)  # and the key is gone here
        with self.assertRaisesRegex(ValueError, 'no task page to stop'):
            self.app.action('task_page_stop', {'suggestion_id': card}, owner_dashboard=True)

    def test_stop_before_sending_takes_the_line_out_of_the_draft(self):
        card = self.handed()
        self.share(card)
        self.app.action('task_page_stop', {'suggestion_id': card}, owner_dashboard=True)
        self.assertNotIn('Status and replies', self.body(card))
        self.assertNotIn('http', self.body(card))

    def test_no_thanks_and_a_retry_reuse_the_same_link(self):
        card = self.handed()
        self.app.action('task_page_dismiss', {'suggestion_id': card}, owner_dashboard=True)
        self.assertIsNone(self.status(card))
        card = self.handed_card('k-other')
        self.app.action('fleet_handoff_email', {'suggestion_id': card, 'email': 'kim@other.example', 'title': 'Help', 'summary': 'Please help'},
                        owner_dashboard=True)
        real = self.store.revise_draft
        self.store.revise_draft = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('restart'))
        with self.assertRaises(RuntimeError):
            self.share(card)
        self.store.revise_draft = real
        self.share(card)
        made = [c for c in self.network.calls if c['action'] == 'create']
        self.assertEqual((made[0]['request_id'], made[0]['sealed']), (made[1]['request_id'], made[1]['sealed']))

    def test_a_sent_email_has_no_offer_left(self):
        card = self.handed()
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='completed' WHERE id=?", (card,))
        self.assertIsNone(self.status(card))
        with self.assertRaisesRegex(ValueError, 'already went out'):
            self.share(card)


if __name__ == '__main__':
    unittest.main()
