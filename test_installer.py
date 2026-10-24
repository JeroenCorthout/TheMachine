"""install.sh / installer.py against a temporary HOME with stub openclaw and systemctl.
Nothing here touches the real user's systemd, OpenClaw or data directory."""
import builtins, json, os, shutil, socket, stat, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

import installer

ROOT = Path(__file__).resolve().parent
STUB = '''#!/bin/sh
echo "{name} $*" >> "{log}"
if [ "$*" = "$ASSISTANT_TEST_FAIL" ]; then echo "Synthetic failure" >&2; exit 1; fi
case "$*" in --version) echo "OpenClaw 2026.10.5 (test)";; *list-unit-files*) echo "openclaw-gateway.service enabled enabled";; esac
exit 0
'''
ANSWERS = {'ASSISTANT_OWNER_NAME': 'Alex Owner', 'ASSISTANT_TELEGRAM_OWNER': '1000001',
           'ASSISTANT_TAILSCALE_OWNER': 'alex@example.com', 'ASSISTANT_DASHBOARD_HOST': 'machine.example-tailnet.ts.net',
           'ASSISTANT_TIMEZONE': 'Europe/Amsterdam', 'ASSISTANT_CONNECTORS': 'gmail,calendar,gateway',
           'ASSISTANT_EMAIL': 'alex@example.com', 'ASSISTANT_GOG_CLIENT': 'work', 'ASSISTANT_BOUNDARY': 'work:Acme'}


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class InstallerTest(unittest.TestCase):
    def test_plugin_enable_is_retried_while_the_gateway_applies_the_install(self):
        calls, tries = [], iter([1, 0])
        def run(command, **kwargs):
            calls.append(command[1:3])
            code = next(tries) if command[1:3] == ['plugins', 'enable'] else 0
            return subprocess.CompletedProcess(command, code, '', 'The CLI command failed.' if code else '')
        with mock.patch.object(installer, 'run', side_effect=run), mock.patch.object(installer.time, 'sleep'), \
             mock.patch.object(installer, 'say'):
            self.assertTrue(installer.install_plugin({'openclaw': 'openclaw'}))
        self.assertEqual(calls.count(['plugins', 'enable']), 2)

    def test_an_unchanged_bridge_is_not_reinstalled_or_reloaded(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command[1:3])
            return subprocess.CompletedProcess(command, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(installer, 'run', side_effect=run), \
                mock.patch.object(installer, 'say'):
            stamp = Path(tmp) / 'plugin-installed'
            facts = {'openclaw': 'openclaw', 'openclaw_version': '2026.10.5'}
            self.assertTrue(installer.install_plugin(facts, stamp=stamp))
            self.assertIn(['plugins', 'reload'], calls)
            calls.clear()
            self.assertTrue(installer.install_plugin(facts, stamp=stamp))  # an update that did not touch it
            self.assertEqual(calls, [])
            self.assertTrue(installer.install_plugin({**facts, 'openclaw_version': '2026.11.1'}, stamp=stamp))
            self.assertIn(['plugins', 'reload'], calls)  # a new OpenClaw gets it again
            calls.clear()
            self.assertTrue(installer.install_plugin(facts, reload=False, stamp=stamp))
            self.assertNotIn(['plugins', 'reload'], calls)

    def gateway(self, reload_code=1, health=(), unit='openclaw-gateway.service', restart_code=0):
        """install_plugin against a fake OpenClaw whose reload fails and whose Gateway health answers `health` in turn."""
        calls, answers = [], iter(health)
        def run(command, **kwargs):
            calls.append(command)
            if command[1:3] == ['plugins', 'reload']:
                return subprocess.CompletedProcess(command, reload_code, '', '[openclaw] The CLI command failed.' if reload_code else '')
            if command[1:3] == ['gateway', 'health']:
                found = next(answers, None)
                return subprocess.CompletedProcess(command, 0 if found else 1, json.dumps(found) if found else '', '')
            if command[:3] == ['systemctl', '--user', 'restart']:
                return subprocess.CompletedProcess(command, restart_code, '', 'Job failed' if restart_code else '')
            return subprocess.CompletedProcess(command, 0, '', '')
        said = []
        tmp = Path(tempfile.mkdtemp(prefix='machine-plugin-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        stamp = tmp / 'plugin-installed'
        with mock.patch.object(installer, 'run', side_effect=run), mock.patch.object(installer.time, 'sleep'), \
                mock.patch.object(installer, 'say', side_effect=said.append), \
                mock.patch.object(installer.onboarding, 'gateway_units', return_value=[unit] if unit else []), \
                mock.patch.dict(os.environ, {'ASSISTANT_GATEWAY_WAIT': '60'}):
            ok = installer.install_plugin({'openclaw': 'openclaw', 'openclaw_version': '2026.10.5'}, stamp=stamp)
        return ok, calls, '\n'.join(said), stamp.exists()

    def test_a_reload_without_a_running_gateway_starts_it_and_checks_the_bridge_loaded(self):
        loaded = {'ok': True, 'plugins': {'loaded': ['telegram', installer.PLUGIN_ID], 'errors': []}}
        ok, calls, said, stamped = self.gateway(health=[None, {'ok': True, 'plugins': {'loaded': ['telegram']}}, loaded])
        self.assertTrue(ok, said)
        self.assertTrue(stamped)
        self.assertIn(['systemctl', '--user', 'reset-failed', 'openclaw-gateway.service'], calls)
        self.assertIn(['systemctl', '--user', 'restart', 'openclaw-gateway.service'], calls)
        self.assertIn('loaded', said)

    def test_a_gateway_that_does_not_load_the_bridge_fails_with_its_reason(self):
        broken = {'ok': True, 'plugins': {'loaded': ['telegram'], 'errors': [{'id': installer.PLUGIN_ID, 'error': 'entry not found'}]}}
        ok, _, said, stamped = self.gateway(health=[broken])
        self.assertFalse(ok)
        self.assertFalse(stamped)
        self.assertIn('did not load ' + installer.PLUGIN_ID + ': entry not found', said)

    def test_a_gateway_that_does_not_start_fails_with_what_to_check(self):
        ok, _, said, _ = self.gateway(health=[], restart_code=1)
        self.assertFalse(ok)
        self.assertIn('Could not start the OpenClaw Gateway', said)
        with mock.patch.object(installer.time, 'time', side_effect=[0, 0, 100, 100]):
            ok, _, said, _ = self.gateway(health=[])
        self.assertFalse(ok)
        self.assertIn('did not come up', said)

    def test_without_a_gateway_service_the_saved_bridge_is_enough(self):
        ok, calls, said, stamped = self.gateway(health=[], unit=None)
        self.assertTrue(ok, said)
        self.assertFalse(stamped)  # not live yet: the next install reloads it
        self.assertNotIn('restart', [c[2] for c in calls if c[0] == 'systemctl'])
        self.assertIn('loads when it starts', said)

    def test_a_running_gateway_that_refuses_the_reload_without_a_service_fails(self):
        ok, _, said, _ = self.gateway(health=[{'ok': True, 'plugins': {'loaded': []}}], unit=None)
        self.assertFalse(ok)
        self.assertIn('plugin reload failed', said)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='machine-install-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.bin, self.log = self.tmp / 'home', self.tmp / 'bin', self.tmp / 'calls.log'
        self.home.mkdir()
        self.bin.mkdir()
        for name in ('openclaw', 'systemctl', 'journalctl'):
            path = self.bin / name
            path.write_text(STUB.format(log=self.log, name=name))
            path.chmod(0o755)
        package = self.tmp / 'lib/node_modules/openclaw'
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name':'openclaw','version':'2026.10.5'}))
        (self.bin / 'node').symlink_to(shutil.which('node'))
        self.data = self.home / '.local/share/personal-assistant'
        self.config = self.data / 'config.json'

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ASSISTANT_', 'OPENCLAW_'))}
        env.update(HOME=str(self.home), PATH=str(self.bin), ASSISTANT_PLUGIN_RETRY_WAIT='0',  # no real Gateway to wait for
                   ASSISTANT_SERVICE_MANAGER='systemd')  # the stubs are systemd's, also on macOS
        env.update(ANSWERS)
        env.update(extra)
        return env

    def install(self, *args, **extra):
        return subprocess.run([sys.executable, str(ROOT / 'installer.py'), '--non-interactive', '--health-timeout', '0', *args],
                              env=self.env(**extra), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_non_interactive_install_writes_config_secrets_unit_plugin_and_cli(self):
        result = self.install('--no-start')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = json.loads(self.config.read_text())
        self.assertEqual((config['owner_name'], config['telegram_owner'], config['tailscale_owner']),
                         ('Alex Owner', '1000001', 'alex@example.com'))
        self.assertEqual(config['dashboard_host'], 'machine.example-tailnet.ts.net:8443')  # its own origin
        self.assertEqual(config['dashboard_path'], '/')
        self.assertEqual([a['id'] for a in config['accounts']], ['work-gmail', 'work-calendar', 'openclaw-gateway'])
        gmail = config['accounts'][0]
        self.assertEqual((gmail['email'], gmail['client'], gmail['boundary']), ('alex@example.com', 'work', 'work:Acme'))
        self.assertNotIn('exclude', gmail)
        self.assertEqual(config['app_dir'], str(ROOT))
        self.assertEqual(config['database'], str(self.data / 'events.sqlite'))
        self.assertFalse(config['delivery_enabled'])
        self.assertFalse(config['review_worker']['enabled'])
        self.assertTrue(Path(config['openclaw_package_dir'], 'package.json').exists())
        self.assertNotIn('~', json.dumps(config))
        self.assertEqual(self.mode(self.data), 0o700)
        for name in ('config.json', 'api-token', 'owner-token', 'vapid.json'):
            self.assertEqual(self.mode(self.data / name), 0o600, name)
        self.assertEqual(len((self.data / 'api-token').read_text()), 64)
        unit = (self.home / '.config/systemd/user/personal-assistant.service').read_text()
        self.assertIn(f'ExecStart={sys.executable} {ROOT}/assistant.py serve', unit)
        self.assertIn(f'WorkingDirectory={ROOT}', unit)
        self.assertEqual((self.home / '.local/bin/assistant').resolve(), ROOT / 'cli.py')
        cli = subprocess.run([sys.executable, str(self.home / '.local/bin/assistant'), '--help'], capture_output=True, text=True)
        self.assertIn('usage: assistant', cli.stdout, cli.stderr)
        calls = self.calls()
        self.assertIn(f'openclaw plugins install --link --force --accept-capabilities {ROOT}/openclaw-plugin', calls)
        self.assertIn('openclaw plugins enable personal-assistant-bridge', calls)
        # Without this OpenClaw skips the bridge's conversation hooks and reviews never bind to their run.
        self.assertIn('openclaw config set plugins.entries.personal-assistant-bridge.hooks.allowConversationAccess true --strict-json', calls)
        self.assertIn('systemctl --user daemon-reload', calls)
        self.assertFalse(any('enable personal-assistant.service' in c for c in calls), calls)  # --no-start
        self.assertIn('Next steps', result.stdout)
        self.assertIn('Connect Gmail: assistant connect gmail', result.stdout)
        self.assertIn('Connect Google Calendar: assistant connect calendar', result.stdout)
        self.assertNotIn('Connect OpenClaw Gateway', result.stdout)
        self.assertIn('assistant doctor', result.stdout)

    def test_rerun_upgrades_without_clobbering_config_or_secrets(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        token = (self.data / 'api-token').read_text()
        vapid = (self.data / 'vapid.json').read_text()
        config = json.loads(self.config.read_text())
        config['owner_name'] = 'Changed By Hand'
        config['accounts'][0]['exclude'] = [{'senders': ['noise@example.com']}]
        config['port'] = free_port()
        del config['weekly_recap']
        self.config.write_text(json.dumps(config))
        result = self.install(ASSISTANT_OWNER_NAME='Someone Else', ASSISTANT_CONNECTORS='gateway')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        again = json.loads(self.config.read_text())
        self.assertEqual(again['owner_name'], 'Changed By Hand')
        self.assertEqual(again['accounts'][0]['exclude'], [{'senders': ['noise@example.com']}])
        self.assertEqual(len(again['accounts']), 3)
        self.assertEqual(again['weekly_recap'], {'enabled': False, 'weekday': 5, 'at': '16:00'})  # missing default restored
        self.assertEqual(self.mode(self.config), 0o600)
        self.assertEqual((self.data / 'api-token').read_text(), token)
        self.assertEqual((self.data / 'vapid.json').read_text(), vapid)
        self.assertIn('Keeping existing', result.stdout)
        self.assertIn('already present, unchanged', result.stdout)
        calls = self.calls()
        self.assertIn('systemctl --user enable personal-assistant.service', calls)
        self.assertIn('systemctl --user restart personal-assistant.service', calls)
        self.assertIn('not answering yet', result.stdout)  # nothing listens on the free port
        before = self.config.read_text()
        self.assertEqual(self.install().returncode, 1)
        self.assertEqual(self.config.read_text(), before)

    def test_telegram_is_optional(self):
        result = self.install('--no-start', ASSISTANT_TELEGRAM_OWNER='')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('No Telegram', result.stdout)
        config = json.loads(self.config.read_text())
        self.assertEqual(config['telegram_owner'], '')
        self.assertNotIn('telegram_account', config)

    def test_missing_answers_fail_clearly_without_writing(self):
        result = self.install('--no-start', ASSISTANT_TAILSCALE_OWNER='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tailscale', result.stdout + result.stderr)
        self.assertFalse(self.config.exists())
        result = self.install('--no-start', ASSISTANT_TELEGRAM_OWNER='@alex')
        self.assertIn('numeric chat ID', result.stdout + result.stderr)
        result = self.install('--no-start', ASSISTANT_CONNECTORS='gmail,fax')
        self.assertIn('Unknown sources: fax', result.stdout + result.stderr)

    def test_upgrades_never_take_the_examples_identity(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        config = json.loads(self.config.read_text())
        for key in ('dashboard_host', 'timezone'):
            del config[key]
        self.config.write_text(json.dumps(config))
        result = self.install('--no-start')
        again = json.loads(self.config.read_text())
        self.assertNotIn('dashboard_host', again)
        self.assertEqual(again['timezone'], installer.local_timezone())
        self.assertIn('dashboard_host is empty', subprocess.run(
            [sys.executable, str(ROOT / 'cli.py'), '--config', str(self.config), 'doctor'], env=self.env(), capture_output=True, text=True).stdout)

    def test_bad_answers_and_a_missing_tailscale_fail_before_writing(self):
        result = self.install('--no-start', ASSISTANT_TIMEZONE='Mars/Olympus')
        self.assertIn('Unknown time zone', result.stdout + result.stderr)
        result = self.install('--no-start', ASSISTANT_TAILSCALE_OWNER='')
        self.assertIn('FAIL  tailscale', result.stdout)
        self.assertIn('https://tailscale.com/download', result.stdout)
        self.assertFalse(self.config.exists())

    def test_a_personal_setup_a_named_bot_and_no_gateway_unit(self):
        home = Path(tempfile.mkdtemp(prefix='machine-wizard-'))
        self.addCleanup(shutil.rmtree, home, True)
        (home / '.openclaw').mkdir()
        (home / '.openclaw/openclaw.json').write_text(json.dumps({'channels': {'telegram': {'accounts': {
            'family': {'botToken': 'x', 'allowFrom': ['5']}, 'mine': {'botToken': 'y', 'allowFrom': ['tg:1000001']}}}}}))
        replies = iter(['Alex Owner', 'alex', '1000001', 'alex@example.com', 'https://box', 'box.example-tailnet.ts.net',
                        'Mars/Olympus', 'Europe/Brussels', 'gmail,fax', 'gmail,gateway', 'nope', 'alex@gmail.com', ''])
        with mock.patch.dict(os.environ, {'HOME': str(home)}, clear=False), \
             mock.patch.dict(os.environ, {k: '' for k in installer.ENV.values()}), \
             mock.patch.object(builtins, 'input', lambda prompt='': next(replies)), \
             mock.patch.object(installer.onboarding, 'gateway_unit', return_value=None), \
             mock.patch.object(installer, 'say') as said:
            values = installer.answers(True, {})
            config = installer.new_config(values, {}, installer.Paths(home))
        self.assertEqual(next(replies, 'done'), 'done')
        said = ' '.join(str(c.args[0]) for c in said.call_args_list)
        for problem in ('not a Telegram user ID', 'a host name', 'unknown time zone', 'unknown: fax', 'an email address', 'skipped the Gateway'):
            self.assertIn(problem, said)
        self.assertEqual((values['telegram_owner'], values['timezone'], values['boundary']), ('1000001', 'Europe/Brussels', 'personal:me'))
        self.assertEqual([(a['id'], a['boundary']) for a in config['accounts']], [('personal-gmail', 'personal:me')])
        self.assertEqual(config['telegram_account'], 'mine')

    def test_tagged_machines_spaces_and_the_cli_path(self):
        status = {'Self': {'UserID': 7, 'DNSName': 'box.example-tailnet.ts.net.'}, 'User': {'7': {'LoginName': 'tagged-devices'}}}
        with mock.patch.object(installer, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(status), '')):
            self.assertEqual(installer.tailscale_identity('tailscale'), {'tailscale_owner': '', 'dashboard_host': 'box.example-tailnet.ts.net:8443'})
        self.assertEqual([installer.dashboard_address(h) for h in ('m.tail.ts.net', 'https://m.tail.ts.net/', 'm.tail.ts.net:443', 'my.example.com')],
                         ['m.tail.ts.net:8443', 'm.tail.ts.net:8443', 'm.tail.ts.net:443', 'my.example.com'])
        unit = installer.unit_text({}, {'python': '/opt/my python/bin/python3'}, '/tmp/c.json')
        self.assertIn('ExecStart="/opt/my python/bin/python3" ', unit)
        self.assertIn('Environment="PATH=', unit)
        result = self.install('--no-start')
        self.assertIn('is not on your PATH', result.stdout)
        self.assertIn(str(self.home / '.local/bin/assistant'), result.stdout)

    def test_missing_openclaw_is_reported_with_a_fix(self):
        (self.bin / 'openclaw').unlink()
        result = self.install('--no-start')
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL  openclaw CLI not found', result.stdout)
        self.assertIn('npm install -g openclaw@2026.10.5', result.stdout)
        self.assertFalse(self.config.exists())

    def test_without_openclaw_it_offers_to_install_it_and_walks_through_its_setup(self):
        settings = self.tmp / 'openclaw/openclaw.json'
        npm_prefix = self.tmp / 'root-owned'  # npm's global folder, not ours: install into ~/.local, never sudo
        npm_prefix.mkdir(); npm_prefix.chmod(0o555); self.addCleanup(npm_prefix.chmod, 0o755)
        ran, said = [], []

        class UI(installer.onboarding.UI):
            def __init__(self, answers): self.answers = answers
            def say(self, text=''): said.append(text)
            def yes(self, prompt, default=True):
                said.append(prompt)
                return self.answers.pop(0)

        def run(args, check=False, **kwargs):
            ran.append(args)
            name = Path(args[0]).name
            if name == 'node':
                return subprocess.CompletedProcess(args, 0, 'v24.16.0\n', '')
            if args[1:] == ['prefix', '-g']:
                return subprocess.CompletedProcess(args, 0, f'{npm_prefix}\n', '')
            if args[1:3] == ['install', '-g']:
                binary = Path(args[args.index('--prefix') + 1]) / 'bin/openclaw'
                binary.parent.mkdir(parents=True); binary.write_text('#!/bin/sh\n'); binary.chmod(0o755)
                return subprocess.CompletedProcess(args, 0, '', 'npm warn install-scripts Run `npm install -g --allow-scripts=esbuild,koffi,openclaw`'
                                                   ' to allow these scripts once\n')
            if args[1:] == ['onboard', '--install-daemon']:  # OpenClaw's own wizard: model, Gateway, Telegram bot
                settings.parent.mkdir(exist_ok=True)
                settings.write_text(json.dumps({'channels': {'telegram': {'botToken': 'x'}}}))
            return subprocess.CompletedProcess(args, 0, '', '')

        which = lambda name: {'node': str(self.bin / 'node'), 'npm': str(self.bin / 'npm')}.get(name)
        with mock.patch.dict(os.environ, {'HOME': str(self.home), 'OPENCLAW_CONFIG_PATH': str(settings), 'PATH': str(self.bin)}), \
             mock.patch.object(installer, 'run', side_effect=run), mock.patch.object(installer.shutil, 'which', side_effect=which), \
             mock.patch.object(installer, 'say'), mock.patch.object(installer.openclaw_pin, 'check') as pinned:
            installer.offer_openclaw(UI([False]))  # no: nothing installed
            self.assertFalse(any(a[1:3] == ['install', '-g'] for a in ran))
            pinned.side_effect = installer.openclaw_pin.PinMismatch('the registry checksum does not match')
            installer.offer_openclaw(UI([True]))  # a checksum that doesn't match the pin: nothing installed either
            self.assertFalse(any(a[1:3] == ['install', '-g'] for a in ran))
            self.assertIn('was not installed: the registry checksum does not match', '\n'.join(said))
            pinned.side_effect = None
            installer.offer_openclaw(UI([True, True]))
            pinned.assert_called_with('2026.10.5')
            self.assertIn(str(self.home / '.local/bin'), os.environ['PATH'])  # the rest of this run finds it
        local = self.home / '.local'
        self.assertIn([str(self.bin / 'npm'), 'install', '-g', 'openclaw@2026.10.5', '--prefix', str(local)], ran)
        # npm 11 held back the install scripts: exactly those run for this install.
        self.assertIn([str(self.bin / 'npm'), 'rebuild', '-g', '--prefix', str(local), '--allow-scripts=esbuild,koffi,openclaw'], ran)
        self.assertIn([str(local / 'bin/openclaw'), 'onboard', '--install-daemon'], ran)
        self.assertIn([str(local / 'bin/openclaw'), 'health'], ran)
        self.assertFalse(any('channels' in a for a in ran))  # the wizard already added the bot
        text = '\n'.join(said)
        self.assertIn('Install OpenClaw 2026.10.5 now? (runs: npm install -g openclaw@2026.10.5 --prefix', text)
        self.assertIn('@BotFather', text)
        self.assertIn('Gateway: say yes to running it as a service', text)

        # Set up, but no Telegram bot: only that step. Set up with a bot: not a single question.
        settings.write_text(json.dumps({'gateway': {}}))
        (self.bin / 'openclaw').chmod(0o755)
        ran.clear(); said.clear()
        which = lambda name: str(self.bin / name) if name == 'openclaw' else None
        with mock.patch.dict(os.environ, {'OPENCLAW_CONFIG_PATH': str(settings)}), mock.patch.object(installer, 'run', side_effect=run), \
             mock.patch.object(installer.shutil, 'which', side_effect=which):
            installer.offer_openclaw(UI([True]))
            self.assertEqual(ran[:1], [[str(self.bin / 'openclaw'), 'channels', 'add', '--channel', 'telegram']], said)
            settings.write_text(json.dumps({'channels': {'telegram': {'accounts': {'work': {'botToken': 'x'}}}}}))
            ran.clear(); said.clear()
            installer.offer_openclaw(UI([]))
            self.assertEqual((ran, said), ([], []))

    def test_without_node_it_says_what_to_install_first(self):
        said = []
        ui = installer.onboarding.UI(); ui.say = said.append
        ui.yes = mock.Mock(side_effect=AssertionError('nothing to ask'))
        with mock.patch.object(installer.shutil, 'which', return_value=None):
            installer.offer_openclaw(ui)
        self.assertIn('Node.js', said[0])
        self.assertIn('nodejs.org', said[0])

    def test_failed_install_steps_exit_nonzero(self):
        for command in ('plugins install --link --force --accept-capabilities ' + str(ROOT / 'openclaw-plugin'),
                        'plugins enable personal-assistant-bridge',
                        'config set plugins.entries.personal-assistant-bridge.hooks.allowConversationAccess true --strict-json',
                        'plugins reload personal-assistant-bridge --wait',
                        '--user daemon-reload', '--user enable personal-assistant.service',
                        '--user restart personal-assistant.service'):
            with self.subTest(command=command):
                result = self.install(ASSISTANT_TEST_FAIL=command, ASSISTANT_GATEWAY_WAIT='0')
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        result = self.install('--no-start', ASSISTANT_TEST_FAIL='--user daemon-reload')
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_activation_is_explicit_and_preserves_existing_choices(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        with mock.patch.dict(os.environ, self.env(), clear=True), mock.patch.object(installer, 'health', return_value=True):
            self.assertEqual(installer.main(['--non-interactive', '--activate']), 0)
        activated = json.loads(self.config.read_text())
        self.assertTrue(activated['delivery_enabled'])
        self.assertTrue(activated['review_worker']['enabled'])
        self.assertEqual(self.install('--no-start').returncode, 0)
        self.assertTrue(json.loads(self.config.read_text())['delivery_enabled'])

    def test_failed_uninstall_never_purges_running_service_data(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        result = self.install('--uninstall', '--purge', ASSISTANT_TEST_FAIL='--user disable --now personal-assistant.service')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(self.config.exists())
        self.assertTrue((self.home / '.config/systemd/user/personal-assistant.service').exists())

    def test_removed_source_is_rejected_without_rewriting_private_config(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        config = json.loads(self.config.read_text())
        config['accounts'].append({'id':'work-feed','service':'rss','transport':'http','boundary':'work','url':'https://example.com/feed.xml'})
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.install('--no-start').returncode, 0, 'connector plugins are supported sources')
        config = json.loads(self.config.read_text())
        config['accounts'].append({'id':'old-source','service':'unsupported'})
        self.config.write_text(json.dumps(config))
        before = self.config.read_text()
        result = self.install('--no-start')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported sources', result.stderr)
        self.assertEqual(self.config.read_text(), before)

    def test_uninstall_keeps_data_and_purge_removes_it(self):
        self.assertEqual(self.install('--no-start').returncode, 0)
        result = self.install('--uninstall')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / '.config/systemd/user/personal-assistant.service').exists())
        self.assertFalse((self.home / '.local/bin/assistant').exists())
        self.assertTrue(self.config.exists())
        calls = self.calls()
        self.assertIn('systemctl --user disable --now personal-assistant.service', calls)
        self.assertIn('openclaw plugins uninstall personal-assistant-bridge --force --keep-files', calls)
        self.assertTrue((ROOT / 'openclaw-plugin/index.ts').exists())
        self.assertEqual(self.install('--uninstall', '--purge').returncode, 0)
        self.assertFalse(self.data.exists())
        self.assertNotEqual(self.install('--purge').returncode, 0)


class WizardTest(unittest.TestCase):
    def test_node_versions_match_the_pinned_openclaw_engine_range(self):
        for text, supported in [('22.13.0',False),('24.15.9',False),('24.16.0',True),
                                ('25.8.0',False),('26.0.9',False),('26.1.0',True),('27.0.0',True)]:
            with self.subTest(version=text):
                self.assertEqual(installer.supported_node(installer.version_tuple(text)), supported)

    def test_mise_shim_uses_the_installed_executable_and_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()  # macOS: /var is /private/var
            shim = root / 'shims/openclaw'
            binary = root / 'node/26.1/bin/openclaw'
            package = binary.parent.parent / 'lib/node_modules/openclaw'
            package.mkdir(parents=True)
            binary.parent.mkdir(parents=True)
            binary.write_text('stub')
            (package / 'package.json').write_text('{"name":"openclaw"}')
            with mock.patch.object(installer.shutil, 'which', return_value='/bin/mise'), \
                 mock.patch.object(installer, 'run', return_value=subprocess.CompletedProcess([],0,str(binary)+'\n','')):
                resolved = installer.resolve_tool(str(shim))
            self.assertEqual(resolved, str(binary))
            self.assertEqual(installer.openclaw_package_dir(resolved), package)

    def test_interactive_answers_build_the_config(self):
        home = Path(tempfile.mkdtemp(prefix='machine-wizard-'))
        self.addCleanup(shutil.rmtree, home, True)
        replies = iter(['Alex Owner', '1000001', 'alex@example.com', 'box.example-tailnet.ts.net', 'Europe/Amsterdam',
                        'gmail,android,whatsapp', 'alex@example.com', 'work:Acme'])
        with mock.patch.dict(os.environ, {'HOME': str(home)}, clear=False), \
             mock.patch.dict(os.environ, {k: '' for k in installer.ENV.values()}), \
             mock.patch.object(builtins, 'input', lambda prompt='': next(replies)), \
             mock.patch.object(installer, 'say'):
            paths = installer.Paths(home)
            values = installer.answers(True, {})
            config = installer.new_config(values, {}, paths)
        self.assertEqual(next(replies, 'done'), 'done')
        self.assertEqual([a['id'] for a in config['accounts']], ['work-gmail', 'personal-android', 'personal-whatsapp'])
        self.assertEqual(config['accounts'][0]['client'], 'default')
        self.assertTrue(installer.onboarding.missing(config, config['accounts'][1]))  # assistant connect android picks the phone
        self.assertEqual(config['accounts'][1]['boundary'], 'personal:phone')
        self.assertEqual(config['accounts'][2]['store'], str(home / '.local/share/wacli'))
        self.assertEqual(config['credentials']['gog-password'], str(home / '.local/share/personal-assistant/gog-password'))
        self.assertEqual(config['dashboard_host'], 'box.example-tailnet.ts.net:8443')

    def test_wizard_defaults_come_from_what_openclaw_already_knows(self):
        home = Path(tempfile.mkdtemp(prefix='machine-wizard-'))
        self.addCleanup(shutil.rmtree, home, True)
        (home / '.openclaw').mkdir()
        (home / '.openclaw/openclaw.json').write_text(json.dumps({'commands': {'ownerAllowFrom': ['telegram:2000002']}}))
        replies = iter([''] * 7)
        with mock.patch.dict(os.environ, {'HOME': str(home)}, clear=False), \
             mock.patch.dict(os.environ, {k: '' for k in installer.ENV.values()}), \
             mock.patch.object(builtins, 'input', lambda prompt='': next(replies)), \
             mock.patch.object(installer, 'tailscale_identity', return_value={'tailscale_owner': 'a@b.c', 'dashboard_host': 'h.ts.net'}), \
             mock.patch.object(installer.onboarding, 'owner_name', return_value='Alex Owner'), \
             mock.patch.object(installer.onboarding, 'gateway_unit', return_value='openclaw-gateway.service'), \
             mock.patch.object(installer, 'say'):
            values = installer.answers(True, {})
        self.assertEqual((values['telegram_owner'], values['owner_name'], values['connectors']), ('2000002', 'Alex Owner', 'gateway'))

    def test_guided_setup_connects_then_offers_preview_and_activation(self):
        home = Path(tempfile.mkdtemp(prefix='machine-guided-'))
        self.addCleanup(shutil.rmtree, home, True)
        with mock.patch.dict(os.environ, {'HOME': str(home), 'OPENCLAW_WORKSPACE': str(home / 'ws')}), \
             mock.patch.object(installer.onboarding, 'gateway_unit', return_value='openclaw-gateway.service'):
            paths = installer.Paths(home)
            values = {**{k: '' for k in installer.ENV}, 'owner_name': 'Alex', 'telegram_owner': '1000001', 'tailscale_owner': 'a@b.c',
                      'dashboard_host': 'h.ts.net', 'timezone': 'UTC', 'connectors': 'gmail,gateway', 'email': 'alex@acme.test',
                      'gog_client': 'default', 'boundary': 'work:Acme', 'whatsapp_store': ''}
            installer.write_private(paths.config, json.dumps(installer.new_config(values, {}, paths)))
            asked, connected = [], []

            class UI(installer.onboarding.UI):
                def say(self, text=''): asked.append(text)
                def yes(self, prompt, default=True):
                    asked.append(prompt)
                    return not prompt.startswith('Make the dashboard')

            def connect(path, name, ui, account_id=None, restart=True):
                connected.append((name, account_id, restart))
                config = json.loads(Path(path).read_text())
                installer.write_private(Path(config['credentials']['gog-password']), 'x')
            with mock.patch.object(installer.onboarding, 'connect', side_effect=connect), \
                 mock.patch.object(installer.onboarding, 'restart_and_sync') as sync, \
                 mock.patch.object(installer.onboarding, 'serve_dashboard') as serve, \
                 mock.patch.object(installer.subprocess, 'call', return_value=0) as preview, \
                 mock.patch.object(installer, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                 mock.patch.object(installer, 'say'):
                config = installer.guided(paths, '2026-01-01T00:00:00+00:00', UI())
        self.assertEqual(connected, [('gmail', 'work-gmail', False)])
        self.assertEqual(sync.call_args.args[1], ['work-gmail', 'openclaw-gateway'])
        serve.assert_called_once()
        self.assertTrue((home / 'ws/USER.md').exists())
        calls = [c.args[0] for c in preview.call_args_list]
        self.assertEqual([c[4] for c in calls], ['profile', 'preview'])  # profile reads the first sync, before the preview
        self.assertEqual(calls[0][-2:], ['--workspace', str(home / 'ws')])
        self.assertEqual(preview.call_args.args[0][-1], 'preview')
        self.assertTrue(config['delivery_enabled'] and config['review_worker']['enabled'])
        self.assertTrue(json.loads(paths.config.read_text())['delivery_enabled'])
        self.assertLess(next(i for i, a in enumerate(asked) if 'preview' in str(a)), next(i for i, a in enumerate(asked) if 'Turn on' in str(a)))

    def test_example_config_covers_every_connector(self):
        example = json.loads((ROOT / 'config.example.json').read_text())
        self.assertEqual(sorted(a['id'] for a in example['accounts']), sorted(installer.CONNECTORS.values()))


if __name__ == '__main__':
    unittest.main()
