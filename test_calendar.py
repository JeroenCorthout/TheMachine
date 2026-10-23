"""The dashboard's Calendar: meetings that overlap a window of at most 15 days, as slim read-only rows, soonest first.
Declined and cancelled meetings stay out, all-day ones count on every day they cover, and only https links go out.
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


def meeting(source_id, start, end, title='Pilot review', **extra):
    raw = {'id': source_id, 'summary': title, 'start': start, 'end': end, 'status': 'confirmed', 'description': 'Private notes',
           'htmlLink': 'https://calendar.google.com/calendar/event?eid=' + source_id, **extra}
    return a.calendar_event(ACCOUNT, raw, 'Europe/Brussels')


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
        self.assertEqual(set(pilot), {'id', 'account_id', 'title', 'start', 'end', 'all_day', 'location', 'guests', 'url'})
        self.assertEqual((pilot['start'], pilot['end'], pilot['location'], pilot['guests']),
                         ('2026-10-23T16:00:00+00:00', '2026-10-23T16:45:00+00:00', 'Zoom', 2))
        self.assertTrue(pilot['url'].startswith('https://calendar.google.com/'))
        self.assertNotIn('Private notes', json.dumps(rows))  # no bodies
        self.assertIsNone(rows[2]['url'])  # only https links
        self.assertTrue(rows[3]['all_day'])
        self.assertEqual(self.store.calendar([], *self.week), [])
        self.assertEqual(self.store.calendar(['other'], *self.week), [])

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
