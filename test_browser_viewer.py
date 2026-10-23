import base64
import datetime as dt
import http.server
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import assistant
import browser_viewer
import preparation_state


def journal(*calls):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE preparations(job_id TEXT,suggestion_id TEXT,status TEXT,started_at TEXT,created_at TEXT)')
    preparation_state.initialize(db)
    for i, (tool, params, ago) in enumerate(calls):
        at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=ago)).isoformat()
        db.execute('INSERT INTO preparation_calls(job_id,call_id,tool,params,started_at) VALUES(?,?,?,?,?)', ('job', str(i), tool, json.dumps(params), at))
    return db


class BrowserWaitTest(unittest.TestCase):
    def test_a_long_browser_step_offers_the_browser_with_its_page(self):
        db = journal(('read', {}, 300), ('browser', {'action': 'navigate', 'url': 'https://www.linkedin.com/login'}, 120), ('browser', {'action': 'snapshot'}, 50))
        waiting = assistant.browser_wait(db, 'job', 'running')
        self.assertEqual(waiting['page'], 'https://www.linkedin.com/login')
        self.assertLessEqual(dt.datetime.fromisoformat(waiting['since']), dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=119))

    def test_a_short_browser_step_or_other_work_offers_nothing(self):
        self.assertIsNone(assistant.browser_wait(journal(('browser', {'action': 'open'}, 10)), 'job', 'running'))
        self.assertIsNone(assistant.browser_wait(journal(('browser', {}, 300), ('read', {}, 200)), 'job', 'running'))
        self.assertIsNone(assistant.browser_wait(journal(('browser', {}, 300)), 'job', 'done'))

    def test_a_run_that_stopped_right_after_the_browser_offers_it_at_once(self):
        db = journal(('browser', {'action': 'open', 'targetUrl': 'https://x.com/login'}, 20), ('assistant_signals', {'action': 'preparation_blocked'}, 5))
        self.assertEqual(assistant.browser_wait(db, 'job', 'blocked')['page'], 'https://x.com/login')
        self.assertIsNone(assistant.browser_wait(journal(('read', {}, 20)), 'job', 'blocked'))

    def test_link_comes_from_browser_url_only(self):
        self.assertEqual(assistant.browser_link({'browser_url': 'https://cp.example/open?to=browser'}), 'https://cp.example/open?to=browser')
        self.assertEqual(assistant.browser_link({'browser_url': browser_viewer.VIEWER_URL}), browser_viewer.VIEWER_URL)
        self.assertIsNone(assistant.browser_link({'browser_url': 'javascript:alert(1)'}))
        self.assertIsNone(assistant.browser_link({}))

    def test_a_desktop_view_links_to_the_dashboards_own_viewer(self):
        config = {'browser_vnc': '/run/user/1000/view/vnc.sock', 'dashboard_host': 'machine.example', 'dashboard_path': '/'}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'vnc.html').write_text("<html><head></head><script>fetch('./defaults.json')</script></html>")
            config['browser_novnc'] = folder
            self.assertEqual(assistant.browser_link(config), 'https://machine.example/browser')  # noVNC 1.5+: the short link
            self.assertEqual(browser_viewer.view_link('/assistant/', folder), '/assistant/browser')
            (Path(folder) / 'vnc.html').write_text('<html><head></head></html>')  # an older noVNC reads only the query
            self.assertEqual(assistant.browser_link(config), 'https://machine.example/browser/vnc.html?' + browser_viewer.VIEW_QUERY + '&path=browser/websockify')
            self.assertTrue(browser_viewer.view_link('/assistant/', folder).endswith('&path=assistant/browser/websockify'))
        self.assertEqual(assistant.browser_link({**config, 'browser_url': 'https://cp.example/open?to=browser'}), 'https://cp.example/open?to=browser')
        self.assertIsNone(assistant.browser_link({'browser_vnc': '/x.sock'}))  # no dashboard address for Telegram

    def test_the_short_viewer_page_and_its_settings(self):
        defaults, mandatory = browser_viewer.view_settings('/')
        self.assertEqual(defaults, {'autoconnect': True, 'reconnect': True, 'resize': 'off', 'view_clip': True, 'frame': 'window'})
        self.assertEqual(mandatory, {'path': '/browser/websockify'})
        self.assertEqual(browser_viewer.view_settings('/assistant/')[1], {'path': '/assistant/browser/websockify'})
        self.assertEqual(browser_viewer.short_page('<html lang="en"><head><title>x</title></head></html>', '/'),
                         '<html lang="en"><head><base href="/browser/"><title>x</title></head></html>')

    def test_telegram_card_says_so_and_gets_a_button_only_for_a_https_viewer(self):
        row = {'id': 'abc', 'status': 'accepted', 'title': 'Reply', 'body': 'Body', 'preparation_status': 'running',
               'preparation_browser': {'since': assistant.now(), 'page': None, 'link': 'https://cp.example/open?to=browser'}}
        buttons = [b for block in assistant.controls(row)['blocks'] for b in block['buttons']]
        self.assertEqual(buttons[0], {'label': 'Open the browser', 'action': {'type': 'url', 'url': 'https://cp.example/open?to=browser'}})
        self.assertIn('browser', assistant.card(row))
        row['preparation_browser']['link'] = browser_viewer.VIEWER_URL  # Telegram can't open this computer's 127.0.0.1
        self.assertNotIn('Open the browser', [b['label'] for block in assistant.controls(row)['blocks'] for b in block['buttons']])


class Tabs(http.server.BaseHTTPRequestHandler):
    activated = []

    def do_GET(self):
        if self.path == '/json/list':
            body = json.dumps([{'id': 'w', 'type': 'service_worker', 'url': 'https://www.linkedin.com/sw.js'},
                               {'id': 'a', 'type': 'page', 'url': 'https://example.com/'},
                               {'id': 'b', 'type': 'page', 'url': 'https://www.linkedin.com/feed/'}]).encode()
        elif self.path.startswith('/json/activate/'):
            Tabs.activated.append(self.path.rsplit('/', 1)[1])
            body = b'Target activated'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FocusTest(unittest.TestCase):
    def test_brings_the_tab_of_the_same_site_to_the_front(self):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Tabs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cdp = f'http://127.0.0.1:{server.server_port}'
            self.assertEqual(browser_viewer.focus('https://www.linkedin.com/login', cdp), 'https://www.linkedin.com/feed/')
            self.assertEqual(browser_viewer.focus(None, cdp), 'https://example.com/')
            self.assertEqual(Tabs.activated, ['b', 'a'])
        finally:
            server.shutdown()
        self.assertIsNone(browser_viewer.focus(None, cdp))  # no browser running
        with self.assertRaises(ValueError):
            browser_viewer.focus(None, 'http://192.0.2.1:9222')

    def test_viewer_files_add_the_clipboard_bridge_to_novnc(self):
        with tempfile.TemporaryDirectory() as tmp:
            novnc, web = Path(tmp, 'novnc'), Path(tmp, 'web')
            (novnc / 'app').mkdir(parents=True)
            (novnc / 'vnc.html').write_text('<html><head></head></html>')
            (novnc / 'index.html').write_text('a packaged index')  # ours wins
            browser_viewer.build_web(web, novnc)
            browser_viewer.build_web(web, novnc)  # again is fine
            self.assertIn('src="machine-clipboard.js"', (web / 'vnc.html').read_text())
            self.assertIn("import UI from './app/ui.js'", (web / 'machine-clipboard.js').read_text())
            self.assertTrue((web / 'app').is_symlink())
            self.assertIn('src="machine-view.js"', (web / 'vnc.html').read_text())
            index = (web / 'index.html').read_text()  # the short /browser/ address on a hosted Machine
            self.assertFalse((web / 'index.html').is_symlink())
            self.assertTrue(index.startswith('<html><head><script>try{if(!location.search&&!location.hash)'))
            self.assertIn("'?autoconnect=1&reconnect=1&resize='+(phone?'remote':'scale')+'&path=browser/websockify'", index)  # the long link's query
            self.assertIn("history.replaceState(history.state,'',bare)", index)  # and the short address again once noVNC started
            self.assertIn('src="machine-view.js"', index)
            self.assertEqual(index.replace(browser_viewer.SHORT_SETTINGS, ''), (web / 'vnc.html').read_text())
            self.assertIn("params.get('frame') === 'window'", (web / 'machine-view.js').read_text())
            self.assertIn("Your Machine's own browser", (web / 'machine-view.js').read_text())  # a blank page never stands on its own

    def test_units_listen_on_loopback_only(self):
        for unit in browser_viewer.UNITS.glob('machine-browser-*'):
            text = unit.read_text()
            for line in text.splitlines():
                if line.startswith('ListenStream='):
                    self.assertTrue(line.startswith('ListenStream=127.0.0.1:'), unit.name)
            if 'websockify' in text:
                self.assertIn(' 127.0.0.1:6081', text)


class InstallerTest(unittest.TestCase):
    def test_browser_viewer_option_points_the_dashboard_at_the_viewer(self):
        import installer
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            paths = installer.Paths(tmp)
            paths.config.parent.mkdir(parents=True)
            paths.config.write_text(json.dumps({'accounts': []}))
            with mock.patch.object(installer, 'run', return_value=subprocess.CompletedProcess(['service'], 0, 'state = running\n', '')), \
                    mock.patch.dict(os.environ, {'ASSISTANT_SERVICE_MANAGER': 'launchd'}):
                self.assertEqual(installer.browser_viewer_setup(paths, install=lambda say: browser_viewer.VIEWER_URL), 0)
                self.assertEqual(installer.browser_viewer_setup(paths, install=lambda say: None), 1)
            config = json.loads(paths.config.read_text())
            self.assertEqual((config['browser_url'], config['browser_cdp_url']), (browser_viewer.VIEWER_URL, 'http://127.0.0.1:9222'))


class DesktopViewTest(unittest.TestCase):
    def test_the_vnc_server_is_on_this_machine_only(self):
        self.assertEqual(browser_viewer.vnc_target('unix:/run/user/1/v.sock'), (browser_viewer.socket.AF_UNIX, '/run/user/1/v.sock'))
        self.assertEqual(browser_viewer.vnc_target('127.0.0.1:5900'), (browser_viewer.socket.AF_INET, ('127.0.0.1', 5900)))
        for bad in ('192.0.2.1:5900', '0.0.0.0:5900', 'example.com:5900', 'relative.sock', ''):
            with self.assertRaises(ValueError):
                browser_viewer.vnc_target(bad)

    def test_the_view_opens_on_the_browser_window_on_hyprland(self):
        from unittest import mock
        clients = [{'pid': 7, 'mapped': True, 'hidden': False, 'focusHistoryID': 3, 'monitor': 1, 'workspace': {'id': 2},
                    'at': [0, 0], 'size': [100, 100], 'title': 'Other', 'address': '0xb'},
                   {'pid': 5, 'mapped': True, 'hidden': False, 'focusHistoryID': 0, 'monitor': 1, 'workspace': {'id': 1},
                    'at': [1280, 40], 'size': [1280, 1400], 'title': 'Wise - Login - Google Chrome', 'address': '0xa'},
                   {'pid': 9, 'mapped': True, 'hidden': False, 'focusHistoryID': 1, 'monitor': 1, 'workspace': {'id': 1},
                    'at': [0, 0], 'size': [10, 10], 'title': 'Not the browser', 'address': '0xc'}]
        monitors = [{'id': 1, 'x': 0, 'y': 0, 'width': 3840, 'height': 2160, 'scale': 1.5, 'transform': 0, 'activeWorkspace': {'id': 1}}]
        calls = []

        def hypr(*args, **_):
            calls.append(args)
            return clients if args[0] == 'clients' else monitors if args[0] == 'monitors' else ''
        with mock.patch.object(browser_viewer, 'hyprctl', side_effect=hypr), \
                mock.patch.object(browser_viewer, 'debugs_on', side_effect=lambda pid, port: pid in (5, 7) and port == 9222):
            self.assertEqual(browser_viewer.window_region('http://127.0.0.1:9222'), {'x': 0.5, 'y': 40 / 1440, 'w': 0.5, 'h': 1400 / 1440})
            browser_viewer.raise_window('http://127.0.0.1:9222', 'Wise - Login')
            self.assertEqual(calls[-1], ('dispatch', 'focuswindow', 'address:0xa'))
            monitors[0]['activeWorkspace'] = {'id': 3}  # not on screen: the whole screen instead
            self.assertIsNone(browser_viewer.window_region('http://127.0.0.1:9222'))
        with mock.patch.object(browser_viewer, 'hyprctl', return_value=None):  # not Hyprland
            self.assertIsNone(browser_viewer.window_region('http://127.0.0.1:9222'))

    def test_viewer_files_stay_inside_the_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'app').mkdir()
            Path(tmp, 'app/ui.js').write_text('x')
            Path(tmp, '.git').mkdir()
            Path(tmp, '.git/config').write_text('x')
            self.assertEqual(browser_viewer.view_file(tmp, 'app/ui.js'), Path(tmp, 'app/ui.js'))
            for bad in ('../secret', 'app/../../x', '.git/config', '', 'app', '%2e%2e/x'):
                self.assertIsNone(browser_viewer.view_file(tmp, bad), bad)

    def test_bridge_carries_vnc_both_ways(self):
        import io
        import socket
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp, 'vnc.sock'))
            server = socket.socket(socket.AF_UNIX)
            server.bind(path)
            server.listen(1)
            got = []
            received = threading.Event()

            def vnc():
                with server.accept()[0] as conn:
                    conn.sendall(b'RFB 003.008\n')
                    got.append(conn.recv(100))
                    received.set()
            upstream = threading.Thread(target=vnc, daemon=True)
            upstream.start()
            mask = b'\x01\x02\x03\x04'
            payload = b'RFB 003.008\n'
            client = bytes([0x82, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            ours, theirs = socket.socketpair()
            headers = {'Sec-WebSocket-Key': base64.b64encode(b'the sample nonce').decode(),  # RFC 6455's example
                       'Sec-WebSocket-Protocol': 'binary'}
            class ClientInput(io.BytesIO):
                def read(self, n=-1):
                    data = super().read(n)
                    if not data:
                        # A real websocket blocks here, rather than immediately
                        # racing EOF against the fake server's greeting/receipt.
                        received.wait(5)
                    return data
            worker = threading.Thread(target=browser_viewer.bridge, args=(ClientInput(client), ours, path, headers), daemon=True)
            worker.start()
            theirs.settimeout(5)
            data = b''
            while b'RFB' not in data:
                data += theirs.recv(1000)
            self.assertIn(b'101 Switching Protocols', data)
            self.assertIn(b's3pPLMBiTxaQ9kYGzzhZRbK+xOo=', data)  # RFC 6455's own example
            self.assertIn(b'Sec-WebSocket-Protocol: binary', data)
            self.assertIn(browser_viewer.ws_frame(b'RFB 003.008\n'), data)
            worker.join(5)
            upstream.join(5)
            self.assertEqual(got, [payload])
            ours.close()
            theirs.close()
            server.close()


if __name__ == '__main__':
    unittest.main()
