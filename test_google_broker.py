"""Gmail and Calendar through a token broker (google_api.py): short-lived tokens per scope, only known
methods, the same send rules as gog. No network: urlopen is replaced by a fake broker and fake Google."""
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

import google_api
import next_actions as n
import onboarding
import test_ux as ux

a = ux.a
BROKER = 'https://cloud.example/api/google/token'


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Fake:
    """The broker grants `granted`; Google answers from `google` (url prefix -> status, body)."""

    def __init__(self, granted=('https://www.googleapis.com/auth/gmail.readonly',)):
        self.granted, self.google, self.calls, self.issued = set(granted), {}, [], 0

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), request.full_url, request.headers.get('Authorization'), body))
        if request.full_url == BROKER:
            if request.headers.get('Authorization') != 'Bearer machine-token':
                raise urllib.error.HTTPError(BROKER, 401, 'no', {}, io.BytesIO())
            if 'scope' not in body:
                return Response(json.dumps({'scopes': sorted(self.granted)}).encode())
            if body['scope'] not in self.granted:
                raise urllib.error.HTTPError(BROKER, 403, 'no', {}, io.BytesIO())
            self.issued += 1
            return Response(json.dumps({'access_token': f'access-{self.issued}', 'expires_in': 3600, 'scopes': sorted(self.granted)}).encode())
        for prefix, (status, answer) in self.google.items():
            if request.full_url.startswith(prefix):
                if status != 200:
                    raise urllib.error.HTTPError(request.full_url, status, 'no', {}, io.BytesIO())
                return Response(json.dumps(answer).encode())
        raise urllib.error.URLError('unreachable')


class Broker(unittest.TestCase):
    def setUp(self):
        google_api._tokens.clear()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        token = Path(self.temp.name) / 'google-broker'; token.write_text('machine-token\n')
        self.account = {'id': 'work-gmail', 'service': 'gmail', 'transport': 'broker', 'email': 'alex@acme.test', 'boundary': 'work:Acme'}
        self.config = {'google_broker': {'url': BROKER}, 'credentials': {'google-broker': str(token)}, 'accounts': [self.account]}
        self.fake = Fake()
        patcher = mock.patch.object(google_api.urllib.request, 'urlopen', self.fake); patcher.start(); self.addCleanup(patcher.stop)

    def test_reads_ask_the_broker_once_per_scope_and_call_google_directly(self):
        self.fake.google['https://gmail.googleapis.com/gmail/v1/users/me/messages/m%2F1'] = (200, {'id': 'm/1'})
        self.fake.google['https://gmail.googleapis.com/gmail/v1/users/me/messages?'] = (200, {'messages': []})
        found = a.gog_read(self.config, self.account, 'gmail', 'v1', 'gmail.users.messages.get', {'userId': 'me', 'id': 'm/1', 'format': 'full'},
                           'https://www.googleapis.com/auth/gmail.readonly')
        self.assertEqual(found, {'id': 'm/1'})
        a.gog_read(self.config, self.account, 'gmail', 'v1', 'gmail.users.messages.list', {'userId': 'me', 'q': 'in:inbox', 'labelIds': ['A', 'B'],
                   'includeSpamTrash': False}, 'https://www.googleapis.com/auth/gmail.readonly')
        broker = [c for c in self.fake.calls if c[1] == BROKER]
        self.assertEqual(len(broker), 1)  # the token is reused until shortly before it expires
        self.assertEqual(broker[0][3], {'email': 'alex@acme.test', 'scope': 'https://www.googleapis.com/auth/gmail.readonly'})
        method, url, auth, _ = self.fake.calls[-1]
        self.assertEqual((method, auth), ('GET', 'Bearer access-1'))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query, {'q': ['in:inbox'], 'labelIds': ['A', 'B'], 'includeSpamTrash': ['false']})
        self.assertTrue(self.fake.calls[1][1].endswith('/messages/m%2F1?format=full'))

    def test_an_expired_token_is_renewed_once_and_errors_stay_content_free(self):
        self.fake.google['https://www.googleapis.com/calendar/v3/calendars/primary/events'] = (401, None)
        with self.assertRaises(a.SourceError) as caught:
            a.gog_read(self.config, self.account, 'calendar', 'v3', 'calendar.events.list', {'calendarId': 'primary'},
                       'https://www.googleapis.com/auth/gmail.readonly')
        self.assertEqual((caught.exception.category, caught.exception.status), ('calendar', 401))
        self.assertEqual(self.fake.issued, 2)
        with self.assertRaises(a.SourceError) as caught:  # a scope the owner didn't grant
            a.gog_read(self.config, self.account, 'calendar', 'v3', 'calendar.events.list', {'calendarId': 'primary'},
                       'https://www.googleapis.com/auth/calendar.readonly')
        self.assertEqual(caught.exception.category, 'google_scope_not_granted')
        calls = len(self.fake.calls)
        with self.assertRaises(a.SourceError) as caught:  # only listed methods; refused before asking for a token
            a.gog_read(self.config, self.account, 'gmail', 'v1', 'gmail.users.messages.trash', {'userId': 'me', 'id': 'x'},
                       'https://www.googleapis.com/auth/gmail.readonly')
        self.assertEqual((caught.exception.category, len(self.fake.calls)), ('unsupported_google_method', calls))
        with self.assertRaises(google_api.BrokerError):  # and writes only through the write list
            google_api.url_for('gmail.users.messages.send', {'userId': 'me'})

    def test_requests_only_go_over_https_to_google_and_the_broker(self):
        verb, url = google_api.url_for('gmail.users.settings.sendAs.list', {'userId': 'me'})
        self.assertEqual((verb, url), ('GET', 'https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs'))
        _, url = google_api.url_for('gmail.users.messages.get', {'userId': 'me', 'id': '../../evil.example/x?y=1#z'})
        self.assertTrue(url.startswith('https://gmail.googleapis.com/gmail/v1/users/me/messages/..%2F..%2Fevil.example'), url)
        for bad in ('http://gmail.googleapis.com/x', 'https://evil.example/x', 'https://gmail.googleapis.com.evil.example/x',
                    'https://user@gmail.googleapis.com/x', 'https://gmail.googleapis.com:8443/x', 'file:///etc/passwd'):
            with self.assertRaises(google_api.BrokerError, msg=bad):
                google_api.google_url(bad)
        calls = len(self.fake.calls)
        for bad in ('http://cloud.example/api/google/token', 'file:///etc/passwd', 'https://', 'https://u:p@cloud.example/t'):
            with self.assertRaises(google_api.BrokerError, msg=bad) as caught:
                google_api.scopes({**self.config, 'google_broker': {'url': bad}}, a.credential, self.account)
            self.assertEqual(caught.exception.category, 'google_broker_not_https')
        self.assertEqual(len(self.fake.calls), calls)  # refused before anything was sent
        with self.assertRaises(google_api.BrokerError) as caught:
            google_api.scopes({**self.config, 'google_broker': {}}, a.credential, self.account)
        self.assertEqual(caught.exception.category, 'google_broker_not_configured')

    def test_sending_needs_a_granted_send_scope_and_never_retries_an_unclear_send(self):
        email = n.EmailTransport(self.config, a.credential, a.gog_read)
        action = {'kind': 'email', 'account_id': 'work-gmail', 'from': 'alex@acme.test'}
        with self.assertRaisesRegex(ValueError, 'read-only'):
            email.preflight(action)
        self.fake.granted.add('https://www.googleapis.com/auth/gmail.send')
        self.fake.google['https://gmail.googleapis.com/gmail/v1/users/me/profile'] = (200, {'emailAddress': 'Alex@acme.test'})
        email.preflight(action)
        send = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
        with mock.patch.object(n, 'mime_bytes', return_value=b'From: alex@acme.test\r\n\r\nHi'):
            self.fake.google[send] = (200, {'id': 'sent-1'})
            self.assertEqual(email.send({**action, 'thread_id': 't1'}, {}), {'message_id': 'sent-1'})
            self.assertEqual(self.fake.calls[-1][3]['threadId'], 't1')
            self.fake.google[send] = (400, None)
            self.assertEqual(email.send(action, {}), {'rejected': 400})
            self.fake.google[send] = (401, None)
            self.assertEqual(email.send(action, {}), {'rejected': 401})  # refused, like the gog path: nothing went out
            for status in (429, 500, 'unreachable'):
                if status == 'unreachable':
                    del self.fake.google[send]
                else:
                    self.fake.google[send] = (status, None)
                before = len([c for c in self.fake.calls if c[1] == send])  # the fake records even unreachable calls
                self.assertEqual(email.send(action, {}), {})  # unknown outcome: never sent twice
                self.assertEqual(len([c for c in self.fake.calls if c[1] == send]), before + 1)

    def test_snooze_needs_modify_and_setup_needs_the_broker(self):
        snooze = n.ThreadSnooze(self.config, a.credential, a.gog_read)
        self.assertFalse(snooze.allowed(self.account))
        self.fake.granted.add(n.MODIFY_SCOPE)
        self.assertTrue(snooze.allowed(self.account))
        self.fake.google['https://gmail.googleapis.com/gmail/v1/users/me/threads/t1/modify'] = (200, {'id': 't1'})
        self.assertEqual(snooze.write(self.account, 'gmail.users.threads.modify', {'userId': 'me', 'id': 't1'}, {'addLabelIds': ['L']}), {'id': 't1'})
        self.assertEqual(onboarding.missing(self.config, self.account), '')
        self.assertEqual(onboarding.missing({**self.config, 'google_broker': {}}, self.account), 'google_broker url is not set')
        self.assertEqual(onboarding.missing({**self.config, 'credentials': {}}, self.account), 'no google-broker credential yet')


if __name__ == '__main__':
    unittest.main()
