"""Read card-related board actions with GET only; never mark a board viewed.

One board per account. The API key (public by Trello's own account) is a setup answer; the token is the
credential, loaded exclusively through helpers.credential. Authorize the token with scope=read. A credential
saved earlier as JSON with ``key`` and ``token`` still works.
The initial import is bounded by bootstrap_days (default seven).
"""
import copy
import datetime as dt
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

KINDS = {'trello.action': 'Trello'}
SETUP = {
    'label': 'Trello',
    'account': {'id': 'trello', 'service': 'trello', 'transport': 'api',
                'boundary': 'work', 'credential': 'trello', 'poll_seconds': 300},
    'ask': [('board', 'Trello board URL or board ID (one board per connection)'),
            ('api_key', 'Trello API key')],
    'secret': 'Trello token',
    'note': 'At https://trello.com/power-ups/admin create a Power-Up (any name), then open its API key page '
            'and copy the API key (not the Secret). Next to it, click the Token link, allow read access '
            'and copy the token it shows (it starts with ATTA).',
    'what': 'Card changes, comments and activity on one Trello board.',
    'hints': {'trello_http:HTTP_401': 'Trello rejected the API key or token. The token starts with ATTA: '
                                      'the Secret on the API key page is not the token.'}}
LIMIT = 1000
PAGES = 5
ID = r'[0-9a-f]{24}'


def timestamp(value, helpers):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise helpers.SourceError('trello_invalid_timestamp') from None


def board_id(value, helpers):
    if isinstance(value, str):
        match = re.fullmatch(r'https://trello\.com/b/([A-Za-z0-9]{8})(?:/[^?#]*)?/?(?:\?[^#]*)?(?:#.*)?', value)
        value = match[1] if match else value
        if re.fullmatch(ID + r'|[A-Za-z0-9]{8}', value):
            return value
    raise helpers.SourceError('trello_invalid_board')


class Source:
    def __init__(self, config, account, helpers):
        self.helpers = helpers
        self.board = board_id(account.get('board'), helpers)
        try:
            raw = helpers.credential(config, account.get('credential', 'trello'))
            if raw.lstrip().startswith('{'):
                secret = json.loads(raw)
                key, token = secret['key'], secret['token']
            else:
                key, token = account.get('api_key'), raw.strip()
            if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]+', v) for v in (key, token)):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise helpers.SourceError('trello_credential_unavailable') from None
        self.headers = {'Accept': 'application/json', 'User-Agent': 'TheMachine/1',
                        'Authorization': f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"'}

    def get(self, actions=False, params=None):
        # No configurable host or arbitrary paths: credentials only go to Trello.
        url = 'https://api.trello.com/1/boards/' + self.board + ('/actions' if actions else '')
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            request = urllib.request.Request(url, headers=self.headers, method='GET')
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise self.helpers.SourceError('trello_response_too_large')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            retry = error.headers.get('Retry-After', '') if error.headers else ''
            delay = min(3600, max(1, int(retry))) if retry.isdigit() else (60 if error.code == 429 else None)
            raise self.helpers.SourceError('trello_http', error.code, retry_after=delay) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise self.helpers.SourceError('trello_network') from None
        except (ValueError, UnicodeError):
            raise self.helpers.SourceError('trello_invalid_json') from None

    def verify(self):
        board = self.get(params={'fields': 'id,name,shortLink'})
        if not isinstance(board, dict) or not re.fullmatch(ID, str(board.get('id', ''))):
            raise self.helpers.SourceError('trello_invalid_board_response')
        if self.board not in (board['id'], board.get('shortLink')):
            raise self.helpers.SourceError('account_identity_mismatch')
        return board


def plain(value):
    text = html.unescape(re.sub(r'<[^>]*>', '', str(value)))
    # Do not collect explicitly labeled passwords or one-time codes in comments.
    return re.sub(r'(?i)\b(password|passcode|one[- ]time code|verification code|otp)\s*[:=]\s*\S+',
                  r'\1: [omitted]', text)


def event(account, item, board, helpers):
    data = item['data']
    card = data.get('card')
    if not isinstance(card, dict) or not card.get('id'):
        return None  # Board/list-only activity is not a card update.
    actor = item.get('memberCreator') or {}
    kind = item['type']
    lines = [f"Action: {kind}", f"Board: {board.get('name', '')}"]
    for field, value in data.items():
        if field not in ('board', 'card'):
            lines.append(f'{field}: ' + (json.dumps(value, ensure_ascii=False, sort_keys=True)
                                         if isinstance(value, (dict, list)) else str(value)))
    link = card.get('shortLink')
    url = 'https://trello.com/c/' + (link if isinstance(link, str) and re.fullmatch(r'[A-Za-z0-9]{8}', link)
                                    else urllib.parse.quote(str(card['id']), safe=''))
    return {'account_id': account['id'], 'source_id': item['id'], 'kind': 'trello.action',
            'occurred_at': timestamp(item['date'], helpers).isoformat(),
            'title': plain(card.get('name') or 'Card update')[:1000],
            'body': plain('\n'.join(lines))[:40000], 'deleted': False,
            'metadata': {'source_url': url, 'board_id': board['id'], 'card_id': card['id'],
                         'action_type': kind, 'card_deleted': kind == 'deleteCard',
                         'from': actor.get('id', ''), 'sender_display': plain(actor.get('fullName') or actor.get('username') or '')}}


def page(source, window, helpers):
    items = source.get(True, {'filter': 'all', 'limit': LIMIT, 'since': window['since'],
                              'before': window.get('cursor') or window['until']})
    if not isinstance(items, list) or len(items) > LIMIT:
        raise helpers.SourceError('trello_invalid_page')
    previous = None
    for item in items:
        if (not isinstance(item, dict) or not re.fullmatch(ID, str(item.get('id', '')))
                or not isinstance(item.get('data'), dict) or not isinstance(item.get('type'), str)):
            raise helpers.SourceError('trello_invalid_action')
        stamp = timestamp(item.get('date'), helpers)
        if (previous and stamp > previous) or not (timestamp(window['since'], helpers) <= stamp <= timestamp(window['until'], helpers)):
            raise helpers.SourceError('trello_invalid_page')
        previous = stamp
    if items and window.get('cursor') and items[-1]['id'] >= window['cursor']:
        raise helpers.SourceError('trello_invalid_cursor')
    return items


def sync(store, config, account, helpers):
    source = Source(config, account, helpers)
    board = source.verify()
    state = copy.deepcopy(store.checkpoint(account['id']) or {})
    now = timestamp(helpers.now(), helpers)

    def fresh(since=None):
        start = timestamp(since, helpers) - dt.timedelta(seconds=1) if since else now - dt.timedelta(days=int(config.get('bootstrap_days', 7)))
        return {'since': start.isoformat(), 'until': now.isoformat()}

    window = state.get('catchup') or fresh(state.get('since'))
    count = read = 0
    reset = False
    for _ in range(PAGES):
        try:
            items = page(source, window, helpers)
        except helpers.SourceError as error:
            if error.status == 400 and window.get('cursor') and not reset:
                # A rejected continuation must not strand the source indefinitely.
                window, reset = {k: v for k, v in window.items() if k != 'cursor'}, True
                items = page(source, window, helpers)
            else:
                raise
        events = [e for e in (event(account, item, board, helpers) for item in reversed(items)) if e]
        read += len(items)
        count += len(events)
        more = len(items) == LIMIT
        if more:
            window['cursor'] = items[-1]['id']
            state['catchup'] = window
        else:
            state['since'] = window['until']
            state.pop('catchup', None)
        state.update(coverage_complete=not more,
                     coverage='Older Trello actions are still being collected.' if more else 'Trello board actions caught up.')
        store.commit_page(account['id'], state, events)
        if not more:
            break
    return {'mode': 'incremental', 'events': count, 'read': read, 'more': more}


def check(config, account, helpers):
    """Probe board access and parse a recent page, without storing anything."""
    source = Source(config, account, helpers)
    board = source.verify()
    now = timestamp(helpers.now(), helpers)
    window = {'since': (now - dt.timedelta(days=int(config.get('bootstrap_days', 7)))).isoformat(),
              'until': now.isoformat()}
    for item in page(source, window, helpers):
        event(account, item, board, helpers)
