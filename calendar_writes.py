"""Owner-approved changes to Google calendars: add, change or delete one event.

Which calendars: the owner's own (a Google Calendar connection without calendar_id, or "primary"), and a calendar
someone shared with them that they marked writable in the config ("writable": true; the share itself must allow
"Make changes to events"). A calendar from a Fleet grant, an Outlook calendar and every other shared calendar stay
read-only here.

How: every write is one the owner saw and tapped. A card offers it as a next action {kind: calendar} and its Add,
Save or Delete runs only after their confirm; the Calendar tab's own form writes when they tap Add or Save. An event
never gets guests and Google never sends anyone an update (sendUpdates none), so a write reaches nobody.
Nothing here asks for a new permission: a sign-in without calendar write access gets a plain refusal."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
import tempfile
import time

WRITE_SCOPES = {'https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/calendar.events'}
SCOPE = 'https://www.googleapis.com/auth/calendar.events'
READ_SCOPE = 'https://www.googleapis.com/auth/calendar.readonly'
OPS = ('create', 'update', 'delete')
TEXT = {'title': 200, 'location': 300, 'notes': 2000}
KEYS = {'kind', 'op', 'calendar', 'event', 'title', 'start', 'end', 'all_day', 'location', 'notes', 'label'}
MAX_DAYS = 60  # one event, not a season


def own(account):
    return str(account.get('calendar_id') or 'primary') == 'primary'


def writable(account):
    """A Google calendar the owner may change from The Machine: their own, or a shared one marked writable."""
    if not isinstance(account, dict) or account.get('service') != 'calendar' or account.get('transport') not in ('gog', 'broker'):
        return False
    if account.get('shared'):  # a connector from a Fleet grant: someone else's sign-in
        return False
    return own(account) or account.get('writable') is True


def name(account):
    """How the owner knows this calendar: its display name ("Robin’s calendar"), else the address."""
    return str(account.get('display_name') or account.get('email') or account.get('id'))[:80]


def calendars(config, allowed=None):
    """The calendars the owner can add to, for the Calendar form's picker and the preparation prompt. allowed(account)
    says whether its sign-in may write (see may_write), so a read-only sign-in never offers a change it can't make."""
    return [{'id': a['id'], 'name': name(a), 'own': own(a)} for a in config.get('accounts', [])
            if writable(a) and (allowed is None or allowed(a))]


GRANTS = {}


def may_write(config, credential, account, scopes, ttl=600, clock=time.monotonic):
    """Whether this account's sign-in has calendar write access, asked at most every ttl seconds (a failed check:
    no, for a minute). The write itself checks again."""
    key = (account.get('id'), account.get('email'), account.get('client'), account.get('transport'))
    hit, now = GRANTS.get(key), clock()
    if hit and now - hit[0] < hit[2]:
        return hit[1]
    try:
        ok, life = bool(set(scopes(config, credential, account)) & WRITE_SCOPES), ttl
    except Exception:  # noqa: BLE001 - any failure to check means no change offered
        ok, life = False, 60
    GRANTS[key] = (now, ok, life)
    return ok


def account_for(config, account_id):
    account = next((a for a in config.get('accounts', []) if a.get('id') == account_id), None)
    if not writable(account):
        raise ValueError('That calendar can’t be changed from The Machine. Pick one of your own, or a shared one marked writable.')
    return account


def _day(value):
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError('An all-day event takes dates like 2026-10-27') from None


def _moment(value):
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Give start and end as a date and time with its offset, like 2026-10-27T16:00:00+01:00') from None
    if stamp.tzinfo is None:
        raise ValueError('Give start and end with a time zone offset, like 2026-10-27T16:00:00+01:00')
    return stamp


def when(start, end, all_day):
    """"Mon 27 Oct 2026, 16:00 to 16:30", "Mon 27 Oct 2026, all day", in the event's own clock."""
    if all_day:
        a, b = _day(start), _day(end)
        day = lambda d: d.strftime('%a ') + str(d.day) + d.strftime(' %b %Y')
        return day(a) + (', all day' if a == b else ' to ' + day(b))
    a, b = _moment(start), _moment(end)
    first = a.strftime('%a ') + str(a.day) + a.strftime(' %b %Y, %H:%M')
    return first + ' to ' + (b.strftime('%H:%M') if a.date() == b.date() else b.strftime('%a ') + str(b.day) + b.strftime(' %b, %H:%M'))


def _times(start, end, all_day):
    if all_day:
        a, b = _day(start), _day(end if end not in (None, '') else start)  # end: the last day, inclusive
        if not 0 <= (b - a).days < MAX_DAYS:
            raise ValueError('An all-day event ends on or after its first day, within 60 days')
        return a.isoformat(), b.isoformat()
    a, b = _moment(start), _moment(end)
    if not dt.timedelta(0) < b - a <= dt.timedelta(days=MAX_DAYS):
        raise ValueError('An event ends after it starts, within 60 days')
    return a.isoformat(), b.isoformat()


def _text(value, field):
    if value is None:
        return ''
    if not isinstance(value, str) or '\x00' in value or len(value) > TEXT[field]:
        raise ValueError(f'{field} is text of at most {TEXT[field]} characters')
    return value.strip() if field == 'notes' else ' '.join(value.split())


def validate(value, config, events=None):
    """A calendar change as the owner will see it. `events` maps an event's id (from the events table) to what is
    known about it: account_id, source_id, title, start, end, all_day, guests. Returns the action without a revision."""
    if set(value) - KEYS:
        raise ValueError('A calendar change has op, calendar, event, title, start, end, all_day, location and notes; '
                         'no guests: it never invites anyone')
    op = value.get('op', 'create')
    if op not in OPS:
        raise ValueError('op is create, update or delete')
    result = {'kind': 'calendar', 'op': op}
    if op == 'create':
        account = account_for(config, value.get('calendar'))
        before = {}
    else:
        event = (events or {}).get(value.get('event')) if isinstance(value.get('event'), int) and not isinstance(value.get('event'), bool) else None
        if not event:
            raise ValueError('event is the id of a meeting on a calendar you can change (from events or the Calendar)')
        if value.get('calendar') not in (None, event['account_id']):
            raise ValueError('That event is on another calendar')
        account = account_for(config, event['account_id'])
        if event.get('guests'):
            raise ValueError('That event has guests. Change it in the calendar itself, so they hear about it.')
        before = {'title': event['title'], 'when': when(event['start'], event['end'], event['all_day'])}
        result.update(event_id=event['source_id'], event=value['event'], was=before)
    result.update(account_id=account['id'], calendar_name=name(account))
    if op == 'delete':
        if set(value) - {'kind', 'op', 'event', 'calendar', 'label'}:
            raise ValueError('A delete only names the event')
        result['title'] = before['title']
        result['when'] = before['when']
        return result
    all_day = value.get('all_day', event['all_day'] if op == 'update' else False)
    if not isinstance(all_day, bool):
        raise ValueError('all_day is true or false')
    changes = {}
    for field in ('title', 'location', 'notes'):
        if op == 'create' or field in value:
            changes[field] = _text(value.get(field), field)
    if op == 'create' and not changes['title']:
        raise ValueError('A new event needs a title')
    if op == 'update' and 'title' in changes and not changes['title']:
        raise ValueError('An event keeps a title')
    if op == 'create' or any(k in value for k in ('start', 'end', 'all_day')):
        start = value.get('start', None if op == 'create' else event['start'])
        end = value.get('end', None if op == 'create' or 'start' in value else event['end'])
        if start in (None, '') or (end in (None, '') and not all_day):
            raise ValueError('Give the event a start and an end')
        if op == 'update' and all_day != event['all_day'] and ('start' not in value or ('end' not in value and not all_day)):
            raise ValueError('Switching between all day and a time needs a new start and end')
        changes['start'], changes['end'] = _times(start, end, all_day)
        changes['all_day'] = all_day
    if op == 'update' and not changes:
        raise ValueError('Say what changes: title, start, end, all_day, location or notes')
    result.update(changes)
    if op == 'update':
        result['changed'] = sorted(changes)
    result['title'] = changes.get('title') or before.get('title', '')
    if 'start' in changes:
        result['when'] = when(changes['start'], changes['end'], changes['all_day'])
    else:
        result['when'] = before['when']
    return result


def label(action):
    return {'create': 'Add to calendar', 'update': 'Save event', 'delete': 'Delete event'}[action['op']]


def done(action):
    """What happened, once it went through: "Added to Robin’s calendar: Swimming"."""
    return {'create': 'Added to ', 'update': 'Changed on ', 'delete': 'Deleted from '}[action['op']] + action['calendar_name'] + ': ' + action['title']


def preview(action):
    head = {'create': 'Add to ', 'update': 'Change on ', 'delete': 'Delete from '}[action['op']] + action['calendar_name']
    lines = [head, action['title'], action['when']]
    if action['op'] == 'update' and action.get('was') and (action['was']['title'], action['was']['when']) != (action['title'], action['when']):
        lines.append('Was: ' + action['was']['title'] + ', ' + action['was']['when'])
    if action.get('location'):
        lines.append('Where: ' + action['location'])
    if action.get('notes'):
        lines.append('Notes: ' + action['notes'])
    return '\n'.join(lines)


def event_id(key):
    """A Google event id from the approval itself (base32hex), so a retried insert is the same event, never a second."""
    return 'tm' + hashlib.sha256(str(key).encode()).hexdigest()[:40]


def body(action, key=None):
    """The Google event fields this change sets: on an update only those it changes; never attendees."""
    changed = set(action.get('changed') or ()) if action['op'] == 'update' else {'title', 'location', 'notes', 'start'}
    out = {}
    if 'title' in changed:
        out['summary'] = action['title']
    for field, google in (('location', 'location'), ('notes', 'description')):
        if field in changed and (action.get(field) or action['op'] == 'update'):
            out[google] = action.get(field, '')
    if 'start' in changed:
        kind = 'date' if action['all_day'] else 'dateTime'
        end = (dt.date.fromisoformat(action['end']) + dt.timedelta(days=1)).isoformat() if action['all_day'] else action['end']
        out['start'], out['end'] = {kind: action['start']}, {kind: end}
        if action['op'] == 'update':  # the other kind of time goes, or Google keeps both
            other = 'dateTime' if action['all_day'] else 'date'
            out['start'][other] = out['end'][other] = None
    if action['op'] == 'create':
        out['id'] = event_id(key)
    return out


class CalendarTransport:
    """Runs one approved calendar change: checks first (the calendar, the sign-in's permission, the event), then one
    write. An unknown outcome is checked by reading the event back, never by writing again."""

    def __init__(self, config, credential, read, run=subprocess.run):
        self.config, self.credential, self.read, self.run = config, credential, read, run

    def account(self, action):
        if action.get('kind') != 'calendar':
            raise ValueError('Only an approved calendar change uses the calendar transport')
        return account_for(self.config, action['account_id'])

    def calendar_id(self, account):
        return str(account.get('calendar_id') or 'primary')

    def get(self, account, method, params):
        return self.read(self.config, account, 'calendar', 'v3', 'calendar.' + method, {'calendarId': self.calendar_id(account), **params}, READ_SCOPE)

    def preflight(self, action):
        import next_actions  # noqa: PLC0415 - next_actions imports this module
        account = self.account(action)
        if not next_actions.granted_scopes(self.config, self.credential, account) & WRITE_SCOPES:
            raise ValueError('This Google sign-in can only read calendars. Nothing was changed.')
        try:
            entry = self.get(account, 'calendarList.get', {})
        except Exception:
            raise ValueError('I couldn’t check that calendar. Nothing was changed.') from None
        if entry.get('accessRole') not in ('writer', 'owner'):
            raise ValueError(f'{name(account)} is shared with you to view only. Its owner can share it with “Make changes to events”. Nothing was changed.')
        if action['op'] != 'create':
            event = self.event(account, action['event_id'])
            if not event or event.get('status') == 'cancelled':
                raise ValueError('That event is no longer on the calendar. Nothing was changed.')
            if any(not a.get('self') for a in event.get('attendees') or []):
                raise ValueError('That event has guests now. Change it in the calendar itself. Nothing was changed.')

    def event(self, account, google_id):
        try:
            return self.get(account, 'events.get', {'eventId': google_id})
        except Exception as error:  # noqa: BLE001 - a read; 404 and 410 mean it is gone
            if getattr(error, 'status', None) in (404, 410):
                return None
            raise

    def write(self, account, method, params, payload=None):
        """(status, answer): status None when it went through, else Google's HTTP status (0 when unknown)."""
        params = {'calendarId': self.calendar_id(account), 'sendUpdates': 'none', **params}
        if account.get('transport') == 'broker':
            import google_api  # noqa: PLC0415
            try:
                return None, google_api.call(self.config, self.credential, account, 'calendar.' + method, params, SCOPE, body=payload, write=True)
            except google_api.BrokerError as error:
                return error.status or 0, {}
        import next_actions  # noqa: PLC0415
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as file:
            file.write(json.dumps(payload or {}, ensure_ascii=False));file.flush()
            command = [self.config['gog'], 'api', 'call', 'calendar', 'v3', 'calendar.' + method, '--params', json.dumps(params),
                       *(['--body', '@' + file.name] if payload is not None else []), '--scope', SCOPE,
                       '--account', account['email'], '--client', account['client'], '--allow-write', '--force', '--no-input', '--json']
            env = next_actions.EmailTransport.env(self)
            try:
                result = self.run(command, env=env, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                return 0, {}
        if result.returncode:
            return next_actions.google_error_code(result.stdout) or 0, {}
        try:
            return None, json.loads(result.stdout or '{}')
        except ValueError:
            return None, {}

    def send(self, action, job):
        account = self.account(action)
        if action['op'] == 'create':
            payload = body(action, job['id'])
            status, answer = self.write(account, 'events.insert', {}, payload)
            if status == 409:  # this approval's event is there already: the earlier try went through
                return self.reconcile(action, job)
            google_id = payload['id']
        elif action['op'] == 'update':
            status, answer = self.write(account, 'events.patch', {'eventId': action['event_id']}, body(action))
            google_id = action['event_id']
        else:
            status, answer = self.write(account, 'events.delete', {'eventId': action['event_id']})
            google_id = action['event_id']
            if status == 410:
                status = None  # gone already: what the owner asked for
        if status is None:
            return {'message_id': 'calendar:' + google_id}
        if 400 <= status < 500 and status not in (408, 429):
            return {'rejected': status}
        return {}

    def reconcile(self, action, job):
        account = self.account(action)
        google_id = event_id(job['id']) if action['op'] == 'create' else action['event_id']
        try:
            event = self.event(account, google_id)
        except Exception:  # noqa: BLE001 - can't read now: still unknown
            return {}
        if action['op'] == 'delete':
            return {'message_id': 'calendar:' + google_id} if not event or event.get('status') == 'cancelled' else {}
        if not event or event.get('status') == 'cancelled':
            return {}
        if action['op'] == 'update' and 'title' in action.get('changed', ()) and event.get('summary') != action['title']:
            return {}
        return {'message_id': 'calendar:' + google_id}
