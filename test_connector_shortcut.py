"""Shortcut tests: all HTTP and credential reads are canned; no live service calls."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

from connectors import shortcut
from test_connectors import a, Response

NOW = '2026-10-20T12:00:00+00:00'
ACCOUNT = {**shortcut.SETUP['account'], 'workspace': 'example-workspace'}
MEMBER = {'id': 'member-fixture', 'workspace2': {'id': 'workspace-fixture', 'url_slug': 'example-workspace'}}
NEXT = '/api/v3/search/stories?query=moved%3A2026-10-13..%2A&next=fixture-page-2'


def story(number=1):
    return {'id': number, 'name': 'Example task', 'description': '<p>Check the release</p>',
            'workflow_state_id': 30, 'owner_ids': ['member-fixture'], 'requested_by_id': 'requester-fixture',
            'deadline': '2026-10-23T12:00:00Z', 'blocked': True, 'completed': False}


def history(number=1, old=10, new=20, date='2026-10-18T12:00:00Z'):
    return {'id': f'history-fixture-{number}', 'changed_at': date, 'member_id': 'member-fixture',
            'actor_name': 'Example member', 'actions': [
                {'id': 1, 'entity_type': 'story', 'action': 'update', 'name': 'Example task',
                 'changes': {'workflow_state_id': {'old': old, 'new': new}}}],
            'references': [{'id': 10, 'entity_type': 'workflow-state', 'name': 'Ready'},
                           {'id': 20, 'entity_type': 'workflow-state', 'name': 'Review'},
                           {'id': 30, 'entity_type': 'workflow-state', 'name': 'Done'}]}


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.credential = Mock(return_value='fixture-token')
        self.helpers = types.SimpleNamespace(SourceError=a.SourceError, now=lambda: NOW,
                                             iso=a.CONNECTOR_HELPERS.iso, credential=self.credential)
        self.member = copy.deepcopy(MEMBER)
        self.pages = [{'data': [story()], 'next': None, 'total': 1}]
        self.histories = {1: [history()]}
        self.requests = []
        self.http = patch('urllib.request.OpenerDirector.open', side_effect=self.respond).start()
        self.addCleanup(patch.stopall)

    def respond(self, request, timeout):
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 30)
        parts = urllib.parse.urlsplit(request.full_url)
        self.assertEqual(parts.scheme, 'https')
        self.assertEqual(parts.netloc, 'api.app.shortcut.com')
        self.assertEqual(request.get_header('Shortcut-token'), 'fixture-token')
        self.assertNotIn('fixture-token', request.full_url)
        self.requests.append((parts.path, urllib.parse.parse_qs(parts.query)))
        if parts.path == '/api/v3/member':
            result = self.member
        elif parts.path == '/api/v3/search/stories':
            result = self.pages.pop(0)
        else:
            self.assertRegex(parts.path, r'^/api/v3/stories/\d+/history$')
            result = self.histories[int(parts.path.split('/')[4])]
        if isinstance(result, Exception):
            raise result
        return Response(result if isinstance(result, bytes) else json.dumps(result).encode())

    def sync(self):
        return shortcut.sync(self.store, {}, ACCOUNT, self.helpers)

    def rows(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events ORDER BY occurred_at,source_id')]

    def test_moves_have_before_after_and_action_context(self):
        noise = history(3)
        noise['actions'][0]['changes'] = {'name': {'old': 'Before', 'new': 'After'}}
        same = history(4, old=20, new=20)
        self.histories[1] = [history(2, old=20, new=30, date='2026-10-19T12:00:00Z'), history(), noise, same,
                             history(5, date='2026-10-01T00:00:00Z'), history(6, date='2026-10-21T00:00:00Z')]
        result = self.sync()
        self.assertEqual(result, {'mode': 'incremental', 'events': 2, 'read': 1, 'more': False})
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first['kind'], 'shortcut.move')
        self.assertEqual(first['occurred_at'], '2026-10-18T12:00:00+00:00')
        self.assertEqual(first['title'], 'Example task')
        self.assertIn('Moved: Ready → Review', first['body'])
        self.assertIn('Owner IDs: member-fixture', first['body'])
        self.assertIn('Check the release', first['body'])
        self.assertIn('Deadline: 2026-10-23', first['body'])
        self.assertIn('may be newer', first['body'])
        meta = json.loads(first['metadata'])
        self.assertEqual(meta['source_url'], 'https://app.shortcut.com/example-workspace/story/1')
        self.assertEqual(meta['previous_state_id'], 10)
        self.assertEqual(meta['state_id'], 20)
        self.assertTrue(meta['from_me'])
        self.assertFalse(first['deleted'])
        self.assertEqual(self.requests[1][1]['query'], ['moved:2026-10-13..*'])
        self.credential.assert_called_once_with({}, 'shortcut')

    def test_replay_and_changed_context_use_stable_ids(self):
        self.histories[1] = [history(date='2026-10-19T12:00:00Z')]
        self.sync()
        self.pages = [{'data': [story()], 'next': None, 'total': 1}]
        self.sync()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['version'], 1)
        changed = story()
        changed['description'] = 'Updated context'
        self.pages = [{'data': [changed], 'next': None, 'total': 1}]
        self.sync()
        self.assertEqual(self.rows()[0]['version'], 2)
        self.assertEqual(self.requests[-2][1]['query'], ['moved:2026-10-19..*'])

    def test_checkpoint_page_resume_and_failed_page_are_atomic(self):
        with patch.object(shortcut, 'PAGES', 1):
            self.pages = [{'data': [story()], 'next': NEXT, 'total': 2}]
            self.assertTrue(self.sync()['more'])
            checkpoint = self.store.checkpoint(ACCOUNT['id'])
            self.assertEqual(checkpoint['catchup']['cursor'], NEXT)
            self.assertFalse(checkpoint['coverage_complete'])
            self.pages = [urllib.error.URLError('private error text')]
            with self.assertRaisesRegex(a.SourceError, '^shortcut_network$'):
                self.sync()
            self.assertEqual(self.store.checkpoint(ACCOUNT['id']), checkpoint)
            second = history(2)
            second['actions'][0]['id'] = 2
            self.histories[2] = [second]
            self.pages = [{'data': [story(2)], 'next': None, 'total': 2}]
            self.assertFalse(self.sync()['more'])
            self.assertEqual(self.requests[-2][1]['next'], ['fixture-page-2'])
            self.assertEqual(len(self.rows()), 2)
            self.assertNotIn('catchup', self.store.checkpoint(ACCOUNT['id']))
            self.assertTrue(self.store.checkpoint(ACCOUNT['id'])['coverage_complete'])

    def test_expired_cursor_restarts_bounded_bootstrap_window(self):
        self.store.commit_page(ACCOUNT['id'], {'catchup': {'since': '2020-01-01T00:00:00+00:00',
                                                        'until': NOW, 'cursor': NEXT}}, [])
        self.pages = [urllib.error.HTTPError('unused', 422, 'private', {}, None),
                      {'data': [story()], 'next': None, 'total': 1}]
        self.sync()
        self.assertEqual(self.requests[-2][1]['query'], ['moved:2026-10-13..*'])
        self.assertEqual(len(self.rows()), 1)

    def test_setup_is_read_only_and_checks_workspace_identity(self):
        shortcut.check({}, ACCOUNT, self.helpers)
        self.assertEqual([path for path, _ in self.requests], ['/api/v3/member'])
        self.assertIsNone(self.store.checkpoint(ACCOUNT['id']))
        self.assertEqual(self.rows(), [])
        self.member['workspace2']['url_slug'] = 'other-workspace'
        with self.assertRaisesRegex(a.SourceError, '^account_identity_mismatch$'):
            self.sync()
        self.assertEqual(self.rows(), [])
        self.assertEqual(shortcut.Source({}, {**ACCOUNT, 'workspace': 'https://app.shortcut.com/example-workspace/stories'}, self.helpers).workspace,
                         'example-workspace')

    def test_safe_http_errors_and_retry_after(self):
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                self.pages = [urllib.error.HTTPError('unused', status, 'private token', {'Retry-After': '120'}, None)]
                with self.assertRaises(a.SourceError) as caught:
                    self.sync()
                self.assertEqual(str(caught.exception), f'shortcut_http:HTTP_{status}')
                self.assertEqual(caught.exception.retry_after, 120)
                self.assertIsNone(self.store.checkpoint(ACCOUNT['id']))
        self.pages = [urllib.error.HTTPError('unused', 429, 'private', {}, None)]
        with self.assertRaises(a.SourceError) as caught:
            self.sync()
        self.assertEqual(caught.exception.retry_after, 60)

    def test_malformed_payloads_never_commit(self):
        for response, label in ((b'not json', 'shortcut_invalid_json'), ([], 'shortcut_invalid_page'),
                                ({'data': {}, 'total': 1}, 'shortcut_invalid_page'),
                                ({'data': [], 'total': 1001}, 'shortcut_search_limit'),
                                ({'data': [{'id': '../member'}], 'total': 1}, 'shortcut_invalid_story'),
                                ({'data': [], 'total': 1, 'next': NEXT}, 'shortcut_invalid_cursor')):
            with self.subTest(label=label):
                self.pages = [response]
                with self.assertRaisesRegex(a.SourceError, '^' + label + '$'):
                    self.sync()
                self.assertIsNone(self.store.checkpoint(ACCOUNT['id']))
        for payload, label in (({}, 'shortcut_invalid_history'), ([{}], 'shortcut_invalid_history'),
                               ([history(date='invalid')], 'shortcut_invalid_timestamp')):
            with self.subTest(label=label):
                self.pages = [{'data': [story()], 'total': 1}]
                self.histories[1] = payload
                with self.assertRaisesRegex(a.SourceError, '^' + label + '$'):
                    self.sync()
                self.assertEqual(self.rows(), [])

    def test_cursor_validation_cycles_and_endpoint_allowlist(self):
        for value in ('https://example.invalid/api/v3/search/stories?next=x', '//example.invalid/x',
                      '/api/v3/member?next=x', '/api/v3/search/stories?next=x#fragment', 3):
            with self.subTest(value=value):
                self.pages = [{'data': [story()], 'total': 2, 'next': value}]
                with self.assertRaisesRegex(a.SourceError, '^shortcut_invalid_cursor$'):
                    self.sync()
        source = shortcut.Source({}, ACCOUNT, self.helpers)
        for path in ('https://example.invalid/', '/api/v3/stories/1', '/api/v3/../member', '/api/v3/member#x'):
            with self.subTest(path=path), self.assertRaisesRegex(a.SourceError, '^shortcut_path_not_allowed$'):
                source.get(path)
        self.pages = [{'data': [story()], 'total': 2, 'next': NEXT},
                      {'data': [story()], 'total': 2, 'next': NEXT}]
        with self.assertRaisesRegex(a.SourceError, '^shortcut_invalid_cursor$'):
            self.sync()
        self.assertEqual(len(self.rows()), 1)  # Only the successful first page was committed.
        self.assertFalse(self.store.checkpoint(ACCOUNT['id'])['coverage_complete'])

    def test_credentials_workspace_and_redirect_protection(self):
        for value in ('', 'https://example.invalid/workspace', '../member', 'bad slug'):
            with self.subTest(value=value), self.assertRaisesRegex(a.SourceError, '^shortcut_invalid_workspace$'):
                shortcut.Source({}, {**ACCOUNT, 'workspace': value}, self.helpers)
        for value in ('', 'bad\nheader', 'token with spaces'):
            self.credential.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(a.SourceError, '^shortcut_credential_unavailable$'):
                shortcut.Source({}, ACCOUNT, self.helpers)
        self.assertEqual(self.requests, [])
        self.assertIsNone(shortcut.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid'))

    def test_plain_text_bounds_and_missing_reference_fallback(self):
        item = story()
        item['description'] = 'verification code: 123456\npassword=example\n' + 'x' * 45000
        self.histories[1][0]['actions'][0]['name'] = '<b>' + 'n' * 1200 + '</b>'
        self.histories[1][0]['references'] = []
        self.pages = [{'data': [item], 'total': 1}]
        self.sync()
        row = self.rows()[0]
        self.assertEqual(len(row['title']), 1000)
        self.assertEqual(len(row['body']), 40000)
        self.assertNotIn('<b>', row['title'])
        self.assertNotIn('123456', row['body'])
        self.assertNotIn('password=example', row['body'])
        self.assertIn('Moved: 10 → 20', row['body'])

    def test_empty_window_advances_and_setup_metadata(self):
        self.pages = [{'data': [], 'total': 0}]
        self.assertFalse(self.sync()['more'])
        self.assertEqual(self.store.checkpoint(ACCOUNT['id'])['since'], NOW)
        self.assertEqual(shortcut.SETUP['ask'], [('workspace', 'Shortcut workspace URL or slug')])
        self.assertIn('https://app.shortcut.com/settings/account/api-tokens', shortcut.SETUP['note'])


if __name__ == '__main__':
    unittest.main()
