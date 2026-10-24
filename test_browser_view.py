"""A ready card whose run worked in the Machine's browser links to that page there (View in browser), from the run's own
recorded browser steps; the link never carries the page's address or a token. A final click left to the owner says so
on the card. Offline: no browser, no Telegram, no site is contacted."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('view_assistant', Path(__file__).with_name('assistant.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

TAB = 'A0F20ED451EA2A1247613CAB889F3BCB'
PAGE = 'https://portal.example.org/campaigns/a05/projects/a0F/Reports/2d05'


def answer(**found):
    text = '\n<<<EXTERNAL_UNTRUSTED_CONTENT id="x">>>\nSource: Browser\n---\n' + json.dumps(found) + '\n<<<END_EXTERNAL_UNTRUSTED_CONTENT id="x">>>'
    return json.dumps({'content': [{'type': 'text', 'text': text}]})


OPENED = answer(targetId=TAB, url='https://portal.example.org/login', wsUrl=f'ws://127.0.0.1:18800/devtools/page/{TAB}', label='report')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = a.Store(Path(self.temp.name) / 'events.sqlite')
        self.store.commit_page('work', {}, [{'account_id': 'work', 'source_id': 'one', 'kind': 'gmail.message', 'occurred_at': a.now(),
                                             'title': 'Evidence', 'body': 'Your quarterly report is waiting', 'metadata': {}, 'deleted': False}])
        self.sid = self.store.suggest('task', 'Get the quarterly report ready', 'I can fill it in.', [1])['id']
        self.app = object.__new__(a.App)
        self.app.store = self.store
        self.app.config = {'accounts': [], 'database': str(Path(self.temp.name) / 'events.sqlite'), 'dashboard_host': 'machine.example',
                           'browser_cdp_url': 'http://127.0.0.1:18800', 'browser_url': 'https://app.example/open?to=browser'}
        self.store.config = self.app.config
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET status='sent',message_id='100' WHERE suggestion_id=?", (self.sid,))
        self.store.decide(self.sid, 'accept', '', 'click', 'telegram')
        self.app.preparation_step(lambda req: {'status': 'admitted'})
        # The Machine's browser, as DevTools lists it: the run's tab is still open unless a test says otherwise
        self.tabs = [{'id': TAB, 'type': 'page', 'url': PAGE}]
        def listed(base, path, **kw):
            if self.tabs is None:
                raise OSError('connection refused')
            assert (base, path) == ('http://127.0.0.1:18800', '/json/list')
            return self.tabs
        patcher = patch.object(a.browser_login.browser_cdp, '_http', side_effect=listed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self):
        with self.store.connect() as db:
            return db.execute('SELECT job_id FROM preparations WHERE suggestion_id=?', (self.sid,)).fetchone()[0]

    def steps(self, *calls):
        with self.store.connect() as db:
            for i, (tool, params, result) in enumerate(calls):
                db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,result,started_at) VALUES(?,?,?,?,?,?)',
                           (self.job(), str(i), tool, json.dumps(params), result,
                            a.dt.datetime(2026, 10, 22, 8, 11, i, tzinfo=a.dt.timezone.utc).isoformat()))

    def fill_in(self):
        # The run as it goes: opens the portal, types and clicks in that tab (the last answer says where the tab is now).
        self.steps(('browser', {'action': 'open', 'url': 'https://portal.example.org/login'}, OPENED),
                   ('browser', {'action': 'act', 'targetId': 'report', 'kind': 'type'}, answer(ok=True, targetId=TAB, url=PAGE + '?session=s3cr3t&tab=2')),
                   ('browser', {'action': 'snapshot', 'targetId': 'report'}, None),
                   ('exec', {'command': 'python3 browser-lease.py release'}, None))

    def save(self, next_actions=None, **outcome):
        self.app.action('prepare', {'suggestion_id': self.sid, 'job_id': self.job(), 'body': 'Filled in the report; it is not submitted.',
                                    'next_actions': next_actions, 'note_kind': None, **outcome})

    def row(self):
        self.store.__dict__.pop('tab_look', None)  # a fresh look at the tabs
        with self.store.connect() as db:
            row = dict(db.execute(a.EDIT_ROWS + ' AND s.id=?', (self.sid,)).fetchone())
            row.update(self.store.suggestion_result(row, db))
        return row


class BrowserView(Fixture):
    def test_a_page_the_run_worked_on_gets_view_in_browser(self):
        self.fill_in()
        self.save(outcome='your_step', step='submit')
        row = self.row()
        link = 'https://machine.example/#browser-for=' + self.sid
        self.assertEqual(row['browser_view'], {'label': 'View in browser', 'site': 'portal.example.org'})
        self.assertEqual(row['next_actions'], [{'kind': 'open', 'label': 'View in browser', 'url': link, 'browser': True, 'site': 'portal.example.org', 'revision': 'browser-view'}])
        # Telegram gets the same: the dashboard's address, which opens the page in the Machine's browser
        buttons = [b for block in a.controls(row)['blocks'] for b in block['buttons']]
        self.assertIn({'label': 'View in browser', 'action': {'type': 'url', 'url': link}}, buttons)
        # The card never carries the page's address or its session
        self.assertNotIn('s3cr3t', json.dumps(row))
        self.assertNotIn('portal.example.org/campaigns', json.dumps(row))
        # The status line says the final click is theirs, never Finished
        self.assertEqual(self.status(row), 'Ready for you to check and submit.')

    def status(self, row):
        return next(line for line in a.card(row).split('\n') if line.startswith(('Ready', 'Finished', 'Needs')))

    def test_a_tab_the_run_closed_gets_no_view_in_browser(self):
        # The run read and clicked on the page, then closed its own tab: nothing to view there any more
        self.steps(('browser', {'action': 'open', 'url': 'https://portal.example.org/login'}, OPENED),
                   ('browser', {'action': 'act', 'targetId': 'report', 'kind': 'click'}, answer(ok=True, targetId=TAB, url=PAGE)),
                   ('browser', {'action': 'close', 'targetId': 'report'}, None))
        self.save([{'kind': 'open', 'label': 'Open draft report', 'url': PAGE}], outcome='done')
        self.tabs = []
        row = self.row()
        self.assertNotIn('browser_view', row)
        self.assertNotIn('browser_open', row)
        (link,) = row['next_actions']
        self.assertEqual((link['url'], link.get('browser')), (PAGE, None))  # a plain link again
        self.assertNotIn('View in browser', json.dumps(a.controls(row)))

    def test_view_in_browser_goes_when_the_tab_does(self):
        self.fill_in()
        self.save(outcome='your_step', step='submit')
        self.assertEqual(self.row()['browser_view']['label'], 'View in browser')
        self.assertTrue(self.row()['browser_open'])
        self.tabs = [{'id': 'OTHER', 'type': 'page', 'url': 'https://portal.example.org/home'}]  # closed by hand, or the browser restarted
        self.assertNotIn('browser_view', self.row())
        self.tabs = None  # the browser can't be reached
        self.assertNotIn('browser_view', self.row())
        self.tabs = [{'id': 'NEW', 'type': 'page', 'url': PAGE + '?session=s3cr3t&tab=2'}]  # the same page, open again
        self.assertEqual(self.row()['browser_view']['label'], 'View in browser')

    def test_the_tabs_are_read_once_for_all_cards(self):
        self.fill_in()
        self.save(outcome='your_step', step='submit')
        self.row()
        with self.store.connect() as db:
            row = dict(db.execute(a.EDIT_ROWS + ' AND s.id=?', (self.sid,)).fetchone())
            for _ in range(3):
                self.store.suggestion_result(row, db)
        self.assertEqual(a.browser_login.browser_cdp._http.call_count, 1)

    def test_task_page_follows_the_tab_the_run_used(self):
        self.fill_in()
        with self.store.connect() as db:
            page = a.task_page(db, self.sid)
        self.assertEqual(page, {'url': PAGE + '?session=s3cr3t&tab=2', 'target': TAB, 'port': 18800, 'title': None, 'worked': True})

    def test_only_reading_pages_gets_no_link_of_its_own(self):
        self.steps(('browser', {'action': 'open', 'url': 'https://news.example.com/story'}, OPENED.replace('portal.example.org/login', 'news.example.com/story')),
                   ('browser', {'action': 'snapshot'}, None))
        self.save(outcome='done')
        row = self.row()
        self.assertNotIn('browser_view', row)
        self.assertNotIn('next_actions', row)
        self.assertEqual(self.status(row), 'Finished. Mark it done if it looks right.')

    def test_the_runs_own_link_to_that_site_opens_in_the_machine_browser(self):
        self.fill_in()
        self.save([{'kind': 'open', 'label': 'Open draft report', 'url': PAGE},
                   {'kind': 'open', 'label': 'Open guidelines', 'url': 'https://docs.example.net/guide?utm_source=x&token=abc'}], outcome='done')
        row = self.row()
        mine, other = row['next_actions']
        self.assertEqual((mine['url'], mine['browser'], mine['label']), ('https://machine.example/#browser-for=' + self.sid, True, 'Open draft report'))
        self.assertEqual(other['url'], 'https://docs.example.net/guide?utm_source=x')  # a plain link, without its token
        self.assertNotIn('browser', other)
        self.assertNotIn('browser_view', row)  # said once
        self.assertEqual(self.status(row), 'Finished. Mark it done if it looks right.')

    def test_without_a_dashboard_address_the_dashboard_still_shows_it(self):
        self.app.config.pop('dashboard_host')
        self.fill_in()
        self.save(outcome='your_step', step='pay')
        row = self.row()
        self.assertEqual(row['browser_view']['label'], 'View in browser')
        self.assertNotIn('next_actions', row)  # Telegram has no address to send
        self.assertEqual(self.status(row), 'Ready for you to check and pay.')

    def test_your_step_needs_a_known_final_click(self):
        with self.assertRaisesRegex(ValueError, 'step'):
            self.save(outcome='your_step', step='delete everything')

    def show(self, tabs):
        with patch.object(a.App, 'signin_tabs', return_value=tabs), \
                patch.object(a.browser_login.browser_cdp, 'open_tab', return_value='NEWTAB') as open_tab, \
                patch.object(a.browser_viewer, 'focus', return_value='https://x') as focus:
            shown = self.app.action('browser_show', {'suggestion_id': self.sid}, owner_dashboard=True)
        return shown, open_tab, focus

    def test_view_in_browser_brings_the_tasks_own_tab_to_the_front(self):
        self.fill_in()
        self.save(outcome='your_step', step='submit')
        shown, open_tab, focus = self.show([{'id': 'OTHER', 'url': 'https://portal.example.org/other'}, {'id': TAB, 'url': PAGE + '?x=1'}])
        self.assertEqual(shown, {'shown': True, 'opened': False, 'site': 'portal.example.org', 'view': 'https://app.example/open?to=browser'})
        open_tab.assert_not_called()
        self.assertEqual(focus.call_args.kwargs['target'], TAB)

    def test_a_closed_tab_is_reopened_in_the_machine_browser(self):
        self.fill_in()
        self.save(outcome='your_step', step='submit')
        shown, open_tab, focus = self.show([{'id': 'OTHER', 'url': 'https://portal.example.org/other'}])
        self.assertTrue(shown['shown'] and shown['opened'])
        open_tab.assert_called_once_with('http://127.0.0.1:18800', PAGE + '?session=s3cr3t&tab=2')
        self.assertEqual(focus.call_args.kwargs['target'], 'NEWTAB')

    def test_only_the_owner_and_only_a_task_that_used_the_browser(self):
        with self.assertRaises(ValueError):
            self.app.action('browser_show', {'suggestion_id': self.sid})
        shown, open_tab, focus = self.show([])
        self.assertEqual(shown, {'shown': False, 'unknown': True, 'view': 'https://app.example/open?to=browser'})
        open_tab.assert_not_called()


class OpenTheBrowser(Fixture):
    """A preparing card's Open the browser: the task's own page in front, opened again when its tab is gone."""

    def front(self, tabs):
        self.tabs = [{**t, 'type': 'page'} for t in tabs]
        with patch.object(a.browser_login.browser_cdp, 'open_tab', return_value='NEWTAB') as open_tab, \
                patch.object(a.browser_viewer, 'focus', return_value='https://x') as focus:
            shown = self.app.action('browser_focus', {'suggestion_id': self.sid}, owner_dashboard=True)
        return shown, open_tab, focus

    def setUp(self):
        super().setUp()
        self.fill_in()
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='running' WHERE suggestion_id=?", (self.sid,))

    def test_the_tab_still_open_comes_to_the_front(self):
        shown, open_tab, focus = self.front([{'id': TAB, 'url': PAGE}])
        self.assertEqual(shown, {'shown': True, 'opened': False})
        open_tab.assert_not_called()
        self.assertEqual(focus.call_args[0][0], PAGE + '?session=s3cr3t&tab=2')

    def test_a_closed_tab_opens_the_page_again(self):
        shown, open_tab, focus = self.front([{'id': 'OTHER', 'url': 'https://news.example.com/'}])
        self.assertEqual(shown, {'shown': True, 'opened': True})
        open_tab.assert_called_once_with('http://127.0.0.1:18800', PAGE + '?session=s3cr3t&tab=2')
        self.assertEqual(focus.call_args.kwargs['target'], 'NEWTAB')

    def test_a_finished_run_has_nothing_to_open(self):
        with self.store.connect() as db:
            db.execute("UPDATE preparations SET status='done' WHERE suggestion_id=?", (self.sid,))
        shown, open_tab, focus = self.front([])
        self.assertEqual(shown, {'shown': False, 'unknown': True})
        open_tab.assert_not_called()


class SafeLink(unittest.TestCase):
    def test_tokens_and_sessions_are_dropped(self):
        self.assertEqual(a.safe_link('https://user:pw@example.com/a?id=7&access_token=x&sessionid=y&sig=z&code=1#t=2'), 'https://example.com/a?id=7')
        self.assertEqual(a.safe_link('https://example.com/a#section'), 'https://example.com/a#section')
        self.assertEqual(a.safe_link('http://example.com:8080/a?X-Amz-Signature=1&page=2'), 'http://example.com:8080/a?page=2')
        for bad in ('javascript:alert(1)', 'file:///etc/passwd', 'ftp://example.com/', '', None):
            self.assertIsNone(a.safe_link(bad))


if __name__ == '__main__':
    unittest.main()
