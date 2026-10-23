"""Zoom fixture tests: every HTTP request is faked, nothing reaches Zoom or a broker. Only GETs go to Zoom; the sign-in
goes to the broker only, never into a URL, an event or an error."""
import json
from pathlib import Path
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from test_connectors import a, Response
from connectors import zoom
import mail_grants

NOW = '2026-10-23T12:00:00+00:00'
ACCOUNT = {**zoom.SETUP['account'], 'email': 'owner@acme.example'}
UUID = 'aBc/123//xyz=='
SUMMARY = {'meeting_host_id': 'h1', 'meeting_host_email': 'owner@acme.example', 'meeting_uuid': UUID, 'meeting_id': 81234567890,
           'meeting_topic': 'Sprint prep week 1: issues', 'meeting_start_time': '2026-10-23T08:00:00Z',
           'meeting_end_time': '2026-10-23T08:45:00Z', 'summary_created_time': '2026-10-23T08:50:00Z',
           'summary_last_modified_time': '2026-10-23T08:50:00Z', 'summary_doc_url': 'https://docs.zoom.us/doc/abc',
           'summary_overview': 'The team went over the open issues for week 1.',
           'summary_details': [{'label': 'Login bug', 'summary': 'Robin will look at the login bug.'}],
           'next_steps': ['Robin fixes the login bug by Monday.', 'Owner sends the release notes.']}
VTT = 'WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nRobin: I can take the login bug.\n\n2\n00:00:03.000 --> 00:00:05.000\nRobin: By Monday.\n'


class ZoomTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = a.Store(Path(tmp.name) / 'events.sqlite')
        self.config = {'database': str(Path(tmp.name) / 'events.sqlite'), 'zoom_broker': {'url': 'https://cp.example/api/zoom/token'}}
        mail_grants.put(self.config, 'zoom', 'owner@acme.example', 'refresh-1')
        zoom._tokens.clear()
        self.helpers = types.SimpleNamespace(SourceError=a.SourceError, now=lambda: NOW, iso=a.CONNECTOR_HELPERS.iso,
                                             credential=lambda config, name: 'app-token' if name == 'zoom-broker' else 1 / 0)
        self.listed = {'summaries': [{'meeting_uuid': UUID, 'summary_last_modified_time': '2026-10-23T08:50:00Z'}], 'next_page_token': ''}
        self.summary, self.transcript, self.list_status = dict(SUMMARY), True, 200
        self.calls, self.broker_calls = [], []
        patch('urllib.request.urlopen', side_effect=self.respond).start()
        self.addCleanup(patch.stopall)

    def respond(self, request, timeout):
        url = urllib.parse.urlsplit(request.full_url)
        if url.netloc == 'cp.example':
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.get_header('Authorization'), 'Bearer app-token')
            sent = json.loads(request.data)
            self.broker_calls.append(sent)
            return Response(json.dumps({'access_token': 'access-' + sent['refresh_token'], 'expires_in': 3600,
                                        'refresh_token': 'refresh-' + str(len(self.broker_calls) + 1)}).encode())
        self.assertEqual(request.get_method(), 'GET')  # nothing is ever written to Zoom
        self.assertTrue(url.netloc.endswith('zoom.us'))
        self.assertNotIn('refresh', request.full_url)
        self.assertTrue(request.get_header('Authorization').startswith('Bearer access-'))
        self.calls.append(url.path)
        if url.path == '/v2/meetings/meeting_summaries':
            if self.list_status != 200:
                raise urllib.error.HTTPError(request.full_url, self.list_status, 'no', {}, None)
            return Response(json.dumps(self.listed).encode())
        if url.path == '/v2/users/me':
            return Response(json.dumps({'id': 'h1', 'email': 'owner@acme.example'}).encode())
        if url.path == '/v2/users/me/meetings':
            return Response(json.dumps({'meetings': [{'id': 81234567890, 'start_time': '2026-10-23T08:00:00Z'}]}).encode())
        if url.path == '/v2/past_meetings/81234567890/instances':
            return Response(json.dumps({'meetings': [{'uuid': UUID, 'start_time': '2026-10-23T08:00:00Z'}]}).encode())
        encoded = urllib.parse.quote(urllib.parse.quote(UUID, safe=''), safe='')  # starts with / or holds //: twice
        if request.full_url == f'https://api.zoom.us/v2/meetings/{encoded}/meeting_summary':
            return Response(json.dumps(self.summary).encode())
        if request.full_url == f'https://api.zoom.us/v2/meetings/{encoded}/transcript':
            if not self.transcript:
                raise urllib.error.HTTPError(request.full_url, 404, 'none', {}, None)
            return Response(json.dumps({'can_download': True, 'download_url': self.transcript if isinstance(self.transcript, str)
                                        else 'https://us02web.zoom.us/rec/download/t1'}).encode())
        if url.path == '/rec/download/t1':
            return Response(VTT.encode())
        raise AssertionError('unexpected request ' + request.full_url)

    def sync(self):
        return zoom.sync(self.store, self.config, ACCOUNT, self.helpers)

    def rows(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT kind,source_id,title,body,metadata,occurred_at,version FROM events ORDER BY kind')]

    def test_a_summary_and_its_transcript_are_stored_once_and_the_rotated_sign_in_kept(self):
        self.assertEqual(self.sync()['events'], 2)
        summary, transcript = self.rows()
        self.assertEqual((summary['kind'], summary['title']), ('zoom.summary', 'Sprint prep week 1: issues'))
        self.assertIn('Next steps\n- Robin fixes the login bug by Monday.\n- Owner sends the release notes.', summary['body'])
        meta = json.loads(summary['metadata'])
        self.assertEqual((meta['meeting_id'], meta['start_at'], meta['source_url']),
                         ('81234567890', '2026-10-23T08:00:00+00:00', 'https://docs.zoom.us/doc/abc'))
        self.assertNotIn(UUID, summary['source_id'] + transcript['source_id'])  # ids are hashes
        self.assertEqual(transcript['body'], 'Robin: I can take the login bug. By Monday.')
        # Zoom rotates the sign-in on each refresh: the new one is kept here, sealed
        self.assertEqual(self.broker_calls, [{'email': 'owner@acme.example', 'refresh_token': 'refresh-1'}])
        self.assertEqual(mail_grants.get(self.config, 'zoom', 'owner@acme.example'), 'refresh-2')
        self.assertNotIn('refresh-2', Path(mail_grants.path(self.config)).read_text())
        # A second run: nothing new, no second read of the summary, no new refresh while the access token is good
        self.calls.clear()
        self.assertEqual(self.sync()['events'], 0)
        self.assertEqual(self.calls, ['/v2/meetings/meeting_summaries'])
        self.assertEqual(len(self.broker_calls), 1)

    def test_an_edited_summary_is_read_again_and_the_newest_steps_win(self):
        self.sync()
        self.listed['summaries'][0]['summary_last_modified_time'] = '2026-10-23T10:00:00Z'
        self.summary.update(summary_last_modified_time='2026-10-23T10:00:00Z',
                            edited_summary={'next_steps': ['Sam fixes the login bug.']})
        self.sync()
        summary = next(r for r in self.rows() if r['kind'] == 'zoom.summary')
        self.assertEqual(summary['version'], 2)
        self.assertEqual(json.loads(summary['metadata'])['next_steps'], ['Sam fixes the login bug.'])

    def test_full_markdown_wins_and_no_transcript_is_fine(self):
        self.summary['summary_content'] = '## Quick recap\nIssues.\n\n## Next steps\n- Robin: the login bug'
        self.transcript = False
        self.assertEqual(self.sync()['events'], 1)
        self.assertEqual(self.rows()[0]['body'], '## Quick recap\nIssues.\n\n## Next steps\n- Robin: the login bug')

    def test_past_meetings_stand_in_when_the_summaries_list_is_closed_to_this_sign_in(self):
        self.list_status = 403
        self.assertEqual(self.sync()['events'], 2)
        self.assertIn('/v2/past_meetings/81234567890/instances', self.calls)
        self.assertEqual(self.store.checkpoint('zoom')['mode'], 'past_meetings')

    def test_a_download_link_off_zoom_is_never_followed_with_the_token(self):
        self.transcript = 'https://evil.example/steal'
        with self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual(caught.exception.category, 'zoom_unexpected_host')

    def test_a_refused_sign_in_asks_to_connect_again_without_saying_more(self):
        def refuse(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, 'revoked', {}, None)
        with patch('urllib.request.urlopen', side_effect=refuse), self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual(caught.exception.category, 'zoom_signin_expired')
        self.assertNotIn('refresh', repr(caught.exception))
        mail_grants.forget(self.config, 'zoom', 'owner@acme.example')
        zoom._tokens.clear()
        with self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual(caught.exception.category, 'zoom_signin_expired')

    def test_rate_limits_and_errors_are_content_free(self):
        self.list_status = 429
        with self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual((caught.exception.category, caught.exception.status), ('zoom_http', 429))

    def test_check_reads_who_is_signed_in_and_stores_nothing(self):
        self.assertIsNone(zoom.check(self.config, ACCOUNT, self.helpers))
        self.assertEqual(self.rows(), [])
        with self.assertRaises(a.SourceError) as caught:
            zoom.check(self.config, {**ACCOUNT, 'email': 'someone@else.example'}, self.helpers)
        self.assertIn(caught.exception.category, ('account_identity_mismatch', 'zoom_signin_expired'))

    def test_without_a_broker_it_says_so(self):
        with self.assertRaises(a.SourceError) as caught:
            zoom.sync(self.store, {'database': self.config['database']}, ACCOUNT, self.helpers)
        self.assertEqual(caught.exception.category, 'zoom_broker_not_configured')

    def test_zoom_is_offered_where_a_hosting_page_signs_it_in(self):
        import onboarding
        found = {s['name']: s for s in onboarding.catalog({'accounts': []})}
        self.assertTrue(found['zoom']['broker'])
        self.assertEqual(onboarding.missing({'credentials': {}}, ACCOUNT), 'zoom_broker url is not set')


if __name__ == '__main__':
    unittest.main()
