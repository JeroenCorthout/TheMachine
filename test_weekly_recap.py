"""The weekly recap is something to act on: what needs the owner first, the week ahead, what changed, then one-tap
choices that change only the owner's own filters and cards. No live calls: Telegram and pushes are stubbed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import test_ux as ux
a = ux.a
dt = a.dt

FRIDAY = '2026-10-23T14:05:00+00:00'  # 16:05 in Brussels
WA = {'id': 'wa', 'service': 'whatsapp', 'display_name': 'Phone WhatsApp', 'boundary': 'personal:Owner'}
MAIL = {'id': 'work', 'service': 'gmail', 'email': 'owner@example.com', 'boundary': 'work:Example'}
CAL = {'id': 'cal', 'service': 'calendar', 'email': 'owner@example.com', 'boundary': 'work:Example'}


def ago(hours):
    return (dt.datetime.fromisoformat(FRIDAY) - dt.timedelta(hours=hours)).isoformat()


class WeeklyRecap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(a, 'LOCAL_TZ', ZoneInfo('Europe/Brussels')), patch.object(a, 'now', return_value=FRIDAY),
                  patch.object(a, 'assistant_token_usage', return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.path = Path(self.tmp.name) / 'config.json'
        self.config = {'accounts': [WA, MAIL, CAL], 'dashboard_host': 'm.example', 'dashboard_path': '/assistant/', 'telegram_owner': '1',
                       'weekly_recap': {'enabled': True}, 'database': str(Path(self.tmp.name) / 'events.sqlite')}
        self.path.write_text(json.dumps(self.config))
        self.store = a.Store(self.config['database'])
        self.app = object.__new__(a.App)
        self.app.store, self.app.config, self.app.config_path = self.store, json.loads(self.path.read_text()), self.path
        self.store.exclusions = {}
        # A busy WhatsApp group that never led to a card, and a person's own chat that is never a filter candidate.
        self.store.commit_page('wa', {}, [{'account_id': 'wa', 'source_id': f'g{i}', 'kind': 'whatsapp.message', 'occurred_at': ago(5), 'title': 'x', 'body': 'y',
                                           'metadata': {'chat_id': '123@g.us', 'chat_name': 'School parents', 'sender_id': f'3247{i % 15}@s.whatsapp.net'},
                                           'deleted': False} for i in range(30)]
                               + [{'account_id': 'wa', 'source_id': f'p{i}', 'kind': 'whatsapp.message', 'occurred_at': ago(5), 'title': 'x', 'body': 'y',
                                   'metadata': {'chat_id': '32470@s.whatsapp.net', 'chat_name': 'Ann'}, 'deleted': False} for i in range(30)])
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'm1', 'kind': 'gmail.message', 'occurred_at': ago(30), 'title': 'Contract',
                                             'body': 'I will send it.', 'metadata': {'from': 'sam@client.example'}, 'deleted': False}])
        with self.store.connect() as db:
            self.mail_event = db.execute("SELECT id FROM events WHERE source_id='m1'").fetchone()[0]

    def promise(self, direction, who, what, due=None, made=ago(30), check_at=None, card=None, status='open'):
        source = f'p{direction}{who}'
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': source, 'kind': 'gmail.message', 'occurred_at': made, 'title': 'Re',
                                             'body': 'Will do.', 'metadata': {'from': 'sam@client.example'}, 'deleted': False}])
        with self.store.connect() as db:
            event = db.execute('SELECT id FROM events WHERE source_id=?', (source,)).fetchone()[0]
            db.execute('INSERT INTO promises(direction,who,what,due,check_at,event_id,account_id,status,suggestion_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (direction, who, what, due, check_at or (due or '2026-10-30') + 'T07:30:00+00:00', event, 'work', status, card, made, made))

    def snoozed_card(self, title, times=2):
        sid = self.store.suggest('s-' + title, title, 'Body.', [self.mail_event])['id']
        for i in range(times):
            self.store.decide(sid, 'snooze', '', f'{sid}-snooze-{i}', 'telegram')
            if i < times - 1:
                self.store.decide(sid, 'accept', '', f'{sid}-back-{i}', 'telegram')  # Unsnooze, then snoozed again
        return sid

    def send(self):
        sent = []
        with patch.object(a, 'telegram_request', side_effect=lambda config, args: sent.append(args) or {'messageId': '555'}):
            self.assertEqual(self.app.recap_step(current=dt.datetime.fromisoformat(FRIDAY)), 'sent')
        self.assertEqual(len(sent), 1)
        return sent[0]

    def tap(self, item, choice, message_id='555', decision='telegram:1'):
        with patch.object(a, 'telegram_request', side_effect=lambda config, args: self.edits.append(args) or {'messageId': '555'}), \
                patch.object(a.threading, 'Thread', side_effect=lambda target, args, daemon: type('T', (), {'start': lambda s: target(*args)})()):
            return self.app.action('recap_decide', {'id': item, 'choice': choice, 'source': 'telegram', 'message_id': message_id, 'decision_id': decision})

    def items(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM recap_items ORDER BY n')]

    def test_what_needs_the_owner_comes_first_with_names_dates_and_promise_links(self):
        card = self.store.suggest('late', 'Chase Sam for the contract', 'Body.', [self.mail_event])['id']
        self.promise('theirs', 'Sam Lee', 'send the signed contract', due='2026-10-20', card=card, status='carded')
        self.promise('theirs', 'Kim', 'review the deck', made=ago(24 * 9), check_at=ago(24))  # undated, past its check: late too
        self.promise('mine', 'Alex', 'send the Q3 deck', due='2026-10-26')
        self.promise('theirs', 'Bo', 'call back', due='2026-10-27')  # not late: in the week ahead instead
        self.promise('theirs', 'Alex', 'send the Q3 deck', due='2026-10-26')  # the same plan from both sides shows once, as the owner's
        self.promise('theirs', 'Dee', 'sign the form', due='2026-10-23')  # due today: still on time, in neither
        self.promise('theirs', 'Eli', 'pay the invoice', due='2026-10-31')  # Saturday after next: past the week ahead
        text = a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY))
        lines = text.split('\n')
        self.assertEqual(lines[0], '**Your week with The Machine** · 16–23 Oct')
        self.assertEqual(lines[2], '**Needs you**')
        with self.store.connect() as db:  # a promise links to its own page, never to its card (the page links the card)
            page = {(r[0], r[1]): f'https://m.example/assistant/#promise/{r[2]}' for r in db.execute('SELECT who,direction,id FROM promises')}
        self.assertEqual(lines[3], f"• Waiting on Sam Lee: [send the signed contract]({page['Sam Lee', 'theirs']}), due Tue 20 Oct")
        self.assertNotIn('#chat=' + card, text)
        self.assertEqual(lines[4], f"• Waiting on Kim: [review the deck]({page['Kim', 'theirs']}), since Wed 14 Oct")
        self.assertEqual(lines[5], f"• Promised Alex: [send the Q3 deck]({page['Alex', 'mine']}), due Mon 26 Oct")
        self.assertIn(f"**Next week**\n• Tue 27 Oct: Bo said they'd [call back]({page['Bo', 'theirs']})", text)
        self.assertNotIn('Dee', text)
        self.assertNotIn('Eli', text)
        self.assertEqual(text.count('send the Q3 deck'), 1)
        self.assertFalse([l for l in lines if l.lstrip('•0123456789. ').startswith('You ')])  # event lines never open with "You"

    def test_week_ahead_has_meetings_with_people_and_their_prep(self):
        def meeting(source_id, title, at, **meta):
            return {'account_id': 'cal', 'source_id': source_id, 'kind': 'calendar.event', 'occurred_at': at, 'title': title, 'body': '', 'deleted': False,
                    'metadata': {'attendees': [{'email': 'owner@example.com', 'self': True, 'responseStatus': meta.pop('me', 'accepted')}, {'email': 'pat@acme.example'}], **meta}}
        self.store.commit_page('cal', {}, [meeting('b', 'Board meeting', '2026-10-28T08:00:00+00:00'),
                                           meeting('w', 'Weekly one-on-one', '2026-10-27T08:00:00+00:00', recurring_event_id='r1'),
                                           meeting('d', 'Declined lunch', '2026-10-27T11:00:00+00:00', me='declined'),
                                           meeting('n', 'Next month', '2026-11-20T08:00:00+00:00'),
                                           meeting('t', 'Team offsite', '2026-10-29T08:00:00+00:00', ical_uid='u1')])
        # A partner's calendar shared with the owner: there 'self' is the partner. Their own appointments aren't the owner's;
        # one the owner is invited to is, and shows once next to the owner's own copy.
        shared = lambda source_id, title, at, guests, **meta: {'account_id': 'cal', 'source_id': source_id, 'kind': 'calendar.event', 'occurred_at': at,
            'title': title, 'body': '', 'deleted': False, 'metadata': {'access': 'shared_read_only', 'credential_account': 'owner@example.com',
            'attendees': [{'email': 'partner@example.com', 'self': True, 'responseStatus': 'accepted'}, *guests], **meta}}
        self.store.commit_page('cal', {}, [shared('s1', 'Dentist with Robin', '2026-10-27T13:00:00+00:00', [{'email': 'dentist@example.com'}]),
                                           shared('s2', 'Team offsite', '2026-10-29T08:00:00+00:00', [{'email': 'owner@example.com', 'responseStatus': 'accepted'}, {'email': 'pat@acme.example'}], ical_uid='u1'),
                                           shared('s3', 'Parents evening', '2026-10-30T16:00:00+00:00', [{'email': 'owner@example.com', 'responseStatus': 'needsAction'}, {'email': 'school@example.com'}])])
        with self.store.connect() as db:
            board = db.execute("SELECT id FROM events WHERE source_id='b'").fetchone()[0]
        prep = self.store.suggest('prep', 'Prep notes for the board', 'Body.', [board])['id']
        text = a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY))
        self.assertIn(f'**Next week**\n• Wed 28 Oct 09:00 Board meeting, 1 person · [card](https://m.example/assistant/#chat={prep})', text)
        self.assertEqual(text.count('Team offsite'), 1)
        self.assertIn('• Fri 30 Oct 17:00 Parents evening, 2 people', text)  # the partner and the school, not the owner
        for routine in ('Weekly one-on-one', 'Declined lunch', 'Next month', 'Dentist with Robin'):
            self.assertNotIn(routine, text)

    def test_new_people_by_name_only(self):
        with self.store.connect() as db:
            for node, name, org in (('email:lena@aliru.example', 'Lena Zwick', 'org:aliru.example'), ('email:support@codehubsupport.example', 'CodeHub', 'org:codehubsupport.example'),
                                    ('email:dana@bounce.example', 'dana@bounce.example', 'org:bounce.example'), ('email:zeb@clickup.example', 'Zeb', 'org:clickup.example')):
                db.execute("INSERT INTO graph_nodes(id,kind,name,strength,last_at,signals,gap_days,quiet,trend,info) VALUES(?,'person',?,1,?,2,NULL,0,0,?)",
                           (node, name, ago(2), json.dumps({'org': org})))
            db.execute("INSERT OR REPLACE INTO graph_state(id,built_at,signals,took_ms) VALUES(1,?,10,1)", (FRIDAY,))
        text = a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY))
        line = next(l for l in text.split('\n') if l.startswith('• New people'))
        self.assertEqual(line, '• New people: Lena Zwick, Zeb, and 1 more known only by address')  # no address, no company desk as a person

    def test_worth_it_and_what_changed_in_plain_lines(self):
        for i, (choice, then) in enumerate((('accept', None), ('accept', 'other'), ('reject', None))):
            sid = self.store.suggest(f'q{i}', f'Card {i}', 'Body.', [self.mail_event])['id']
            self.store.decide(sid, choice, '', f'q{i}a', 'telegram')
            if then:
                self.store.decide(sid, then, 'Shorter please', f'q{i}b', 'telegram')
        with self.store.connect() as db:
            db.execute("INSERT INTO rule_changes(at,kind,text) VALUES(?,'draft_rule','Draft rule: keep it short')", (ago(3),))
        text = a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY) + dt.timedelta(minutes=1), {'input': 1e6, 'cacheRead': 9e6, 'output': 4e4, 'runs': 3, 'usd': 3.3})
        self.assertIn('**What changed**\n• 67% of cards worth it (2 of 3). Do it was right first time in 1 of 2.\n• Adjusted: 1 writing rule.\n', text)
        self.assertNotIn('got it right the first time', text)
        last = text.split('\n')[-1]
        self.assertTrue(last.startswith('_Behind the scenes: ') and '$3.30' in last, last)  # one short line, at the end
        self.assertEqual(text.count('Behind the scenes'), 1)

    def test_filter_and_keep_are_one_tap_and_only_change_the_owners_filters(self):
        self.edits = []
        sent = self.send()
        self.assertIn('**Busy without results**\n1. School parents (Phone WhatsApp): 30 this week, no cards\nNothing changes until you tap.', sent['message'])
        self.assertNotIn('Ann', sent['message'])  # a person's own chat is never offered as noise
        rows = sent['presentation']['blocks']
        item = self.items()[0]
        self.assertEqual([[b['label'] for b in r['buttons']] for r in rows], [['Filter 1', 'Keep 1']])
        self.assertEqual(rows[0]['buttons'][0]['action'], {'type': 'callback', 'value': f"assistant:recap:{item['id']}:filter"})
        self.assertLessEqual(len(('tgcb1:xxxxx:' + rows[0]['buttons'][0]['action']['value']).encode()), 64)  # Telegram's callback limit
        with self.assertRaisesRegex(ValueError, 'not the recap'):
            self.tap(item['id'], 'filter', message_id='999')
        self.assertEqual(json.loads(self.path.read_text())['accounts'][0].get('exclude'), None)  # refused: nothing changed

        self.assertEqual(self.tap(item['id'], 'filter')['status'], 'filtered')
        self.assertEqual(json.loads(self.path.read_text())['accounts'][0]['exclude'], [{'chats': ['123@g.us']}])
        self.assertEqual(self.store.exclusions['wa'], [{'chats': ['123@g.us']}])  # new messages from it are skipped from now on
        edit = self.edits.pop()
        self.assertEqual((edit['action'], edit['messageId']), ('edit', '555'))  # the recap itself shows it; nothing new is sent
        self.assertIn('1. School parents (Phone WhatsApp): 30 this week, no cards · **Filtered**', edit['message'])
        self.assertEqual([[b['label'] for b in r['buttons']] for r in edit['presentation']['blocks']], [['Undo 1']])
        self.assertEqual(self.tap(item['id'], 'keep', decision='telegram:2').get('reason'), 'Already done: filtered.')
        self.assertEqual(self.tap(item['id'], 'filter', decision='telegram:1').get('duplicate'), True)  # a replayed tap

        self.assertEqual(self.tap(item['id'], 'undo', decision='telegram:3')['status'], 'open')
        self.assertEqual(json.loads(self.path.read_text())['accounts'][0]['exclude'], [])
        self.assertEqual(self.tap(item['id'], 'keep', decision='telegram:4')['status'], 'kept')
        with self.store.connect() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT text FROM rule_changes WHERE kind='filter' ORDER BY id")],
                             ['Filter: School parents (Phone WhatsApp)', 'Removed filter: School parents (Phone WhatsApp)'])
            db.execute('DELETE FROM recaps')
        self.assertNotIn('School parents', a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY)))  # kept: not asked again

    def test_group_chats_of_friends_and_family_are_never_offered_as_noise(self):
        def chat(chat_id, name, n, senders, mine=0):
            return [{'account_id': 'wa', 'source_id': f'{chat_id}-{i}', 'kind': 'whatsapp.message', 'occurred_at': ago(5), 'title': 'x', 'body': 'y',
                     'metadata': {'chat_id': chat_id, 'chat_name': name, **({'from_me': True} if i < mine else {'sender_id': f'{chat_id}-s{i % senders}'})},
                     'deleted': False} for i in range(n)]
        self.store.commit_page('wa', {}, chat('9@g.us', 'Weekend crew', 40, 12, mine=3)  # the owner writes in it: their own chat
                               + chat('8@g.us', 'Cousins', 40, 5)  # a handful of people: a circle, even when the owner only reads
                               + chat('7@g.us', 'Street announcements', 25, 20))  # many people, the owner never writes: a feed
        text = a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY))
        busy = text.split('**Busy without results**\n')[1].split('\nNothing changes')[0]
        self.assertEqual(busy, '1. School parents (Phone WhatsApp): 30 this week, no cards\n2. Street announcements (Phone WhatsApp): 25 this week, no cards')

    def test_cards_snoozed_again_and_again_drop_do_it_now_or_keep(self):
        self.edits = []
        drop, now, keep = (self.snoozed_card(t, n) for t, n in (('Share the tow-bar cost', 4), ('Pay the painter', 3), ('Book the dentist', 2)))
        sent = self.send()
        self.assertIn(f'**Snoozed more than once**\n2. [Share the tow-bar cost](https://m.example/assistant/#chat={drop}), 4 times', sent['message'])
        items = {i['ref']: i for i in self.items() if i['kind'] == 'snoozed'}
        self.assertEqual([b['label'] for b in sent['presentation']['blocks'][1]['buttons']], ['Drop 2', 'Do it now 2', 'Keep 2'])
        with self.store.connect() as db:
            status = lambda sid: db.execute('SELECT status FROM suggestions WHERE id=?', (sid,)).fetchone()[0]
            self.assertEqual([status(x) for x in (drop, now, keep)], ['snoozed'] * 3)  # nothing changed by sending it
        self.assertEqual(self.tap(items[drop]['id'], 'drop')['status'], 'dropped')
        self.assertEqual(self.tap(items[now]['id'], 'now', decision='telegram:2')['status'], 'started')
        self.assertEqual(self.tap(items[keep]['id'], 'keep', decision='telegram:3')['status'], 'kept')
        with self.store.connect() as db:
            status = lambda sid: db.execute('SELECT status FROM suggestions WHERE id=?', (sid,)).fetchone()[0]
            self.assertEqual([status(x) for x in (drop, now, keep)], ['rejected', 'accepted', 'snoozed'])
            self.assertEqual(db.execute('SELECT count(*) FROM action_jobs').fetchone()[0], 0)  # Do it prepares; nothing is sent
        self.assertIn('· **Dropped**', self.edits[-1]['message'])
        with self.store.connect() as db:
            db.execute('DELETE FROM recaps')
        self.assertNotIn('Book the dentist', a.weekly_recap(self.store, self.config, dt.datetime.fromisoformat(FRIDAY)))  # kept until snoozed again

    def test_taps_come_only_from_telegram_on_that_message(self):
        self.edits = []
        self.send()
        item = self.items()[0]
        with self.assertRaisesRegex(ValueError, 'come from the recap message'):
            self.app.action('recap_decide', {'id': item['id'], 'choice': 'filter', 'source': 'assistant'})
        with self.assertRaisesRegex(ValueError, 'Unknown choice'):
            self.tap(item['id'], 'drop')
        self.assertIn('recap_decide', a.OWNER_ACTIONS)  # the plugin's owner key, never the API token alone

    def test_without_telegram_the_push_leads_with_what_needs_the_owner(self):
        self.promise('mine', 'Alex', 'send the Q3 deck', due='2026-10-26')
        self.app.config['telegram_owner'] = ''
        pushed = []
        with self.store.connect() as db:
            db.execute("INSERT INTO push_subscriptions(endpoint,p256dh,auth,created_at) VALUES('https://push.example/1','k','a',?)", (FRIDAY,))
        with patch.object(a, 'push_node', side_effect=lambda config, request: pushed.append(request) or {'results': []}), \
                patch.object(a, 'telegram_request', side_effect=AssertionError('no Telegram')):
            self.assertEqual(self.app.recap_step(current=dt.datetime.fromisoformat(FRIDAY)), 'sent')
        self.assertTrue(pushed[0]['payload']['body'].startswith('Needs you • Promised Alex: send the Q3 deck, due Mon 26 Oct'), pushed[0]['payload']['body'])
        self.assertEqual(self.items(), [])  # no buttons to tap without Telegram


if __name__ == '__main__':
    unittest.main()
