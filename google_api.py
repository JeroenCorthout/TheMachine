"""Gmail and Google Calendar through a token broker instead of gog.

A broker is a service you run that holds the Google OAuth client and its secret (a hosted control plane, for
example). The account's refresh token stays on this machine (mail_grants.py): it asks the broker for a short-lived
access token for one account and one scope, sending the refresh token along, then calls Google's REST API directly. An account uses it with
"transport": "broker"; the broker's address and this machine's token are in the config:

    "google_broker": {"url": "https://app.example.com/api/google/token"},
    "credentials": {"google-broker": "~/.local/share/personal-assistant/google-broker"}

The broker answers POST {"email", "scope", "refresh_token"} with {"access_token", "expires_in", "scopes"} (the
scopes the owner granted) and keeps nothing; POST {"email"} with {"scopes"}, and 403 for a scope that wasn't granted.
An account connected before its sign-in was kept here asks without "refresh_token" until it is moved here. Only the methods
below can be called; anything else is refused here, before a token is asked for."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import mail_grants

READ = {
    'gmail.users.getProfile': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/profile'),
    'gmail.users.messages.list': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/messages'),
    'gmail.users.messages.get': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/messages/{id}'),
    'gmail.users.messages.attachments.get': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}'),
    'gmail.users.history.list': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/history'),
    'gmail.users.threads.get': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/threads/{id}'),
    'gmail.users.labels.list': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/labels'),
    'gmail.users.settings.sendAs.get': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}'),
    'gmail.users.settings.sendAs.list': ('GET', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/settings/sendAs'),
    'calendar.calendars.get': ('GET', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}'),
    'calendar.events.list': ('GET', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events'),
    'calendar.events.get': ('GET', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}'),
    'calendar.calendarList.get': ('GET', 'https://www.googleapis.com/calendar/v3/users/me/calendarList/{calendarId}'),
}
WRITE = {
    'gmail.users.messages.send': ('POST', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/messages/send'),
    'gmail.users.labels.create': ('POST', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/labels'),
    'gmail.users.threads.modify': ('POST', 'https://gmail.googleapis.com/gmail/v1/users/{userId}/threads/{id}/modify'),
    # Owner-approved calendar changes (calendar_writes.py); a sign-in without calendar write access is refused by Google.
    'calendar.events.insert': ('POST', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events'),
    'calendar.events.patch': ('PATCH', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}'),
    'calendar.events.delete': ('DELETE', 'https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}'),
}
_tokens: dict = {}
_lock = threading.Lock()


class BrokerError(Exception):
    """A content-free failure: `status` is Google's or the broker's HTTP status, None when unreachable."""

    def __init__(self, category, status=None):
        super().__init__(category)
        self.category, self.status = category, status


def _broker(config, credential, payload, timeout=20):
    url = (config.get('google_broker') or {}).get('url')
    if not url:
        raise BrokerError('google_broker_not_configured')
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={'Authorization': 'Bearer ' + credential(config, 'google-broker'),
                                              'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(200_000))
    except urllib.error.HTTPError as error:
        error.close()
        raise BrokerError('google_scope_not_granted' if error.code == 403 else 'google_broker', error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise BrokerError('google_broker_unreachable') from None


def scopes(config, credential, account):
    """The scopes the owner granted this account at the broker."""
    return set(_broker(config, credential, {'email': account['email']}).get('scopes') or [])


def token(config, credential, account, scope, fresh=False):
    key = (account['email'].lower(), scope)
    with _lock:
        hit = _tokens.get(key)
        if hit and not fresh and hit[1] > time.monotonic():
            return hit[0]
    try:
        kept = mail_grants.get(config, 'google', account['email'])
    except mail_grants.GrantError:
        raise BrokerError('google_signin_unreadable') from None
    answer = _broker(config, credential, {'email': account['email'], 'scope': scope, **({'refresh_token': kept} if kept else {})})
    if not answer.get('access_token'):
        raise BrokerError('google_broker')
    if kept and answer.get('refresh_token') and answer['refresh_token'] != kept:
        mail_grants.rotate(config, 'google', account['email'], kept, answer['refresh_token'])
    with _lock:
        _tokens[key] = (answer['access_token'], time.monotonic() + max(0, int(answer.get('expires_in') or 0) - 120))
    return answer['access_token']


def url_for(method, params, write=False):
    known = (WRITE if write else READ).get(method)
    if not known:
        raise BrokerError('unsupported_google_method')
    verb, template = known
    rest = dict(params or {})
    for name in [part.split('}')[0] for part in template.split('{')[1:]]:
        if name not in rest:
            raise BrokerError('missing_google_parameter')
        template = template.replace('{' + name + '}', urllib.parse.quote(str(rest.pop(name)), safe=''))
    query = urllib.parse.urlencode({k: (str(v).lower() if isinstance(v, bool) else v) for k, v in rest.items()}, doseq=True)
    return verb, template + ('?' + query if query else '')


def call(config, credential, account, method, params, scope, body=None, write=False, timeout=60):
    """One Google API call as the account's owner; the parsed JSON answer."""
    verb, url = url_for(method, params, write)
    for attempt in (0, 1):
        access = token(config, credential, account, scope, fresh=bool(attempt))
        request = urllib.request.Request(url, method=verb, data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Authorization': 'Bearer ' + access, 'Accept': 'application/json',
                                                  **({'Content-Type': 'application/json'} if body is not None else {})})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 401 and not attempt and not write:
                continue  # the access token expired early: one fresh one, for reads only
            raise BrokerError(method.split('.')[0], error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BrokerError('timeout') from None
        except ValueError:
            raise BrokerError('invalid_json') from None
