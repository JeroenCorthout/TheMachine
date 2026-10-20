"""Shortcut pipeline moves, via GET-only story search and history.

Uses the standard library, with no SDK or write endpoints. Shortcut tokens have
full account access (no read-only token scope); this reader never uses it to write.
Search uses a one-day overlap for date precision and indexing delays. History
supplies individual transitions, including multiple moves between polls.
"""
import copy
import datetime as dt
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

KINDS = {'shortcut.move': 'Shortcut'}
SETUP = {
    'label': 'Shortcut',
    'what': 'Story pipeline moves with previous and new states, owners, deadlines and story context.',
    'account': {'id': 'shortcut', 'service': 'shortcut', 'transport': 'api',
                'boundary': 'work', 'credential': 'shortcut', 'poll_seconds': 300},
    'ask': [('workspace', 'Shortcut workspace URL or slug')],
    'secret': 'Shortcut API token',
    'note': 'Create a token at https://app.shortcut.com/settings/account/api-tokens '
            '(Settings > Account > API Tokens). Tokens have full account access; this connector only reads. '
            'API documentation: https://developer.shortcut.com/api/rest/v3',
}
BASE = 'https://api.app.shortcut.com'
PREFIX = '/api/v3/'
PAGES = 3
LIMIT = 25


def timestamp(value, helpers):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise helpers.SourceError('shortcut_invalid_timestamp') from None


def plain(value):
    value = html.unescape(re.sub(r'<[^>]*>', '', str(value or '')))
    return re.sub(r'(?i)\b(password|passcode|one[- ]time code|verification code|otp)\s*[:=]\s*\S+',
                  r'\1: [omitted]', value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the credential to a redirect target.


class Source:
    def __init__(self, config, account, helpers):
        self.helpers, self.account = helpers, account
        workspace = account.get('workspace', '')
        if not isinstance(workspace, str):
            raise helpers.SourceError('shortcut_invalid_workspace')
        match = re.fullmatch(r'https://app\.shortcut\.com/([a-zA-Z0-9_-]+)(?:/[^?#]*)?/?', workspace)
        self.workspace = match[1] if match else workspace
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', self.workspace):
            raise helpers.SourceError('shortcut_invalid_workspace')
        secret = helpers.credential(config, account.get('credential', 'shortcut'))
        if not isinstance(secret, str):
            raise helpers.SourceError('shortcut_credential_unavailable')
        secret = secret.strip()
        if not secret or not re.fullmatch(r'[A-Za-z0-9_-]+', secret):
            raise helpers.SourceError('shortcut_credential_unavailable')
        self.headers = {'Shortcut-Token': secret, 'Accept': 'application/json', 'User-Agent': 'TheMachine/1'}
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path):
        # Only these read endpoints are allowed, including server-provided continuations.
        parts = urllib.parse.urlsplit(path)
        if parts.scheme or parts.netloc or parts.fragment or not (
                parts.path in (PREFIX + 'member', PREFIX + 'search/stories')
                or re.fullmatch(PREFIX + r'stories/[1-9][0-9]*/history', parts.path)):
            raise self.helpers.SourceError('shortcut_path_not_allowed')
        try:
            request = urllib.request.Request(BASE + path, headers=self.headers, method='GET')
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise self.helpers.SourceError('shortcut_response_too_large')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            retry = error.headers.get('Retry-After', '') if error.headers else ''
            delay = min(3600, max(1, int(retry))) if retry.isdigit() else (60 if error.code == 429 else None)
            raise self.helpers.SourceError('shortcut_http', error.code, retry_after=delay) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise self.helpers.SourceError('shortcut_network') from None
        except (ValueError, UnicodeError):
            raise self.helpers.SourceError('shortcut_invalid_json') from None

    def verify(self):
        member = self.get(PREFIX + 'member')
        if not isinstance(member, dict) or not member.get('id') or not isinstance(member.get('workspace2'), dict):
            raise self.helpers.SourceError('shortcut_invalid_member')
        if member['workspace2'].get('url_slug') != self.workspace:
            raise self.helpers.SourceError('account_identity_mismatch')
        return member


def continuation(value, helpers):
    if not value:
        return None
    if not isinstance(value, str):
        raise helpers.SourceError('shortcut_invalid_cursor')
    # The API returns a path and query string, not an arbitrary URL or endpoint.
    parts = urllib.parse.urlsplit(value)
    if parts.scheme or parts.netloc or parts.fragment or parts.path != PREFIX + 'search/stories' or not parts.query:
        raise helpers.SourceError('shortcut_invalid_cursor')
    return value


def moves(source, story, window, member, helpers):
    if not isinstance(story, dict) or type(story.get('id')) is not int or story['id'] <= 0:
        raise helpers.SourceError('shortcut_invalid_story')
    if (not isinstance(story.get('owner_ids', []), list)
            or any(not isinstance(owner, str) for owner in story.get('owner_ids', []))):
        raise helpers.SourceError('shortcut_invalid_story')
    history = source.get(PREFIX + f"stories/{story['id']}/history")
    if not isinstance(history, list):
        raise helpers.SourceError('shortcut_invalid_history')
    events = []
    for item in history:
        if not isinstance(item, dict) or not item.get('id') or not isinstance(item.get('actions'), list):
            raise helpers.SourceError('shortcut_invalid_history')
        stamp = timestamp(item.get('changed_at'), helpers)
        if not timestamp(window['since'], helpers) <= stamp <= timestamp(window['until'], helpers):
            continue
        refs = item.get('references') or []
        if not isinstance(refs, list) or any(not isinstance(r, dict) for r in refs):
            raise helpers.SourceError('shortcut_invalid_history')
        names = {r.get('id'): plain(r.get('name')) for r in refs if r.get('entity_type') == 'workflow-state'}
        for action in item['actions']:
            if not isinstance(action, dict):
                raise helpers.SourceError('shortcut_invalid_history')
            if action.get('entity_type') != 'story' or action.get('id') != story['id']:
                continue
            changes = action.get('changes') or {}
            if not isinstance(changes, dict):
                raise helpers.SourceError('shortcut_invalid_history')
            change = changes.get('workflow_state_id')
            if change is None:
                continue
            if not isinstance(change, dict) or any(type(change.get(k)) is not int for k in ('old', 'new')):
                raise helpers.SourceError('shortcut_invalid_move')
            old, new = change['old'], change['new']
            if old == new:
                continue
            before, after = names.get(old) or str(old), names.get(new) or str(new)
            title = plain(action.get('name') or story.get('name') or 'Story')
            body = '\n'.join([f'Moved: {before} → {after}',
                              'Current story context (may be newer than this move):',
                              f"Current state ID: {story.get('workflow_state_id', '')}",
                              f"Owner IDs: {', '.join(story.get('owner_ids') or [])}",
                              f"Requested by: {story.get('requested_by_id') or ''}",
                              f"Deadline: {story.get('deadline') or ''}",
                              f"Blocked: {bool(story.get('blocked'))}; completed: {bool(story.get('completed'))}",
                              plain(story.get('description'))])
            key = f"{story['id']}:{item['id']}:{old}:{new}"
            events.append({'account_id': source.account['id'], 'source_id': hashlib.sha256(key.encode()).hexdigest(),
                           'kind': 'shortcut.move', 'occurred_at': stamp.isoformat(), 'title': title[:1000],
                           'body': plain(body)[:40000], 'deleted': False,
                           'metadata': {'source_url': f"https://app.shortcut.com/{source.workspace}/story/{story['id']}",
                                        'story_id': story['id'], 'history_id': item['id'],
                                        'previous_state_id': old, 'state_id': new,
                                        'previous_state': before, 'state': after,
                                        'owner_ids': story.get('owner_ids') or [], 'deadline': story.get('deadline'),
                                        'from': item.get('member_id') or '',
                                        'sender_display': plain(item.get('actor_name')),
                                        'from_me': bool(item.get('member_id')) and item['member_id'] == member['id']}})
    return events


def sync(store, config, account, helpers):
    source = Source(config, account, helpers)
    member = source.verify()
    state = copy.deepcopy(store.checkpoint(account['id']) or {})
    now = timestamp(helpers.now(), helpers)

    def fresh():
        since = timestamp(state['since'], helpers) - dt.timedelta(days=1) if state.get('since') else now - dt.timedelta(days=int(config.get('bootstrap_days', 7)))
        return {'since': since.isoformat(), 'until': now.isoformat()}

    window = state.get('catchup') or fresh()
    count = read = 0
    reset = False
    seen = set()
    for _ in range(PAGES):
        query = 'moved:' + timestamp(window['since'], helpers).date().isoformat() + '..*'
        path = continuation(window.get('cursor'), helpers) or PREFIX + 'search/stories?' + urllib.parse.urlencode(
            {'query': query, 'page_size': LIMIT, 'detail': 'full'})
        try:
            page = source.get(path)
        except helpers.SourceError as error:
            if error.status in (400, 422) and window.get('cursor') and not reset:
                # Expired search snapshots restart a bounded recent window; replay is idempotent.
                window, reset = fresh(), True
                query = 'moved:' + timestamp(window['since'], helpers).date().isoformat() + '..*'
                page = source.get(PREFIX + 'search/stories?' + urllib.parse.urlencode(
                    {'query': query, 'page_size': LIMIT, 'detail': 'full'}))
            else:
                raise
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise helpers.SourceError('shortcut_invalid_page')
        # Shortcut caps search at 1000 results. Never claim complete coverage if it truncates.
        if not isinstance(page.get('total'), int) or page['total'] > 1000:
            raise helpers.SourceError('shortcut_search_limit')
        cursor = continuation(page.get('next'), helpers)
        if cursor and (not page['data'] or cursor == window.get('cursor') or cursor in seen):
            raise helpers.SourceError('shortcut_invalid_cursor')
        events = []
        for story in page['data']:
            events.extend(moves(source, story, window, member, helpers))
        count += len(events)
        read += len(page['data'])
        more = bool(cursor)
        if more:
            seen.add(cursor)
            window['cursor'] = cursor
            state['catchup'] = window
        else:
            state['since'] = window['until']
            state.pop('catchup', None)
        state.update(mode='incremental', coverage_complete=not more,
                     coverage='Shortcut story history pages are still being collected.' if more else 'Shortcut pipeline moves caught up.')
        store.commit_page(account['id'], state, events)
        if not more:
            break
    return {'mode': 'incremental', 'events': count, 'read': read, 'more': more}


def check(config, account, helpers):
    """Verify workspace access, with no store or checkpoint writes."""
    Source(config, account, helpers).verify()
