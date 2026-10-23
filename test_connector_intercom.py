"""Intercom connector: read-only routes, exact workspace and admin checks, frozen search windows. No live calls."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

from connectors import intercom
from test_connectors import a, Response

TOKEN = 'fixture-intercom-token'
ACCOUNT = {**intercom.SETUP['account'], 'workspace_id': 'abc123', 'admin_id': '42', 'region': 'us'}
ME = {'type': 'admin', 'id': '42', 'email': 'Owner@example.com', 'app': {'id_code': 'abc123', 'type': 'app', 'name': '  Example   Support '}}


def conversation(cid='1', body='<p>Hello <b>world</b></p><script>secret()</script>', updated=100):
    return {'type': 'conversation', 'id': cid, 'updated_at': updated, 'created_at': 50, 'title': 'Export question',
            'source': {'body': body, 'author': {'email': 'customer@example.com', 'type': 'user'}},
            'conversation_parts': {'conversation_parts': [{'id': 'p1', 'created_at': 90, 'body': '<p>Latest reply</p>',
                                                           'author': {'type': 'admin', 'name': 'Sam'}, 'part_type': 'comment'}],
                                   'total_count': 1}}


class IntercomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.credential = Mock(return_value=TOKEN + '\n')
        self.helpers = types.SimpleNamespace(SourceError=a.SourceError, iso=a.CONNECTOR_HELPERS.iso, credential=self.credential)
        self.me = copy.deepcopy(ME)
        self.hosts = {'api.intercom.io'}  # where the token is known
        self.pages = [{'type': 'conversation.list', 'conversations': [{'id': '1'}], 'total_count': 1,
                       'pages': {'type': 'pages', 'page': 1, 'total_pages': 1}}]
        self.details = {'1': conversation()}
        self.requests = []
        patch('urllib.request.OpenerDirector.open', side_effect=self.respond).start()
        self.addCleanup(patch.stopall)

    def respond(self, request, timeout):
        host, path = request.host, request.selector
        self.requests.append((request.get_method(), host, path, json.loads(request.data) if request.data else None))
        self.assertEqual(timeout, 30)
        self.assertTrue(request.full_url.startswith('https://'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(request.get_header('Intercom-version'), '2.11')
        self.assertNotIn(TOKEN, request.full_url)
        if host not in self.hosts:
            raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized ' + TOKEN, {}, None)
        if path == '/me':
            return Response(json.dumps(self.me).encode())
        if path == '/conversations/search':
            self.assertEqual(request.get_method(), 'POST')
            page = self.pages.pop(0)
            if isinstance(page, Exception):
                raise page
            return Response(json.dumps(page).encode())
        cid = path.rsplit('/', 1)[1]
        detail = self.details[cid]
        if isinstance(detail, Exception):
            raise detail
        return Response(json.dumps(detail).encode())

    def sync(self, account=ACCOUNT):
        return intercom.sync(self.store, {}, account, self.helpers)

    def rows(self):
        return self.store.events([ACCOUNT['id']])

    def test_it_is_a_connector_with_setup(self):
        self.assertIs(a.load_connector('intercom'), a.load_connector('intercom'))
        self.assertEqual(a.source_kind('intercom.conversation'), 'Intercom')
        setup = intercom.SETUP
        self.assertEqual((setup['label'], setup['secret'], setup.get('ask')), ('Intercom', 'Intercom access token', None))
        self.assertIn('Developer Hub', setup['note'])
        self.assertEqual(setup['note'].count('. '), 0)  # one line
        for text in (setup['what'], setup['note'], *setup['hints'].values()):
            self.assertNotIn('—', text)

    def test_only_read_routes_are_admitted_before_the_token_is_read(self):
        source = intercom.Source({}, ACCOUNT, self.helpers)
        for method, path in (('POST', '/conversations/1/reply'), ('POST', '/conversations/1/parts'), ('PUT', '/conversations/1'),
                             ('POST', '/conversations/1/tags'), ('DELETE', '/conversations/1'), ('POST', '/messages'),
                             ('GET', '/conversations/1?token=x'), ('GET', '/contacts'), ('POST', '/me'), ('GET', '/conversations/search'),
                             ('GET', '/conversations/../admins')):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(a.SourceError, '^unsupported_intercom_method$'):
                source.request(method, path, {})
        self.credential.assert_not_called()
        self.assertEqual(self.requests, [])

    def test_connect_reads_the_workspace_from_the_token_and_keeps_it(self):
        account = dict(intercom.SETUP['account'])
        self.assertIsNone(intercom.check({}, account, self.helpers))
        self.assertEqual((account['workspace_id'], account['workspace_name'], account['admin_id'], account['region'], account['email']),
                         ('abc123', 'Example Support', '42', 'us', 'owner@example.com'))
        self.assertEqual([(m, p) for m, _, p, _ in self.requests], [('GET', '/me')])
        self.assertIsNone(self.store.checkpoint(account['id']))
        intercom.check({}, account, self.helpers)  # connecting again with a token of the same workspace
        self.assertEqual(account['workspace_id'], 'abc123')
        # an account keeps its workspace: a token from another one is refused, nothing changes
        other = {**intercom.SETUP['account'], 'workspace_id': 'other1', 'workspace_name': 'Other'}
        with self.assertRaisesRegex(a.SourceError, '^account_identity_mismatch$'):
            intercom.check({}, other, self.helpers)
        self.assertEqual((other['workspace_id'], other['workspace_name']), ('other1', 'Other'))
        self.assertNotIn('admin_id', other)
        for broken in ({'app': None}, {'app': {'id_code': '../x'}}, {'id': 'x'}):
            with self.subTest(broken=broken):
                self.me = {**copy.deepcopy(ME), **broken}
                with self.assertRaisesRegex(a.SourceError, '^intercom_invalid_profile$'):
                    intercom.check({}, dict(intercom.SETUP['account']), self.helpers)
        self.me = {**copy.deepcopy(ME), 'app': {'id_code': 'abc123'}}
        unnamed = dict(intercom.SETUP['account'])
        intercom.check({}, unnamed, self.helpers)
        self.assertEqual(unnamed['workspace_name'], 'abc123')  # no name: its ID

    def test_connect_finds_an_eu_workspace(self):
        self.hosts = {'api.eu.intercom.io'}
        account = dict(intercom.SETUP['account'])
        intercom.check({}, account, self.helpers)
        self.assertEqual(account['region'], 'eu')
        self.assertEqual([h for _, h, _, _ in self.requests], ['api.intercom.io', 'api.eu.intercom.io'])
        self.sync(account)
        self.assertTrue(all(h == 'api.eu.intercom.io' for _, h, _, _ in self.requests[2:]))
        self.assertTrue(self.rows()[0]['metadata']['source_url'].startswith('https://app.eu.intercom.com/a/inbox/abc123/'))

    def test_a_rejected_token_says_so_without_the_token(self):
        self.hosts = set()
        with self.assertRaises(a.SourceError) as caught:
            intercom.check({}, dict(intercom.SETUP['account']), self.helpers)
        self.assertEqual(str(caught.exception), 'intercom:HTTP_401')
        self.assertNotIn(TOKEN, repr(caught.exception.__dict__))
        self.assertIn('intercom:HTTP_401', intercom.SETUP['hints'])

    def test_every_sync_checks_workspace_and_admin_exactly(self):
        for change in ({'id': '43'}, {'app': {'id_code': 'other1'}}, {'app': None}):
            with self.subTest(change=change):
                self.me = {**copy.deepcopy(ME), **change}
                self.requests.clear()
                with self.assertRaisesRegex(a.SourceError, '^account_identity_mismatch$'):
                    self.sync()
                self.assertEqual([p for _, _, p, _ in self.requests], ['/me'])
        self.me = copy.deepcopy(ME)
        with self.assertRaisesRegex(a.SourceError, '^account_identity_mismatch$'):
            self.sync({k: v for k, v in ACCOUNT.items() if k != 'admin_id'})
        self.assertEqual(self.rows(), [])

    def test_conversations_are_stored_as_plain_text_once(self):
        self.assertEqual(self.sync()['events'], 1)
        [row] = self.rows()
        self.assertEqual((row['kind'], row['title'], row['source_id']), ('intercom.conversation', 'Export question', '1'))
        self.assertIn('Hello world', row['body'])
        self.assertIn('Sam · comment: Latest reply', row['body'])
        self.assertNotIn('secret()', row['body'])
        self.assertEqual(row['metadata']['from'], 'customer@example.com')
        self.assertEqual(row['metadata']['source_url'], 'https://app.intercom.com/a/inbox/abc123/inbox/shared/all/conversation/1')
        search = next(body for m, _, p, body in self.requests if p == '/conversations/search')
        self.assertEqual(search['sort'], {'field': 'updated_at', 'order': 'ascending'})
        checkpoint = self.store.checkpoint(ACCOUNT['id'])
        self.assertNotIn('window', checkpoint)
        self.assertTrue(checkpoint['coverage_complete'])
        self.pages = [{'conversations': [{'id': '1'}], 'total_count': 1, 'pages': {'page': 1, 'total_pages': 1}}]
        self.sync()
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT count(*),max(version) FROM events').fetchone()), (1, 1))

    def test_long_conversations_keep_the_opening_and_the_latest_and_say_so(self):
        raw = conversation()
        raw['conversation_parts']['total_count'] = 501
        raw['conversation_parts']['conversation_parts'][0]['body'] = 'old' + 'x' * 45000 + 'LATEST'
        row = intercom.event(ACCOUNT, raw, self.helpers)
        self.assertTrue(row['metadata']['truncated'])
        self.assertTrue(row['body'].startswith('Hello world'))
        self.assertTrue(row['body'].endswith('LATEST'))
        self.assertLessEqual(len(row['body']), 40000)
        raw['source'], raw['title'] = None, None
        self.assertEqual(intercom.event(ACCOUNT, raw, self.helpers)['title'], 'Support conversation')

    def test_cursor_commits_with_its_page_and_expired_cursor_replays_the_window(self):
        self.pages = [{'conversations': [{'id': '1'}], 'total_count': 2,
                       'pages': {'page': 1, 'total_pages': 2, 'next': {'starting_after': 'page2'}}}]
        self.assertTrue(self.sync()['more'])
        first = self.store.checkpoint(ACCOUNT['id'])
        self.assertEqual(first['window']['cursor'], 'page2')
        self.assertNotIn('watermark', first)
        self.assertEqual(len(self.rows()), 1)
        self.pages = [urllib.error.HTTPError('unused', 400, 'expired', {}, None)]
        with self.assertRaisesRegex(a.SourceError, '^intercom:HTTP_400$'):
            self.sync()
        after = self.store.checkpoint(ACCOUNT['id'])
        self.assertEqual(after['window']['before'], first['window']['before'])
        self.assertNotIn('cursor', after['window'])
        self.assertNotIn('watermark', after)

    def test_a_failed_detail_commits_nothing(self):
        before = {'mode': 'incremental', 'watermark': 100, 'next_full': 9999999999}
        self.store.commit_page(ACCOUNT['id'], before, [], record_sync=False)
        self.details['1'] = urllib.error.HTTPError('unused', 503, 'down', {'Retry-After': '30'}, None)
        with self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual((str(caught.exception), caught.exception.retry_after), ('intercom:HTTP_503', 30))
        self.assertEqual(self.store.checkpoint(ACCOUNT['id']), before)
        self.assertEqual(self.rows(), [])

    def test_a_repeated_or_strange_cursor_drops_the_window_without_advancing(self):
        for cursor in ('repeated', '../../admins'):
            with self.subTest(cursor=cursor):
                self.store.commit_page(ACCOUNT['id'], {}, [], record_sync=False)
                page = {'conversations': [{'id': '1'}], 'total_count': 2,
                        'pages': {'page': 1, 'total_pages': 2, 'next': {'starting_after': cursor}}}
                self.pages = [copy.deepcopy(page), copy.deepcopy(page)]
                if cursor == 'repeated':
                    self.sync()
                with self.assertRaisesRegex(a.SourceError, '^intercom_invalid_cursor$'):
                    self.sync()
                after = self.store.checkpoint(ACCOUNT['id'])
                self.assertNotIn('window', after)
                self.assertNotIn('watermark', after)

    def test_malformed_answers_never_commit(self):
        for page, label in (({'conversations': {}, 'pages': {}}, 'intercom_invalid_page'),
                            ({'conversations': [{'id': '../me'}], 'pages': {}}, 'intercom_invalid_conversation'),
                            ({'type': 'error.list', 'errors': []}, 'intercom_invalid_response')):
            with self.subTest(label=label):
                self.pages = [page]
                with self.assertRaisesRegex(a.SourceError, '^' + label + '$'):
                    self.sync()
                self.assertIsNone(self.store.checkpoint(ACCOUNT['id']))
        self.pages = [{'conversations': [{'id': '1'}], 'pages': {}}]
        self.details['1'] = {**conversation(), 'id': '2'}
        with self.assertRaisesRegex(a.SourceError, '^intercom_conversation_identity_mismatch$'):
            self.sync()
        self.assertEqual(self.rows(), [])

    def test_a_token_in_an_env_file_works_too(self):
        self.credential.return_value = '# saved\nINTERCOM_ACCESS_TOKEN="' + TOKEN + '"\nOTHER=1\n'
        self.sync({**ACCOUNT, 'credential_variable': 'INTERCOM_ACCESS_TOKEN'})
        self.assertEqual(len(self.rows()), 1)
        self.credential.return_value = ''
        with self.assertRaisesRegex(a.SourceError, '^missing_intercom_credential$'):
            self.sync()

    def test_connect_through_onboarding_saves_the_checked_account(self):
        import onboarding
        folder = Path(self.temp.name)
        config_path = folder / 'config.json'
        config_path.write_text(json.dumps({'database': str(folder / 'db.sqlite'), 'accounts': [], 'credentials': {}}))

        class UI(onboarding.UI):
            interactive, explains = True, False
            def __init__(self):
                self.said, self.asked = [], []
            def say(self, text=''):
                self.said.append(str(text))
            def ask(self, prompt, default=''):
                self.asked.append(prompt)
                return default
            def secret(self, prompt):
                return TOKEN if prompt.startswith('Intercom access token') else ''
            def yes(self, prompt, default=True):
                return default
            def choose(self, prompt, options):
                self.asked.append(prompt)
                return options[0][0]

        ui = UI()
        real = a.CONNECTOR_HELPERS.credential
        with patch.object(onboarding, 'assistant', return_value=a):
            account = onboarding.connect(config_path, 'intercom', ui, restart=False)
        self.assertEqual((account['admin_id'], account['region'], account['workspace_id'], account['workspace_name']),
                         ('42', 'us', 'abc123', 'Example Support'))
        saved = json.loads(config_path.read_text())
        self.assertEqual(saved['accounts'][0]['admin_id'], '42')
        # Always work: no Work or personal question, the company is the workspace (renamed later as any account)
        self.assertEqual(ui.asked, [])
        self.assertEqual(saved['accounts'][0]['boundary'], 'work:Example Support')
        self.assertNotIn(TOKEN, config_path.read_text())
        self.assertNotIn(TOKEN, '\n'.join(ui.said))
        token_file = Path(saved['credentials'][account['credential']])
        self.assertEqual(token_file.read_text().strip(), TOKEN)
        self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
        self.assertIs(a.CONNECTOR_HELPERS.credential, real)


if __name__ == '__main__':
    unittest.main()
