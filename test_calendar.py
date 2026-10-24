"""The dashboard's Calendar: meetings that overlap a window of at most 15 days, as slim read-only rows, soonest first.
Declined and canceled meetings stay out, all-day ones count on every day they cover, and only https links go out.
No live calls."""
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('calendar_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

ACCOUNT = {'id': 'cal', 'email': 'owner@example.com', 'service': 'calendar', 'boundary': 'work:Example'}
SHARED = {'id': 'partner-cal', 'email': 'owner@example.com', 'service': 'calendar', 'boundary': 'personal:Home', 'calendar_id': 'robin.lane@example.net'}


def meeting(source_id, start, end, title='Pilot review', account=ACCOUNT, **extra):
    raw = {'id': source_id, 'summary': title, 'start': start, 'end': end, 'status': 'confirmed', 'description': 'Private notes',
           'htmlLink': 'https://calendar.google.com/calendar/event?eid=' + source_id, **extra}
    return a.calendar_event(account, raw, 'Europe/Brussels')


def eid(event, calendar):
    return a.base64.urlsafe_b64encode(f'{event} {calendar}'.encode()).decode().rstrip('=')


class Calendar(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        odd = meeting('odd', {'dateTime': '2026-10-25T09:00:00+02:00'}, {'dateTime': '2026-10-25T09:30:00+02:00'}, 'Odd link')
        odd['metadata']['source_url'] = 'javascript:alert(1)'
        self.store.commit_page('cal', {}, [
            meeting('before', {'dateTime': '2026-10-22T10:00:00+02:00'}, {'dateTime': '2026-10-22T11:00:00+02:00'}, 'Yesterday'),
            meeting('ongoing', {'dateTime': '2026-10-22T23:30:00+02:00'}, {'dateTime': '2026-10-23T00:30:00+02:00'}, 'Late call'),
            meeting('pilot', {'dateTime': '2026-10-23T18:00:00+02:00'}, {'dateTime': '2026-10-23T18:45:00+02:00'}, location='Zoom',
                    attendees=[{'email': 'owner@example.com', 'self': True, 'responseStatus': 'accepted'},
                               {'email': 'sam@client.example'}, {'email': 'kim@client.example'}]),
            meeting('declined', {'dateTime': '2026-10-24T09:00:00+02:00'}, {'dateTime': '2026-10-24T10:00:00+02:00'}, 'Said no',
                    attendees=[{'email': 'owner@example.com', 'self': True, 'responseStatus': 'declined'}]),
            meeting('cancelled', {'dateTime': '2026-10-24T11:00:00+02:00'}, {'dateTime': '2026-10-24T12:00:00+02:00'}, 'Off', status='cancelled'),
            meeting('offsite', {'date': '2026-10-26'}, {'date': '2026-10-28'}, 'Team offsite'),
            meeting('later', {'dateTime': '2026-11-02T09:00:00+01:00'}, {'dateTime': '2026-11-02T10:00:00+01:00'}, 'Next month'),
            odd])
        self.week = ('2026-10-22T22:00:00.000Z', '2026-10-29T23:00:00.000Z')  # Brussels midnight to midnight, seven days

    def test_the_week_is_slim_rows_soonest_first(self):
        rows = self.store.calendar(['cal'], *self.week)
        self.assertEqual([r['title'] for r in rows], ['Late call', 'Pilot review', 'Odd link', 'Team offsite'])
        pilot = rows[1]
        self.assertEqual(set(pilot), {'id', 'account_id', 'title', 'start', 'end', 'all_day', 'location', 'guests', 'people', 'url', 'shared'})
        self.assertEqual((pilot['people'], pilot['shared']), (['sam@client.example', 'kim@client.example'], None))
        self.assertEqual((pilot['start'], pilot['end'], pilot['location'], pilot['guests']),
                         ('2026-10-23T16:00:00+00:00', '2026-10-23T16:45:00+00:00', 'Zoom', 2))
        self.assertTrue(pilot['url'].startswith('https://calendar.google.com/'))
        self.assertNotIn('Private notes', json.dumps(rows))  # no bodies
        self.assertIsNone(rows[2]['url'])  # only https links
        self.assertTrue(rows[3]['all_day'])
        self.assertEqual(self.store.calendar([], *self.week), [])
        self.assertEqual(self.store.calendar(['other'], *self.week), [])

    def shared_week(self):
        at = lambda h: {'dateTime': f'2026-10-27T{h}:00:00+01:00'}
        theirs = {'email': 'robin.lane@example.net', 'self': True}  # 'self' on a shared calendar is its owner
        self.store.commit_page('partner-cal', {}, [
            meeting('trip', {'dateTime': '2026-10-23T19:00:00+02:00'}, {'dateTime': '2026-10-25T19:00:00+01:00'}, 'Weekend away', SHARED, organizer=theirs),
            meeting('parents', at('16'), at('17'), 'Parents evening', SHARED, organizer={'email': 'owner@example.com', 'displayName': 'Owner'},
                    attendees=[{'email': 'robin.lane@example.net', 'self': True, 'responseStatus': 'accepted'}],
                    htmlLink='https://www.google.com/calendar/event?eid=' + eid('abc123', 'robin.lane@example.net')),
            meeting('dinner', at('19'), at('21'), 'Dinner with Alex', SHARED, organizer=theirs,
                    attendees=[{'email': 'robin.lane@example.net', 'self': True}, {'email': 'owner@example.com', 'responseStatus': 'accepted'}]),
            meeting('their-no', at('09'), at('10'), 'Dentist', SHARED, organizer=theirs,
                    attendees=[{'email': 'owner@example.com', 'responseStatus': 'declined'}, {'email': 'robin.lane@example.net', 'self': True}])])
        self.store.commit_page('cal', {}, [
            meeting('parents-mine', at('16'), at('17'), 'Parents evening', organizer={'email': 'owner@example.com', 'self': True},
                    htmlLink='https://www.google.com/calendar/event?eid=' + eid('abc123', 'owner@example.com')),
            meeting('copy', at('12'), at('13'), 'Lunch  with Sam'), meeting('copy-2', at('12'), at('13'), 'lunch with sam')])
        return {m['title']: m for m in self.store.calendar(['cal', 'partner-cal'], *self.week, owner={'Owner@example.com'})}

    def test_a_shared_calendar_shows_marked_and_a_meeting_on_both_once_as_the_owners(self):
        rows = self.shared_week()
        # Someone else's event on a calendar shared with the owner: shown, with that person's first name
        self.assertEqual((rows['Weekend away']['shared'], rows['Weekend away']['account_id']), ('Robin', 'partner-cal'))
        # On both calendars (the same Google event): once, the owner's own copy
        self.assertEqual((rows['Parents evening']['account_id'], rows['Parents evening']['shared']), ('cal', None))
        # Invited on the shared calendar: theirs to attend, so it is the owner's meeting; declined stays out
        self.assertIsNone(rows['Dinner with Alex']['shared'])
        self.assertEqual(rows['Dinner with Alex']['guests'], 1)
        self.assertNotIn('Dentist', rows)
        # Two invites with the same start, end and title: once
        self.assertEqual(sum(t.casefold().split() == ['lunch', 'with', 'sam'] for t in rows), 1)

    def test_a_shared_calendar_never_makes_cards(self):
        self.shared_week()
        with self.store.connect() as db:
            ids = dict(db.execute("SELECT source_id,id FROM events WHERE source_id IN ('trip','dinner')").fetchall())
        with self.assertRaisesRegex(ValueError, "someone else's calendar"):
            self.store.suggest('trip-prep', 'Pack for the weekend', 'Pack for it.', [ids['trip']])
        self.assertEqual(self.store.suggest('dinner-prep', 'Book the dinner', 'Book a table.', [ids['dinner']])['status'], 'pending')
        with self.store.connect() as db:
            trip = dict(db.execute('SELECT * FROM events WHERE id=?', (ids['trip'],)).fetchone())
        self.assertIn("Robin's calendar", a.review_view(trip)['not_owners'])

    def test_the_window_is_bounded_and_has_time_zones(self):
        for start, end in [('2026-10-23T00:00:00', '2026-10-24T00:00:00'), ('2026-10-23T00:00:00Z', '2026-11-23T00:00:00Z'),
                           ('2026-10-24T00:00:00Z', '2026-10-23T00:00:00Z'), ('', ''), ('soon', 'later')]:
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.store.calendar(['cal'], start, end)

    def test_dashboard_route_is_read_only_for_the_owner(self):
        app = object.__new__(a.App)
        app.store, app.token, app.stop = self.store, 'test-token', threading.Event()
        app.graph_dirty = threading.Event()
        app.owner_token = 'owner-key'
        app.config = {'accounts': [ACCOUNT], 'tailscale_owner': 'owner@example.com', 'dashboard_host': 'machine.example:8443',
                      'port': 0, 'http_timeout': 1}
        servers, real = [], a.http.server.ThreadingHTTPServer
        def capture(*args, **kwargs):
            servers.append(real(*args, **kwargs)); return servers[-1]
        for patcher in (patch.object(a.http.server, 'ThreadingHTTPServer', side_effect=capture), patch.object(a.signal, 'signal'),
                        patch.object(a.App, 'preparation_worker'), patch.object(a.App, 'delivery_worker')):
            patcher.start()
            self.addCleanup(patcher.stop)
        thread = threading.Thread(target=app.serve, daemon=True)
        thread.start()
        while not servers:
            pass
        self.addCleanup(lambda: (app.stop.set(), thread.join(5)))
        port = servers[0].server_address[1]
        owner = {'Host': 'machine.example:8443', 'Tailscale-User-Login': 'owner@example.com'}
        def get(path):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            conn.request('GET', path, headers=owner)
            r = conn.getresponse()
            body = r.read()
            conn.close()
            return r.status, json.loads(body)
        before = self.store.snapshot([ACCOUNT])
        status, body = get('/api/calendar?from=' + self.week[0] + '&to=' + self.week[1])
        self.assertEqual(status, 200)
        self.assertEqual([m['title'] for m in body['meetings']], ['Late call', 'Pilot review', 'Odd link', 'Team offsite'])
        self.assertEqual(get('/api/calendar?from=2026-10-23T00:00:00Z&to=2027-01-01T00:00:00Z')[0], 400)
        self.assertEqual(get('/api/calendar')[0], 400)
        after = self.store.snapshot([ACCOUNT])
        self.assertEqual((before['suggestions'], before['decisions']), (after['suggestions'], after['decisions']))


if __name__ == '__main__':
    unittest.main()
