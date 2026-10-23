"""Intercom: customer conversations in your Intercom workspace, read-only.

Only GET /me, GET /conversations/<id> and the conversation search (a read-only POST) are ever called; any
other method or path is refused before the token is read. It never replies, assigns, closes, tags or sends.

Config, with an access token from an app in Intercom's Developer Hub in a credential file:
    {"id": "intercom", "service": "intercom", "transport": "api", "boundary": "work:Acme",
     "credential": "intercom", "poll_seconds": 600}
The credential file holds the token alone, or an INTERCOM_ACCESS_TOKEN= line (set "credential_variable").
Connecting reads the workspace from the token (GET /me) and keeps its ID and name, the admin and the region
(US, EU or Australia). Every sync checks the workspace and admin again, exactly. Connecting an account that
already has a workspace with a token from another workspace is refused: add that one as another account.

Coverage: conversations updated in the last `bootstrap_days` (7), read in frozen search windows whose cursor
commits with the conversations. Polls overlap six hours, the whole window is reread daily, and an expired
cursor replays its window. Intercom returns at most 500 parts per conversation. The opening message keeps up
to 20,000 characters and the latest history fills the rest of a 40,000-character body; anything cut is
flagged. Attachments aren't collected.
"""
import datetime as dt
import html.parser
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

KINDS = {'intercom.conversation': 'Intercom'}
# For `assistant connect intercom`: the account it adds and what it asks (see docs/adding-a-source.md).
SETUP = {'label': 'Intercom', 'what': 'Customer conversations in your Intercom workspace.',
         'account': {'id': 'intercom', 'service': 'intercom', 'transport': 'api', 'boundary': 'work',
                     'credential': 'intercom', 'poll_seconds': 600},
         'note': 'In Intercom\'s Developer Hub, open your app, then Authentication, and copy its access token.',
         'secret': 'Intercom access token',
         'hints': {'account_identity_mismatch': 'that token is for another Intercom workspace; add it as another account',
                   'intercom:HTTP_401': 'Intercom turned the access token down; copy it again from your app in the Developer Hub'}}
VERSION = '2.11'
# Intercom keeps each workspace in one region, with its own API and app address.
REGIONS = {'us': ('https://api.intercom.io', 'https://app.intercom.com'),
           'eu': ('https://api.eu.intercom.io', 'https://app.eu.intercom.com'),
           'au': ('https://api.au.intercom.io', 'https://app.au.intercom.com')}
OPENING, BODY, MAX_PARTS = 20000, 40000, 500
OVERLAP, FULL_EVERY, MAX_PAGES = 6 * 3600, 86400, 500
CONVERSATION = re.compile(r'/conversations/[0-9]{1,20}')


def readable(method, path):
    # Search is a read-only POST. No other POST or mutation is admitted.
    return (method == 'GET' and (path == '/me' or CONVERSATION.fullmatch(path) is not None)) or \
        (method == 'POST' and path == '/conversations/search')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never send the token on to another address


class Source:
    def __init__(self, config, account, helpers):
        self.config, self.account, self.helpers = config, account, helpers
        self.opener = urllib.request.build_opener(NoRedirect())

    def token(self):
        SourceError = self.helpers.SourceError
        secret = self.helpers.credential(self.config, self.account.get('credential', 'intercom'))
        if not isinstance(secret, str):
            raise SourceError('missing_intercom_credential')
        variable = self.account.get('credential_variable')
        if variable:
            values = {}
            for line in secret.splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
            secret = values.get(variable, '')
        secret = secret.strip()
        if not re.fullmatch(r'[A-Za-z0-9_=:.+/-]{8,512}', secret):
            raise SourceError('missing_intercom_credential')
        return secret

    def request(self, method, path, payload=None, region=None):
        SourceError = self.helpers.SourceError
        if not readable(method, path):
            raise SourceError('unsupported_intercom_method')
        region = region or self.account.get('region') or 'us'
        if region not in REGIONS:
            raise SourceError('intercom_invalid_region')
        request = urllib.request.Request(REGIONS[region][0] + path, method=method,
            data=json.dumps(payload, separators=(',', ':')).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + self.token(), 'Intercom-Version': VERSION,
                     'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'TheMachine/1'})
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise SourceError('intercom_response_too_large')
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            retry = error.headers.get('Retry-After', '') if error.headers else ''
            delay = min(max(int(retry), 1), 86400) if retry.isdigit() else (60 if error.code == 429 else None)
            raise SourceError('intercom', error.code, delay) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceError('network:' + type(error).__name__ + ':intercom_read') from None
        except (ValueError, UnicodeError):
            raise SourceError('intercom_invalid_json') from None
        if not isinstance(data, dict) or data.get('type') == 'error.list':
            raise SourceError('intercom_invalid_response')
        return data

    def connect(self):
        """At connect: reads which workspace, admin and region the token belongs to and keeps them, so every sync
        after can check them exactly. An account that already has a workspace keeps it."""
        SourceError = self.helpers.SourceError
        # A token is only known in its workspace's region: a 401 elsewhere means try the next one.
        regions = [self.account['region']] if self.account.get('region') in REGIONS else list(REGIONS)
        for region in regions:
            try:
                profile = self.request('GET', '/me', region=region)
                break
            except SourceError as error:
                if error.status != 401 or region == regions[-1]:
                    raise
        app = profile.get('app') if isinstance(profile.get('app'), dict) else {}
        workspace = app.get('id_code')
        if not isinstance(workspace, str) or not re.fullmatch(r'[A-Za-z0-9]{1,40}', workspace) \
                or not re.fullmatch(r'[0-9]{1,20}', str(profile.get('id', ''))):
            raise SourceError('intercom_invalid_profile')
        if self.account.get('workspace_id') and self.account['workspace_id'] != workspace:
            raise SourceError('account_identity_mismatch')
        name = ' '.join(str(app.get('name') or '').split())[:100]
        self.account.update(workspace_id=workspace, workspace_name=name or workspace, region=region, admin_id=str(profile['id']))
        if isinstance(profile.get('email'), str) and '@' in profile['email']:
            self.account['email'] = profile['email'].lower()
        return profile

    def profile(self):
        """At every sync: the same workspace and admin as at connect, exactly."""
        profile = self.request('GET', '/me')
        app = profile.get('app') if isinstance(profile.get('app'), dict) else {}
        if (not self.account.get('workspace_id') or app.get('id_code') != self.account['workspace_id']
                or not self.account.get('admin_id') or str(profile.get('id', '')) != self.account['admin_id']):
            raise self.helpers.SourceError('account_identity_mismatch')
        return profile


class PlainHTML(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainHTML()
    parser.feed(str(value or ''))
    parser.close()
    return '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())


def event(account, raw, helpers):
    SourceError = helpers.SourceError
    if not re.fullmatch(r'[0-9]{1,20}', str(raw.get('id', ''))) or type(raw.get('updated_at')) is not int:
        raise SourceError('intercom_invalid_conversation')
    container = raw.get('conversation_parts') or {}
    parts = container.get('conversation_parts') if isinstance(container, dict) else None
    if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts):
        raise SourceError('intercom_missing_parts')
    source = raw.get('source') if isinstance(raw.get('source'), dict) else {}
    root = plain(source.get('body'))
    lines = []
    for part in sorted(parts, key=lambda p: (p.get('created_at') or 0, str(p.get('id', '')))):
        author = part.get('author') if isinstance(part.get('author'), dict) else {}
        lines.append(f"{part.get('created_at', '')} · {author.get('name') or author.get('email') or author.get('type', '')} · "
                     f"{part.get('part_type', '')}: {plain(part.get('body'))}")
    history = '\n'.join(lines)
    # The opening message gets up to 20,000 characters; the latest history fills the rest.
    opening = root[:OPENING]
    room = BODY - len(opening) - 1
    body = opening + '\n' + (history[-room:] if room > 0 else '')
    reported = container.get('total_count') if type(container.get('total_count')) is int else len(parts)
    truncated = len(root) > OPENING or len(history) > room or reported > len(parts) or len(parts) >= MAX_PARTS
    cid = str(raw['id'])
    app = REGIONS.get(account.get('region') or 'us', REGIONS['us'])[1]
    metadata = {'conversation_id': cid, 'state': raw.get('state'), 'open': raw.get('open'), 'priority': raw.get('priority'),
                'admin_assignee_id': raw.get('admin_assignee_id'), 'team_assignee_id': raw.get('team_assignee_id'),
                'created_at': raw.get('created_at'), 'updated_at': raw['updated_at'],
                'from': str((source.get('author') or {}).get('email') or ''), 'parts_returned': len(parts),
                'parts_reported': reported, 'truncated': truncated, 'source_redacted': bool(source.get('redacted')),
                'source_url': f"{app}/a/inbox/{account['workspace_id']}/inbox/shared/all/conversation/{cid}"}
    return {'account_id': account['id'], 'source_id': cid, 'kind': 'intercom.conversation',
            'occurred_at': helpers.iso(raw['updated_at']),
            'title': (raw.get('title') or source.get('subject') or root.split('\n')[0] or 'Support conversation')[:1000],
            'body': body, 'metadata': metadata, 'deleted': False}


def check(config, account, helpers):
    """Connect-time check: GET /me only, no store or checkpoint writes. Keeps the region and admin on the account."""
    Source(config, account, helpers).connect()


def sync(store, config, account, helpers, source=None):
    SourceError = helpers.SourceError
    source = source or Source(config, account, helpers)
    source.profile()
    state = store.checkpoint(account['id']) or {}
    stamp = int(time.time())
    if not state.get('window'):
        days = int(config.get('bootstrap_days', 7))
        initial = not state.get('watermark')
        full = stamp >= state.get('next_full', 0)
        after = stamp - days * 86400 if initial or full else max(stamp - days * 86400, state['watermark'] - OVERLAP)
        state.update(mode='bootstrap' if initial else 'incremental',
                     window={'after': after, 'before': stamp, 'full': full, 'pages': 0, 'cursors': []})
    window = state['window']
    pagination = {'per_page': 10}
    if window.get('cursor'):
        pagination['starting_after'] = window['cursor']
    query = {'query': {'operator': 'AND', 'value': [
                {'field': 'updated_at', 'operator': '>=', 'value': window['after']},
                {'field': 'updated_at', 'operator': '<', 'value': window['before']}]},
             'sort': {'field': 'updated_at', 'order': 'ascending'}, 'pagination': pagination}
    try:
        page = source.request('POST', '/conversations/search', query)
    except SourceError as error:
        if error.status == 400 and window.get('cursor'):
            # An expired cursor: replay the same frozen window; never advance its watermark.
            window.pop('cursor', None)
            window.update(pages=0, cursors=[])
            store.commit_page(account['id'], state, [], record_sync=False)
        raise
    rows, pages = page.get('conversations'), page.get('pages')
    if not isinstance(rows, list) or not isinstance(pages, dict):
        raise SourceError('intercom_invalid_page')
    events = []
    for row in rows:
        cid = str((row or {}).get('id', '')) if isinstance(row, dict) else ''
        if not re.fullmatch(r'[0-9]{1,20}', cid):
            raise SourceError('intercom_invalid_conversation')
        detail = source.request('GET', '/conversations/' + cid)
        if str(detail.get('id')) != cid:
            raise SourceError('intercom_conversation_identity_mismatch')
        events.append(event(account, detail, helpers))
    next_page = pages.get('next') if isinstance(pages.get('next'), dict) else {}
    cursor = next_page.get('starting_after')

    def abandon_window(category):
        # Retrying the same broken page can never succeed: drop the frozen window (watermark unchanged),
        # so the next poll starts a fresh search.
        state.pop('window', None)
        store.commit_page(account['id'], state, [], record_sync=False)
        raise SourceError(category)
    if cursor is not None and not (isinstance(cursor, str) and re.fullmatch(r'[A-Za-z0-9_=+/-]{1,512}', cursor)):
        abandon_window('intercom_invalid_cursor')
    if not cursor and (next_page or (pages.get('page') or 1) < (pages.get('total_pages') or 1)):
        abandon_window('intercom_missing_cursor')
    window['pages'] += 1
    if cursor:
        if not rows or cursor in window['cursors'] or window['pages'] >= MAX_PAGES:
            abandon_window('intercom_invalid_cursor')
        window['cursors'].append(cursor)
        window['cursor'] = cursor
    else:
        state.update(mode='incremental', watermark=window['before'])
        if window['full']:
            state['next_full'] = stamp + FULL_EVERY
        state.pop('window')
    state['reported_conversations'] = page.get('total_count')
    state.update(coverage_complete=not cursor, coverage='Older Intercom conversations are still being collected.' if cursor
                 else f"Intercom conversations updated in the last {int(config.get('bootstrap_days', 7))} days, caught up.")
    store.commit_page(account['id'], state, events)
    return {'mode': state['mode'], 'events': len(events), 'more': bool(cursor)}
