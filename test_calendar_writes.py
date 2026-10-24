"""Owner-approved calendar changes (calendar_writes.py): only on the owner's own calendars and shared ones marked
writable, never with guests, and only after the owner saw the change and tapped it (a card's Add, or the Calendar
form). Synthetic data only: no Google call is made and no real event is created."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import test_preparation as p

a = p.a
cw = a.calendar_writes

OWN = {'id': 'cal', 'email': 'owner@example.com', 'service': 'calendar', 'transport': 'gog', 'client': 'c', 'boundary': 'work:Example'}
KID = {'id': 'kid-cal', 'email': 'owner@example.com', 'service': 'calendar', 'transport': 'gog', 'client': 'c', 'boundary': 'personal:Kid',
       'calendar_id': 'kid.example@example.net', 'display_name': 'Kid’s calendar', 'writable': True}
PARTNER = {'id': 'partner-cal', 'email': 'owner@example.com', 'service': 'calendar', 'transport': 'gog', 'client': 'c',
           'boundary': 'personal:Partner', 'calendar_id': 'partner.example@example.net', 'display_name': 'Partner’s calendar'}
CONFIG = {'accounts': [OWN, KID, PARTNER], 'gog': '/bin/false', 'credentials': {}}
NEW = {'kind': 'calendar', 'op': 'create', 'calendar': 'kid-cal', 'title': 'Daycare pick-up',
       'start': '2026-10-27T16:00:00+01:00', 'end': '2026-10-27T16:30:00+01:00', 'location': 'Daycare'}


def raw(source_id, title, start, end, **extra):
    return {'id': source_id, 'summary': title, 'start': start, 'end': end, 'status': 'confirmed',
            'htmlLink': 'https://calendar.google.com/calendar/event?eid=' + source_id, **extra}


class FakeGoogle:
    """Stands in for gog: answers reads, records each write command, and never reaches Google."""

    def __init__(self, role='writer', scopes=('https://www.googleapis.com/auth/calendar',), events=None):
        self.role, self.scopes, self.events, self.writes = role, set(scopes), dict(events or {}), []
        self.answer = None

    def read(self, config, account, api, version, method, params, scope):
        assert scope == cw.READ_SCOPE
        if method == 'calendar.calendarList.get':
            return {'id': params['calendarId'], 'accessRole': self.role}
        if method == 'calendar.events.get':
            if params['eventId'] not in self.events:
                raise a.SourceError('calendar', 404)
            return self.events[params['eventId']]
        raise AssertionError(method)

    def run(self, command, **kwargs):
        method, params = command[5], json.loads(command[command.index('--params') + 1])
        payload = json.loads(Path(command[command.index('--body') + 1][1:]).read_text()) if '--body' in command else None
        self.writes.append((method, params, payload, command))
        if self.answer:
            return self.answer
        if method == 'calendar.events.insert':
            self.events[payload['id']] = {**payload, 'status': 'confirmed'}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload or {}), stderr='')

    def transport(self):
        t = cw.CalendarTransport(CONFIG, lambda *x: 'pw', self.read, run=self.run)
        return t


def granted(google):
    return patch.object(a.next_actions, 'granted_scopes', lambda config, credential, account: google.scopes)


class WhichCalendars(unittest.TestCase):
    def test_own_and_marked_shared_calendars_only(self):
        self.assertEqual([c['id'] for c in cw.calendars(CONFIG)], ['cal', 'kid-cal'])
        self.assertEqual(cw.calendars(CONFIG)[1]['name'], 'Kid’s calendar')
        for account in ({**OWN, 'shared': {'fleet_name': 'Team'}}, {**OWN, 'service': 'outlook_calendar'}, {**OWN, 'transport': 'imap'},
                        {**KID, 'writable': 'yes'}, PARTNER):
            self.assertFalse(cw.writable(account), account)

    def test_offered_only_on_a_sign_in_that_may_write(self):
        cw.GRANTS.clear();self.addCleanup(cw.GRANTS.clear)
        asks, t = [], [0]
        def scopes(config, credential, account):
            asks.append(account['id'])
            if account['id'] == 'cal':
                return {'https://www.googleapis.com/auth/calendar.readonly'}  # what a default sign-in grants
            return {'https://www.googleapis.com/auth/calendar'}
        allowed = lambda account: cw.may_write(CONFIG, None, account, scopes, clock=lambda: t[0])
        self.assertEqual([c['id'] for c in cw.calendars(CONFIG, allowed)], ['kid-cal'])
        cw.calendars(CONFIG, allowed);t[0] = 599;cw.calendars(CONFIG, allowed)
        self.assertEqual(asks, ['cal', 'kid-cal'], 'asked once per sign-in for ten minutes')
        t[0] = 601;cw.calendars(CONFIG, allowed);self.assertEqual(len(asks), 4)
        def down(*x):
            raise ValueError('no keyring')
        cw.GRANTS.clear()
        self.assertEqual(cw.calendars(CONFIG, lambda account: cw.may_write(CONFIG, None, account, down)), [])
        with patch.object(a.next_actions, 'granted_scopes', scopes):
            cw.GRANTS.clear()
            self.assertEqual([c['id'] for c in a.writable_calendars(CONFIG)], ['kid-cal'])

    def test_a_read_only_shared_calendar_is_refused_before_anything(self):
        with self.assertRaisesRegex(ValueError, 'can’t be changed'):
            cw.validate({**NEW, 'calendar': 'partner-cal'}, CONFIG)
        with self.assertRaisesRegex(ValueError, 'can’t be changed'):
            cw.validate({**NEW, 'calendar': 'nope'}, CONFIG)


class Validate(unittest.TestCase):
    def test_a_new_event_shows_what_where_and_when(self):
        action = cw.validate(NEW, CONFIG)
        self.assertEqual((action['account_id'], action['calendar_name'], action['when']), ('kid-cal', 'Kid’s calendar', 'Tue 27 Oct 2026, 16:00 to 16:30'))
        self.assertEqual(cw.preview(action), 'Add to Kid’s calendar\nDaycare pick-up\nTue 27 Oct 2026, 16:00 to 16:30\nWhere: Daycare')
        self.assertEqual(cw.label(action), 'Add to calendar')
        self.assertEqual(cw.done(action), 'Added to Kid’s calendar: Daycare pick-up')

    def test_never_guests_and_sane_times(self):
        for bad, why in (({'attendees': ['a@b.example']}, 'never invites'), ({'start': '2026-10-27T16:00:00'}, 'offset'),
                         ({'end': '2026-10-27T15:00:00+01:00'}, 'ends after'), ({'title': ' '}, 'needs a title'),
                         ({'op': 'move'}, 'op is'), ({'all_day': 'yes'}, 'true or false')):
            with self.assertRaisesRegex(ValueError, why):
                cw.validate({**NEW, **bad}, CONFIG)

    def test_an_all_day_event_takes_its_last_day(self):
        action = cw.validate({**NEW, 'all_day': True, 'start': '2026-10-27', 'end': '2026-10-28'}, CONFIG)
        self.assertEqual(action['when'], 'Tue 27 Oct 2026 to Wed 28 Oct 2026')
        body = cw.body(action, 'tap')
        self.assertEqual((body['start'], body['end']), ({'date': '2026-10-27'}, {'date': '2026-10-29'}))  # Google's end is the day after
        self.assertEqual(cw.validate({**NEW, 'all_day': True, 'start': '2026-10-27', 'end': None}, CONFIG)['when'], 'Tue 27 Oct 2026, all day')

    def test_a_new_event_has_a_stable_id_and_no_guests(self):
        body = cw.body(cw.validate(NEW, CONFIG), 'job-1')
        self.assertEqual(body['id'], cw.event_id('job-1'))
        self.assertRegex(body['id'], r'^[a-v0-9]{5,1024}$')
        self.assertNotIn('attendees', body)
        self.assertNotEqual(cw.event_id('job-1'), cw.event_id('job-2'))

    def test_an_update_sends_only_what_changes(self):
        events = {7: {'account_id': 'kid-cal', 'source_id': 'g7', 'title': 'Swimming', 'start': '2026-10-28T10:00:00+01:00',
                      'end': '2026-10-28T11:00:00+01:00', 'all_day': False, 'guests': 0}}
        action = cw.validate({'kind': 'calendar', 'op': 'update', 'event': 7, 'title': 'Swimming lesson'}, CONFIG, events)
        self.assertEqual(cw.body(action), {'summary': 'Swimming lesson'})
        self.assertEqual(action['when'], 'Wed 28 Oct 2026, 10:00 to 11:00')
        moved = cw.validate({'kind': 'calendar', 'op': 'update', 'event': 7, 'start': '2026-10-28T11:00:00+01:00', 'end': '2026-10-28T12:00:00+01:00'}, CONFIG, events)
        self.assertEqual(set(cw.body(moved)), {'start', 'end'})
        self.assertIn('Was: Swimming, Wed 28 Oct 2026, 10:00 to 11:00', cw.preview(moved))
        gone = cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 7}, CONFIG, events)
        self.assertEqual((gone['title'], cw.label(gone)), ('Swimming', 'Delete event'))
        with self.assertRaisesRegex(ValueError, 'only names the event'):
            cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 7, 'title': 'x'}, CONFIG, events)

    def test_never_an_event_with_guests_or_on_a_read_only_calendar(self):
        events = {8: {'account_id': 'cal', 'source_id': 'g8', 'title': 'Team call', 'start': '2026-10-28T10:00:00+01:00',
                      'end': '2026-10-28T11:00:00+01:00', 'all_day': False, 'guests': 3},
                  9: {'account_id': 'partner-cal', 'source_id': 'g9', 'title': 'Theirs', 'start': '2026-10-28T10:00:00+01:00',
                      'end': '2026-10-28T11:00:00+01:00', 'all_day': False, 'guests': 0}}
        with self.assertRaisesRegex(ValueError, 'has guests'):
            cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 8}, CONFIG, events)
        with self.assertRaisesRegex(ValueError, 'can’t be changed'):
            cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 9}, CONFIG, events)
        with self.assertRaisesRegex(ValueError, 'event is the id'):
            cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 99}, CONFIG, events)


class Transport(unittest.TestCase):
    def test_checks_permission_and_the_share_before_writing(self):
        action = cw.validate(NEW, CONFIG)
        read_only = FakeGoogle(scopes=('https://www.googleapis.com/auth/calendar.readonly',))
        with granted(read_only), self.assertRaisesRegex(ValueError, 'can only read calendars'):
            read_only.transport().preflight(action)
        viewer = FakeGoogle(role='reader')
        with granted(viewer), self.assertRaisesRegex(ValueError, 'Make changes to events'):
            viewer.transport().preflight(action)
        self.assertEqual(read_only.writes + viewer.writes, [])

    def test_one_insert_on_the_pinned_calendar_with_no_updates_to_anyone(self):
        google = FakeGoogle()
        action = cw.validate(NEW, CONFIG)
        with granted(google):
            google.transport().preflight(action)
            result = google.transport().send(action, {'id': 'job-1'})
        self.assertEqual(result, {'message_id': 'calendar:' + cw.event_id('job-1')})
        [(method, params, payload, command)] = google.writes
        self.assertEqual((method, params), ('calendar.events.insert', {'calendarId': 'kid.example@example.net', 'sendUpdates': 'none'}))
        self.assertIn('--allow-write', command)
        self.assertEqual(payload['summary'], 'Daycare pick-up')
        # A retried tap is the same event: Google says it exists, and that counts as done
        google.answer = types.SimpleNamespace(returncode=1, stdout=json.dumps({'error': {'message': '{"code": 409}'}}), stderr='')
        self.assertEqual(google.transport().send(action, {'id': 'job-1'}), result)

    def test_refusals_and_unknown_outcomes(self):
        google = FakeGoogle()
        action = cw.validate(NEW, CONFIG)
        google.answer = types.SimpleNamespace(returncode=1, stdout=json.dumps({'error': {'message': '{"code": 403}'}}), stderr='')
        self.assertEqual(google.transport().send(action, {'id': 'j'}), {'rejected': 403})
        google.answer = types.SimpleNamespace(returncode=1, stdout='', stderr='timeout')
        self.assertEqual(google.transport().send(action, {'id': 'j'}), {})
        self.assertEqual(google.transport().reconcile(action, {'id': 'j'}), {})  # not there: still unknown, never written again
        google.events[cw.event_id('j')] = {'status': 'confirmed'}
        self.assertEqual(google.transport().reconcile(action, {'id': 'j'}), {'message_id': 'calendar:' + cw.event_id('j')})

    def test_a_delete_of_an_event_that_is_already_gone_is_done(self):
        events = {7: {'account_id': 'kid-cal', 'source_id': 'g7', 'title': 'Swimming', 'start': '2026-10-28', 'end': '2026-10-28', 'all_day': True, 'guests': 0}}
        action = cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 7}, CONFIG, events)
        google = FakeGoogle()
        with granted(google), self.assertRaisesRegex(ValueError, 'no longer on the calendar'):
            google.transport().preflight(action)
        self.assertEqual(google.transport().reconcile(action, {'id': 'j'}), {'message_id': 'calendar:g7'})
        google.events['g7'] = {'status': 'confirmed', 'attendees': [{'email': 'x@example.net'}]}
        with granted(google), self.assertRaisesRegex(ValueError, 'has guests now'):
            google.transport().preflight(action)


class Store(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.commit_page('kid-cal', {}, [
            a.calendar_event(KID, raw('swim', 'Swimming', {'dateTime': '2026-10-28T10:00:00+01:00'}, {'dateTime': '2026-10-28T11:00:00+01:00'},
                                      organizer={'email': 'kid.example@example.net', 'self': True}), 'Europe/Brussels'),
            a.calendar_event(KID, raw('party', 'Party', {'date': '2026-10-29'}, {'date': '2026-10-30'},
                                      attendees=[{'email': 'other@example.org'}]), 'Europe/Brussels')])
        self.store.commit_page('partner-cal', {}, [a.calendar_event(PARTNER, raw('away', 'Away', {'dateTime': '2026-10-28T09:00:00+01:00'},
                                                                                 {'dateTime': '2026-10-28T12:00:00+01:00'}), 'Europe/Brussels')])
        self.window = ('2026-10-26T00:00:00Z', '2026-11-01T00:00:00Z')

    def test_only_meetings_without_guests_on_a_writable_calendar_can_be_changed(self):
        rows = {r['title']: r for r in self.store.calendar(['kid-cal', 'partner-cal'], *self.window, {'owner@example.com'}, {'kid-cal', 'cal'})}
        self.assertEqual(rows['Swimming']['edit'], {'start': '2026-10-28T10:00:00+01:00', 'end': '2026-10-28T11:00:00+01:00', 'all_day': False})
        self.assertNotIn('edit', rows['Party'])
        self.assertNotIn('edit', rows['Away'])
        with self.store.connect() as db:
            ids = {r['source_id']: r['id'] for r in db.execute('SELECT id,source_id FROM events')}
            found = a.calendar_write_events(db, [ids['swim'], ids['party'], 'x'], {'owner@example.com'})
        self.assertEqual(found[ids['swim']]['guests'], 0)
        self.assertEqual((found[ids['party']]['start'], found[ids['party']]['end'], found[ids['party']]['guests']), ('2026-10-29', '2026-10-29', 1))


    def test_marked_with_the_name_its_config_gives(self):
        self.assertEqual(a.calendar_account_owner(KID), 'Kid')
        self.assertEqual(a.calendar_account_owner({'display_name': "Sam's calendar"}), 'Sam')
        self.assertEqual(a.calendar_account_owner({'display_name': 'Family', 'calendar_owner': 'Robin'}), 'Robin')
        self.assertIsNone(a.calendar_account_owner({'display_name': 'Family'}))
        rows = self.store.calendar(['kid-cal', 'partner-cal'], *self.window, {'owner@example.com'}, (), {'kid-cal': 'Kid'})
        self.assertEqual({r['title']: r['shared'] for r in rows}, {'Swimming': 'Kid', 'Party': 'Kid', 'Away': 'Partner'})


class CalendarForm(unittest.TestCase):
    """The Calendar's New event and edit form: the owner's tap in the dashboard, run once."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.app = object.__new__(a.App);self.app.store, self.app.config = self.store, CONFIG
        self.google = FakeGoogle()
        self.synced = []
        self.app.sync_soon = self.synced.append

    def write(self, args, owner=True):
        with granted(self.google):
            return self.app.action('calendar_write', args, owner_dashboard=owner) if owner else self.app.action('calendar_write', args)

    def test_only_from_the_owners_dashboard(self):
        with self.assertRaisesRegex(ValueError, 'your tap in the dashboard'):
            self.write({'request_id': 'a' * 32, **NEW}, owner=False)
        self.assertEqual(self.google.writes, [])

    def test_one_tap_one_event(self):
        t = self.google.transport()
        with patch.object(cw, 'CalendarTransport', lambda *x: t):
            first = self.write({'request_id': 'b' * 32, **NEW, 'source': 'dashboard'})
            again = self.write({'request_id': 'b' * 32, **NEW})
        self.assertEqual((first['status'], first['calendar'], again), ('done', 'Kid’s calendar', {'status': 'done', 'duplicate': True}))
        self.assertEqual(len(self.google.writes), 1)
        self.assertEqual(self.synced, ['kid-cal'])

    def test_a_view_only_calendar_is_never_written(self):
        t = self.google.transport()
        with patch.object(cw, 'CalendarTransport', lambda *x: t), self.assertRaises(ValueError):
            self.write({'request_id': 'c' * 32, **NEW, 'calendar': 'partner-cal'})
        self.assertEqual(self.google.writes, [])


class CardChange(unittest.TestCase):
    """A card offers the change; nothing happens until the owner confirms it, then exactly that change runs once."""
    setUp = p.Preparation.setUp
    job = p.Preparation.job
    accept = p.Preparation.accept

    def test_offered_then_made_on_the_owners_confirm(self):
        self.app.config = CONFIG
        self.accept();self.app.preparation_step(lambda r: {'status': 'admitted'})
        self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': self.job()['job_id'], 'body': 'Ready to add it.',
                                    'next_actions': [{**NEW, 'label': 'Add to calendar'}], 'outcome': 'needs_you', 'needs': 'Add it'})
        with self.store.connect() as db:
            db.execute("UPDATE preparation_runs SET status='ok',finished_at=?", (a.now(),))
            action = a.next_actions.actions(db.execute('SELECT next_action FROM suggestions').fetchone()[0])[0]
        self.assertEqual((action['kind'], action['account_id'], a.next_actions.label(action)), ('calendar', 'kid-cal', 'Add to calendar'))
        google = FakeGoogle()
        transport = a.next_actions.ActionTransport(None, None, calendar=google.transport())
        with granted(google):
            self.assertIsNone(self.app.action_step(transport))  # nothing approved yet: nothing runs
            self.assertEqual(google.writes, [])
            self.app.action('confirm_action', {'suggestion_id': self.sid, 'revision': action['revision'], 'approval_id': 'tap'}, owner_dashboard=True)
            self.assertEqual(self.app.action_step(transport), 'sent')
            self.assertIsNone(self.app.action_step(transport))
        self.assertEqual(len(google.writes), 1)
        with self.store.connect() as db:
            status, note = db.execute("SELECT s.status,d.feedback FROM suggestions s JOIN decisions d ON d.suggestion_id=s.id AND d.choice='complete'").fetchone()
        self.assertEqual((status, note), ('completed', 'Added to Kid’s calendar: Daycare pick-up'))

    def test_the_prompt_names_the_calendars_and_still_fits(self):
        cw.GRANTS.clear();self.addCleanup(cw.GRANTS.clear)
        self.enterContext(granted(FakeGoogle()))
        self.accept()
        job = dict(self.job(), next_step='x' * 2000)
        limit = int(__import__('re').search(r'message\.length>(\d+)', (a.ROOT / 'preparation-run.mjs').read_text())[1])
        prompt = a.preparation_prompt(job, '- ' + 'f' * 3000 + '\n…', config=CONFIG)
        self.assertIn('Calendars you can change: cal (owner@example.com), kid-cal (Kid’s calendar)', prompt)
        self.assertNotIn('partner-cal', prompt)
        self.assertLess(len(prompt), limit - 1000)
        self.assertNotIn('Calendars you can change', a.preparation_prompt(job, '', config={'accounts': [PARTNER]}))


class Invites(unittest.TestCase):
    """Guests only from the Calendar form, only on the owner's own calendar, listed in what they approve. Google sends
    the invitations on that tap (sendUpdates all); every other write tells nobody. FakeGoogle: no invite is sent."""
    OWN_NEW = {**NEW, 'calendar': 'cal', 'guests': ['Sam@Example.org', 'sam@example.org', 'kim@example.net', 'owner@example.com']}

    def test_a_card_never_invites(self):
        with self.assertRaisesRegex(ValueError, 'never invites'):
            cw.validate(self.OWN_NEW, CONFIG)

    def test_the_form_invites_from_the_owners_own_calendar_only(self):
        action = cw.validate(self.OWN_NEW, CONFIG, invite=True)
        self.assertEqual(action['guests'], ['sam@example.org', 'kim@example.net'])  # each once, never the owner
        self.assertEqual(cw.label(action), 'Add and invite 2')
        self.assertTrue(cw.preview(action).endswith('Invites: sam@example.org, kim@example.net'))
        self.assertEqual(cw.done(action), 'Added to owner@example.com: Daycare pick-up, invited 2 people')
        with self.assertRaisesRegex(ValueError, 'your own calendar only'):
            cw.validate({**self.OWN_NEW, 'calendar': 'kid-cal'}, CONFIG, invite=True)
        for bad in (['not-an-address'], 'sam@example.org', ['a@b.example'] * 51, [7]):
            with self.assertRaises(ValueError):
                cw.validate({**self.OWN_NEW, 'guests': bad}, CONFIG, invite=True)
        self.assertNotIn('guests', cw.validate({**self.OWN_NEW, 'guests': []}, CONFIG, invite=True))

    def test_google_sends_the_invites_only_with_guests(self):
        google = FakeGoogle()
        action = cw.validate(self.OWN_NEW, CONFIG, invite=True)
        with granted(google):
            google.transport().preflight(action)
            self.assertEqual(google.transport().send(action, {'id': 'j'}), {'message_id': 'calendar:' + cw.event_id('j')})
        [(method, params, payload, _)] = google.writes
        self.assertEqual(params['sendUpdates'], 'all')
        self.assertEqual(payload['attendees'], [{'email': 'sam@example.org'}, {'email': 'kim@example.net'}])
        self.assertEqual(google.transport().reconcile(action, {'id': 'j'}), {'message_id': 'calendar:' + cw.event_id('j')})
        plain = cw.validate({**NEW, 'calendar': 'cal'}, CONFIG, invite=True)
        with granted(google):
            google.transport().send(plain, {'id': 'k'})
        self.assertEqual(google.writes[-1][1]['sendUpdates'], 'none')
        self.assertNotIn('attendees', google.writes[-1][2])

    def test_inviting_people_to_an_event_without_guests(self):
        events = {7: {'account_id': 'cal', 'source_id': 'g7', 'title': 'Lunch', 'start': '2026-10-28T12:00:00+01:00',
                      'end': '2026-10-28T13:00:00+01:00', 'all_day': False, 'guests': 0}}
        action = cw.validate({'kind': 'calendar', 'op': 'update', 'event': 7, 'guests': ['sam@example.org']}, CONFIG, events, invite=True)
        self.assertEqual((cw.body(action), cw.label(action)), ({'attendees': [{'email': 'sam@example.org'}]}, 'Save and invite 1'))
        google = FakeGoogle(events={'g7': {'status': 'confirmed'}})
        with granted(google):
            google.transport().preflight(action)
            google.transport().send(action, {'id': 'j'})
        self.assertEqual((google.writes[0][0], google.writes[0][1]['sendUpdates']), ('calendar.events.patch', 'all'))
        with self.assertRaisesRegex(ValueError, 'only names the event'):
            cw.validate({'kind': 'calendar', 'op': 'delete', 'event': 7, 'guests': ['sam@example.org']}, CONFIG, events, invite=True)
        events[7]['account_id'] = 'kid-cal'
        with self.assertRaisesRegex(ValueError, 'your own calendar only'):
            cw.validate({'kind': 'calendar', 'op': 'update', 'event': 7, 'guests': ['sam@example.org']}, CONFIG, events, invite=True)

    def test_the_form_sends_only_the_count_the_button_showed(self):
        form = CalendarForm('test_one_tap_one_event');form.setUp();self.addCleanup(form.doCleanups)
        t = form.google.transport()
        guests = ['sam@example.org', 'kim@example.net']
        with patch.object(cw, 'CalendarTransport', lambda *x: t):
            with self.assertRaisesRegex(ValueError, 'Nothing was sent'):
                form.write({'request_id': 'd' * 32, **NEW, 'calendar': 'cal', 'guests': guests})
            with self.assertRaisesRegex(ValueError, 'Nothing was sent'):
                form.write({'request_id': 'd' * 32, **NEW, 'calendar': 'cal', 'guests': guests, 'invite': 1})
            self.assertEqual(form.google.writes, [])
            done = form.write({'request_id': 'd' * 32, **NEW, 'calendar': 'cal', 'guests': guests, 'invite': 2})
        self.assertEqual((done['status'], done['invited']), ('done', 2))
        self.assertEqual(form.google.writes[0][2]['attendees'], [{'email': g} for g in guests])


class Suggestions(unittest.TestCase):
    """Where and Invite suggestions: the owner's own places and people first; the map only gets the typed text."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.commit_page('cal', {}, [
            a.calendar_event(OWN, raw(f'e{i}', 'Coffee', {'dateTime': f'2026-10-2{i}T10:00:00+01:00'}, {'dateTime': f'2026-10-2{i}T11:00:00+01:00'},
                                      location=place), 'Europe/Brussels')
            for i, place in enumerate(['Café Labath, Oude Houtlei 1, Gent', 'Café Labath, Oude Houtlei 1, Gent', 'https://zoom.us/j/1'])])
        with self.store.connect() as db:
            for node, kind, name, strength, info in (
                    ('org:acme.example', 'org', 'Acme', 3, {'domain': 'acme.example'}),
                    ('p1', 'person', 'Sam Lee', 5, {'identifiers': ['email:sam@acme.example', 'phone:+3212']}),
                    ('p2', 'person', 'Samira Bot', 1, {'identifiers': ['email:noreply@acme.example']}),
                    ('p3', 'person', 'Kim', 2, {'identifiers': ['email:kim.sammy@example.net']}),
                    ('p4', 'person', 'Me', 9, {'identifiers': ['email:owner@example.com']})):
                db.execute('INSERT INTO graph_nodes(id,kind,name,strength,signals,info) VALUES(?,?,?,?,0,?)', (node, kind, name, strength, json.dumps(info)))
            db.execute("INSERT INTO org_profiles(domain,status,profile,fetched_at) VALUES('acme.example','found',?,'2026-10-01')",
                       (json.dumps({'location': 'Ghent, Belgium'}),))
        a.places._cache.clear();self.addCleanup(a.places._cache.clear)

    def test_people_from_the_graph(self):
        with self.store.connect() as db:
            self.assertEqual(a.invite_people(db, 'sam', {'owner@example.com'}),
                             [{'name': 'Sam Lee', 'email': 'sam@acme.example'}, {'name': 'Kim', 'email': 'kim.sammy@example.net'}])
            self.assertEqual(a.invite_people(db, 's'), [])
            self.assertEqual(a.invite_people(db, 'owner', {'owner@example.com'}), [])

    def test_own_places_first_then_the_map_with_only_the_text(self):
        asked = []
        def fetch(url, timeout):
            asked.append(url)
            return json.dumps({'features': [{'properties': {'name': 'Café Labath', 'street': 'Oude Houtlei', 'housenumber': '1',
                                                            'postcode': '9000', 'city': 'Gent', 'country': 'België / Belgique / Belgien'}},
                                            {'properties': {'name': 'Gent', 'city': 'Gent', 'country': 'Belgium'}}]}).encode()
        with self.store.connect() as db:
            found = a.places.suggest(db, {}, 'labath', fetch)
            self.assertEqual([(f['value'], f['source']) for f in found],
                             [('Café Labath, Oude Houtlei 1, Gent', 'calendar'), ('Gent, Belgium', 'map')])  # the café once: the owner's own
            self.assertEqual(asked, ['https://photon.komoot.io/api/?q=labath&limit=6'])
            a.places.suggest(db, {}, 'Labath ', fetch)
            self.assertEqual(len(asked), 1, 'cached')
            self.assertEqual([f['value'] for f in a.places.local(db, 'ghent')], ['Acme, Ghent, Belgium'])
            self.assertEqual([f['source'] for f in a.places.local(db, 'labath')], ['calendar'], 'an organization only when its name or place matches')
            self.assertEqual(a.places.local(db, 'zoom'), [], 'a link is not a place')
            a.places.suggest(db, {'place_search': False}, 'somewhere', fetch)
            a.places.suggest(db, {}, 'ab', fetch)
            self.assertEqual(len(asked), 1, 'off in the config, or too short: nothing goes out')
            def down(url, timeout):
                raise a.urllib.error.URLError('offline')
            self.assertEqual([f['source'] for f in a.places.suggest(db, {}, 'labath gent', down)], ['calendar'])

    def test_one_outside_request_a_second(self):
        t, slept = [10.0], []
        a.places._last[0] = 9.7
        a.places.photon('first place', fetch=lambda u, s: b'{}', clock=lambda: t[0], sleep=slept.append)
        self.assertEqual([round(x, 1) for x in slept], [0.7])


if __name__ == '__main__':
    unittest.main()
