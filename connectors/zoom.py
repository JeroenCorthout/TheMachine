"""Zoom meeting notes: the AI Companion summary of each meeting the owner hosted, and its transcript when Zoom has one.
Read only: GET requests to api.zoom.us, nothing is ever written to Zoom.

The sign-in is the owner's own (user-level OAuth, a Zoom Marketplace general app), never an account-wide
Server-to-Server app: it reads one person's meetings, not their whole company's. The app and its secret live with a token
broker (a hosting control plane, like the Google and Microsoft ones); the refresh token lives on this Machine only,
sealed in mail_grants.py, and is sent along to the broker for each short-lived access token. Zoom rotates it on every
refresh: the broker passes the new one back and it is kept here.

    "zoom_broker": {"url": "https://app.example.com/api/zoom/token"},
    "credentials": {"zoom-broker": "~/.local/share/personal-assistant/google-broker"}

The broker answers POST {"email", "refresh_token"} with {"access_token", "expires_in", "scopes", "refresh_token"}.

Scopes (granular, user level): user:read:user, meeting:read:list_summaries, meeting:read:summary,
meeting:read:list_meetings, meeting:read:list_past_instances, cloud_recording:read:meeting_transcript.

Each run lists the summaries made since the last one (an overlap window; the first run reads bootstrap_days back) and
reads each new or edited one. When the summaries list is not open to this sign-in, past meetings and their instances
are listed instead. Summaries exist only for meetings with AI Companion on, on a paid plan, not end-to-end encrypted.
"""
import datetime as dt
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

KINDS = {'zoom.summary': 'Zoom notes', 'zoom.transcript': 'Zoom transcript'}
SETUP = {
    'label': 'Zoom',
    'account': {'id': 'zoom', 'service': 'zoom', 'transport': 'broker', 'boundary': 'work', 'poll_seconds': 900},
    'what': 'Meeting summaries and transcripts from Zoom.',
    'note': 'Zoom is connected with Connect Zoom on your setup page, which signs in with your own Zoom account. '
            'It needs a Zoom sign-in broker (zoom_broker in config.json).',
    'ask': [('email', 'Your Zoom sign-in address')],
    'broker': True,  # signed in at the hosting control plane, not here
    'hints': {'zoom_signin_expired': 'Zoom asks you to connect again', 'zoom_broker_not_configured': 'Zoom needs a sign-in broker',
              'zoom_http:HTTP_403': 'Zoom refused: AI Companion summaries need a paid plan, and the app needs its read scopes'}}
API = 'https://api.zoom.us/v2'
PAGE = 300
OVERLAP_HOURS = 24  # a summary is made, or edited, a while after its meeting: reread a day back each time
MAX_MEETINGS = 60  # per run, at most this many summaries are read (the rest next run)
TRANSCRIPT_BYTES = 2_000_000
SEEN_KEEP = 500
_tokens = {}
_lock = threading.Lock()


def broker_token(config, account, helpers, fresh=False):
    """A short-lived access token through the broker, sending the sign-in kept here and keeping the rotated one."""
    import mail_grants
    email = str(account.get('email') or '').strip().lower()
    if not email:
        raise helpers.SourceError('zoom_no_email')
    with _lock:
        hit = _tokens.get(email)
        if hit and not fresh and hit[1] > time.monotonic():
            return hit[0]
        url = (config.get('zoom_broker') or {}).get('url')
        if not url or not str(url).startswith('https://'):
            raise helpers.SourceError('zoom_broker_not_configured')
        try:
            kept = mail_grants.get(config, 'zoom', email)
        except mail_grants.GrantError:
            raise helpers.SourceError('zoom_signin_unreadable') from None
        if not kept:
            raise helpers.SourceError('zoom_signin_expired')
        try:
            secret = helpers.credential(config, 'zoom-broker')
        except Exception:
            raise helpers.SourceError('zoom_broker_credential') from None
        request = urllib.request.Request(url, method='POST', data=json.dumps({'email': email, 'refresh_token': kept}).encode(),
                                         headers={'Authorization': 'Bearer ' + secret.strip(), 'Content-Type': 'application/json',
                                                  'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                answer = json.loads(response.read(200_000))
        except urllib.error.HTTPError as error:
            retry = error.headers.get('Retry-After', '') if error.headers else ''
            error.close()
            if error.code in (401, 404):
                raise helpers.SourceError('zoom_signin_expired', error.code) from None
            raise helpers.SourceError('zoom_broker', error.code, retry_after=int(retry) if retry.isdigit() else None) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise helpers.SourceError('zoom_broker_unreachable') from None
        access = answer.get('access_token') if isinstance(answer, dict) else None
        if not isinstance(access, str) or not access:
            raise helpers.SourceError('zoom_broker')
        rotated = answer.get('refresh_token')
        if isinstance(rotated, str) and rotated and rotated != kept:
            mail_grants.rotate(config, 'zoom', email, kept, rotated)
        _tokens[email] = (access, time.monotonic() + max(0, int(answer.get('expires_in') or 0) - 120))
        return access


class Source:
    def __init__(self, config, account, helpers):
        self.config, self.account, self.helpers = config, account, helpers

    def get(self, path, params=None, raw=False, url=None, limit=10_000_000):
        """One GET to Zoom's API (or a transcript's download link on zoom.us). Never any other method or host."""
        target = url or API + path + ('?' + urllib.parse.urlencode(params) if params else '')
        host = urllib.parse.urlsplit(target)
        if host.scheme != 'https' or not (host.hostname == 'zoom.us' or str(host.hostname).endswith('.zoom.us')):
            raise self.helpers.SourceError('zoom_unexpected_host')
        for attempt in (0, 1):
            token = broker_token(self.config, self.account, self.helpers, fresh=bool(attempt))
            request = urllib.request.Request(target, method='GET', headers={'Authorization': 'Bearer ' + token,
                                                                            'Accept': '*/*' if raw else 'application/json',
                                                                            'User-Agent': 'TheMachine/1'})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = response.read(limit + 1)
                if len(body) > limit:
                    raise self.helpers.SourceError('zoom_response_too_large')
                return body if raw else json.loads(body or b'{}')
            except urllib.error.HTTPError as error:
                retry = error.headers.get('Retry-After', '') if error.headers else ''
                error.close()
                if error.code == 401 and not attempt:
                    continue  # the access token expired early: one fresh one
                delay = min(3600, max(1, int(retry))) if retry.isdigit() else (60 if error.code == 429 else None)
                raise self.helpers.SourceError('zoom_http', error.code, retry_after=delay) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise self.helpers.SourceError('zoom_network') from None
            except (ValueError, UnicodeError):
                raise self.helpers.SourceError('zoom_invalid_json') from None
        raise self.helpers.SourceError('zoom_http', 401)

    def me(self):
        found = self.get('/users/me')
        if not isinstance(found, dict) or not found.get('id'):
            raise self.helpers.SourceError('zoom_invalid_user')
        return found

    def listed(self, start, end):
        """(uuid, last modified) of the summaries made in the window, newest first, or None when this sign-in may not
        list them (the fallback then lists past meetings)."""
        found, token = [], ''
        for _ in range(10):
            params = {'from': zulu(start), 'to': zulu(end), 'page_size': PAGE}
            if token:
                params['next_page_token'] = token
            try:
                page = self.get('/meetings/meeting_summaries', params)
            except self.helpers.SourceError as error:
                if error.status in (400, 403, 404) and not found:
                    return None
                raise
            for item in page.get('summaries') or []:
                if isinstance(item, dict) and isinstance(item.get('meeting_uuid'), str):
                    found.append((item['meeting_uuid'], str(item.get('summary_last_modified_time') or item.get('summary_created_time') or '')))
            token = page.get('next_page_token') or ''
            if not token:
                break
        return found

    def past(self, start, end):
        """The fallback: instances of the owner's past meetings in the window, each a (uuid, '') to try."""
        found = []
        page = self.get('/users/me/meetings', {'type': 'previous_meetings', 'page_size': PAGE})
        for meeting in page.get('meetings') or []:
            if not isinstance(meeting, dict) or not meeting.get('id'):
                continue
            begun = stamp(meeting.get('start_time'))
            if begun and begun > end:
                continue
            try:
                instances = self.get(f"/past_meetings/{int(meeting['id'])}/instances")
            except (self.helpers.SourceError, ValueError, TypeError):
                continue
            for item in instances.get('meetings') or []:
                at = stamp((item or {}).get('start_time'))
                if at and start <= at <= end and isinstance(item.get('uuid'), str):
                    found.append((item['uuid'], ''))
            if len(found) >= MAX_MEETINGS:
                break
        return found

    def summary(self, uuid):
        try:
            return self.get(f'/meetings/{meeting_path(uuid)}/meeting_summary')
        except self.helpers.SourceError as error:
            if error.status in (400, 404):
                return None  # no summary for this meeting (AI Companion was off, or it is not ready yet)
            raise

    def transcript(self, uuid):
        """The meeting's transcript as plain text, or None when Zoom has none for it or this sign-in may not read it."""
        try:
            found = self.get(f'/meetings/{meeting_path(uuid)}/transcript')
            link = found.get('download_url') if isinstance(found, dict) and found.get('can_download', True) else None
            if not isinstance(link, str) or not link:
                return None
            return vtt_text(self.get('', url=link, raw=True, limit=TRANSCRIPT_BYTES).decode('utf-8', 'replace'))
        except self.helpers.SourceError as error:
            if error.status in (400, 403, 404, 422):
                return None
            raise


def meeting_path(uuid):
    """Zoom's rule for a meeting UUID in a path: encoded twice when it starts with / or holds //."""
    once = urllib.parse.quote(uuid, safe='')
    return urllib.parse.quote(once, safe='') if uuid.startswith('/') or '//' in uuid else once


def zulu(moment):
    return moment.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def vtt_text(vtt):
    """A WebVTT transcript as 'Speaker: words' lines: cue numbers and timings dropped, a speaker's run joined."""
    lines, last = [], None
    for block in re.split(r'\r?\n\r?\n', vtt):
        text = [line for line in block.splitlines() if line.strip() and '-->' not in line
                and not line.strip().isdigit() and not line.startswith('WEBVTT')]
        if not text:
            continue
        line = ' '.join(t.strip() for t in text)
        who, sep, said = line.partition(': ')
        if sep and len(who) <= 80 and who == last and lines:
            lines[-1] += ' ' + said
        else:
            lines.append(line)
            last = who if sep else None
    return '\n'.join(lines)


def summary_text(found):
    """The summary as plain text: Zoom's full Markdown when it has it, else the older overview, sections and next steps
    (the owner's edited version first)."""
    content = found.get('summary_content')
    if isinstance(content, str) and content.strip():
        return content.strip()
    edited = found.get('edited_summary') if isinstance(found.get('edited_summary'), dict) else {}
    parts = []
    overview = edited.get('summary_overview') or found.get('summary_overview')
    if overview:
        parts.append(str(overview).strip())
    details = edited.get('summary_details') or found.get('summary_details')
    if isinstance(details, str) and details.strip():
        parts.append(details.strip())
    elif isinstance(details, list):
        for item in details:
            if isinstance(item, dict) and item.get('summary'):
                parts.append((f"{item['label']}\n" if item.get('label') else '') + str(item['summary']).strip())
    steps = next_steps(found)
    if steps:
        parts.append('Next steps\n' + '\n'.join('- ' + s for s in steps))
    return '\n\n'.join(parts)


def next_steps(found):
    edited = found.get('edited_summary') if isinstance(found.get('edited_summary'), dict) else {}
    steps = edited.get('next_steps') or found.get('next_steps') or []
    return [' '.join(str(s).split()) for s in steps if isinstance(s, str) and s.strip()][:40] if isinstance(steps, list) else []


def source_id(kind, uuid):
    return kind + ':' + hashlib.sha256(uuid.encode()).hexdigest()[:32]


def events_for(account, found, transcript, helpers):
    uuid = str(found.get('meeting_uuid') or '')
    begun = stamp(found.get('meeting_start_time')) or stamp(found.get('summary_start_time'))
    if not uuid or not begun:
        raise helpers.SourceError('zoom_invalid_summary')
    ended = stamp(found.get('meeting_end_time')) or stamp(found.get('summary_end_time'))
    topic = ' '.join(str(found.get('meeting_topic') or found.get('summary_title') or 'Zoom meeting').split())
    link = found.get('summary_doc_url')
    host = urllib.parse.urlsplit(link) if isinstance(link, str) else None
    meta = {'meeting_id': str(found.get('meeting_id') or ''), 'start_at': begun.isoformat(),
            'end_at': ended.isoformat() if ended else None, 'host_email': str(found.get('meeting_host_email') or '').lower(),
            'next_steps': next_steps(found), 'edited_at': str(found.get('summary_last_modified_time') or ''),
            **({'source_url': link} if host and host.scheme == 'https' and str(host.hostname).endswith('zoom.us') else {})}
    out = [{'account_id': account['id'], 'source_id': source_id('summary', uuid), 'kind': 'zoom.summary',
            'occurred_at': begun.isoformat(), 'title': topic[:1000], 'body': summary_text(found)[:40000],
            'metadata': meta, 'deleted': False}]
    if transcript:
        out.append({'account_id': account['id'], 'source_id': source_id('transcript', uuid), 'kind': 'zoom.transcript',
                    'occurred_at': begun.isoformat(), 'title': topic[:1000], 'body': transcript[:40000],
                    'metadata': {**{k: meta[k] for k in ('meeting_id', 'start_at', 'end_at', 'host_email')},
                                 'truncated': len(transcript) > 40000}, 'deleted': False})
    return out


def sync(store, config, account, helpers):
    source = Source(config, account, helpers)
    state = dict(store.checkpoint(account['id']) or {})
    now = stamp(helpers.now())
    since = stamp(state.get('since'))
    start = (since - dt.timedelta(hours=OVERLAP_HOURS)) if since else now - dt.timedelta(days=int(config.get('bootstrap_days', 7)))
    listed = source.listed(start, now)
    mode = 'summaries'
    if listed is None:
        listed, mode = source.past(start, now), 'past_meetings'
    seen = dict(state.get('seen') or {})
    count = read = 0
    for uuid, edited in listed[:MAX_MEETINGS]:
        key = source_id('summary', uuid)
        if key in seen and (not edited or seen[key] == edited):
            continue
        found = source.summary(uuid)
        read += 1
        if not isinstance(found, dict) or not found.get('meeting_uuid'):
            continue
        found_edit = str(found.get('summary_last_modified_time') or edited or found.get('summary_created_time') or '')
        if key in seen and seen[key] == found_edit:
            continue
        transcript = source.transcript(uuid) if config.get('zoom_transcripts', True) else None
        events = events_for(account, found, transcript, helpers)
        seen[key] = found_edit or 'read'
        if len(seen) > SEEN_KEEP:
            seen = dict(list(seen.items())[-SEEN_KEEP:])
        store.commit_page(account['id'], {**state, 'seen': seen}, events)
        count += len(events)
    more = len(listed) > MAX_MEETINGS
    state.update(seen=seen, mode=mode, coverage_complete=not more,
                 coverage='Older Zoom summaries are still being read.' if more else 'Zoom summaries caught up.')
    if not more:
        state['since'] = now.isoformat()
    store.commit_page(account['id'], state, [])
    return {'mode': 'incremental', 'events': count, 'read': read, 'more': more}


def check(config, account, helpers):
    """A real read without storing anything: who is signed in, and that their summaries can be listed."""
    source = Source(config, account, helpers)
    me = source.me()
    email = str(me.get('email') or '').lower()
    if email and account.get('email') and email != str(account['email']).lower():
        raise helpers.SourceError('account_identity_mismatch')
    now = stamp(helpers.now())
    if source.listed(now - dt.timedelta(days=1), now) is None:
        source.get('/users/me/meetings', {'type': 'previous_meetings', 'page_size': 1})
    return None
