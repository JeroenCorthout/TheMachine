"""Outlook mail and calendar (Microsoft 365 and Outlook.com) through Microsoft Graph.

Two ways to get a token, per account ("transport"):
- "broker": a service you run holds the Microsoft app and its secret (a hosted control plane, for example). The refresh
  token stays on this machine (mail_grants.py); it asks the broker for a short-lived access token for one account and
  one scope, sending the refresh token along and keeping the rotated one the broker passes back, like google_api.py:

    "microsoft_broker": {"url": "https://app.example.com/api/microsoft/token"},
    "credentials": {"microsoft-broker": "~/.local/share/personal-assistant/google-broker"}

  The service that connects a broker account sets its "connected_at" (an ISO time) on each connect.

- "graph": your own Entra app registration (public client flows on) and a device-code sign-in
  (`assistant connect outlook`). The refresh token stays in a mode-600 file named by the account's "credential":

    "microsoft_graph": {"client_id": "<your app's client ID>", "tenant": "common"}

Scopes are delegated only: User.Read, Mail.Read (or Mail.ReadWrite), Mail.Send, Calendars.Read. Mail.ReadWrite and
Mail.Send come together, for sending what the owner approves and snoozing that thread; reading needs neither.

Only the Graph calls below can be made; anything else is refused here, before a token is asked for. Errors carry a
category and an HTTP status, never content. Message IDs are immutable (Prefer: IdType="ImmutableId"), so a message
keeps its ID when it moves between folders.

The rest of the app speaks Gmail's shapes. `gmail_call` answers the handful of Gmail reads it makes (profile, message
list and history, a message in "full" format, the send-as signature) from Graph, so an Outlook mailbox flows through
the same sync, cards, drafts and attachments as a Gmail one."""
from __future__ import annotations

import base64
import datetime as dt
import email.parser
import email.policy
import portable
import json
import mail_grants
import os
import re
import threading
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GRAPH = 'https://graph.microsoft.com/v1.0'
LOGIN = 'https://login.microsoftonline.com'
SCOPES_READ = ('User.Read', 'Mail.Read', 'Calendars.Read')
SCOPES_SEND = ('Mail.ReadWrite', 'Mail.Send')
# An ask for the scope on the left is answered by any one on the right.
ANSWERS = {'User.Read': {'User.Read'}, 'Mail.Read': {'Mail.Read', 'Mail.ReadWrite'}, 'Mail.ReadWrite': {'Mail.ReadWrite'},
           'Mail.Send': {'Mail.Send'}, 'Calendars.Read': {'Calendars.Read', 'Calendars.ReadWrite'}}

# name -> (verb, path, scope)
READ = {
    'me': ('GET', '/me', 'User.Read'),
    'messages.list': ('GET', '/me/messages', 'Mail.Read'),
    'messages.get': ('GET', '/me/messages/{id}', 'Mail.Read'),
    'messages.mime': ('GET', '/me/messages/{id}/$value', 'Mail.Read'),
    'attachments.list': ('GET', '/me/messages/{id}/attachments', 'Mail.Read'),
    'attachments.value': ('GET', '/me/messages/{id}/attachments/{attachment}/$value', 'Mail.Read'),
    'folders.get': ('GET', '/me/mailFolders/{id}', 'Mail.Read'),
    'folders.list': ('GET', '/me/mailFolders', 'Mail.Read'),
    'folders.messages': ('GET', '/me/mailFolders/{id}/messages', 'Mail.Read'),
    'calendar.get': ('GET', '/me/calendar', 'Calendars.Read'),
    'calendars.get': ('GET', '/me/calendars/{id}', 'Calendars.Read'),
    'calendar.delta': ('GET', '/me/calendarView/delta', 'Calendars.Read'),
    'calendars.view': ('GET', '/me/calendars/{id}/calendarView', 'Calendars.Read'),
    'calendars.delta': ('GET', '/me/calendars/{id}/calendarView/delta', 'Calendars.Read'),
}
WRITE = {
    'messages.create': ('POST', '/me/messages', 'Mail.ReadWrite'),        # a draft from MIME (never sent by itself)
    'messages.send': ('POST', '/me/messages/{id}/send', 'Mail.Send'),
    'sendMail': ('POST', '/me/sendMail', 'Mail.Send'),
    'folders.create': ('POST', '/me/mailFolders', 'Mail.ReadWrite'),
    'messages.move': ('POST', '/me/messages/{id}/move', 'Mail.ReadWrite'),
    'messages.update': ('PATCH', '/me/messages/{id}', 'Mail.ReadWrite'),
}
LINK_HOSTS = ('outlook.office.com', 'outlook.office365.com', 'outlook.live.com')
# Right after connecting, Microsoft can refuse a fresh sign-in for a few minutes while it takes the new permissions:
# within this window a broker account's refused sign-in is retried with backoff (each wait as long as the account's age) before it
# counts as expired.
SETTLE_SECONDS = 900
MAX_RESPONSE = 40_000_000  # Never save an oversized response as a successful partial file.
_tokens: dict = {}
_folders: dict = {}
_lock = threading.Lock()


class GraphError(Exception):
    """A content-free failure: `status` is Graph's, the broker's or the sign-in's HTTP status, None when unreachable."""

    def __init__(self, category, status=None, retry_after=None):
        super().__init__(category)
        self.category, self.status, self.retry_after = category, status, retry_after


def _post_json(url, payload, headers, timeout=20):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={**headers, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(200_000))


def _post_form(url, form, timeout=30):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(200_000))


def _error_code(error):
    try:
        return json.loads(error.read(20_000) or b'{}').get('error')
    except (ValueError, OSError, AttributeError):
        return None
    finally:
        error.close()


# ------------------------------------------------------------------------------------------- tokens

def _broker(config, credential, payload, timeout=20):
    url = (config.get('microsoft_broker') or {}).get('url')
    if not url:
        raise GraphError('microsoft_broker_not_configured')
    try:
        return _post_json(url, payload, {'Authorization': 'Bearer ' + credential(config, 'microsoft-broker')}, timeout)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 401 and payload.get('scope'):
            raise GraphError('outlook:microsoft_signin_expired', 401, retry_after=3600) from None
        if error.code in (429, 503):  # the broker says when to ask again (a grant Microsoft is still settling, say)
            raise GraphError('microsoft_broker_busy', error.code, retry_after=_retry_after(error.headers)) from None
        raise GraphError('microsoft_scope_not_granted' if error.code == 403 else 'microsoft_broker', error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise GraphError('microsoft_broker_unreachable') from None


def _from_broker(config, credential, account, scope):
    """An access token through the broker, sending the sign-in kept here (mail_grants.py) and keeping the rotated one."""
    try:
        kept = mail_grants.get(config, 'microsoft', account['email'])
    except mail_grants.GrantError:
        raise GraphError('microsoft_signin_unreadable') from None
    answer = _broker(config, credential, {'email': account['email'], 'scope': scope, **({'refresh_token': kept} if kept else {})})
    if kept and answer.get('refresh_token') and answer['refresh_token'] != kept:  # Microsoft rotates them: keep the newest
        mail_grants.rotate(config, 'microsoft', account['email'], kept, answer['refresh_token'])
    return answer


def _retry_after(headers):
    try:
        return max(15, min(900, int((headers or {}).get('Retry-After') or 60)))
    except (TypeError, ValueError):
        return 60


def connected_age(config, account):
    """Seconds since a broker account was (re)connected (its "connected_at"); None when unknown."""
    try:
        stamp = account.get('connected_at') if account.get('transport') == 'broker' else None
        return time.time() - dt.datetime.fromisoformat(stamp).timestamp() if stamp else None
    except (TypeError, ValueError):
        return None


def connected_since(config, account, since):
    """True when the account was connected again after `since` (an ISO time): its own-app sign-in written again, or
    the broker account connected again."""
    try:
        when = (credential_path(config, account).stat().st_mtime if account.get('transport') == 'graph'
                else time.time() - connected_age(config, account))
        return when > dt.datetime.fromisoformat(since).timestamp()
    except (OSError, TypeError, ValueError, GraphError):
        return False


def credential_path(config, account):
    name = account.get('credential') or credential_name(account.get('email', ''))
    path = (config.get('credentials') or {}).get(name)
    if not path:
        raise GraphError('outlook:not_signed_in_run_assistant_connect_outlook')
    return Path(path).expanduser()


def credential_name(email):
    return 'microsoft-' + ''.join(c if c.isalnum() else '-' for c in email.lower()).strip('-')


def _saved(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise GraphError('outlook:not_signed_in_run_assistant_connect_outlook') from None


def save_signin(path, refresh_token, scopes):
    """Write the sign-in (mode 600, atomically): the refresh token and the scopes Microsoft granted."""
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        portable.lock(lock)
        _write_signin(path, refresh_token, scopes)


def _write_signin(path, refresh_token, scopes):
    """Atomic write while the caller holds the credential lock (including owner reconnects)."""
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump({'refresh_token': refresh_token, 'scopes': sorted(scopes)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def short_scopes(text):
    """Graph's short scope names from a token answer's space-separated, full-URL ones."""
    return {s.rsplit('/', 1)[-1] for s in str(text or '').split() if s.startswith('https://graph.microsoft.com/') or s in ANSWERS}


def _direct(config, account, scope):
    """An access token from the owner's own app registration, renewing the stored refresh token (Microsoft rotates
    it: the new one replaces the old, under a lock, so two readers never race)."""
    app = config.get('microsoft_graph') or {}
    if not app.get('client_id'):
        raise GraphError('microsoft_graph_not_configured')
    path = credential_path(config, account)
    with open(str(path) + '.lock', 'a') as lock:
        portable.lock(lock)
        saved = _saved(path)
        granted = set(saved.get('scopes') or [])
        if not ANSWERS.get(scope, {scope}) & granted:
            raise GraphError('microsoft_scope_not_granted', 403)
        try:
            answer = _post_form(f"{LOGIN}/{urllib.parse.quote(app.get('tenant') or 'common')}/oauth2/v2.0/token", {
                'client_id': app['client_id'], 'grant_type': 'refresh_token', 'refresh_token': saved.get('refresh_token', ''),
                'scope': ' '.join('https://graph.microsoft.com/' + s for s in sorted(granted)) + ' offline_access'})
        except urllib.error.HTTPError as error:
            code = _error_code(error)
            if code in ('invalid_grant', 'interaction_required'):
                raise GraphError('outlook:microsoft_signin_expired_run_assistant_connect_outlook', error.code, retry_after=3600) from None
            raise GraphError('microsoft_signin', error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GraphError('microsoft_signin_unreachable') from None
        if answer.get('refresh_token') and answer['refresh_token'] != saved.get('refresh_token'):
            _write_signin(path, answer['refresh_token'], short_scopes(answer.get('scope')) or granted)
    return answer, granted


def scopes(config, credential, account):
    """The scopes the owner granted this account."""
    if account.get('transport') == 'broker':
        return set(_broker(config, credential, {'email': account['email']}).get('scopes') or [])
    if account.get('transport') == 'graph':
        return set(_saved(credential_path(config, account)).get('scopes') or [])
    raise GraphError('unsupported_transport')


def _connection_key(config, account):
    """Cache only within one account connection, including reconnects and rotated credential files."""
    transport = account.get('transport')
    name = 'microsoft-broker' if transport == 'broker' else account.get('credential') or credential_name(account['email'])
    path = str((config.get('credentials') or {}).get(name) or '')
    try:
        stat = Path(path).expanduser().stat()
        version = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except OSError:
        version = None
    app = config.get('microsoft_graph') or {}
    return (account.get('id'), account['email'].lower(), transport, account.get('connected_at'), path, version,
            (config.get('microsoft_broker') or {}).get('url'), app.get('client_id'), app.get('tenant'))


def token(config, credential, account, scope, fresh=False):
    key = (_connection_key(config, account), scope)
    with _lock:
        hit = _tokens.get(key)
        if hit and not fresh and hit[1] > time.monotonic():
            return hit[0]
    try:
        if account.get('transport') == 'broker':
            answer = _from_broker(config, credential, account, scope)
        elif account.get('transport') == 'graph':
            answer, _ = _direct(config, account, scope)
        else:
            raise GraphError('unsupported_transport')
    except GraphError as error:
        age = connected_age(config, account) if 'microsoft_signin_expired' in error.category else None
        if age is not None and age < SETTLE_SECONDS:
            raise GraphError('outlook:microsoft_signin_pending', error.status, retry_after=max(30, int(age))) from None
        raise
    if not answer.get('access_token'):
        raise GraphError('microsoft_broker')
    with _lock:
        key = (_connection_key(config, account), scope)  # refresh may have rotated the credential file
        _tokens[key] = (answer['access_token'], time.monotonic() + max(0, int(answer.get('expires_in') or 0) - 120))
    return answer['access_token']


# ------------------------------------------------------------------------------------------- calls

def url_for(method, params, write=False):
    known = (WRITE if write else READ).get(method)
    if not known:
        raise GraphError('unsupported_graph_method')
    verb, template, scope = known
    rest = dict(params or {})
    path = template
    for name in [part.split('}')[0] for part in template.split('{')[1:]]:
        if name not in rest:
            raise GraphError('missing_graph_parameter')
        path = path.replace('{' + name + '}', urllib.parse.quote(str(rest.pop(name)), safe=''))
    link = rest.pop('$link', None)
    if link:
        # A next or delta link Graph gave for this same call: only its query is new.
        parts = urllib.parse.urlsplit(str(link))
        if parts.scheme != 'https' or parts.netloc != 'graph.microsoft.com' or parts.path != '/v1.0' + path:
            raise GraphError('unexpected_graph_link')
        return verb, scope, str(link)
    query = urllib.parse.urlencode({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in rest.items()}, doseq=True)
    return verb, scope, GRAPH + path + ('?' + query if query else '')


def call(config, credential, account, method, params=None, body=None, write=False, raw=False, content_type=None,
         headers=None, timeout=60):
    """One Graph call as the account's owner: the parsed JSON answer ({} when empty), or bytes with raw=True.
    `body` is JSON, or bytes sent as `content_type`."""
    verb, scope, url = url_for(method, params, write)
    if isinstance(body, (bytes, bytearray)):
        data, kind = bytes(body), content_type or 'application/octet-stream'
    elif body is not None:
        data, kind = json.dumps(body).encode(), 'application/json'
    else:
        data, kind = None, None
    for attempt in (0, 1):
        access = token(config, credential, account, scope, fresh=bool(attempt))
        request = urllib.request.Request(url, method=verb, data=data, headers={
            'Authorization': 'Bearer ' + access, 'Accept': '*/*' if raw else 'application/json',
            'Prefer': 'IdType="ImmutableId"' + ''.join(', ' + h for h in (headers or {}).get('Prefer', [])),
            **({'Content-Type': kind} if kind else {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE + 1)
                if len(payload) > MAX_RESPONSE:
                    raise GraphError('response_too_large')
                return payload if raw else json.loads(payload or b'{}')
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 401 and not attempt and not write:
                continue  # the access token expired early: one fresh one, for reads only
            raise GraphError('outlook', error.code, retry_after=_retry_after(error.headers) if error.code in (429, 503) else None) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise GraphError('timeout') from None
        except ValueError:
            raise GraphError('invalid_json') from None


def identity(me):
    """The mailbox address of a /me answer: `mail`, else the sign-in name (personal accounts often have no `mail`)."""
    return str(me.get('mail') or me.get('userPrincipalName') or '').lower()


def folders(config, credential, account):
    """The well-known folders' IDs for this mailbox, looked up once per run."""
    key = _connection_key(config, account)
    with _lock:
        if key in _folders:
            return _folders[key]
    found = {}
    for name in ('inbox', 'sentitems', 'drafts', 'deleteditems', 'junkemail'):
        try:
            found[name] = call(config, credential, account, 'folders.get', {'id': name, '$select': 'id'}).get('id')
        except GraphError as error:
            if error.status != 404:
                raise
    with _lock:
        _folders[_connection_key(config, account)] = found
    return found


CONSUMER_TENANT = '9188040d-6c67-4c5b-b112-36a304b66dad'  # Microsoft's own tenant for Outlook.com accounts


def personal(account):
    """An Outlook.com account (not Microsoft 365): the consumer tenant when known, else a consumer domain such as
    outlook.com, hotmail.co.uk or live.be."""
    if account.get('tenant_id'):
        return account['tenant_id'] == CONSUMER_TENANT
    domain = str(account.get('email') or '').lower().rsplit('@', 1)[-1]
    return bool(re.fullmatch(r'(outlook|hotmail|live|msn|passport)\.[a-z]{2,3}(\.[a-z]{2})?', domain))


def open_link(account, message_id, link=None):
    """Where the owner opens one message: its own webLink, else Outlook on the web's read deeplink."""
    if web_link(link):
        return link
    base = 'https://outlook.live.com/mail/0/deeplink/read/' if personal(account) else 'https://outlook.office.com/mail/deeplink/read/'
    return base + urllib.parse.quote(str(message_id), safe='')


def web_link(url):
    parts = urllib.parse.urlsplit(str(url or ''))
    return url if parts.scheme == 'https' and parts.hostname in LINK_HOSTS else None


def attachments(config, credential, account, message_id):
    """A message's attachments: files can be saved; links to cloud files and attached emails or events can't."""
    params = {'id': message_id, '$select': 'id,name,contentType,size,isInline'}
    kinds = {'#microsoft.graph.fileAttachment': 'file', '#microsoft.graph.itemAttachment': 'item', '#microsoft.graph.referenceAttachment': 'link'}
    found = []
    seen = set()
    while True:
        page = call(config, credential, account, 'attachments.list', params)
        found.extend({'id': a.get('id'), 'name': str(a.get('name') or 'attachment')[:200], 'mime': str(a.get('contentType') or 'application/octet-stream')[:100],
                      'size': int(a.get('size') or 0), 'inline': bool(a.get('isInline')), 'kind': kinds.get(a.get('@odata.type'), 'file')}
                     for a in page.get('value', []))
        link = page.get('@odata.nextLink')
        if not link:
            return found
        if link in seen:
            raise GraphError('repeated_graph_page')
        seen.add(link)
        params = {'id': message_id, '$link': link}


def sent_bodies(config, credential, account, count=10):
    """The HTML of the last sent messages, without what they quote (uniqueBody) where Graph has it."""
    page = call(config, credential, account, 'folders.messages', {'id': 'sentitems', '$top': count, '$orderby': 'sentDateTime desc',
                                                                  '$select': 'uniqueBody,body'})
    out = []
    for message in page.get('value', []):
        part = message.get('uniqueBody') or message.get('body') or {}
        if part.get('contentType', 'html').lower() == 'html' and part.get('content'):
            out.append(part['content'])
    return out


def attachment(config, credential, account, message_id, attachment_id):
    """One file attachment's bytes (raw $value); over MAX_RESPONSE is an explicit failure, never a partial file."""
    return call(config, credential, account, 'attachments.value', {'id': message_id, 'attachment': attachment_id}, raw=True, timeout=120)


# ------------------------------------------------------------------------------------------- Gmail shapes

MESSAGE_FIELDS = 'id,conversationId,internetMessageId,parentFolderId,isRead,isDraft,receivedDateTime,lastModifiedDateTime,bodyPreview,webLink,inferenceClassification'


def _stamp(value, zone=None):
    """Graph's ISO time (up to seven fraction digits, maybe Z, maybe naive in `zone`) as an aware datetime."""
    text = str(value or '').strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    match = re.match(r'^(.*?T\d\d:\d\d(?::\d\d)?)(\.\d+)?(.*)$', text)
    if match:
        text = match[1] + (match[2][:7] if match[2] else '') + match[3]
    stamp = dt.datetime.fromisoformat(text)
    if stamp.tzinfo:
        return stamp
    try:
        return stamp.replace(tzinfo=ZoneInfo(zone) if zone and zone != 'UTC' else dt.timezone.utc)
    except (ZoneInfoNotFoundError, ValueError):
        return stamp.replace(tzinfo=dt.timezone.utc)  # a Windows zone name: Prefer asked for UTC, so this is rare


def _iso(stamp):
    return stamp.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def payload(mime):
    """A MIME message as Gmail's "full" payload: headers, parts, and each part's decoded body (base64url). Text parts
    are UTF-8, whatever charset they came in."""
    def part(node, part_id):
        out = {'partId': part_id, 'mimeType': node.get_content_type(), 'filename': node.get_filename() or '',
               'headers': [{'name': k, 'value': str(v)} for k, v in node.items()]}
        if node.is_multipart():
            children = node.get_payload()
            out['body'] = {'size': 0}
            out['parts'] = [part(child, f'{part_id}.{i}' if part_id else str(i)) for i, child in enumerate(children)]
            return out
        try:
            if node.get_content_maintype() == 'text':
                data = node.get_content().encode('utf-8')
            else:
                data = node.get_payload(decode=True) or b''
        except (LookupError, ValueError, AttributeError):
            data = node.get_payload(decode=True) or b''
        out['body'] = {'size': len(data), 'data': base64.urlsafe_b64encode(data).decode().rstrip('=')}
        return out
    return part(email.parser.BytesParser(policy=email.policy.default).parsebytes(mime), '')


def labels(message, known):
    """Gmail-style labels for a Graph message: its folder, unread, and Outlook's "Other" tab as an update."""
    folder = message.get('parentFolderId')
    names = {v: k for k, v in known.items() if v}
    found = {'inbox': ['INBOX'], 'sentitems': ['SENT'], 'drafts': ['DRAFT'], 'deleteditems': ['TRASH'],
             'junkemail': ['SPAM']}.get(names.get(folder), [])
    if message.get('isDraft') and 'DRAFT' not in found:
        found.append('DRAFT')
    if not message.get('isRead') and 'SENT' not in found:
        found.append('UNREAD')
    if message.get('inferenceClassification') == 'other':
        found.append('CATEGORY_UPDATES')
    return found


def gmail_message(config, credential, account, message_id, form='full'):
    """One message in Gmail's shape: id, threadId (the conversation), labelIds, internalDate, snippet, payload, and
    webLink (Outlook on the web) for the card's Open link."""
    meta = call(config, credential, account, 'messages.get', {'id': message_id, '$select': MESSAGE_FIELDS})
    out = {'id': meta['id'], 'threadId': meta.get('conversationId'), 'labelIds': labels(meta, folders(config, credential, account)),
           'internalDate': str(int(_stamp(meta.get('receivedDateTime')).timestamp() * 1000)), 'snippet': meta.get('bodyPreview') or '',
           'webLink': web_link(meta.get('webLink'))}
    if form != 'minimal':
        out['payload'] = payload(call(config, credential, account, 'messages.mime', {'id': message_id}, raw=True))
    return out


def send_as(me):
    """A Microsoft mailbox's own addresses in Gmail's sendAs shape: its main address, and its other SMTP proxy addresses
    as aliases (Exchange delivers each one to this mailbox). A personal Outlook.com account has none to show."""
    primary = identity(me)
    out = [{'sendAsEmail': primary, 'isPrimary': True, 'treatAsAlias': True, 'verificationStatus': 'accepted'}] if primary else []
    for proxy in me.get('proxyAddresses') or ():
        kind, _, address = str(proxy).partition(':')
        if kind.lower() == 'smtp' and '@' in address and address.lower() != str(primary or '').lower():
            out.append({'sendAsEmail': address.lower(), 'isPrimary': False, 'treatAsAlias': True, 'verificationStatus': 'accepted'})
    return {'sendAs': out}


def gmail_call(config, credential, account, method, params):
    """The Gmail reads the app makes, answered from Graph. The history cursor is a time: messages changed since."""
    params = dict(params or {})
    if method == 'gmail.users.getProfile':
        me = call(config, credential, account, 'me', {'$select': 'mail,userPrincipalName'})
        return {'emailAddress': identity(me), 'historyId': _iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2))}
    if method == 'gmail.users.settings.sendAs.get':
        return {'sendAsEmail': params.get('sendAsEmail'), 'signature': ''}  # Graph has no API for Outlook signatures
    if method == 'gmail.users.settings.sendAs.list':  # the mailbox's own addresses: its proxy addresses (User.Read)
        return send_as(call(config, credential, account, 'me', {'$select': 'mail,userPrincipalName,proxyAddresses'}))
    if method == 'gmail.users.messages.get':
        return gmail_message(config, credential, account, params['id'], params.get('format', 'full'))
    if method == 'gmail.users.messages.attachments.get':
        raise GraphError('outlook', 404)  # every part's data is already in the message's payload
    if method == 'gmail.users.messages.list':
        query = str(params.get('q') or '')
        if query.startswith('in:sent rfc822msgid:'):
            wanted = query.split(':', 2)[2].strip()
            page = call(config, credential, account, 'folders.messages', {
                'id': 'sentitems', '$filter': f"internetMessageId eq '{wanted.replace(chr(39), chr(39) * 2)}'",
                '$select': 'id,conversationId', '$top': 2})
        elif params.get('pageToken'):
            page = call(config, credential, account, 'messages.list', {'$link': params['pageToken']})
        elif query.startswith('after:'):
            since = dt.datetime.fromtimestamp(int(query[6:].split()[0]), dt.timezone.utc)
            page = call(config, credential, account, 'messages.list', {
                '$filter': f'receivedDateTime ge {_iso(since)}', '$orderby': 'receivedDateTime desc',
                '$select': 'id,conversationId', '$top': int(params.get('maxResults') or 40)})
        else:
            raise GraphError('unsupported_gmail_query')
        out = {'messages': [{'id': m['id'], 'threadId': m.get('conversationId')} for m in page.get('value', [])]}
        if page.get('@odata.nextLink'):
            out['nextPageToken'] = page['@odata.nextLink']
        return out
    if method == 'gmail.users.history.list':
        start = str(params['startHistoryId'])
        try:
            _stamp(start)
        except ValueError:
            raise GraphError('outlook', 404) from None  # not a cursor of ours: start again from the window
        if params.get('pageToken'):
            link, latest = params['pageToken'].split(' ', 1) if ' ' in params['pageToken'] else (params['pageToken'], start)
            page = call(config, credential, account, 'messages.list', {'$link': link})
        else:
            latest = start
            page = call(config, credential, account, 'messages.list', {
                '$filter': f'lastModifiedDateTime ge {start}', '$orderby': 'lastModifiedDateTime asc',
                '$select': 'id,lastModifiedDateTime', '$top': int(params.get('maxResults') or 100)})
        history = []
        for m in page.get('value', []):
            history.append({'labelsAdded': [{'message': {'id': m['id']}}]})
            if m.get('lastModifiedDateTime') and _stamp(m['lastModifiedDateTime']) > _stamp(latest):
                latest = _iso(_stamp(m['lastModifiedDateTime']))
        out = {'history': history, 'historyId': latest}
        if page.get('@odata.nextLink'):
            out['nextPageToken'] = page['@odata.nextLink'] + ' ' + latest
        return out
    raise GraphError('unsupported_gmail_method')


# ------------------------------------------------------------------------------------------- calendar in Google's shape

RESPONSES = {'accepted': 'accepted', 'tentativelyAccepted': 'tentative', 'declined': 'declined', 'none': 'needsAction',
             'notResponded': 'needsAction', 'organizer': 'accepted'}


def _when(value, all_day):
    value = value or {}
    if all_day:
        return {'date': str(value.get('dateTime') or '')[:10]}
    return {'dateTime': _stamp(value.get('dateTime'), value.get('timeZone')).isoformat()}


def calendar_item(raw, owner):
    """A Graph event as a Google Calendar one (the fields calendar_event reads)."""
    if '@removed' in raw:
        return {'id': raw['id'], 'status': 'cancelled'}
    all_day = bool(raw.get('isAllDay'))
    organizer = (raw.get('organizer') or {}).get('emailAddress') or {}
    attendees = []
    for a in raw.get('attendees') or []:
        address = (a.get('emailAddress') or {})
        attendees.append({'email': str(address.get('address') or '').lower(), 'displayName': address.get('name') or '',
                          'responseStatus': RESPONSES.get((a.get('status') or {}).get('response'), 'needsAction'),
                          **({'self': True} if str(address.get('address') or '').lower() == owner else {})})
    item = {'id': raw['id'], 'summary': raw.get('subject') or '(untitled event)', 'start': _when(raw.get('start'), all_day),
            'end': _when(raw.get('end'), all_day), 'status': 'cancelled' if raw.get('isCancelled') else 'confirmed',
            'updated': raw.get('lastModifiedDateTime'), 'location': ((raw.get('location') or {}).get('displayName') or None),
            'organizer': {'email': str(organizer.get('address') or '').lower(), 'displayName': organizer.get('name') or ''},
            'attendees': attendees, 'description': raw.get('bodyPreview') or '', 'webLink': web_link(raw.get('webLink'))}
    if raw.get('seriesMasterId'):
        item['recurringEventId'] = raw['seriesMasterId']
        if raw.get('originalStart'):
            item['originalStartTime'] = {'date': str(raw['originalStart'])[:10]} if all_day else {'dateTime': _stamp(raw['originalStart'], (raw.get('originalStartTimeZone') or 'UTC')).isoformat()}
    if raw.get('showAs') == 'free':
        item['transparency'] = 'transparent'
    return item


def calendar_call(config, credential, account, method, params):
    """Google Calendar's calendars.get and events.list (singleEvents, sync tokens) answered from Graph's calendar
    view and its delta: the next and delta links are the page and sync tokens."""
    owner = account['email'].lower()
    calendar_id = account.get('calendar_id', 'primary')
    if method == 'calendars.get':
        if calendar_id == 'primary':
            found = call(config, credential, account, 'calendar.get', {'$select': 'id,owner'})
            address = str(((found.get('owner') or {}).get('address')) or '').lower()
        else:
            found = call(config, credential, account, 'calendars.get', {'id': calendar_id, '$select': 'id,owner'})
            address = calendar_id
        return {'id': address, 'timeZone': 'UTC'}
    if method != 'events.list':
        raise GraphError('unsupported_calendar_method')
    # Each calendar has its own delta (its links are its sync tokens); the default one is /me/calendarView.
    method, where = ('calendar.delta', {}) if calendar_id == 'primary' else ('calendars.delta', {'id': calendar_id})
    if params.get('syncToken') == 'none':
        raise GraphError('outlook', 410)  # an earlier whole read without delta: start a delta now
    link = params.get('pageToken') or params.get('syncToken')
    if link and calendar_id != 'primary' and urllib.parse.urlsplit(link).path.endswith('/calendarView'):
        return _calendar_view(config, credential, account, owner, calendar_id, params)  # a whole-read page
    query = {**where, '$link': link} if link else {**where, 'startDateTime': params['timeMin'], 'endDateTime': params['timeMax']}
    try:
        page = call(config, credential, account, method, query,
                    headers={'Prefer': ['odata.maxpagesize=100', 'outlook.timezone="UTC"']})
    except GraphError as error:
        if params.get('syncToken') and error.status in (400, 404, 410):
            raise GraphError('outlook', 410) from None  # the delta expired: Google's word for that
        if not link and calendar_id != 'primary' and error.status in (400, 404, 501):
            return _calendar_view(config, credential, account, owner, calendar_id, params)  # no delta for this one
        raise
    out = {'items': [calendar_item(e, owner) for e in page.get('value', [])]}
    if page.get('@odata.nextLink'):
        out['nextPageToken'] = page['@odata.nextLink']
    if page.get('@odata.deltaLink'):
        out['nextSyncToken'] = page['@odata.deltaLink']
    return out


def _calendar_view(config, credential, account, owner, calendar_id, params):
    """A calendar without delta (some shared ones), read whole each round: no sync token, so the window is read again."""
    query = {'id': calendar_id, '$link': params['pageToken']} if params.get('pageToken') else {
        'id': calendar_id, 'startDateTime': params['timeMin'], 'endDateTime': params['timeMax'], '$top': 100}
    page = call(config, credential, account, 'calendars.view', query, headers={'Prefer': ['outlook.timezone="UTC"']})
    out = {'items': [calendar_item(e, owner) for e in page.get('value', [])]}
    if page.get('@odata.nextLink'):
        out['nextPageToken'] = page['@odata.nextLink']
    else:
        out['nextSyncToken'] = 'none'
    return out


# ------------------------------------------------------------------------------------------- device-code sign-in

def device_signin(app, scopes_wanted, say, wait=900, poll=None):
    """Sign in with a device code against the owner's own app: shows where to go and the code, waits for it, and
    returns (refresh token, granted short scopes, signed-in address). Nothing is stored here."""
    tenant = urllib.parse.quote(app.get('tenant') or 'common')
    scope = ' '.join(['offline_access', *('https://graph.microsoft.com/' + s for s in scopes_wanted)])
    try:
        start = _post_form(f'{LOGIN}/{tenant}/oauth2/v2.0/devicecode', {'client_id': app['client_id'], 'scope': scope})
    except urllib.error.HTTPError as error:
        raise GraphError('microsoft_signin:' + str(_error_code(error) or error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise GraphError('microsoft_signin_unreachable') from None
    say(f"Open {start.get('verification_uri', 'https://microsoft.com/devicelogin')} and enter the code {start.get('user_code', '')}")
    interval = int(start.get('interval') or 5)
    deadline = time.monotonic() + min(wait, int(start.get('expires_in') or wait))
    sleep = poll or time.sleep
    while time.monotonic() < deadline:
        sleep(interval)
        try:
            answer = _post_form(f'{LOGIN}/{tenant}/oauth2/v2.0/token', {
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'client_id': app['client_id'],
                'device_code': start.get('device_code', '')})
        except urllib.error.HTTPError as error:
            code = _error_code(error)
            if code == 'authorization_pending':
                continue
            if code == 'slow_down':
                interval += 5
                continue
            raise GraphError('microsoft_signin:' + str(code or error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        if not answer.get('refresh_token') or not answer.get('access_token'):
            raise GraphError('microsoft_signin:no_lasting_access')
        request = urllib.request.Request(GRAPH + '/me?$select=mail,userPrincipalName',
                                         headers={'Authorization': 'Bearer ' + answer['access_token'], 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                me = json.loads(response.read(200_000))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise GraphError('microsoft_signin:profile') from None
        return answer['refresh_token'], short_scopes(answer.get('scope')), identity(me)
    raise GraphError('microsoft_signin:timed_out')
