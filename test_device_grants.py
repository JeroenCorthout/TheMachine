"""Sign in with Google, Microsoft or Zoom for a paired self-hosted Machine, started on its own dashboard as Sign in with
Intercom is (test_device_connect.py). The control plane seals the refresh token for this Machine's own key; here
onboarding.connect_grant checks each account with a real read as the address it signed in as before keeping it, keeps the
token only here (mail_grants.py), and gets access tokens from the control plane it is paired with, over its own device
token, sending the refresh token along each time. Disconnecting the last account on a sign-in forgets it, here and
there. No live calls: the reads are stubbed and the control plane is a fake."""
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import assistant
import fleet
import google_api
import mail_grants
import network_crypto as nc
import onboarding
from test_connectors import a
from test_device_connect import context, fleet_with

REFRESH = 'fixture-refresh-token-1'
DEVICE = 'fixture-device-token'


class ConnectGrant(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'fleet.token').write_text(DEVICE + '\n')
        self.path = self.root / 'config.json'
        self.save({'database': str(self.root / 'events.sqlite'), 'credentials': {'fleet': str(self.root / 'fleet.token')}, 'accounts': []})
        self.checked, self.answer = [], None
        patch.object(onboarding, 'assistant', return_value=a).start()
        patch.object(onboarding, 'check', side_effect=lambda config, account: self.checked.append((config, dict(account))) or self.answer).start()
        self.addCleanup(patch.stopall)

    def save(self, config):
        self.path.write_text(json.dumps(config))

    def config(self):
        return json.loads(self.path.read_text())

    def args(self, provider='google', **change):
        grant = {'provider': provider, 'email': 'Dee@Acme.example', 'refresh_token': REFRESH, 'scopes': ['openid'], **change}
        return {'source': provider, 'grant': grant}

    def test_google_adds_gmail_and_calendar_kept_here_and_refreshed_at_the_paired_control_plane(self):
        self.assertEqual(onboarding.connect_delivered(self.path, self.args()), {'ok': True, 'name': 'dee@acme.example'})
        config = self.config()
        self.assertEqual(sorted((x['service'], x['email'], x['transport']) for x in config['accounts']),
                         [('calendar', 'dee@acme.example', 'broker'), ('gmail', 'dee@acme.example', 'broker')])
        self.assertEqual({x['boundary'] for x in config['accounts']}, {'work:Acme'})
        self.assertEqual(config['google_broker'], {'url': fleet.NETWORK + '/api/fleet/google-token'})
        self.assertEqual(config['credentials']['google-broker'], config['credentials']['fleet'])  # its own device token
        self.assertNotIn(REFRESH, self.path.read_text())
        self.assertEqual(mail_grants.get(config, 'google', 'dee@acme.example'), REFRESH)  # kept here only, sealed
        self.assertEqual([x[1]['service'] for x in self.checked], ['gmail', 'calendar'])  # each read as that address first

    def test_microsoft_adds_outlook_mail_and_calendar_and_zoom_one_work_account(self):
        onboarding.connect_delivered(self.path, self.args('microsoft'))
        onboarding.connect_delivered(self.path, self.args('zoom', email='dee@gmail.com'))
        config = self.config()
        self.assertEqual(sorted(x['service'] for x in config['accounts']), ['outlook', 'outlook_calendar', 'zoom'])
        self.assertTrue(all(x.get('connected_at') for x in config['accounts'] if x['service'].startswith('outlook')))
        [zoom] = [x for x in config['accounts'] if x['service'] == 'zoom']
        self.assertTrue(zoom['boundary'].startswith('work'))
        self.assertEqual((config['microsoft_broker']['url'], config['zoom_broker']['url']),
                         (fleet.NETWORK + '/api/fleet/microsoft-token', fleet.NETWORK + '/api/fleet/zoom-token'))

    def test_an_account_that_does_not_check_out_leaves_nothing(self):
        self.answer = 'account_identity_mismatch'
        with self.assertRaisesRegex(onboarding.SetupError, 'did not check out'):
            onboarding.connect_delivered(self.path, self.args())
        config = self.config()
        self.assertEqual((config['accounts'], 'google_broker' in config), ([], False))
        self.assertIsNone(mail_grants.get(config, 'google', 'dee@acme.example'))

    def test_signing_in_again_keeps_the_accounts_and_a_failed_one_keeps_the_sign_in_before(self):
        onboarding.connect_delivered(self.path, self.args())
        before = self.config()['accounts']
        onboarding.connect_delivered(self.path, self.args(refresh_token='fixture-refresh-token-2'))
        self.assertEqual([x['id'] for x in self.config()['accounts']], [x['id'] for x in before])  # the same accounts, no copies
        self.answer = 'google_broker'
        with self.assertRaises(onboarding.SetupError):
            onboarding.connect_delivered(self.path, self.args(refresh_token='fixture-refresh-token-3'))
        self.assertEqual(mail_grants.get(self.config(), 'google', 'dee@acme.example'), 'fixture-refresh-token-2')

    def test_a_config_save_failure_restores_the_previous_sign_in_or_removes_the_uncommitted_one(self):
        for reconnect in (False, True):
            self.save({'database': str(self.root / 'events.sqlite'), 'credentials': {'fleet': str(self.root / 'fleet.token')}, 'accounts': []})
            mail_grants.forget(self.config(), 'google', 'dee@acme.example', revoke=False)
            if reconnect:
                onboarding.connect_delivered(self.path, self.args())
            before = self.path.read_bytes()
            with patch.object(onboarding, 'save', side_effect=OSError('disposable config write failure')):
                with self.assertRaises(OSError):
                    onboarding.connect_delivered(self.path, self.args(refresh_token='fixture-refresh-token-uncommitted'))
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(mail_grants.get(self.config(), 'google', 'dee@acme.example'), REFRESH if reconnect else None)

    def test_only_a_whole_grant_for_a_paired_self_hosted_machine(self):
        for args in (self.args(provider='microsoft') | {'source': 'google'}, self.args(email='not an address'),
                     self.args(refresh_token='short'), self.args(refresh_token=None), {'source': 'google'}):
            with self.assertRaises(onboarding.SetupError):
                onboarding.connect_delivered(self.path, args)
        config = {'database': str(self.root / 'events.sqlite'), 'credentials': {}, 'accounts': []}
        for changed in ({}, {'credentials': {'fleet': str(self.root / 'fleet.token')}, 'manage_url': 'https://app.example.org/account'},
                        {'credentials': {'fleet': str(self.root / 'fleet.token')}, 'google_broker': {'url': 'https://elsewhere.example/api/google/token'}}):
            self.save({**config, **changed})
            with self.assertRaises(onboarding.SetupError):
                onboarding.connect_delivered(self.path, self.args())
        self.assertEqual(self.checked, [])

    def test_an_access_token_comes_from_the_paired_control_plane_with_the_refresh_token_sent_along(self):
        onboarding.connect_delivered(self.path, self.args())
        config = self.config()
        asked = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout=None):
            asked.append((request.full_url, request.headers.get('Authorization'), json.loads(request.data)))
            return Answer(json.dumps({'access_token': 'fixture-access', 'expires_in': 3600}).encode())
        credential = lambda config, name: Path(config['credentials'][name]).read_text().strip()
        account = next(x for x in config['accounts'] if x['service'] == 'gmail')
        with patch('urllib.request.urlopen', urlopen):
            self.assertEqual(google_api.token(config, credential, account, 'https://www.googleapis.com/auth/gmail.readonly', fresh=True), 'fixture-access')
        self.assertEqual(asked, [(fleet.NETWORK + '/api/fleet/google-token', 'Bearer ' + DEVICE,
                                  {'email': 'dee@acme.example', 'scope': 'https://www.googleapis.com/auth/gmail.readonly', 'refresh_token': REFRESH})])


class Round(unittest.TestCase):
    def test_a_google_sign_in_is_opened_connected_and_shown_without_its_token_in_the_log(self):
        f = fleet_with(self, [])
        body = {'command': 'connect', 'args': {'source': 'google', 'grant': {'provider': 'google', 'email': 'dee@acme.example', 'refresh_token': REFRESH}}}
        f.relay.items = [{'id': 'dc_1', 'sealed': nc.seal_box(body, nc.unb64(nc.public_keys(f.private)['box'], 32), context('dev_1', 'dc_1'))}]
        f.connect_delivered = lambda got: f.got.append(got) or {'ok': True, 'name': 'dee@acme.example'}
        f.connects_due = True
        with self.assertLogs(fleet.LOG, logging.DEBUG) as logs:
            fleet.LOG.debug('start')
            f.connects_round()
        self.assertEqual(f.got, [body])
        self.assertEqual(f.signin_state('google'), {'state': 'done', 'name': 'dee@acme.example'})
        self.assertNotIn(REFRESH, '\n'.join(logs.output))


class Dashboard(unittest.TestCase):
    def app(self, accounts=(), paired=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'config.json'
        config = {'accounts': list(accounts), 'database': str(Path(temp.name) / 'events.sqlite')}
        path.write_text(json.dumps(config))
        app = object.__new__(assistant.App)
        app.config, app.config_path = config, path
        app.fleet = fleet_with(self, []) if paired else None
        return app

    def test_gmail_calendar_outlook_and_zoom_sign_in_with_their_provider(self):
        app = self.app()
        found = {x['service']: x.get('signin') for x in app.action('connect_sources', {}, owner_dashboard=True)['sources']}
        self.assertEqual({k: found.get(k) for k in ('gmail', 'calendar', 'outlook', 'outlook_calendar', 'zoom', 'intercom')},
                         {'gmail': 'google', 'calendar': 'google', 'outlook': 'microsoft', 'outlook_calendar': 'microsoft', 'zoom': 'zoom', 'intercom': 'intercom'})
        app.action('connect_signin', {'name': 'calendar'}, owner_dashboard=True)
        body = app.fleet.relay.calls[-1][2]
        self.assertEqual((app.fleet.relay.calls[-1][1], body['source']), ('/connect-start', 'google'))
        self.assertEqual(app.action('connect_signin_state', {'name': 'gmail'}, owner_dashboard=True), {'state': 'started'})
        with self.assertRaisesRegex(ValueError, 'Pair this Machine first'):
            self.app(paired=False).action('connect_signin', {'name': 'gmail'}, owner_dashboard=True)

    def test_the_last_account_on_a_sign_in_forgets_it_here_and_there(self):
        gmail = {'id': 'acme-gmail', 'service': 'gmail', 'email': 'dee@acme.example', 'transport': 'broker', 'boundary': 'work:Acme'}
        calendar = {**gmail, 'id': 'acme-calendar', 'service': 'calendar'}
        app = self.app([gmail, calendar])
        mail_grants.put(app.config, 'google', 'dee@acme.example', REFRESH)
        with patch.object(mail_grants, '_revoke_google', return_value=True) as revoke, patch.object(assistant, 'restart_self', return_value=False):
            app.action('connect_remove', {'account': 'acme-gmail'}, owner_dashboard=True)
            self.assertEqual(mail_grants.get(app.config, 'google', 'dee@acme.example'), REFRESH)  # Calendar still reads with it
            app.config['accounts'] = [calendar]
            app.action('connect_remove', {'account': 'acme-calendar'}, owner_dashboard=True)
        self.assertIsNone(mail_grants.get(app.config, 'google', 'dee@acme.example'))
        revoke.assert_called_once()
        self.assertIn(('POST', '/grant-forget', {'provider': 'google', 'email': 'dee@acme.example'}), app.fleet.relay.calls)


if __name__ == '__main__':
    unittest.main()
