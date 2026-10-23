#!/usr/bin/env python3
"""Install, upgrade or remove The Machine for the current user.

    ./install.sh                     interactive install, or upgrade of an existing install
    ./install.sh --non-interactive   read answers from ASSISTANT_* environment variables
    ./install.sh --preview           one review of the last 7 days, shown only; nothing is sent
    ./install.sh --uninstall         remove the service, plugin and CLI link; keep data
    ./install.sh --browser-viewer    existing install: give the assistant a browser of its own that you watch and
                                     use from yours (noVNC on 127.0.0.1:6080; see browser_viewer.py)
    ./install.sh --uninstall --purge also delete the data directory (config, database, secrets)
    ./install.sh --dry-run           check prerequisites and show what would be written; changes nothing

Linux runs the service as a systemd user unit, macOS as a launchd agent (service.py). On Windows, run
install.ps1: it sets up WSL2 and runs this installer inside it.

In a terminal, a healthy install goes on to connect each source (onboarding.py: sign-ins,
keys and IDs, a test read and the first sync), offers the dashboard on your tailnet and a
starter USER.md, then a preview review and, after it, turning reviews and Telegram on.

Without OpenClaw, a terminal install offers to install it (npm, after a yes) and then hands
over to OpenClaw's own setup (`openclaw onboard`: your model, the Gateway service, Telegram),
saying first what The Machine needs from it.

Re-running is safe: an existing config.json is kept, missing keys are added, and secrets
are only created when absent. OpenClaw's own configuration is only read (your approved
Telegram ID, paired phones); the installer changes nothing there beyond `openclaw plugins
install/enable/uninstall`, approving your own Telegram pairing request if you say yes, and
OpenClaw's own setup commands when it offers them and you say yes.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import message_seal
import onboarding
import service
from onboarding import CONNECTORS, expand, write_private

ROOT = Path(__file__).resolve().parent
PLUGIN_ID = 'personal-assistant-bridge'
UNIT_NAME = service.UNIT_NAME
MIN_PYTHON = (3, 12)
NODE_REQUIREMENT = '>=24.16.0 <25 or >=26.1.0 (OpenClaw 2026.10.5 or later)'


MACOS = service.manager() == 'launchd'


def supported_node(version):
    return bool(version and ((version >= (24, 16, 0) and version < (25, 0, 0))
                             or version >= (26, 1, 0)))

# Answers for --non-interactive (and defaults for the wizard).
ENV = {
    'owner_name': 'ASSISTANT_OWNER_NAME',
    'telegram_owner': 'ASSISTANT_TELEGRAM_OWNER',
    'tailscale_owner': 'ASSISTANT_TAILSCALE_OWNER',
    'dashboard_host': 'ASSISTANT_DASHBOARD_HOST',
    'timezone': 'ASSISTANT_TIMEZONE',
    'connectors': 'ASSISTANT_CONNECTORS',       # comma list: gmail,calendar,gateway,android,whatsapp,slack,crm (or none)
    'email': 'ASSISTANT_EMAIL',                 # Google account for gmail/calendar
    'gog_client': 'ASSISTANT_GOG_CLIENT',
    'boundary': 'ASSISTANT_BOUNDARY',           # e.g. work:Acme
    'android_node_id': 'ASSISTANT_ANDROID_NODE_ID',
    'whatsapp_store': 'ASSISTANT_WHATSAPP_STORE',
}
class Paths:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get('HOME') or Path.home())
        self.data = self.home / '.local/share/personal-assistant'
        self.config = Path(os.environ.get('ASSISTANT_CONFIG') or self.data / 'config.json')
        self.unit = service.definition_path(UNIT_NAME, self.home)
        self.cli = self.home / '.local/bin/assistant'
        self.workspace = Path(os.environ.get('OPENCLAW_WORKSPACE') or self.home / '.openclaw/workspace')


def say(*parts):
    print(*parts, flush=True)


def version_tuple(text):
    found = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    return tuple(int(x or 0) for x in found.groups()) if found else None


def run(args, check=False, **kwargs):
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    try:
        return subprocess.run(args, check=check, timeout=kwargs.pop('timeout', 120), **kwargs)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 127, '', str(error))


# ---------------------------------------------------------------- prerequisites

def required_openclaw():
    package = json.loads((ROOT / 'openclaw-plugin/package.json').read_text())
    return package.get('peerDependencies', {}).get('openclaw', '')


def resolve_tool(binary):
    """Resolve symlinks and mise/asdf shims without depending on a shell startup file."""
    if not binary:
        return None
    path = Path(binary)
    if 'shims' in path.parts:
        for manager in ('mise', 'asdf'):
            command = shutil.which(manager)
            if command:
                result = run([command, 'which', path.name])
                candidate = Path(result.stdout.strip())
                if result.returncode == 0 and candidate.is_file() and candidate != path:
                    return str(candidate.absolute())
    return str(path.absolute())


def openclaw_package_dir(binary, node=None):
    """The installed openclaw npm package (for the Node helpers), found from the CLI's real path."""
    if not binary:
        return None
    path = Path(binary).resolve()
    for parent in [path.parent, *path.parents]:
        manifest = parent / 'package.json'
        if manifest.exists():
            try:
                if json.loads(manifest.read_text()).get('name') == 'openclaw':
                    return parent
            except ValueError:
                pass
    guesses = [Path(binary).parent.parent / 'lib/node_modules/openclaw']
    if node:
        guesses.append(Path(node).resolve().parent.parent / 'lib/node_modules/openclaw')
    npm = shutil.which('npm')
    if npm:
        result = run([npm, 'root', '-g'])
        if result.returncode == 0 and result.stdout.strip():
            guesses.append(Path(result.stdout.strip()) / 'openclaw')
    for guess in guesses:
        manifest = guess / 'package.json'
        try:
            if json.loads(manifest.read_text()).get('name') == 'openclaw':
                return guess.resolve()
        except (OSError, ValueError):
            pass
    return None


def check_prerequisites():
    """Report what is there; return (ok, facts). Python, Node and OpenClaw are required."""
    facts, ok = {}, True
    say('Checking prerequisites')
    py = sys.version_info[:2]
    if py >= MIN_PYTHON:
        say(f'  ok    python {py[0]}.{py[1]} ({sys.executable})')
    else:
        ok = False
        say(f'  FAIL  python {py[0]}.{py[1]}: need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+; install a newer python3 and rerun with it')
    facts['python'] = sys.executable
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        ok = False
        say('  FAIL  running as root: run ./install.sh as the user who runs OpenClaw (the service runs as that user)')

    node = resolve_tool(shutil.which('node'))
    node_version = version_tuple(run([node, '--version']).stdout) if node else None
    if node and supported_node(node_version):
        say(f'  ok    node {".".join(map(str, node_version))} ({node})')
    else:
        ok = False
        say(f'  FAIL  need Node.js {NODE_REQUIREMENT}; install a supported version and rerun'
            + ('' if not MACOS else ' (Homebrew: brew install node@24, then put it on your PATH as brew says)'))
    facts['node'] = node

    openclaw = resolve_tool(shutil.which('openclaw'))
    want = required_openclaw()
    if openclaw:
        got = run([openclaw, '--version']).stdout
        have = '.'.join(map(str, version_tuple(got) or ())) or 'unknown'
        note = '' if have == want else f' (tested with {want}; the Telegram Change prompt falls back to a plain prompt on other versions)'
        say(f'  ok    openclaw {have}{note}')
        facts['openclaw_version'] = have
    else:
        ok = False
        say(f'  FAIL  openclaw CLI not found: install OpenClaw {want} (npm install -g openclaw@{want}) and set up its Telegram channel,'
            ' or run ./install.sh in a terminal and it offers to do that with you')
    facts['openclaw'] = openclaw
    facts['openclaw_package_dir'] = openclaw_package_dir(openclaw, node)
    if openclaw and not facts['openclaw_package_dir']:
        ok = False
        say('  FAIL  cannot locate the OpenClaw npm package; expose its installed CLI on PATH and rerun')

    manager = {'systemd': 'systemctl', 'launchd': 'launchctl'}.get(service.manager(), 'systemctl')
    for name, hint in (('gog', 'Gmail and Calendar sources: install gog (gogcli' + (', brew install gogcli' if MACOS else '')
                        + '); signing in comes later in this setup'),
                       ('tailscale', 'dashboard access: install Tailscale and join your tailnet'),
                       ('caddy', 'only needed to serve the dashboard on a custom domain (see deploy/Caddyfile)'),
                       ('wacli', 'only needed for the WhatsApp source'),
                       (manager, 'needed to run the service; without it start `python3 assistant.py serve` yourself')):
        found = onboarding.portable.tailscale() if name == 'tailscale' else shutil.which(name)
        facts[name] = found
        say(f'  {"ok   " if found else "skip "} {name}{"" if found else ": " + hint}')
    facts['service'] = service.available()
    fresh = not Paths().config.exists()
    if fresh and not facts['tailscale'] and not (os.environ.get(ENV['tailscale_owner']) and os.environ.get(ENV['dashboard_host'])):
        ok = False
        say('  FAIL  tailscale: the dashboard only opens for your Tailscale login. Install Tailscale on this machine and your phone\n'
            '        (https://tailscale.com/download), run `sudo tailscale up`, then rerun ./install.sh')
    elif fresh and facts['tailscale'] and not tailscale_identity(facts['tailscale']).get('dashboard_host'):
        say('  note  tailscale is installed but not logged in; run `sudo tailscale up` first, or type your login and host below')
    return ok, facts


# ---------------------------------------------------------------- OpenClaw itself

def writable(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.access(path, os.W_OK)


def npm_beside(node):
    """npm from the same Node install as `node`, else the one on PATH."""
    beside = Path(node).parent / 'npm' if node else None
    return str(beside) if beside and beside.exists() else shutil.which('npm')


def openclaw_install_command(npm, want, home=None):
    """npm install -g openclaw@<want>, into ~/.local when npm's global folder isn't yours: never sudo."""
    command = [npm, 'install', '-g', f'openclaw@{want}']
    prefix = run([npm, 'prefix', '-g']).stdout.strip()
    if not prefix or not writable(Path(prefix) / 'lib/node_modules'):
        command += ['--prefix', str(Path(home or Path.home()) / '.local')]
    return command


def installed_openclaw(npm, command):
    """The openclaw CLI npm just installed, put on this run's PATH when it isn't there yet."""
    if '--prefix' in command:
        prefix = command[command.index('--prefix') + 1]
    else:
        prefix = run([npm, 'prefix', '-g']).stdout.strip()
    if shutil.which('mise') and '/mise/' in str(npm):
        run(['mise', 'reshim'], timeout=60)  # mise puts new global npm binaries on PATH through its shims
    found = shutil.which('openclaw')
    if found:
        return found
    binary = Path(prefix) / 'bin/openclaw'
    if not binary.exists():
        return None
    os.environ['PATH'] = f'{binary.parent}{os.pathsep}{os.environ.get("PATH", "")}'
    say(f'  note  {binary.parent} is not on your PATH; add it in your shell profile so `openclaw` works in new terminals')
    return str(binary)


def has_telegram(settings):
    """OpenClaw's config has a Telegram channel (a bot token, a token file or accounts), whatever its shape."""
    channel = (settings.get('channels') or {}).get('telegram')
    return isinstance(channel, dict) and bool(channel) and channel.get('enabled') is not False


ALLOW_SCRIPTS = re.compile(r'--allow-scripts=([\w@./,-]+)')


def allow_install_scripts(npm, command, output, ui):
    """npm 11+ skips package install scripts unless they're allowed, and says which ones it skipped. OpenClaw
    needs them (its own setup and native parts such as koffi), so run exactly those, for the package just installed."""
    skipped = ALLOW_SCRIPTS.search(output)
    if not skipped:
        return subprocess.CompletedProcess(command, 0, '', '')
    prefix = command[command.index('--prefix'):command.index('--prefix') + 2] if '--prefix' in command else []
    ui.say(f'  Running the install steps npm held back: {skipped.group(1).replace(",", ", ")}')
    result = run([npm, 'rebuild', '-g', *prefix, f'--allow-scripts={skipped.group(1)}'], timeout=1800)
    if result.returncode != 0:
        ui.say(((result.stderr or '') + (result.stdout or '')).strip()[-1500:])
    return result


def offer_openclaw(ui):
    """In a terminal, before the prerequisite check: without OpenClaw, offer to install it; when it was never
    set up, or has no Telegram bot, walk through OpenClaw's own setup with what The Machine needs from it.
    Every step asks first and can be answered no; the prerequisite check after it says what is still missing."""
    openclaw = resolve_tool(shutil.which('openclaw'))
    want = required_openclaw()
    acted = False
    if not openclaw:
        node = resolve_tool(shutil.which('node'))
        npm = npm_beside(node)
        if not (node and supported_node(version_tuple(run([node, '--version']).stdout)) and npm):
            ui.say(f'\nThe Machine runs on OpenClaw, which is not installed here. It needs Node.js {NODE_REQUIREMENT} with npm first:\n'
                   '  install it from https://nodejs.org or with a version manager (mise use -g node@24), then rerun ./install.sh')
            return
        command = openclaw_install_command(npm, want)
        ui.say('\nThe Machine runs on OpenClaw: your own agent, with your model, talking to you through its dashboard app and, if you like, a Telegram bot.'
               ' It is not installed here.')
        if not ui.yes(f'Install OpenClaw {want} now? (runs: npm {" ".join(command[1:])})', True):
            return
        ui.say('  Installing (a few minutes)...')
        result = run(command, timeout=1800)
        if result.returncode == 0:
            result = allow_install_scripts(npm, command, (result.stdout or '') + (result.stderr or ''), ui)
        else:
            ui.say(((result.stderr or '') + (result.stdout or '')).strip()[-1500:])
        openclaw = installed_openclaw(npm, command) if result.returncode == 0 else None
        if not openclaw:
            ui.say(f'  ✗ OpenClaw did not install. Run it yourself and rerun ./install.sh:\n    npm {" ".join(command[1:])}')
            return
        ui.say(f'  ✓ OpenClaw {want} installed')
        acted = True
    settings = onboarding.openclaw_settings()
    if not settings:
        ui.say('\nOpenClaw is not set up yet. Its own guided setup comes next; this is what The Machine needs from it:\n'
               '  1. Model: pick the provider you use and sign in or paste its key. Reviews and Do it run on it.\n'
               '  2. Gateway: say yes to running it as a service, so it keeps running in the background.\n'
               '  3. Channels (optional): choose Telegram to get cards there too. Make a bot first: in Telegram, message\n'
               '     @BotFather, send /newbot, pick a name and a free username ending in "bot", and paste the token. Skip it to use the dashboard app\'s notifications.\n'
               '  The rest is up to you. When it finishes, you come back here.')
        if ui.yes('Start OpenClaw\'s setup now?', True):
            run([openclaw, 'onboard', '--install-daemon'], timeout=None, capture_output=False)
            settings = onboarding.openclaw_settings()
            acted = True
        if not settings:
            ui.say('  OpenClaw is still not set up; run `openclaw onboard --install-daemon`, then rerun ./install.sh')
            return
    if not has_telegram(settings):
        ui.say('\nOpenClaw has no Telegram bot. That is optional: cards also reach you through the dashboard app\n'
               '(add it to your phone\'s home screen, turn on notifications). For Telegram too: in Telegram, message\n'
               '@BotFather, send /newbot, pick a name and a free username ending in "bot", and copy the token it gives you.')
        if ui.yes('Connect a Telegram bot to OpenClaw now? (runs: openclaw channels add --channel telegram)', False):
            run([openclaw, 'channels', 'add', '--channel', 'telegram'], timeout=None, capture_output=False)
            acted = True
        if not has_telegram(onboarding.openclaw_settings()):
            ui.say('  No Telegram bot yet; add one later with `openclaw channels add --channel telegram`.')
    if acted and run([openclaw, 'health'], timeout=60).returncode != 0:
        ui.say('  note  the OpenClaw Gateway is not answering yet; `openclaw doctor --fix` says why (reviews and cards need it)')


# ---------------------------------------------------------------- config

def tailscale_identity(binary):
    if not binary:
        return {}
    result = run([binary, 'status', '--json'], timeout=10)
    try:
        status = json.loads(result.stdout)
        user = status['User'][str(status['Self']['UserID'])]['LoginName']
        # A tagged machine has no owner login of its own: the dashboard owner is whoever you sign in as on your phone.
        return {'tailscale_owner': '' if user == 'tagged-devices' or '@' not in user else user,
                'dashboard_host': dashboard_address(status['Self']['DNSName'].rstrip('.'))}
    except (ValueError, KeyError, TypeError):
        return {}


def dashboard_address(host):
    """A tailnet name gets the dashboard's own port (:8443): its own origin, so the phone app installs
    cleanly next to OpenClaw's web app on :443. Your own domain is kept as typed."""
    host = host.strip().removeprefix('https://').rstrip('/')
    return f'{host}:8443' if host.endswith('.ts.net') and ':' not in host else host


def ask(prompt, default=''):
    answer = input(f'{prompt}{f" [{default}]" if default else ""}: ').strip()
    return answer or default


def ask_until(prompt, default, valid, problem):
    """Ask again until the answer passes `valid`; Enter keeps a valid default."""
    while True:
        answer = ask(prompt, default)
        if valid(answer):
            return answer
        say(f'  {problem(answer)}')


def valid_timezone(name):
    try:
        ZoneInfo(name)
        return True
    except (ValueError, KeyError, OSError):
        return False


def answers(interactive, facts):
    # Smart defaults from this machine: Tailscale identity, the Telegram ID OpenClaw already trusts,
    # Google accounts gog knows, and sources that are installed or linked here.
    detected = tailscale_identity(facts.get('tailscale'))
    owners = onboarding.telegram_owners()
    google = onboarding.gog_accounts(facts.get('gog'))
    defaults = {'owner_name': onboarding.owner_name(), 'telegram_owner': owners[0] if owners else '',
                'tailscale_owner': detected.get('tailscale_owner', ''),
                'dashboard_host': detected.get('dashboard_host', ''), 'timezone': local_timezone(),
                'connectors': onboarding.suggested_sources(facts), 'email': google[0][0] if google else '',
                'gog_client': google[0][1] if google else 'default', 'boundary': '',
                'android_node_id': '', 'whatsapp_store': str(Path.home() / '.local/share/wacli')}
    values = {key: os.environ.get(var) or defaults[key] for key, var in ENV.items()}
    unknown = lambda text: sorted(set(split(text)) - set(CONNECTORS) - {'none'})
    if interactive:
        say('\nA few questions (Enter keeps the default):')
        values['owner_name'] = ask('Your name (used to sign emails)', values['owner_name'])
        if not values['telegram_owner'] and facts.get('openclaw'):
            values['telegram_owner'] = onboarding.telegram_from_pairing(facts['openclaw'], onboarding.UI()) or ''
        values['telegram_owner'] = ask_until('Your Telegram user ID (the private chat with your OpenClaw bot'
                                             + (', found in OpenClaw)' if values['telegram_owner'] else
                                                '; your bot\'s pairing reply shows it. Optional: Enter skips Telegram,'
                                                ' then cards reach you through the dashboard app)'), values['telegram_owner'],
                                             lambda a: telegram_off(a) or onboarding.telegram_id(a),
                                             lambda a: f'{a!r} is not a Telegram user ID: that is a number like 123456789')
        values['telegram_owner'] = onboarding.telegram_id(values['telegram_owner']) or ''
        values['tailscale_owner'] = ask_until('Your Tailscale login, as on your phone (only it may open the dashboard)'
                                              + ('' if values['tailscale_owner'] else '; this machine is tagged or not logged in, so type it'),
                                              values['tailscale_owner'], lambda a: bool(a.strip()), lambda a: 'needed for the dashboard')
        values['dashboard_host'] = ask_until('Dashboard address (your machine on the tailnet, e.g. machine.tailnet.ts.net:8443)', values['dashboard_host'],
                                             lambda a: bool(re.fullmatch(r'[A-Za-z0-9.-]+\.[A-Za-z]{2,}(:\d{2,5})?', dashboard_address(a))),
                                             lambda a: 'a host name, like machine.tailnet.ts.net:8443 (no path)')
        values['timezone'] = ask_until('Time zone', values['timezone'], valid_timezone,
                                       lambda a: f'unknown time zone {a!r}; use a name like Europe/Brussels or America/New_York')
        values['connectors'] = ask_until('Sources to enable (' + ','.join(CONNECTORS) + '; you connect each one next)',
                                         values['connectors'], lambda a: not unknown(a),
                                         lambda a: 'unknown: ' + ', '.join(unknown(a)) + '; other sources (github, x, ...) come later with assistant connect')
        chosen = set(split(values['connectors']))
        if chosen & {'gmail', 'calendar', 'slack', 'crm'}:
            values['email'] = ask_until('Your email address for these sources', values['email'], lambda a: '@' in a,
                                        lambda a: 'an email address, like you@company.com')
            guess = values['boundary'] or onboarding.company_of(values['email']) or ('personal' if values['email'] else '')
            values['boundary'] = ask('Company name for these sources, or "personal" (keeps work and private apart)', guess)
    values['boundary'] = onboarding.boundary_label(values['boundary'] or onboarding.company_of(values['email']))
    values['dashboard_host'] = dashboard_address(values['dashboard_host'])
    # Telegram is optional: without it, cards reach the owner through the installed dashboard app.
    values['telegram_owner'] = '' if telegram_off(values['telegram_owner']) else onboarding.telegram_id(values['telegram_owner']) or values['telegram_owner']
    missing = [key for key in ('tailscale_owner', 'dashboard_host') if not values[key]]
    if missing:
        raise SystemExit('Missing answers: ' + ', '.join(missing) + ' (set ' + ', '.join(ENV[k] for k in missing) + ')')
    if not values['telegram_owner']:
        say('No Telegram: cards reach you through the dashboard app (add it to your home screen, turn on notifications).'
            ' Add Telegram later with assistant telegram on <your Telegram user ID>.')
    elif not re.fullmatch(r'-?\d{3,20}', values['telegram_owner']):
        raise SystemExit('The Telegram owner must be a numeric chat ID, got ' + repr(values['telegram_owner']))
    if unknown(values['connectors']):
        raise SystemExit('Unknown sources: ' + ', '.join(unknown(values['connectors'])))
    if not valid_timezone(values['timezone']):
        raise SystemExit('Unknown time zone ' + repr(values['timezone']) + ' (set ASSISTANT_TIMEZONE, e.g. Europe/Brussels)')
    return values


def telegram_off(answer):
    return str(answer or '').strip().lower() in ('', 'none', 'off', 'no')


def split(text):
    return [part.strip() for part in (text or '').split(',') if part.strip()]


def local_timezone():
    link = Path('/etc/localtime')
    try:
        target = str(link.resolve())
        return target.split('zoneinfo/', 1)[1] if 'zoneinfo/' in target else 'UTC'
    except OSError:
        return 'UTC'


def new_config(values, facts, paths):
    """config.json from config.example.json plus the wizard's answers."""
    example = json.loads((ROOT / 'config.example.json').read_text())
    templates = {a['id']: a for a in example.pop('accounts')}
    config = expand(example, paths.home)
    config.update(owner_name=values['owner_name'], timezone=values['timezone'], telegram_owner=values['telegram_owner'],
                  tailscale_owner=values['tailscale_owner'], dashboard_host=values['dashboard_host'], heartbeat_job_id='',
                  booking_links={})
    bot = onboarding.telegram_account(values['telegram_owner'])
    if bot != 'default':
        config['telegram_account'] = bot  # the OpenClaw Telegram account (bot) you talk to
    config['delivery_enabled'] = False
    config['review_worker']['enabled'] = False
    data = str(paths.data)
    config['database'] = data + '/events.sqlite'
    config['prompt_receipts'] = data + '/prompt-receipts'
    config['attachment_dir'] = data + '/attachments'
    config['credentials'] = {'api-token': data + '/api-token', 'owner-token': data + '/owner-token'}
    config['attachment_sources'] = [str(paths.workspace / 'data/assistant-attachments'), str(paths.workspace / 'memory/assistant')]
    accounts = []
    for name in split(values['connectors']):
        if name == 'none':
            continue
        if name == 'gateway' and not onboarding.gateway_unit():
            say('  skipped the Gateway log source: the OpenClaw Gateway does not run as a systemd user unit here')
            continue
        account = expand(dict(templates[CONNECTORS[name]]), paths.home)
        if 'email' in account:
            account['email'] = values['email'] or account['email']
            if 'owner_emails' in account:
                account['owner_emails'] = [account['email']]
        if account.get('transport') == 'gog':
            account['client'] = values['gog_client']
            config['credentials']['gog-password'] = data + '/gog-password'
        if account.get('boundary', '').startswith('work:'):
            account['boundary'] = values['boundary']
            if values['boundary'].startswith('personal:'):
                account['id'] = account['id'].replace('work-', 'personal-', 1)
        account.pop('exclude', None)  # example noise rules are not yours
        if name == 'android':
            account['node_id'] = values['android_node_id'] or account['node_id']
        if name == 'whatsapp':
            account['store'] = values['whatsapp_store']
        if name == 'gateway':
            account['unit'] = onboarding.gateway_unit()
        if account.get('credential'):
            config['credentials'][account['credential']] = f"{data}/{account['credential']}.env"
        accounts.append(account)
    config['accounts'] = accounts
    return config


def refresh_install_paths(config, facts, paths):
    """Keys that describe this checkout and toolchain; safe to update on every run."""
    config['app_dir'] = str(ROOT)
    for key in ('openclaw', 'node'):
        if facts.get(key):
            config[key] = facts[key]
    if facts.get('openclaw_package_dir'):
        config['openclaw_package_dir'] = str(facts['openclaw_package_dir'])
    if facts.get('gog') and config.get('gog') in (None, 'gog'):
        config['gog'] = facts['gog']
    if facts.get('wacli') and config.get('wacli') in (None, 'wacli'):
        config['wacli'] = facts['wacli']
    return config


def setup_config(interactive, facts, paths):
    if paths.config.exists():
        config = json.loads(paths.config.read_text())
        example = expand(json.loads((ROOT / 'config.example.json').read_text()), paths.home)
        example.pop('accounts')
        # The example's identity (owner, Telegram, Tailscale, host) is not yours; its time zone is this machine's.
        added = [key for key in example if key not in config and key not in
                 ('booking_links', 'heartbeat_job_id', 'owner_name', 'telegram_owner', 'tailscale_owner', 'dashboard_host')]
        for key in added:
            config[key] = local_timezone() if key == 'timezone' else example[key]
        bot = onboarding.telegram_account(config.get('telegram_owner', ''))
        if 'telegram_account' not in config and bot != 'default':
            config['telegram_account'] = bot
            added.append(f'telegram_account {bot}')
        say(f'Keeping existing {paths.config}' + (f' (added defaults: {", ".join(added)})' if added else ''))
    else:
        config = new_config(answers(interactive, facts), facts, paths)
        say(f'Writing {paths.config}')
    refresh_install_paths(config, facts, paths)
    # Built-in sources plus connector plugins (connectors/<service>.py, see docs/adding-a-source.md).
    supported = {'gmail', 'calendar', 'outlook', 'outlook_calendar', 'slack', 'salesflare', 'android', 'whatsapp', 'gateway'} | {
        p.stem for p in (ROOT / 'connectors').glob('*.py') if re.fullmatch(r'[a-z][a-z0-9_]{0,30}', p.stem)}
    unsupported = [a['id'] for a in config.get('accounts', []) if a.get('service', 'gmail') not in supported]
    if unsupported:
        raise SystemExit('Unsupported sources in existing config: ' + ', '.join(unsupported)
                         + '. Remove those accounts and unused credentials from config.json; database history is kept.')
    text = json.dumps(config, indent=2) + '\n'
    if not paths.config.exists() or paths.config.read_text() != text:
        write_private(paths.config, text)
    os.chmod(paths.config, 0o600)
    return config


# ---------------------------------------------------------------- secrets

def setup_secrets(config, facts, paths):
    paths.data.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(paths.data, 0o700)
    created = []
    for name in ('api-token', 'owner-token'):
        path = Path(config['credentials'].get(name) or paths.data / name)
        if not path.exists():
            write_private(path, secrets.token_hex(32))
            created.append(name)
        os.chmod(path, 0o600)
    vapid = Path(config['database']).parent / 'vapid.json'
    if not vapid.exists() and facts.get('node'):
        env = dict(os.environ, PUSH_VAPID_FILE=str(vapid))
        result = run([facts['node'], str(ROOT / 'push-send.mjs')], input='{"action":"key"}', env=env, timeout=30)
        if result.returncode == 0 and vapid.exists():
            created.append('vapid.json')
        else:
            say('  note: could not create the Web Push key now; the service creates it on first use')
    if vapid.exists():
        os.chmod(vapid, 0o600)
    say('Secrets: ' + (('created ' + ', '.join(created)) if created else 'already present, unchanged') + f' in {paths.data} (mode 600)')


# ---------------------------------------------------------------- systemd, CLI, plugin

def quote_unit(path):
    """A path for a systemd command line: quoted when it has spaces."""
    text = str(path)
    return f'"{text}"' if re.search(r'\s', text) else text


def unit_text(config, facts, config_path=None):
    bins = [str(Path(p).parent) for p in (facts.get('node'), facts.get('openclaw'), facts.get('gog'), facts.get('wacli')) if p]
    path = ':'.join(dict.fromkeys(bins + ['%h/.local/bin', '/usr/local/bin', '/usr/bin', '/bin']))
    return f"""# Written by install.sh from {ROOT}; rerun ./install.sh to update it.
[Unit]
Description=The Machine: signal collector and suggestions inbox
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={quote_unit(facts.get('python') or sys.executable)} {quote_unit(ROOT / 'assistant.py')} serve
Restart=always
RestartSec=10
TimeoutStopSec=20
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
Environment="PATH={path}"
Environment=PYTHONUNBUFFERED=1
Environment="ASSISTANT_CONFIG={str(config_path or Paths().config)}"
Environment="OPENCLAW_WORKSPACE={Paths().workspace}"

[Install]
WantedBy=default.target
"""


def service_path(facts):
    bins = [str(Path(p).parent) for p in (facts.get('node'), facts.get('openclaw'), facts.get('gog'), facts.get('wacli')) if p]
    home = str(Paths().home)
    return ':'.join(dict.fromkeys(bins + [home + '/.local/bin', '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin',
                                          '/usr/sbin', '/sbin']))


def agent_text(config, facts, config_path=None, paths=None):
    """The launchd agent for macOS: the same program, environment and restart policy as the systemd unit."""
    paths = paths or Paths()
    return service.plist_text([facts.get('python') or sys.executable, str(ROOT / 'assistant.py'), 'serve'], workdir=ROOT,
                              env={'PATH': service_path(facts), 'PYTHONUNBUFFERED': '1', 'HOME': str(paths.home),
                                   'ASSISTANT_CONFIG': str(config_path or paths.config), 'OPENCLAW_WORKSPACE': str(paths.workspace)},
                              base=paths.home)


def install_unit(config, facts, paths, start):
    launchd = service.manager() == 'launchd'
    text = agent_text(config, facts, paths.config, paths) if launchd else unit_text(config, facts, paths.config)
    changed = not paths.unit.exists() or paths.unit.read_text() != text
    if changed:
        paths.unit.parent.mkdir(parents=True, exist_ok=True)
        paths.unit.write_text(text)
    say(f'{"launchd agent" if launchd else "systemd unit"}: {"written" if changed else "unchanged"} {paths.unit}')
    if not facts.get('service', facts.get('systemctl')):
        say(f'  {"launchctl" if launchd else "systemctl"} not found: start the service with `python3 assistant.py serve`')
        return None if not start else False
    if launchd:
        if not start:
            say('  not started (--no-start)')
            return None
        result = service.enable(UNIT_NAME, run=run)
        if result.returncode != 0:
            say('  could not start it: ' + (result.stderr or result.stdout).strip()[:300])
            return False
        say(f'  loaded and started; it starts at login. Log: {service.log_path()}')
        return True
    reload = run(['systemctl', '--user', 'daemon-reload'])
    if reload.returncode != 0:
        error = (reload.stderr or reload.stdout).strip()
        say('  daemon-reload failed: ' + error[:300])
        if re.search(r'bus|XDG_RUNTIME_DIR', error, re.I):
            say('  There is no systemd user session here (sudo su, a container, or SSH without one). Log in as this user\n'
                '  directly (or: sudo loginctl enable-linger $USER, then log in again) and rerun, or install with\n'
                '  --no-start and run `python3 assistant.py serve` yourself.')
        return False
    if not start:
        say('  not started (--no-start)')
        return None
    result = run(['systemctl', '--user', 'enable', UNIT_NAME])
    result = run(['systemctl', '--user', 'restart', UNIT_NAME]) if result.returncode == 0 else result
    if result.returncode != 0:
        say('  could not start it: ' + (result.stderr or result.stdout).strip()[:300])
        return False
    say('  enabled and (re)started')
    linger = run(['loginctl', 'show-user', os.environ.get('USER') or str(os.getuid()), '-p', 'Linger', '--value'], timeout=10)
    if linger.returncode == 0 and linger.stdout.strip() == 'no':
        say('  note: it stops when you log out. To keep it (and the Gateway) running: sudo loginctl enable-linger $USER')
    return True


def install_cli(paths):
    target = ROOT / 'cli.py'
    paths.cli.parent.mkdir(parents=True, exist_ok=True)
    if paths.cli.is_symlink() and paths.cli.resolve() == target:
        warn_path(paths)
        return
    if paths.cli.exists() and not paths.cli.is_symlink():
        raise SystemExit(f'CLI install failed: {paths.cli} is an existing file; move it aside and rerun')
    if paths.cli.is_symlink():
        paths.cli.unlink()
    paths.cli.symlink_to(target)
    os.chmod(target, 0o755)
    say(f'CLI: {paths.cli} -> cli.py')
    warn_path(paths)


def node_packages_current(root=ROOT):
    """Every package in package-lock.json is installed at its locked version."""
    try:
        packages = json.loads((root / 'package-lock.json').read_text())['packages']
        return all(json.loads((root / path / 'package.json').read_text()).get('version') == entry.get('version')
                   for path, entry in packages.items() if path)
    except (OSError, ValueError, KeyError, AttributeError):
        return False


def install_node_packages(node, root=ROOT):
    """The pinned Node packages beside the app (package-lock.json: the Gateway's official client for card chat).
    npm ci only when they don't match the lock, with no install scripts. Not fatal: without them only the card
    chat view is unavailable."""
    if not (root / 'package-lock.json').exists() or node_packages_current(root):
        return True
    npm = npm_beside(node)
    result = run([npm, 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=str(root)) if npm else None
    if not result or result.returncode or not node_packages_current(root):
        say('  warn  could not install the Node packages (npm ci); card chat stays unavailable until the next install')
        return False
    say('Node packages: installed (package-lock.json)')
    return True


def warn_path(paths):
    folders = [Path(p).expanduser() for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    if paths.cli.parent not in folders:
        say(f'  note: {paths.cli.parent} is not on your PATH, so `assistant` is not found yet. Use {paths.cli},\n'
            f'  or add it: echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.profile (then open a new terminal)')


def plugin_fingerprint(facts):
    """What the bridge in OpenClaw is made of: its files, where they are and the OpenClaw that runs them."""
    digest = hashlib.sha256()
    root = ROOT / 'openclaw-plugin'
    for path in sorted(p for p in root.rglob('*') if p.is_file() and 'node_modules' not in p.parts):
        digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes() + b'\0')
    digest.update(f"{root}\0{facts.get('openclaw')}\0{facts.get('openclaw_version')}".encode())
    return digest.hexdigest()


def install_plugin(facts, reload=True, stamp=None):
    """Link, enable and reload the bridge, unless this exact bridge is already in. Every reload replaces the plugin
    inside a running Gateway, which is slow on a small server and can leave the model runtime unpublished (every
    model call then fails until the Gateway restarts): an update that did not touch the bridge should not do it."""
    openclaw = facts.get('openclaw')
    if not openclaw:
        return False
    fingerprint = plugin_fingerprint(facts)
    if stamp and reload and stamp.exists() and stamp.read_text().strip() == fingerprint:
        say(f'OpenClaw plugin {PLUGIN_ID}: unchanged, left as it is')
        return True
    plugin = str(ROOT / 'openclaw-plugin')
    say('Installing the local bridge and accepting its declared OpenClaw capabilities (see README Security model).')
    result = run([openclaw, 'plugins', 'install', '--link', '--force', '--accept-capabilities', plugin], timeout=300, capture_output=False)
    if result.returncode != 0:
        say(f'  plugin install failed; run it yourself: openclaw plugins install --link --force --accept-capabilities {plugin}')
        return False
    wait = float(os.environ.get('ASSISTANT_PLUGIN_RETRY_WAIT', 5))
    for attempt in range(3):  # right after an install the Gateway may still be applying it; the CLI then fails briefly
        result = run([openclaw, 'plugins', 'enable', PLUGIN_ID], timeout=120)
        if result.returncode == 0 or attempt == 2:
            break
        time.sleep(wait * (attempt + 1))
    if result.returncode != 0:
        say('  plugin enable failed: ' + (result.stderr or result.stdout).strip()[:300])
        return False
    # OpenClaw only runs a non-bundled plugin's conversation hooks with this set; without them a review
    # is never tied to its run and every preview ends "without saving its review".
    result = run([openclaw, 'config', 'set', f'plugins.entries.{PLUGIN_ID}.hooks.allowConversationAccess', 'true', '--strict-json'], timeout=120)
    if result.returncode != 0:
        say('  could not allow the plugin\'s conversation hooks: ' + (result.stderr or result.stdout).strip()[:300]
            + f'\n  run it yourself: openclaw config set plugins.entries.{PLUGIN_ID}.hooks.allowConversationAccess true --strict-json')
        return False
    applied = True
    if reload:
        result = run([openclaw, 'plugins', 'reload', PLUGIN_ID, '--wait'], timeout=300)
        if result.returncode != 0:
            # Reload only works in a running Gateway. One that is down (stopped, still starting, or given up by
            # systemd after failed starts) has the bridge saved and loads it as it starts: start it and check.
            applied = load_in_gateway(openclaw, (result.stderr or result.stdout).strip())
            if applied is False:
                return False
    if stamp and reload and applied:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fingerprint + '\n')
    say(f'OpenClaw plugin {PLUGIN_ID}: linked and enabled'
        + ('' if reload else ' (not reloaded: --no-start)')
        + (' (loads when the Gateway starts)' if applied is None else ''))
    return True


def gateway_health(openclaw):
    """The running Gateway's own health (with the plugins it loaded), or None when no Gateway answers."""
    result = run([openclaw, 'gateway', 'health', '--json', '--timeout', '20000'], timeout=60)
    try:
        found = json.loads(result.stdout or '{}')
    except ValueError:
        return None
    return found if isinstance(found, dict) and found.get('ok') else None


def load_in_gateway(openclaw, reload_error=''):
    """After a failed reload: True once the Gateway runs with the bridge loaded, None when no Gateway service runs
    here at all (the bridge is saved and loads when the owner starts one), False with the reason said otherwise."""
    unit = (onboarding.gateway_units() or [None])[0]
    if not unit:
        if gateway_health(openclaw) is None:
            say('  The OpenClaw Gateway is not running here; the bridge is saved and loads when it starts (openclaw gateway start).')
            return None
        say('  plugin reload failed: ' + reload_error[:300])
        return False
    say(f'  The OpenClaw Gateway did not take the bridge live; (re)starting {unit} so it loads it.')
    run(['systemctl', '--user', 'reset-failed', unit], timeout=30)  # after repeated failed starts systemd stops trying
    result = run(['systemctl', '--user', 'restart', unit], timeout=300)
    if result.returncode != 0:
        say(f'  Could not start the OpenClaw Gateway ({unit}): ' + (result.stderr or result.stdout).strip()[:300]
            + f'\n  Check: systemctl --user status {unit}; journalctl --user -u {unit} -n 50; openclaw doctor')
        return False
    deadline = time.time() + float(os.environ.get('ASSISTANT_GATEWAY_WAIT', 300))  # a first start on 4 GB takes a minute or more
    errors = []
    while True:
        live = gateway_health(openclaw)
        plugins = (live or {}).get('plugins') or {}
        if PLUGIN_ID in (plugins.get('loaded') or []):
            say(f'  The Gateway is running with {PLUGIN_ID} loaded.')
            return True
        errors = [e.get('error') for e in plugins.get('errors') or [] if isinstance(e, dict) and e.get('id') == PLUGIN_ID]
        if errors or time.time() >= deadline:
            break
        time.sleep(5)
    if not live:
        say('  The OpenClaw Gateway did not come up; the bridge is installed and loads when it starts.'
            f'\n  Check: systemctl --user status {unit}; journalctl --user -u {unit} -n 50; openclaw doctor')
        return False
    say(f'  The Gateway is running but did not load {PLUGIN_ID}' + (': ' + str(errors[0])[:300] if errors else '')
        + '\n  Check: openclaw plugins doctor')
    return False


def health(config, timeout):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{config.get('port', 8787)}/health"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if json.loads(response.read()).get('ok'):
                    return True
        except (OSError, ValueError):
            pass
        if time.time() >= deadline:
            return False
        time.sleep(1)


def activate(paths):
    config = json.loads(paths.config.read_text())
    config['delivery_enabled'] = True
    config.setdefault('review_worker', {})['enabled'] = True
    write_private(paths.config, json.dumps(config, indent=2) + '\n')
    return config


def guided(paths, since, ui=None):
    """After a healthy start in a terminal: connect what is not set up, show the first sync, offer the
    dashboard and USER.md, then a preview and, after it, turning reviews on. Every step can be skipped."""
    ui = ui or onboarding.UI()
    config = json.loads(paths.config.read_text())
    todo = [a for a in config.get('accounts', []) if onboarding.missing(config, a)]
    connected = []
    if todo:
        say('\nConnect your sources now (a minute each; skip any and run `assistant connect <name>` later).')
    for account in todo:
        name = onboarding.connect_name(account, config)
        if not ui.yes(f'Connect {onboarding.label(account.get("service", "gmail"))} ({account["id"]}) now?', True):
            continue
        try:
            onboarding.connect(paths.config, account.get('service', 'gmail'), ui, account['id'], restart=False)
            connected.append(account['id'])
        except onboarding.SetupError as error:
            say(f'  ✗ {error.message}' + (f'\n    fix: {error.fix}' if error.fix else '') + f'\n    later: assistant connect {name}')
        except (KeyboardInterrupt, EOFError):
            raise
        except Exception as error:  # one source's surprise should not end the whole setup
            say(f'  ✗ {account["id"]} failed unexpectedly: {type(error).__name__}: {str(error)[:200]}\n    later: assistant connect {name}')
    config = json.loads(paths.config.read_text())
    ready = [a['id'] for a in config.get('accounts', []) if not onboarding.missing(config, a)]
    if ready:
        if connected:
            onboarding.restart_and_sync(config, ready, ui, config_path=paths.config)
        else:
            onboarding.first_sync(config, ready, since, ui, wait=45)
    onboarding.serve_dashboard(config, ui)
    onboarding.offer_user_md(config, ui, paths.workspace)
    if ready and not onboarding.says_how_you_write(onboarding.user_md_path(paths.workspace)) and ui.yes(
            'Draft how you write and what matters to you from your own recent messages? Your agent reads them once;'
            ' you see the draft before USER.md changes', True):
        subprocess.call([sys.executable, str(ROOT / 'cli.py'), '--config', str(paths.config), 'profile', '--workspace', str(paths.workspace)])
    if config.get('delivery_enabled') and config.get('review_worker', {}).get('enabled'):
        return config
    if ready and ui.yes('Run a preview now? Your agent reviews the last 7 days and shows the cards here and on the dashboard;'
                        ' nothing is sent and nothing is done in your accounts', True):
        previewed = subprocess.call([sys.executable, str(ROOT / 'cli.py'), '--config', str(paths.config), 'preview']) == 0
        if not previewed:
            say('  The preview did not finish; turn reviews on once it works (./install.sh --preview, then --activate).')
        if ui.yes('Turn on daily reviews and Telegram cards now? (Do it acts with your accounts only when you tap it;'
                  ' see README "Security model")', previewed):
            config = activate(paths)
            restarted = service.restart(UNIT_NAME, run=run).returncode == 0
            say('  Reviews and Telegram cards are on.' if restarted else
                f'  Saved; restart to apply: {service.hint("restart")}')
    return config


def next_steps(config, healthy, started, paths=None):
    paths = paths or Paths()
    host, path = config.get('dashboard_host', ''), config.get('dashboard_path', '/assistant/')
    lines = ['', 'Next steps:']
    if started:
        lines.append(f'  - Health: {"OK" if healthy else "not answering yet; check " + service.hint("logs")}')
    for account in config.get('accounts', []):
        if onboarding.missing(config, account):
            lines.append(f'  - Connect {onboarding.label(account.get("service", "gmail"))}: assistant connect {onboarding.connect_name(account, config)}')
    served = onboarding.dashboard_url(config)
    if served:
        lines.append(f'  - Dashboard: {served}')
    else:
        lines += [f'  - Dashboard on your tailnet: {" ".join(onboarding.serve_command(config))}',
                  f'    then open https://{host}{path} (custom domain instead: see deploy/Caddyfile)']
    user_md = onboarding.user_md_path(paths.workspace)
    if not user_md.exists() or onboarding.user_md_is_template(user_md):
        lines.append(f'  - Tell the agent about you: create USER.md in {paths.workspace} (name, companies, tone, preferences)')
    elif not onboarding.says_how_you_write(user_md):
        lines.append('  - Let your agent draft how you write and what matters to you from your own messages: assistant profile (you see it first)')
    if config.get('message_protection') is not False and config.get('database'):
        key = message_seal.key_path(Path(config['database']).expanduser())
        if message_seal.in_keyring(Path(config['database']).expanduser()):
            lines.append('  - Messages are encrypted in the database with a key in your keyring (assistant message-key status)')
        else:
            lines.append(f'  - Messages are encrypted in the database with the key {key}. Back it up with the database:'
                         ' without it they are lost')
    lines += ['  - Keep `Do it` in mind: it lets the agent act with your accounts. Read README "Security model".',
              '  - After editing config.json: ' + service.hint('restart')]
    if not config.get('delivery_enabled') or not config.get('review_worker', {}).get('enabled'):
        lines += ['  - See what it would suggest: ./install.sh --preview (one review of the last 7 days by your agent;',
                  '    the cards show here and on the dashboard; nothing is sent and nothing is done in your accounts)',
                  '  - Once sources are connected and Security model is understood: ./install.sh --activate']
    lines.append('  - Check everything any time: assistant doctor (each problem comes with its fix)')
    say('\n'.join(lines))


# ---------------------------------------------------------------- uninstall

def uninstall(paths, purge, interactive):
    launchd = service.manager() == 'launchd'
    if service.available():
        stopped = service.disable_now(UNIT_NAME, run=run)
        if stopped.returncode != 0 and paths.unit.exists():
            say('Could not stop the service; kept files and data. Check ' + service.hint('status'))
            return 1
    elif paths.unit.exists():
        say('Cannot stop the installed service without its service manager; kept files and data.')
        return 1
    if paths.unit.exists():
        paths.unit.unlink()
        say(f'Removed {paths.unit}')
    pattern = service.LABEL_PREFIX + 'wacli-*.plist' if launchd else 'the-machine-wacli-*.service'
    for sync in sorted(paths.unit.parent.glob(pattern)):
        # The WhatsApp syncs it started; the linked wacli store itself stays.
        if service.available():
            unit = 'the-machine-' + sync.stem.removeprefix(service.LABEL_PREFIX) + '.service' if launchd else sync.name
            service.disable_now(unit, run=run)
        sync.unlink()
        say(f'Removed {sync}')
    if shutil.which('systemctl') and not launchd:
        if run(['systemctl', '--user', 'daemon-reload']).returncode != 0:
            say('systemd daemon-reload failed; rerun uninstall after fixing the user service manager.')
            return 1
    openclaw = shutil.which('openclaw')
    if openclaw:
        result = run([openclaw, 'plugins', 'uninstall', PLUGIN_ID, '--force', '--keep-files'], timeout=120)
        say(f'OpenClaw plugin {PLUGIN_ID}: ' + ('uninstalled' if result.returncode == 0 else 'not removed (' + (result.stderr or '').strip()[:200] + ')'))
        if result.returncode != 0:
            say('Kept data because plugin removal failed.')
            return 1
    if paths.cli.is_symlink() and paths.cli.resolve() == ROOT / 'cli.py':
        paths.cli.unlink()
        say(f'Removed {paths.cli}')
    if purge:
        if interactive and ask(f'Delete {paths.data} with your config, database and secrets? Type "delete"') != 'delete':
            say('Kept the data directory.')
            return 0
        if paths.data.exists():
            shutil.rmtree(paths.data)
            say(f'Deleted {paths.data}')
    else:
        say(f'Kept your data in {paths.data} (use --purge to delete it)')
    return 0


# ---------------------------------------------------------------- main

def browser_viewer_setup(paths, install=None):
    """The optional viewer: its units and files, then the dashboard links stuck cards to it (`browser_url`) and
    brings tabs to the front through its DevTools port (`browser_cdp_url`)."""
    import browser_viewer
    url = (install or browser_viewer.install)(say=say)
    if not url:
        return 1
    config = onboarding.load(paths.config)
    config.update(browser_url=url, browser_cdp_url=browser_viewer.CDP_URL)
    onboarding.save(paths.config, config)
    restarted = service.try_restart(UNIT_NAME, run=run).returncode == 0
    say('  Cards that wait on the browser now link to it.' if restarted else f'  Saved; restart to apply: {service.hint("restart")}')
    return 0


WINDOWS_ROUTE = ('On Windows, The Machine runs inside WSL2 (Linux), next to OpenClaw\'s Gateway. In PowerShell:\n'
                 '  irm https://raw.githubusercontent.com/Salesflare/TheMachine/main/install.ps1 | iex\n'
                 'See docs/getting-started.md, "Windows".')


def dry_run(paths):
    """What an install would do here, without writing or starting anything."""
    if os.name == 'nt':
        say(WINDOWS_ROUTE)
        return 0
    ok, facts = check_prerequisites()
    manager = service.manager()
    say(f'\nDry run: nothing written. Service manager: {manager}' + (' (inside WSL2)' if manager == 'systemd' and service.wsl() else ''))
    say(f'  config      {paths.config}' + (' (kept)' if paths.config.exists() else ' (new)'))
    say(f'  data        {paths.data} (mode 700; secrets mode 600)')
    say(f'  CLI         {paths.cli} -> {ROOT / "cli.py"}')
    say(f'  plugin      openclaw plugins install --link --force --accept-capabilities {ROOT / "openclaw-plugin"}')
    if manager == 'none':
        say('  service     none here: start it with `python3 assistant.py serve`')
    else:
        text = agent_text({}, facts, paths.config, paths) if manager == 'launchd' else unit_text({}, facts, paths.config)
        say(f'  service     {paths.unit}:\n' + '\n'.join('    ' + line for line in text.rstrip().splitlines()))
    say('\nReady to install.' if ok else '\nFix the FAIL items above before installing.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--non-interactive', action='store_true', help='take answers from ASSISTANT_* environment variables')
    parser.add_argument('--uninstall', action='store_true', help='remove the service, plugin and CLI link (keeps data)')
    parser.add_argument('--purge', action='store_true', help='with --uninstall: also delete config, database and secrets')
    parser.add_argument('--no-start', action='store_true', help='install files but do not enable or start the service')
    parser.add_argument('--skip-plugin', action='store_true', help='do not install the OpenClaw plugin')
    parser.add_argument('--activate', action='store_true', help='enable reviews and Telegram delivery on an existing install')
    parser.add_argument('--preview', action='store_true', help='existing install: one review of the last 7 days, shown only (assistant preview)')
    parser.add_argument('--browser-viewer', action='store_true', help='existing install: a browser of its own on a virtual display, viewed in yours (127.0.0.1 only)')
    parser.add_argument('--dry-run', action='store_true', help='check prerequisites and show what would be written; changes nothing')
    parser.add_argument('--health-timeout', type=float, default=30)
    args = parser.parse_args(argv)
    interactive = not args.non_interactive and sys.stdin.isatty()
    paths = Paths()
    if args.dry_run:
        return dry_run(paths)
    if os.name == 'nt':
        say(WINDOWS_ROUTE)
        return 1
    if args.purge and not args.uninstall:
        parser.error('--purge only works with --uninstall')
    if args.activate and (args.no_start or args.uninstall or not paths.config.exists()):
        parser.error('--activate needs an existing installation and a running service (no --no-start/--uninstall)')
    if args.preview:
        if args.activate or args.uninstall or not paths.config.exists():
            parser.error('--preview needs an existing installation and a running service (no --activate/--uninstall)')
        # Installs nothing and changes no config: the CLI asks the running service for one preview review.
        return subprocess.call([sys.executable, str(ROOT / 'cli.py'), '--config', str(paths.config), 'preview'])
    if args.browser_viewer:
        if args.uninstall or not paths.config.exists():
            parser.error('--browser-viewer needs an existing installation (no --uninstall)')
        if service.manager() != 'systemd':
            parser.error('--browser-viewer runs on Linux only (a virtual display with systemd units)')
        return browser_viewer_setup(paths)
    if args.uninstall:
        return uninstall(paths, args.purge, interactive)
    if interactive:
        try:
            offer_openclaw(onboarding.UI())
        except (KeyboardInterrupt, EOFError):
            say('\nStopped; nothing of The Machine was written. Rerun ./install.sh to go on.')
            return 1
    ok, facts = check_prerequisites()
    if not ok:
        say('\nFix the FAIL items above and rerun ./install.sh')
        return 1
    try:
        config = setup_config(interactive, facts, paths)
    except (KeyboardInterrupt, EOFError):
        say('\nStopped; nothing was written.')
        return 1
    setup_secrets(config, facts, paths)
    install_cli(paths)
    install_node_packages(facts['node'])
    plugin_ok = args.skip_plugin or install_plugin(facts, reload=not args.no_start, stamp=paths.data / 'plugin-installed')
    if not plugin_ok:
        return 1
    if args.activate:
        config = activate(paths)
    since = onboarding.now()
    started = install_unit(config, facts, paths, start=not args.no_start)
    healthy = started and health(config, args.health_timeout)
    if interactive and healthy:
        try:
            config = guided(paths, since)
        except (KeyboardInterrupt, EOFError):
            say('\nStopped the guided setup; everything done so far is saved. Pick it up with: assistant doctor')
            config = json.loads(paths.config.read_text())
    next_steps(config, healthy, started, paths)
    return 0 if (args.no_start and started is None) or (started and healthy) else 1


if __name__ == '__main__':
    sys.exit(main())
