"""A card handed to a teammate waits under Later and comes back to check in if they haven't answered. When follows the
card's own urgency and deadline (its `urgent` and `due` fields), and the card, Telegram and the chat say it is a
check-in, not a snooze. Nothing is sent anywhere."""
import datetime as dt
import unittest
from unittest.mock import patch

import test_fleet_requests as tr
from test_fleet import a

BXL = a.ZoneInfo('Europe/Amsterdam')


def at(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=BXL).astimezone(dt.timezone.utc).isoformat()


class CheckInTime(unittest.TestCase):
    def checkin(self, current, **card):
        with patch.object(a, 'now', return_value=current):
            return a.handoff_checkin(**card)

    def test_due_today_checks_in_the_same_day(self):
        self.assertEqual(self.checkin(at(2026, 10, 23, 10), due='2026-10-23'), at(2026, 10, 23, 13))  # Friday, 3 hours later
        self.assertEqual(self.checkin(at(2026, 10, 23, 10), due='2026-10-20'), at(2026, 10, 23, 13))  # overdue too
        # Late in the evening: the next morning, not past midnight.
        self.assertEqual(self.checkin(at(2026, 10, 22, 21, 30), due='2026-10-22'), at(2026, 10, 23, 9))

    def test_urgency_alone_waits_until_its_day_has_passed(self):
        # Urgent, no date: the next work morning, never a few hours later (that pulled it back to the top too soon).
        self.assertEqual(self.checkin(at(2026, 10, 22, 10), urgent=True), at(2026, 10, 23, 9))  # Thursday: Friday
        self.assertEqual(self.checkin(at(2026, 10, 23, 10), urgent=True), at(2026, 10, 26, 9))  # Friday: Monday
        self.assertEqual(self.checkin(at(2026, 10, 24, 14), urgent=True), at(2026, 10, 26, 9))  # Saturday: Monday
        # A real deadline still wins: urgent and due today is 3 hours later.
        self.assertEqual(self.checkin(at(2026, 10, 22, 10), urgent=True, due='2026-10-22'), at(2026, 10, 22, 13))

    def test_from_when_it_was_handed_off(self):
        self.assertEqual(a.handoff_checkin(True, None, start=at(2026, 10, 22, 10)), at(2026, 10, 23, 9))
        self.assertEqual(a.handoff_checkin(False, None, start=at(2026, 10, 20, 10)), at(2026, 10, 23, 9))

    def test_a_near_deadline_checks_in_a_work_day_before_it(self):
        self.assertEqual(self.checkin(at(2026, 10, 26, 10), due='2026-10-28'), at(2026, 10, 27, 9))  # due Wed: Tue morning
        # Due Monday, handed off Thursday: Friday morning, not the weekend and not after it.
        self.assertEqual(self.checkin(at(2026, 10, 22, 10), due='2026-10-26'), at(2026, 10, 23, 9))
        # Due tomorrow and that morning has passed: still today.
        self.assertEqual(self.checkin(at(2026, 10, 22, 10), due='2026-10-23'), at(2026, 10, 22, 13))

    def test_otherwise_three_work_days(self):
        self.assertEqual(self.checkin(at(2026, 10, 20, 10)), at(2026, 10, 23, 9))  # Tuesday: Friday
        self.assertEqual(self.checkin(at(2026, 10, 23, 10)), at(2026, 10, 26, 9))  # Friday: Monday
        self.assertEqual(self.checkin(at(2026, 10, 22, 18, 40), due='2026-11-05'), at(2026, 10, 26, 9))  # far deadline
        self.assertEqual(self.checkin(at(2026, 10, 23, 10), due='not a date'), at(2026, 10, 26, 9))


class CheckInCard(unittest.TestCase):
    setUp, handed_card, card_row = tr.Requests.setUp, tr.Requests.handed_card, tr.Requests.card_row
    def handed(self, urgent=False, due='2026-11-03', key=None):
        card = self.handed_card(key=key) if key else self.handed_card()
        with self.store.connect() as db:
            db.execute('UPDATE suggestions SET urgent=?,due=? WHERE id=?', (int(urgent), due, card))
        self.fleet.wait_card = lambda card_id, why, key, expected: self.store.wait_handoff(card_id, why, key, expected)
        self.fleet.hand_off(card, self.ref, 'pat1', 'Send Sam the pricing', 'Sam wants pricing.')
        return card

    def shown(self, card):
        row = self.card_row(card)
        with self.store.connect() as db:
            row.update(self.store.suggestion_result(row, db))
        return row

    def test_an_urgent_handoff_stays_with_them_until_the_next_work_morning(self):
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 10)):
            card = self.handed(urgent=True, due=None)
            self.assertEqual(self.card_row(card)['snoozed_until'], at(2026, 10, 23, 9))
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 13, 30)):  # the old 3-hour check-in: still under Later
            self.store.wake_snoozed()
            self.assertEqual(self.card_row(card)['status'], 'snoozed')
        with patch.object(a, 'now', return_value=at(2026, 10, 23, 9, 1)):  # its day has passed: back on top
            self.store.wake_snoozed()
            self.assertEqual(self.card_row(card)['status'], 'pending')

    def woke_early(self, key=None):
        """An urgent card handed off Thursday 10:00 that the old rule brought back at 13:00, still with no answer."""
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 10)):
            card = self.handed(urgent=True, due=None, key=key)
        with self.store.connect() as db:
            db.execute("UPDATE suggestions SET status='pending',snoozed_until=NULL,resume_status=NULL WHERE id=?", (card,))
        return card

    def test_a_card_the_old_rule_brought_back_goes_back_to_later(self):
        card = self.woke_early()
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 15)):
            self.store.wake_snoozed()
            row = self.card_row(card)
            self.assertEqual((row['status'], row['snoozed_until']), ('snoozed', at(2026, 10, 23, 9)))
        with patch.object(a, 'now', return_value=at(2026, 10, 23, 9, 1)):
            self.store.wake_snoozed()
            self.assertEqual(self.card_row(card)['status'], 'pending')  # its check-in came: it stays on top
            self.store.wake_snoozed()
            self.assertEqual(self.card_row(card)['status'], 'pending')

    def test_an_answer_or_the_owners_own_choice_keeps_it_on_top(self):
        card = self.woke_early()
        with self.store.connect() as db:
            db.execute("UPDATE fleet_out SET status='answered',answer='{\"stage\":\"declined\"}' WHERE kind='handoff'")
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 15)):
            self.store.wake_snoozed()
            self.assertEqual(self.card_row(card)['status'], 'pending')
        other = self.woke_early(key='k-other')
        self.assertNotEqual(other, card)
        self.store.decide(other, 'other', 'Ask Pat about it', 'dashboard:c1', 'dashboard')
        with patch.object(a, 'now', return_value=at(2026, 10, 22, 15)):
            self.store.wake_snoozed()
            self.assertNotEqual(self.card_row(other)['status'], 'snoozed')

    def test_the_card_says_it_is_a_check_in_not_a_snooze(self):
        with patch.object(a, 'now', return_value=at(2026, 10, 23, 10)):  # Friday; due 3 Nov is far: Monday
            card = self.handed()
            row = self.shown(card)
            self.assertEqual(row['status'], 'snoozed')
            self.assertEqual(row['snoozed_until'], at(2026, 10, 26, 9))
            self.assertEqual(row['handoff_wait'], {'name': 'Pat'})  # first name only
            self.assertEqual(a.checkin_line(row), 'Check in with Pat Mon 09:00 if no answer')
            self.assertIn('Check in with Pat Mon 09:00 if no answer.', a.card(row))
            self.assertNotIn('Snoozed', a.card(row))
            labels = [b['label'] for block in a.controls(row)['blocks'] for b in block.get('buttons', [])]
            self.assertEqual(labels[:3], ['Check in now', 'Change time', 'Not needed'])
        with patch.object(a, 'now', return_value=at(2026, 10, 26, 7)):
            self.assertEqual(a.checkin_line(row), 'Check in with Pat today 09:00 if no answer')

    def test_a_plain_snooze_and_an_answered_handoff_keep_their_own_words(self):
        with patch.object(a, 'now', return_value=at(2026, 10, 23, 10)):
            card = self.handed()
            with self.store.connect() as db:
                ask_id = db.execute("SELECT id FROM fleet_out WHERE kind='handoff'").fetchone()[0]
                db.execute("UPDATE fleet_out SET status='answered',answer='{\"stage\":\"declined\"}' WHERE id=?", (ask_id,))
            self.assertNotIn('handoff_wait', self.shown(card))
            other = self.handed_card(key='k-plain', title='Plain card')
            self.assertNotEqual(other, card)
            self.store.decide(other, 'snooze', '', 'dashboard:s1', 'dashboard', snooze_option='3d')
            row = self.shown(other)
            self.assertNotIn('handoff_wait', row)
            labels = [b['label'] for block in a.controls(row)['blocks'] for b in block.get('buttons', [])]
            self.assertEqual(labels[0], 'Unsnooze')


class CheckInChat(unittest.TestCase):
    def setUp(self):
        import test_card_chat
        test_card_chat.CardChat.setUp(self)

    def test_the_chat_says_check_in_not_unsnoozed(self):
        rows = (('fleet-handoff-wait-q_1', 'snooze', 'fleet', '2026-01-01T10:00:00+00:00'),
                ('d1', 'snooze', 'dashboard', '2026-01-01T11:00:00+00:00'),
                ('d2', 'accept', 'telegram', '2026-01-02T09:00:00+00:00'),
                ('d3', 'snooze', 'dashboard', '2026-01-02T10:00:00+00:00'),
                ('d4', 'accept', 'dashboard', '2026-01-02T11:00:00+00:00'))
        with self.store.connect() as db:
            for id_, choice, source, at_ in rows:
                db.execute("INSERT INTO decisions(id,suggestion_id,choice,feedback,source,created_at) VALUES(?,?,?,'',?,?)", (id_, self.sid, choice, source, at_))
            owner, _ = self.app.card_chat_service(db, self.sid)
        # With the teammate: Change time moves the check-in, Check in now brings it back. After that, a snooze is a snooze.
        self.assertEqual([i['text'] for i in owner if i['kind'] == 'event'],
                         ['Moved the check-in', 'Brought it back for a check-in in Telegram', 'Snoozed', 'Unsnoozed'])


if __name__ == '__main__':
    unittest.main()
