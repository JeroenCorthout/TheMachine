"""onboarding.py: Telegram detection, assistant connect, first sync, doctor and the starter USER.md.
Stub gog/openclaw/wacli/tailscale binaries and patched source reads; nothing contacts a real account."""
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import onboarding

ROOT = Path(__file__).resolve().parent
EXAMPLE = {a['id']: a for a in json.loads((ROOT / 'config.example.json').read_text())['accounts']}


class FakeUI(onboarding.UI):
    def __init__(self, answers=(), secrets=(), yes=()):
        super().__init__(True)
        self.answers, self.secrets, self.yeses, self.out = list(answers), list(secrets), list(yes), []

    def say(self, text=''):
        self.out.append(str(text))

    def ask(self, prompt, default=''):
        self.out.append(prompt)
        answer = self.answers.pop(0) if self.answers else ''
        return answer or default

    def secret(self, prompt):
        self.out.append(prompt)
        return self.secrets.pop(0) if self.secrets else ''

    def yes(self, prompt, default=True):
        self.out.append(prompt)
        return self.yeses.pop(0) if self.yeses else default

    def text(self):
        return '\n'.join(self.out)


PLUGINS = hasattr(onboarding.assistant(), 'load_connector')  # connector plugins (OSS); else built-in SETUPS


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='machine-onboarding-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.bin, self.log = self.tmp / 'home', self.tmp / 'bin', self.tmp / 'calls.log'
        self.home.mkdir()
        self.bin.mkdir()
        self.data = self.home / '.local/share/personal-assistant'
        self.data.mkdir(parents=True)
        self.path = self.data / 'config.json'
        env = mock.patch.dict(os.environ, {'HOME': str(self.home), 'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                                           'OPENCLAW_WORKSPACE': str(self.home / 'workspace'),
                                           'ASSISTANT_SERVICE_MANAGER': 'systemd'})  # the stubs are systemd's, also on macOS
        env.start()
        self.addCleanup(env.stop)
        self.stub('systemctl', 'print("inactive")')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(f'#!{sys.executable}\nimport json, os, sys\nargs = sys.argv[1:]\n'
                        f'open({str(self.log)!r}, "a").write({name!r} + " " + " ".join(args) + "\\n")\n' + body + '\n')
        path.chmod(0o755)
        return str(path)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ''

    def config(self, *accounts, **extra):
        config = {'database': str(self.data / 'events.sqlite'), 'port': 1, 'telegram_owner': '1000001',
                  'tailscale_owner': 'alex@example.com', 'dashboard_host': 'box.example.ts.net',
                  'credentials': {'api-token': str(self.data / 'api-token'), 'owner-token': str(self.data / 'owner-token')},
                  'accounts': [json.loads(json.dumps(a)) for a in accounts], **extra}
        for account in config['accounts']:
            account.pop('exclude', None)
            if account.get('credential'):
                config['credentials'][account['credential']] = str(self.data / f'{account["credential"]}.env')
        onboarding.write_private(self.path, json.dumps(config))
        return config

    def saved(self):
        return json.loads(self.path.read_text())

    def mode(self, path):
        return stat.S_IMODE(Path(path).stat().st_mode)

    def test_plugin_setup_uses_the_same_helpers_as_its_collector(self):
        from types import SimpleNamespace
        def check(config, account, helpers):
            self.assertIs(helpers, onboarding.assistant().CONNECTOR_HELPERS)
            self.assertEqual(helpers.iso(0), '1970-01-01T00:00:00+00:00')
        with mock.patch.object(onboarding.assistant(), 'load_connector', return_value=SimpleNamespace(check=check)):
            self.assertIsNone(onboarding.check({}, {'service':'test'}))

    def test_a_busy_browser_does_not_fail_setup(self):
        self.config()
        ui = FakeUI(answers=['personal', 'alex_example'])
        with mock.patch.object(onboarding, 'check', return_value='x_browser_busy'), \
             mock.patch.object(onboarding, 'step_plugin', side_effect=lambda config, account, ui: account.update(handle='alex_example')):
            onboarding.connect(self.path, 'x', ui, restart=False)
        self.assertEqual([a['service'] for a in json.loads(self.path.read_text())['accounts']], ['x'])
        self.assertTrue(any('Saved; the first sync reads it' in line for line in ui.out))
        with mock.patch.object(onboarding, 'check', return_value='x_signed_out'), \
             mock.patch.object(onboarding, 'step_plugin', side_effect=lambda config, account, ui: None), self.assertRaises(onboarding.SetupError):
            onboarding.connect(self.path, 'x', FakeUI(answers=['personal']), 'other-x', restart=False)

    def test_signed_out_says_so_plainly_and_a_waiting_ui_connects_once_signed_in(self):
        self.config()
        with mock.patch.object(onboarding, 'check', return_value='linkedin_signed_out'), \
             mock.patch.object(onboarding, 'step_plugin', side_effect=lambda config, account, ui: None), \
             self.assertRaises(onboarding.SetupError) as raised:
            onboarding.connect(self.path, 'linkedin', FakeUI(answers=['personal']), restart=False)
        self.assertEqual(raised.exception.message, 'You are not signed in to LinkedIn on your Machine yet')
        self.assertNotIn('linkedin_signed_out', str(raised.exception))
        ui = FakeUI(answers=['personal'])
        ui.wait_signin = 60  # the hosted setup page: wait for the owner to sign in, then go on
        checks = iter(['linkedin_signed_out', 'linkedin_signed_out', None])
        with mock.patch.object(onboarding, 'check', side_effect=lambda config, account: next(checks)), \
             mock.patch.object(onboarding, 'step_plugin', side_effect=lambda config, account, ui: None), \
             mock.patch.object(onboarding, 'SIGNIN_EVERY', 0):
            onboarding.connect(self.path, 'linkedin', ui, restart=False)
        self.assertEqual([a['service'] for a in json.loads(self.path.read_text())['accounts']], ['linkedin'])
        self.assertTrue(any('waiting for you to sign in to LinkedIn on your Machine' in line for line in ui.out))

    def test_a_second_browser_account_keeps_the_browser_profile_it_was_signed_in_with(self):
        self.config()
        def plugin(config, account, ui):
            account.setdefault('handle', 'alex_example')
        with mock.patch.object(onboarding, 'check', return_value=None), mock.patch.object(onboarding, 'step_plugin', side_effect=plugin), \
             mock.patch.dict(os.environ, {'X_INBOX_BROWSER_PROFILE': 'machine'}):
            onboarding.connect(self.path, 'x', FakeUI(answers=['personal']), restart=False)
        with mock.patch.object(onboarding, 'check', return_value=None), mock.patch.object(onboarding, 'step_plugin', side_effect=plugin), \
             mock.patch.dict(os.environ, {'X_INBOX_BROWSER_PROFILE': 'machine-2', 'X_INBOX_BROWSER_CDP': 'http://127.0.0.1:9304'}):
            onboarding.connect(self.path, 'x', FakeUI(answers=['personal']), 'second-x', restart=False)
        with mock.patch.object(onboarding, 'check', return_value=None), mock.patch.object(onboarding, 'step_plugin', side_effect=plugin), \
             mock.patch.dict(os.environ, {'X_INBOX_BROWSER_PROFILE': 'machine'}):
            onboarding.connect(self.path, 'x', FakeUI(), 'second-x', restart=False)  # connecting it again keeps its own
        accounts = json.loads(self.path.read_text())['accounts']
        self.assertEqual([a.get('browser_profile') for a in accounts], ['machine', 'machine-2'])
        self.assertEqual([a.get('browser_cdp') for a in accounts], [None, 'http://127.0.0.1:9304'])
        self.assertNotEqual(accounts[0]['id'], accounts[1]['id'])

    def test_plugin_setup_without_a_read_check_is_not_reported_verified(self):
        from types import SimpleNamespace
        with mock.patch.object(onboarding.assistant(), 'load_connector', return_value=SimpleNamespace()):
            self.assertEqual(onboarding.check({}, {'service':'test'}), 'source_check_unavailable')

    # ------------------------------------------------------------ Telegram

    def test_telegram_ids_come_from_openclaw_owner_first_without_touching_the_bot_token(self):
        openclaw = self.home / '.openclaw'
        (openclaw / 'state').mkdir(parents=True)
        (openclaw / 'openclaw.json').write_text(json.dumps({
            'commands': {'ownerAllowFrom': ['telegram:111111', 'discord:999999']},
            'channels': {'telegram': {'botToken': '123:SECRET', 'allowFrom': ['222222', '@someone'],
                                      'accounts': {'work': {'allowFrom': ['tg:333333']}}}}}))
        with sqlite3.connect(openclaw / 'state/openclaw.sqlite') as db:
            db.execute('CREATE TABLE channel_pairing_allow_entries(channel_key TEXT, account_id TEXT, entry TEXT, sort_order INTEGER, updated_at INTEGER)')
            db.executemany('INSERT INTO channel_pairing_allow_entries VALUES(?,?,?,?,0)',
                           [('telegram', 'default', '444444', 1), ('telegram', 'default', '111111', 0), ('whatsapp', 'default', '555555', 0)])
        self.assertEqual(onboarding.telegram_owners(), ['111111', '222222', '333333', '444444'])
        self.assertEqual(onboarding.telegram_owners(root=self.tmp / 'nowhere'), [])
        self.assertEqual((onboarding.telegram_account('333333'), onboarding.telegram_account('111111')), ('work', 'default'))
        # openclaw.json is JSON5: comments, trailing commas and unquoted keys are fine; a named-only bot is found too.
        (openclaw / 'openclaw.json').write_text('{\n  // mine\n  channels: {telegram: {accounts: {bot: {botToken: "1:x", allowFrom: ["555555",],},},},},\n}')
        self.assertEqual(onboarding.telegram_owners()[:1], ['555555'])
        self.assertEqual(onboarding.telegram_account('999'), 'bot')

    def test_telegram_turns_off_and_back_on_and_only_removes_the_bot_when_asked(self):
        openclaw = self.home / '.openclaw'
        openclaw.mkdir(parents=True, exist_ok=True)
        (openclaw / 'openclaw.json').write_text(json.dumps({'channels': {'telegram': {'accounts': {'work': {'allowFrom': ['333333']}}}}}))
        path = self.tmp / 'config.json'
        path.write_text(json.dumps({'telegram_owner': '333333', 'telegram_account': 'work', 'openclaw': sys.executable}))
        calls = []
        runner = lambda args, **kw: calls.append(args) or onboarding.subprocess.CompletedProcess(args, 0, '', '')
        self.assertEqual(onboarding.set_telegram(path, None, runner=runner, restart=False), [])
        self.assertEqual(calls, [])  # The Machine stops using Telegram; the OpenClaw bot stays for chatting
        self.assertEqual(json.loads(path.read_text())['telegram_owner'], '')
        self.assertNotIn('telegram_account', json.loads(path.read_text()))
        onboarding.set_telegram(path, '333333', runner=runner, restart=False)
        self.assertEqual({k: json.loads(path.read_text())[k] for k in ('telegram_owner', 'telegram_account')}, {'telegram_owner': '333333', 'telegram_account': 'work'})
        notes = onboarding.set_telegram(path, None, remove_bot=True, runner=runner, restart=False)
        self.assertEqual(calls[-1][1:], ['channels', 'remove', '--channel', 'telegram', '--account', 'work', '--delete'])
        self.assertIn('removed the Telegram bot', notes[0])
        failing = lambda args, **kw: onboarding.subprocess.CompletedProcess(args, 1, '', 'gateway busy')
        path.write_text(json.dumps({'telegram_owner': '333333', 'openclaw': sys.executable}))
        with self.assertRaises(onboarding.SetupError):
            onboarding.set_telegram(path, None, remove_bot=True, runner=failing, restart=False)
        self.assertEqual(json.loads(path.read_text())['telegram_owner'], '333333')  # nothing half-done
        with self.assertRaises(onboarding.SetupError):
            onboarding.set_telegram(path, '@alex', runner=runner, restart=False)

    def test_pairing_fallback_picks_up_the_new_sender_and_approves_only_on_yes(self):
        counter = self.tmp / 'count'
        openclaw = self.stub('openclaw', f'''
n = int(open({str(counter)!r}).read()) if os.path.exists({str(counter)!r}) else 0
open({str(counter)!r}, "w").write(str(n + 1))
old = [{{"id": "777777", "code": "OLDCODE2"}}]
new = [{{"id": "555555", "code": "ABCD2345", "meta": {{"username": "alex"}}}}]
if args[:2] == ["pairing", "list"]:
    print(json.dumps({{"channel": "telegram", "requests": old + (new if n >= 2 else [])}}))''')
        ui = FakeUI(yes=[True])
        with mock.patch.object(onboarding.time, 'sleep'):
            self.assertEqual(onboarding.telegram_from_pairing(openclaw, ui), '555555')
        self.assertIn('openclaw pairing approve telegram ABCD2345 --notify', self.calls())
        self.assertNotIn('OLDCODE2 --notify', self.calls())
        self.assertIn('alex', ui.text())
        counter.unlink()
        ui = FakeUI(yes=[False])
        with mock.patch.object(onboarding.time, 'sleep'):
            self.assertEqual(onboarding.telegram_from_pairing(openclaw, ui), '555555')
        self.assertEqual(self.calls().count('pairing approve'), 1)

    # ------------------------------------------------------------ connect

    def test_slack_finds_its_ids_and_the_key_never_shows(self):
        self.config(EXAMPLE['work-slack'])
        answers = {'auth.test': {'ok': True, 'team_id': 'T123', 'user_id': 'U456', 'url': 'https://acme.slack.com/'},
                   'users.info': {'ok': True, 'user': {'profile': {'email': 'alex@acme.test'}}}}
        ui = FakeUI(secrets=['MATON_API_KEY=mk_SECRET'])
        with mock.patch.object(onboarding.assistant().Slack, 'get', lambda self, method, params=None: answers[method]), \
                mock.patch.object(onboarding, 'maton_connections', return_value=['conn-1']) as listed:
            onboarding.connect(self.path, 'slack', ui, restart=False)
        self.assertEqual(listed.call_args.args[2], 'slack')
        self.assertNotIn('connection ID', ui.text())  # found in Maton, not asked
        account = self.saved()['accounts'][0]
        self.assertEqual((account['team_id'], account['user_id'], account['email'], account['slack_host'], account['connection_id']),
                         ('T123', 'U456', 'alex@acme.test', 'acme.slack.com', 'conn-1'))
        secret = self.data / 'maton-slack.env'
        self.assertEqual(secret.read_text(), 'MATON_API_KEY=mk_SECRET\n')
        self.assertEqual((self.mode(secret), self.mode(self.path)), (0o600, 0o600))
        self.assertNotIn('mk_SECRET', ui.text())
        self.assertTrue((self.data / 'config.json.bak').exists())
        self.assertEqual(onboarding.missing(self.saved(), account), '')

    def test_slack_with_several_workspaces_in_maton_asks_which_by_name(self):
        self.config(EXAMPLE['work-slack'])
        teams = {'conn-a': 'https://acme.slack.com/', 'conn-b': 'https://home.slack.com/'}

        def get(slack, method, params=None):
            if method == 'auth.test':
                return {'ok': True, 'team_id': 'T' + slack.account['connection_id'][-1], 'user_id': 'U1', 'user': 'alex',
                        'url': teams[slack.account['connection_id']]}
            return {'ok': True, 'user': {'profile': {'email': 'alex@home.test'}}}
        ui = FakeUI(answers=['2'], secrets=['mk'])
        with mock.patch.object(onboarding.assistant().Slack, 'get', get), \
                mock.patch.object(onboarding, 'maton_connections', return_value=['conn-a', 'conn-b']):
            onboarding.connect(self.path, 'slack', ui, restart=False)
        self.assertIn('acme.slack.com as alex', ui.text())
        self.assertIn('Which Slack workspace in Maton', ui.text())
        self.assertEqual(self.saved()['accounts'][0]['slack_host'], 'home.slack.com')

    def test_maton_lists_active_connections_newest_first_and_says_when_it_cannot(self):
        account = dict(EXAMPLE['work-slack'], credential='maton-slack', credential_variable='MATON_API_KEY')
        onboarding.write_private(self.data / 'maton-slack.env', 'MATON_API_KEY=mk\n')
        config = {'credentials': {'maton-slack': str(self.data / 'maton-slack.env')}}
        body = {'connections': [{'connection_id': 'old', 'status': 'ACTIVE', 'creation_time': '2026-01-01'},
                                {'connection_id': 'new', 'status': 'ACTIVE', 'creation_time': '2026-06-01'}, {'status': 'ACTIVE'}]}
        seen = []

        def urlopen(request, timeout):
            seen.append(request)
            return io.BytesIO(json.dumps(body).encode())
        with mock.patch.object(onboarding.urllib.request, 'urlopen', urlopen):
            self.assertEqual(onboarding.maton_connections(config, account, 'slack'), ['new', 'old'])
        self.assertEqual(seen[0].full_url, 'https://ctrl.maton.ai/connections?app=slack&status=ACTIVE')
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer mk')
        with mock.patch.object(onboarding.urllib.request, 'urlopen', side_effect=OSError('down')):
            self.assertIsNone(onboarding.maton_connections(config, account, 'slack'))
        with mock.patch.object(onboarding.urllib.request, 'urlopen', side_effect=onboarding.urllib.error.HTTPError('u', 401, 'no', {}, None)):
            with self.assertRaisesRegex(onboarding.SetupError, 'rejected the API key'):
                onboarding.maton_connections(config, account, 'slack')
        with mock.patch.object(onboarding, 'maton_connections', return_value=[]):
            with self.assertRaisesRegex(onboarding.SetupError, 'no Slack connection'):
                onboarding.maton_connection(config, account, FakeUI(), 'slack', 'Slack workspace')
        with mock.patch.object(onboarding, 'maton_connections', return_value=None):
            self.assertEqual(onboarding.maton_connection(config, account, FakeUI(answers=['typed']), 'slack', 'Slack workspace'), 'typed')

    def test_salesflare_ids_come_from_me(self):
        self.config(EXAMPLE['work-crm'])
        me = {'id': 42, 'email': 'alex@acme.test', 'team': {'id': 7}}
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', lambda self, path, params=None: me):
            onboarding.connect(self.path, 'crm', FakeUI(secrets=['sf_KEY']), restart=False)
        account = self.saved()['accounts'][0]
        self.assertEqual((account['user_id'], account['team_id'], account['email'], account['owner_emails']), (42, 7, 'alex@acme.test', ['alex@acme.test']))
        self.assertEqual((self.data / 'salesflare.env').read_text(), 'SALESFLARE_API_KEY=sf_KEY\n')

    def test_a_failing_test_read_saves_nothing_and_says_how_to_fix_it(self):
        self.config(EXAMPLE['work-crm'])
        before = self.path.read_text()
        error = onboarding.assistant().SourceError('salesflare', 401)
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', side_effect=error):
            with self.assertRaises(onboarding.SetupError) as raised:
                onboarding.connect(self.path, 'crm', FakeUI(secrets=['wrong']), restart=False)
        self.assertIn('rejected the API key', raised.exception.fix)
        self.assertEqual(self.path.read_text(), before)

    def test_whatsapp_identity_is_the_hash_of_the_linked_account(self):
        jid = '32470000000@s.whatsapp.net'
        wacli = self.stub('wacli', f'print(json.dumps({{"success": True, "error": None, "data": {{"authenticated": True, "linked_jid": {jid!r}}}}}))')
        store = self.home / 'wa'
        store.mkdir()
        self.config(EXAMPLE['personal-whatsapp'], wacli=wacli)
        onboarding.connect(self.path, 'whatsapp', FakeUI(answers=[str(store)]), restart=False)
        account = self.saved()['accounts'][0]
        self.assertEqual(account['identity_sha256'], hashlib.sha256(jid.encode()).hexdigest())
        self.assertIn('--read-only', self.calls())
        self.assertNotIn('wacli --store ' + str(store) + ' auth\n', self.calls())

    def test_android_takes_the_only_paired_phone(self):
        nodes = {'pending': [], 'paired': [{'nodeId': 'linux-1', 'platform': 'linux'},
                                           {'nodeId': 'phone-1', 'platform': 'android', 'displayName': 'Pixel', 'connected': True}]}
        openclaw = self.stub('openclaw', f'print(json.dumps({nodes!r}))')
        self.config(EXAMPLE['personal-android'], openclaw=openclaw)
        ui = FakeUI()
        with mock.patch.object(onboarding.assistant().Android, 'snapshot', return_value={}):
            onboarding.connect(self.path, 'android', ui, restart=False)
        self.assertEqual(self.saved()['accounts'][0]['node_id'], 'phone-1')
        self.assertIn('Pixel', ui.text())

    def test_phone_apps_that_repeat_a_source_are_skipped_only_when_asked(self):
        phone = {'id': 'phone', 'service': 'android'}
        config = {'accounts': [{'id': 'mail', 'service': 'gmail'}, {'id': 'chat', 'service': 'whatsapp'}, {'id': 'log', 'service': 'gateway'}]}
        ui = FakeUI(yes=[True, False])
        onboarding.offer_phone_skip(config, phone, ui)
        self.assertEqual(phone['exclude'], [{'packages': ['com.google.android.gm']}])  # WhatsApp kept: the owner said no
        self.assertEqual(sum('Skip' in line for line in ui.out), 2)  # nothing to ask for the Gateway log
        config['accounts'].append(phone)
        slack = {'id': 'team', 'service': 'slack'}
        onboarding.offer_phone_skip(config, slack, FakeUI(yes=[True]))
        self.assertEqual(phone['exclude'][-1], {'packages': ['com.Slack']})
        ui = FakeUI(yes=[True])
        onboarding.offer_phone_skip(config, slack, ui)  # already skipped: not asked again
        self.assertEqual((ui.out, len(phone['exclude'])), ([], 2))
        onboarding.offer_phone_skip(config, {'id': 'more', 'service': 'whatsapp'}, onboarding.UI(False))
        self.assertEqual(len(phone['exclude']), 2)  # unattended setup never drops phone notifications

    def test_gmail_signs_in_read_only_and_calendar_reuses_the_sign_in(self):
        flag = self.tmp / 'authorized'
        gog = self.stub('gog', f'''
if args[:3] == ["auth", "credentials", "list"]:
    print(json.dumps({{"clients": [{{"client": "work", "default": True, "path": "x"}}]}}))
elif args[:2] == ["auth", "list"]:
    print(json.dumps({{"accounts": []}}))
elif args[:2] == ["auth", "add"]:
    open({str(flag)!r}, "w").write(os.environ.get("GOG_KEYRING_PASSWORD", ""))
elif args[:2] == ["api", "call"] and os.path.exists({str(flag)!r}) and os.environ.get("GOG_KEYRING_PASSWORD") == open({str(flag)!r}).read():
    print(json.dumps({{"emailAddress": "alex@acme.test", "historyId": "9", "id": "alex@acme.test"}}))
else:
    sys.stderr.write("HTTP 401"); sys.exit(1)''')
        gmail = dict(EXAMPLE['work-gmail'], client='default')
        self.config(gmail, gog=gog)
        ui = FakeUI(answers=['alex@acme.test'], secrets=[''])
        with mock.patch.object(onboarding, 'headless', return_value=False):
            onboarding.connect(self.path, 'gmail', ui, restart=False)
            config = self.saved()
            password = Path(config['credentials']['gog-password'])
            self.assertEqual(self.mode(password), 0o600)
            self.assertGreater(len(password.read_text().strip()), 20)
            self.assertNotIn(password.read_text().strip(), ui.text())
            self.assertIn('gog auth add alex@acme.test --client work --services gmail,calendar --readonly --gmail-scope readonly', self.calls())
            self.assertEqual(config['accounts'][0]['client'], 'work')
            onboarding.connect(self.path, 'calendar', FakeUI(answers=['']), restart=False)
        config = self.saved()
        self.assertEqual([a['id'] for a in config['accounts']], ['work-gmail', 'work-calendar'])
        self.assertEqual((config['accounts'][1]['email'], config['accounts'][1]['client']), ('alex@acme.test', 'work'))
        self.assertEqual(self.calls().count('auth add'), 1)

    def test_an_existing_gog_keyring_keeps_its_password(self):
        gog = self.stub('gog', '''
if args[:3] == ["auth", "credentials", "list"]: print(json.dumps({"clients": [{"client": "default"}]}))
elif args[:2] == ["auth", "list"]: print(json.dumps({"accounts": [{"email": "alex@acme.test", "client": "default"}]}))
elif os.environ.get("GOG_KEYRING_PASSWORD") == "kept": print(json.dumps({"emailAddress": "alex@acme.test", "historyId": "9"}))
else: sys.stderr.write("HTTP 401"); sys.exit(1)''')
        self.config(dict(EXAMPLE['work-gmail'], client='default'), gog=gog)
        ui = FakeUI(answers=[''], secrets=[''])
        with self.assertRaises(onboarding.SetupError) as raised:
            onboarding.connect(self.path, 'gmail', ui, restart=False)
        self.assertIn('already holds alex@acme.test', ui.text())
        self.assertIn('GOG_KEYRING_PASSWORD', raised.exception.fix)
        self.assertFalse((self.data / 'gog-password').exists())
        with mock.patch.dict(os.environ, {'GOG_KEYRING_PASSWORD': 'kept'}):
            onboarding.connect(self.path, 'gmail', FakeUI(answers=['']), restart=False)
        self.assertEqual((self.data / 'gog-password').read_text().strip(), 'kept')
        self.assertNotIn('auth add', self.calls())

    def test_boundaries_from_what_people_type(self):
        cases = {'Acme': 'work:Acme', 'personal': 'personal:me', 'Me': 'personal:me', 'family': 'personal:family',
                 'work:Beta': 'work:Beta', 'personal:Sam': 'personal:Sam', '': 'work:Main', 'work:': 'work:Main'}
        self.assertEqual({k: onboarding.boundary_label(k) for k in cases}, cases)
        self.assertEqual([onboarding.company_of(e) for e in ('alex@acme.io', 'sam@mail.globex.co.uk', 'me@gmail.com', 'alex@example.com')],
                         ['Acme', 'Globex', '', ''])
        self.assertEqual(onboarding.slug('Café Ünïon'), 'cafe-union')

    # ------------------------------------------------------------ setups that are not the example

    def test_a_shared_key_file_keeps_its_other_keys_and_saved_keys_are_offered_by_name(self):
        crm = dict(EXAMPLE['work-crm'], credential_variable='SALESFLARE_STAGING_API_KEY', api_base='https://stagingapi.example.test')
        self.config(crm)
        shared = self.data / 'salesflare.env'
        onboarding.write_private(shared, '# mine\nSALESFLARE_PROD_API_KEY=prod1\nSALESFLARE_BASE_URL=https://x\nSALESFLARE_STAGING_API_KEY=old\n')
        me = {'id': 1, 'email': 'alex@acme.test', 'team': {'id': 1}}
        ui = FakeUI(answers=['3'], secrets=['new-staging'])  # 3 = a new key
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', lambda self, path, params=None: me):
            onboarding.connect(self.path, 'crm', ui, restart=False)
        self.assertEqual(shared.read_text(), '# mine\nSALESFLARE_PROD_API_KEY=prod1\nSALESFLARE_BASE_URL=https://x\nSALESFLARE_STAGING_API_KEY=new-staging\n')
        self.assertIn('the saved SALESFLARE_STAGING_API_KEY (salesflare.env)', ui.text())
        self.assertNotIn('prod1', ui.text())
        account = self.saved()['accounts'][0]
        self.assertEqual((account['api_base'], account['credential_variable']), ('https://stagingapi.example.test', 'SALESFLARE_STAGING_API_KEY'))
        ui = FakeUI(answers=['1', '2'])  # redo it, with the saved production key
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', lambda self, path, params=None: me):
            onboarding.connect(self.path, 'crm', ui, restart=False)
        self.assertEqual(self.saved()['accounts'][0]['credential_variable'], 'SALESFLARE_PROD_API_KEY')
        self.assertEqual(ui.secrets, [])
        config = self.saved()
        config['accounts'][0]['credential_variable'] = 'SALESFLARE_GONE_KEY'
        self.assertEqual(onboarding.missing(config, config['accounts'][0]), 'SALESFLARE_GONE_KEY is not in salesflare.env')

    @unittest.skipUnless(PLUGINS, 'Stripe with its own key needs the connector plugin')
    def test_stripe_can_reuse_the_maton_key_slack_already_uses(self):
        slack = dict(EXAMPLE['work-slack'], email='alex@acme.test', credential_variable='MATON_WORK_KEY', connection_id='conn-slack', team_id='T1', user_id='U1')
        self.config(slack)
        onboarding.write_private(self.data / 'maton-slack.env', 'MATON_WORK_KEY=mk\n')
        ui = FakeUI(answers=['1', 'acct_123', '1', '1'])  # Work · Acme, account, through Maton, the saved key; the connection is found
        with mock.patch.object(onboarding.assistant().load_connector('stripe').Source, 'get', return_value={'id': 'acct_123'}), \
                mock.patch.object(onboarding, 'maton_connections', return_value=['conn-stripe']):
            onboarding.connect(self.path, 'stripe', ui, restart=False)
        stripe = self.saved()['accounts'][1]
        self.assertEqual({k: stripe[k] for k in ('transport', 'credential', 'credential_variable', 'connection_id', 'stripe_account', 'boundary')},
                         {'transport': 'maton', 'credential': 'maton-slack', 'credential_variable': 'MATON_WORK_KEY',
                          'connection_id': 'conn-stripe', 'stripe_account': 'acct_123', 'boundary': 'work:Acme'})
        self.assertEqual((self.data / 'maton-slack.env').read_text(), 'MATON_WORK_KEY=mk\n')
        self.assertEqual(ui.secrets, [])
        # A new key for one of them goes to its own file; the other keeps the shared one.
        answers = {'auth.test': {'ok': True, 'team_id': 'T1', 'user_id': 'U1'}, 'users.info': {'ok': True, 'user': {'profile': {'email': 'alex@acme.test'}}}}
        ui = FakeUI(answers=['1', '2'], secrets=['mk2'])  # redo the Slack account, a new key
        with mock.patch.object(onboarding.assistant().Slack, 'get', lambda self, method, params=None: answers[method]), \
                mock.patch.object(onboarding, 'maton_connections', return_value=['conn-slack']):
            onboarding.connect(self.path, 'slack', ui, restart=False)
        config = self.saved()
        self.assertEqual(config['accounts'][0]['credential'], 'work-slack')
        self.assertEqual(Path(config['credentials']['work-slack']).read_text(), 'MATON_WORK_KEY=mk2\n')
        self.assertEqual((self.data / 'maton-slack.env').read_text(), 'MATON_WORK_KEY=mk\n')

    def test_a_second_company_gets_its_own_account_named_your_way(self):
        gog = self.stub('gog', '''
if args[:2] == ["auth", "list"]:
    print(json.dumps({"accounts": [{"email": "me@one.test", "client": "mine"}, {"email": "me@two.test", "client": "mine"}]}))
elif args[:3] == ["auth", "credentials", "list"]:
    print(json.dumps({"clients": [{"client": "mine", "default": True}]}))
elif args[:2] == ["calendar", "calendars"]:
    print(json.dumps([{"id": "me@two.test", "summary": "Me", "primary": True}, {"id": "team@group.test", "summary": "Team", "selected": True}]))
elif args[:2] == ["api", "call"]:
    me = args[args.index("--account") + 1]
    me = json.loads(args[args.index("--params") + 1]).get("calendarId", "primary").replace("primary", me)
    print(json.dumps({"emailAddress": me, "historyId": "1", "id": me}))''')
        one = dict(EXAMPLE['work-gmail'], id='one-gmail', email='me@one.test', boundary='work:One', client='mine', excluded_terms=['secret-1'])
        self.config(one, EXAMPLE['personal-whatsapp'], gog=gog)
        onboarding.write_private(self.data / 'gog-password', 'pw')
        config = self.saved()
        config['credentials']['gog-password'] = str(self.data / 'gog-password')
        onboarding.write_private(self.path, json.dumps(config))
        ui = FakeUI(answers=['2', '2', 'work:Two', ''])  # add another, something else, its name, the suggested address
        onboarding.connect(self.path, 'gmail', ui, restart=False)
        two = self.saved()['accounts'][-1]
        self.assertEqual((two['id'], two['boundary'], two['email'], two['client']), ('two-gmail', 'work:Two', 'me@two.test', 'mine'))
        self.assertNotIn('excluded_terms', two)
        self.assertEqual(two['boundary_by'], 'owner')  # the owner's own marking, which a Work Fleet's removal follows
        self.assertNotIn('personal:whatsapp', ui.text())
        self.assertNotIn('auth add', self.calls())
        ui = FakeUI(answers=['2', '', '2'])  # Two, its own Google account, then the shared team calendar
        onboarding.connect(self.path, 'calendar', ui, restart=False)
        calendar = self.saved()['accounts'][-1]
        self.assertIn('Which part of your life', ui.text())
        self.assertEqual((calendar['id'], calendar['email'], calendar['calendar_id'], calendar['display_name']),
                         ('two-calendar', 'me@two.test', 'team@group.test', 'Team'))

    def test_salesflare_on_another_server(self):
        self.config(EXAMPLE['work-crm'])
        def get(source, path, params=None):
            if source.account['api_base'] != 'https://stagingapi.example.test':
                raise onboarding.assistant().SourceError('salesflare', 401)
            return {'id': 5, 'email': 'alex@acme.test', 'team': {'id': 6}}
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', get):
            onboarding.connect(self.path, 'crm', FakeUI(answers=['stagingapi.example.test'], secrets=['k']), restart=False)
        account = self.saved()['accounts'][0]
        self.assertEqual((account['api_base'], account['app_base']), ('https://stagingapi.example.test', 'https://staging.example.test'))

    def test_a_new_salesflare_account_starts_on_the_preset_server_and_only_sends_the_key_there(self):
        staging = {'api_base': 'https://stagingapi.salesflare.com', 'app_base': 'https://staging.salesflare.com'}
        self.config(salesflare_server=staging)
        asked = []
        def get(source, path, params=None):
            asked.append(source.account['api_base'])
            return {'id': 5, 'email': 'alex@acme.test', 'team': {'id': 6}}
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', get):
            onboarding.connect(self.path, 'crm', FakeUI(secrets=['k']), restart=False)
        account = self.saved()['accounts'][0]
        self.assertEqual((account['api_base'], account['app_base']), (staging['api_base'], staging['app_base']))
        self.assertEqual(set(asked), {'https://stagingapi.salesflare.com'})
        # one connected before keeps its own server when it is set up again
        prod = dict(EXAMPLE['work-crm'], api_base='https://api.salesflare.com', app_base='https://app.salesflare.com')
        self.config(prod, salesflare_server=staging)
        asked.clear()
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', get):
            onboarding.connect(self.path, 'crm', FakeUI(answers=['1'], secrets=['k']), restart=False)
        self.assertEqual(set(asked), {'https://api.salesflare.com'})
        self.assertEqual(self.saved()['accounts'][0]['app_base'], 'https://app.salesflare.com')

    def test_a_salesflare_preset_counts_only_as_https_on_salesflare_com(self):
        good = {'api_base': 'https://stagingapi.salesflare.com', 'app_base': 'https://staging.salesflare.com/'}
        self.assertEqual(onboarding.salesflare_preset({'salesflare_server': good}),
                         {'api_base': 'https://stagingapi.salesflare.com', 'app_base': 'https://staging.salesflare.com'})
        for api in ('http://stagingapi.salesflare.com', 'https://stagingapi.salesflare.com.evil.test', 'https://salesflare.com',
                    'https://evilsalesflare.com', 'https://x@stagingapi.salesflare.com', 'https://stagingapi.salesflare.com:8443',
                    'https://stagingapi.salesflare.com/v2', 'https://stagingapi.salesflare.com?x=1', 'stagingapi.salesflare.com', 7):
            self.assertIsNone(onboarding.salesflare_preset({'salesflare_server': dict(good, api_base=api)}), api)
        self.assertIsNone(onboarding.salesflare_preset({'salesflare_server': 'https://stagingapi.salesflare.com'}))
        self.assertIsNone(onboarding.salesflare_preset({}))

    def test_another_salesflare_server_must_be_https(self):
        self.config(EXAMPLE['work-crm'])
        def get(source, path, params=None):
            raise onboarding.assistant().SourceError('salesflare', 401)
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', get), self.assertRaises(onboarding.SetupError) as caught:
            onboarding.connect(self.path, 'crm', FakeUI(answers=['http://stagingapi.example.test'], secrets=['k']), restart=False)
        self.assertIn('not an https address', caught.exception.message)
        for typed in ('ftp://x.test', 'https://u:p@x.test', 'https://x.test?k=1', 'https://'):
            self.assertRaises(onboarding.SetupError, onboarding.salesflare_address, typed)
        self.assertEqual(onboarding.salesflare_address('stagingapi.example.test/'), 'https://stagingapi.example.test')
        self.assertEqual(onboarding.salesflare_app_base('https://stagingapi.example.test'), 'https://staging.example.test')

    def test_the_same_salesflare_address_again_stops_instead_of_asking_forever(self):
        self.config(EXAMPLE['work-crm'])
        def get(source, path, params=None):
            raise onboarding.assistant().SourceError('salesflare', 401)
        with mock.patch.object(onboarding.assistant().Salesflare, 'get', get), self.assertRaises(onboarding.SetupError) as caught:
            onboarding.connect(self.path, 'crm', FakeUI(answers=['api.salesflare.com'] * 3, secrets=['k']), restart=False)
        self.assertIn('rejected that key', caught.exception.message)

    def test_dashboard_on_your_own_domain_counts(self):
        self.stub('tailscale', 'print("{}")')
        config = {'port': 8787, 'dashboard_host': 'my.example.test', 'dashboard_path': '/'}
        with mock.patch.object(onboarding, 'reachable', return_value=True) as probe:
            self.assertEqual(onboarding.dashboard_url(config), 'https://my.example.test/')
        probe.assert_called_once_with('https://my.example.test/')
        with mock.patch.object(onboarding, 'reachable', return_value=False):
            self.assertIsNone(onboarding.dashboard_url(config))

    def test_connect_lists_what_this_machine_has_but_no_source_reads(self):
        gog = self.stub('gog', 'print(json.dumps({"accounts": [{"email": "me@one.test"}, {"email": "new@two.test"}]}))')
        nodes = {'paired': [{'nodeId': 'p1', 'platform': 'android', 'displayName': 'Pixel'}, {'nodeId': 'p2', 'platform': 'android'}]}
        openclaw = self.stub('openclaw', f'print(json.dumps({nodes!r}))')
        self.stub('gh', 'print("octo")')
        config = self.config(dict(EXAMPLE['work-gmail'], email='me@one.test'), dict(EXAMPLE['personal-android'], node_id='p1'), gog=gog, openclaw=openclaw)
        found = dict(onboarding.discover(config))
        self.assertEqual(found, {'gog is signed in as new@two.test, but no source reads it': 'assistant connect gmail',
                                 'gh is signed in as octo': 'assistant connect github',
                                 'an Android phone is paired with OpenClaw': 'assistant connect android'})

    @unittest.skipUnless(PLUGINS, 'the RSS connector is a plugin')
    def test_a_plugin_source_asks_its_own_setup_fields(self):
        self.config()
        with mock.patch.object(onboarding.assistant().load_connector('rss'), 'fetch', return_value=(b'<rss><channel/></rss>', {})):
            onboarding.connect(self.path, 'rss', FakeUI(answers=['personal', 'https://example.com/feed.xml']), restart=False)
        account = self.saved()['accounts'][0]
        self.assertEqual((account['id'], account['service'], account['url'], account['boundary']),
                         ('feed', 'rss', 'https://example.com/feed.xml', 'personal:me'))
        with self.assertRaises(onboarding.SetupError) as raised:
            onboarding.connect(self.path, 'fax', FakeUI(), restart=False)
        self.assertIn('rss', raised.exception.fix)

    def test_revoked_github_connection_is_not_saved_as_connected(self):
        account = {'id': 'github', 'service': 'github', 'transport': 'gh', 'boundary': 'work:Acme', 'login': 'intended-owner', 'poll_seconds': 600}
        config = self.config(account)
        self.stub('gh', 'sys.stderr.write("HTTP 401: revoked sign-in\\n"); sys.exit(1)')
        before = self.path.read_text()
        ui = FakeUI()
        with self.assertRaises(onboarding.SetupError):
            onboarding.connect(self.path, 'github', ui, account_id='github', restart=False)
        self.assertEqual(self.path.read_text(), before)
        self.assertNotIn('connected', ui.text())

    def test_every_plugin_setup_is_complete(self):
        extra = [s for s in onboarding.sources() if s not in onboarding.SERVICES]
        self.assertIn('github', extra)
        for service in extra:
            setup = onboarding.setup_of(service)
            self.assertTrue(setup, service)
            self.assertEqual(setup['account']['service'], service)
            for key, prompt, *_ in setup.get('ask', []):
                self.assertTrue(key and prompt)

    # ------------------------------------------------------------ first sync, doctor

    def database(self, rows=(), events=(), checkpoints=()):
        with sqlite3.connect(self.data / 'events.sqlite') as db:
            db.execute('CREATE TABLE sync_activity(id TEXT, account_id TEXT, at TEXT, status TEXT, signals INTEGER, error TEXT, retry_at TEXT)')
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, account_id TEXT, occurred_at TEXT, deleted INTEGER DEFAULT 0)')
            db.execute('CREATE TABLE checkpoints(account_id TEXT, state TEXT, last_success TEXT, error TEXT, error_at TEXT, failures INTEGER, retry_at TEXT)')
            db.execute('CREATE TABLE analyses(id TEXT, started_at TEXT)')
            db.executemany('INSERT INTO sync_activity VALUES(?,?,?,?,?,?,NULL)', rows)
            db.executemany('INSERT INTO events(account_id, occurred_at) VALUES(?,?)', events)
            db.executemany('INSERT INTO checkpoints VALUES(?,?,?,?,?,0,NULL)', checkpoints)

    def test_first_sync_shows_counts_and_failures(self):
        stamp = lambda days: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
        since = stamp(0.01)
        self.database(rows=[('1', 'work-gmail', stamp(0), 'success', 3, None), ('2', 'work-slack', stamp(0), 'failed', None, 'slack_invalid_auth'),
                            ('3', 'home', stamp(1), 'success', 1, None)],
                      events=[('work-gmail', stamp(1)), ('work-gmail', stamp(2)), ('work-gmail', stamp(30))])
        ui = FakeUI()
        ok = onboarding.first_sync({'database': str(self.data / 'events.sqlite')}, ['work-gmail', 'work-slack', 'home'], since, ui, wait=0)
        self.assertFalse(ok)
        self.assertIn('✓ work-gmail: 3 signals so far (2 from the last 7 days)', ui.text())
        self.assertIn('✗ work-slack: slack_invalid_auth: Maton rejected the API key', ui.text())
        self.assertIn('… home: no sync finished yet', ui.text())

    def test_doctor_points_every_problem_at_its_fix(self):
        recent = dt.datetime.now(dt.timezone.utc).isoformat()
        gmail = dict(EXAMPLE['work-gmail'], email='alex@acme.test')
        config = self.config(gmail, EXAMPLE['personal-whatsapp'], openclaw=str(self.bin / 'missing'))
        config['credentials']['gog-password'] = str(self.data / 'gog-password')
        onboarding.write_private(self.path, json.dumps(config))
        onboarding.write_private(self.data / 'gog-password', 'x')
        onboarding.write_private(self.data / 'api-token', 'x')
        (self.data / 'owner-token').write_text('x')
        os.chmod(self.data / 'owner-token', 0o644)
        self.database(events=[('work-gmail', recent)], checkpoints=[('work-gmail', '{}', recent, None, None)])
        results = onboarding.doctor(self.path)
        text = '\n'.join(f'{m} {t} | {f}' for m, t, f in results)
        self.assertIn('✗ service not running', text)
        self.assertIn('✓ work-gmail (Gmail): synced just now, 1 signals', text)
        self.assertIn('✗ personal-whatsapp (WhatsApp): not set up (identity_sha256 is still the example value) | assistant connect whatsapp', text)
        self.assertIn('! credential owner-token is readable by others | chmod 600', text)
        self.assertIn('! reviews and Telegram cards are off | try one: assistant preview', text)
        self.assertIn('USER.md missing', text)
        ui = FakeUI()
        self.assertEqual(onboarding.print_doctor(results, ui), 1)
        self.assertIn('fix: assistant connect whatsapp', ui.text())

    def test_doctor_checks_the_telegram_bot_and_the_gateway_unit(self):
        (self.home / '.openclaw').mkdir()
        (self.home / '.openclaw/openclaw.json').write_text(json.dumps({'channels': {'telegram': {'accounts': {
            'family': {'botToken': 'x', 'allowFrom': ['5']}, 'work': {'botToken': 'y', 'allowFrom': ['1000001']}}}}}))
        self.config(dict(EXAMPLE['openclaw-gateway'], unit='openclaw-gateway.service'), openclaw=str(self.bin / 'missing'))
        text = '\n'.join(f'{m} {t} | {f}' for m, t, f in onboarding.doctor(self.path))
        self.assertIn('✗ telegram_account default is not a Telegram account in OpenClaw (there: family, work) | set "telegram_account": "work"', text)
        self.assertIn('✗ openclaw-gateway (OpenClaw Gateway log): no systemd unit openclaw-gateway.service here', text)
        self.stub('systemctl', 'print("openclaw-gateway.service enabled enabled" if "list-unit-files" in args else "inactive")')
        self.config(dict(EXAMPLE['openclaw-gateway'], unit='openclaw-gateway.service'), telegram_account='family', openclaw=str(self.bin / 'missing'))
        text = '\n'.join(f'{m} {t} | {f}' for m, t, f in onboarding.doctor(self.path))
        self.assertIn('! you are approved on the Telegram account work, but cards go out through family', text)
        self.assertNotIn('no systemd unit', text)

    def test_doctor_notices_when_openclaw_skips_the_review_hooks(self):
        self.stub('openclaw', 'print(json.dumps({"plugins": [{"id": "personal-assistant-bridge", "enabled": True, "status": "loaded"}]}))')
        (self.home / '.openclaw').mkdir()
        settings = self.home / '.openclaw/openclaw.json'
        settings.write_text(json.dumps({'plugins': {'entries': {'personal-assistant-bridge': {'enabled': True}}}}))
        self.config(openclaw=str(self.bin / 'openclaw'))
        text = '\n'.join(f'{m} {t} | {f}' for m, t, f in onboarding.doctor(self.path))
        self.assertIn("✗ OpenClaw skips the bridge plugin's review hooks, so reviews never finish | "
                      'openclaw config set plugins.entries.personal-assistant-bridge.hooks.allowConversationAccess true --strict-json', text)
        settings.write_text(json.dumps({'plugins': {'entries': {'personal-assistant-bridge': {'enabled': True, 'hooks': {'allowConversationAccess': True}}}}}))
        text = '\n'.join(f'{m} {t} | {f}' for m, t, f in onboarding.doctor(self.path))
        self.assertIn('✓ OpenClaw bridge plugin loaded', text)

    def test_only_the_service_that_reads_this_config_is_restarted(self):
        self.stub('systemctl', f'''
if "is-active" in args: print("active")
elif "show" in args: print("HOME=/home/someone-else PATH=/usr/bin")''')
        self.assertTrue(onboarding.service_active())
        self.assertFalse(onboarding.service_active(self.path))  # same default path, but under this test's HOME
        self.assertTrue(onboarding.service_active('/home/someone-else/.local/share/personal-assistant/config.json'))
        ui = FakeUI()
        self.assertFalse(onboarding.restart_and_sync({'port': 1}, ['a'], ui, config_path=self.path))
        self.assertNotIn('restart', ' '.join(line for line in self.calls().splitlines() if line.startswith('systemctl --user restart')))
        self.assertIn('reads another config', ui.text())
        self.stub('systemctl', f'''
if "is-active" in args: print("active")
elif "show" in args: print('"ASSISTANT_CONFIG={self.path}" HOME=/elsewhere')''')
        self.assertTrue(onboarding.service_active(self.path))

    def test_dashboard_is_found_in_tailscale_serve(self):
        status = {'TCP': {'443': {'HTTPS': True}}, 'Web': {'box.example.ts.net:443': {'Handlers': {'/assistant': {'Proxy': 'http://127.0.0.1:8787'}}}}}
        self.stub('tailscale', f'print(json.dumps({status!r}))')
        self.assertEqual(onboarding.dashboard_served({'port': 8787}), 'https://box.example.ts.net/assistant/')
        self.assertIsNone(onboarding.dashboard_served({'port': 9999}))
        self.assertEqual(onboarding.serve_command({'dashboard_path': '/assistant/', 'port': 8787})[-3:],
                         ['--set-path', '/assistant', 'http://127.0.0.1:8787'])
        # New installs: the dashboard on its own port, so its phone app is its own origin.
        status = {'Web': {'box.example.ts.net:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8787'}}}}}
        self.stub('tailscale', f'print(json.dumps({status!r}))')
        self.assertEqual(onboarding.dashboard_served({'port': 8787}), 'https://box.example.ts.net:8443/')
        self.assertEqual(onboarding.dashboard_url({'port': 8787, 'dashboard_host': 'box.example.ts.net:8443', 'dashboard_path': '/'}),
                         'https://box.example.ts.net:8443/')
        self.assertEqual(onboarding.serve_command({'dashboard_host': 'box.example.ts.net:8443', 'dashboard_path': '/', 'port': 8787}),
                         ['tailscale', 'serve', '--bg', '--https=8443', '--set-path', '/', 'http://127.0.0.1:8787'])

    def test_tailscale_serve_says_what_is_in_the_way(self):
        self.stub('tailscale', '''
if args[:2] == ["serve", "status"]: print("{}")
else: print("Serve is not enabled on your tailnet. To enable, visit: https://login.tailscale.com/f/serve?node=abc"); sys.exit(1)''')
        ui = FakeUI()
        self.assertIsNone(onboarding.serve_dashboard({'port': 8787, 'dashboard_path': '/assistant/'}, ui))
        self.assertIn('Enable it at https://login.tailscale.com/f/serve?node=abc', ui.text())
        self.stub('tailscale', '''
if args[:2] == ["serve", "status"]: print("{}")
else: sys.stderr.write("Access denied: serve config denied"); sys.exit(1)''')
        ui = FakeUI()
        onboarding.serve_dashboard({'port': 8787}, ui)
        self.assertIn('sudo tailscale set --operator=$USER', ui.text())

    def test_starter_user_md_is_written_once_from_the_answers(self):
        config = {'owner_name': 'Alex Owner', 'timezone': 'Europe/Brussels',
                  'accounts': [dict(EXAMPLE['work-gmail'], email='alex@acme.test'), dict(EXAMPLE['personal-whatsapp'])]}
        workspace = self.home / 'workspace'
        self.assertTrue(onboarding.offer_user_md(config, FakeUI(), workspace))
        text = (workspace / 'USER.md').read_text()
        for expected in ('Name: Alex Owner', 'Time zone: Europe/Brussels', 'Work: Acme', 'Email: alex@acme.test', 'Sign-off: Alex'):
            self.assertIn(expected, text)
        (workspace / 'USER.md').write_text('mine')
        self.assertFalse(onboarding.offer_user_md(config, FakeUI(), workspace))
        self.assertEqual((workspace / 'USER.md').read_text(), 'mine')
        self.assertFalse(onboarding.offer_user_md(config, onboarding.UI(False), self.home / 'other'))

    def test_the_openclaw_template_user_md_still_gets_an_about_me(self):
        workspace = self.home / 'seeded'
        workspace.mkdir()
        template = '# USER.md - User Model\n\n## Directives\n\n<!-- observed: YYYY-MM-DD | status: active -->\n\n- Prefer ...\n'
        (workspace / 'USER.md').write_text(template)
        self.assertTrue(onboarding.offer_user_md({'owner_name': 'Alex Owner', 'accounts': []}, FakeUI(), workspace))
        text = (workspace / 'USER.md').read_text()
        self.assertTrue(text.startswith('# USER.md - User Model'))
        self.assertIn('Name: Alex Owner', text)
        self.assertFalse(onboarding.offer_user_md({'accounts': []}, FakeUI(), workspace))

    def test_a_profile_draft_fills_the_starter_blanks_and_never_overwrites_the_owner(self):
        config = {'owner_name': 'Alex Owner', 'accounts': [dict(EXAMPLE['work-gmail'], email='alex@acme.test')]}
        starter = onboarding.starter_user_md(config)
        path = self.home / 'profile' / 'USER.md'
        path.parent.mkdir()
        path.write_text(starter)
        self.assertFalse(onboarding.says_how_you_write(path))
        draft = {'role': 'Founder at Acme', 'tone': 'Short and friendly', 'languages': ['Dutch: team', 'English: customers'],
                 'examples': ['Thanks, sounds good'], 'matters': ['Sam (Globex): weekly call'], 'based_on': '12 sent emails'}
        merged = onboarding.merge_profile(starter, draft, '2026-10-17')
        for gone in ('- Role: \n', '- Languages: \n', '(for example: short and friendly', '(customers, projects, people'):
            self.assertNotIn(gone, merged)
        for kept in ('Name: Alex Owner', 'Sign-off: Alex', '## Leave alone', '- Role: Founder at Acme', '- Languages: Dutch: team; English: customers',
                     '"Thanks, sounds good"', '### What matters\n- Sam (Globex): weekly call', '2026-10-17 · from 12 sent emails'):
            self.assertIn(kept, merged)
        path.write_text(merged)
        self.assertTrue(onboarding.says_how_you_write(path))
        # A later draft replaces only its own section, while the marker line is still there.
        mine = merged.replace('## Leave alone', '## Leave alone\n\n- Recruiters')
        again = onboarding.merge_profile(mine, {'tone': 'Warm'}, '2026-11-01')
        self.assertEqual(again.count(onboarding.PROFILE_HEADING), 1)
        self.assertIn('- Recruiters', again)
        self.assertIn('- Tone: Warm', again)
        self.assertNotIn('Founder at Acme', again)
        # Once the owner deletes the marker, the section is theirs.
        adopted = re.sub(r'^<!-- assistant profile.*\n', '', merged, flags=re.M)
        self.assertIsNone(onboarding.merge_profile(adopted, {'tone': 'Warm'}, '2026-11-01'))
        # Text the owner wrote under their own headings stays word for word.
        own = '# About me\n\n- Tone: blunt, always in English\n\n## Food\n\n- vegetarian\n'
        self.assertTrue(onboarding.merge_profile(own, draft, '2026-10-17').startswith(own.rstrip()))
        self.assertTrue(onboarding.says_how_you_write(self.home / 'missing.md') is False)

    def test_a_profile_draft_fits_in_what_openclaw_loads(self):
        own = '# About me\n\n' + '- A rule I wrote myself and want kept.\n' * 95  # about 3,700 characters
        draft = {'tone': 'Short and friendly', 'habits': 'Shares a booking link instead of trading times.',
                 'examples': ['Thanks, sounds good', 'Works for me'], 'matters': [f'Customer {n}: weekly call about the rollout' for n in range(8)],
                 'based_on': '30 email, 20 Slack'}
        self.assertEqual(onboarding.profile_room(own), 4000 - len(own.rstrip()) - 2)
        merged, left_out = onboarding.fit_profile(own, draft, '2026-10-17')
        self.assertLessEqual(len(merged), 4000)
        self.assertTrue(merged.startswith(own.rstrip()))
        self.assertIn('- Tone: Short and friendly', merged)
        self.assertEqual(left_out[:2], ['examples: Works for me', 'examples: Thanks, sounds good'])  # examples go first, last ones first
        self.assertIn('matters: Customer 7: weekly call about the rollout', left_out)
        self.assertTrue(all(line.startswith(('examples', 'matters')) for line in left_out))
        # An earlier draft section doesn't count against the room; the owner's own text always stays.
        self.assertEqual(onboarding.profile_room(merged), onboarding.profile_room(own))
        tiny, gone = onboarding.fit_profile(own * 2, draft, '2026-10-17')
        self.assertIn('- Tone: Short and friendly', tiny)  # never less than one line
        self.assertTrue(tiny.startswith((own * 2).rstrip()))
        self.assertEqual(onboarding.fit_profile('## Observed from my messages\n- mine\n', draft, '2026-10-17'), (None, []))
        # Per-channel voices: their own block, the tone blank goes, and the least used channel is cut first.
        voices = {'tone': 'Warm', 'voices': {'Email': 'Hey [name]; Regards, Alex', 'Slack': 'terse, lowercase', 'WhatsApp': 'emoji, Dutch'}}
        starter = onboarding.starter_user_md({'owner_name': 'Alex', 'accounts': []})
        merged = onboarding.merge_profile(starter, {'voices': voices['voices']}, '2026-10-17')
        self.assertIn('### Per channel\n- Email: Hey [name]; Regards, Alex\n- Slack: terse, lowercase\n- WhatsApp: emoji, Dutch', merged)
        self.assertNotIn('(for example: short and friendly', merged)
        fitted, cut = onboarding.fit_profile(own + '- one more of mine\n' * 4, voices, '2026-10-17')
        self.assertLessEqual(len(fitted), 4000)
        self.assertEqual(cut[0], 'WhatsApp voice: emoji, Dutch')
        self.assertIn('- Email: Hey [name]; Regards, Alex', fitted)


class WhatsAppSyncTest(unittest.TestCase):
    setUp, stub, calls = OnboardingTest.setUp, OnboardingTest.stub, OnboardingTest.calls

    def test_linking_starts_one_sync_unit_unless_the_store_is_already_synced(self):
        store = self.home / 'wa'
        store.mkdir()
        wacli = self.stub('wacli', 'print(json.dumps({"success": True, "data": {"authenticated": True, "linked_jid": "1@s.whatsapp.net"}}))')
        account = {'id': 'personal-whatsapp', 'service': 'whatsapp', 'store': str(store)}
        onboarding.step_whatsapp({'wacli': wacli}, account, FakeUI(answers=[str(store)]))
        unit = self.home / '.config/systemd/user/the-machine-wacli-personal-whatsapp.service'
        text = unit.read_text()
        self.assertIn(f'ExecStart={wacli} --store {store} sync --follow', text)
        self.assertNotIn('read-only', text)
        self.assertIn('systemctl --user enable --now the-machine-wacli-personal-whatsapp.service', self.calls())
        unit.unlink()
        import portable
        with open(store / 'LOCK', 'a') as held:
            portable.lock(held)
            ui = FakeUI(answers=[str(store)])
            onboarding.step_whatsapp({'wacli': wacli}, account, ui)
        self.assertFalse(unit.exists())
        self.assertIn('another wacli already keeps this store synced', ui.text())


if __name__ == '__main__':
    unittest.main()
