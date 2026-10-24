"""Broker sign-ins kept on the Machine (mail_grants.py): sealed with the message key, sent along with each token request,
rotated ones kept, revoked at Google when forgotten. No network: urlopen is replaced by a fake broker and fake Google."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import google_api
import mail_grants
import message_seal
import ms_graph

GOOGLE_BROKER, MS_BROKER = 'https://cloud.example/api/google/token', 'https://cloud.example/api/microsoft/token'


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Fake:
    def __init__(self, rotate=None):
        self.calls, self.rotate, self.revoke_status = [], rotate, 200

    def __call__(self, request, timeout=None):
        url = request.full_url
        if url == mail_grants.GOOGLE_REVOKE:
            self.calls.append(('revoke', request.data.decode()))
            if self.revoke_status != 200:
                raise urllib.error.HTTPError(url, self.revoke_status, 'no', {}, io.BytesIO())
            return Response(b'{}')
        body = json.loads(request.data)
        self.calls.append((url, body))
        if 'scope' not in body:
            return Response(json.dumps({'scopes': ['Mail.ReadWrite']}).encode())
        answer = {'access_token': 'access', 'expires_in': 3600, 'scopes': ['Mail.ReadWrite']}
        if self.rotate:
            answer['refresh_token'] = self.rotate
        return Response(json.dumps(answer).encode())


class Grants(unittest.TestCase):
    def setUp(self):
        google_api._tokens.clear(); ms_graph._tokens.clear()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'broker').write_text('machine-token\n')
        self.config = {'database': str(root / 'events.sqlite'), 'google_broker': {'url': GOOGLE_BROKER},
                       'microsoft_broker': {'url': MS_BROKER},
                       'credentials': {'google-broker': str(root / 'broker'), 'microsoft-broker': str(root / 'broker')}}
        self.fake = Fake()
        for module in (google_api, ms_graph, mail_grants):
            patcher = mock.patch.object(module.urllib.request, 'urlopen', self.fake); patcher.start(); self.addCleanup(patcher.stop)
        self.credential = lambda config, name: Path(config['credentials'][name]).read_text().strip()

    def test_sealed_on_disk_with_the_message_key_and_only_readable_by_the_owner(self):
        mail_grants.put(self.config, 'google', 'Alex@Acme.test', '1//refresh-secret')
        path = mail_grants.path(self.config)
        self.assertNotIn('refresh-secret', path.read_text())
        if os.name != 'nt':  # POSIX modes are not Windows ACLs; Windows runs the service inside WSL2
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(message_seal.key_path(self.config['database']).is_file())
        self.assertEqual(mail_grants.get(self.config, 'google', 'alex@acme.test'), '1//refresh-secret')
        self.assertIsNone(mail_grants.get(self.config, 'microsoft', 'alex@acme.test'))
        self.assertEqual(mail_grants.held(self.config), [('google', 'alex@acme.test')])

    def test_late_rotation_does_not_overwrite_a_new_sign_in_or_restore_a_removed_one(self):
        account = {'email': 'alex@acme.test', 'transport': 'broker'}
        for provider, module in [('google', google_api), ('microsoft', ms_graph)]:
            for remove in (False, True):
                mail_grants.put(self.config, provider, account['email'], 'old-token')
                def delayed(*args, **kwargs):
                    if remove:
                        mail_grants.forget(self.config, provider, account['email'], revoke=False)
                    else:
                        mail_grants.put(self.config, provider, account['email'], 'new-login')
                    return {'access_token': 'access', 'refresh_token': 'rotated-old-token', 'expires_in': 3600}
                with mock.patch.object(module, '_broker', delayed):
                    if provider == 'google':
                        google_api.token(self.config, self.credential, account, 'gmail.readonly', fresh=True)
                    else:
                        ms_graph._from_broker(self.config, self.credential, account, 'Mail.Read')
                self.assertEqual(mail_grants.get(self.config, provider, account['email']), None if remove else 'new-login')

    @unittest.skipIf(os.name == 'nt', 'directory fsync is a POSIX durability step; Windows service runs in WSL2')
    def test_failed_directory_flush_is_not_acknowledged_and_saved_receipt_is_recoverable(self):
        real = os.fsync
        import stat
        def flush(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('directory flush failed')
            return real(fd)
        with mock.patch.object(mail_grants.os, 'fsync', side_effect=flush):
            with self.assertRaises(OSError):
                mail_grants.put(self.config, 'google', 'alex@acme.test', 'token', handoff='ho_flush')
        self.assertTrue(mail_grants.handed(self.config, 'google', 'alex@acme.test', 'ho_flush'))

    def test_exact_handoff_receipt_survives_rotation_and_changes_on_reconnect(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'old', handoff='ho_1')
        self.assertTrue(mail_grants.handed(self.config, 'google', 'alex@acme.test', 'ho_1'))
        mail_grants.rotate(self.config, 'google', 'alex@acme.test', 'old', 'rotated')
        self.assertTrue(mail_grants.handed(self.config, 'google', 'alex@acme.test', 'ho_1'))
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'new', handoff='ho_2')
        self.assertFalse(mail_grants.handed(self.config, 'google', 'alex@acme.test', 'ho_1'))
        self.assertTrue(mail_grants.handed(self.config, 'google', 'alex@acme.test', 'ho_2'))
        self.assertNotIn('ho_2', mail_grants.path(self.config).read_text())

    def test_a_sign_in_moved_to_another_name_is_refused(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'secret')
        data = json.loads(mail_grants.path(self.config).read_text())
        data['google:sam@acme.test'] = data['google:alex@acme.test']
        mail_grants.path(self.config).write_text(json.dumps(data))
        with self.assertRaises(mail_grants.GrantError):
            mail_grants.get(self.config, 'google', 'sam@acme.test')

    def test_a_key_kept_by_the_hosting_that_has_not_arrived_is_waited_for_not_replaced(self):
        key = message_seal.key_path(self.config['database'])
        os.symlink(Path(self.temp.name) / 'run-key', key)
        with self.assertRaises(mail_grants.GrantError):
            mail_grants.put(self.config, 'google', 'alex@acme.test', 'secret')
        self.assertFalse(key.exists())

    def test_google_sends_the_kept_sign_in_and_without_one_asks_as_before(self):
        account = {'id': 'g', 'service': 'gmail', 'transport': 'broker', 'email': 'alex@acme.test'}
        google_api.token(self.config, self.credential, account, 'https://www.googleapis.com/auth/gmail.readonly')
        self.assertNotIn('refresh_token', self.fake.calls[-1][1])
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'kept')
        google_api.token(self.config, self.credential, account, 'https://www.googleapis.com/auth/gmail.readonly', fresh=True)
        self.assertEqual(self.fake.calls[-1][1]['refresh_token'], 'kept')
        google_api.scopes(self.config, self.credential, account)
        self.assertNotIn('refresh_token', self.fake.calls[-1][1])  # asking what was granted needs no sign-in

    def test_microsoft_keeps_the_rotated_sign_in(self):
        account = {'id': 'o', 'service': 'outlook', 'transport': 'broker', 'email': 'sam@contoso.test'}
        mail_grants.put(self.config, 'microsoft', 'sam@contoso.test', 'first')
        self.fake.rotate = 'second'
        ms_graph.token(self.config, self.credential, account, 'Mail.Read')
        self.assertEqual(self.fake.calls[-1][1]['refresh_token'], 'first')
        self.assertEqual(mail_grants.get(self.config, 'microsoft', 'sam@contoso.test'), 'second')

    def test_forgetting_revokes_google_at_google_and_microsoft_only_here(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'g-secret')
        mail_grants.put(self.config, 'microsoft', 'sam@contoso.test', 'm-secret')
        self.assertEqual(mail_grants.forget(self.config, 'google', 'alex@acme.test'), 'revoked')
        self.assertEqual(self.fake.calls, [('revoke', 'token=g-secret')])
        self.assertEqual(mail_grants.forget(self.config, 'microsoft', 'sam@contoso.test'), 'forgotten')
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(mail_grants.held(self.config), [])
        self.assertIsNone(mail_grants.forget(self.config, 'google', 'alex@acme.test'))

    def test_google_unreachable_still_forgets_and_says_so(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'g-secret')
        self.fake.revoke_status = 503
        self.assertEqual(mail_grants.forget(self.config, 'google', 'alex@acme.test'), 'not_revoked')
        self.assertIsNone(mail_grants.get(self.config, 'google', 'alex@acme.test'))

    def test_a_revoke_google_did_not_confirm_stays_pending_and_is_tried_again_until_it_is(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'g-secret')
        mail_grants.put(self.config, 'microsoft', 'sam@contoso.test', 'm-secret')
        self.fake.revoke_status = 503
        self.assertEqual(mail_grants.forget(self.config, 'google', 'alex@acme.test'), 'not_revoked')
        mail_grants.forget(self.config, 'microsoft', 'sam@contoso.test')
        self.assertEqual(mail_grants.held(self.config), [])  # never used again for anything but the revoke
        self.assertNotIn('g-secret', mail_grants.path(self.config).read_text())
        self.assertEqual([(r['provider'], r['email'], r['state']) for r in mail_grants.receipts(self.config)],
                         [('microsoft', 'sam@contoso.test', 'removed'), ('google', 'alex@acme.test', 'pending')])
        self.assertNotIn('secret', mail_grants._receipts_path(self.config).read_text())
        self.assertEqual(mail_grants.retry(self.config, due_only=True), 1)  # tried a moment ago: not yet
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(mail_grants.retry(self.config), 1)  # Google still away
        self.fake.revoke_status = 200
        self.assertEqual(mail_grants.forget(self.config, 'google', 'alex@acme.test'), 'revoked')  # the control plane asks again
        self.assertEqual(self.fake.calls[-1], ('revoke', 'token=g-secret'))
        self.assertEqual(mail_grants.retry(self.config), 0)
        self.assertEqual({r['email']: r['state'] for r in mail_grants.receipts(self.config)},
                         {'alex@acme.test': 'removed', 'sam@contoso.test': 'removed'})
        self.assertEqual(mail_grants._read(self.config), {})
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'new-secret')  # connected again: no receipt
        self.assertEqual([r['email'] for r in mail_grants.receipts(self.config)], ['sam@contoso.test'])

    def test_internal_cleanup_leaves_no_receipt(self):
        mail_grants.put(self.config, 'google', 'alex@acme.test', 'g-secret')
        mail_grants.forget(self.config, 'google', 'alex@acme.test', revoke=False)
        self.assertEqual(mail_grants.receipts(self.config), [])

    def test_no_database_in_the_config_keeps_nothing(self):
        self.assertIsNone(mail_grants.get({}, 'google', 'alex@acme.test'))
        with self.assertRaises(mail_grants.GrantError):
            mail_grants.put({}, 'google', 'alex@acme.test', 'secret')


if __name__ == '__main__':
    unittest.main()
