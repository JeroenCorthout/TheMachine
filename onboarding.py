"""Guided setup shared by install.sh and the `assistant` CLI.

    assistant connect gmail     ask, write config and credential files (mode 600), find IDs, test, first sync
    assistant doctor            check the whole setup; every problem comes with its fix

Reads OpenClaw's state read-only (approved Telegram senders, paired nodes, pairing requests).
The only OpenClaw change it can make is approving your own Telegram pairing request, and only
after you say yes. Secrets are read without echo, written only to files with mode 600 in the
data directory, and never printed.
"""
import datetime as dt
import getpass
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path

import message_seal
import portable
import service

ROOT = Path(__file__).resolve().parent
UNIT_NAME = service.UNIT_NAME
# Connector name -> account id in config.example.json.
CONNECTORS = {'gmail': 'work-gmail', 'calendar': 'work-calendar', 'slack': 'work-slack',
              'crm': 'work-crm', 'android': 'personal-android', 'whatsapp': 'personal-whatsapp', 'gateway': 'openclaw-gateway'}
SERVICES = {'gmail': 'gmail', 'calendar': 'calendar', 'outlook': 'outlook', 'outlook_calendar': 'outlook_calendar',
            'outlook-calendar': 'outlook_calendar', 'slack': 'slack', 'crm': 'salesflare', 'salesflare': 'salesflare',
            'android': 'android', 'whatsapp': 'whatsapp', 'gateway': 'gateway'}
LABELS = {'gmail': 'Gmail', 'calendar': 'Google Calendar', 'outlook': 'Outlook', 'outlook_calendar': 'Outlook Calendar', 'slack': 'Slack', 'salesflare': 'Salesflare CRM',
          'android': 'Android notifications', 'whatsapp': 'WhatsApp', 'gateway': 'OpenClaw Gateway log'}
# What each built-in source brings in, in one short line (a connector's own is SETUP['what']). The dashboard's Add tab
# and a hosted control plane's setup page show it under the name.
WHAT = {'gmail': 'Your email, what comes in and what you send.', 'calendar': 'Your meetings, so you go in prepared and follow up.',
        'outlook': 'Your Outlook email, what comes in and what you send.',
        'outlook_calendar': 'Your Outlook meetings, so you go in prepared and follow up.',
        'slack': 'Your direct messages, mentions and the threads you are in.',
        'salesflare': 'Accounts, contacts and deals, so it knows who matters.',
        'android': 'The notifications on your Android phone, through the OpenClaw app.',
        'whatsapp': 'Your WhatsApp chats, as a linked device.', 'gateway': "Your server's own log, so The Machine spots its own trouble."}
# Microsoft 365 and Outlook.com (ms_graph.py): your own app registration and a device-code sign-in. A hosted control
# plane adds them with "transport": "broker" instead.
MICROSOFT = {'outlook': {'id': 'work-outlook', 'service': 'outlook', 'transport': 'graph', 'email': 'alex@example.com',
                         'boundary': 'work:Acme'},
             'outlook_calendar': {'id': 'work-outlook-calendar', 'service': 'outlook_calendar', 'transport': 'graph',
                                  'email': 'alex@example.com', 'calendar_id': 'primary', 'boundary': 'work:Acme',
                                  'exclude': []}}
# Values in config.example.json that mean "not filled in yet".
PLACEHOLDERS = {'email': 'alex@example.com', 'connection_id': '00000000-0000-0000-0000-000000000000',
                'team_id': 'T00000000', 'user_id': 'U00000000', 'node_id': 'replace-with-your-openclaw-node-id',
                'identity_sha256': 'sha256-of-your-linked-whatsapp-jid'}
# Per-account identity: never copied when a new account starts from one you already have.
IDENTITY = {'id', 'email', 'owner_emails', 'display_name', 'connection_id', 'team_id', 'user_id', 'slack_host', 'node_id',
            'identity_sha256', 'store', 'calendar_id', 'handle', 'login', 'url', 'workspace_id', 'stripe_account',
            'exclude', 'excluded_terms', 'scope', 'browser_profile', 'browser_cdp', 'connected_at', 'boundary_by'}
# The source is set up but can't serve right now (shared browser in use, rate limit): saved anyway, and the
# collector's own retry reads it, instead of failing setup over a moment's contention.
BUSY = ('x_browser_busy', 'linkedin_browser_busy', 'x_rate_limited', 'outlook:microsoft_signin_pending', 'microsoft_broker_busy')
# Phone apps whose notifications repeat a source. Setup offers to skip them on the Android account, never by
# default: the phone app may also show inboxes or workspaces that nothing here reads.
PHONE_APPS = {'gmail': ['com.google.android.gm'], 'calendar': ['com.google.android.calendar'],
              'outlook': ['com.microsoft.office.outlook'], 'outlook_calendar': ['com.microsoft.office.outlook'],
              'whatsapp': ['com.whatsapp', 'com.whatsapp.w4b'], 'slack': ['com.Slack'], 'salesflare': ['org.salesflare.app'],
              'linkedin': ['com.linkedin.android'], 'x': ['com.twitter.android'], 'github': ['com.github.android'],
              'stripe': ['com.stripe.android.dashboard']}
# Redacted sync error labels -> what to do about them (first matching prefix wins).
HINTS = (
    ('account_identity_mismatch', 'the sign-in belongs to a different account than config.json says'),
    ('connection_identity_mismatch', 'Maton answered for a different connection; check connection_id'),
    ('gmail:HTTP_403', 'enable the Gmail API in your Google Cloud project, or authorize again'),
    ('calendar:HTTP_403', 'enable the Google Calendar API in your Google Cloud project, or authorize again'),
    ('outlook:microsoft_signin_pending', 'Microsoft is still taking the new sign-in; it retries by itself'),
    ('microsoft_broker_busy', 'Microsoft is still taking the new sign-in; it retries by itself'),
    ('outlook:microsoft_signin_expired', 'the Microsoft sign-in expired or was revoked: connect the account again'),
    ('outlook:not_signed_in', 'sign in to Microsoft: assistant connect outlook'),
    ('microsoft_graph_not_configured', 'set your app registration: assistant connect outlook'),
    ('microsoft_scope_not_granted', 'the Microsoft account did not grant this; connect it again'),
    ('microsoft_broker', 'the control plane could not give a Microsoft token; connect the account again from your account page'),
    ('outlook:HTTP_403', 'Microsoft refused: check the app registration\'s API permissions, then connect again'),
    ('outlook', 'Microsoft Graph did not answer as expected; it retries by itself'),
    ('gmail', 'gog could not read Gmail: authorize again, and check the keyring password file'),
    ('calendar', 'gog could not read the calendar: authorize again, and check the keyring password file'),
    ('slack_invalid_auth', 'Maton rejected the API key'),
    ('slack_token_revoked', 'the Slack sign-in in Maton was revoked; reconnect Slack in Maton'),
    ('slack_credential_unavailable', 'the Maton credential file needs exactly one MATON_API_KEY=… line'),
    ('slack', 'Slack through Maton failed; check the key and connection in Maton'),
    ('missing_salesflare_credential', 'the Salesflare credential file needs a SALESFLARE_API_KEY=… line'),
    ('salesflare:HTTP_401', 'Salesflare rejected the API key'),
    ('whatsapp_source_unavailable', 'wacli could not read its store; is it linked (wacli auth status)?'),
    ('whatsapp', 'wacli answered unexpectedly; try wacli doctor'),
    ('android_offline', 'the phone is not connected to OpenClaw right now; open the OpenClaw app on it'),
    ('android_notifications_unavailable', 'allow notification access for the OpenClaw app on the phone'),
    ('android_identity_mismatch', 'node_id is not a paired Android node; pick it again'),
    ('android', 'the OpenClaw Gateway could not reach the phone'),
    ('x_browser_busy', 'the OpenClaw browser was busy; it retries by itself'),
    ('linkedin_browser_busy', 'the OpenClaw browser was busy; it retries by itself'),
    ('network:', 'a network error; it retries by itself'),
    ('timeout', 'the source was slow to answer; it retries by itself'),
)


class SetupError(Exception):
    def __init__(self, message, fix=''):
        self.message, self.fix = message, fix
        super().__init__(message + (f' (fix: {fix})' if fix else ''))


class UI:
    """Terminal questions. Without a terminal every question takes its default and nothing is approved."""
    def __init__(self, interactive=True):
        self.interactive = interactive

    def say(self, text=''):
        print(text, flush=True)

    def ask(self, prompt, default=''):
        if not self.interactive:
            return default
        answer = input(f'{prompt}{f" [{default}]" if default else ""}: ').strip()
        return answer or default

    def secret(self, prompt):
        return getpass.getpass(prompt + ': ').strip() if self.interactive else ''

    def yes(self, prompt, default=True):
        if not self.interactive:
            return False
        answer = input(f'{prompt} [{"Y/n" if default else "y/N"}]: ').strip().lower()
        return default if not answer else answer.startswith('y')

    def choose(self, prompt, options):
        """options: [(value, label)]; one option is taken without asking."""
        if len(options) == 1:
            return options[0][0]
        for number, (_, label) in enumerate(options, 1):
            self.say(f'  {number}. {label}')
        while True:
            answer = self.ask(prompt, '1')
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][0]
            if not self.interactive:
                return options[0][0]


class WebUI:
    """The same questions, answered on the dashboard while `connect` runs inside the service (Connections, Add): each
    question waits there, up to `wait` seconds, for the owner's answer, and what it says is shown there as it goes.
    A key typed there only goes to the step that asked for it, never into these lines or a log."""
    interactive = True
    web = True  # steps that need a terminal (a QR code, a browser on this computer) say what to run instead
    reconnect = False  # signing an existing account in again: its address, access and calendar stay as they are

    def __init__(self, wait=900):
        self.lines, self.question, self.wait, self.cancelled = [], None, wait, False
        self._answer, self._asked, self._cond = None, 0, threading.Condition()

    def say(self, text=''):
        with self._cond:
            self.lines += [line.strip() for line in str(text).splitlines() if line.strip()]
            del self.lines[:-200]

    def view(self):
        with self._cond:
            return {'lines': list(self.lines), 'question': dict(self.question) if self.question else None}

    def answer(self, question_id, value):
        """The owner's answer to the question showing now; False when that question is no longer asked."""
        with self._cond:
            if not self.question or self.question['id'] != question_id:
                return False
            self._answer = [value]
            self._cond.notify_all()
            return True

    def cancel(self):
        with self._cond:
            self.cancelled = True
            self._cond.notify_all()

    def _ask(self, kind, prompt, default='', options=None):
        with self._cond:
            if self.cancelled:
                raise SetupError('Stopped')
            self._asked += 1
            self.question = {'id': self._asked, 'kind': kind, 'prompt': str(prompt), 'options': options,
                             'default': '' if kind == 'secret' else default}
            self._answer = None
            given = self._cond.wait_for(lambda: self._answer is not None or self.cancelled, self.wait)
            self.question = None
            if self.cancelled:
                raise SetupError('Stopped')
            if not given:
                raise SetupError('No answer in time', 'start again')
            return self._answer[0]

    def ask(self, prompt, default=''):
        answer = str(self._ask('ask', prompt, default) or '').strip()
        return answer or default

    def secret(self, prompt):
        return str(self._ask('secret', prompt) or '').strip()

    def yes(self, prompt, default=True):
        return bool(self._ask('yes', prompt, bool(default)))

    def choose(self, prompt, options):
        if len(options) == 1:
            return options[0][0]
        picked = self._ask('choose', prompt, 0, [str(label) for _, label in options])
        if not isinstance(picked, int) or not 0 <= picked < len(options):
            raise SetupError('Not one of the choices', 'start again')
        return options[picked][0]


def catalog(config):
    """The sources this install can add, with what each asks for and how many are connected already. `broker`:
    a hosted machine's control plane signs it in (its account page), not this install."""
    have = {}
    for account in config.get('accounts', []):
        have[account.get('service', 'gmail')] = have.get(account.get('service', 'gmail'), 0) + 1
    brokered = {'gmail', 'calendar'} if config.get('google_broker') else set()
    brokered |= {'outlook', 'outlook_calendar'} if config.get('microsoft_broker') else set()
    found = []
    for name in sources():
        service = SERVICES.get(name, name)
        setup = setup_of(service) or {}
        found.append({'name': name, 'service': service, 'label': label(service), 'note': setup.get('note'), 'what': setup.get('what'),
                      'connected': have.get(service, 0), 'broker': service in brokered})
    return found


def run(args, **kwargs):
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    try:
        return subprocess.run(args, timeout=kwargs.pop('timeout', 60), **kwargs)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 127, '', str(error))


ENV_LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=')


def env_names(path):
    """Variable names in a KEY=value credential file. Values are never read out."""
    try:
        lines = Path(path).expanduser().read_text().splitlines()
    except OSError:
        return []
    return [m[1] for m in map(ENV_LINE.match, lines) if m]


def set_env(path, variable, value):
    """Set one KEY=value line and keep every other line (other keys, comments) as it was."""
    path = Path(path).expanduser()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    done, out = False, []
    for line in lines:
        match = ENV_LINE.match(line)
        if match and match[1] == variable:
            if not done:
                out.append(f'{variable}={value}')
            done = True
        else:
            out.append(line)
    write_private(path, '\n'.join(out + ([] if done else [f'{variable}={value}'])) + '\n')


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def expand(value, home):
    if isinstance(value, str) and value.startswith('~/'):
        return str(Path(home) / value[2:])
    if isinstance(value, list):
        return [expand(v, home) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, home) for k, v in value.items()}
    return value


_assistant = None


def assistant():
    """assistant.py, loaded on first use (sources, credentials, connector plugins)."""
    global _assistant
    if _assistant is None:
        spec = importlib.util.spec_from_file_location('assistant', ROOT / 'assistant.py')
        _assistant = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_assistant)
    return _assistant


def home():
    return Path(os.environ.get('HOME') or Path.home())


def tool(config, name):
    path = (config or {}).get(name) or name
    return shutil.which(path) or (path if Path(path).is_file() else None)


# ---------------------------------------------------------------- Telegram

def telegram_id(value):
    text = re.sub(r'^(telegram|tg):', '', str(value).strip(), flags=re.I)
    return text if re.fullmatch(r'-?\d{3,20}', text) else None


def json5(text):
    """JSON with comments and trailing commas (openclaw.json is JSON5), without a JSON5 library."""
    out, i, string = [], 0, None
    while i < len(text):
        c = text[i]
        if string:
            out.append(c)
            if c == '\\':
                out.append(text[i + 1:i + 2])
                i += 1
            elif c == string:
                string = None
        elif c in '"\'':
            string = c
            out.append(c)
        elif text.startswith('//', i):
            i = text.find('\n', i)
            i = len(text) if i < 0 else i - 1
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = len(text) if end < 0 else end + 1
        else:
            out.append(c)
        i += 1
    cleaned = re.sub(r',(\s*[}\]])', r'\1', ''.join(out))
    cleaned = re.sub(r"'((?:[^'\\]|\\.)*)'", lambda m: json.dumps(m[1]), cleaned)
    return json.loads(re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1"\2":', cleaned))


def openclaw_dir(root=None):
    """OpenClaw's state directory: OPENCLAW_STATE_DIR, else ~/.openclaw."""
    if root is None and os.environ.get('OPENCLAW_STATE_DIR', '').strip():
        return Path(os.environ['OPENCLAW_STATE_DIR'].strip()).expanduser()
    return Path(root or home()) / '.openclaw'


def hooks_allowed(settings):
    """OpenClaw runs a non-bundled plugin's conversation hooks (review binding, recovery) only with this set."""
    entry = ((settings.get('plugins') or {}).get('entries') or {}).get('personal-assistant-bridge') or {}
    return bool((entry.get('hooks') or {}).get('allowConversationAccess'))


def openclaw_settings(root=None):
    """openclaw.json (OPENCLAW_CONFIG_PATH, else in the state directory), read-only; {} when unreadable."""
    path = Path(os.environ['OPENCLAW_CONFIG_PATH'].strip()).expanduser() if root is None and os.environ.get('OPENCLAW_CONFIG_PATH', '').strip() \
        else openclaw_dir(root) / 'openclaw.json'
    try:
        text = path.read_text()
    except OSError:
        return {}
    for parse in (json.loads, json5):
        try:
            settings = parse(text)
            return settings if isinstance(settings, dict) else {}
        except ValueError:
            continue
    return {}


def telegram_accounts(settings):
    """{account name: its settings} for OpenClaw's Telegram channel; the top level is 'default'."""
    channel = (settings.get('channels') or {}).get('telegram') or {}
    accounts = {k: v for k, v in (channel.get('accounts') or {}).items() if isinstance(v, dict)} if isinstance(channel, dict) else {}
    if isinstance(channel, dict) and (not accounts or channel.get('botToken') or channel.get('tokenFile')):
        accounts = {'default': accounts.get('default', channel), **accounts}
    return accounts


def telegram_account(owner, root=None):
    """The OpenClaw Telegram account (bot) the owner talks to: the one whose allowFrom has them,
    else 'default', else the only one there is."""
    accounts = telegram_accounts(openclaw_settings(root))
    for name, account in accounts.items():
        allow = account.get('allowFrom') or []
        if str(owner) in {telegram_id(a) for a in (allow if isinstance(allow, list) else [allow])}:
            return name
    return 'default' if 'default' in accounts or len(accounts) != 1 else next(iter(accounts))


def telegram_owners(config=None, root=None):
    """Telegram IDs OpenClaw already trusts, owner first: commands.ownerAllowFrom, then the
    channel's allowFrom, then approved pairing senders. Read-only; never prints the bot token."""
    found = []
    settings = openclaw_settings(root)
    if settings:
        owners = (settings.get('commands') or {}).get('ownerAllowFrom') or []
        found += [o for o in (owners if isinstance(owners, list) else [owners]) if str(o).lower().startswith(('telegram:', 'tg:'))]
        for scope in telegram_accounts(settings).values():
            allow = scope.get('allowFrom') or []
            found += allow if isinstance(allow, list) else [allow]
    state = Path((config or {}).get('openclaw_state') or openclaw_dir(root) / 'state/openclaw.sqlite').expanduser()
    if state.exists():
        try:
            with closing(sqlite3.connect(f'file:{state}?mode=ro', uri=True, timeout=2)) as db:
                found += [r[0] for r in db.execute("SELECT entry FROM channel_pairing_allow_entries "
                                                   "WHERE channel_key='telegram' ORDER BY sort_order")]
        except sqlite3.Error:
            pass
    return list(dict.fromkeys(i for i in map(telegram_id, found) if i))


def pairing_requests(openclaw):
    result = run([openclaw, 'pairing', 'list', 'telegram', '--json'], timeout=30)
    try:
        return json.loads(result.stdout).get('requests') or [] if result.returncode == 0 else None
    except (ValueError, AttributeError):
        return None


def telegram_from_pairing(openclaw, ui, wait=120):
    """Ask the owner to message the bot, then read the sender ID from OpenClaw's pairing request
    and offer to approve it. Returns the numeric ID or None."""
    before = pairing_requests(openclaw)
    if before is None:
        return None
    known = {(r.get('id'), r.get('code')) for r in before}
    ui.say('Send any message to your OpenClaw bot in Telegram now; I will pick up your ID from it.\n'
           f'Waiting up to {wait // 60} minutes (Ctrl+C to type the ID yourself)...')
    deadline, new = time.monotonic() + wait, []
    try:
        while not new and time.monotonic() < deadline:
            time.sleep(3)
            new = [r for r in pairing_requests(openclaw) or [] if (r.get('id'), r.get('code')) not in known and telegram_id(r.get('id', ''))]
    except KeyboardInterrupt:
        ui.say('')
        return None
    if not new:
        ui.say('No new message reached the bot. If you were approved before, nothing new shows up here.')
        return None
    request = new[-1]
    sender = telegram_id(request['id'])
    meta = request.get('meta') or {}
    who = ', '.join(str(v) for v in meta.values() if isinstance(v, (str, int)) and str(v).strip())[:80] if isinstance(meta, dict) else ''
    ui.say(f'Got a message from Telegram user {sender}' + (f' ({who})' if who else '') + '.')
    if request.get('code') and ui.yes('Approve this account in OpenClaw so your bot answers it?', True):
        result = run([openclaw, 'pairing', 'approve', 'telegram', str(request['code']), '--notify'], timeout=60)
        ui.say('  approved' if result.returncode == 0 else
               f'  could not approve it; run: openclaw pairing approve telegram {request["code"]}')
    return sender


# ---------------------------------------------------------------- detection for the wizard

def owner_name():
    return portable.owner_name()


def gog_env(config):
    env = os.environ.copy()
    env['GOG_KEYRING_BACKEND'] = 'file'
    password = Path(config.get('credentials', {}).get('gog-password') or '/nonexistent')
    if password.exists():
        env['GOG_KEYRING_PASSWORD'] = password.read_text().strip()
    env.pop('DBUS_SESSION_BUS_ADDRESS', None)
    return env


def gog_accounts(gog):
    """Google accounts gog already knows (email, client); no tokens are read."""
    if not gog:
        return []
    result = run([gog, 'auth', 'list', '--json', '--no-input'], timeout=20,
                 env={**os.environ, 'GOG_KEYRING_BACKEND': 'file', 'GOG_KEYRING_PASSWORD': ''})
    try:
        return [(a['email'], a.get('client') or 'default') for a in json.loads(result.stdout).get('accounts', []) if a.get('email')]
    except (ValueError, AttributeError, TypeError):
        return []


def gog_clients(gog):
    result = run([gog, 'auth', 'credentials', 'list', '--json'], timeout=20)
    try:
        clients = json.loads(result.stdout).get('clients') or []
        return sorted((c['client'] for c in clients), key=lambda name: not next(c.get('default') for c in clients if c['client'] == name))
    except (ValueError, AttributeError, TypeError, KeyError, StopIteration):
        return []


def android_nodes(openclaw):
    if not openclaw:
        return []
    result = run([openclaw, 'nodes', 'list', '--json'], timeout=30)
    try:
        return [n for n in json.loads(result.stdout).get('paired', []) if n.get('platform') == 'android' and n.get('nodeId')]
    except (ValueError, AttributeError):
        return []


def whatsapp_linked(wacli, store):
    if not wacli or not Path(store).is_dir():
        return None
    result = run([wacli, '--store', str(store), '--read-only', '--json', '--timeout', '15s', 'auth', 'status'],
                 timeout=20, env={**os.environ, 'WACLI_READONLY': '1'})
    try:
        data = json.loads(result.stdout).get('data') or {}
        return data.get('linked_jid') if data.get('authenticated') is True else None
    except (ValueError, AttributeError):
        return None


def suggested_sources(facts):
    """Default source list for the wizard from what is installed and linked on this machine."""
    chosen = ['gmail', 'calendar'] if facts.get('gog') else []
    if whatsapp_linked(facts.get('wacli'), home() / '.local/share/wacli'):
        chosen.append('whatsapp')
    if android_nodes(facts.get('openclaw')):
        chosen.append('android')
    return ','.join(chosen + (['gateway'] if gateway_unit() else []))


def discover(config):
    """[(what, command)] for accounts this machine already has that no source reads yet.
    Asks local tools only (gog, gh, OpenClaw, wacli); no tokens or messages are read."""
    accounts, found = config.get('accounts', []), []
    read = {str(a.get('email', '')).lower() for a in accounts if a.get('transport') == 'gog'}
    for email, _ in gog_accounts(tool(config, 'gog')):
        if email.lower() not in read:
            found.append((f'gog is signed in as {email}, but no source reads it', 'assistant connect gmail'))
    if not siblings(config, 'github') and shutil.which('gh') and setup_of('github'):
        login = run(['gh', 'api', 'user', '--jq', '.login'], timeout=20)
        if login.returncode == 0 and login.stdout.strip():
            found.append((f'gh is signed in as {login.stdout.strip()}', 'assistant connect github'))
    nodes = {a.get('node_id') for a in siblings(config, 'android')}
    for node in android_nodes(tool(config, 'openclaw')):
        if node['nodeId'] not in nodes:
            found.append((f'{node.get("displayName") or "an Android phone"} is paired with OpenClaw', 'assistant connect android'))
    linked = whatsapp_linked(tool(config, 'wacli'), home() / '.local/share/wacli')
    if linked and hashlib.sha256(linked.encode()).hexdigest() not in {a.get('identity_sha256') for a in siblings(config, 'whatsapp')}:
        found.append(('wacli has a linked WhatsApp', 'assistant connect whatsapp'))
    return found


# ---------------------------------------------------------------- config helpers

def load(path):
    try:
        return json.loads(Path(path).read_text())
    except OSError as error:
        raise SetupError(f'Cannot read {path}: {error.strerror}', 'run ./install.sh first') from None
    except ValueError as error:
        raise SetupError(f'{path} is not valid JSON: {error}', 'fix the file, or restore config.json.bak') from None


def save(path, config):
    path = Path(path)
    if path.exists():
        write_private(path.with_name(path.name + '.bak'), path.read_text())
    write_private(path, json.dumps(config, indent=2) + '\n')


def set_telegram(config_path, owner=None, remove_bot=False, restart=True, runner=None):
    """Turn The Machine's Telegram delivery on (owner = your Telegram user ID) or off (owner None).
    Off: cards, reminders and recaps reach you through the dashboard app instead. With remove_bot
    the OpenClaw Telegram account is deleted too, its bot token included. Returns notes to show."""
    runner = runner or run
    config = load(config_path)
    notes = []
    if owner is not None:
        owner = telegram_id(owner)
        if not owner:
            raise SetupError('That is not a Telegram user ID', 'a number like 123456789; your bot\'s pairing reply shows it')
        config['telegram_owner'] = owner
        bot = telegram_account(owner)
        if bot != 'default':
            config['telegram_account'] = bot
        else:
            config.pop('telegram_account', None)
        if owner not in telegram_owners(config):
            notes.append('message your bot; if it asks for pairing: openclaw pairing approve telegram <code>')
    else:
        account = str(config.get('telegram_account') or 'default')
        if remove_bot:
            openclaw = tool(config, 'openclaw')
            if not openclaw:
                raise SetupError('openclaw CLI not found, so the bot stays', 'install OpenClaw, or leave out --remove-bot')
            result = runner([openclaw, 'channels', 'remove', '--channel', 'telegram', '--account', account, '--delete'], timeout=120)
            if result.returncode:
                raise SetupError('OpenClaw could not remove the Telegram bot: ' + ((result.stderr or result.stdout or '').strip()[:300] or 'no details'),
                                 f'openclaw channels remove --channel telegram --account {account} --delete')
            notes.append(f'removed the Telegram bot ({account}) and its token from OpenClaw')
        config['telegram_owner'] = ''
        config.pop('telegram_account', None)
    save(config_path, config)
    if restart and service.available():
        service.restart(UNIT_NAME, run=runner)
    return notes


def data_dir(config):
    return Path(config['database']).expanduser().parent


def plugin(service):
    loader = getattr(assistant(), 'load_connector', None)
    return loader(service) if loader and service not in LABELS else None


def setup_of(service):
    """What `connect` asks for a non-built-in source: its connector's SETUP, or assistant.SETUPS
    for installs that build those sources in. A built-in one has only its 'what'."""
    if service in LABELS:
        return {'what': WHAT[service]}
    return getattr(plugin(service), 'SETUP', None) or (getattr(assistant(), 'SETUPS', None) or {}).get(service) or {}


def label(service):
    if service in LABELS:
        return LABELS[service]
    kinds = list((getattr(plugin(service), 'KINDS', None) or {}).values())
    return setup_of(service).get('label') or (kinds[0] if kinds else service.title())


def connect_name(account, config=None):
    name = 'crm' if account.get('service', 'gmail') == 'salesflare' else account.get('service', 'gmail')
    same = [a for a in (config or {}).get('accounts', []) if a.get('service', 'gmail') == account.get('service', 'gmail')]
    return name + (f' --account {account["id"]}' if len(same) > 1 else '')


def missing(config, account):
    """Why this account cannot sync yet ('' when it is filled in). Nothing is contacted."""
    if account.get('service', 'gmail') == 'gateway':
        return ''
    for key, value in PLACEHOLDERS.items():
        if account.get(key) == value:
            return f'{key} is still the example value'
    names = [account['credential']] if account.get('credential') else []
    if account.get('transport') == 'gog':
        names.append('gog-password')
    microsoft = account.get('service') in MICROSOFT
    if account.get('transport') == 'broker' and microsoft:
        if not (config.get('microsoft_broker') or {}).get('url'):
            return 'microsoft_broker url is not set'
        names.append('microsoft-broker')
    elif account.get('transport') == 'broker':
        if not (config.get('google_broker') or {}).get('url'):
            return 'google_broker url is not set'
        names.append('google-broker')
    if account.get('transport') == 'graph':
        if not (config.get('microsoft_graph') or {}).get('client_id'):
            return 'microsoft_graph client_id is not set'
        if not account.get('credential'):
            return 'not signed in to Microsoft yet'
    for name in names:
        path = config.get('credentials', {}).get(name)
        if not path or not Path(path).expanduser().exists():
            return f'no {name} credential yet'
        variable = account.get('credential_variable') if name == account.get('credential') else None
        if variable and variable not in env_names(path):
            return f'{variable} is not in {Path(path).name}'
    for key, *_ in setup_of(account.get('service', 'gmail')).get('ask', []):
        if not account.get(key):
            return f'{key} is not set'
    return ''


def hint(error):
    return next((text for prefix, text in HINTS if str(error or '').startswith(prefix)), '')


def siblings(config, service):
    return [a for a in config.get('accounts', []) if a.get('service', 'gmail') == service]


def slug(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or 'account'


WEBMAIL = {'gmail', 'googlemail', 'outlook', 'hotmail', 'live', 'msn', 'yahoo', 'icloud', 'me', 'mac', 'aol', 'proton',
           'protonmail', 'pm', 'gmx', 'web', 'mail', 'yandex', 'zoho', 'fastmail', 'hey', 'tutanota', 'skynet', 'telenet'}


def company_of(email):
    """A company name guessed from a work address (alex@acme.io -> Acme, sam@mail.acme.co.uk -> Acme); '' for webmail."""
    parts = str(email or '').rpartition('@')[2].lower().split('.')[:-1]
    if parts and parts[-1] in ('co', 'com', 'org', 'net', 'ac', 'gov') and len(parts) > 1:
        parts = parts[:-1]
    name = parts[-1] if parts else ''
    return '' if not name or name in WEBMAIL or name == 'example' else name.capitalize()


def boundary_label(text, default='work:Main'):
    """work:<Company> or personal:<name> from what someone typed: 'Acme' is work:Acme, 'personal' or 'me'
    is personal:me, and a full label is kept."""
    text = str(text or '').strip()
    kind, colon, name = text.partition(':')
    if colon and kind.strip().lower() in ('work', 'personal', 'owner') and name.strip():
        return f'{kind.strip().lower()}:{name.strip()}'
    if text.lower().rstrip(':') in ('personal', 'private', 'me', 'home', 'family'):
        return 'personal:' + ('family' if text.lower() == 'family' else 'me')
    if text.lower().rstrip(':') in ('', 'work'):
        return default
    return 'work:' + (name.strip() if colon else text)


def ask_boundary(ui, default):
    return boundary_label(ui.ask('Company name, or "personal" (stored as work:<Company> or personal:<name>)', default), default)


def scope_label(boundary):
    """How a boundary reads to the owner: work:Acme is Work · Acme, personal:me is Personal."""
    kind, _, name = str(boundary or '').partition(':')
    if kind == 'work' and name:
        return 'Work · ' + name
    if kind == 'personal':
        return 'Personal' if name in ('', 'me') else 'Personal · ' + name
    return str(boundary or '')


def same_signin(config, account):
    """The accounts that share this one's sign-in (its address: Gmail and Calendar of one Google account), itself included."""
    email = str(account.get('email') or account.get('handle') or '').strip().lower()
    if '@' not in email:
        return [account]
    return [a for a in config.get('accounts', []) if a is account or str(a.get('email') or a.get('handle') or '').strip().lower() == email]


def set_boundary(config_path, account_id, boundary):
    """The owner marks a sign-in Work (work:<Company>) or Personal: this account and the others on the same address
    get that boundary, marked as the owner's own choice (boundary_by: owner), which a Work Fleet's removal follows
    (work_removal.py). Returns the ids changed."""
    config = load(config_path)
    account = next((a for a in config.get('accounts', []) if a.get('id') == account_id), None)
    if not account:
        raise SetupError('That account is not connected here')
    if str(account.get('boundary') or '').startswith('owner:'):
        raise SetupError('This source belongs to your server itself')
    text = str(boundary or '').strip()
    if not text or len(text) > 80 or not re.fullmatch(r'[^\x00-\x1f<>]+', text):
        raise SetupError('Pick work with a company name, or personal')
    label = boundary_label(text, default='')
    if not label or label.startswith('owner:'):
        raise SetupError('Pick work with a company name, or personal')
    changed = []
    for a in same_signin(config, account):
        if str(a.get('boundary') or '').startswith('owner:'):
            continue
        a['boundary'], a['boundary_by'] = label, 'owner'
        changed.append(a['id'])
    save(config_path, config)
    return changed


def boundaries(config, service):
    """Boundaries a new account of this service could belong to, ones it does not cover yet first.
    Device and system boundaries (phone, WhatsApp, Gateway) are not offered for other sources."""
    devices = {'android', 'whatsapp', 'gateway'}
    known = [a['boundary'] for a in config.get('accounts', []) if a.get('boundary') and ':' in a['boundary']
             and not a['boundary'].startswith('owner:') and (a.get('service', 'gmail') not in devices or a.get('service') == service)]
    covered = {a.get('boundary') for a in siblings(config, service)}
    people = {a.get('boundary') for a in config.get('accounts', []) if a.get('service', 'gmail') in ('gmail', 'calendar', 'outlook', 'outlook_calendar', 'slack', 'salesflare')}
    # Boundaries with a mailbox or calendar (a company, a person) first, ones this source does not cover yet before
    # ones it does, then the busiest.
    return sorted(dict.fromkeys(known), key=lambda b: (b not in people, b in covered, -known.count(b)))


def mark(account, ui):
    """Work or personal as the owner answered it: their own marking (boundary_by), unless nobody was asked (a terminal
    without input, or a form that left it to a guess: ui.marks false)."""
    if getattr(ui, 'interactive', True) and getattr(ui, 'marks', True):
        account['boundary_by'] = 'owner'


def new_account(config, service, account_id=None, ui=None):
    """A new account for this service. It starts from one you already have (same transport, keys,
    client and settings, without its identity), else from the example; its boundary and id follow
    your own naming."""
    ui = ui or UI(False)
    same = siblings(config, service)
    if same:
        first_ask = next(iter(setup_of(service).get('ask', [])), ('',))[0]
        account = {k: v for k, v in same[-1].items() if k not in IDENTITY and k != first_ask}
        template_id = None
    elif service in MICROSOFT:
        account = dict(MICROSOFT[service])
        template_id = account['id']
    elif service in LABELS:
        name = next(k for k, v in SERVICES.items() if v == service)
        example = json.loads((ROOT / 'config.example.json').read_text())
        account = next(a for a in example['accounts'] if a['id'] == CONNECTORS[name])
        template_id = account['id']
    else:
        account = dict(setup_of(service).get('account') or {'id': service, 'service': service, 'boundary': 'work'})
        template_id = account['id']
    account = expand(json.loads(json.dumps(account)), home())
    account.pop('exclude', None)  # example noise rules are not yours
    options = boundaries(config, service)
    if service in ('android', 'whatsapp', 'gateway') and not same:
        pass  # keep the example's device boundary
    elif same or options:
        # Work (which company) or personal: the owner's own choice, which a Work Fleet's removal follows
        choice = ui.choose('Which part of your life is this account', [(b, scope_label(b)) for b in options]
                           + [(None, 'Another company, or personal')]) if options else None
        account['boundary'] = choice or ask_boundary(ui, account.get('boundary') if ':' in str(account.get('boundary')) else 'work:Main')
        mark(account, ui)
    elif str(account.get('boundary', '')).startswith('work') or ':' not in str(account.get('boundary', '')):
        account['boundary'] = ask_boundary(ui, account['boundary'] if ':' in str(account.get('boundary', '')) else 'work:Main')
        mark(account, ui)
    taken = {a['id'] for a in config.get('accounts', [])}
    suffix = 'crm' if service == 'salesflare' else service.replace('_', '-')
    # Your ids follow <boundary name>-<source> (acme-gmail): name this one the same way.
    yours = any(a['id'] == f"{slug(str(a.get('boundary', '')).split(':', 1)[-1])}-{'crm' if a.get('service') == 'salesflare' else a.get('service', 'gmail')}"
                for a in config.get('accounts', []))
    if account_id:
        account['id'] = account_id
    elif template_id and template_id not in taken and not yours:
        account['id'] = template_id
    else:
        account['id'] = f"{slug(str(account.get('boundary', '')).split(':', 1)[-1])}-{suffix}"
    base, number = account['id'], 2
    while account['id'] in taken:
        account['id'], number = f'{base}-{number}', number + 1
    if account.get('email') == PLACEHOLDERS['email']:
        emails = [a['email'] for a in config.get('accounts', []) if a.get('email') and a['email'] != PLACEHOLDERS['email']]
        nearby = [e for e in emails if any(a.get('email') == e and a.get('boundary') == account.get('boundary') for a in config['accounts'])]
        if nearby or emails:
            account['email'] = (nearby or emails)[0]
            if 'owner_emails' in account:
                account['owner_emails'] = [account['email']]
    if account.get('transport') == 'gog':
        account['client'] = account.get('client') if same else next(
            (a['client'] for a in config.get('accounts', []) if a.get('transport') == 'gog'), account.get('client', 'default'))
        config.setdefault('credentials', {}).setdefault('gog-password', str(data_dir(config) / 'gog-password'))
    if account.get('credential'):
        config.setdefault('credentials', {}).setdefault(account['credential'], str(data_dir(config) / f'{account["credential"]}.env'))
    return account


def saved_keys(config, family):
    """(credential name, variable) for keys of this family already saved in any credential file. Names only."""
    return [(name, variable) for name, path in sorted(config.get('credentials', {}).items())
            for variable in env_names(path) if variable.startswith(family) and re.search(r'(KEY|TOKEN|SECRET)$', variable)]


def store_secret(config, account, ui, prompt, family=None):
    """Ask for a key without echo and save it (mode 600). Keys of the same kind you already saved,
    in any credential file, are offered first by name. Other lines in a shared file are kept."""
    if family:
        saved = sorted(saved_keys(config, family), key=lambda o: o != (account.get('credential'), account.get('credential_variable')))
        if saved:
            choice = ui.choose(prompt, [(o, f'the saved {o[1]} ({Path(config["credentials"][o[0]]).name})') for o in saved]
                               + [(None, 'a new key')])
            if choice:
                account['credential'], account['credential_variable'] = choice
                return
    variable = account.get('credential_variable')
    shared = any(a.get('id') != account['id'] and a.get('credential') == account['credential']
                 and a.get('credential_variable') == variable for a in config.get('accounts', []))
    if shared:  # a new key must not replace the one other sources still use
        account['credential'] = account['id']
    name = account['credential']
    path = Path(config.setdefault('credentials', {}).setdefault(name, str(data_dir(config) / f'{name}.env'))).expanduser()
    has = path.exists() and (variable in env_names(path) if variable else bool(path.read_text().strip()))
    value = ui.secret(prompt + (' (Enter keeps the saved one)' if has else ''))
    if not value:
        if has:
            return
        raise SetupError(f'No {prompt.split(" (")[0]} given', 'rerun this when you have it')
    if variable:
        set_env(path, variable, value.split('=', 1)[1].strip() if value.startswith(variable + '=') else value)
    else:
        write_private(path, value + '\n')


def check(config, account):
    """Read the source once as the service would. Returns None when it works, else a redacted error label."""
    a = assistant()
    service = account.get('service', 'gmail')
    try:
        if service in ('gmail', 'outlook'):
            a.Gmail(config, account).profile()
        elif service in ('calendar', 'outlook_calendar'):
            a.Calendar(config, account).profile()
        elif service == 'slack':
            a.Slack(config, account).profile()
        elif service == 'salesflare':
            a.Salesflare(config, account).profile()
        elif service == 'whatsapp':
            a.WhatsApp(config, account).profile()
        elif service == 'android':
            a.Android(config, account).snapshot()
        elif service == 'gateway':
            return None if shutil.which('journalctl') else 'journalctl_missing'
        elif getattr(a, 'load_connector', None):
            plugin = a.load_connector(service)
            helpers = a.CONNECTOR_HELPERS
            if callable(getattr(plugin, 'check', None)):
                return plugin.check(config, account, helpers)
            if not hasattr(plugin, 'Source'):
                return 'source_check_unavailable'
            source = plugin.Source(config, account, helpers)
            if callable(getattr(source, 'verify', None)):
                source.verify()
            elif callable(getattr(source, 'profile', None)):
                source.profile()
            elif service in ('linkedin', 'x'):
                source.page(int(time.time() * 1000), {})
            else:
                return 'source_check_unavailable'
        else:
            factory = {'intercom': a.Intercom, 'github': a.GitHubSource, 'stripe': a.StripeSource,
                       'linkedin': a.LinkedIn, 'x': a.X}.get(service)
            if not factory:
                return 'source_check_unavailable'
            source = factory(config, account)
            if callable(getattr(source, 'verify', None)):
                source.verify()
            elif callable(getattr(source, 'profile', None)):
                source.profile()
            elif service in ('linkedin', 'x'):
                source.page(int(time.time() * 1000), {})
            else:
                return 'source_check_unavailable'
        return None
    except a.SourceError as error:
        return str(error)
    except (OSError, KeyError, TypeError, ValueError) as error:
        return 'setup_incomplete:' + type(error).__name__


# ---------------------------------------------------------------- per-source steps

def headless():
    return not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) or bool(os.environ.get('SSH_CONNECTION'))


def step_google(config, account, ui):
    gog = tool(config, 'gog')
    if not gog:
        raise SetupError('gog is not installed', 'install gog (gogcli), then run: assistant connect ' + account.get('service', 'gmail'))
    config['gog'] = config.get('gog') if config.get('gog') not in (None, 'gog') else gog
    known = gog_accounts(gog)
    own = account.get('email') if account.get('email') not in (None, '', PLACEHOLDERS['email']) else ''
    used = {str(a.get('email', '')).lower() for a in siblings(config, account.get('service', 'gmail')) if a['id'] != account['id']}
    nearby = [a['email'] for a in config.get('accounts', []) if a.get('transport') == 'gog' and a.get('boundary') == account.get('boundary')
              and a['id'] != account['id'] and a.get('email') not in (None, PLACEHOLDERS['email'])]
    unused = [e for e, _ in known if e.lower() not in used]
    email = own if own and getattr(ui, 'reconnect', False) else \
        ui.ask('Google account to read', own or next(iter(nearby + unused + [e for e, _ in known]), ''))
    if not email or '@' not in email:
        raise SetupError('No Google account given', 'rerun with the address you use for Gmail and Calendar')
    account['email'] = email
    if 'owner_emails' in account:
        account['owner_emails'] = sorted({email, *account['owner_emails']} - {PLACEHOLDERS['email']})
    clients = gog_clients(gog)
    client = next((c for e, c in known if e.lower() == email.lower() and c in clients), None)
    if not clients:
        ui.say('gog needs a Google OAuth client first. In the Google Cloud console: create a project, enable the Gmail\n'
               'and Google Calendar APIs, create an OAuth client of type "Desktop app" and download its JSON.\n'
               '(`gog auth setup` walks you through the same steps.)')
        path = ui.ask('Path to the downloaded client JSON')
        if not path or not Path(path).expanduser().is_file():
            raise SetupError('No OAuth client JSON', 'download it, then run: assistant connect ' + account.get('service', 'gmail'))
        client = account.get('client') or 'default'
        result = run([gog, 'auth', 'credentials', 'set', str(Path(path).expanduser()), '--client', client], timeout=30)
        if result.returncode:
            raise SetupError('gog could not store the OAuth client', 'gog auth credentials set <file> --client ' + client)
    elif not client:
        client = account.get('client') if account.get('client') in clients else clients[0]
        if len(clients) > 1:
            client = ui.choose('gog OAuth client', [(c, c) for c in clients])
    account['client'] = client
    password = Path(config.setdefault('credentials', {}).setdefault('gog-password', str(data_dir(config) / 'gog-password'))).expanduser()
    if not password.exists():
        # The service needs the keyring password too: reuse the one gog already uses here, never replace it.
        typed = os.environ.get('GOG_KEYRING_PASSWORD', '').strip()
        if typed:
            ui.say('  using your GOG_KEYRING_PASSWORD')
        elif known:
            typed = ui.secret('Password of your gog file keyring (it already holds ' + ', '.join(e for e, _ in known) + ')')
            if not typed:
                raise SetupError('No keyring password', 'run assistant connect again with the password your gog keyring uses '
                                 '(or export GOG_KEYRING_PASSWORD first)')
        else:
            typed = ui.secret('Password for the gog file keyring that keeps the Google token (Enter generates one)')
        write_private(password, (typed or secrets.token_urlsafe(24)) + '\n')
        ui.say('  keyring password saved to ' + str(password) + (' (generated)' if not typed else ''))
    if check(config, account) is None:
        ui.say(f'  {email} is already authorized')
    else:
        sign_in(config, account, gog, ui)
    if account.get('service') == 'calendar':
        pick_calendar(config, account, ui)


def sign_in(config, account, gog, ui):
    email, client = account['email'], account['client']
    ui.say('Signing in to Google with read-only access to Gmail and Calendar'
           + (' (open the link it prints, then paste the address you land on)' if headless() else ' (a browser opens)') + '...')
    args = [gog, 'auth', 'add', email, '--client', client, '--services', 'gmail,calendar', '--readonly', '--gmail-scope', 'readonly']
    if getattr(ui, 'web', False):
        return sign_in_web(config, account, gog, ui, args)
    if headless():
        args.append('--manual')
    try:
        subprocess.run(args, env=gog_env(config), timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        pass


def gog_same_scopes(config, gog, email, client):
    """Flags for `gog auth add` that ask Google for exactly the scopes this address's sign-in holds now, checked with
    gog's own dry run, so signing in again never widens or narrows what it may do. None when they cannot be matched."""
    listed = run([gog, 'auth', 'list', '--json', '--no-input'], env=gog_env(config), timeout=30)
    try:
        entry = next(a for a in json.loads(listed.stdout).get('accounts', [])
                     if str(a.get('email', '')).lower() == email.lower() and (a.get('client') or 'default') == client)
    except (ValueError, AttributeError, TypeError, StopIteration):
        return None
    have, services = set(entry.get('scopes') or []), [str(s) for s in entry.get('services') or []]
    if not have or not services:
        return None

    def asked(flags):
        result = run([gog, 'auth', 'add', email, '--client', client, *flags, '--dry-run', '--json', '--no-input'],
                     env=gog_env(config), timeout=30)
        try:
            return set(json.loads(result.stdout)['request']['scopes'])
        except (ValueError, KeyError, TypeError):
            return None

    gmail = ('readonly', 'full', 'send', 'read-send') if 'gmail' in services else (None,)
    drive = ('full', 'readonly', 'file') if 'drive' in services else (None,)
    tries = sorted(((r, g, d) for r in (True, False) for g in gmail for d in drive),  # the dashboard's own sign-in first, then gog's default
                   key=lambda t: 0 if t[0] and t[1] in ('readonly', None) else 1 if not t[0] and t[1] in ('full', None) and t[2] in ('full', None) else 2)
    for readonly, gmail_mode, drive_mode in tries:
        flags = ['--services', ','.join(services)] + (['--gmail-scope', gmail_mode] if gmail_mode else []) + (
            ['--drive-scope', drive_mode] if drive_mode else []) + (['--readonly'] if readonly else [])
        got = asked(flags)
        if got is None or not got <= have:
            continue
        if have - got:
            flags += ['--extra-scopes', ','.join(sorted(have - got))]
        if asked(flags) == have:
            return flags
    return None


def sign_in_web(config, account, gog, ui, args):
    """The same read-only Google sign-in from the dashboard: gog's remote flow (the address to open, then the address
    Google sends you back to, pasted). An address gog holds already (signing in again after it expired) asks for exactly
    the scopes it has now, never others; when those cannot be matched it stops and says what to run instead."""
    email, service = account['email'], account.get('service', 'gmail')
    again = email.lower() in {e.lower() for e, _ in gog_accounts(gog)}
    if again:
        ui.say('Checking what this Google sign-in may do now, to ask for the same again...')
        same = gog_same_scopes(config, gog, email, account['client'])
        if same is None:
            raise SetupError(f'{email} is signed in to gog already, with access this page cannot ask for again',
                             f'run this in a terminal on this computer: assistant connect {service}')
        args = [gog, 'auth', 'add', email, '--client', account['client'], *same, '--force-consent']
    first = run(args + ['--remote', '--step', '1'], env=gog_env(config), timeout=60)
    url = next(iter(re.findall(r'https://accounts\.google\.com/\S+', first.stdout + '\n' + first.stderr)), None)
    if first.returncode or not url:
        raise SetupError('gog could not start the Google sign-in', f'run this in a terminal on this computer: assistant connect {service}')
    ui.say('Open this address, sign in as ' + email + (' and allow access again.' if again else ' and allow read-only access.') + ' Google then sends you to an address '
           'that may not load: copy that whole address from the address bar.')
    ui.say(url)
    landed = ui.ask('The address Google sent you to').strip()
    if not landed.startswith(('http://', 'https://')):
        raise SetupError('That is not the address Google sent you to', 'start again')
    second = run(args + ['--remote', '--step', '2', '--auth-url', landed], env=gog_env(config), timeout=120)
    if second.returncode:
        raise SetupError('Google did not accept that sign-in', 'start again; the address works only once and for a few minutes')


def gog_calendars(config, account):
    """Calendars this Google account can see (its own, shared and subscribed ones). Read-only."""
    result = run([config['gog'], 'calendar', 'calendars', '--json', '--results-only', '--account', account['email'],
                  '--client', account['client'], '--no-input'], env=gog_env(config), timeout=30)
    try:
        items = json.loads(result.stdout)
    except ValueError:
        return []
    items = items if isinstance(items, list) else (items or {}).get('items') or []
    return [c for c in items if isinstance(c, dict) and c.get('id')]


def pick_calendar(config, account, ui):
    """Your own calendar unless you pick a shared one (a partner's, a team's)."""
    calendars = gog_calendars(config, account) if ui.interactive and not getattr(ui, 'reconnect', False) else []
    if len(calendars) < 2:  # signing in again keeps the calendar it reads
        return
    name = lambda c: c.get('summaryOverride') or c.get('summary') or c['id']
    calendars.sort(key=lambda c: (not c.get('primary'), not c.get('selected'), name(c).lower()))
    current = account.get('calendar_id', 'primary')
    options = [('primary' if c.get('primary') else c['id'], name(c) + (' (your own)' if c.get('primary') else '')) for c in calendars]
    options.sort(key=lambda o: o[0] != current)
    chosen = ui.choose('Which calendar', options)
    if chosen == 'primary':
        account.pop('calendar_id', None)
    else:
        account['calendar_id'] = chosen
        account.setdefault('display_name', next(text for value, text in options if value == chosen))


def maton_connections(config, account, app):
    """The active connections for this app in the owner's Maton, newest first; None when Maton cannot list them."""
    try:
        secret = assistant().credential(config, account['credential'])
        found = re.findall(r'^' + re.escape(account['credential_variable']) + r'=(.*)$', secret, re.M)
        request = urllib.request.Request('https://ctrl.maton.ai/connections?' + urllib.parse.urlencode({'app': app, 'status': 'ACTIVE'}),
                                         headers={'Authorization': 'Bearer ' + found[0].strip().strip('"\''), 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            items = json.load(response).get('connections')
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise SetupError('Maton rejected the API key', 'copy the API key again from maton.ai') from None
        return None
    except (OSError, KeyError, IndexError, ValueError, AttributeError):
        return None
    if not isinstance(items, list):
        return None
    items = [c for c in items if isinstance(c, dict) and isinstance(c.get('connection_id'), str) and c.get('status', 'ACTIVE') == 'ACTIVE']
    return [c['connection_id'] for c in sorted(items, key=lambda c: str(c.get('creation_time') or ''), reverse=True)]


def maton_connection(config, account, ui, app, label, describe=None):
    """The Maton connection to read: found with the key. With several, the owner picks one (by what it is, when
    `describe` can tell); only when Maton cannot list them is the ID asked."""
    found = maton_connections(config, account, app)
    if found is None:
        connection = ui.ask(f'Maton {label.split()[0]} connection ID', account.get('connection_id', ''))
        if not connection:
            raise SetupError('No Maton connection ID given', f'copy it from Maton, then rerun: assistant connect {account["service"]}')
        return connection
    if not found:
        raise SetupError(f'Maton has no {label.split()[0]} connection yet',
                         f'connect {label.split()[0]} at maton.ai, then rerun: assistant connect {account["service"]}')
    if len(found) == 1:
        return found[0]
    taken = {a.get('connection_id') for a in config.get('accounts', []) if a.get('id') != account['id']}
    found.sort(key=lambda c: (c != account.get('connection_id'), c in taken))
    return ui.choose(f'Which {label} in Maton', [(c, (describe(c) if describe else c) + (' (already connected)' if c in taken else ''))
                                                for c in found])


def explain(ui, text):
    """How to get a key or sign in: for a terminal. A web form (ui.explains False) already said it next to its fields."""
    if getattr(ui, 'explains', True):
        ui.say(text)


def step_slack(config, account, ui):
    explain(ui, 'Slack is read through Maton (maton.ai), which holds the Slack sign-in.\n'
           'In Maton: connect Slack, then copy your API key; the Slack connection is found with it.')
    account.setdefault('credential_variable', 'MATON_API_KEY')
    store_secret(config, account, ui, 'Maton API key', 'MATON_')
    if account.get('connection_id') == PLACEHOLDERS['connection_id']:
        account.pop('connection_id')

    def workspace(connection):
        try:
            me = assistant().Slack(config, {**account, 'connection_id': connection}).get('auth.test')
        except assistant().SourceError:
            return connection
        return ' as '.join(x for x in ((urllib.parse.urlparse(me.get('url') or '').netloc or me.get('team')), me.get('user')) if x) or connection
    account['connection_id'] = maton_connection(config, account, ui, 'slack', 'Slack workspace', workspace)
    slack = assistant().Slack(config, account)
    try:
        me = slack.get('auth.test')
        user = slack.get('users.info', {'user': me['user_id']}).get('user') or {}
    except assistant().SourceError as error:
        raise SetupError(f'Slack did not answer: {error}', hint(error)) from None
    account.update(team_id=me['team_id'], user_id=me['user_id'], email=(user.get('profile') or {}).get('email') or account.get('email', ''))
    if me.get('url'):
        account['slack_host'] = urllib.parse.urlparse(me['url']).netloc or account.get('slack_host')
    ui.say(f'  Slack: {account["email"] or me["user_id"]} in {account.get("slack_host") or me["team_id"]}')


SALESFLARE_API = 'https://api.salesflare.com'


def salesflare_address(text):
    """An API address typed for another Salesflare server, as https://host[/path]. Only https: the key goes there."""
    text = str(text or '').strip()
    parsed = urllib.parse.urlparse(text if '://' in text else 'https://' + text)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SetupError(f'{text} is not an https address', 'type its https address, such as https://api.example.com')
    return f'https://{parsed.netloc}{parsed.path}'.rstrip('/')


def salesflare_app_base(api_base):
    """The web app next to an API address: stagingapi.x -> staging.x, api.x -> app.x."""
    host = urllib.parse.urlparse(api_base).netloc
    first, _, rest = host.partition('.')
    return 'https://' + ((first.replace('api', '') or 'app') + '.' + rest if rest else host)


def salesflare_preset(config):
    """The Salesflare server this install's new Salesflare accounts start on (`salesflare_server`, set by a hosted
    machine's control plane for a team on another server, such as staging), or None. Only https addresses on
    salesflare.com subdomains count; anything else is ignored, so new accounts start on Salesflare's own API."""
    preset = config.get('salesflare_server')
    if not isinstance(preset, dict):
        return None
    out = {}
    for field in ('api_base', 'app_base'):
        value = preset.get(field)
        if not isinstance(value, str):
            return None
        parsed = urllib.parse.urlparse(value.strip())
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or not host.endswith('.salesflare.com') or parsed.port or parsed.username or parsed.password
                or parsed.path.strip('/') or parsed.query or parsed.fragment or parsed.netloc.lower() != host):
            return None
        out[field] = 'https://' + host
    return out


def step_salesflare(config, account, ui):
    explain(ui, 'Salesflare: create an API key in Salesflare\'s settings (API keys).')
    account.setdefault('credential_variable', 'SALESFLARE_API_KEY')
    store_secret(config, account, ui, 'Salesflare API key', 'SALESFLARE_')
    preset = salesflare_preset(config)
    new = account.get('id') not in {a.get('id') for a in config.get('accounts', [])}
    if preset and new and account.get('api_base') in (None, SALESFLARE_API):
        account.update(preset)  # a new account starts on the team's server, so its key is only sent there
    account.setdefault('api_base', SALESFLARE_API)
    while True:
        try:
            me = assistant().Salesflare(config, account).get('/me')
            break
        except assistant().SourceError as error:
            other = str(error) == 'salesflare:HTTP_401' and ui.ask(
                f'{account["api_base"]} rejected that key. Is it for another Salesflare server (staging, self-hosted)? '
                'Its API address, or Enter to stop')
            if not other:
                raise SetupError(f'Salesflare did not answer: {error}', hint(error)) from None
            if salesflare_address(other) == account['api_base']:
                raise SetupError(f'{account["api_base"]} rejected that key', 'check the key, or give the API address of the server it is for')
            account['api_base'] = salesflare_address(other)
            account['app_base'] = salesflare_app_base(account['api_base'])
    account.update(user_id=me['id'], team_id=(me.get('team') or {}).get('id'), email=me['email'])
    account['owner_emails'] = sorted({me['email'], *account.get('owner_emails', [])} - {PLACEHOLDERS['email']})
    ui.say(f'  Salesflare: {me["email"]}')


def step_whatsapp(config, account, ui):
    wacli = tool(config, 'wacli')
    if not wacli:
        raise SetupError('wacli is not installed', 'install wacli (https://wacli.sh), then run: assistant connect whatsapp')
    config['wacli'] = config.get('wacli') if config.get('wacli') not in (None, 'wacli') else wacli
    account['store'] = ui.ask('wacli store directory', account.get('store') or str(home() / '.local/share/wacli'))
    linked = whatsapp_linked(wacli, account['store'])
    if not linked and getattr(ui, 'web', False):  # the QR code to scan only shows in a terminal
        raise SetupError('WhatsApp is not linked yet', f'run this in a terminal on this computer and scan the code: '
                         f'{wacli} --store {account["store"]} auth; then add it here again')
    if not linked and ui.yes('WhatsApp is not linked yet. Link it now? (scan the QR code in WhatsApp > Linked devices)', True):
        Path(account['store']).mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([wacli, '--store', account['store'], 'auth'], timeout=900)
        except (OSError, subprocess.TimeoutExpired):
            pass
        linked = whatsapp_linked(wacli, account['store'])
    if not linked:
        raise SetupError('WhatsApp is not linked', f'wacli --store {account["store"]} auth, then: assistant connect whatsapp')
    account['identity_sha256'] = hashlib.sha256(linked.encode()).hexdigest()
    ui.say('  WhatsApp: linked')
    whatsapp_sync(wacli, account, ui)


def store_busy(store):
    """Whether another wacli (such as your own sync) holds the store: then that one keeps it synced."""
    try:
        with open(Path(store) / 'LOCK', 'a') as lock:
            portable.lock(lock, blocking=False)
            portable.unlock(lock)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def whatsapp_sync(wacli, account, ui):
    """wacli only receives messages while its sync runs: keep one running as a user service (systemd on Linux,
    launchd on macOS), the store's single writer. Sends hand off to it. Skipped when something else already syncs this store."""
    name = f"the-machine-wacli-{re.sub(r'[^A-Za-z0-9_-]', '-', account['id'])}.service"
    unit = service.definition_path(name, home())
    if not unit.exists() and store_busy(account['store']):
        ui.say('  WhatsApp: another wacli already keeps this store synced')
        return
    if not service.available():
        ui.say(f'  WhatsApp: keep it synced yourself: {wacli} --store {account["store"]} sync --follow')
        return
    if not unit.exists() and not ui.yes('Keep WhatsApp synced in the background (wacli sync as a user service)?', True):
        return
    parts = (wacli, '--store', account['store'], 'sync', '--follow', '--presence-mode', 'quiet',
             '--max-messages', '0', '--max-db-size', '2GB', '--max-reconnect', '10m')
    unit.parent.mkdir(parents=True, exist_ok=True)
    if service.manager() == 'launchd':
        unit.write_text(service.plist_text(parts, unit=name, base=home(), throttle=30))
        started = service.enable(name, run=run)
        ui.say(f'  WhatsApp: synced by {service.label(name)}' if not started.returncode else
               f'  WhatsApp: start it with {service.hint("enable", name)}')
        return
    command = ' '.join(shlex.quote(str(part)) for part in parts)
    unit.write_text('[Unit]\nDescription=The Machine: keep WhatsApp (wacli) synced\nAfter=network-online.target\n\n'
                    f'[Service]\nExecStart={command}\nRestart=always\nRestartSec=30\nMemoryMax=512M\n\n[Install]\nWantedBy=default.target\n')
    run(['systemctl', '--user', 'daemon-reload'], timeout=30)
    started = run(['systemctl', '--user', 'enable', '--now', unit.name], timeout=60)
    ui.say(f'  WhatsApp: synced by {unit.name}' if not started.returncode else f'  WhatsApp: start it with systemctl --user enable --now {unit.name}')


def step_android(config, account, ui):
    nodes = android_nodes(tool(config, 'openclaw'))
    if not nodes:
        raise SetupError('No Android phone is paired with OpenClaw',
                         'install the OpenClaw app on the phone, pair it with this Gateway (docs/getting-started.md, "Pairing your Android phone"), '
                         'allow notification access, then: assistant connect android')
    account['node_id'] = ui.choose('Which phone', [(n['nodeId'], f'{n.get("displayName") or n["nodeId"]}'
                                                   + ('' if n.get('connected') else ' (offline now)')) for n in nodes])
    name = next(n.get('displayName') for n in nodes if n['nodeId'] == account['node_id'])
    ui.say(f'  Android: {name or account["node_id"]}')


def gateway_units():
    """The OpenClaw Gateway's systemd user units here (any name with openclaw in it), gateway ones first."""
    if not shutil.which('systemctl'):
        return []
    units = run(['systemctl', '--user', 'list-unit-files', '--type=service', '--no-legend', '--plain', '*openclaw*'], timeout=15).stdout.split()
    return sorted((u for u in units if u.endswith('.service')), key=lambda u: 'gateway' not in u)


def gateway_unit():
    """The Gateway unit to read logs from, or None when the Gateway does not run under systemd here."""
    units = gateway_units()
    return units[0] if units and shutil.which('journalctl') else None


def step_gateway(config, account, ui):
    """The Gateway's own systemd unit, also when it is named differently here."""
    units = gateway_units()
    if account.get('unit') not in units and units:
        account['unit'] = units[0]
    if not units:
        raise SetupError('No OpenClaw Gateway systemd unit found',
                         'the Gateway source reads its journal; install it as a service (openclaw gateway install) or remove this source')
    account.setdefault('unit', 'openclaw-gateway.service')


def step_plugin(config, account, ui):
    setup = setup_of(account.get('service', 'gmail'))
    if setup.get('note') and not setup.get('maton'):
        explain(ui, setup['note'])
    for key, prompt, *rest in setup.get('ask', []):
        command = (setup.get('detect') or {}).get(key)
        detected = run(command, timeout=20).stdout.strip() if command and shutil.which(command[0]) else ''
        default = account.get(key) or detected or (rest[0].format(**account) if rest else '')
        account[key] = ui.ask(prompt, default)
        if not account[key]:
            raise SetupError(f'No {key} given', f'rerun: assistant connect {account.get("service", "gmail")}')
    own_key = account.get('transport') != 'maton' and Path(config.get('credentials', {}).get(account.get('credential', ''), '/nonexistent')).expanduser().exists()
    if setup.get('maton') and (not setup.get('secret') or (account.get('transport') == 'maton' or saved_keys(config, 'MATON_')) and ui.choose(
            f'How should it read {setup.get("label", account["service"])}', sorted(
                [('maton', 'through Maton, which you already use'), ('api', 'with its own key')],
                key=lambda o: o[0] != ('api' if own_key else 'maton'))) == 'maton'):
        account['transport'] = 'maton'
        account.setdefault('credential', 'maton')
        account.setdefault('credential_variable', 'MATON_API_KEY')
        store_secret(config, account, ui, 'Maton API key', 'MATON_')
        account['connection_id'] = maton_connection(config, account, ui, setup.get('maton_app') or account['service'],
                                                    setup.get('label', account['service']) + ' account')
    elif setup.get('secret'):
        if setup.get('note') and setup.get('maton'):
            explain(ui, setup['note'])
        if account.get('transport') == 'maton':
            account.update(transport=(setup.get('account') or {}).get('transport', 'api'))
            account.pop('connection_id', None)
            account.pop('credential_variable', None)
            account['credential'] = (setup.get('account') or {}).get('credential') or account['service']
        account.setdefault('credential', account.get('service', 'gmail'))
        store_secret(config, account, ui, setup['secret'], setup.get('family'))


def step_microsoft(config, account, ui):
    """Outlook mail or calendar. Hosted: the control plane's broker already holds the sign-in. Yourself: your own Entra app
    registration and a device-code sign-in; mail and calendar of one address share it."""
    import ms_graph
    own = account.get('email') if account.get('email') not in (None, '', PLACEHOLDERS['email']) else ''
    if account.get('transport') == 'broker':
        if not own:
            raise SetupError('No Microsoft account given', 'connect it from your account page')
        return
    app = config.setdefault('microsoft_graph', {})
    if not app.get('client_id'):
        explain(ui, 'Microsoft needs your own app registration once. In the Entra admin center (entra.microsoft.com), App '
                    'registrations, New registration: pick the account types you need, no redirect URI. Then Authentication: '
                    'Allow public client flows, Yes. Then API permissions, Microsoft Graph, Delegated: User.Read, Mail.Read and '
                    'Calendars.Read; add Mail.ReadWrite and Mail.Send to send what you approve.')
        app['client_id'] = ui.ask('Application (client) ID')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', app.get('client_id') or ''):
            config.pop('microsoft_graph', None)
            raise SetupError('No app registration given', 'rerun: assistant connect outlook')
        app['tenant'] = ui.ask('Tenant (common, organizations, consumers or your tenant ID)', app.get('tenant') or 'common')
    nearby = [a['email'] for a in config.get('accounts', []) if a.get('transport') == 'graph' and a['id'] != account['id']
              and a.get('email') not in (None, PLACEHOLDERS['email'])]
    again = bool(own) and getattr(ui, 'reconnect', False)  # signing in again: the same address and the same access
    email = own.lower() if again else ui.ask('Microsoft account to read', own or next(iter(nearby), '')).strip().lower()
    if '@' not in email:
        raise SetupError('No Microsoft account given', 'rerun with the address you use for Outlook')
    account['email'] = email
    name = ms_graph.credential_name(email)
    account['credential'] = name
    path = Path(config.setdefault('credentials', {}).setdefault(name, str(data_dir(config) / name))).expanduser()
    try:
        granted = set(json.loads(path.read_text()).get('scopes') or []) if path.exists() else set()
    except (OSError, ValueError):
        granted = set()
    send = 'Mail.Send' in granted if again and granted else ui.yes('Also send what you approve (and snooze those threads)?', True)
    wanted = set(ms_graph.SCOPES_READ) | (set(ms_graph.SCOPES_SEND) if send else set())
    if wanted <= {s for g in granted for s, answers in ms_graph.ANSWERS.items() if g in answers} and check(config, account) is None:
        ui.say(f'  {email} is already signed in')
        return
    try:
        refresh, granted, signed_in = ms_graph.device_signin(app, sorted(wanted - ({'Mail.Read'} if send else set())), ui.say)
    except ms_graph.GraphError as error:
        raise SetupError(f'Microsoft sign-in failed: {error}', 'rerun: assistant connect outlook')
    if signed_in != email:
        raise SetupError(f'You signed in as {signed_in}, not {email}', 'rerun and sign in with ' + email)
    ms_graph.save_signin(path, refresh, granted)
    ui.say('  signed in; the sign-in is saved to ' + str(path))


STEPS = {'gmail': step_google, 'calendar': step_google, 'outlook': step_microsoft, 'outlook_calendar': step_microsoft, 'slack': step_slack, 'salesflare': step_salesflare,
         'whatsapp': step_whatsapp, 'android': step_android, 'gateway': step_gateway}


def sources():
    extra = getattr(assistant(), 'connector_services', None)
    return sorted({*SERVICES} - {'salesflare', 'outlook-calendar'}) + (extra() if extra else sorted(getattr(assistant(), 'SETUPS', None) or {}))


def offer_phone_skip(config, account, ui):
    """Ask once per phone app that repeats a connected source whether the Android account should skip its notifications.
    Default no; a yes adds an explicit `packages` rule to that Android account."""
    if not ui.interactive:
        return
    others = [a for a in config.get('accounts', []) if a.get('id') != account.get('id')]
    phones = [account] if account.get('service') == 'android' else [a for a in others if a.get('service') == 'android']
    services = sorted({a.get('service', 'gmail') for a in others}) if account.get('service') == 'android' else [account.get('service', 'gmail')]
    for phone in phones:
        for service in services:
            packages = PHONE_APPS.get(service)
            skipped = {p for rule in phone.get('exclude') or [] if set(rule) == {'packages'} for p in rule['packages']}
            if not packages or set(packages) <= skipped:
                continue
            where = '' if phone is account else f' ({phone["id"]})'
            if ui.yes(f'  Skip {label(service)} notifications from the phone{where}? The {label(service)} source already reads them;'
                      ' say no if the phone app also shows accounts not connected here', default=False):
                phone.setdefault('exclude', []).append({'packages': packages})


SIGNIN_EVERY = 10  # seconds between looks while a connect waits on the owner signing in to the browser


def wait_signed_in(config, account, name, ui, error):
    """A source read through the browser, connected before the owner signed in there: a UI that can wait (the hosted
    setup page, `ui.wait_signin` seconds) keeps looking until they are in, then the connect goes on by itself.
    Returns the last check's error (None once signed in)."""
    ui.say(f'  waiting for you to sign in to {name} on your Machine...')
    until = time.monotonic() + float(ui.wait_signin)
    while error and error.endswith('_signed_out') and time.monotonic() < until:
        time.sleep(SIGNIN_EVERY)
        error = check(config, account)
    if not error:
        ui.say(f'  signed in to {name}')
    return error


def connect(config_path, name, ui, account_id=None, restart=True):
    """Set up one source: ask, write config and credentials, find IDs, read it once; then restart
    the service and show the first sync. Returns the account. Raises SetupError with a fix."""
    config = load(config_path)
    service = SERVICES.get(name, name)
    if service not in LABELS and not setup_of(service):
        raise SetupError(f'Unknown source {name}', 'choose one of: ' + ', '.join(sources()))
    same = siblings(config, service)
    if account_id:
        account = next((a for a in same if a['id'] == account_id), None)
    elif any(missing(config, a) for a in same):
        account = next(a for a in same if missing(config, a))
    elif same and ui.interactive:
        account = ui.choose(f'You already have {label(service)}', [(a, f'redo {a["id"]}' + (f' ({a["email"]})' if a.get('email') else ''))
                                                                   for a in same] + [(None, f'add another {label(service)} account')])
    else:
        account = same[0] if same else None
    existing = account is not None
    ui.say(f'\n{label(service)}' + (f' ({account["id"]})' if existing else ': new account'))
    account = json.loads(json.dumps(account)) if existing else new_account(config, service, account_id, ui)
    if not existing:
        ui.say(f'  saving it as {account["id"]} in {account.get("boundary")}')
    own = {field: os.environ[var] for field, var in (setup_of(service).get('browser_env') or {}).items() if os.environ.get(var)}
    if own and not any(account.get(field) for field in own):
        account.update(own)  # it reads in the browser it was signed in with, also once others are added
    STEPS.get(service, step_plugin)(config, account, ui)
    error = check(config, account)
    if error and error.endswith('_signed_out') and getattr(ui, 'wait_signin', 0):
        error = wait_signed_in(config, account, label(service), ui, error)
    if error and error.endswith('_signed_out'):
        # in plain words: the raw code and the browser jargon stay out of what the owner reads
        raise SetupError(f'You are not signed in to {label(service)} on your Machine yet',
                         f'sign in to {label(service)} in your Machine\'s browser, then connect again')
    if error and error.startswith(BUSY):
        ui.say(f'  ! not read yet: {hint(error) or error}. Saved; the first sync reads it.')
    elif error:
        own = next((text for prefix, text in (setup_of(service).get('hints') or {}).items() if error.startswith(prefix)), '')
        raise SetupError(f'{account["id"]} does not work yet: {error}', own or hint(error) or f'rerun: assistant connect {name}')
    offer_phone_skip(config, account, ui)
    config['accounts'] = [account if a['id'] == account['id'] else a for a in config.get('accounts', [])] + ([] if existing else [account])
    save(config_path, config)
    ui.say(f'  ✓ {label(service)} connected')
    if restart:
        restart_and_sync(config, [account['id']], ui, config_path=config_path)
    return account


def disconnect(config_path, account_id):
    """Stop reading one account: it leaves config.json (the old file is kept as config.json.bak). What it already
    brought in stays, and so do sign-ins and keys, which other accounts or tools may share. Returns the account."""
    config = load(config_path)
    account = next((a for a in config.get('accounts', []) if a.get('id') == account_id), None)
    if not account:
        raise SetupError('That account is not connected here')
    config['accounts'] = [a for a in config['accounts'] if a.get('id') != account_id]
    save(config_path, config)
    return account


# ---------------------------------------------------------------- service, first sync

def service_active(config_path=None):
    """Is the service running (and, given a config path, running with that config)?"""
    if not service.active(UNIT_NAME, run=run):
        return False
    if config_path is None:
        return True
    env = service.environment(UNIT_NAME, run=run)
    # Without ASSISTANT_CONFIG the service reads the default under its own HOME (not this process's).
    used = Path(env['ASSISTANT_CONFIG']) if env.get('ASSISTANT_CONFIG') else \
        Path(env.get('HOME') or portable.home_of_user()) / '.local/share/personal-assistant/config.json'
    return used.expanduser().resolve() == Path(config_path).expanduser().resolve()


def healthy(config, timeout=0):
    deadline = time.time() + timeout
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{config.get('port', 8787)}/health", timeout=3) as response:
                if json.loads(response.read()).get('ok'):
                    return True
        except (OSError, ValueError):
            pass
        if time.time() >= deadline:
            return False
        time.sleep(1)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def restart_and_sync(config, account_ids, ui, wait=90, config_path=None):
    if not service_active(config_path):
        ui.say(f'  The running service reads another config; restart the one that reads {config_path} to start syncing.'
               if config_path and service_active() else f'  Start syncing with: {service.hint("restart")}')
        return False
    since = now()
    result = service.restart(UNIT_NAME, run=run)
    if result.returncode or not healthy(config, 30):
        ui.say(f'  The service did not come back; see: {service.hint("logs")}')
        return False
    return first_sync(config, account_ids, since, ui, wait)


def database(config):
    path = Path(config.get('database', '')).expanduser()
    if not path.is_file():
        return None
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def first_sync(config, account_ids, since, ui, wait=90):
    """Wait for each account's first sync after `since` and show what came in. Read-only."""
    ui.say('First sync (Ctrl+C stops waiting; syncing goes on in the background):')
    deadline, seen = time.monotonic() + wait, {}
    try:
        while True:
            db = database(config)
            if db:
                with closing(db):
                    for account_id in account_ids:
                        row = db.execute('SELECT status,signals,error FROM sync_activity WHERE account_id=? AND at>=? '
                                         'ORDER BY at DESC LIMIT 1', (account_id, since)).fetchone()
                        if row:
                            seen[account_id] = dict(row)
            if len(seen) == len(account_ids) or time.monotonic() >= deadline:
                break
            time.sleep(2)
    except KeyboardInterrupt:
        ui.say('')
    week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    counts = {}
    db = database(config)
    if db:
        with closing(db):
            for account_id in account_ids:
                counts[account_id] = db.execute('SELECT count(*), sum(occurred_at>=?) FROM events WHERE account_id=? AND deleted=0',
                                                (week, account_id)).fetchone()
    ok = True
    for account_id in account_ids:
        row, (total, recent) = seen.get(account_id), counts.get(account_id) or (0, 0)
        if not row:
            ui.say(f'  … {account_id}: no sync finished yet; it may still be working. Check later: assistant sources')
        elif row['status'] == 'failed':
            ok = False
            ui.say(f'  ✗ {account_id}: {row["error"]}' + (f': {hint(row["error"])}' if hint(row['error']) else ''))
        else:
            ui.say(f'  ✓ {account_id}: {total} signals so far ({recent or 0} from the last 7 days)')
    return ok


# ---------------------------------------------------------------- dashboard, USER.md

def dashboard_served(config):
    """The tailnet URL that proxies to this service, from `tailscale serve status`, or None."""
    tailscale = portable.tailscale()
    if not tailscale:
        return None
    try:
        status = json.loads(run([tailscale, 'serve', 'status', '--json'], timeout=15).stdout or '{}')
    except ValueError:
        return None
    port = str(config.get('port', 8787))
    for host, web in (status.get('Web') or {}).items():
        for path, handler in ((web or {}).get('Handlers') or {}).items():
            target = str((handler or {}).get('Proxy', '')).rstrip('/')
            if target.endswith(':' + port) and ('127.0.0.1' in target or 'localhost' in target):
                return f'https://{host.removesuffix(":443")}{path.rstrip("/")}/'
    return None


def reachable(url):
    """Does anything answer HTTPS at this address (any status: login pages and 401s count)?"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=5):
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


def dashboard_url(config):
    """Where the dashboard is reachable: your own dashboard_host (Caddy, a custom domain, another
    proxy) when it answers, else the tailnet address from tailscale serve, else None."""
    served = dashboard_served(config)
    host = str(config.get('dashboard_host') or '')
    if host and (not served or urllib.parse.urlparse(served).netloc != host):
        url = f'https://{host}/{config.get("dashboard_path", "/").strip("/")}'.rstrip('/') + '/'
        if reachable(url):
            return url
    return served


def serve_command(config):
    """tailscale serve for the dashboard: on its own port (machine.ts.net:8443), so it is its own origin and
    the installed dashboard app never collides with OpenClaw's web app or anything else on :443."""
    https = str(config.get('dashboard_host') or '').rpartition(':')[2]
    path = config.get('dashboard_path', '/').rstrip('/') or '/'
    return ['tailscale' if shutil.which('tailscale') else portable.tailscale() or 'tailscale', 'serve', '--bg', f'--https={https if https.isdigit() else 443}', '--set-path', path,
            f'http://127.0.0.1:{config.get("port", 8787)}']


def serve_dashboard(config, ui):
    """Offer to publish the dashboard on the tailnet (only your tailnet can reach it)."""
    served = dashboard_url(config)
    if served:
        ui.say(f'Dashboard: {served}')
        return served
    if not portable.tailscale():
        return None
    command = serve_command(config)
    if not ui.yes(f'Make the dashboard reachable on your tailnet? ({" ".join(command)})', True):
        return None
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        output, failed = (result.stdout or '') + (result.stderr or ''), result.returncode
    except subprocess.TimeoutExpired as error:  # it waits when Serve still has to be enabled for the tailnet
        output, failed = ''.join(x.decode() if isinstance(x, bytes) else x or '' for x in (error.stdout, error.stderr)), 1
    except OSError as error:
        output, failed = str(error), 1
    served = dashboard_served(config)
    if failed or not served:
        link = re.search(r'https://login\.tailscale\.com/\S+', output)
        if link:
            ui.say(f'  Tailscale Serve is not enabled on your tailnet yet. Enable it at {link[0]}, then run:\n    ' + ' '.join(command))
        elif re.search(r'access denied|operator|permission', output, re.I):
            ui.say('  tailscale refused. Allow your user once with: sudo tailscale set --operator=$USER, then run:\n    ' + ' '.join(command))
        else:
            ui.say('  tailscale serve did not work' + (': ' + output.strip().splitlines()[-1][:300] if output.strip() else '')
                   + '\n    try it yourself: ' + ' '.join(command))
        return None
    ui.say(f'  Dashboard: {served} (add it to your phone\'s home screen)')
    return served


def user_md_path(workspace=None):
    return Path(workspace or os.environ.get('OPENCLAW_WORKSPACE') or home() / '.openclaw/workspace') / 'USER.md'


def user_md_is_template(path):
    """OpenClaw seeds USER.md with its template; that one still says nothing about the owner."""
    try:
        text = path.read_text()
    except OSError:
        return False
    return '# USER.md - User Model' in text and '- Prefer ...' in text and '# About me' not in text


def starter_user_md(config):
    companies = sorted({a['boundary'].split(':', 1)[1] for a in config.get('accounts', [])
                        if str(a.get('boundary', '')).startswith('work:') and ':' in a['boundary']})
    emails = sorted({a['email'] for a in config.get('accounts', []) if a.get('email') and a['email'] != PLACEHOLDERS['email']})
    first = (config.get('owner_name') or '').split(' ')[0]
    return '\n'.join([
        '# About me', '',
        f'- Name: {config.get("owner_name") or ""}',
        f'- Time zone: {config.get("timezone") or ""}',
        f'- Work: {", ".join(companies)}' if companies else '- Work: ',
        f'- Email: {", ".join(emails)}' if emails else '- Email: ',
        '- Role: ', '',
        '## How I write', '',
        '- Languages: ',
        '- Tone: (for example: short and friendly, first names, no exclamation marks)',
        f'- Sign-off: {first}', '',
        '## What matters to me', '',
        '- (customers, projects, people you never want to keep waiting)', '',
        '## Leave alone', '',
        '- (newsletters, tools or people the assistant should ignore)', ''])


PROFILE_HEADING = '## Observed from my messages'
PROFILE_MARK = '<!-- assistant profile '
# Empty starter lines a profile draft covers; they go when the draft is saved, so nothing says the same twice.
STARTER_BLANKS = {'role': r'- Role: *', 'languages': r'- Languages: *', 'tone': r'- Tone: *(?:\(for example[^\n]*\))? *',
                  'voices': r'- Tone: *(?:\(for example[^\n]*\))? *'}
STARTER_SECTIONS = {'matters': '## What matters to me\n\n- (customers, projects, people you never want to keep waiting)\n',
                    'leave_alone': '## Leave alone\n\n- (newsletters, tools or people the assistant should ignore)\n'}


def render_profile(profile, day):
    """The USER.md section for a profile draft (assistant profile)."""
    lines = [PROFILE_HEADING, f'{PROFILE_MARK}{day}' + (f' · from {profile["based_on"]}' if profile.get('based_on') else '')
             + ' · edit freely; delete this line to keep later drafts out -->', '']
    for key, title in (('role', 'Role'), ('tone', 'Tone'), ('greeting', 'Greeting'), ('sign_off', 'Sign-off'), ('habits', 'Habits')):
        if profile.get(key):
            lines.append(f'- {title}: {profile[key]}')
    if profile.get('languages'):
        lines.append('- Languages: ' + '; '.join(x.rstrip(' .;') for x in profile['languages']))
    if profile.get('examples'):
        lines.append('- Sounds like: ' + ' · '.join(f'"{e.strip(chr(34))}"' for e in profile['examples']))
    if profile.get('voices'):
        lines += ['', '### Per channel', *(f'- {channel}: {voice}' for channel, voice in profile['voices'].items())]
    for key, title in (('matters', 'What matters'), ('leave_alone', 'Leave alone')):
        if profile.get(key):
            lines += ['', f'### {title}', *(f'- {item}' for item in profile[key])]
    return '\n'.join(lines) + '\n'


def merge_profile(text, profile, day):
    """USER.md with the draft as its own section: replaces an earlier draft section whose marker line is
    still there, drops the empty starter lines it covers, and never touches anything the owner wrote.
    None when the owner made an earlier draft section their own (marker deleted)."""
    text = text or ''
    found = re.search(r'^' + re.escape(PROFILE_HEADING) + r'\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if found:
        if PROFILE_MARK not in found[1]:
            return None
        text = text[:found.start()] + text[found.end():]
    for key, pattern in STARTER_BLANKS.items():
        if profile.get(key):
            text = re.sub(r'^' + pattern + r'\n', '', text, flags=re.M)
    for key, section in STARTER_SECTIONS.items():
        if profile.get(key):
            text = text.replace(section, '')
    text = re.sub(r'\n{3,}', '\n\n', text).rstrip()
    return (text + '\n\n' if text else '') + render_profile(profile, day)


USER_MD_BUDGET = 4000  # OpenClaw chat sessions load this much of USER.md
# What goes first when a draft doesn't fit: the last examples, then the last people and projects, … the tone last.
PROFILE_DROP = ('examples', 'matters', 'leave_alone', 'habits', 'role', 'languages', 'greeting', 'sign_off', 'voices', 'tone')


def without_profile(text):
    """USER.md without an earlier draft section that still has its marker (the part a new draft keeps)."""
    found = re.search(r'^' + re.escape(PROFILE_HEADING) + r'\n(.*?)(?=^## |\Z)', text or '', re.M | re.S)
    return text[:found.start()] + text[found.end():] if found and PROFILE_MARK in found[1] else text or ''


def profile_room(text, limit=USER_MD_BUDGET):
    """Characters a draft section can use before USER.md passes what OpenClaw loads."""
    return limit - len(re.sub(r'\n{3,}', '\n\n', without_profile(text)).rstrip()) - 2


def fit_profile(text, profile, day, limit=USER_MD_BUDGET):
    """(merged USER.md, what was left out) with the draft trimmed to fit within limit, least useful
    lines first. Keeps at least one line; (None, []) when the owner made the section their own."""
    profile, left_out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in profile.items()}, []
    merged = merge_profile(text, profile, day)
    lines = lambda: sum(len(v) if isinstance(v, (list, dict)) else 1 for k, v in profile.items() if k in PROFILE_DROP and v)
    for key in PROFILE_DROP:
        while merged and len(merged) > limit and profile.get(key) and lines() > 1:
            if isinstance(profile[key], dict):  # voices: the least used channel goes first (they come most used first)
                channel, voice = profile[key].popitem()
                left_out.append(f'{channel} voice: {voice}')
                if not profile[key]:
                    profile.pop(key)
            elif isinstance(profile[key], list):
                left_out.append(f'{key.replace("_", " ")}: {profile[key].pop()}')
                if not profile[key]:
                    profile.pop(key)
            else:
                left_out.append(f'{key.replace("_", " ")}: {profile.pop(key)}')
            merged = merge_profile(text, profile, day)
    return merged, left_out


def says_how_you_write(path):
    """False for a USER.md that is only the starter: no draft section and the tone still blank."""
    try:
        text = path.read_text()
    except OSError:
        return False
    return PROFILE_HEADING in text or not re.search(r'^' + STARTER_BLANKS['tone'] + r'$', text, re.M)


def offer_user_md(config, ui, workspace=None):
    path = user_md_path(workspace)
    template = user_md_is_template(path)
    if path.exists() and not template:
        return False
    if not ui.yes(f'{"Add an About me section to" if template else "Write a starter"} {path} so your agent knows who you are? (edit it afterwards)', True):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    text = starter_user_md(config)
    if template:
        text = path.read_text().rstrip() + '\n\n' + text
    write_private(path, text)
    ui.say(f'  wrote {path}; fill in the empty lines when you have a minute')
    return True


# ---------------------------------------------------------------- doctor

def ago(stamp):
    try:
        seconds = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))).total_seconds()
    except ValueError:
        return str(stamp)
    for size, unit in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if seconds >= size:
            count = int(seconds // size)
            return f'{count} {unit}{"s" if count > 1 else ""} ago'
    return 'just now'


def doctor(config_path, workspace=None):
    """Every check as (mark, text, fix): ✓ fine, ! worth a look, ✗ broken. Contacts none of your
    accounts (only your own dashboard address); source health comes from the service's own sync records."""
    results = []
    add = lambda mark, text, fix='': results.append((mark, text, fix))
    try:
        config = load(config_path)
    except SetupError as error:
        return [('✗', error.message, error.fix)]
    add('✓', f'config {config_path}')
    for key in ('tailscale_owner', 'dashboard_host'):
        if not config.get(key):
            add('✗', f'{key} is empty in config.json', './install.sh, or set it in config.json')
    # Service
    if service.available():
        active = service_active()
        add('✓' if active else '✗', 'service ' + ('running' if active else 'not running'),
            '' if active else f'{service.hint("restart")}; then {service.hint("logs")}')
    up = healthy(config)
    add('✓' if up else '✗', f'health on 127.0.0.1:{config.get("port", 8787)} ' + ('OK' if up else 'not answering'),
        '' if up else service.hint('logs'))
    # OpenClaw bridge
    openclaw = tool(config, 'openclaw')
    if not openclaw:
        add('✗', 'openclaw CLI not found', 'install OpenClaw and rerun ./install.sh')
    else:
        try:
            plugins = json.loads(run([openclaw, 'plugins', 'list', '--json'], timeout=60).stdout or '{}').get('plugins', [])
        except (ValueError, AttributeError):
            plugins = None
        bridge = next((p for p in plugins or [] if p.get('id') == 'personal-assistant-bridge'), None)
        if plugins is None:
            add('!', 'could not list OpenClaw plugins', 'openclaw plugins list')
        elif not bridge:
            add('✗', 'OpenClaw bridge plugin not installed', './install.sh')
        elif not bridge.get('enabled') or bridge.get('status') not in (None, 'loaded'):
            add('✗', f'OpenClaw bridge plugin {"disabled" if not bridge.get("enabled") else bridge.get("status")}',
                'openclaw plugins enable personal-assistant-bridge && openclaw plugins reload personal-assistant-bridge --wait')
        elif not hooks_allowed(openclaw_settings()):
            add('✗', 'OpenClaw skips the bridge plugin\'s review hooks, so reviews never finish',
                'openclaw config set plugins.entries.personal-assistant-bridge.hooks.allowConversationAccess true --strict-json'
                ' && openclaw plugins reload personal-assistant-bridge --wait')
        else:
            add('✓', 'OpenClaw bridge plugin loaded')
    # Telegram (optional: without it cards reach you through the dashboard app)
    owners = telegram_owners(config)
    owner = str(config.get('telegram_owner') or '')
    if not owner:
        add('✓', 'Telegram off: cards reach you through the dashboard app (add it to your home screen and turn on notifications)')
    elif not owners:
        add('!', 'could not confirm your Telegram ID in OpenClaw', 'message your bot; if it asks for pairing: openclaw pairing approve telegram <code>')
    elif owner in owners:
        add('✓', f'Telegram {owner} is approved in OpenClaw')
    else:
        add('✗', f'telegram_owner {owner} is not an approved Telegram sender in OpenClaw (found {", ".join(owners)})',
            'set telegram_owner in config.json to your own ID, or approve it: openclaw pairing list telegram')
    bots = telegram_accounts(openclaw_settings()) if owner else []
    configured = str(config.get('telegram_account') or 'default')
    if bots and configured not in bots:
        add('✗', f'telegram_account {configured} is not a Telegram account in OpenClaw (there: {", ".join(bots)})',
            f'set "telegram_account": "{telegram_account(owner)}" in config.json, then {service.hint("restart")}')
    elif bots and owner and telegram_account(owner) != configured and len(bots) > 1:
        add('!', f'you are approved on the Telegram account {telegram_account(owner)}, but cards go out through {configured}',
            f'set "telegram_account": "{telegram_account(owner)}" in config.json, then {service.hint("restart")}')
    # Credentials (a source's own missing credential is reported with that source below)
    for name, path in sorted(config.get('credentials', {}).items()):
        path = Path(path).expanduser()
        if not path.exists() and name in ('api-token', 'owner-token'):
            add('✗', f'credential {name} missing', './install.sh')
        elif path.exists() and path.stat().st_mode & 0o077:
            add('!', f'credential {name} is readable by others', f'chmod 600 {path}')
    # Message contents encrypted in the database: the key must survive with it
    if config.get('database'):
        db_path = Path(config['database']).expanduser()
        found = message_seal.problems(db_path)
        for name, text, fix in found:
            add('✗' if name == 'message-key-missing' else '!', text, fix)
        sealed, _ = message_seal.state(db_path)
        if sealed and not found and message_seal.held_elsewhere(db_path):
            add('✓', 'messages encrypted; the key is kept off this disk by your hosting')
        elif sealed and not found:
            add('✓', f'messages encrypted; keep {message_seal.key_path(db_path)} in your backups with the database')
        elif sealed is False and not message_seal.wanted(config):
            add('!', 'messages are stored unencrypted ("message_protection": false in config.json)',
                f'remove that line, then {service.hint("restart")}')
        elif sealed is False:
            add('!', 'messages are not encrypted yet: that happens at the next start with nothing running', service.hint('restart'))
    # Sources
    db = database(config)
    checkpoints, counts, last_review = {}, {}, None
    if db:
        with closing(db):
            try:
                checkpoints = {r['account_id']: dict(r) for r in db.execute('SELECT * FROM checkpoints')}
                counts = dict(db.execute('SELECT account_id, count(*) FROM events WHERE deleted=0 GROUP BY account_id').fetchall())
                last_review = db.execute("SELECT max(started_at) FROM analyses").fetchone()[0]
            except sqlite3.Error:
                pass
    for account in config.get('accounts', []):
        name, title = connect_name(account, config), f'{account["id"]} ({label(account.get("service", "gmail"))})'
        reason = missing(config, account)
        point = checkpoints.get(account['id']) or {}
        if account.get('service') == 'gateway' and shutil.which('systemctl') and account.get('unit') not in gateway_units():
            add('✗', f'{title}: no systemd unit {account.get("unit")} here, so there is no Gateway log to read',
                f'assistant connect gateway (finds the unit), or remove {account["id"]} from config.json')
        elif reason:
            add('✗', f'{title}: not set up ({reason})', f'assistant connect {name}')
        elif not point.get('last_success') and point.get('error'):
            add('✗', f'{title}: {point["error"]}' + (f': {hint(point["error"])}' if hint(point['error']) else ''), f'assistant connect {name}')
        elif not point.get('last_success'):
            add('!', f'{title}: no sync yet', 'give it a few minutes after a restart; then assistant sources')
        elif point['last_success'] < (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat():
            add('✗' if point.get('error') else '!', f'{title}: last sync {ago(point["last_success"])}'
                + (f'; now failing with {point["error"]}' + (f': {hint(point["error"])}' if hint(point['error']) else '') if point.get('error') else ''),
                f'assistant connect {name}' if point.get('error') else 'assistant sources; ' + service.hint('logs'))
        elif point.get('error') and str(point.get('error_at') or '') >= str(point['last_success']):
            add('!', f'{title}: synced {ago(point["last_success"])}; the last try failed with {point["error"]}'
                + (f' ({hint(point["error"])})' if hint(point['error']) else ''), '' if hint(point['error']) else f'assistant connect {name}')
        else:
            add('✓', f'{title}: synced {ago(point["last_success"])}, {counts.get(account["id"], 0)} signals')
    if not config.get('accounts'):
        add('✗', 'no sources configured', 'assistant connect gmail (or any of: ' + ', '.join(sources()) + ')')
    # Reviews and delivery
    on = config.get('delivery_enabled') and (config.get('review_worker') or {}).get('enabled')
    if not on:
        add('!', 'reviews and Telegram cards are off', 'try one: assistant preview; turn them on: ./install.sh --activate')
    elif not last_review or last_review < (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat():
        add('!', 'no review in the last 2 days' if last_review else 'no review yet', 'assistant reviews; openclaw logs')
    else:
        add('✓', f'reviews on, last one {ago(last_review)}')
    # Dashboard and USER.md
    served = dashboard_url(config)
    if served:
        add('✓', f'dashboard {served}')
    else:
        add('!', f'dashboard not reachable at {config.get("dashboard_host") or "your dashboard_host"} nor in tailscale serve', ' '.join(serve_command(config)) + ' (or a custom domain: deploy/Caddyfile)')
    path = user_md_path(workspace)
    known = path.exists() and not user_md_is_template(path)
    if known and not says_how_you_write(path):
        add('!', f'{path} does not say how you write yet', 'assistant profile (drafts it from your own messages; you see it first)')
    else:
        add('✓' if known else '!', f'{path} ' + ('exists' if known else
            ('is still the OpenClaw template' if path.exists() else 'missing') + ': the agent does not know who you are'),
            '' if known else 'rerun ./install.sh for a starter one, or write it yourself')
    return results


def print_doctor(results, ui):
    for mark, text, fix in results:
        ui.say(f'  {mark} {text}')
        if fix and mark != '✓':
            ui.say(f'      fix: {fix}')
    broken, warn = sum(m == '✗' for m, *_ in results), sum(m == '!' for m, *_ in results)
    ui.say('\n' + ('All good.' if not broken and not warn else
                   ', '.join(x for x in (f'{broken} to fix' if broken else '', f'{warn} worth a look' if warn else '') if x) + '.'))
    return 1 if broken else 0
